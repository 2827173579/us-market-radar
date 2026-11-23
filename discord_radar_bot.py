# -*- coding: utf-8 -*-
"""
独立外挂模块：Discord 双向交互机器人 (discord_radar_bot.py)

设计红线：
1. 物理隔离与零 API 压力：严禁调用 Yahoo / Finnhub / Jin10 等外部行情接口，只从本地只读快照
   `data_cache/latest_radar_snapshot.json` 与只读账本 `data_cache/signal_ledger.json` 毫秒级响应。
2. 零硬编码凭证：严格从 `.env` 或环境变量读取 `DISCORD_BOT_TOKEN` 与可选的 `DISCORD_GUILD_ID`。
3. 纯函数可测：所有检索、模糊匹配与 Embed 卡片构建均抽离为无网关依赖的纯函数。
"""

import asyncio
import difflib
import json
import math
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import signal_ledger
except Exception:
    signal_ledger = None

try:
    import ai_commentary_engine
except Exception:
    ai_commentary_engine = None

try:
    import discord
    from discord import app_commands
    _DISCORD_PY_AVAILABLE = True
except Exception:
    discord = None
    app_commands = None
    _DISCORD_PY_AVAILABLE = False


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENV_PATH = os.path.join(BASE_DIR, ".env")
DEFAULT_SNAPSHOT_PATH = os.path.join(BASE_DIR, "data_cache", "latest_radar_snapshot.json")
DEFAULT_LEDGER_PATH = os.path.join(BASE_DIR, "data_cache", "signal_ledger.json")
BOT_COOLDOWN_SECONDS = 3.0

# 终端编码与实时行缓冲适配 (Windows PowerShell / CMD)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass


BUILTIN_TICKER_ALIASES: Dict[str, List[str]] = {
    "xau": ["XAUUSD", "GLD"],
    "xauusd": ["XAUUSD"],
    "gold": ["XAUUSD", "GLD"],
    "黄金": ["XAUUSD", "GLD"],
    "金价": ["XAUUSD", "GLD"],
    "伦敦金": ["XAUUSD", "GLD"],
    "现货黄金": ["XAUUSD", "GLD"],
    "btc": ["BTC-USD"],
    "btcusd": ["BTC-USD"],
    "btc-usd": ["BTC-USD"],
    "bitcoin": ["BTC-USD"],
    "比特币": ["BTC-USD"],
    "大饼": ["BTC-USD"],
    "qqq": ["QQQ"],
    "nasdaq": ["QQQ"],
    "纳指": ["QQQ", "SQQQ"],
    "纳斯达克": ["QQQ", "SQQQ"],
    "做空纳指": ["SQQQ"],
    "spy": ["SPY"],
    "sp500": ["SPY"],
    "标普": ["SPY"],
    "大盘": ["SPY", "QQQ"],
    "dia": ["DIA"],
    "道指": ["DIA"],
    "道琼斯": ["DIA"],
    "iwm": ["IWM"],
    "罗素": ["IWM"],
    "小盘": ["IWM"],
    "soxl": ["SOXL"],
    "半导体": ["SOXL", "SMH", "SOXX", "SOXS"],
    "费半": ["SOXL", "SOXX", "SMH"],
    "芯片": ["NVDA", "SOXL", "SMH", "AMD"],
    "做空半导体": ["SOXS"],
    "koru": ["KORU"],
    "韩国": ["KORU"],
    "igv": ["IGV"],
    "软件": ["IGV", "MSFT", "NOW"],
    "tlt": ["TLT"],
    "美债": ["TLT"],
    "国债": ["TLT"],
    "gld": ["GLD"],
    "vixy": ["VIXY"],
    "恐慌": ["VIXY"],
    "恐慌指数": ["VIXY"],
    "sqqq": ["SQQQ"],
    "soxs": ["SOXS"],
    "nvda": ["NVDA"],
    "英伟达": ["NVDA"],
    "老黄": ["NVDA"],
    "tsla": ["TSLA"],
    "特斯拉": ["TSLA"],
    "aapl": ["AAPL"],
    "苹果": ["AAPL"],
    "msft": ["MSFT"],
    "微软": ["MSFT"],
    "googl": ["GOOGL"],
    "goog": ["GOOGL"],
    "谷歌": ["GOOGL"],
    "amzn": ["AMZN"],
    "亚马逊": ["AMZN"],
    "meta": ["META"],
    "脸书": ["META"],
    "tsm": ["TSM"],
    "台积电": ["TSM"],
    "amd": ["AMD"],
    "超微": ["AMD"],
    "avgo": ["AVGO"],
    "博通": ["AVGO"],
    "intc": ["INTC"],
    "英特尔": ["INTC"],
    "mu": ["MU"],
    "美光": ["MU"],
    "pltr": ["PLTR"],
    "帕兰提尔": ["PLTR"],
    "asml": ["ASML"],
    "阿斯麦": ["ASML"],
    "ko": ["KO"],
    "可口可乐": ["KO"],
    "spcx": ["SPCX"],
    "spacex": ["SPCX"],
    "glw": ["GLW"],
    "康宁": ["GLW"],
}

FLAGSHIP_PREVIEW_ORDER: Tuple[str, ...] = (
    "SPY", "QQQ", "SOXL", "NVDA", "TSLA", "AAPL", "MSFT", "META",
    "AMD", "TSM", "AVGO", "AMZN", "GOOGL", "PLTR", "MU",
    "XAUUSD", "BTC-USD", "GLD", "TLT", "VIXY", "SQQQ", "SOXS",
)


def load_env_values(env_path: Optional[str] = None) -> Dict[str, str]:
    """安全加载 `.env` 键值对到进程环境（不覆盖已存在环境变量），绝不打印敏感值。"""
    target = os.path.abspath(env_path or DEFAULT_ENV_PATH)
    loaded: Dict[str, str] = {}
    if not os.path.exists(target):
        return loaded
    try:
        with open(target, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k:
                    loaded[k] = v
                    if k not in os.environ:
                        os.environ[k] = v
    except Exception:
        return {}
    return loaded


def load_radar_snapshot(snapshot_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """严格只读加载主雷达最新全市场快照 (`latest_radar_snapshot.json`)。"""
    target = os.path.abspath(snapshot_path or DEFAULT_SNAPSHOT_PATH)
    if not os.path.exists(target):
        return None
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("tickers"), dict):
            return data
    except Exception:
        return None
    return None


def _opt_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        fv = float(val)
        return fv if math.isfinite(fv) else None
    except Exception:
        return None


def _fmt_price(val: Any) -> str:
    fv = _opt_float(val)
    if fv is None or fv <= 0:
        return "--"
    if fv >= 1000:
        return f"${fv:,.2f}"
    return f"${fv:.2f}"


def _fmt_pct(val: Any, digits: int = 2) -> str:
    fv = _opt_float(val)
    if fv is None:
        return "--"
    return f"{fv:+.{digits}f}%"


def _fmt_num(val: Any, digits: int = 2, suffix: str = "") -> str:
    fv = _opt_float(val)
    if fv is None:
        return "--"
    return f"{fv:.{digits}f}{suffix}"


def _build_footer_text(snapshot: Optional[Dict[str, Any]] = None) -> str:
    snap = snapshot if isinstance(snapshot, dict) else {}
    upd = str(snap.get("updated_at_et") or "").strip() or "等待快照更新"
    sess_name = str(snap.get("session_name") or snap.get("session_code") or "").strip()
    sess_part = f" ({sess_name})" if sess_name else ""
    return f"数据更新: {upd} ET{sess_part} · 仅作量化参考，不构成投资建议"


def _strip_bare_hr_lines(text: str) -> str:
    """防御性过滤任何单独成行的裸 `---`，确保 Discord 渲染洁净。"""
    lines = [ln for ln in str(text or "").splitlines() if ln.strip() != "---"]
    return "\n".join(lines).strip()


def search_ticker_candidates(
    query: str,
    snapshot: Optional[Dict[str, Any]],
    limit: int = 25,
) -> List[Dict[str, Any]]:
    """
    对 79 只监控池进行大小写不敏感、中文别名与容错模糊检索。
    """
    if not isinstance(snapshot, dict):
        return []
    tickers_map = snapshot.get("tickers")
    if not isinstance(tickers_map, dict) or not tickers_map:
        return []

    raw_q = str(query or "").strip()
    max_n = max(1, min(25, int(limit)))

    if not raw_q:
        ordered: List[Dict[str, Any]] = []
        seen = set()
        for tk in FLAGSHIP_PREVIEW_ORDER:
            if tk in tickers_map and tk not in seen:
                seen.add(tk)
                it = tickers_map[tk]
                ordered.append({
                    "ticker": tk,
                    "name": str(it.get("name") or tk),
                    "cat": str(it.get("cat") or ""),
                    "score": 90.0,
                })
        for tk in sorted(tickers_map.keys()):
            if tk not in seen:
                it = tickers_map[tk]
                ordered.append({
                    "ticker": tk,
                    "name": str(it.get("name") or tk),
                    "cat": str(it.get("cat") or ""),
                    "score": 50.0,
                })
        return ordered[:max_n]

    ql = raw_q.lower()
    qu = raw_q.upper()

    # 构建别名加权表
    alias_bonus: Dict[str, float] = {}
    if ql in BUILTIN_TICKER_ALIASES:
        for idx, sym in enumerate(BUILTIN_TICKER_ALIASES[ql]):
            alias_bonus[sym.upper()] = max(alias_bonus.get(sym.upper(), 0.0), 95.0 - idx * 3.0)
    else:
        for a_key, syms in BUILTIN_TICKER_ALIASES.items():
            if ql in a_key or (len(a_key) >= 2 and a_key in ql):
                for idx, sym in enumerate(syms):
                    alias_bonus[sym.upper()] = max(alias_bonus.get(sym.upper(), 0.0), 66.0 - idx * 2.0)

    scored: List[Dict[str, Any]] = []
    for tk_raw, item in tickers_map.items():
        if not isinstance(item, dict):
            continue
        tk = str(tk_raw).upper()
        name = str(item.get("name") or tk)
        sub = str(item.get("sub") or "")
        cat = str(item.get("cat") or "")
        name_l = name.lower()
        sub_l = sub.lower()
        cat_l = cat.lower()

        score = alias_bonus.get(tk, 0.0)
        if tk == qu:
            score = max(score, 100.0)
        elif tk.startswith(qu):
            score = max(score, 82.0)
        elif ql in tk.lower():
            score = max(score, 72.0)

        if name_l == ql:
            score = max(score, 92.0)
        elif ql in name_l:
            score = max(score, 74.0)
        elif ql in cat_l or ql in sub_l:
            score = max(score, 62.0)

        if score <= 0.0:
            ratio = difflib.SequenceMatcher(None, qu, tk).ratio()
            if ratio >= 0.60:
                score = round(ratio * 45.0, 2)

        if score > 0.0:
            scored.append({
                "ticker": tk,
                "name": name,
                "cat": cat,
                "score": score,
            })

    scored.sort(key=lambda x: (-float(x["score"]), x["ticker"]))
    return scored[:max_n]


def resolve_ticker(query: str, snapshot: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    精确/高置信度解析用户输入至单个监控池代码；无法高置信确认时返回 None（触发优雅提示与建议）。
    """
    if not isinstance(snapshot, dict):
        return None
    tickers_map = snapshot.get("tickers")
    if not isinstance(tickers_map, dict) or not tickers_map:
        return None

    raw_q = str(query or "").strip()
    if not raw_q:
        return None
    ql = raw_q.lower()
    qu = raw_q.upper()

    if qu in tickers_map:
        return qu

    if ql in BUILTIN_TICKER_ALIASES:
        for sym in BUILTIN_TICKER_ALIASES[ql]:
            if sym.upper() in tickers_map:
                return sym.upper()

    # 若支持 Autocomplete 选择标签回传（如 "NVDA · NVIDIA (芯片算力)"）也兼容提取前缀代码
    if " · " in raw_q:
        prefix = raw_q.split(" · ", 1)[0].strip().upper()
        if prefix in tickers_map:
            return prefix

    # 精确中文名称匹配
    for tk_raw, item in tickers_map.items():
        if isinstance(item, dict) and str(item.get("name") or "").strip().lower() == ql:
            return str(tk_raw).upper()

    # 唯一前缀或唯一名称子串匹配
    prefix_hits = [str(tk).upper() for tk in tickers_map.keys() if str(tk).upper().startswith(qu)]
    if len(prefix_hits) == 1:
        return prefix_hits[0]

    sub_hits = [
        str(tk).upper()
        for tk, item in tickers_map.items()
        if isinstance(item, dict) and ql in str(item.get("name") or "").lower()
    ]
    if len(sub_hits) == 1:
        return sub_hits[0]

    return None


def build_autocomplete_choices(
    query: str,
    snapshot: Optional[Dict[str, Any]],
    limit: int = 25,
) -> List[Tuple[str, str]]:
    """构建符合 Discord 100 字符上限与 25 条数量上限的 Autocomplete `(展示名, value)` 列表。"""
    cands = search_ticker_candidates(query, snapshot, limit=limit)
    choices: List[Tuple[str, str]] = []
    for c in cands[:25]:
        tk = str(c["ticker"])
        name = str(c.get("name") or tk)
        cat = str(c.get("cat") or "")
        label = f"{tk} · {name}" + (f" ({cat})" if cat else "")
        if len(label) > 100:
            label = label[:97] + "..."
        choices.append((label, tk[:100]))
    return choices


def _rel_pct_str(level_px: Any, spot_px: Any) -> str:
    lv = _opt_float(level_px)
    sp = _opt_float(spot_px)
    if lv is None or sp is None or lv <= 0 or sp <= 0:
        return ""
    diff = ((lv - sp) / sp) * 100.0
    return f" ({diff:+.1f}%)"


def _plain_regime_label(snapshot: Optional[Dict[str, Any]]) -> str:
    snap = snapshot if isinstance(snapshot, dict) else {}
    code = str(snap.get("market_regime") or "").strip().upper()
    if code == "BEAR_DEFENSE":
        return "空头防守（大盘偏弱，管住手）"
    if code == "BULL_TREND":
        return "多头顺风（大盘偏强）"
    if code == "NEUTRAL_CHOP":
        return "震荡分化（挑强做、别追高）"
    cn = str(snap.get("market_regime_cn") or "").strip()
    return cn.replace("🔴 ", "").replace("🟢 ", "").replace("🟡 ", "") or "稳健观察"


def _pick_profit_protection_line(item: Dict[str, Any], price: Optional[float]) -> Optional[float]:
    """为止盈/高位震荡标的选择合理的利润保护线（优先均线/防守止损/破前压力转支撑）。"""
    ref_spot = price if (price and price > 0) else _opt_float(item.get("last"))
    cands: List[float] = []
    sr_state = str(item.get("sr_breach_state") or "").upper()
    anc_r1 = _opt_float(item.get("anchor_r1") if item.get("anchor_r1") is not None else item.get("r1"))
    anc_r2 = _opt_float(item.get("anchor_r2") if item.get("anchor_r2") is not None else item.get("r2"))
    anc_s1 = _opt_float(item.get("anchor_s1") if item.get("anchor_s1") is not None else item.get("s1"))
    for raw in (
        item.get("ema20"),
        item.get("entry_stop_loss"),
        anc_r2 if sr_state == "ALL_RESISTANCE_BROKEN" else None,
        anc_r1 if sr_state == "R1_BROKEN" else None,
        item.get("vwap"),
        anc_s1,
    ):
        fv = _opt_float(raw)
        if fv is not None and fv > 0:
            if ref_spot and ref_spot > 0:
                if fv < ref_spot * 0.995:
                    cands.append(fv)
            else:
                cands.append(fv)
    if cands:
        return round(max(cands), 2)
    for raw in (item.get("entry_stop_loss"), item.get("vwap"), anc_s1):
        fv = _opt_float(raw)
        if fv is not None and fv > 0:
            return round(fv, 2)
    return None


def build_check_embed_dict(
    query: str,
    snapshot: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """构建 `/check <ticker>` 3 秒通俗诊断卡 Embed 字典。"""
    footer_txt = _build_footer_text(snapshot)
    now_iso = datetime.now(timezone.utc).isoformat()

    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("tickers"), dict) or not snapshot["tickers"]:
        return {
            "title": "⏳ 量化雷达数据还没准备好",
            "description": _strip_bare_hr_lines(
                "💬 一句话结论：主雷达正在刷新最新行情，稍等一两分钟再查。\n"
                "本地只读快照 `data_cache/latest_radar_snapshot.json` 暂未生成或为空，请稍后重试。"
            ),
            "color": 0xF59E0B,
            "timestamp": now_iso,
            "footer": {"text": footer_txt},
        }

    tickers_map = snapshot["tickers"]
    tk = resolve_ticker(query, snapshot)
    if not tk or tk not in tickers_map:
        cands = search_ticker_candidates(query, snapshot, limit=5)
        sug_lines = [
            f"• **{c['ticker']}** · {c['name']}" + (f"（{c['cat']}）" if c.get("cat") else "")
            for c in cands
        ]
        sug_block = "\n".join(sug_lines) if sug_lines else "• 热门示例：`NVDA` · `SOXL` · `QQQ` · `SPY` · `XAUUSD` · `BTC-USD`"
        return {
            "title": f"🔍 没找到「{str(query or '').strip()[:40] or '--'}」",
            "description": _strip_bare_hr_lines(
                f"💬 一句话结论：这只票不在当前监控的 {len(tickers_map)} 只核心名单里（支持大小写和中文搜名字）。\n\n"
                f"**您是不是要找：**\n{sug_block}"
            ),
            "color": 0xF59E0B,
            "timestamp": now_iso,
            "footer": {"text": footer_txt},
        }

    item = tickers_map[tk]
    stance = str(item.get("decision_stance") or "NEUTRAL").upper()
    verdict = str(item.get("decision_verdict") or "")
    is_inv = bool(item.get("is_inverse_hedge") or tk in ("SQQQ", "SOXS", "VIXY"))
    is_ipo = bool(item.get("is_ipo"))
    is_crash = bool(item.get("is_crash_halt")) or ("暴跌风险" in verdict)
    is_cooling = bool(item.get("is_cooling_halt")) or ("大跌冷却" in verdict)
    is_broken_bear = bool(item.get("is_broken_bearish")) or ("空头占优" in verdict)
    is_stop = bool(item.get("is_stop")) or ("跌破支撑" in verdict)
    is_pit = bool(item.get("is_pit")) or ("低吸窗口" in verdict)
    is_pit_watch = bool(item.get("is_pit_watch")) or ("先观望" in verdict)
    is_deep_pb = bool(item.get("is_deep_pullback")) or ("深度回调" in verdict)
    is_broken_reb = bool(item.get("is_broken_rebound")) or ("短线反抽" in verdict)
    is_tp_mode = bool(item.get("is_take_profit")) or any(k in verdict for k in ("止盈", "高位震荡", "落袋"))
    is_bear = bool(item.get("is_bear_defense_card")) or (stance == "BEAR") or is_crash or is_cooling or is_broken_bear or is_stop

    if is_bear:
        color = 0xEF4444
        stance_icon = "🔴"
    elif stance == "BULL" and not is_tp_mode and not is_inv and not is_ipo:
        color = 0x10B981
        stance_icon = "🟢"
    else:
        color = 0xF59E0B
        stance_icon = "🟡"

    name = str(item.get("name") or tk)
    cat = str(item.get("cat") or "核心池")
    price = _opt_float(item.get("price") or item.get("last"))
    last = _opt_float(item.get("last"))
    eff_pct = _opt_float(item.get("effective_pct") if item.get("effective_pct") is not None else item.get("pct_change"))
    pct_rth = _opt_float(item.get("pct_change"))
    ext_px = _opt_float(item.get("ext_price"))
    ext_pct = _opt_float(item.get("ext_pct"))
    sess_code = str(snapshot.get("session_code") or "").strip().upper()
    sess_tag = str(item.get("session_tag") or "").strip()

    anc_s1 = item.get("anchor_s1") if item.get("anchor_s1") is not None else item.get("s1")
    anc_s2 = item.get("anchor_s2") if item.get("anchor_s2") is not None else item.get("s2")
    anc_r1 = item.get("anchor_r1") if item.get("anchor_r1") is not None else item.get("r1")
    anc_r2 = item.get("anchor_r2") if item.get("anchor_r2") is not None else item.get("r2")
    s1_src = item.get("s1_source")
    s2_src = item.get("s2_source")
    r1_src = item.get("r1_source")
    r2_src = item.get("r2_source")
    sr_state = str(item.get("sr_breach_state") or "INTACT").upper()

    if is_ipo:
        one_liner = "刚上市没几天，波动大、规律还没出来，先看少动。"
    elif is_crash:
        one_liner = "正在暴跌，千万别手痒抄底，先看戏等企稳。"
    elif is_cooling:
        one_liner = "刚经历急跌，余震还没消，暂时别急着抄底。"
    elif is_bear:
        one_liner = "趋势走弱还没止跌，千万别手痒抄底，等重新站稳均价线再看。"
    elif is_tp_mode:
        one_liner = "涨太高了别追，手里有货的记得分批落袋保护利润。"
    elif is_inv:
        one_liner = "这是大盘下跌时的反向防守工具，只适合短线对冲，涨多了别追、别长拿。"
    elif is_pit_watch:
        one_liner = "快到低吸位置了，但板块或大盘还偏弱，先忍住别急着接刀。"
    elif is_deep_pb:
        one_liner = "回撤比较深但还没完全止跌，等股价放量站回均价线上方再考虑。"
    elif is_broken_reb:
        one_liner = "大趋势还偏弱，现在只是跌多了反抽一下，快进快出别贪心。"
    elif stance == "BULL":
        if sr_state == "ALL_RESISTANCE_BROKEN":
            one_liner = "走势非常强，已冲破今天所有原定压力，拿稳筹码跟着均价线移动止盈、别追高。"
        elif is_pit:
            one_liner = "跌到了关键支撑区且开始止跌，可留意低吸机会并带好止损。"
        else:
            one_liner = "走势偏强且站稳节奏，可留意回踩均价线或支撑位时的机会，别忘了设止损。"
    else:
        one_liner = "正在不上不下地晃悠，没有高把握买点，先观望最省心。"

    # 1. 💰 价格（严格区分盘后/休市薄盘 vs 正规盘收盘）
    is_post_or_weekend = sess_code in ("POST_MARKET", "WEEKEND") or (not sess_code and sess_tag in ("休市", "盘后"))
    is_pre_or_night = sess_code in ("PRE_MARKET", "OVERNIGHT") or (not sess_code and sess_tag in ("盘前", "夜盘"))
    if is_post_or_weekend:
        if ext_px and ext_px > 0 and last and last > 0:
            price_text = f"盘后 **{_fmt_price(ext_px)}**（盘后 {_fmt_pct(ext_pct)}，休市薄盘别当真）｜正规盘收 **{_fmt_price(last)}**（{_fmt_pct(pct_rth)}）"
        else:
            price_text = f"正规盘收 **{_fmt_price(last or price)}**（{_fmt_pct(pct_rth if pct_rth is not None else eff_pct)}）· 当前休市"
    elif is_pre_or_night:
        ext_lbl = "夜盘" if (sess_code == "OVERNIGHT" or sess_tag == "夜盘") else "盘前"
        if ext_px and ext_px > 0 and last and last > 0:
            price_text = f"{ext_lbl} **{_fmt_price(ext_px)}**（{ext_lbl} {_fmt_pct(ext_pct)}，薄盘供参考）｜上日正规盘收 **{_fmt_price(last)}**（{_fmt_pct(pct_rth)}）"
        else:
            price_text = f"上日正规盘收 **{_fmt_price(last or price)}**（{_fmt_pct(pct_rth if pct_rth is not None else eff_pct)}）· {ext_lbl}暂无新成交"
    else:
        price_text = f"现价 **{_fmt_price(price)}**（今日 {_fmt_pct(pct_rth if pct_rth is not None else eff_pct)} · 常规盘中）"

    # 2. 🎯 怎么操作（彻底解决止盈/观望态还出“参考挂单”的自相矛盾）
    vwap_val = _opt_float(item.get("vwap"))
    vwap_diff = _opt_float(item.get("vwap_diff_pct"))
    vwap_stale = bool(item.get("vwap_stale")) or (sess_code not in ("", "REGULAR"))
    res_badge = str(item.get("resonance_badge_text") or item.get("four_h_div") or "").strip()
    res_suppressed = bool(item.get("resonance_suppressed"))
    opt_lim = item.get("optimal_limit")
    sl_px = item.get("entry_stop_loss")
    tp_px = item.get("target_profit_price")

    op_lines: List[str] = []
    if is_bear:
        op_lines.append("🚫 **别抄底 · 先管住手等右边走稳**")
        vwap_cond = f"放量站稳均价线 {_fmt_price(vwap_val)}" if (vwap_val and vwap_val > 0) else "放量止跌转强"
        if sr_state == "ALL_SUPPORT_BREACHED" and anc_s2:
            op_lines.append(f"观察条件：至少等放量收回原支撑 {_fmt_price(anc_s2)} 或{vwap_cond}，别在下跌半途接刀。")
        else:
            op_lines.append(f"观察条件：至少等{vwap_cond}、不再创新低，确认止跌了再考虑。")
        if res_suppressed and res_badge:
            badge_disp = res_badge if res_badge.startswith("⚠️") else f"⚠️ {res_badge}"
            op_lines.append(f"{badge_disp}（别把弱势超跌误当成见底买点）")
    elif is_tp_mode:
        op_lines.append("🟣 **别追高 · 手里有货的分批止盈**")
        prot_px = _pick_profit_protection_line(item, price)
        if prot_px:
            op_lines.append(f"🛡️ **利润保护线**：**{_fmt_price(prot_px)}**{_rel_pct_str(prot_px, price)}（跌破这里说明冲高结束，先落袋保护利润）")
        else:
            op_lines.append(f"🛡️ **利润保护线**：盯紧当日均价线 {_fmt_price(vwap_val)}，跌破及时落袋。")
        if is_inv:
            op_lines.append("⚠️ 反向杠杆损耗大、波动剧烈，见好就收别恋战；空仓现在绝对别追。")
        else:
            op_lines.append("💡 没上车的别追涨，等充分回踩企稳再说。")
    elif is_inv:
        op_lines.append("🛡️ **反向防守工具 · 别追高，仅限大盘大跌时短线对冲**")
        prot_px = _pick_profit_protection_line(item, price)
        if prot_px:
            op_lines.append(f"🛡️ **利润保护线**：**{_fmt_price(prot_px)}**{_rel_pct_str(prot_px, price)}（走弱及时兑现，不长期拿着耗损净值）")
    elif stance != "BULL":
        op_lines.append("👀 **先观望 · 暂不急着买入**")
        vwap_cond = f"站稳均价线 {_fmt_price(vwap_val)}" if (vwap_val and vwap_val > 0) else "走出明确强势"
        if is_broken_reb and anc_r1:
            op_lines.append(f"短线反抽到原压力 {_fmt_price(anc_r1)} 附近容易遇阻，{vwap_cond}前小仓短做或继续看戏。")
        else:
            op_lines.append(f"等待股价{vwap_cond}并走出高胜率多头信号再动手。")
    else:
        if (_opt_float(opt_lim) or 0) > 0:
            op_lines.append(f"🟢 **参考关注价**：**{_fmt_price(opt_lim)}**{_rel_pct_str(opt_lim, price)}（回踩这附近企稳再考虑）")
        else:
            op_lines.append(f"🟢 **顺势低吸**：不追高，留意回踩均价线 {_fmt_price(vwap_val)} 附近企稳机会")
        if (_opt_float(sl_px) or 0) > 0:
            op_lines.append(f"🛡️ **防守止损线**：**{_fmt_price(sl_px)}**{_rel_pct_str(sl_px, price)}（跌破先撤退保本金）")
        tp_use = tp_px if (_opt_float(tp_px) or 0) > 0 else anc_r1
        if (_opt_float(tp_use) or 0) > 0:
            op_lines.append(f"🏁 **上方目标参考**：**{_fmt_price(tp_use)}**{_rel_pct_str(tp_use, price)}")
        if sr_state == "ALL_RESISTANCE_BROKEN":
            op_lines.append("🚀 今日原定压力已全破，不猜天花板，沿均价线移动止盈。")

    # 3. 🧱 关键价位（大白话接管 V4.4 静态锚 + ATR 估算标注）
    def _src_plain(src_val: Any) -> str:
        return "（估算参考，无真实筹码）" if str(src_val or "").upper() == "ATR_ESTIMATE" else ""

    if sr_state == "ALL_SUPPORT_BREACHED":
        levels_lines = [
            f"⚠️ **今日原定支撑已全线跌穿**（原支撑一 {_fmt_price(anc_s1)} / 原支撑二 {_fmt_price(anc_s2)}），**下方无真实支撑，别猜底**",
            f"上方先看压力：原支撑二 **{_fmt_price(anc_s2)}**{_src_plain(s2_src)} ➔ 原压力一 **{_fmt_price(anc_r1)}**{_src_plain(r1_src)}",
        ]
    elif sr_state == "S1_BREACHED":
        levels_lines = [
            f"📉 原支撑一 {_fmt_price(anc_s1)} 已跌穿（转为上方压力），半空别接刀",
            f"下方退守支撑二：**{_fmt_price(anc_s2)}**{_src_plain(s2_src)}{_rel_pct_str(anc_s2, price)} ｜ 上方压力：{_fmt_price(anc_s1)} / {_fmt_price(anc_r1)}{_src_plain(r1_src)}",
        ]
    elif sr_state == "ALL_RESISTANCE_BROKEN":
        levels_lines = [
            f"🚀 **已突破今日全部原定阻力**（原压力一 {_fmt_price(anc_r1)} / 原压力二 {_fmt_price(anc_r2)}），上方不硬猜天花板",
            f"原压力二 **{_fmt_price(anc_r2)}** 现转为防守底线{_rel_pct_str(anc_r2, price)}，跌不破可继续拿着跟趋势",
        ]
    elif sr_state == "R1_BROKEN":
        levels_lines = [
            f"📈 **已突破原阻力 {_fmt_price(anc_r1)}（现转为支撑）**{_rel_pct_str(anc_r1, price)}，上看 **{_fmt_price(anc_r2)}**{_src_plain(r2_src)}{_rel_pct_str(anc_r2, price)}",
            f"不追冲高针，等回踩原阻力 {_fmt_price(anc_r1)} 或均价线站稳更稳妥",
        ]
    else:
        sup_s = f"下方支撑：**{_fmt_price(anc_s1)}**{_src_plain(s1_src)}{_rel_pct_str(anc_s1, price)}"
        if anc_s2:
            sup_s += f" / 深度支撑 {_fmt_price(anc_s2)}{_src_plain(s2_src)}"
        res_s = f"上方压力：**{_fmt_price(anc_r1)}**{_src_plain(r1_src)}{_rel_pct_str(anc_r1, price)}"
        if anc_r2:
            res_s += f" / 强压力 {_fmt_price(anc_r2)}{_src_plain(r2_src)}"
        levels_lines = [sup_s, res_s]

    # 4. 📊 通俗体检（把所有技术指标翻成 4 句大白话）
    vr_snap = snapshot.get("vix_regime") if isinstance(snapshot.get("vix_regime"), dict) else {}
    if vwap_val and vwap_val > 0 and price:
        diff_v = vwap_diff if vwap_diff is not None else (((price - vwap_val) / vwap_val) * 100.0)
        pos_w = "上方" if diff_v >= 0 else "下方"
        mood_w = "偏强（买盘占优）" if diff_v >= 0.3 else ("偏弱（上方有抛压）" if diff_v <= -0.3 else "贴近均价（多空胶着）")
        stale_w = "（昨日参考）" if vwap_stale else ""
        vwap_line = f"• **均价线**：现价在均价线 {_fmt_price(vwap_val)} {pos_w}（{diff_v:+.1f}%），{mood_w}{stale_w}"
    else:
        vwap_line = "• **均价线**：暂无当日均价数据"

    cor = item.get("corridor") if isinstance(item.get("corridor"), dict) else {}
    cor_stat = str(cor.get("status") or "").upper()
    if cor_stat == "BELOW_P25":
        path_desc = "比预期弱很多，已跌破正常波动范围"
    elif cor_stat == "ABOVE_P75":
        path_desc = "走势很强，超出正常预期"
    elif cor_stat == "IN_CORRIDOR":
        path_desc = "基本按正常预期节奏走，没明显走坏"
    else:
        path_desc = "今日正常波动范围尚未生成"
    pm_hl = ""
    if cor.get("pmh") or cor.get("pml"):
        pm_hl = f"（盘前高 {_fmt_price(cor.get('pmh'))} / 盘前低 {_fmt_price(cor.get('pml'))}）"
    path_line = f"• **今日强弱**：{path_desc}{pm_hl}"

    rs20 = _opt_float(item.get("rs_20d"))
    if rs20 is None:
        rs_desc = "近一月对比大盘数据暂缺"
    elif rs20 >= 8.0:
        rs_desc = f"明显跑赢大盘（近一月强于大盘 {rs20:+.1f}%）"
    elif rs20 >= 2.0:
        rs_desc = f"小幅跑赢大盘（近一月强于大盘 {rs20:+.1f}%）"
    elif rs20 > -2.0:
        rs_desc = f"基本跟着大盘走（近一月和大盘差 {rs20:+.1f}%）"
    elif rs20 > -8.0:
        rs_desc = f"略微跑输大盘（近一月落后 {rs20:+.1f}%）"
    else:
        rs_desc = f"明显跑输大盘（近一月落后 {rs20:+.1f}%）"
    rs_line = f"• **近一月表现**：{rs_desc}"

    l_wr = _opt_float(item.get("ai_long_win_rate_pct"))
    l_raw = _opt_float(item.get("ai_long_raw_pct"))
    is_capped = (not is_inv) and (
        bool(item.get("ai_regime_capped"))
        or bool(
            (vr_snap.get("vix_hard_brake") or str(snapshot.get("market_regime") or "").upper() == "BEAR_DEFENSE")
            and l_raw is not None
            and l_wr is not None
            and (l_raw - l_wr) >= 5.0
        )
    )
    cap_txt = "（大盘太恐慌，系统已强制降档）" if is_capped else ""
    ai_line = f"• **AI 胜率**：做多参考胜率 **{_fmt_num(l_wr, 1, '%')}**{cap_txt}"
    if res_suppressed and res_badge:
        ai_line += f" ｜ {res_badge}"

    exam_lines = [vwap_line, path_line, rs_line, ai_line]
    bear_inertia_on = bool(item.get("bear_inertia_active")) and (not is_inv)
    if bear_inertia_on:
        p15_t5 = _opt_float(item.get("p15_t5_pct"))
        if p15_t5 is not None:
            tc_bear_line = f"• **15日推演**：⛔ 空头惯性延续，已切入空头防守同境模式（T+5 悲观下沿 {_fmt_pct(p15_t5)}，暂缓盲目看涨反弹）"
        else:
            tc_bear_line = "• **15日推演**：⛔ 空头惯性延续，已切入空头防守同境模式（暂缓盲目看涨反弹）"
        exam_lines.append(tc_bear_line)

    fields = [
        {"name": "💰 价格", "value": _strip_bare_hr_lines(price_text)[:1024], "inline": False},
        {"name": "🎯 怎么操作", "value": _strip_bare_hr_lines("\n".join(op_lines))[:1024], "inline": False},
        {"name": "🧱 关键价位", "value": _strip_bare_hr_lines("\n".join(levels_lines))[:1024], "inline": False},
        {
            "name": "📊 通俗体检",
            "value": _strip_bare_hr_lines("\n".join(exam_lines))[:1024],
            "inline": False,
        },
    ]

    return {
        "title": f"{stance_icon} {tk} · {name}（{cat}）"[:256],
        "description": _strip_bare_hr_lines(
            f"💬 一句话结论：**{one_liner}**\n"
            f"大盘环境：{_plain_regime_label(snapshot)} ｜ 天气：{snapshot.get('weather') or '稳健观察'}"
        ),
        "color": color,
        "fields": fields,
        "timestamp": now_iso,
        "footer": {"text": footer_txt},
    }


def build_vix_embed_dict(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """构建 `/vix` 今天大盘安全吗？大白话诊断 Embed 字典。"""
    footer_txt = _build_footer_text(snapshot)
    now_iso = datetime.now(timezone.utc).isoformat()

    if not isinstance(snapshot, dict):
        return {
            "title": "⚡ 今天大盘安全吗？",
            "description": _strip_bare_hr_lines(
                "💬 一句话结论：主雷达正在刷新最新恐慌数据，请稍等片刻再查。"
            ),
            "color": 0xF59E0B,
            "timestamp": now_iso,
            "footer": {"text": footer_txt},
        }

    vr = snapshot.get("vix_regime") if isinstance(snapshot.get("vix_regime"), dict) else {}
    liq = snapshot.get("cross_market_liquidity") if isinstance(snapshot.get("cross_market_liquidity"), dict) else {}
    opx = snapshot.get("opex_shield") if isinstance(snapshot.get("opex_shield"), dict) else {}

    vix_state = str(vr.get("vix_state") or "UNAVAILABLE").upper()
    color_map = {
        "BACKWARDATION_BRAKE": 0xEF4444,
        "WASHOUT_BOTTOM_WINDOW": 0x10B981,
        "BOTTOM_PREMARKET_PREVIEW": 0xF59E0B,
        "ELEVATED_CAUTION": 0xF59E0B,
        "CALM": 0x38BDF8,
    }
    color = color_map.get(vix_state, 0x64748B)

    headline_map = {
        "BACKWARDATION_BRAKE": "市场非常恐慌，机构还在抢避险，今天管住手别抄底。",
        "WASHOUT_BOTTOM_WINDOW": "恐慌明显退潮，可以开始关注右侧抄底机会。",
        "BOTTOM_PREMARKET_PREVIEW": "盘前恐慌有降温苗头，但还没开正式盘不算数，等美东 09:30 开盘确认后再动手。",
        "ELEVATED_CAUTION": "市场情绪依然偏紧张，轻仓防守为主，别盲目重仓抄底。",
        "CALM": "大盘情绪平稳，按个股节奏正常操作即可。",
    }
    headline = headline_map.get(vix_state, "正在监测市场恐慌情绪，控制仓位按强弱选股。")

    vix_spot = _opt_float(vr.get("vix_spot"))
    vxn_spot = _opt_float(vr.get("vxn_spot"))
    spread_v = _opt_float(vr.get("vxn_vix_spread"))
    ratio_v = _opt_float(vr.get("vix_term_ratio"))
    tnx_v = _opt_float(vr.get("tnx_yield"))
    peak_v = _opt_float(vr.get("vix_10d_peak"))
    dd_v = _opt_float(vr.get("vix_drawdown_from_peak_pct"))

    if vix_spot is None:
        vix_mood = "暂无数据"
    elif vix_spot >= 30:
        vix_mood = "很慌"
    elif vix_spot >= 22:
        vix_mood = "偏紧张"
    elif vix_spot >= 16:
        vix_mood = "小有波动"
    else:
        vix_mood = "很平静"

    if spread_v is None:
        tech_mood = "科技股恐慌暂无数据"
    elif spread_v >= 4.0:
        tech_mood = "跌得比大盘狠"
    elif spread_v >= 2.0:
        tech_mood = "科技股比大盘更紧张"
    else:
        tech_mood = "和大盘情绪差不多"

    if ratio_v is None:
        term_plain = "远期恐慌数据暂缺"
    elif ratio_v >= 1.00:
        term_plain = f"短期比长期还慌（倒挂 {ratio_v:.2f}，还没跌透）"
    elif ratio_v >= 0.94:
        term_plain = f"短长期接近（比值 {ratio_v:.2f}，情绪还在消化）"
    else:
        term_plain = f"短期低于长期（比值 {ratio_v:.2f}，恐慌结构恢复正常）"

    peak_note = f" ｜ 近10天最高 {_fmt_num(peak_v, 1)}（已回落 {_fmt_num(dd_v, 1, '%')}）" if peak_v is not None else ""
    vix_lines = [
        f"• 大盘恐慌 **{_fmt_num(vix_spot, 1)}**（{vix_mood}）｜科技股恐慌 **{_fmt_num(vxn_spot, 1)}**（{tech_mood}）",
        f"• 短期 vs 长期：**{term_plain}**",
        f"• 10年美债利率：**{_fmt_num(tnx_v, 2, '%')}**{peak_note}",
    ]

    if vix_state == "BACKWARDATION_BRAKE":
        gate_lines = [
            "• **波段抄底**：🔴 **红灯停** — 机构还在抢短期避险，需等恐慌比值回落到 0.97 以下、并从高点明显回落后再亮灯",
            "• **日内抢反弹**：🔴 **禁止抄底** — 短期倒挂踩踏期极易接在半山腰",
        ]
    elif vix_state == "WASHOUT_BOTTOM_WINDOW":
        gate_lines = [
            "• **波段抄底**：🟢 **绿灯亮了** — 恐慌已从高位明显退潮且结构恢复正常，可留意抗跌龙头右侧机会",
            "• **日内抢反弹**：🟢 **允许参与** — 可依托均价线和支撑位顺势低吸",
        ]
    elif vix_state == "BOTTOM_PREMARKET_PREVIEW":
        gate_lines = [
            "• **波段抄底**：🟡 **黄灯预热** — 盘前恐慌看着在降温，但远期合约还没开盘，需等美东 09:30 开正式盘确认才算亮灯",
            "• **日内抢反弹**：🟡 **等开盘** — 美东 09:30 开盘后再动手，盘前不提前抢跑",
        ]
    elif vix_state == "ELEVATED_CAUTION":
        gate_lines = [
            "• **波段抄底**：🟡 **黄灯等待** — 恐慌还没彻底消退，还差自高点继续回落并稳在 0.97 以下的确认",
            "• **日内抢反弹**：🟡 **轻仓挑强** — 只做明显强于大盘的票，破位弱股不接刀",
        ]
    else:
        gate_lines = [
            "• **波段抄底**：⚪ **无需避险刹车** — 大盘没有系统性恐慌，按个股自身买点正常操作",
            "• **日内抢反弹**：🟢 **正常允许** — 关注均价线附近低吸节奏即可",
        ]

    hyg_5d = _opt_float(liq.get("hyg_ief_5d_pct"))
    dxy_5d = _opt_float(liq.get("dxy_5d_pct"))
    jpy_5d = _opt_float(liq.get("usdjpy_5d_pct"))

    if hyg_5d is None:
        hyg_plain = "暂无近5天数据"
    elif hyg_5d <= -0.8:
        hyg_plain = "资金明显在躲风险"
    elif hyg_5d >= 0.8:
        hyg_plain = "资金愿意买风险资产"
    else:
        hyg_plain = "风险偏好大致平稳"

    if dxy_5d is None:
        dxy_plain = "变动不大"
    elif dxy_5d >= 0.8:
        dxy_plain = "美元走强，场外资金偏紧"
    elif dxy_5d <= -0.8:
        dxy_plain = "美元走弱，资金压力缓和"
    else:
        dxy_plain = "波动温和"

    if jpy_5d is None:
        jpy_plain = "平稳"
    elif jpy_5d <= -1.2:
        jpy_plain = "日元急升，小心套息资金撤退"
    else:
        jpy_plain = "走势平稳"

    if opx.get("active"):
        kind_cn = "四巫日" if opx.get("is_quad_witching") else "月度期权到期日"
        when_cn = "今天" if str(opx.get("timing") or "").upper() == "TODAY" else "明天"
        opx_line = f"• **期权到期日提醒**：⚠️ {when_cn}是{kind_cn}，尾盘容易剧烈波动，小心冲高回落假突破"
    else:
        kind_cn = "四巫日" if opx.get("next_is_quad_witching") else "月度期权到期日"
        days_s = opx.get("days_to_opex", "--")
        opx_line = f"• **期权到期日提醒**：距下次{kind_cn}（{opx.get('next_opex_date') or '--'}）还有 **{days_s} 天**，近期暂无到期扰动"

    liq_lines = [
        f"• **高收益债偏好（跌 = 资金在躲风险）**：近5天 `{_fmt_pct(hyg_5d)}`（{hyg_plain}）",
        f"• **美元指数**：{_fmt_num(liq.get('dxy_spot'))}（近5天 `{_fmt_pct(dxy_5d)}`，{dxy_plain}）｜**日元**：{_fmt_num(liq.get('usdjpy_spot'))}（近5天 `{_fmt_pct(jpy_5d)}`，{jpy_plain}）",
        opx_line,
    ]
    mt = snapshot.get("market_temperature") if isinstance(snapshot.get("market_temperature"), dict) else {}
    futs = snapshot.get("futures") if isinstance(snapshot.get("futures"), dict) else (mt.get("futures") if isinstance(mt.get("futures"), dict) else {})
    if mt or futs:
        nq_i = futs.get("NQ=F") if isinstance(futs.get("NQ=F"), dict) else {}
        es_i = futs.get("ES=F") if isinstance(futs.get("ES=F"), dict) else {}
        nq_p = _fmt_pct(_opt_float(nq_i.get("pct_chg")))
        es_p = _fmt_pct(_opt_float(es_i.get("pct_chg")))
        mb_s = _fmt_num(_opt_float(mt.get("macro_bubble_score")), 0)
        ib_s = _fmt_num(_opt_float(mt.get("intraday_bubble_score")), 0)
        mbt_s = _fmt_num(_opt_float(mt.get("macro_bottom_score")), 0)
        ibt_s = _fmt_num(_opt_float(mt.get("intraday_bottom_score")), 0)
        lock_s = "🔒" if (mt.get("knife_catch_locked") or mt.get("intraday_knife_catch_locked")) else ""
        liq_lines.append(
            f"• **期货先导 (NQ/ES)**：纳指 `{nq_p}` ｜ 标普 `{es_p}` ｜ **泡沫/抄底双温**：泡沫 **{mb_s}/{ib_s}** · 抄底 **{mbt_s}/{ibt_s}{lock_s}**（查详情用 `/temp`）"
        )

    return {
        "title": "⚡ 今天大盘安全吗？",
        "description": _strip_bare_hr_lines(
            f"💬 一句话结论：**{headline}**\n"
            f"大盘环境：{_plain_regime_label(snapshot)} ｜ 天气：**{snapshot.get('weather') or '观察中'}**"
        ),
        "color": color,
        "fields": [
            {"name": "🌡️ 恐慌温度", "value": _strip_bare_hr_lines("\n".join(vix_lines))[:1024], "inline": False},
            {"name": "🚦 抄底灯亮了吗", "value": _strip_bare_hr_lines("\n".join(gate_lines))[:1024], "inline": False},
            {"name": "🌊 场外资金风向", "value": _strip_bare_hr_lines("\n".join(liq_lines))[:1024], "inline": False},
        ],
        "timestamp": now_iso,
        "footer": {"text": footer_txt},
    }


def build_temp_embed_dict(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """构建 `/temp` 大盘与日内泡沫/抄底双温度计及股指期货先导大白话诊断 Embed 字典。"""
    footer_txt = _build_footer_text(snapshot)
    now_iso = datetime.now(timezone.utc).isoformat()

    if not isinstance(snapshot, dict):
        return {
            "title": "🌡️ 大盘泡沫与抄底温度计（不含BTC）",
            "description": _strip_bare_hr_lines(
                "💬 一句话结论：主雷达正在刷新最新股指期货与温度计数据，请稍等片刻再查。"
            ),
            "color": 0xF59E0B,
            "timestamp": now_iso,
            "footer": {"text": footer_txt},
        }

    mt = snapshot.get("market_temperature") if isinstance(snapshot.get("market_temperature"), dict) else {}
    futs = snapshot.get("futures") if isinstance(snapshot.get("futures"), dict) else (mt.get("futures") if isinstance(mt.get("futures"), dict) else {})
    if not mt and not futs:
        return {
            "title": "🌡️ 大盘泡沫与抄底温度计（不含BTC）",
            "description": _strip_bare_hr_lines(
                "💬 一句话结论：当前快照尚未写入最新温度计数据，等待主雷达下一轮刷新后即可查看。"
            ),
            "color": 0x64748B,
            "timestamp": now_iso,
            "footer": {"text": footer_txt},
        }

    mb = _opt_float(mt.get("macro_bubble_score"))
    ib = _opt_float(mt.get("intraday_bubble_score"))
    mbt = _opt_float(mt.get("macro_bottom_score"))
    ibt = _opt_float(mt.get("intraday_bottom_score"))
    macro_locked = bool(mt.get("knife_catch_locked", False))
    intra_locked = bool(mt.get("intraday_knife_catch_locked", False))
    src_tag = str(mt.get("source_tag") or "期货实时")
    headline = str(mt.get("headline_cn") or "按温度计强弱与均价线节奏控制仓位，不盲目追高或接刀。")

    if macro_locked or intra_locked or (mb is not None and mb >= 80.0) or (ib is not None and ib >= 75.0):
        color = 0xEF4444
    elif (mbt is not None and mbt >= 65.0) or (ibt is not None and ibt >= 65.0):
        color = 0x10B981
    elif (mb is not None and mb >= 65.0) or (ib is not None and ib >= 60.0):
        color = 0xF59E0B
    else:
        color = 0x38BDF8

    nq = futs.get("NQ=F") if isinstance(futs.get("NQ=F"), dict) else {}
    es = futs.get("ES=F") if isinstance(futs.get("ES=F"), dict) else {}

    def _fmt_fut_line(label: str, item: Dict[str, Any]) -> str:
        px = _opt_float(item.get("last"))
        pct = _opt_float(item.get("pct_chg"))
        vdiff = _opt_float(item.get("vwap_diff_pct"))
        stag = str(item.get("status_tag") or "24H 期货实时")
        px_s = f"{px:,.2f}" if px is not None else "--"
        return f"• **{label}**：`{px_s}`（`{_fmt_pct(pct)}`）｜ 相对24H均价线 `{_fmt_pct(vdiff)}` ｜ {stag}"

    fut_lines = [
        _fmt_fut_line("纳指100期货 (NQ=F)", nq),
        _fmt_fut_line("标普500期货 (ES=F)", es),
    ]

    bubble_lines = [
        f"• **大盘整体泡沫**：**{_fmt_num(mb, 1)} / 100** — {mt.get('macro_bubble_tier') or '正常健康'}",
        f"• **日内泡沫（{src_tag}）**：**{_fmt_num(ib, 1)} / 100** — {mt.get('intraday_bubble_tier') or '日内无追高泡沫'}",
    ]

    lock_tag_m = " 🔒（倒挂/阴跌硬接刀锁生效，封顶 ≤50）" if macro_locked else ""
    lock_tag_i = " 🔒（日内单边阴跌接刀锁生效，封顶 ≤50）" if intra_locked else ""
    bottom_lines = [
        f"• **大盘整体抄底**：**{_fmt_num(mbt, 1)} / 100** — {mt.get('macro_bottom_tier') or '未到抄底区'}{lock_tag_m}",
        f"• **日内抄底（{src_tag}）**：**{_fmt_num(ibt, 1)} / 100** — {mt.get('intraday_bottom_tier') or '日内未见超卖抄底点'}{lock_tag_i}",
    ]

    return {
        "title": "🌡️ 大盘泡沫与抄底温度计（纯美股 · 不含BTC）",
        "description": _strip_bare_hr_lines(
            f"💬 一句话结论：**{headline}**\n"
            f"统计口径：仅计纯美股核心指数与股票（严格剔除加密、黄金、美债与反向标的）｜ 日内数据源：**{src_tag}**"
        ),
        "color": color,
        "fields": [
            {"name": "📡 股指期货先导 (NQ=F / ES=F)", "value": _strip_bare_hr_lines("\n".join(fut_lines))[:1024], "inline": False},
            {"name": "🔥 泡沫评分（高分防追高）", "value": _strip_bare_hr_lines("\n".join(bubble_lines))[:1024], "inline": False},
            {"name": "🧲 抄底指数（右侧解禁才动手）", "value": _strip_bare_hr_lines("\n".join(bottom_lines))[:1024], "inline": False},
        ],
        "timestamp": now_iso,
        "footer": {"text": footer_txt},
    }


AI_OWNER_DENY_MSG = "🔒 此指令仅限雷达管理员本人使用"
AI_COOLDOWN_SECONDS = 10.0


def build_ai_embed_dict(
    target: str,
    brief_result: Optional[Dict[str, Any]],
    snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建 `/ai` 管理员私有 AI 首席策略师大白话剧本 Embed 字典。"""
    footer_txt = _build_footer_text(snapshot)
    now_iso = datetime.now(timezone.utc).isoformat()
    tk = str(target or "OVERVIEW").strip().upper()
    meta_map = {
        "OVERVIEW": "大盘全景",
        "QQQ": "QQQ (纳指 100)",
        "SPY": "SPY / VOO (标普500 · 走势100%同步)",
        "SOXX": "SOXX (半导体 ETF)",
    }
    title_lbl = meta_map.get(tk, tk)
    res = brief_result if isinstance(brief_result, dict) else {}
    status = str(res.get("status") or "ERROR").upper()
    risk_locked = bool(res.get("risk_lock_active", False))
    provider_used = str(res.get("provider_used") or "未配置")
    upd_et = str(res.get("updated_at_et") or "")
    upd_kst = str(res.get("updated_at_kst") or "")
    body_txt = str(res.get("text") or "").strip()

    if status != "OK" or not body_txt:
        err_short = str(res.get("error_short") or "").strip()
        err_suffix = f"（原因：{err_short}）" if err_short else ""
        err_hint = (
            "尚未配置 GEMINI_API_KEY 或 DEEPSEEK_API_KEY，请在服务端 .env 配置后重试。"
            if status == "UNCONFIGURED"
            else f"当前大模型接口临时繁忙或暂无缓存{err_suffix}，请稍后重试（或切换另一引擎）。"
        )
        return {
            "title": f"🧠 AI 首席策略师 · {title_lbl}",
            "description": _strip_bare_hr_lines(f"💬 一句话结论：{err_hint}"),
            "color": 0xF59E0B,
            "timestamp": now_iso,
            "footer": {"text": f"引擎: {provider_used} ｜ {footer_txt}"},
        }

    lock_badge = "⛔ 硬风控锁生效中（严禁左侧抄底）" if risk_locked else "🛡️ 硬风控监控正常"
    color = 0xEF4444 if risk_locked else 0x38BDF8
    ts_line = f"{upd_et}" + (f" / {upd_kst}" if upd_kst else "")
    desc = _strip_bare_hr_lines(
        f"**{lock_badge}** ｜ 引擎：`{provider_used}` ｜ 更新：`{ts_line or '刚刚'}`\n\n{body_txt}"
    )[:4000]
    return {
        "title": f"🧠 AI 首席策略师 · {title_lbl}",
        "description": desc,
        "color": color,
        "timestamp": now_iso,
        "footer": {"text": footer_txt},
    }


def handle_ai_command_request(
    user_id: Any,
    target: str = "OVERVIEW",
    engine: str = "auto",
    refresh: bool = True,
    snapshot: Optional[Dict[str, Any]] = None,
    cache_path: Optional[str] = None,
    env_path: Optional[str] = None,
    env_override: Optional[Dict[str, str]] = None,
    http_post_fn: Any = None,
) -> Dict[str, Any]:
    """纯函数封装 `/ai` 权限白名单校验 + 缓存/实时双引擎调用（严格白名单前拦截，未授权 0 次调用 LLM）。"""
    if ai_commentary_engine is None:
        return {
            "allowed": False,
            "ephemeral": True,
            "message": "❌ AI 解盘引擎模块未加载。",
            "embed": None,
        }
    if not ai_commentary_engine.is_discord_owner(user_id, env_path=env_path, env_override=env_override):
        return {
            "allowed": False,
            "ephemeral": True,
            "message": AI_OWNER_DENY_MSG,
            "embed": None,
        }

    tk = str(target or "OVERVIEW").strip().upper()
    if tk not in ai_commentary_engine.VALID_TARGETS:
        tk = "OVERVIEW"
    cpath = cache_path or ai_commentary_engine.DEFAULT_CACHE_PATH

    if not refresh:
        cached = ai_commentary_engine.load_ai_commentary_cache(cpath)
        items = cached.get("items") if isinstance(cached.get("items"), dict) else {}
        if isinstance(items.get(tk), str) and items.get(tk).strip():
            tk_locked, tk_reasons = ai_commentary_engine.evaluate_risk_lock(snapshot, target=tk)
            res_obj = {
                "status": "OK",
                "target": tk,
                "text": ai_commentary_engine.apply_post_risk_safety_net(items[tk], risk_lock_active=tk_locked),
                "provider_used": f"{cached.get('provider_used') or '缓存'} (缓存)",
                "risk_lock_active": tk_locked or bool(cached.get("risk_lock_active", False)),
                "risk_lock_reasons": tk_reasons or cached.get("risk_lock_reasons", []),
                "updated_at_et": cached.get("updated_at_et", ""),
                "updated_at_kst": cached.get("updated_at_kst", ""),
            }
            return {
                "allowed": True,
                "ephemeral": None,
                "message": "",
                "embed": build_ai_embed_dict(tk, res_obj, snapshot=snapshot),
                "result": res_obj,
            }

    res_obj = ai_commentary_engine.generate_single_brief(
        target=tk,
        snapshot=snapshot,
        provider=engine,
        env_path=env_path,
        env_override=env_override,
        http_post_fn=http_post_fn,
        cache_path=cpath,
        write_back_cache=True,
    )
    return {
        "allowed": True,
        "ephemeral": None,
        "message": "",
        "embed": build_ai_embed_dict(tk, res_obj, snapshot=snapshot),
        "result": res_obj,
    }


def build_top5_embed_dict(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """构建 `/top5` 大盘震荡期最抗跌的 5 只票（紧凑 2 行防折行、休市统一用正规盘数据）。"""
    footer_txt = _build_footer_text(snapshot)
    now_iso = datetime.now(timezone.utc).isoformat()

    if not isinstance(snapshot, dict):
        return {
            "title": "🛡️ 大盘震荡期最抗跌的 5 只票",
            "description": _strip_bare_hr_lines(
                "💬 一句话结论：主雷达正在刷新最新排名，请稍等片刻再查。"
            ),
            "color": 0xF59E0B,
            "timestamp": now_iso,
            "footer": {"text": footer_txt},
        }

    top5 = snapshot.get("resilient_leaders_top5")
    if not isinstance(top5, list) or not top5:
        return {
            "title": "🛡️ 大盘震荡期最抗跌的 5 只票",
            "description": _strip_bare_hr_lines(
                "💬 一句话结论：现在没有同时抗跌又走稳的股票，先管住手等机会。\n"
                "已自动排除反向做空、黄金、国债、比特币和刚暴跌的股票，目前无标的达标。"
            ),
            "color": 0x64748B,
            "timestamp": now_iso,
            "footer": {"text": footer_txt},
        }

    sess_code = str(snapshot.get("session_code") or "").strip().upper()
    is_closed_rth = sess_code in ("POST_MARKET", "WEEKEND")
    px_lbl = "收" if is_closed_rth else ("盘前" if sess_code == "PRE_MARKET" else "现")

    rows: List[str] = []
    for idx, ld in enumerate(top5[:5], 1):
        tk = str(ld.get("ticker") or "--")
        name = str(ld.get("name") or tk)
        cat = str(ld.get("cat") or "")
        px_val = ld.get("rth_close") if (is_closed_rth and ld.get("rth_close") is not None) else ld.get("price")
        pct_val = ld.get("rth_pct") if (is_closed_rth and ld.get("rth_pct") is not None) else ld.get("pct")
        px_s = _fmt_price(px_val)
        pct_num = _opt_float(pct_val) or 0.0
        pct_s = _fmt_pct(pct_num)
        exc_num = _opt_float(ld.get("excess_vs_spy")) or 0.0
        exc_word = f"比大盘强 {exc_num:+.1f}%" if exc_num >= 0 else f"比大盘弱 {exc_num:+.1f}%"

        rs_num = _opt_float(ld.get("rs_20d")) or 0.0
        if rs_num >= 8.0:
            month_str = "近一月强势"
        elif rs_num >= 2.0:
            month_str = "近一月偏强"
        elif rs_num > -2.0:
            month_str = "近一月平稳"
        else:
            month_str = "近一月偏弱"

        if not is_closed_rth and sess_code == "REGULAR" and ld.get("above_vwap"):
            short_badge = "站稳均价"
        elif pct_num > 0:
            short_badge = "正涨幅"
        elif exc_num > 0.3:
            short_badge = "强于大盘"
        else:
            short_badge = "相对抗跌"

        cat_part = f"（{cat}）" if cat else ""
        rows.append(
            f"**{idx}. {tk}** {name}{cat_part} · {short_badge}\n"
            f"{px_lbl} {px_s} ({pct_s})｜{exc_word}｜{month_str}"
        )

    return {
        "title": "🛡️ 大盘震荡期最抗跌的 5 只票",
        "description": _strip_bare_hr_lines(
            "💬 一句话结论：大盘恐慌时，这 5 只股票最抗跌、资金还在买（已自动排除反向做空和黄金国债）：\n\n"
            + "\n\n".join(rows)
        ),
        "color": 0x10B981,
        "timestamp": now_iso,
        "footer": {"text": footer_txt},
    }


def build_ledger_embed_dict(
    ledger_path: Optional[str] = None,
    snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建 `/ledger` 最近 30 天提醒准不准？大白话战绩摘要 Embed 字典。"""
    footer_txt = _build_footer_text(snapshot)
    now_iso = datetime.now(timezone.utc).isoformat()
    target_ledger = ledger_path or DEFAULT_LEDGER_PATH

    if signal_ledger is None:
        return {
            "title": "📒 最近 30 天提醒准不准？",
            "description": _strip_bare_hr_lines(
                "💬 一句话结论：记账本模块暂时未加载，稍后再试。"
            ),
            "color": 0x64748B,
            "timestamp": now_iso,
            "footer": {"text": footer_txt},
        }

    summary = signal_ledger.get_recent_summary(days=30, max_recent=5, ledger_path=target_ledger)
    total_rec = int(summary.get("total_records") or 0)

    if total_rec == 0:
        return {
            "title": "📒 最近 30 天提醒准不准？",
            "description": _strip_bare_hr_lines(
                "💬 一句话结论：📒 账本已就绪，从下周第一条实盘提醒开始自动记账复盘\n\n"
                "之后主雷达每发一条实盘提醒，系统都会悄悄记上一笔，并在 1 小时后、当天收盘、第 3 天和第 5 天自动核对涨跌，"
                "把**做多提醒准不准**和**提醒别抄底拦得准不准**分开算账。"
            ),
            "color": 0x38BDF8,
            "timestamp": now_iso,
            "footer": {"text": footer_txt},
        }

    long_wr = summary.get("long_win_rate_pct")
    long_wr_s = f"**{long_wr:.1f}%**（{summary.get('long_wins', 0)}/{summary.get('long_settled', 0)} 次做对）" if long_wr is not None else "待结算"
    def_eff = summary.get("defense_eff_rate_pct")
    def_eff_s = f"**{def_eff:.1f}%**（{summary.get('defense_hits', 0)}/{summary.get('defense_settled', 0)} 次拦对）" if def_eff is not None else "暂无/待结算"

    long_head = f"{long_wr:.0f}%" if long_wr is not None else "待结算"
    def_head = f"{def_eff:.0f}%" if def_eff is not None else "待结算"

    kpi_lines = [
        f"💬 一句话结论：最近 30 天发了 {total_rec} 次提醒，做多胜率 {long_head}，拦住别抄底有效率 {def_head}。\n",
        f"• **最近 30 天发了**：**{total_rec} 次提醒**（已核对 {summary.get('settled_total', 0)} 次）",
        f"• 📈 **做多提醒准不准**（{summary.get('long_total', 0)} 次）：胜率 {long_wr_s}",
        f"• 🛡️ **提醒别抄底拦得准不准**（{summary.get('defense_total', 0)} 次）：拦截有效率 {def_eff_s}",
    ]

    recent_lines: List[str] = []
    for rec in (summary.get("recent_records") or [])[:5]:
        is_def = (rec.get("direction") == "BEAR_DEFENSE")
        dir_tag = "🛡️ 提醒别抄底" if is_def else "📈 做多提醒"
        hit_v = rec.get("hit")
        res_icon = "✅" if hit_v is True else ("❌" if hit_v is False else "⏳")
        eod_s = _fmt_pct(rec.get("ret_eod_pct"))
        t3_s = _fmt_pct(rec.get("ret_t3_pct"))
        recent_lines.append(
            f"{res_icon} {rec.get('date_et')} · **{rec.get('ticker')}**（{dir_tag} · {rec.get('signal_label_cn')}）\n"
            f"提醒价 {_fmt_price(rec.get('entry_price'))}｜当天收盘 {eod_s}｜3天后 {t3_s}"
        )

    return {
        "title": "📒 最近 30 天提醒准不准？",
        "description": _strip_bare_hr_lines("\n".join(kpi_lines)),
        "color": 0x10B981 if (long_wr is not None and long_wr >= 50.0) else 0x38BDF8,
        "fields": [
            {
                "name": "📝 最近 5 条提醒战绩",
                "value": _strip_bare_hr_lines("\n".join(recent_lines))[:1024] if recent_lines else "暂无明细",
                "inline": False,
            }
        ],
        "timestamp": now_iso,
        "footer": {"text": footer_txt},
    }


def _embed_from_dict(embed_dict: Dict[str, Any]):
    if discord is None:
        return embed_dict
    return discord.Embed.from_dict(embed_dict)


if _DISCORD_PY_AVAILABLE:

    class RadarBotClient(discord.Client):
        """独立 Discord 双向斜杠交互客户端（物理隔离于主雷达守护进程）。"""

        def __init__(
            self,
            guild_id: str = "",
            snapshot_path: str = DEFAULT_SNAPSHOT_PATH,
            ledger_path: str = DEFAULT_LEDGER_PATH,
        ):
            intents = discord.Intents.default()
            super().__init__(intents=intents)
            self.guild_id = str(guild_id or "").strip()
            self.snapshot_path = snapshot_path
            self.ledger_path = ledger_path
            self.tree = app_commands.CommandTree(self)
            self.synced_mode = "NONE"
            self.synced_count = 0
            self._register_commands()

        def _register_commands(self) -> None:
            bot_ref = self

            @self.tree.command(name="check", description="用大白话秒查个股能不能买、关键支撑压力与真实体检")
            @app_commands.describe(ticker="输入美股代码或中文名（支持模糊补全，如 NVDA / soxl / 黄金）")
            @app_commands.checks.cooldown(1, BOT_COOLDOWN_SECONDS, key=lambda i: i.user.id)
            async def check_cmd(interaction: discord.Interaction, ticker: str):
                snap = load_radar_snapshot(bot_ref.snapshot_path)
                emb = _embed_from_dict(build_check_embed_dict(ticker, snap))
                await interaction.response.send_message(embed=emb)

            @check_cmd.autocomplete("ticker")
            async def check_autocomplete(interaction: discord.Interaction, current: str):
                snap = load_radar_snapshot(bot_ref.snapshot_path)
                choices = build_autocomplete_choices(current, snap, limit=25)
                return [app_commands.Choice(name=label, value=val) for label, val in choices]

            @self.tree.command(name="vix", description="今天大盘安全吗？看恐慌温度、抄底灯亮了没和场外资金风向")
            @app_commands.checks.cooldown(1, BOT_COOLDOWN_SECONDS, key=lambda i: i.user.id)
            async def vix_cmd(interaction: discord.Interaction):
                snap = load_radar_snapshot(bot_ref.snapshot_path)
                emb = _embed_from_dict(build_vix_embed_dict(snap))
                await interaction.response.send_message(embed=emb)

            @self.tree.command(name="top5", description="大盘震荡期最抗跌、资金还在买的 5 只股票")
            @app_commands.checks.cooldown(1, BOT_COOLDOWN_SECONDS, key=lambda i: i.user.id)
            async def top5_cmd(interaction: discord.Interaction):
                snap = load_radar_snapshot(bot_ref.snapshot_path)
                emb = _embed_from_dict(build_top5_embed_dict(snap))
                await interaction.response.send_message(embed=emb)

            @self.tree.command(name="ledger", description="最近 30 天群里发的提醒准不准？（做多与防接刀分开算账）")
            @app_commands.checks.cooldown(1, BOT_COOLDOWN_SECONDS, key=lambda i: i.user.id)
            async def ledger_cmd(interaction: discord.Interaction):
                snap = load_radar_snapshot(bot_ref.snapshot_path)
                emb = _embed_from_dict(build_ledger_embed_dict(ledger_path=bot_ref.ledger_path, snapshot=snap))
                await interaction.response.send_message(embed=emb)

            @self.tree.command(name="temp", description="看纳指/标普期货先导、大盘与日内泡沫评分和抄底指数（不含BTC）")
            @app_commands.checks.cooldown(1, BOT_COOLDOWN_SECONDS, key=lambda i: i.user.id)
            async def temp_cmd(interaction: discord.Interaction):
                snap = load_radar_snapshot(bot_ref.snapshot_path)
                emb = _embed_from_dict(build_temp_embed_dict(snap))
                await interaction.response.send_message(embed=emb)

            @self.tree.command(name="ai", description="管理员私有：AI 首席策略师大白话盘中剧本（大盘/QQQ/SPY·VOO/SOXX）")
            @app_commands.describe(
                target="选择解盘视角（大盘全景 / QQQ / SPY·VOO / SOXX）",
                engine="选择大模型引擎（默认自动：Gemini 优先 + DeepSeek 兜底）",
                refresh="True=实时调用大模型并更新缓存 / False=秒读最新缓存",
                private="True=仅自己可见 / False=发送到当前频道",
            )
            @app_commands.choices(
                target=[
                    app_commands.Choice(name="大盘全景", value="OVERVIEW"),
                    app_commands.Choice(name="QQQ", value="QQQ"),
                    app_commands.Choice(name="SPY / VOO", value="SPY"),
                    app_commands.Choice(name="SOXX", value="SOXX"),
                ],
                engine=[
                    app_commands.Choice(name="自动(Gemini优先)", value="auto"),
                    app_commands.Choice(name="强制Gemini", value="gemini"),
                    app_commands.Choice(name="强制DeepSeek", value="deepseek"),
                ],
            )
            @app_commands.checks.cooldown(1, AI_COOLDOWN_SECONDS, key=lambda i: i.user.id)
            async def ai_cmd(
                interaction: discord.Interaction,
                target: str = "OVERVIEW",
                engine: str = "auto",
                refresh: bool = True,
                private: bool = True,
            ):
                if ai_commentary_engine is None or not ai_commentary_engine.is_discord_owner(interaction.user.id):
                    await interaction.response.send_message(AI_OWNER_DENY_MSG, ephemeral=True)
                    return
                await interaction.response.defer(ephemeral=private)
                snap = load_radar_snapshot(bot_ref.snapshot_path)
                resp = await asyncio.to_thread(
                    handle_ai_command_request,
                    interaction.user.id,
                    target,
                    engine,
                    refresh,
                    snap,
                )
                if not resp.get("allowed"):
                    await interaction.followup.send(resp.get("message") or AI_OWNER_DENY_MSG, ephemeral=True)
                    return
                emb = _embed_from_dict(resp["embed"])
                await interaction.followup.send(embed=emb, ephemeral=private)

            @self.tree.error
            async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
                if isinstance(error, app_commands.CommandOnCooldown):
                    msg = f"⏳ 查询太快啦，请 **{error.retry_after:.1f} 秒** 后再试（单用户 3 秒防刷屏冷却）。"
                    try:
                        if interaction.response.is_done():
                            await interaction.followup.send(msg, ephemeral=True)
                        else:
                            await interaction.response.send_message(msg, ephemeral=True)
                    except Exception:
                        pass
                    return
                print(f"[!] ⚠️ Slash 命令执行异常: {error}")
                try:
                    err_msg = "❌ 查询处理出现临时异常，请稍后重试。"
                    if interaction.response.is_done():
                        await interaction.followup.send(err_msg, ephemeral=True)
                    else:
                        await interaction.response.send_message(err_msg, ephemeral=True)
                except Exception:
                    pass

        async def setup_hook(self) -> None:
            if self.guild_id.isdigit():
                try:
                    guild_obj = discord.Object(id=int(self.guild_id))
                    self.tree.copy_global_to(guild=guild_obj)
                    synced = await self.tree.sync(guild=guild_obj)
                    self.synced_mode = f"GUILD:{self.guild_id}"
                    self.synced_count = len(synced)
                    print(f"[+] ✅ 已向 Guild ({self.guild_id}) 瞬时同步 {len(synced)} 个斜杠命令: {[c.name for c in synced]}")
                    return
                except Exception as exc:
                    print(f"[!] ⚠️ Guild ({self.guild_id}) 同步失败，回退全局同步: {exc}")
            synced = await self.tree.sync()
            self.synced_mode = "GLOBAL"
            self.synced_count = len(synced)
            print(f"[+] ✅ 已全局同步 {len(synced)} 个斜杠命令: {[c.name for c in synced]}")

        async def on_ready(self) -> None:
            user_str = str(self.user) if self.user else "Unknown"
            user_id = getattr(self.user, "id", "--")
            print(
                f"[+] 🤖 Discord 交互机器人已上线: {user_str} (ID: {user_id}) | "
                f"命令同步: {self.synced_mode} ({self.synced_count} 个) | 只读快照: {self.snapshot_path}"
            )


def main(
    env_path: Optional[str] = None,
    env_override: Optional[Dict[str, str]] = None,
    snapshot_path: str = DEFAULT_SNAPSHOT_PATH,
    ledger_path: str = DEFAULT_LEDGER_PATH,
) -> int:
    """独立进程入口：未配置 DISCORD_BOT_TOKEN 时打印指引并优雅返回 0，绝不抛出未捕获异常。"""
    if env_override is None:
        load_env_values(env_path)
        token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        guild_id = os.environ.get("DISCORD_GUILD_ID", "").strip()
    else:
        token = str(env_override.get("DISCORD_BOT_TOKEN", "")).strip()
        guild_id = str(env_override.get("DISCORD_GUILD_ID", "")).strip()

    if not token:
        print("=" * 68)
        print("[*] ℹ️ 未检测到 DISCORD_BOT_TOKEN，Discord 双向交互机器人优雅退出 (exit 0)。")
        print("    请在项目根目录 .env 中添加：")
        print("      DISCORD_BOT_TOKEN=<你的 Discord Bot Token>")
        print("      DISCORD_GUILD_ID=<可选：服务器 Guild ID，配置后命令秒级生效>")
        print("    配置完成后运行 start_discord_bot.bat 或 python -u discord_radar_bot.py 即可独立上线。")
        print("=" * 68)
        return 0

    if not _DISCORD_PY_AVAILABLE:
        print("[!] ❌ 当前 Python 环境未安装 discord.py，请先执行: python -m pip install -U discord.py")
        return 1

    print(f"[*] 🚀 启动独立 Discord 双向交互机器人 (零行情 API 调用 · 只读快照={snapshot_path}) ...")
    client = RadarBotClient(guild_id=guild_id, snapshot_path=snapshot_path, ledger_path=ledger_path)
    try:
        client.run(token, log_handler=None)
    except discord.LoginFailure:
        print("[!] ❌ Discord Bot Token 鉴权失败，请检查 .env 中的 DISCORD_BOT_TOKEN 是否有效或是否已重置。")
        return 1
    except KeyboardInterrupt:
        print("[*] Discord 交互机器人已收到中断信号，优雅退出。")
        return 0
    except Exception as exc:
        print(f"[!] ❌ Discord 交互机器人运行异常退出: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
