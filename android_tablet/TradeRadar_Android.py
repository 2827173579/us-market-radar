#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TradeRadar_Android.py - 安卓 / 小米平板专属轻量控制中心与 60s 极速轮询哨兵
- 纯 Python 原生标准库开发，零编译、零 C++ 依赖（Termux 开箱即用）
- 1:1 复刻电脑版控制中心暗黑触控 UI（大按键设计，支持平板添加到主屏幕作为原生全屏 App）
- 支持一键启动/停止 60 秒极速实盘轮询守护
- 支持双通道微信（ClawBot + 公众号）与 Discord 实时预警推送
- 支持手机/平板浏览器局域网触控操作与实时滚动控制台日志
"""

import os
import sys
import time
import json
import re
import socket
import subprocess
import threading
import urllib
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

# 保证 UTF-8 编码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)

# ------------------------------------------------------------------------------
# 1. 环境配置与 Token 加载
# ------------------------------------------------------------------------------
def load_env():
    env_candidates = [
        os.path.join(PROJECT_DIR, ".env"),
        os.path.join(os.path.dirname(PROJECT_DIR), ".env"),
    ]
    for env_file in env_candidates:
        if os.path.exists(env_file):
            try:
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass

load_env()
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "").strip()
PUSHPLUS_CHANNEL = os.environ.get("PUSHPLUS_CHANNEL", "clawbot").strip()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
DISCORD_CLAW_WEBHOOK_URL = os.environ.get("DISCORD_CLAW_WEBHOOK_URL", "").strip()
DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL = os.environ.get(
    "DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL",
    ""
).strip()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "2827173579/us-market-radar").strip()
PORT = 8765

# ------------------------------------------------------------------------------
# 2. 内存日志与全局运行状态管理器
# ------------------------------------------------------------------------------
MAX_LOG_LINES = 250
LOG_BUFFER = []
LOG_LOCK = threading.Lock()
ALERT_HISTORY_FILE = os.path.join(PROJECT_DIR, ".android_alert_history.json")

def append_log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    with LOG_LOCK:
        LOG_BUFFER.append(entry)
        if len(LOG_BUFFER) > MAX_LOG_LINES:
            LOG_BUFFER.pop(0)
    print(entry, flush=True)

class PollerState:
    def __init__(self):
        self.is_running = False
        self.thread = None
        self.stop_event = threading.Event()
        self.round_idx = 0
        self.last_sync_time = "未同步"
        self.session_name = "检测中"
        self.pid = os.getpid()

STATE = PollerState()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ------------------------------------------------------------------------------
# 3. 美股交易时段探测器 (严格对齐美东时间与夏/冬令时)
# ------------------------------------------------------------------------------
def get_us_market_session():
    utc_now = datetime.now(timezone.utc)
    # 美东时间通常为 UTC-4 (夏令时) 或 UTC-5 (冬令时)
    # 3月第二个周日 ~ 11月第一个周日为夏令时 EDT (UTC-4)，其余为 EST (UTC-5)
    year = utc_now.year
    # 简易美东时间转换
    is_dst = (utc_now.month > 3 and utc_now.month < 11) or (utc_now.month == 3 and utc_now.day >= 8) or (utc_now.month == 11 and utc_now.day < 1)
    et_offset = timedelta(hours=-4 if is_dst else -5)
    et_now = utc_now + et_offset
    
    weekday = et_now.weekday() # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    hm = et_now.hour * 60 + et_now.minute

    if weekday in (5, 6):
        return "CLOSED", "💤 週末休市"

    if 0 <= hm < 240:         # 20:00 - 04:00 ET (包含前夜盘)
        return "NIGHT", "🌌 美股夜盘"
    elif 240 <= hm < 570:     # 04:00 - 09:30 ET
        return "PRE", "🌅 盘前交易"
    elif 570 <= hm < 960:     # 09:30 - 16:00 ET
        return "REGULAR", "☀️ 常规盘中"
    elif 960 <= hm < 1200:    # 16:00 - 20:00 ET
        return "POST", "🌆 盘后交易"
    else:                     # 20:00 - 24:00 ET
        return "NIGHT", "🌌 美股夜盘"

# ------------------------------------------------------------------------------
# 4. 微信 PushPlus & Discord 双通道实时预警发送引擎
# ------------------------------------------------------------------------------
def send_pushplus(title: str, markdown_content: str):
    if not PUSHPLUS_TOKEN or PUSHPLUS_TOKEN == "YOUR_TOKEN":
        return False
    url = "https://www.pushplus.plus/send"
    channels = [PUSHPLUS_CHANNEL, "wechat"] if PUSHPLUS_CHANNEL != "wechat" else ["wechat"]
    success = False
    for ch in channels:
        payload = {
            "token": PUSHPLUS_TOKEN,
            "title": title,
            "content": markdown_content,
            "template": "markdown",
            "channel": ch
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                if res.get("code") == 200:
                    ch_desc = "ClawBot机器人" if ch == "clawbot" else "微信服务号"
                    append_log(f"  [+] 📱 微信[{ch_desc}]推送成功: {title}")
                    success = True
                else:
                    append_log(f"  [!] 📱 微信[{ch}]报错: {res.get('msg')}")
        except Exception as e:
            append_log(f"  [!] 📱 微信[{ch}]网络异常: {e}")
    return success

def send_discord(title: str, description: str, color: int = 0x00e676, fields: list = None):
    targets = [u for u in [DISCORD_WEBHOOK_URL, DISCORD_CLAW_WEBHOOK_URL] if u]
    if not targets:
        return False
    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "⚡ TradeRadar Android Sentinel · 小米平板轻量哨兵"}
    }
    if fields:
        embed["fields"] = fields
    payload = {"username": "⚡ TradeRadar 哨兵", "embeds": [embed]}
    success = False
    for wh in targets:
        try:
            req = urllib.request.Request(
                wh,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status in (200, 204):
                    append_log(f"  [+] 🎮 Discord Webhook 镜像推送成功！")
                    success = True
        except Exception as e:
            append_log(f"  [!] 🎮 Discord 推送异常: {e}")
    return success

# ------------------------------------------------------------------------------
# 5. 核心轻量实盘抓取与风控比对逻辑
# ------------------------------------------------------------------------------
# ------------------------------------------------------------------------------
# 70 只全量标的池配置 (16 只宏观/行业/杠杆 ETF + 54 只 AI 与核心个股)
# ------------------------------------------------------------------------------
ETF_TICKERS = [
    "QQQ", "SPY", "DIA", "IWM", "SMH", "SOXX", "SOXL", "KORU",
    "IGV", "TLT", "GLD", "QQQI", "VIXY", "SQQQ", "SOXS", "IBIT"
]

CORE_STOCKS = [
    "AAOI", "INTC", "NBIS", "SNDK", "ASTS", "AXTI", "COIN", "NOW", "FPS", "OKLO",
    "MRVL", "LITE", "MSTR", "STX", "CRWV", "CRDO", "APLD", "DELL", "WDC", "NVDA",
    "SKHY", "HPE", "AMAT", "ORCL", "GOOGL", "ASML", "BE", "PLTR", "MU", "VRT",
    "META", "TSM", "VST", "COHR", "SPCX", "AXP", "CRWD", "MSFT", "QCOM", "NKE",
    "CEG", "V", "RKLB", "KO", "TSLA", "AMZN", "MCD", "PEP", "AAPL", "AVGO",
    "AMD", "LLY", "MRNA", "FLY"
]

FULL_70_POOL = list(dict.fromkeys(ETF_TICKERS + CORE_STOCKS))


# ------------------------------------------------------------------------------
# 4.5 防御因子起飞 (Defensive Flight) 监控与专属 Discord 推送引擎
# ------------------------------------------------------------------------------
DEFENSIVE_FLIGHT_STATE_FILE = os.path.join(PROJECT_DIR, ".android_defensive_state.json")

def evaluate_and_send_defensive_flight(quotes: dict, now_str: str, force_send: bool = False):
    """
    小米平板专属防御因子监控引擎：
    1. 8只防御避险资产: KO, PEP, MCD, LLY, MRNA, TLT, GLD, VIXY
    2. 4只核心进攻科技: SMH, SOXL, NVDA, QQQ
    3. 背离分 Flight Score = 防御均值 - 科技均值
    4. 变化 >=0.10%p 或个股变化 >=0.50% 时自动推送到【防御因子背离百分比】Discord 专属频道
    """
    if not DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL or not quotes:
        return False

    def_tickers = ["KO", "PEP", "MCD", "LLY", "MRNA", "TLT", "GLD", "VIXY"]
    off_tickers = ["SMH", "SOXL", "NVDA", "QQQ"]

    def_items = []
    off_items = []
    def_vals = []
    off_vals = []

    for s in def_tickers:
        if s in quotes:
            p = float(quotes[s].get("pct_change", 0.0))
            def_vals.append(p)
            def_items.append(f"{s} `{p:+.2f}%`")

    for s in off_tickers:
        if s in quotes:
            p = float(quotes[s].get("pct_change", 0.0))
            off_vals.append(p)
            off_items.append(f"{s} `{p:+.2f}%`")

    if not def_vals or not off_vals:
        return False

    def_mean = sum(def_vals) / len(def_vals)
    off_mean = sum(off_vals) / len(off_vals)
    score = def_mean - off_mean

    # 防刷屏检查 (Score 变动 >=0.10% 或 任何成分变动 >=0.50%)
    prev_state = {}
    if os.path.exists(DEFENSIVE_FLIGHT_STATE_FILE):
        try:
            with open(DEFENSIVE_FLIGHT_STATE_FILE, "r", encoding="utf-8") as f:
                prev_state = json.load(f)
        except Exception:
            pass

    last_score = prev_state.get("last_score")
    prev_comps = prev_state.get("components", {})

    should_send = force_send or (last_score is None)
    if not should_send:
        if abs(score - float(last_score)) >= 0.10:
            should_send = True
        else:
            for s in (def_tickers + off_tickers):
                if s in quotes:
                    curr_p = float(quotes[s].get("pct_change", 0.0))
                    prev_p = float(prev_comps.get(s, curr_p))
                    if abs(curr_p - prev_p) >= 0.50:
                        should_send = True
                        break

    if not should_send:
        return False

    # 确定状态与色阶
    is_alert = (score >= 2.5)
    if is_alert:
        color = 0xF85149  # 警示红
        status_tag = "🚨 【防御因子起飞 · 警惕机构盘中去杠杆踩踏】"
        advice = "⚠️ **操盘警示**：防御避险资产超额走强，科技成长股正被机构急促抽离资金！短线多头切勿盲目抄底接飞刀，实盘注意上移保本止损，防范盘中踩踏！"
    elif score >= 1.5:
        color = 0xFF944D  # 警觉橙
        status_tag = "⚠️ 【避险情绪升温 · 防御因子走强】"
        advice = "👀 **盘面观察**：防御因子相对科技出现温和背离，机构资金偏向保守防御，多头进攻动能有所收敛，持仓宜设移动止盈保护。"
    elif score <= -1.0:
        color = 0x00E676  # 顺风绿
        status_tag = "🚀 【进攻科技占优 · 市场风险偏好高昂】"
        advice = "🟢 **多头顺风**：进攻型科技成长股显著领跑防御资产，市场风险偏好健康，多头仓位顺势拿稳。"
    else:
        color = 0x58A6FF  # 平稳蓝
        status_tag = "🛡️ 【因子表现均衡 · 机构仓位平稳】"
        advice = "✅ **常规格局**：防御因子与进攻科技相对强弱处于正常均衡区间，机构未见系统性去杠杆调仓。"

    payload = {
        "embeds": [{
            "title": status_tag,
            "description": advice,
            "color": color,
            "fields": [
                {"name": "🎯 因子背离差值 (Flight Score)", "value": f"`{score:+.2f}%` (防御均值 `{def_mean:+.2f}%` vs 进攻均值 `{off_mean:+.2f}%`)", "inline": False},
                {"name": f"🛡️ 防御避险资产 ({len(def_vals)}只)", "value": " ｜ ".join(def_items), "inline": False},
                {"name": f"🚀 进攻科技资产 ({len(off_vals)}只)", "value": " ｜ ".join(off_items), "inline": False}
            ],
            "footer": {"text": f"TradeRadar · 小米平板防御因子哨兵 · {now_str}"}
        }]
    }

    try:
        req = urllib.request.Request(
            DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "TradeRadar-Android/2.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 204):
                append_log(f"[+] 🛡️ [防御因子] 背离分 {score:+.2f}% 成功推送至 Discord 专属频道！")
                cur_comps = {s: float(quotes[s].get("pct_change", 0.0)) for s in (def_tickers + off_tickers) if s in quotes}
                with open(DEFENSIVE_FLIGHT_STATE_FILE, "w", encoding="utf-8") as f_s:
                    json.dump({"last_score": score, "components": cur_comps, "last_sent": now_str}, f_s, ensure_ascii=False, indent=2)
                return True
    except Exception as e:
        append_log(f"[!] ⚠️ Discord 防御因子推送异常: {e}")
    return False

# ------------------------------------------------------------------------------
# 5. 双线程平滑令牌桶频控引擎 (FinnhubRateLimiter)
# ------------------------------------------------------------------------------
class FinnhubRateLimiter:
    """严控 50 次/分安全红线，两线程交替拉取，杜绝 429 超频"""
    def __init__(self, max_per_minute=50.0, min_interval=1.05):
        self.capacity = float(max_per_minute)
        self.tokens = float(max_per_minute)
        self.fill_rate = float(max_per_minute) / 60.0
        self.last_fill_time = time.time()
        self.last_request_time = 0.0
        self.min_interval = min_interval
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_fill_time
            self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
            self.last_fill_time = now

            if self.tokens < 1.0:
                needed = (1.0 - self.tokens) / self.fill_rate
                time.sleep(needed)
                now = time.time()
                self.tokens = 0.0
                self.last_fill_time = now
            else:
                self.tokens -= 1.0

            time_since_last = now - self.last_request_time
            if time_since_last < self.min_interval:
                time.sleep(self.min_interval - time_since_last)
                now = time.time()

            self.last_request_time = now

    def handle_429(self, attempt=0):
        with self.lock:
            backoff = 10.0 if attempt == 0 else 20.0
            append_log(f"[!] ⚠️ Finnhub 触发 429 限流保护，执行指数退避休眠 {backoff:.1f} 秒...")
            self.tokens = 0.0
            self.last_fill_time = time.time() + backoff
            self.last_request_time = time.time() + backoff
        time.sleep(backoff)

FINNHUB_LIMITER = FinnhubRateLimiter(max_per_minute=50.0, min_interval=1.05)

def fetch_single_finnhub_quote(symbol: str, api_key: str):
    """单标的通过 Finnhub REST API 获取秒级报价 (加 1.05s 稳态令牌桶频控与 429 保护)"""
    url = f"https://finnhub.io/api/v1/quote?symbol={urllib.parse.quote(symbol)}&token={api_key}"
    for attempt in range(2):
        try:
            FINNHUB_LIMITER.acquire()
            req = urllib.request.Request(url, headers={"User-Agent": "TradeRadar-Android/2.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("c", 0) > 0:
                    return symbol, {
                        "last": float(data["c"]),
                        "change": float(data.get("d", 0.0)),
                        "pct_change": float(data.get("dp", 0.0)),
                        "high": float(data.get("h", 0.0)),
                        "low": float(data.get("l", 0.0)),
                        "prev_close": float(data.get("pc", 0.0))
                    }
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == 0:
                    FINNHUB_LIMITER.handle_429(attempt)
                    continue
        except Exception:
            pass
    return symbol, None

def fetch_finnhub_quotes_batch(tickers, api_key):
    """双线程并发抓取 (max_workers=2)，由令牌桶严格保证不超过 50 次/分红线"""
    if not api_key or not tickers:
        return {}
    results = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_to_sym = {executor.submit(fetch_single_finnhub_quote, t, api_key): t for t in tickers}
        for future in as_completed(future_to_sym):
            sym, q = future.result()
            if q:
                results[sym] = q
    return results

def load_ai_predictions():
    candidates = [
        os.path.join(PROJECT_DIR, "data_cache", "latest_ai_predictions.json"),
        os.path.join(PROJECT_DIR, "latest_ai_predictions.json"),
        os.path.join(os.path.dirname(PROJECT_DIR), "data_cache", "latest_ai_predictions.json"),
        os.path.join(os.path.dirname(PROJECT_DIR), "latest_ai_predictions.json"),
    ]
    for ai_file in candidates:
        if os.path.exists(ai_file):
            try:
                with open(ai_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}

def run_single_scan_cycle(round_idx=1):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    session_code, session_name = get_us_market_session()
    STATE.session_name = session_name
    STATE.last_sync_time = now_str

    append_log(f"⏱️ [{now_str}] [第 {round_idx} 轮] 执行 60 秒轻量市场扫描...")
    append_log(f"[*] 🕒 当前美东市场时段状态: 【{session_name}】")

    # 1. 加载 AI 预测与关键阈值
    ai_data = load_ai_predictions()
    by_ticker = ai_data.get("by_ticker", {})
    weather = ai_data.get("macro_weather", {}).get("status", "⛅ 阴天防守")

    # 加载自选监控池 (优先加载 target_watchlist.json)
    wl_candidates = [
        os.path.join(PROJECT_DIR, "target_watchlist.json"),
        os.path.join(os.path.dirname(PROJECT_DIR), "target_watchlist.json"),
    ]
    watchlist = []
    for wl_file in wl_candidates:
        if os.path.exists(wl_file):
            try:
                with open(wl_file, "r", encoding="utf-8") as f:
                    watchlist = json.load(f)
                    if watchlist:
                        break
            except Exception:
                pass
    if not watchlist:
        watchlist = ["NVDA", "META", "TSM", "AMD", "MRVL", "AAOI", "AXTI", "BE", "SKHY", "LITE", "SPCX", "INTC", "MSTR", "SNDK", "QQQ", "SOXL", "SOXS", "KORU", "QCOM", "IBIT"]

    # 2. 双线程令牌桶平稳并发拉取 70 只全量标的池 (16 只 ETF + 54 只个股)
    # 结合 target_watchlist.json 优先，若无则抓取全量 70 只标的
    scan_pool = list(dict.fromkeys(watchlist + FULL_70_POOL))
    append_log(f"[*] 🚀 启动双线程令牌桶频控引擎 (workers=2, bucket=50/min, min_int=1.05s)...")
    append_log(f"[*] 📊 正在平稳抓取 {len(scan_pool)} 只全量标的 (含 16 只 ETF + 54 只核心股) 实时报价...")
    
    fetched_quotes = fetch_finnhub_quotes_batch(scan_pool, FINNHUB_API_KEY) if FINNHUB_API_KEY else {}
    append_log(f"[+] ✅ 实时行情穿透完成: 成功拉取 {len(fetched_quotes)}/{len(scan_pool)} 只标的！")

    # 3. 动态风控阈值比对
    alerts_to_send = []
    for tk, q in fetched_quotes.items():
        curr_p = q["last"]
        change_pct = q["pct_change"]
        ai_item = by_ticker.get(tk, {})
        sl_price = ai_item.get("stop_loss_price", 0.0)
        tp_price = ai_item.get("target_profit_price", 0.0)
        action = ai_item.get("action", "")

        # 止损跌破检测
        if sl_price > 0 and curr_p <= sl_price:
            alerts_to_send.append({
                "ticker": tk,
                "type": "STOP_LOSS",
                "title": f"🔴 触发防守止损线",
                "desc": f"**{tk}** 当前价 **${curr_p:.2f}** ({change_pct:+.2f}%) 跌破预设止损线 **${sl_price:.2f}**！建议严格规避下行风险。"
            })
        # 止盈触达检测
        elif tp_price > 0 and curr_p >= tp_price:
            alerts_to_send.append({
                "ticker": tk,
                "type": "TAKE_PROFIT",
                "title": f"🟣 触达第一止盈线",
                "desc": f"**{tk}** 当前价 **${curr_p:.2f}** ({change_pct:+.2f}%) 成功触及目标止盈位 **${tp_price:.2f}**！建议分批锁定利润。"
            })

    # 4. 触发预警推送
    if alerts_to_send:
        append_log(f"[!] 🚨 扫描发现 {len(alerts_to_send)} 项重要价格异动！正在推送双通道...")
        for al in alerts_to_send:
            title = f"【美股量化预警】{al['title']} · {al['ticker']}"
            md = [
                f"### {al['title']}: {al['ticker']}",
                f"- **最新现价**: `${fetched_quotes[al['ticker']]['last']:.2f}` ({fetched_quotes[al['ticker']]['pct_change']:+.2f}%)",
                f"- **时段状态**: {session_name}",
                f"- **大盘气象**: {weather}",
                f"- **详细说明**: {al['desc']}",
                f"- **推送节点**: 小米平板极速哨兵 · {now_str}"
            ]
            send_pushplus(title, "\n".join(md))
            send_discord(title, al['desc'], color=0xda3633 if "止损" in al['title'] else 0x8957e5)
    else:
        append_log(f"[+] 🛡️ 自选监控池价格均在安全运行区间内，暂无破位触发。")

    # 4.5 防御因子实时计算与 Discord 专属频道推送
    evaluate_and_send_defensive_flight(fetched_quotes, now_str)

    # 5. 自动同步发布到 GitHub Pages (若处于 Git 仓库环境)
    sync_to_github_pages(round_idx, now_str, quotes=fetched_quotes)

# ------------------------------------------------------------------------------
# 6. 后台守护线程调度器
# ------------------------------------------------------------------------------

# ------------------------------------------------------------------------------
# 5.5 GitHub Pages 自动同步引擎 (可选)
# ------------------------------------------------------------------------------
def sync_to_github_pages(round_idx: int, now_str: str, quotes: dict = None):
    """
    小米平板 GitHub Pages 全自动同步引擎：
    1. 自动定位 Git 仓库根目录（兼容从 android_tablet 或根目录启动）
    2. 刷新 futu_pro_terminal.html 与 index.html 的时间戳与实时报价
    3. 同步更新 data_cache/latest_ai_predictions.json
    4. 自动携带 GITHUB_TOKEN 配置远端并提交推送
    5. 触发 GitHub Pages 自动部署上线
    """
    try:
        work_dir = PROJECT_DIR
        chk = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False, cwd=PROJECT_DIR)
        if chk.returncode == 0 and chk.stdout.strip():
            work_dir = chk.stdout.strip()
        else:
            chk2 = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, check=False, cwd=PROJECT_DIR)
            if chk2.returncode != 0:
                return False

        # 1. 刷新 futu_pro_terminal.html
        futu_path = os.path.join(work_dir, "futu_pro_terminal.html")
        if os.path.exists(futu_path):
            try:
                with open(futu_path, "r", encoding="utf-8") as f_in:
                    futu_content = f_in.read()

                # A. 替换数据时间戳
                futu_content = re.sub(
                    r"数据更新于[:：]\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}",
                    f"数据更新于: {now_str}",
                    futu_content
                )
                futu_content = re.sub(
                    r'"sync_time":\s*"[^"]+"',
                    f'"sync_time": "{now_str}"',
                    futu_content
                )

                # B. 更新 STOCKS_DATA (54 只个股) 与 MACRO_DATA (16 只宏观/行业 ETF)
                if quotes:
                    # 1) 更新 54 只个股
                    m = re.search(r"const STOCKS_DATA = (\[.*?\]);\s*\n", futu_content)
                    if m:
                        try:
                            stocks_list = json.loads(m.group(1))
                            for s in stocks_list:
                                tk = s.get("ticker")
                                if tk in quotes:
                                    q = quotes[tk]
                                    s["last"] = round(float(q.get("last", 0.0)), 2)
                                    s["active_price"] = round(float(q.get("last", 0.0)), 2)
                                    s["change"] = round(float(q.get("change", 0.0)), 2)
                                    s["pct_change"] = round(float(q.get("pct_change", 0.0)), 4)
                            new_json = json.dumps(stocks_list, ensure_ascii=False)
                            futu_content = futu_content[:m.start(1)] + new_json + futu_content[m.end(1):]
                        except Exception:
                            pass

                    # 2) 更新 16 只宏观/行业 ETF (MACRO_DATA)
                    m_macro = re.search(r"const MACRO_DATA = (\{.*?\});\s*\n", futu_content)
                    if m_macro:
                        try:
                            macro_dict = json.loads(m_macro.group(1))
                            for etf_sym, etf_item in macro_dict.items():
                                if etf_sym in quotes:
                                    q = quotes[etf_sym]
                                    etf_item["last"] = round(float(q.get("last", 0.0)), 2)
                                    etf_item["active_price"] = round(float(q.get("last", 0.0)), 2)
                                    etf_item["change"] = round(float(q.get("change", 0.0)), 2)
                                    etf_item["pct_change"] = round(float(q.get("pct_change", 0.0)), 4)
                            new_macro_json = json.dumps(macro_dict, ensure_ascii=False)
                            futu_content = futu_content[:m_macro.start(1)] + new_macro_json + futu_content[m_macro.end(1):]
                        except Exception:
                            pass

                with open(futu_path, "w", encoding="utf-8") as f_out:
                    f_out.write(futu_content)

                # C. 同步覆盖 index.html，保证访问根目录或 futu_pro_terminal 体验完全一致
                index_path = os.path.join(work_dir, "index.html")
                with open(index_path, "w", encoding="utf-8") as f_idx:
                    f_idx.write(futu_content)

            except Exception as e_html:
                append_log(f"[*] 看板 HTML 刷新提示: {e_html}")

        # 2. 同步更新 data_cache/latest_ai_predictions.json
        if quotes:
            for pred_path in [
                os.path.join(work_dir, "data_cache", "latest_ai_predictions.json"),
                os.path.join(work_dir, "latest_ai_predictions.json")
            ]:
                if os.path.exists(pred_path):
                    try:
                        with open(pred_path, "r", encoding="utf-8") as f_pred:
                            ai_dict = json.load(f_pred)
                        if isinstance(ai_dict, dict) and "by_ticker" in ai_dict:
                            for tk, q in quotes.items():
                                if tk in ai_dict["by_ticker"]:
                                    ai_dict["by_ticker"][tk]["last"] = q.get("last")
                                    ai_dict["by_ticker"][tk]["pct_change"] = q.get("pct_change")
                            with open(pred_path, "w", encoding="utf-8") as f_pred_out:
                                json.dump(ai_dict, f_pred_out, ensure_ascii=False, indent=2)
                    except Exception:
                        pass

        # 3. 校验并配置带 PAT 的 Git Remote
        token = GITHUB_TOKEN
        if not token:
            rem_chk = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=False, cwd=work_dir)
            if "ghp_" in rem_chk.stdout:
                token = "ghp_" + rem_chk.stdout.split("ghp_")[1].split("@")[0]
        if token:
            auth_url = f"https://2827173579:{token}@github.com/{GITHUB_REPO}.git"
            subprocess.run(["git", "remote", "set-url", "origin", auth_url], check=False, cwd=work_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 4. 执行 Git 暂存、配置身份与提交
        # 自动配置本地仓库提交者身份 (防止 Termux 新环境没有 global config 导致 commit 报 128 Author identity unknown)
        subprocess.run(["git", "config", "user.name", "2827173579"], check=False, cwd=work_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.email", "a18669358277@gmail.com"], check=False, cwd=work_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        files_to_add = ["futu_pro_terminal.html", "index.html", "data_cache/latest_ai_predictions.json"]
        for f in files_to_add:
            if os.path.exists(os.path.join(work_dir, f)):
                subprocess.run(["git", "add", f], check=False, cwd=work_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        commit_res = subprocess.run(
            ["git", "commit", "-m", f"⚡ [小米平板实盘同步] 第 {round_idx} 轮 ({now_str})"],
            capture_output=True, text=True, check=False, cwd=work_dir
        )
        if commit_res.returncode != 0 and "nothing to commit" not in (commit_res.stdout + commit_res.stderr).lower():
            err_c = commit_res.stderr.strip()[:80]
            if err_c:
                append_log(f"[*] ⚠️ Git 提交提示: {err_c}")
        # rebase pull 以免冲突
        subprocess.run(["git", "pull", "--rebase", "--autostash", "-X", "theirs", "origin", "main"], check=False, cwd=work_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        push_res = subprocess.run(["git", "push", "origin", "HEAD:main"], capture_output=True, text=True, check=False, cwd=work_dir)
        if push_res.returncode != 0:
            subprocess.run(["git", "pull", "--rebase", "--autostash", "-X", "theirs", "origin", "main"], check=False, cwd=work_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            push_res = subprocess.run(["git", "push", "origin", "HEAD:main"], capture_output=True, text=True, check=False, cwd=work_dir)
        if push_res.returncode == 0:
            append_log(f"[+] 🌐 [GitHub Pages] 成功同步第 {round_idx} 轮数据至云端！页面已自动刷新")
            return True
        else:
            err_msg = push_res.stderr.strip()[:100]
            if err_msg:
                append_log(f"[*] 🌐 [GitHub Pages] 推送反馈: {err_msg}")
    except Exception as e:
        append_log(f"[!] 🌐 [GitHub Pages] 同步异常: {e}")
    return False

def poller_daemon_thread():
    STATE.round_idx = 1
    append_log("🚀 小米平板 60 秒极速轮询守护引擎已正式启动！")
    while not STATE.stop_event.is_set():
        try:
            run_single_scan_cycle(STATE.round_idx)
            STATE.round_idx += 1
        except Exception as e:
            append_log(f"[!] ⚠️ 本轮扫描异常捕获: {e}")
        
        # 60 秒休眠，支持即刻终止
        for _ in range(60):
            if STATE.stop_event.is_set():
                break
            time.sleep(1)
    
    append_log("🛑 后台轮询守护进程已安全停止。")
    STATE.is_running = False

# ------------------------------------------------------------------------------
# 7. 嵌入式暗黑触屏 Web 控制中心 HTML & CSS
# ------------------------------------------------------------------------------
INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>⚡ 美股全周期量化雷达 · 平板控制中心</title>
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="theme-color" content="#0d1117">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
        body {
            background-color: #0d1117;
            color: #c9d1d9;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            padding: 16px;
            max-width: 760px;
            margin: 0 auto;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }
        .header {
            margin-bottom: 12px;
            padding-bottom: 8px;
            border-bottom: 1px solid #21262d;
        }
        .header h1 {
            font-size: 19px;
            font-weight: 700;
            color: #f0f6fc;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .header p {
            font-size: 12px;
            color: #8b949e;
            margin-top: 4px;
        }
        .card {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            padding: 14px 16px;
            margin-bottom: 14px;
        }
        .status-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }
        .status-badge {
            font-size: 14px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .badge-active { color: #3fb950; }
        .badge-stopped { color: #f85149; }
        .pid-text { font-size: 12px; color: #8b949e; font-family: monospace; }
        .info-text {
            font-size: 11px;
            color: #8b949e;
            line-height: 1.5;
        }
        .btn-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            margin-bottom: 12px;
        }
        .btn {
            border: none;
            border-radius: 8px;
            padding: 14px 10px;
            font-size: 14px;
            font-weight: 700;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            transition: opacity 0.15s, transform 0.1s;
            user-select: none;
        }
        .btn:active { transform: scale(0.98); opacity: 0.85; }
        .btn-green  { background: #238636; color: #ffffff; }
        .btn-red    { background: #da3633; color: #ffffff; }
        .btn-blue   { background: #1f6feb; color: #ffffff; }
        .btn-purple { background: #8957e5; color: #ffffff; }
        .btn-orange { background: #d29922; color: #ffffff; }
        .btn-gray   { background: #30363d; color: #f0f6fc; }
        
        .toolbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }
        .tool-btn {
            background: #21262d;
            color: #8b949e;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 5px 12px;
            font-size: 12px;
            cursor: pointer;
        }
        .log-box {
            background: #010409;
            border: 1px solid #21262d;
            border-radius: 8px;
            padding: 12px;
            flex-grow: 1;
            min-height: 240px;
            max-height: 380px;
            overflow-y: auto;
            font-family: Consolas, monospace;
            font-size: 11.5px;
            line-height: 1.5;
            color: #7ee787;
            white-space: pre-wrap;
            word-break: break-all;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>⚡ 美股量化雷达与 Discord 哨兵 · 平板控制台</h1>
        <p>全周期量化终端 · 60s极速实盘轮询 · 每日4次定时哨兵 · 双通道预警镜像</p>
    </div>

    <div class="card">
        <div class="status-row">
            <div id="statusBadge" class="status-badge badge-stopped">● 守护进程已停止 (STOPPED)</div>
            <div id="pidInfo" class="pid-text">未运行</div>
        </div>
        <div class="info-text" id="infoText">
            本地端口: 8765 | 美东时段: 检测中...<br>
            上次同步: 未执行
        </div>
    </div>

    <div class="btn-grid">
        <button class="btn btn-green" onclick="doAction('/api/start')">● 启动后台轮询守护</button>
        <button class="btn btn-red" onclick="doAction('/api/stop')">● 停止所有后台进程</button>
        <button class="btn btn-blue" onclick="doAction('/api/scan_once')">🔄 手动即时刷新一次</button>
        <button class="btn btn-purple" onclick="doAction('/api/test_alert')">📢 发送预警镜像测试</button>
        <button class="btn btn-orange" onclick="doAction('/api/send_sentinel')">🎯 立即发送量化哨兵</button>
        <button class="btn btn-gray" onclick="window.open('https://2827173579.github.io/us-market-radar/', '_blank')">🌐 打开富途看盘网页</button>
    </div>

    <div class="toolbar">
        <span style="font-size: 12px; color: #8b949e;">📟 实时运行控制台日志:</span>
        <button class="tool-btn" onclick="doAction('/api/clear_logs')">🧹 清屏日志</button>
    </div>

    <div class="log-box" id="logBox">正在连接平板控制中心服务...</div>

    <script>
        function updateUI() {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    const badge = document.getElementById('statusBadge');
                    const pid = document.getElementById('pidInfo');
                    const info = document.getElementById('infoText');

                    if (data.is_running) {
                        badge.className = 'status-badge badge-active';
                        badge.innerText = '● 守护进程运行中 (ACTIVE)';
                        pid.innerText = 'PID: ' + data.pid + ' [第 ' + data.round_idx + ' 轮]';
                    } else {
                        badge.className = 'status-badge badge-stopped';
                        badge.innerText = '● 守护进程已停止 (STOPPED)';
                        pid.innerText = '未运行';
                    }

                    info.innerHTML = '美东时段: ' + data.session_name + ' | 局域网: http://' + data.lan_ip + ':8765<br>上次同步: ' + data.last_sync;
                }).catch(e => {});

            fetch('/api/logs')
                .then(r => r.json())
                .then(data => {
                    if (data && Array.isArray(data.logs)) {
                        const box = document.getElementById('logBox');
                        box.innerText = data.logs.join('\\n');
                        box.scrollTop = box.scrollHeight;
                    }
                }).catch(e => {});
        }

        function doAction(api) {
            fetch(api, { method: 'POST' })
                .then(r => r.json())
                .then(res => {
                    setTimeout(updateUI, 200);
                });
        }

        setInterval(updateUI, 1800);
        updateUI();
    </script>
</body>
</html>
"""

# ------------------------------------------------------------------------------
# 8. HTTP API 与 Web 路由处理器
# ------------------------------------------------------------------------------
class AndroidRadarHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass # 静默 HTTP 请求终端输出，保持日志干净

    def do_GET(self):
        if self.path in ["/futu", "/futu_pro_terminal.html"]:
            work_dir = PROJECT_DIR
            chk = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False, cwd=PROJECT_DIR)
            if chk.returncode == 0 and chk.stdout.strip():
                work_dir = chk.stdout.strip()
            futu_p = os.path.join(work_dir, "futu_pro_terminal.html")
            if not os.path.exists(futu_p):
                futu_p = os.path.join(PROJECT_DIR, "futu_pro_terminal.html")
            if os.path.exists(futu_p):
                with open(futu_p, "rb") as f_futu:
                    c = f_futu.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(c)
                return

        if self.path == "/" or self.path.startswith("/index") or self.path == "/control":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(INDEX_HTML.encode("utf-8"))
        elif self.path == "/api/status":
            session_code, session_name = get_us_market_session()
            data = {
                "is_running": STATE.is_running,
                "pid": STATE.pid,
                "round_idx": STATE.round_idx,
                "session_name": session_name,
                "last_sync": STATE.last_sync_time,
                "lan_ip": get_local_ip()
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
        elif self.path == "/api/logs":
            with LOG_LOCK:
                logs_copy = list(LOG_BUFFER)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"logs": logs_copy}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/start":
            if not STATE.is_running:
                STATE.stop_event.clear()
                STATE.is_running = True
                t = threading.Thread(target=poller_daemon_thread, daemon=True)
                STATE.thread = t
                t.start()
                append_log("🟢 用户在平板端触发: 启动后台轮询守护！")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))

        elif self.path == "/api/stop":
            if STATE.is_running:
                append_log("🔴 用户在平板端触发: 停止所有后台进程...")
                STATE.stop_event.set()
                STATE.is_running = False
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))

        elif self.path == "/api/scan_once":
            append_log("🔵 用户在平板端触发: 手动即时刷新一次！")
            threading.Thread(target=run_single_scan_cycle, args=(STATE.round_idx or 1,), daemon=True).start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))

        elif self.path == "/api/test_alert":
            append_log("🟣 用户在平板端触发: 发送预警镜像测试...")
            now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            title = "【美股量化雷达】📢 小米平板双通道预警测试成功"
            content = f"### 🚀 小米平板测试卡片成功投递！\\n- **测试时间**: {now_s}\\n- **通信链路**: 微信 ClawBot + 微信公众号 + Discord Webhook\\n- **设备节点**: 小米平板极速哨兵 (Android Termux)"
            send_pushplus(title, content)
            send_discord(title, f"来自小米平板控制中心的即时镜像测试成功！时间: {now_s}", color=0x8957e5)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))

        elif self.path == "/api/send_sentinel":
            append_log("🟠 用户在平板端触发: 立即发送量化哨兵...")
            now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            session_code, session_name = get_us_market_session()
            ai_data = load_ai_predictions()
            weather = ai_data.get("macro_weather", {}).get("status", "⛅ 阴天防守")
            title = f"【量化哨兵】🎯 全市场核心水温战报 ({session_name})"
            desc = f"**当前时段**: {session_name}\\n**大盘气象**: {weather}\\n**监控标的**: 71 只全量标的 (含最新接入 QCOM)\\n**哨兵节点**: 小米平板控制中心"
            send_pushplus(title, desc)
            send_discord(title, desc, color=0xd29922)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))

        elif self.path == "/api/clear_logs":
            with LOG_LOCK:
                LOG_BUFFER.clear()
            append_log("🧹 日志已清空。")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

# ------------------------------------------------------------------------------
# 9. 启动入口
# ------------------------------------------------------------------------------
def main():
    append_log("=" * 60)
    append_log("⚡ 美股全周期量化雷达 · 小米平板轻量控制中心已启动")
    append_log(f"📱 平板本地访问地址: http://127.0.0.1:{PORT}")
    append_log(f"🌐 局域网访问地址  : http://{get_local_ip()}:{PORT}")
    append_log("💡 提示: 在平板自带浏览器或 Chrome 中打开上述网址，")
    append_log("   点击浏览器菜单「添加到主屏幕」，即可享受原生全屏 App 体验！")
    append_log("=" * 60)

    server = HTTPServer(("0.0.0.0", PORT), AndroidRadarHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        append_log("[!] 收到退出信号，服务正在退出...")
        if STATE.is_running:
            STATE.stop_event.set()
        server.server_close()
        sys.exit(0)

if __name__ == "__main__":
    main()
