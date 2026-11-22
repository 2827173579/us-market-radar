# -*- coding: utf-8 -*-
"""
独立外挂模块：Discord 双向交互机器人 (discord_radar_bot.py)

设计红线：
1. 物理隔离与零 API 压力：严禁调用 Yahoo / Finnhub / Jin10 等外部行情接口，只从本地只读快照
   `data_cache/latest_radar_snapshot.json` 与只读账本 `data_cache/signal_ledger.json` 毫秒级响应。
2. 零硬编码凭证：严格从 `.env` 或环境变量读取 `DISCORD_BOT_TOKEN` 与可选的 `DISCORD_GUILD_ID`。
3. 纯函数可测：所有检索、模糊匹配与 Embed 卡片构建均抽离为无网关依赖的纯函数。
"""

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


def build_check_embed_dict(
    query: str,
    snapshot: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """构建 `/check <ticker>` 动态颜色单标的全景诊断 Embed 字典。"""
    footer_txt = _build_footer_text(snapshot)
    now_iso = datetime.now(timezone.utc).isoformat()

    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("tickers"), dict) or not snapshot["tickers"]:
        return {
            "title": "⏳ 量化雷达快照尚未就绪",
            "description": _strip_bare_hr_lines(
                "本地只读快照 `data_cache/latest_radar_snapshot.json` 暂未生成或为空。\n"
                "请先确认主雷达守护进程已完成本轮刷新后再试。"
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
            f"• **`{c['ticker']}`** · {c['name']}" + (f" ({c['cat']})" if c.get("cat") else "")
            for c in cands
        ]
        sug_block = "\n".join(sug_lines) if sug_lines else "• 热门示例：`NVDA` · `SOXL` · `QQQ` · `SPY` · `XAUUSD` · `BTC-USD`"
        return {
            "title": f"🔍 未找到 `{str(query or '').strip()[:40] or '--'}`",
            "description": _strip_bare_hr_lines(
                f"当前仅支持雷达监控池内的 **{len(tickers_map)} 只标的（14 ETF + 65 个股）**，并支持大小写与中文关键词自动补全。\n\n"
                f"**您是不是要找：**\n{sug_block}"
            ),
            "color": 0xF59E0B,
            "timestamp": now_iso,
            "footer": {"text": footer_txt},
        }

    item = tickers_map[tk]
    stance = str(item.get("decision_stance") or "NEUTRAL").upper()
    is_bear = bool(item.get("is_bear_defense_card")) or (stance == "BEAR")

    if is_bear:
        color = 0xEF4444
        stance_icon = "🔴"
    elif stance == "BULL":
        color = 0x10B981
        stance_icon = "🟢"
    else:
        color = 0xF59E0B
        stance_icon = "🟡"

    name = str(item.get("name") or tk)
    cat = str(item.get("cat") or "未分类")
    sub = str(item.get("sub") or "")
    price = _opt_float(item.get("price") or item.get("last"))
    last = _opt_float(item.get("last"))
    eff_pct = _opt_float(item.get("effective_pct") if item.get("effective_pct") is not None else item.get("pct_change"))
    pct_rth = _opt_float(item.get("pct_change"))
    ext_px = _opt_float(item.get("ext_price"))
    ext_pct = _opt_float(item.get("ext_pct"))
    sess_tag = str(item.get("session_tag") or snapshot.get("session_name") or "盘中")

    price_lines = [
        f"现价：**{_fmt_price(price)}** (`{_fmt_pct(eff_pct)}` · {sess_tag})",
    ]
    if ext_px and ext_px > 0 and last and last > 0:
        price_lines.append(f"正规盘收：{_fmt_price(last)} (`{_fmt_pct(pct_rth)}`)")
        price_lines.append(f"扩展时段：{_fmt_price(ext_px)} (`{_fmt_pct(ext_pct)}`)")

    l_cal = _fmt_num(item.get("ai_long_win_rate_pct"), 1, "%")
    l_raw = _fmt_num(item.get("ai_long_raw_pct"), 1, "%")
    s_cal = _fmt_num(item.get("ai_short_win_rate_pct"), 1, "%")
    s_raw = _fmt_num(item.get("ai_short_raw_pct"), 1, "%")
    ai_lines = [
        f"赛道：**{cat}**" + (f" · {sub[:28]}" if sub else ""),
        f"做多校准胜率：**{l_cal}** (原始 {l_raw})",
        f"做空校准胜率：**{s_cal}** (原始 {s_raw})",
    ]

    verdict = str(item.get("decision_verdict") or "⚪ 震荡整理 · 等待明确方向")
    verdict_sub = str(item.get("decision_sub") or "")
    ai_act = str(item.get("ai_action") or "").strip()
    decision_lines = [f"**{verdict}**"]
    if verdict_sub:
        decision_lines.append(verdict_sub)
    if ai_act and ai_act not in verdict_sub:
        decision_lines.append(f"AI 战术提示：{ai_act}")

    if is_bear:
        r1 = item.get("r1")
        s1 = item.get("s1")
        levels_title = "🧭 关键压力 / 支撑（防守视角）"
        levels_lines = [
            f"上方压力 R1：**{_fmt_price(r1)}**{_rel_pct_str(r1, price)}",
            f"下方支撑 S1：**{_fmt_price(s1)}**{_rel_pct_str(s1, price)}",
            "风控纪律：**严禁左侧接刀**，等重新站稳 VWAP 再看反抽",
        ]
    else:
        opt_lim = item.get("optimal_limit")
        sl_px = item.get("entry_stop_loss")
        tp_px = item.get("target_profit_price")
        fib_lo = item.get("fib_low")
        fib_hi = item.get("fib_high")
        levels_title = "🎯 参考挂单与防守点位"
        levels_lines = [
            f"参考挂单：**{_fmt_price(opt_lim)}**{_rel_pct_str(opt_lim, price)}" if (_opt_float(opt_lim) or 0) > 0 else "参考挂单：**暂缓 / 观察**",
            f"防守止损：**{_fmt_price(sl_px)}**{_rel_pct_str(sl_px, price)}" if (_opt_float(sl_px) or 0) > 0 else "防守止损：**--**",
            f"目标参考：**{_fmt_price(tp_px)}**{_rel_pct_str(tp_px, price)}" if (_opt_float(tp_px) or 0) > 0 else f"60日坑带：{_fmt_price(fib_lo)} ~ {_fmt_price(fib_hi)}",
        ]

    vwap_val = _opt_float(item.get("vwap"))
    vwap_diff = _opt_float(item.get("vwap_diff_pct"))
    vwap_stale = bool(item.get("vwap_stale"))
    vwap_lbl = str(item.get("vwap_label") or ("上日参考 · 09:30重置" if vwap_stale else "当日日内"))
    vwap_tag_str = f" _({vwap_lbl})_" if vwap_stale else f" ({vwap_lbl})"
    rsi5_str = _fmt_num(item.get("rsi_5m"), 1) if item.get("rsi_5m") is not None else "待开盘 / --"
    rvol5_str = f" ({_fmt_num(item.get('rvol_5m'), 2, 'x')})" if item.get("rvol_5m") is not None else ""
    micro_lines = [
        f"日内 VWAP：**{_fmt_price(vwap_val)}** (`{_fmt_pct(vwap_diff)}`){vwap_tag_str}",
        f"5m RSI：**{rsi5_str}**{rvol5_str} ｜ 14日 RSI：{_fmt_num(item.get('rsi_14'), 1)}",
        f"20日超额 RS：**{_fmt_pct(item.get('rs_20d'), 1)}** ｜ MA50 偏离：{_fmt_pct(item.get('bias_ma50'), 1)}",
    ]

    cor = item.get("corridor") if isinstance(item.get("corridor"), dict) else {}
    cor_cn = str(cor.get("status_cn") or "暂无走廊")
    cor_time = f" ({cor.get('latest_time')} ET)" if cor.get("latest_time") else ""
    cor_dev = f" ｜ 偏离 P50 `{_fmt_pct(cor.get('pct_vs_p50'))}`" if cor.get("pct_vs_p50") is not None else ""
    if cor.get("p50") is not None:
        cor_range = f"P25 **{_fmt_price(cor.get('p25'))}** ｜ P50 **{_fmt_price(cor.get('p50'))}** ｜ P75 **{_fmt_price(cor.get('p75'))}**"
    else:
        cor_range = "盘前开盘 corridor 尚未就绪或连续/次新品种跳过"
    pm_line = ""
    if cor.get("pmh") or cor.get("pml"):
        pm_line = f"\n盘前/参考高低：PMH {_fmt_price(cor.get('pmh'))} ｜ PML {_fmt_price(cor.get('pml'))}"

    fields = [
        {"name": "💰 报价与时段", "value": _strip_bare_hr_lines("\n".join(price_lines))[:1024], "inline": True},
        {"name": "🤖 赛道与 AI 胜率", "value": _strip_bare_hr_lines("\n".join(ai_lines))[:1024], "inline": True},
        {"name": "🧭 当前决策结论", "value": _strip_bare_hr_lines("\n".join(decision_lines))[:1024], "inline": False},
        {"name": levels_title, "value": _strip_bare_hr_lines("\n".join(levels_lines))[:1024], "inline": True},
        {"name": "📐 微观量价与强弱", "value": _strip_bare_hr_lines("\n".join(micro_lines))[:1024], "inline": True},
        {
            "name": f"📈 今日盘中走廊 (P25~P75){cor_time}",
            "value": _strip_bare_hr_lines(f"状态：**{cor_cn}**{cor_dev}\n{cor_range}{pm_line}")[:1024],
            "inline": False,
        },
    ]

    return {
        "title": f"{stance_icon} {tk} · {name} 全景诊断"[:256],
        "description": _strip_bare_hr_lines(
            f"大盘体制：**{snapshot.get('market_regime_cn') or snapshot.get('market_regime') or '--'}** ｜ 宏观气象：{snapshot.get('weather') or '--'}"
        ),
        "color": color,
        "fields": fields,
        "timestamp": now_iso,
        "footer": {"text": footer_txt},
    }


def build_vix_embed_dict(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """构建 `/vix` 真 VIX/VXN 恐慌中枢 + 跨市场流动性 + OPEX 护盾 Embed 字典。"""
    footer_txt = _build_footer_text(snapshot)
    now_iso = datetime.now(timezone.utc).isoformat()

    if not isinstance(snapshot, dict):
        return {
            "title": "⏳ VIX 恐慌中枢快照尚未就绪",
            "description": "请等待主雷达首轮刷新完成生成 `data_cache/latest_radar_snapshot.json` 后再查。",
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

    spread_v = _opt_float(vr.get("vxn_vix_spread"))
    spread_str = f"{spread_v:+.2f}" if spread_v is not None else "--"

    vix_lines = [
        f"• **`^VIX` 标普恐慌**：`{_fmt_num(vr.get('vix_spot'))}` ({_fmt_pct(vr.get('vix_pct'))}) ｜ **`^VIX3M`**：`{_fmt_num(vr.get('vix3m_spot'))}`",
        f"• **期限比 `VIX/VIX3M`**：**`{_fmt_num(vr.get('vix_term_ratio'), 3)}`** ({vr.get('term_structure_cn') or '--'})",
        f"• **`^VXN` 纳指恐慌**：`{_fmt_num(vr.get('vxn_spot'))}` ｜ 科技溢价 `(VXN - VIX)`：**`{spread_str}`**",
        f"• **科技压制评估**：{vr.get('tech_fear_label') or '⚪ 科技波动平稳'}",
        f"• **`^VVIX`**：`{_fmt_num(vr.get('vvix_spot'))}` ｜ **10Y 美债 `^TNX`**：`{_fmt_num(vr.get('tnx_yield'), 2, '%')}`",
        f"• **10日峰值 / 回撤**：`{_fmt_num(vr.get('vix_10d_peak'))}` / `-{_fmt_num(vr.get('vix_drawdown_from_peak_pct'), 1, '%')}`",
    ]

    gate_lines = [
        f"• **策略 A（波段抄底窗）**：{vr.get('strategy_a_status') or '--'}",
        f"• **策略 B（盘中真退潮）**：{vr.get('strategy_b_status') or '--'}",
        f"• **门禁指引**：{vr.get('advice_cn') or vr.get('summary_cn') or '--'}",
    ]

    liq_lines = [
        f"• **综合判定**：{liq.get('badge_cn') or '⚪ 暂缺'}",
        f"• **`HYG / IEF` 信用利差**：`{_fmt_num(liq.get('hyg_ief_ratio'), 4)}` (5日 `{_fmt_pct(liq.get('hyg_ief_5d_pct'))}`)",
        f"• **美元指数 `DXY`**：`{_fmt_num(liq.get('dxy_spot'))}` (5日 `{_fmt_pct(liq.get('dxy_5d_pct'))}`)",
        f"• **美元/日元 `USD/JPY`**：`{_fmt_num(liq.get('usdjpy_spot'))}` (5日 `{_fmt_pct(liq.get('usdjpy_5d_pct'))}`)",
    ]

    if opx.get("active"):
        opx_lines = [
            f"• **激活护盾**：**{opx.get('badge') or 'OPEX 护盾'}**",
            f"• {opx.get('headline') or ''}",
            f"• {opx.get('detail') or ''}",
        ]
    else:
        opx_lines = [
            "• **状态**：⚪ 非 OPEX / T-1 常规交易日",
            f"• **下一次 OPEX / 四巫日**：`{opx.get('next_opex_date') or '--'}`（距今 `{opx.get('days_to_opex', '--')}` 天）",
        ]

    return {
        "title": "⚡ 美股量化雷达 · 真 VIX/VXN 恐慌中枢与宏观护盾",
        "description": _strip_bare_hr_lines(
            f"**{vr.get('pill_text') or '--'}**\n"
            f"大盘体制：**{snapshot.get('market_regime_cn') or snapshot.get('market_regime') or '--'}** ｜ "
            f"气象：**{snapshot.get('weather') or '--'}**"
        ),
        "color": color,
        "fields": [
            {"name": "🌡️ VIX / VXN 期限矩阵", "value": _strip_bare_hr_lines("\n".join(vix_lines))[:1024], "inline": False},
            {"name": "🚦 策略 A / B 双保险门禁", "value": _strip_bare_hr_lines("\n".join(gate_lines))[:1024], "inline": False},
            {"name": "🌊 跨市场流动性", "value": _strip_bare_hr_lines("\n".join(liq_lines))[:1024], "inline": True},
            {"name": "🗓️ OPEX / 四巫日护盾", "value": _strip_bare_hr_lines("\n".join(opx_lines))[:1024], "inline": True},
        ],
        "timestamp": now_iso,
        "footer": {"text": footer_txt},
    }


def build_top5_embed_dict(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """构建 `/top5` 🛡️ 抗跌领涨 Top 5 真龙头榜 Embed 字典。"""
    footer_txt = _build_footer_text(snapshot)
    now_iso = datetime.now(timezone.utc).isoformat()

    if not isinstance(snapshot, dict):
        return {
            "title": "🛡️ 抗跌领涨 Top 5 真龙头榜",
            "description": "本地只读快照尚未就绪，请等待主雷达完成首轮刷新后再试。",
            "color": 0xF59E0B,
            "timestamp": now_iso,
            "footer": {"text": footer_txt},
        }

    top5 = snapshot.get("resilient_leaders_top5")
    if not isinstance(top5, list) or not top5:
        return {
            "title": "🛡️ 抗跌领涨 Top 5 真龙头榜",
            "description": _strip_bare_hr_lines(
                "当前暂无满足筛选条件的抗跌领涨榜单。\n"
                "（筛选器已自动剔除 `SQQQ / SOXS / VIXY / TLT / GLD / XAUUSD / BTC-USD` 等反向与防御避险工具，仅保留非暴跌冷却的正向抗跌领涨标的。）"
            ),
            "color": 0x64748B,
            "timestamp": now_iso,
            "footer": {"text": footer_txt},
        }

    vr = snapshot.get("vix_regime") if isinstance(snapshot.get("vix_regime"), dict) else {}
    rows: List[str] = []
    for idx, ld in enumerate(top5[:5], 1):
        tk = str(ld.get("ticker") or "--")
        name = str(ld.get("name") or tk)
        cat = str(ld.get("cat") or "")
        px_s = _fmt_price(ld.get("price"))
        pct_s = _fmt_pct(ld.get("pct"))
        rs_s = _fmt_pct(ld.get("rs_20d"), 1)
        exc_s = _fmt_pct(ld.get("excess_vs_spy"))
        vwap_tag = str(ld.get("vwap_tag") or ("✓VWAP" if ld.get("above_vwap") else "VWAP下"))
        cat_part = f" · {cat}" if cat else ""
        rows.append(
            f"**{idx}. `{tk}`** ({name}{cat_part})\n"
            f"> 现价 `{px_s}` (`{pct_s}` ｜ 相对SPY `{exc_s}`) ｜ 20日RS **`{rs_s}`** ｜ `{vwap_tag}`"
        )

    return {
        "title": "🛡️ 抗跌领涨 Top 5 真龙头榜",
        "description": _strip_bare_hr_lines(
            f"当前恐慌状态：{vr.get('pill_text') or '--'} ｜ 自动剔除反向 ETF/国债/黄金/加密与暴跌熔断股，按 20日超额 RS 与日内 VWAP 承接排序。\n\n"
            + "\n".join(rows)
        ),
        "color": 0x10B981,
        "timestamp": now_iso,
        "footer": {"text": footer_txt},
    }


def build_ledger_embed_dict(
    ledger_path: Optional[str] = None,
    snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建 `/ledger` 📒 近 30 日实盘推送信号记账摘要 Embed 字典（严格只读、多空分账）。"""
    footer_txt = _build_footer_text(snapshot)
    now_iso = datetime.now(timezone.utc).isoformat()
    target_ledger = ledger_path or DEFAULT_LEDGER_PATH

    if signal_ledger is None:
        return {
            "title": "📒 实盘推送信号记账本（近 30 日）",
            "description": "记账本模块 `signal_ledger.py` 当前不可用，无法读取战绩数据。",
            "color": 0x64748B,
            "timestamp": now_iso,
            "footer": {"text": footer_txt},
        }

    summary = signal_ledger.get_recent_summary(days=30, max_recent=5, ledger_path=target_ledger)
    total_rec = int(summary.get("total_records") or 0)

    if total_rec == 0:
        return {
            "title": "📒 实盘推送信号记账本（近 30 日）",
            "description": _strip_bare_hr_lines(
                "**📒 账本已就绪，自下周首条推送起自动记录**\n\n"
                "当主雷达向 Discord 真实推送实盘信号后，系统会静默记账并在 `+1h` / 收盘 `16:05 ET` / `T+3` / `T+5` 自动零未来函数回填战绩，"
                "**多头实盘胜率**与**防守拦截有效率**严格分账统计。"
            ),
            "color": 0x38BDF8,
            "timestamp": now_iso,
            "footer": {"text": footer_txt},
        }

    long_wr = summary.get("long_win_rate_pct")
    long_wr_s = f"**{long_wr:.1f}%** (`{summary.get('long_wins', 0)}/{summary.get('long_settled', 0)}`)" if long_wr is not None else "待结算"
    def_eff = summary.get("defense_eff_rate_pct")
    def_eff_s = f"**{def_eff:.1f}%** (`{summary.get('defense_hits', 0)}/{summary.get('defense_settled', 0)}`)" if def_eff is not None else "暂无/待结算"

    kpi_lines = [
        f"• **近 30 日记账总数**：`{total_rec}` 笔（已结算 `{summary.get('settled_total', 0)}` 笔）",
        f"• 📈 **多头实盘胜率 (`{summary.get('long_total', 0)}` 笔)**：{long_wr_s}",
        f"• 🛡️ **防守拦截有效率 (`{summary.get('defense_total', 0)}` 笔)**：{def_eff_s}",
    ]

    recent_lines: List[str] = []
    for rec in (summary.get("recent_records") or [])[:5]:
        dir_tag = "🛡️防守" if rec.get("direction") == "BEAR_DEFENSE" else "📈做多"
        hit_v = rec.get("hit")
        res_icon = "✅" if hit_v is True else ("❌" if hit_v is False else "⏳")
        eod_s = _fmt_pct(rec.get("ret_eod_pct"))
        t3_s = _fmt_pct(rec.get("ret_t3_pct"))
        mae_s = _fmt_pct(rec.get("mae_pct"))
        recent_lines.append(
            f"> {res_icon} `{rec.get('date_et')}` · **{rec.get('ticker')}** ({dir_tag} · {rec.get('signal_label_cn')})\n"
            f"> 触发 `{_fmt_price(rec.get('entry_price'))}` ｜ 当日 `{eod_s}` / T+3 `{t3_s}` / MAE `{mae_s}`"
        )

    return {
        "title": "📒 实盘推送信号记账本（近 30 日）",
        "description": _strip_bare_hr_lines("\n".join(kpi_lines)),
        "color": 0x10B981 if (long_wr is not None and long_wr >= 50.0) else 0x38BDF8,
        "fields": [
            {
                "name": "📝 最近 5 条推送追踪明细",
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

            @self.tree.command(name="check", description="秒查 79 只监控池标的现价、VWAP、AI胜率、关键点位与盘中走廊")
            @app_commands.describe(ticker="输入美股代码或中文关键词（支持模糊补全，如 NVDA / soxl / 黄金 / xau）")
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

            @self.tree.command(name="vix", description="一键查看真 VIX/VXN 恐慌中枢、策略 A/B 门禁、跨市场流动性与 OPEX 护盾")
            @app_commands.checks.cooldown(1, BOT_COOLDOWN_SECONDS, key=lambda i: i.user.id)
            async def vix_cmd(interaction: discord.Interaction):
                snap = load_radar_snapshot(bot_ref.snapshot_path)
                emb = _embed_from_dict(build_vix_embed_dict(snap))
                await interaction.response.send_message(embed=emb)

            @self.tree.command(name="top5", description="一键查看当前 🛡️ 抗跌领涨 Top 5 真龙头榜")
            @app_commands.checks.cooldown(1, BOT_COOLDOWN_SECONDS, key=lambda i: i.user.id)
            async def top5_cmd(interaction: discord.Interaction):
                snap = load_radar_snapshot(bot_ref.snapshot_path)
                emb = _embed_from_dict(build_top5_embed_dict(snap))
                await interaction.response.send_message(embed=emb)

            @self.tree.command(name="ledger", description="查看近 30 日 Discord 实盘推送信号记账摘要（多空严格分账）")
            @app_commands.checks.cooldown(1, BOT_COOLDOWN_SECONDS, key=lambda i: i.user.id)
            async def ledger_cmd(interaction: discord.Interaction):
                snap = load_radar_snapshot(bot_ref.snapshot_path)
                emb = _embed_from_dict(build_ledger_embed_dict(ledger_path=bot_ref.ledger_path, snapshot=snap))
                await interaction.response.send_message(embed=emb)

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
