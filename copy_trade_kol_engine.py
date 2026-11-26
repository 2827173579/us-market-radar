# -*- coding: utf-8 -*-
"""
V5.7 精简版独立模块：$1,000 精简 KOL 狙击轨模拟跟单引擎 (copy_trade_kol_engine.py)

经 51/52 日防穿越全维度消融与「做减法」精简对照验证 (`Slim_2S_BE26_ETF52`:
25 bps 净收益 +26.27% / PF 1.53 / MaxDD -7.86% / 均笔 +$3.28 / 9-10月 +$145.65 / 11月 +$117.07 / 2~3%倒亏降至 6 笔；
3 bps 净收益 +35.77% / PF 1.76) 验货达标后正式上线，与 V5.4 稳健基准轨 (data_cache/copy_trade_ledger.json)
100% 物理隔离，独立持久化至 data_cache/copy_trade_ledger_kol.json。

V5.7 精简版核心规则（严格零夹带未经回测验证参数）：
1. 火力底座（沿用 V5.5.1 高质选股与 2 槽×50% 集中度）：
   - 初始 $1,000.00，正盘主槽位 2 槽 × 50%（30% 底仓 + 20% 浮盈顺势加仓，严禁浮亏摊平）；
   - 个股沿用 AI 校准胜率 >= 55.0% 且净差 >= 10.0%、`prob + spread*0.25 + pri` 排序与 `09:45 ~ close-40min`（半日市动态锚定）正盘窗；
   - 1x 结构硬止损区间收紧为 [2.0%, 3.5%]，3x 杠杆 ETF 为 [2.5%, 4.5%]；
   - 彻底关闭负效噪音项：不开 09:35~09:44 开盘半仓、不开盘前开仓、不做 AI 软权重重排、不开动态换仓、不做温度计硬拦截与 SOXS 尾盘盲目减半。
2. 7 只核心 ETF 独立门槛 + 禁空反向/波动率 ETF：
   - SOXX / SOXL / SOXS / QQQ / TQQQ / SQQQ / KSTR 放宽至 >= 52.0% 且净差 >= 6.0% (`MAIN_AI_ETF`)；
   - 严禁做空 SOXS / SQQQ / VIXY（看空仅顺势做多反向 ETF 或做空正股/SOXX/QQQ，贴合实盘零借券费与防逼空）。
3. Neo Lee 快推保本 `BE26` + 完整 50% 尾仓追踪：
   - 先执行金字塔顺势加仓 (+1.8% 1x / +2.8% 3x + 移损至原底仓外侧 1.0%)；
   - 达到 +2.6% (1x) / +4.0% (3x) 先平 25% 落袋现金并将余仓止损推至保本微利 (+0.3%)；
   - 达到 +4.0% (1x) / +6.5% (3x) 再平余仓 1/3（即保留完整 50% 尾仓）进入 TRAIL_TP / CLIMAX_TP 让大肉奔跑。
4. 夜盘 `OVERNIGHT_APAC_PILOT` 10% 轻仓特批通道（独立于主槽位）：
   - 仅限 OVERNIGHT 时段 + 亚盘半导体风向 (`apac_chip_sentiment`) 明确多/空共振 + 白名单 + 点差 <= 35 bps + 连续 2 轮(>=60s)确认；
   - 单笔上限 10% 轻仓、最多 1 笔，独立打标审计且不挤占正盘 2 个主槽位。
5. 延展时段防插针保护止损（连续 2 轮 >= 60s 确认 + 宽点差 > 60 bps 护栏），严禁输出“建议股数”，严禁干跑写盘/外发。
"""

import html as _html
import json
import os
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import copy_trade_engine as cte

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_KOL_LEDGER_PATH = os.path.join(BASE_DIR, "data_cache", "copy_trade_ledger_kol.json")

INITIAL_CAPITAL = 1000.00
MAX_POSITIONS = 2
STARTER_WEIGHT = 0.30
ADDON_WEIGHT = 0.20
FULL_WEIGHT = 0.50

ONE_WAY_SLIPPAGE = cte.ONE_WAY_SLIPPAGE          # 1.5 bps
ROUND_TRIP_SLIPPAGE = cte.ROUND_TRIP_SLIPPAGE    # 3.0 bps
HOLD_TRADING_DAYS = 3                            # T+3 最大持仓周期

# V5.7 精简版入场门禁（个股对齐 V5.4/V5.5.1；7 只核心 ETF 独立放宽至 52%/6%）
MIN_PROB_THRESHOLD = 55.0
MIN_SPREAD_THRESHOLD = 10.0
ETF_MIN_PROB_THRESHOLD = 52.0
ETF_MIN_SPREAD_THRESHOLD = 6.0
V57_CORE_ETF_TIER = frozenset({"SOXX", "SOXL", "SOXS", "QQQ", "TQQQ", "SQQQ", "KSTR"})
# 严禁做空反向/波动率 ETF（仅顺势做多，保证模拟账本贴合实盘零借券费与防逼空）
V57_NO_SHORT_INVERSE_ETFS = frozenset({"SOXS", "SQQQ", "VIXY"})

# 入场窗口与冷却
ENTRY_START_MIN = 9 * 60 + 45   # 09:45 ET
ENTRY_END_MIN = 15 * 60 + 20    # 15:20 ET（半日市自动锚定 close_min - 40）
T3_FORCE_EXIT_MIN = 15 * 60 + 50
SLOT_COOLDOWN_MINUTES = 30
MAX_NEW_ENTRIES_PER_DAY = 3     # 2 正常槽位 + 平仓冷却后允许补 1 笔

# V5.7 精简版结构硬止损收紧区间（1x 回归 [2.0%, 3.5%] 对齐 Slim_2S_BE26_ETF52）
SL_MIN_1X = 0.020
SL_MAX_1X = 0.035
SL_MIN_3X = 0.025
SL_MAX_3X = 0.045

# 顺势加仓触发线与外侧 1.0% 呼吸缓冲
ADDON_TRIGGER_1X_PCT = 1.8
ADDON_TRIGGER_3X_PCT = 2.8
ADDON_SL_BUFFER = 0.01

# V5.7 Neo Lee 快推保本 BE26 + 一阶分批止盈 + 完整 50% 尾仓追踪 & 卖强
FAST_BE_1X_PCT = 2.6
FAST_BE_3X_PCT = 4.0
FAST_BE_TRIM_FRAC = 0.25        # 达 +2.6%/+4.0% 先平 25% 落袋现金 + 推保本损 (+0.3%)
PARTIAL_TP_1X_PCT = 4.0
PARTIAL_TP_3X_PCT = 6.5
PARTIAL_AFTER_BE_TRIM_FRAC = 1.0 / 3.0  # 若已 BE26 平过 25%，一阶仅再平余仓 1/3 (=原仓 25%，保留完整 50% 尾仓)
BE_STOP_BUFFER = 0.003          # 成本价 +0.3% 保本损
TRAIL_DRAWDOWN_1X_PCT = 2.2
TRAIL_DRAWDOWN_3X_PCT = 4.0
CLIMAX_PROFIT_1X_PCT = 6.0
CLIMAX_PROFIT_3X_PCT = 9.0
CLIMAX_RSI_HIGH = 78.0
CLIMAX_RSI_LOW = 22.0
CLIMAX_RVOL_MIN = 2.5

# 补丁 D-v2: 「该强不强」早撤推迟至 T+2 (尾盘仓顺延至 13:30 ET)
WEAK_EXIT_MIN_TRADING_DAYS = 2
LATE_ENTRY_CUTOFF_MIN = 14 * 60       # 14:00 ET
EARLY_CHECK_NORMAL_MIN = 11 * 60 + 30 # T+2 11:30 ET
EARLY_CHECK_LATE_MIN = 13 * 60 + 30   # 尾盘仓顺延至 T+2 13:30 ET

# V5.7 延展时段防插针连续确认参数 + 宽点差防假扫损护栏
EXTENDED_SL_CONFIRM_POLLS = 2
EXTENDED_SL_CONFIRM_SECONDS = 60.0
EXTENDED_MAX_SPREAD_BPS = 60.0
REGULAR_SESSIONS = frozenset({"REGULAR", "RTH", "INTRADAY"})
EXTENDED_SESSIONS = frozenset({"PRE_MARKET", "PREMARKET", "AFTER_HOURS", "AFTERHOURS", "POST_MARKET", "OVERNIGHT", "EXTENDED"})
CLOSED_SESSIONS = frozenset({"CLOSED", "WEEKEND", "HOLIDAY"})

# V5.7 夜盘 OVERNIGHT_APAC_PILOT 10% 轻仓特批通道（独立于正盘 2 主槽位，独立打标审计）
OVERNIGHT_PILOT_WEIGHT = 0.10
OVERNIGHT_PILOT_MAX_POSITIONS = 1
OVERNIGHT_PILOT_MAX_SPREAD_BPS = 35.0
OVERNIGHT_PILOT_CONFIRM_POLLS = 2
OVERNIGHT_PILOT_CONFIRM_SECONDS = 60.0
OVERNIGHT_PILOT_WHITELIST = frozenset({
    "SOXX", "SOXL", "SOXS", "QQQ", "TQQQ", "SQQQ", "KSTR",
    "NVDA", "TSM", "AMD", "AVGO", "MU",
})

LEVERAGED_3X_ETFS = frozenset({"SOXL", "SOXS", "TQQQ", "SQQQ", "KORU"})
KOL_CORE_WHITELIST = frozenset({
    "SOXL", "SOXS", "TQQQ", "SQQQ", "KORU", "KSTR",
    "NVDA", "MU", "SNDK", "AVGO", "TSM", "AMAT", "MRVL", "AMD", "TSLA",
    "MSTR", "CRCL", "NBIS", "PLTR", "COIN", "META", "GOOGL", "MSFT", "AMZN",
    "QQQ", "SPY", "SOXX", "SMH", "GLD", "TLT", "VIXY",
})


def is_3x_leveraged(ticker: str) -> bool:
    return str(ticker or "").strip().upper() in LEVERAGED_3X_ETFS


def _count_trading_days_between(start_d: date, end_d: date) -> int:
    if end_d <= start_d:
        return 0
    return cte.count_remaining_trading_days(start_d, end_d)


def _parse_et_timestamp(ts_str: str, fallback_et: datetime) -> Optional[datetime]:
    if not ts_str:
        return None
    s = str(ts_str).strip()[:19]
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=fallback_et.tzinfo)
    except Exception:
        return None


def _compute_kol_stats(closed_trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    tp_cnt = 0
    trail_cnt = 0
    climax_cnt = 0
    be_cnt = 0
    weak_cnt = 0
    sl_cnt = 0
    t3_win_cnt = 0
    t3_loss_cnt = 0
    long_total = 0
    long_wins = 0
    short_total = 0
    short_wins = 0
    gross_profit = 0.0
    gross_loss = 0.0
    addon_trades = 0
    flip_trades = 0
    overnight_pilot_cnt = 0
    overnight_pilot_wins = 0
    overnight_pilot_pnl = 0.0
    fast_be_triggered_cnt = 0

    for tr in closed_trades or []:
        if not isinstance(tr, dict):
            continue
        outcome = str(tr.get("outcome") or "").upper()
        direction = str(tr.get("direction") or "LONG").upper()
        entry_tag_u = str(tr.get("entry_tag") or "").upper()
        pnl = cte._safe_float(tr.get("pnl_usd"), 0.0)
        if int(tr.get("stage") or 1) >= 2:
            addon_trades += 1
        if entry_tag_u == "FLIP":
            flip_trades += 1
        if bool(tr.get("fast_be_trimmed", False)):
            fast_be_triggered_cnt += 1
        if entry_tag_u == "OVERNIGHT_APAC_PILOT":
            overnight_pilot_cnt += 1
            overnight_pilot_pnl += pnl
            if pnl > 0:
                overnight_pilot_wins += 1

        if outcome == "TRAIL_TP":
            trail_cnt += 1
            tp_cnt += 1
            is_win = pnl > 0
        elif outcome == "CLIMAX_TP":
            climax_cnt += 1
            tp_cnt += 1
            is_win = pnl > 0
        elif outcome == "BE_STOP":
            be_cnt += 1
            is_win = pnl >= 0
        elif outcome == "WEAK_EARLY_EXIT":
            weak_cnt += 1
            is_win = pnl > 0
        elif outcome == "SL_HIT":
            sl_cnt += 1
            is_win = False
        elif outcome == "T3_WIN" or (outcome == "T3_EXPIRE" and pnl > 0):
            t3_win_cnt += 1
            is_win = True
        else:
            t3_loss_cnt += 1
            is_win = pnl > 0

        if direction == "SHORT":
            short_total += 1
            if is_win:
                short_wins += 1
        else:
            long_total += 1
            if is_win:
                long_wins += 1

        if pnl > 0:
            gross_profit += pnl
        elif pnl < 0:
            gross_loss += abs(pnl)

    closed_cnt = len([t for t in (closed_trades or []) if isinstance(t, dict)])
    win_cnt = long_wins + short_wins
    loss_cnt = max(0, closed_cnt - win_cnt)
    overall_wr = round((win_cnt / closed_cnt) * 100.0, 1) if closed_cnt > 0 else None
    long_wr = round((long_wins / long_total) * 100.0, 1) if long_total > 0 else None
    short_wr = round((short_wins / short_total) * 100.0, 1) if short_total > 0 else None
    if gross_loss > 1e-6:
        pf: Optional[float] = round(gross_profit / gross_loss, 2)
    elif gross_profit > 1e-6:
        pf = 99.9
    else:
        pf = None

    return {
        "closed_count": closed_cnt,
        "win_count": win_cnt,
        "loss_count": loss_cnt,
        "tp_hit_count": tp_cnt,
        "trail_tp_count": trail_cnt,
        "climax_tp_count": climax_cnt,
        "be_stop_count": be_cnt,
        "weak_early_exit_count": weak_cnt,
        "t3_win_count": t3_win_cnt,
        "t3_loss_count": t3_loss_cnt,
        "sl_hit_count": sl_cnt,
        "addon_trade_count": addon_trades,
        "flip_trade_count": flip_trades,
        "fast_be_triggered_count": fast_be_triggered_cnt,
        "overnight_pilot_count": overnight_pilot_cnt,
        "overnight_pilot_wins": overnight_pilot_wins,
        "overnight_pilot_pnl_usd": round(overnight_pilot_pnl, 2),
        "overall_win_rate_pct": overall_wr,
        "long_total": long_total,
        "long_wins": long_wins,
        "long_win_rate_pct": long_wr,
        "short_total": short_total,
        "short_wins": short_wins,
        "short_win_rate_pct": short_wr,
        "gross_profit_usd": round(gross_profit, 2),
        "gross_loss_usd": round(gross_loss, 2),
        "profit_factor": pf,
    }


def create_empty_kol_ledger(initial_capital: float = INITIAL_CAPITAL, now_et: Optional[datetime] = None) -> Dict[str, Any]:
    cur_et = cte._to_et_datetime(now_et)
    cap = round(float(initial_capital), 2)
    return {
        "schema_version": "v5.7-kol-sniper-ledger",
        "track_name": "V5.7 精简 KOL 狙击轨 (Slim_2S_BE26_ETF52)",
        "initial_capital": cap,
        "max_positions": MAX_POSITIONS,
        "starter_weight": STARTER_WEIGHT,
        "addon_weight": ADDON_WEIGHT,
        "full_weight": FULL_WEIGHT,
        "one_way_slippage_bps": 1.5,
        "cash": cap,
        "unrealized_pnl": 0.0,
        "total_equity": cap,
        "total_return_pct": 0.0,
        "peak_equity": cap,
        "max_drawdown_pct": 0.0,
        "entries_date_et": "",
        "entries_today_count": 0,
        "last_close_time_et": "",
        "last_exit_by_ticker": {},
        "overnight_pending_confirm": {},
        "updated_at_et": cur_et.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "open_positions": [],
        "closed_trades": [],
        "equity_curve": [],
        "stats": _compute_kol_stats([]),
        "alert_dedupe_keys": [],
    }


def _classify_market_session(session_code: str, cur_et: datetime) -> Tuple[bool, bool, str]:
    """返回 (is_regular, is_extended, normalized_session_code)。支持美股半日市 13:00 ET 收盘切入 AFTER_HOURS。"""
    today_d = cur_et.date()
    et_min = cur_et.hour * 60 + cur_et.minute
    close_min = cte.get_us_market_close_minutes(today_d)
    ah_end_min = cte.get_us_after_hours_end_minutes(today_d)
    is_early = cte.is_us_early_close_day(today_d)

    sess_u = str(session_code or "").strip().upper()
    if sess_u in REGULAR_SESSIONS:
        if is_early and et_min >= close_min:
            if et_min < ah_end_min:
                return False, True, "AFTER_HOURS"
            return False, False, "CLOSED"
        return True, False, "REGULAR"
    if sess_u in EXTENDED_SESSIONS:
        if sess_u in ("POST_MARKET", "AFTERHOURS"):
            return False, True, "AFTER_HOURS"
        return False, True, sess_u
    if sess_u in CLOSED_SESSIONS:
        return False, False, sess_u
    if cte.is_us_trading_day(today_d):
        if 9 * 60 + 30 <= et_min < close_min:
            return True, False, "REGULAR"
        if 4 * 60 <= et_min < 9 * 60 + 30:
            return False, True, "PRE_MARKET"
        if close_min <= et_min < ah_end_min:
            return False, True, "AFTER_HOURS"
    return False, False, "CLOSED"


def _resolve_effective_quote_price(lv: Optional[Dict[str, Any]], is_extended: bool = False) -> Tuple[float, bool]:
    """
    提取当前时段有效成交参考价，返回 (price, is_valid_fresh_quote)。
    - 延展时段优先取 ext_price（若有效 > 0），其次取非陈旧 active_price / last；
      若行情明确标记为陈旧/停盘 (is_stale / quote_stale / ext_stale / session_tag=CLOSED)，返回 (0.0, False)。
    """
    if not isinstance(lv, dict):
        return 0.0, False
    if is_extended:
        if (
            bool(lv.get("is_stale", False))
            or bool(lv.get("quote_stale", False))
            or bool(lv.get("ext_stale", False))
            or str(lv.get("session_tag") or "").strip().upper() in ("CLOSED", "STALE", "WEEKEND", "HOLIDAY")
        ):
            return 0.0, False
        ext_px = cte._safe_float(lv.get("ext_price"), 0.0)
        if ext_px > 0:
            return ext_px, True
        act_px = cte._safe_float(lv.get("active_price"), 0.0)
        if act_px > 0:
            return act_px, True
        last_px = cte._safe_float(lv.get("last"), 0.0)
        if last_px > 0 and bool(lv.get("has_realtime", True)):
            return last_px, True
        return 0.0, False

    px = cte._safe_float(lv.get("active_price") or lv.get("last"), 0.0)
    return (px, True) if px > 0 else (0.0, False)


def load_kol_ledger(ledger_path: Optional[str] = None, now_et: Optional[datetime] = None) -> Dict[str, Any]:
    target = os.path.abspath(ledger_path or DEFAULT_KOL_LEDGER_PATH)
    if os.path.exists(target):
        try:
            with open(target, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if isinstance(doc, dict) and "initial_capital" in doc:
                doc.setdefault("schema_version", "v5.5-kol-sniper-ledger")
                doc.setdefault("track_name", "V5.5 激进 KOL 狙击轨")
                doc.setdefault("max_positions", MAX_POSITIONS)
                doc.setdefault("open_positions", [])
                doc.setdefault("closed_trades", [])
                doc.setdefault("equity_curve", [])
                doc.setdefault("alert_dedupe_keys", [])
                doc.setdefault("last_exit_by_ticker", {})
                doc.setdefault("overnight_pending_confirm", {})
                doc.setdefault("flip_count_by_ticker_today", {})
                doc.setdefault("entries_date_et", "")
                doc.setdefault("entries_today_count", 0)
                doc.setdefault("last_close_time_et", "")
                doc["stats"] = _compute_kol_stats(doc.get("closed_trades") or [])
                return doc
        except Exception:
            pass
    return create_empty_kol_ledger(now_et=now_et)


def save_kol_ledger(doc: Dict[str, Any], ledger_path: Optional[str] = None) -> str:
    return cte.save_copy_ledger(doc, ledger_path=ledger_path or DEFAULT_KOL_LEDGER_PATH)


def _extract_corridor_and_extrema(
    lv: Optional[Dict[str, Any]],
    ci: Optional[Dict[str, Any]],
) -> Tuple[str, float, float, float, float]:
    """
    兼容线上主雷达字典与离线回测字典的走廊/盘前/日内高低点提取：
    - corr_div: 支持 ci['divergence']={status:...} 嵌套字典、ci['divergence_code'] 字符串及 lv['corridor']['status']；
    - day_low / day_high: 兼容 day_low/rth_low/low_price 与 day_high/rth_high/high_price；
    - pm_low / pm_high: 兼容 pm_low/pml 与 pm_high/pmh（含 lv['corridor'] 嵌套回退）。
    """
    lv_d = lv if isinstance(lv, dict) else {}
    ci_d = ci if isinstance(ci, dict) else {}
    lv_corr = lv_d.get("corridor") if isinstance(lv_d.get("corridor"), dict) else {}

    def _parse_div(src: Dict[str, Any]) -> str:
        if not isinstance(src, dict):
            return ""
        raw_code = src.get("divergence_code")
        if isinstance(raw_code, str) and raw_code.strip():
            return raw_code.strip().upper()
        raw_div = src.get("divergence")
        if isinstance(raw_div, dict):
            st = raw_div.get("status") or raw_div.get("code") or raw_div.get("divergence_code")
            if isinstance(st, str) and st.strip():
                return st.strip().upper()
        elif isinstance(raw_div, str) and raw_div.strip():
            return raw_div.strip().upper()
        st2 = src.get("status")
        if isinstance(st2, str) and st2.strip().upper() in ("ABOVE_P75", "BELOW_P25", "IN_CORRIDOR", "OPENING_WINDOW"):
            return st2.strip().upper()
        return ""

    corr_div = _parse_div(ci_d) or _parse_div(lv_corr)
    day_low = cte._safe_float(
        ci_d.get("day_low") or ci_d.get("rth_low") or lv_d.get("day_low") or lv_d.get("rth_low") or lv_d.get("low_price"),
        0.0,
    )
    day_high = cte._safe_float(
        ci_d.get("day_high") or ci_d.get("rth_high") or lv_d.get("day_high") or lv_d.get("rth_high") or lv_d.get("high_price"),
        0.0,
    )
    pm_low = cte._safe_float(
        ci_d.get("pm_low") or ci_d.get("pml") or lv_d.get("pm_low") or lv_d.get("pml") or lv_corr.get("pml"),
        0.0,
    )
    pm_high = cte._safe_float(
        ci_d.get("pm_high") or ci_d.get("pmh") or lv_d.get("pm_high") or lv_d.get("pmh") or lv_corr.get("pmh"),
        0.0,
    )
    return corr_div, day_low, day_high, pm_low, pm_high


def resolve_kol_structural_sl(
    ticker: str,
    direction: str,
    entry_price: float,
    live_quote: Optional[Dict[str, Any]] = None,
    sr_anchor: Optional[Dict[str, Any]] = None,
    corridor_item: Optional[Dict[str, Any]] = None,
) -> float:
    """
    计算 V5.5 结构硬止损（含补丁 A）：
    - 1x 股/ETF：锚定结构低/高点外侧 0.6%，严格收紧在 [2.0%, 4.5%]；
    - 3x 杠杆 ETF (SOXL/SOXS/TQQQ/SQQQ/KORU)：锚定结构外侧 1.0%，严格收紧在 [2.5%, 4.5%]。
    """
    if entry_price <= 0:
        return 0.01
    lev3 = is_3x_leveraged(ticker)
    sl_min = SL_MIN_3X if lev3 else SL_MIN_1X
    sl_max = SL_MAX_3X if lev3 else SL_MAX_1X
    pad = 0.010 if lev3 else 0.006

    lv = live_quote if isinstance(live_quote, dict) else {}
    sr = sr_anchor if isinstance(sr_anchor, dict) else {}
    ci = corridor_item if isinstance(corridor_item, dict) else {}
    _, day_low, day_high, pm_low, pm_high = _extract_corridor_and_extrema(lv, ci)
    dir_u = str(direction or "LONG").upper()

    if dir_u == "LONG":
        cands = [
            day_low,
            pm_low,
            cte._safe_float(sr.get("s1") or lv.get("anchor_s1") or lv.get("s1"), 0.0),
            cte._safe_float(lv.get("entry_stop_loss"), 0.0),
        ]
        valid = [c * (1.0 - pad) for c in cands if 0 < c < entry_price]
        lo_cap = entry_price * (1.0 - sl_max)
        hi_cap = entry_price * (1.0 - sl_min)
        if valid:
            # 选最靠近合理区间的结构止损，再硬夹在 [lo_cap, hi_cap]
            in_band = [v for v in valid if lo_cap <= v <= hi_cap]
            raw_sl = max(in_band) if in_band else max(lo_cap, min(hi_cap, max(valid)))
        else:
            raw_sl = entry_price * (1.0 - (0.035 if lev3 else 0.030))
        sl_px = max(lo_cap, min(hi_cap, raw_sl))
        return max(0.01, round(sl_px, 2))

    # SHORT
    cands = [
        day_high,
        pm_high,
        cte._safe_float(sr.get("r1") or lv.get("anchor_r1") or lv.get("r1"), 0.0),
    ]
    valid = [c * (1.0 + pad) for c in cands if c > entry_price]
    lo_cap = entry_price * (1.0 + sl_min)
    hi_cap = entry_price * (1.0 + sl_max)
    if valid:
        in_band = [v for v in valid if lo_cap <= v <= hi_cap]
        raw_sl = min(in_band) if in_band else max(lo_cap, min(hi_cap, min(valid)))
    else:
        raw_sl = entry_price * (1.0 + (0.035 if lev3 else 0.030))
    sl_px = max(lo_cap, min(hi_cap, raw_sl))
    return max(round(entry_price + 0.01, 2), round(sl_px, 2))


def _is_nq_reclaimed_vwap(futures_data: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(futures_data, dict):
        return False
    nq = futures_data.get("NQ=F")
    if not isinstance(nq, dict):
        return False
    if bool(nq.get("reclaim_vwap", False)):
        return True
    vdiff = cte._safe_float(nq.get("vwap_diff_pct"), -999.0)
    last_px = cte._safe_float(nq.get("last"), 0.0)
    vwap_px = cte._safe_float(nq.get("vwap_24h"), 0.0)
    if vdiff >= 0.0 and vdiff > -900.0:
        return True
    if last_px > 0 and vwap_px > 0 and last_px >= vwap_px:
        return True
    return False


def screen_kol_sniper_candidates(
    ai_predictions: Dict[str, Any],
    live_quotes: Dict[str, Dict[str, Any]],
    open_positions: List[Dict[str, Any]],
    cur_et: datetime,
    earnings_map: Optional[Dict[str, Dict[str, Any]]] = None,
    market_regime: str = "",
    vix_regime: Optional[Dict[str, Any]] = None,
    futures_data: Optional[Dict[str, Any]] = None,
    resilient_leaders: Optional[List[str]] = None,
    daily_sr_anchors: Optional[Dict[str, Dict[str, Any]]] = None,
    intraday_path_payload: Optional[Dict[str, Any]] = None,
    last_exit_by_ticker: Optional[Dict[str, Dict[str, Any]]] = None,
    flip_count_by_ticker_today: Optional[Dict[str, int]] = None,
    max_slots: int = MAX_POSITIONS,
) -> List[Dict[str, Any]]:
    """
    V5.5 激进 KOL 盘中结构狙击筛选器：
    - 优先覆盖 KOL 高弹性核心白名单 + 当日 resilient_leaders_top5
    - 必须同时满足 AI 方向优势 + 盘中微观结构形态（Sweep收回 / 突破共振 / 反抽压制空 / 空头15%超跌反抽 / 补丁C 45分钟反手）
    """
    # 正盘主槽位计数严格排除夜盘 10% 特批仓，确保夜盘轻仓绝不挤占正盘 2 主槽位
    main_open = [
        p for p in (open_positions or [])
        if str((p or {}).get("entry_tag") or "").upper() != "OVERNIGHT_APAC_PILOT"
    ]
    free_slots = max(0, int(max_slots) - len(main_open))
    if free_slots <= 0:
        return []

    by_ticker = ai_predictions.get("by_ticker") if isinstance(ai_predictions, dict) else None
    if not isinstance(by_ticker, dict) or not by_ticker:
        return []

    today_d = cur_et.date()
    today_str = today_d.isoformat()
    regime_u = str(market_regime or ai_predictions.get("market_regime") or "NEUTRAL_CHOP").strip().upper()
    vr = vix_regime if isinstance(vix_regime, dict) else (ai_predictions.get("vix_regime") if isinstance(ai_predictions.get("vix_regime"), dict) else {})
    vix_hard_brake = bool(vr.get("vix_hard_brake", False)) or (str(vr.get("vix_state") or "").upper() == "BACKWARDATION_BRAKE")
    panic_clearing = bool(vr.get("panic_clearing_active", False)) or (str(vr.get("vix_state") or "").upper() == "PANIC_CLEARING")
    long_regime_blocked = bool((regime_u == "BEAR_DEFENSE" or vix_hard_brake) and (not panic_clearing))
    nq_vwap_ok = _is_nq_reclaimed_vwap(futures_data)

    leaders_set = {
        str(x.get("ticker") if isinstance(x, dict) else x).strip().upper()
        for x in (resilient_leaders or [])
        if x
    }
    qqq_lv = (live_quotes or {}).get("QQQ") if isinstance(live_quotes, dict) else None
    qqq_pct = cte._safe_float((qqq_lv or {}).get("pct_change"), 0.0)

    corridor_by_tk = {}
    if isinstance(intraday_path_payload, dict):
        corridor_by_tk = intraday_path_payload.get("by_ticker") or intraday_path_payload.get("tickers") or {}

    held_tickers = {str(p.get("ticker") or "").strip().upper() for p in open_positions}
    held_groups = {cte.get_mirror_group(tk) for tk in held_tickers if cte.get_mirror_group(tk)}
    exit_hist = last_exit_by_ticker if isinstance(last_exit_by_ticker, dict) else {}
    flip_cnts = flip_count_by_ticker_today if isinstance(flip_count_by_ticker_today, dict) else {}

    raw_cands: List[Dict[str, Any]] = []
    for tk_raw, ai_item in by_ticker.items():
        if not isinstance(ai_item, dict):
            continue
        tk = str(tk_raw or ai_item.get("ticker") or "").strip().upper()
        if not tk or tk in cte.NON_TRADABLE_DIRECT_TICKERS or tk in held_tickers:
            continue
        if tk not in KOL_CORE_WHITELIST and tk not in leaders_set:
            continue
        mgrp = cte.get_mirror_group(tk)
        if mgrp and mgrp in held_groups:
            continue

        lv = (live_quotes or {}).get(tk) if isinstance(live_quotes, dict) else None
        if not isinstance(lv, dict):
            continue
        active_px = cte._safe_float(lv.get("active_price") or lv.get("last"), 0.0)
        if active_px <= 0:
            continue

        earn_info = (earnings_map or {}).get(tk) if isinstance(earnings_map, dict) else None
        if cte._has_earnings_within_7d(earn_info, today_d):
            continue

        prev_c = cte._safe_float(lv.get("prev_close"), 0.0)
        pct_chg = cte._safe_float(lv.get("pct_change"), 0.0)
        if prev_c <= 0 and abs(pct_chg) < 95.0 and (1.0 + pct_chg / 100.0) > 0:
            prev_c = active_px / (1.0 + pct_chg / 100.0)
        gap_pct = ((active_px - prev_c) / prev_c) * 100.0 if prev_c > 0 else pct_chg

        lev3 = is_3x_leveraged(tk)
        max_gap = 9.0 if lev3 else 5.0

        p_long = cte._safe_float(ai_item.get("calibrated_win_rate_pct"), 0.0)
        p_short = cte._safe_float(ai_item.get("calibrated_short_win_rate_pct"), 0.0)
        is_crash = bool(ai_item.get("is_crash_halt", False) or lv.get("is_crash_halt", False))
        is_cooling = bool(ai_item.get("is_cooling_halt", False) or lv.get("is_cooling_halt", False))
        is_tp_ext = bool(lv.get("is_take_profit", False))
        is_broken = bool(lv.get("is_broken_bearish", False) or lv.get("broken_bearish", False))
        is_deep_pb = bool(lv.get("is_deep_pullback", False) or lv.get("is_pit", False))
        rsi_14 = cte._safe_float(lv.get("rsi") or lv.get("rsi_14") or ai_item.get("rsi_14"), 50.0)
        rsi_5m = cte._safe_float(lv.get("rsi_5m"), 50.0)
        rvol_5m = cte._safe_float(lv.get("rvol_5m"), 1.0)
        vwap = cte._safe_float(lv.get("vwap") or lv.get("vwap_rth"), 0.0)
        vwap_diff = cte._safe_float(lv.get("vwap_diff_pct"), ((active_px - vwap) / vwap * 100.0) if vwap > 0 else 0.0)

        sr = (daily_sr_anchors or {}).get(tk) if isinstance(daily_sr_anchors, dict) else {}
        s1 = cte._safe_float((sr or {}).get("s1") or lv.get("anchor_s1") or lv.get("s1"), 0.0)
        r1 = cte._safe_float((sr or {}).get("r1") or lv.get("anchor_r1") or lv.get("r1"), 0.0)

        ci = corridor_by_tk.get(tk) if isinstance(corridor_by_tk, dict) else {}
        if not isinstance(ci, dict):
            ci = lv.get("corridor") if isinstance(lv.get("corridor"), dict) else {}
        corr_div, day_low, _day_high, pm_low, _pm_high = _extract_corridor_and_extrema(lv, ci)

        # 检查同标的当日平仓冷却 (>= 30 分钟；V5.5.1 已彻底移除冗余 FLIP 分支)
        last_ex = exit_hist.get(tk) if isinstance(exit_hist.get(tk), dict) else None
        last_ex_dir = str((last_ex or {}).get("direction") or "").upper()
        last_ex_date = str((last_ex or {}).get("exit_date") or "")
        last_ex_reason = str((last_ex or {}).get("exit_reason") or "").upper()
        last_ex_dt = _parse_et_timestamp(str((last_ex or {}).get("exit_time_et") or ""), cur_et) if last_ex else None
        elapsed_since_ex = ((cur_et - last_ex_dt).total_seconds() / 60.0) if last_ex_dt else 9999.0

        def _check_reentry_cooldown(target_dir: str) -> bool:
            if not last_ex or last_ex_date != today_str:
                return True
            if last_ex_dir != target_dir and last_ex_reason in ("SL_HIT", "WEAK_EARLY_EXIT"):
                return False  # V5.5.1 已彻底关闭同日止损/早撤反向 FLIP
            return elapsed_since_ex >= SLOT_COOLDOWN_MINUTES

        priority_bonus = 2.0 if tk in leaders_set else (1.0 if tk in LEVERAGED_3X_ETFS else 0.0)
        is_core_etf = tk in V57_CORE_ETF_TIER
        min_prob_req = ETF_MIN_PROB_THRESHOLD if is_core_etf else MIN_PROB_THRESHOLD
        min_spread_req = ETF_MIN_SPREAD_THRESHOLD if is_core_etf else MIN_SPREAD_THRESHOLD
        cand_entry_tag = "MAIN_AI_ETF" if is_core_etf else "NORMAL"

        # ================== A. LONG 通道（个股 >=55%/10%，7只核心ETF >=52%/6%） ==================
        if _check_reentry_cooldown("LONG") and gap_pct <= max_gap and (not is_cooling):
            normal_long_allowed = (not long_regime_blocked) or (tk in cte.SAFE_HAVEN_LONG_EXEMPT)
            if normal_long_allowed and (not is_crash) and p_long >= min_prob_req and (p_long - p_short) >= min_spread_req:
                # 形态 1: 抗跌扫损收回多 (Sweep / 回踩 S1 或 P25 收回 VWAP)
                swept_low = (
                    (s1 > 0 and ((day_low > 0 and day_low <= s1 * 1.006 and active_px >= s1) or abs(active_px - s1) / s1 <= 0.012))
                    or (pm_low > 0 and day_low > 0 and day_low <= pm_low * 1.005 and active_px >= pm_low)
                    or is_deep_pb
                    or corr_div == "BELOW_P25"
                )
                resilient_vs_qqq = (pct_chg >= qqq_pct - 0.5) or (tk in leaders_set)
                sweep_reclaim = swept_low and (vwap_diff >= -0.20) and (rvol_5m >= 1.3) and resilient_vs_qqq

                # 形态 2: 顺势突破共振多 (站稳 VWAP + 突破 R1 或 P75 + 放量)
                breakout_hit = (
                    (vwap_diff >= 0.0)
                    and ((r1 > 0 and active_px >= r1 * 0.998) or corr_div == "ABOVE_P75")
                    and (rvol_5m >= 1.5)
                    and (50.0 <= rsi_5m <= 76.0)
                )

                if sweep_reclaim or breakout_hit:
                    setup_name = "SWEEP_RECLAIM_LONG" if sweep_reclaim else "BREAKOUT_MOMENTUM_LONG"
                    sl_px = resolve_kol_structural_sl(tk, "LONG", active_px, lv, sr, ci)
                    raw_cands.append({
                        "ticker": tk, "direction": "LONG", "setup": setup_name,
                        "entry_tag": cand_entry_tag, "slot_weight": STARTER_WEIGHT, "can_add": True,
                        "calibrated_prob_pct": round(p_long, 1), "opp_prob_pct": round(p_short, 1),
                        "prob_spread_pct": round(p_long - p_short, 1),
                        "score": p_long + (p_long - p_short) * 0.25 + priority_bonus + (2.0 if sweep_reclaim else 1.0),
                        "entry_price": round(active_px, 2), "sl_price": sl_px,
                        "gap_pct": round(gap_pct, 2), "mirror_group": mgrp,
                    })

        # ================== B. SHORT 通道（严禁做空反向/波动率 ETF: SOXS/SQQQ/VIXY） ==================
        inv_extreme_surge = bool(tk in cte.INVERSE_ETFS and (gap_pct >= 8.0 or pct_chg >= 8.0 or is_tp_ext or rsi_14 >= 68.0))
        if (
            _check_reentry_cooldown("SHORT")
            and (tk not in cte.NON_SHORTABLE_TICKERS)
            and (tk not in V57_NO_SHORT_INVERSE_ETFS)
            and (not inv_extreme_surge)
            and (-gap_pct) <= max_gap
        ):
            if p_short >= min_prob_req and (p_short - p_long) >= min_spread_req:
                bear_context = is_broken or (regime_u == "BEAR_DEFENSE") or vix_hard_brake or (pct_chg < 0) or (corr_div == "BELOW_P25")
                near_vwap_or_r1_reject = (
                    (-1.5 <= vwap_diff <= 0.40)
                    or (r1 > 0 and abs(active_px - r1) / r1 <= 0.012)
                ) and (vwap_diff <= 0.25) and (rsi_5m <= 58.0)
                if bear_context and near_vwap_or_r1_reject:
                    sl_px = resolve_kol_structural_sl(tk, "SHORT", active_px, lv, sr, ci)
                    raw_cands.append({
                        "ticker": tk, "direction": "SHORT", "setup": "REBOUND_REJECT_SHORT",
                        "entry_tag": cand_entry_tag, "slot_weight": STARTER_WEIGHT, "can_add": True,
                        "calibrated_prob_pct": round(p_short, 1), "opp_prob_pct": round(p_long, 1),
                        "prob_spread_pct": round(p_short - p_long, 1),
                        "score": p_short + (p_short - p_long) * 0.25 + priority_bonus + (2.5 if long_regime_blocked else 0.0),
                        "entry_price": round(active_px, 2), "sl_price": sl_px,
                        "gap_pct": round(gap_pct, 2), "mirror_group": mgrp,
                    })

    raw_cands.sort(key=lambda c: (-c["score"], -c["calibrated_prob_pct"], c["ticker"]))
    selected: List[Dict[str, Any]] = []
    used_tickers = set(held_tickers)
    used_groups = set(held_groups)
    for c in raw_cands:
        if len(selected) >= free_slots:
            break
        tk = c["ticker"]
        mg = c["mirror_group"]
        if tk in used_tickers:
            continue
        if mg and mg in used_groups:
            continue
        selected.append(c)
        used_tickers.add(tk)
        if mg:
            used_groups.add(mg)
    return selected


def screen_overnight_apac_pilot_candidate(
    ai_predictions: Dict[str, Any],
    live_quotes: Dict[str, Dict[str, Any]],
    open_positions: List[Dict[str, Any]],
    cur_et: datetime,
    apac_chip_sentiment: Optional[Dict[str, Any]] = None,
    earnings_map: Optional[Dict[str, Dict[str, Any]]] = None,
    market_regime: str = "",
    vix_regime: Optional[Dict[str, Any]] = None,
    daily_sr_anchors: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    V5.7 夜盘 OVERNIGHT_APAC_PILOT 10% 轻仓特批筛选器（独立于正盘 2 主槽位）：
    - 仅在 OVERNIGHT 时段、且亚盘半导体风向 (`apac_chip_sentiment.chip_group.bias`) 明确为 BULLISH / BEARISH 时激活；
    - 仅限 OVERNIGHT_PILOT_WHITELIST + 有效盘外实时价 + 点差 <= 35 bps + 无 7 日财报雷 + 严禁做空反向 ETF；
    - 返回最高分单只候选，供调用方执行连续 2 轮 (>=60s) 确认后再建 10% 独立轻仓。
    """
    if not isinstance(apac_chip_sentiment, dict):
        return None
    cg = apac_chip_sentiment.get("chip_group") if isinstance(apac_chip_sentiment.get("chip_group"), dict) else {}
    chip_bias = str(cg.get("bias") or apac_chip_sentiment.get("soxx_lead_bias") or "NEUTRAL").strip().upper()
    if chip_bias not in ("BULLISH", "BEARISH"):
        return None

    overnight_open = [
        p for p in (open_positions or [])
        if str((p or {}).get("entry_tag") or "").upper() == "OVERNIGHT_APAC_PILOT"
    ]
    if len(overnight_open) >= OVERNIGHT_PILOT_MAX_POSITIONS:
        return None

    by_ticker = ai_predictions.get("by_ticker") if isinstance(ai_predictions, dict) else None
    if not isinstance(by_ticker, dict) or not by_ticker:
        return None

    today_d = cur_et.date()
    regime_u = str(market_regime or ai_predictions.get("market_regime") or "NEUTRAL_CHOP").strip().upper()
    vr = vix_regime if isinstance(vix_regime, dict) else (ai_predictions.get("vix_regime") if isinstance(ai_predictions.get("vix_regime"), dict) else {})
    vix_hard_brake = bool(vr.get("vix_hard_brake", False)) or (str(vr.get("vix_state") or "").upper() == "BACKWARDATION_BRAKE")
    panic_clearing = bool(vr.get("panic_clearing_active", False)) or (str(vr.get("vix_state") or "").upper() == "PANIC_CLEARING")
    long_regime_blocked = bool((regime_u == "BEAR_DEFENSE" or vix_hard_brake) and (not panic_clearing))

    held_tickers = {str(p.get("ticker") or "").strip().upper() for p in (open_positions or [])}
    held_groups = {cte.get_mirror_group(tk) for tk in held_tickers if cte.get_mirror_group(tk)}

    cands: List[Dict[str, Any]] = []
    for tk in sorted(OVERNIGHT_PILOT_WHITELIST):
        if tk in held_tickers or tk in cte.NON_TRADABLE_DIRECT_TICKERS:
            continue
        mgrp = cte.get_mirror_group(tk)
        if mgrp and mgrp in held_groups:
            continue
        ai_item = by_ticker.get(tk)
        if not isinstance(ai_item, dict):
            continue
        lv = (live_quotes or {}).get(tk) if isinstance(live_quotes, dict) else None
        if not isinstance(lv, dict):
            continue
        ext_px, q_ok = _resolve_effective_quote_price(lv, is_extended=True)
        if (not q_ok) or ext_px <= 0:
            continue
        # 严格点差护栏 (<= 35 bps)
        spread_bps = cte._safe_float(lv.get("spread_bps"), None)
        if spread_bps is not None and spread_bps > OVERNIGHT_PILOT_MAX_SPREAD_BPS:
            continue
        earn_info = (earnings_map or {}).get(tk) if isinstance(earnings_map, dict) else None
        if cte._has_earnings_within_7d(earn_info, today_d):
            continue

        p_long = cte._safe_float(ai_item.get("calibrated_win_rate_pct"), 0.0)
        p_short = cte._safe_float(ai_item.get("calibrated_short_win_rate_pct"), 0.0)
        is_crash = bool(ai_item.get("is_crash_halt", False) or lv.get("is_crash_halt", False))
        is_cooling = bool(ai_item.get("is_cooling_halt", False) or lv.get("is_cooling_halt", False))
        sr = (daily_sr_anchors or {}).get(tk) if isinstance(daily_sr_anchors, dict) else {}

        if chip_bias == "BULLISH":
            if tk in cte.INVERSE_ETFS or is_crash or is_cooling:
                continue
            if long_regime_blocked and tk not in cte.SAFE_HAVEN_LONG_EXEMPT:
                continue
            if p_long >= ETF_MIN_PROB_THRESHOLD and (p_long - p_short) >= ETF_MIN_SPREAD_THRESHOLD:
                sl_px = resolve_kol_structural_sl(tk, "LONG", ext_px, lv, sr, None)
                cands.append({
                    "ticker": tk, "direction": "LONG", "setup": "OVERNIGHT_APAC_RESONANCE",
                    "entry_tag": "OVERNIGHT_APAC_PILOT", "slot_weight": OVERNIGHT_PILOT_WEIGHT,
                    "can_add": False, "calibrated_prob_pct": round(p_long, 1),
                    "opp_prob_pct": round(p_short, 1), "prob_spread_pct": round(p_long - p_short, 1),
                    "score": p_long + (p_long - p_short) * 0.25 + (3.0 if tk in ("SOXX", "SOXL", "NVDA", "TSM") else 0.0),
                    "entry_price": round(ext_px, 2), "sl_price": sl_px, "mirror_group": mgrp,
                    "apac_chip_bias": chip_bias, "apac_chip_score": cte._safe_float(cg.get("score"), 50.0),
                })
        else:
            # BEARISH 亚盘芯片共振：优先做多反向 ETF (SOXS / SQQQ)，或做空非反向芯片标的
            if tk in cte.INVERSE_ETFS:
                if (not is_crash) and (not is_cooling) and p_long >= ETF_MIN_PROB_THRESHOLD and (p_long - p_short) >= ETF_MIN_SPREAD_THRESHOLD:
                    sl_px = resolve_kol_structural_sl(tk, "LONG", ext_px, lv, sr, None)
                    cands.append({
                        "ticker": tk, "direction": "LONG", "setup": "OVERNIGHT_APAC_RESONANCE",
                        "entry_tag": "OVERNIGHT_APAC_PILOT", "slot_weight": OVERNIGHT_PILOT_WEIGHT,
                        "can_add": False, "calibrated_prob_pct": round(p_long, 1),
                        "opp_prob_pct": round(p_short, 1), "prob_spread_pct": round(p_long - p_short, 1),
                        "score": p_long + (p_long - p_short) * 0.25 + 4.0,
                        "entry_price": round(ext_px, 2), "sl_price": sl_px, "mirror_group": mgrp,
                        "apac_chip_bias": chip_bias, "apac_chip_score": cte._safe_float(cg.get("score"), 50.0),
                    })
            elif tk not in cte.NON_SHORTABLE_TICKERS and tk not in V57_NO_SHORT_INVERSE_ETFS:
                if p_short >= ETF_MIN_PROB_THRESHOLD and (p_short - p_long) >= ETF_MIN_SPREAD_THRESHOLD:
                    sl_px = resolve_kol_structural_sl(tk, "SHORT", ext_px, lv, sr, None)
                    cands.append({
                        "ticker": tk, "direction": "SHORT", "setup": "OVERNIGHT_APAC_RESONANCE",
                        "entry_tag": "OVERNIGHT_APAC_PILOT", "slot_weight": OVERNIGHT_PILOT_WEIGHT,
                        "can_add": False, "calibrated_prob_pct": round(p_short, 1),
                        "opp_prob_pct": round(p_long, 1), "prob_spread_pct": round(p_short - p_long, 1),
                        "score": p_short + (p_short - p_long) * 0.25 + (2.0 if tk in ("SOXX", "NVDA", "TSM") else 0.0),
                        "entry_price": round(ext_px, 2), "sl_price": sl_px, "mirror_group": mgrp,
                        "apac_chip_bias": chip_bias, "apac_chip_score": cte._safe_float(cg.get("score"), 50.0),
                    })

    if not cands:
        return None
    cands.sort(key=lambda c: (-c["score"], -c["calibrated_prob_pct"], c["ticker"]))
    return cands[0]


def _recalculate_kol_equity(
    ledger: Dict[str, Any],
    live_quotes: Dict[str, Dict[str, Any]],
    cur_et: datetime,
    record_curve_point: bool = True,
    is_extended: bool = False,
) -> Dict[str, Any]:
    init_cap = cte._safe_float(ledger.get("initial_capital"), INITIAL_CAPITAL)
    cash = round(cte._safe_float(ledger.get("cash"), init_cap), 2)
    today_d = cur_et.date()
    today_str = today_d.isoformat()

    unrealized_total = 0.0
    deployed_value = 0.0
    for pos in ledger.get("open_positions") or []:
        tk = str(pos.get("ticker") or "").upper()
        direction = str(pos.get("direction") or "LONG").upper()
        entry_px = cte._safe_float(pos.get("entry_price"), 0.0)
        alloc = cte._safe_float(pos.get("allocated_capital"), 0.0)
        lv = (live_quotes or {}).get(tk) if isinstance(live_quotes, dict) else None
        cur_px, q_ok = _resolve_effective_quote_price(lv, is_extended=is_extended)
        if (not q_ok) or cur_px <= 0:
            cur_px = cte._safe_float(pos.get("last_price"), entry_px)
        net_ret = cte._calc_net_return(direction, entry_px, cur_px)
        u_pnl = round(alloc * net_ret, 2)
        pos["last_price"] = round(cur_px, 2)
        pos["unrealized_return_pct"] = round(net_ret * 100.0, 2)
        pos["unrealized_pnl_usd"] = u_pnl

        prev_mfe = cte._safe_float(pos.get("max_favorable_return_pct"), -999.0)
        pos["max_favorable_return_pct"] = round(max(prev_mfe, net_ret * 100.0), 2)
        if direction == "LONG":
            prev_best = cte._safe_float(pos.get("best_price"), entry_px)
            pos["best_price"] = round(max(prev_best, cur_px), 2)
        else:
            prev_best = cte._safe_float(pos.get("best_price"), entry_px)
            pos["best_price"] = round(min(prev_best, cur_px) if prev_best > 0 else cur_px, 2)

        try:
            exp_d = datetime.strptime(str(pos.get("expiry_date"))[:10], "%Y-%m-%d").date()
            pos["remaining_trading_days"] = cte.count_remaining_trading_days(today_d, exp_d)
        except Exception:
            pos["remaining_trading_days"] = 0

        unrealized_total += u_pnl
        deployed_value += max(0.0, alloc + u_pnl)

    total_equity = round(cash + deployed_value, 2)
    total_ret_pct = round(((total_equity - init_cap) / init_cap) * 100.0, 2) if init_cap > 0 else 0.0
    prev_peak = max(init_cap, cte._safe_float(ledger.get("peak_equity"), init_cap))
    peak_equity = round(max(prev_peak, total_equity), 2)
    cur_dd = ((peak_equity - total_equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0
    max_dd = round(max(cte._safe_float(ledger.get("max_drawdown_pct"), 0.0), cur_dd), 2)

    ledger["cash"] = cash
    ledger["unrealized_pnl"] = round(unrealized_total, 2)
    ledger["total_equity"] = total_equity
    ledger["total_return_pct"] = total_ret_pct
    ledger["peak_equity"] = peak_equity
    ledger["max_drawdown_pct"] = max_dd
    ledger["updated_at_et"] = cur_et.strftime("%Y-%m-%d %H:%M:%S")
    ledger["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    ledger["stats"] = _compute_kol_stats(ledger.get("closed_trades") or [])

    if record_curve_point and (ledger.get("open_positions") or ledger.get("closed_trades")):
        curve = ledger.setdefault("equity_curve", [])
        pt = {"date": today_str, "equity": total_equity, "return_pct": total_ret_pct}
        if curve and isinstance(curve[-1], dict) and curve[-1].get("date") == today_str:
            curve[-1] = pt
        else:
            curve.append(pt)
        if len(curve) > 250:
            ledger["equity_curve"] = curve[-250:]

    return ledger


def format_kol_sniper_event_alert(event_kind: str, payload: Dict[str, Any], ledger: Dict[str, Any]) -> Tuple[str, str]:
    """生成 V5.5 激进 KOL 轨开仓/加仓/分批止盈/平仓提醒（严禁出现“建议股数”）。"""
    tk = str(payload.get("ticker") or "")
    direction = str(payload.get("direction") or "LONG").upper()
    dir_icon = "🔴 做空" if direction == "SHORT" else "🟢 做多"
    eq = cte._safe_float(ledger.get("total_equity"), INITIAL_CAPITAL)
    tot_ret = cte._safe_float(ledger.get("total_return_pct"), 0.0)

    if event_kind == "OPEN":
        wt = int(round(cte._safe_float(payload.get("slot_weight"), STARTER_WEIGHT) * 100))
        entry = cte._safe_float(payload.get("entry_price"), 0.0)
        sl = cte._safe_float(payload.get("sl_price"), 0.0)
        sl_pct = ((sl - entry) / entry) * 100.0 if entry > 0 else 0.0
        setup = str(payload.get("setup") or "KOL_SNIPER")
        tag = str(payload.get("entry_tag") or "NORMAL")
        title = f"⚡ [KOL狙击·开仓] {dir_icon} {tk} ({wt}%仓位 · {tag})"
        body = (
            f"⚡ [V5.7 KOL狙击·开仓] {dir_icon} {tk} ({wt}%仓位) | "
            f"入场 ${entry:.2f} | 结构止损 ${sl:.2f} ({sl_pct:+.1f}%) | 形态: {setup} | 最大持有至 T+3 ({payload.get('expiry_date', '--')})\n"
            f"• 账户净值: ${eq:,.2f} ({tot_ret:+.2f}%) · 滑点口径: 单边 1.5 bps"
        )
        return title, body

    if event_kind == "ADDON":
        avg_ep = cte._safe_float(payload.get("entry_price"), 0.0)
        add_px = cte._safe_float(payload.get("add_price"), avg_ep)
        sl = cte._safe_float(payload.get("sl_price"), 0.0)
        title = f"📈 [KOL狙击·顺势加仓] {dir_icon} {tk} (30%→50%仓位)"
        body = (
            f"📈 [V5.7 顺势加仓] {dir_icon} {tk} 加仓 +20% @ ${add_px:.2f} ➔ 新均价 ${avg_ep:.2f} | "
            f"硬止损已强制上移至原底仓附近 ${sl:.2f}（锁定总风险不放大）\n"
            f"• 账户净值: ${eq:,.2f} ({tot_ret:+.2f}%)"
        )
        return title, body

    if event_kind == "FAST_BE":
        px = cte._safe_float(payload.get("fast_be_exit_price") or payload.get("last_price"), 0.0)
        sl = cte._safe_float(payload.get("sl_price"), 0.0)
        rp = cte._safe_float(payload.get("fast_be_realized_pnl_usd"), 0.0)
        title = f"🛡️ [KOL狙击·BE26快推保本] {dir_icon} {tk} 先平25% + 推保本"
        body = (
            f"🛡️ [V5.7 BE26快推保本] {dir_icon} {tk} 先落袋 25% @ ${px:.2f} (已落袋 +${abs(rp):.2f}) | "
            f"余仓 75% 止损已无条件推至保本损 ${sl:.2f}，杜绝浮盈倒亏\n"
            f"• 账户净值: ${eq:,.2f} ({tot_ret:+.2f}%)"
        )
        return title, body

    if event_kind == "PARTIAL_TP":
        px = cte._safe_float(payload.get("partial_exit_price"), 0.0)
        sl = cte._safe_float(payload.get("sl_price"), 0.0)
        rp = cte._safe_float(payload.get("partial_realized_pnl_usd"), 0.0)
        title = f"💰 [KOL狙击·一阶落袋] {dir_icon} {tk} 锁利 + 50%尾仓追踪"
        body = (
            f"💰 [V5.7 一阶止盈] {dir_icon} {tk} 阶梯减仓 @ ${px:.2f} (累计已落袋 +${abs(rp):.2f}) | "
            f"保留完整 50% 尾仓保本损 ${sl:.2f} 并开启移动追踪止盈\n"
            f"• 账户净值: ${eq:,.2f} ({tot_ret:+.2f}%)"
        )
        return title, body

    # CLOSE
    outcome = str(payload.get("outcome") or "SL_HIT").upper()
    tag_map = {
        "TRAIL_TP": "✅ [KOL狙击·追踪止盈]",
        "CLIMAX_TP": "🔥 [KOL狙击·放量卖强]",
        "BE_STOP": "🛡️ [KOL狙击·保本损退场]",
        "WEAK_EARLY_EXIT": "⚡ [KOL狙击·不及预期早撤]",
        "SL_HIT": "🛑 [KOL狙击·结构止损]",
        "T3_WIN": "⏱️ [KOL狙击·T+3获利结算]",
        "T3_LOSS": "⏱️ [KOL狙击·T+3到期强平]",
    }
    tag = tag_map.get(outcome, f"📋 [KOL狙击·{outcome}]")
    ret_pct = cte._safe_float(payload.get("net_return_pct"), 0.0)
    pnl_usd = cte._safe_float(payload.get("pnl_usd"), 0.0)
    ps = "+" if pnl_usd >= 0 else "-"
    ep = cte._safe_float(payload.get("entry_price"), 0.0)
    xp = cte._safe_float(payload.get("exit_price"), 0.0)
    title = f"{tag} {dir_icon} {tk} ({ret_pct:+.2f}%)"
    body = (
        f"{tag} {dir_icon} {tk} | {ret_pct:+.2f}% ({ps}${abs(pnl_usd):.2f}) | "
        f"KOL 净值: ${eq:,.2f} ({tot_ret:+.2f}%)\n"
        f"• 成交回溯: 均价 ${ep:.2f} ➔ 平仓 ${xp:.2f}（已扣往返 3 bps 滑点）"
    )
    return title, body


def run_kol_copy_trade_cycle(
    ai_predictions: Dict[str, Any],
    live_quotes: Dict[str, Dict[str, Any]],
    earnings_map: Optional[Dict[str, Dict[str, Any]]] = None,
    market_regime: str = "",
    vix_regime: Optional[Dict[str, Any]] = None,
    futures_data: Optional[Dict[str, Any]] = None,
    resilient_leaders: Optional[List[str]] = None,
    daily_sr_anchors: Optional[Dict[str, Dict[str, Any]]] = None,
    intraday_path_payload: Optional[Dict[str, Any]] = None,
    apac_chip_sentiment: Optional[Dict[str, Any]] = None,
    session_code: str = "REGULAR",
    market_session: Optional[str] = None,
    now_et: Optional[datetime] = None,
    ledger_path: Optional[str] = None,
    dry_run: Optional[bool] = None,
    alert_sender: Optional[Callable[[str, str, str, int], Any]] = None,
) -> Dict[str, Any]:
    """
    执行单轮 V5.7 精简版 KOL 狙击轨状态机 (`Slim_2S_BE26_ETF52` + 禁空反向 ETF + 夜盘 10% 特批)：
    1. 常规盘中 (REGULAR) 全功能管理在途持仓：
       a) 即时止损/保本损 (SL_HIT / BE_STOP)；
       b) 金字塔顺势加仓 (+1.8%/+2.8%，加仓 +20% 移损至原底仓外侧 1.0% 缓冲，优先于 BE26)；
       c) Neo Lee 快推保本 BE26 (+2.6% / 3x +4.0%)：先平 25% 落袋并将余仓止损推至 +0.3% 保本；
       d) 一阶阶梯止盈 (+4.0% / 3x +6.5%)：再平余仓 1/3（保留完整 50% 尾仓）开启追踪；
       e) 二阶抛物线卖强 (CLIMAX_TP) 与移动追踪止盈 (TRAIL_TP)；
       f) T+2 不及预期早撤 (WEAK_EARLY_EXIT) 与 T+3 到期强平（半日市动态锚定 close-10m）。
    2. 延展时段 (PRE_MARKET / AFTER_HOURS / OVERNIGHT) 防插针保护止损（连续 2 轮 >= 60s 确认 + 宽点差 > 60 bps 护栏）。
    3. 常规盘中 09:45 ~ close-40m 滚动狙击（2 槽×50%，个股 55%/10%，7 只核心 ETF 52%/6%，严禁做空反向 ETF）。
    4. OVERNIGHT 夜盘独立 10% 轻仓特批 (`OVERNIGHT_APAC_PILOT`)：亚盘半导体共振 + 白名单 + 点差 <= 35 bps + 连续 2 轮确认，独立于主 2 槽。
    """
    cur_et = cte._to_et_datetime(now_et)
    today_d = cur_et.date()
    today_str = today_d.isoformat()
    et_min = cur_et.hour * 60 + cur_et.minute
    close_min = cte.get_us_market_close_minutes(today_d)
    t3_force_exit_min = close_min - 10            # 正常日 15:50 ET / 半日市 12:50 ET
    entry_end_min = min(close_min - 40, close_min - 10)  # 正常日 15:20 ET / 半日市 12:20 ET（尾盘禁开新仓）
    eff_session_arg = market_session if (market_session is not None and str(market_session).strip()) else session_code
    is_regular_session, is_extended_session, norm_session = _classify_market_session(eff_session_arg, cur_et)

    eff_dry_run = bool(dry_run) if dry_run is not None else cte.is_external_alerts_disabled()
    ledger = load_kol_ledger(ledger_path=ledger_path, now_et=cur_et)

    if str(ledger.get("entries_date_et") or "") != today_str:
        ledger["entries_date_et"] = today_str
        ledger["entries_today_count"] = 0

    closed_now: List[Dict[str, Any]] = []
    opened_now: List[Dict[str, Any]] = []
    addons_now: List[Dict[str, Any]] = []
    fast_be_now: List[Dict[str, Any]] = []
    partials_now: List[Dict[str, Any]] = []
    alerts_sent: List[Dict[str, str]] = []
    dedupe_set = set(ledger.get("alert_dedupe_keys") or [])

    # 1. 常规盘中全功能管理 OR 延展时段纯防守防插针止损
    if (is_regular_session or is_extended_session) and ledger.get("open_positions"):
        still_open: List[Dict[str, Any]] = []
        for pos in ledger["open_positions"]:
            tk = str(pos.get("ticker") or "").upper()
            direction = str(pos.get("direction") or "LONG").upper()
            entry_px = cte._safe_float(pos.get("entry_price"), 0.0)
            sl_px = cte._safe_float(pos.get("sl_price"), 0.0)
            alloc = cte._safe_float(pos.get("allocated_capital"), 0.0)
            lv = (live_quotes or {}).get(tk) if isinstance(live_quotes, dict) else None
            cur_px, quote_valid = _resolve_effective_quote_price(lv, is_extended=is_extended_session)
            if (not quote_valid) or cur_px <= 0 or entry_px <= 0:
                still_open.append(pos)
                continue

            lev3 = is_3x_leveraged(tk)
            rsi_5m = cte._safe_float((lv or {}).get("rsi_5m"), 50.0)
            rvol_5m = cte._safe_float((lv or {}).get("rvol_5m"), 1.0)
            vwap = cte._safe_float((lv or {}).get("vwap") or (lv or {}).get("vwap_rth"), 0.0)
            vwap_diff = cte._safe_float((lv or {}).get("vwap_diff_pct"), ((cur_px - vwap) / vwap * 100.0) if vwap > 0 else 0.0)

            net_ret = cte._calc_net_return(direction, entry_px, cur_px)
            ret_pct = net_ret * 100.0
            partial_done = bool(pos.get("partial_tp_done", False))
            be_active = bool(pos.get("be_stop_active", False))

            exit_reason = ""
            ext_confirmed_close = False

            if is_extended_session:
                # ---------- 延展时段 (PRE_MARKET / AFTER_HOURS / OVERNIGHT)：仅检查防守止损/保本损，连续2轮且>=60s确认 ----------
                # 宽点差防假扫损护栏：spread_bps > 60.0 时跳过本次止损判定，既不误杀也不清零已有真实破位计数
                cur_spread_bps = cte._safe_float((lv or {}).get("spread_bps"), 0.0)
                if cur_spread_bps > EXTENDED_MAX_SPREAD_BPS:
                    still_open.append(pos)
                    continue
                sl_breached = bool(
                    sl_px > 0 and (
                        (direction == "LONG" and cur_px <= sl_px)
                        or (direction == "SHORT" and cur_px >= sl_px)
                    )
                )
                if sl_breached:
                    prev_cnt = int(pos.get("ext_sl_breach_count") or 0)
                    first_ts_str = str(pos.get("ext_sl_first_breach_ts") or "")
                    first_dt = _parse_et_timestamp(first_ts_str, cur_et) if first_ts_str else None
                    if prev_cnt <= 0 or first_dt is None:
                        pos["ext_sl_breach_count"] = 1
                        pos["ext_sl_first_breach_ts"] = cur_et.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        elapsed_sec = (cur_et - first_dt).total_seconds()
                        if elapsed_sec >= EXTENDED_SL_CONFIRM_SECONDS:
                            pos["ext_sl_breach_count"] = prev_cnt + 1
                            exit_reason = "BE_STOP" if be_active else "SL_HIT"
                            ext_confirmed_close = True
                        else:
                            # 距首次击穿不足 60 秒，继续等待下一轮防插针确认
                            pos["ext_sl_breach_count"] = 1
                else:
                    # 价格回弹至安全区 -> 立即清零连续计数器
                    pos["ext_sl_breach_count"] = 0
                    pos["ext_sl_first_breach_ts"] = ""
            else:
                # ---------- 常规盘中 (REGULAR)：止损 -> 顺势加仓 -> BE26快推保本 -> 一阶落袋 -> 追踪/卖强 -> T+2/T+3 ----------
                pos["ext_sl_breach_count"] = 0
                pos["ext_sl_first_breach_ts"] = ""

                gross_ret_pct = (
                    ((cur_px - entry_px) / entry_px) if direction == "LONG" else ((entry_px - cur_px) / entry_px)
                ) * 100.0
                prev_mfe = cte._safe_float(pos.get("max_favorable_return_pct"), gross_ret_pct)
                mfe_pct = max(prev_mfe, gross_ret_pct)
                pos["max_favorable_return_pct"] = round(mfe_pct, 2)

                if direction == "LONG":
                    best_px = max(cte._safe_float(pos.get("best_price"), entry_px), cur_px)
                else:
                    pb = cte._safe_float(pos.get("best_price"), entry_px)
                    best_px = min(pb, cur_px) if pb > 0 else cur_px
                pos["best_price"] = round(best_px, 2)

                # (1a) 优先检查硬止损 / 保本损
                if direction == "LONG" and sl_px > 0 and cur_px <= sl_px:
                    exit_reason = "BE_STOP" if be_active else "SL_HIT"
                elif direction == "SHORT" and sl_px > 0 and cur_px >= sl_px:
                    exit_reason = "BE_STOP" if be_active else "SL_HIT"

                # (1b) 金字塔顺势加仓 (+1.8% / 3x +2.8%，优先于 BE26，仅主槽位底仓允许加仓)
                if (
                    (not exit_reason)
                    and int(pos.get("stage") or 1) == 1
                    and bool(pos.get("can_add", False))
                    and (not partial_done)
                    and (not bool(pos.get("fast_be_trimmed", False)))
                    and str(pos.get("entry_tag") or "").upper() != "OVERNIGHT_APAC_PILOT"
                ):
                    add_trig = ADDON_TRIGGER_3X_PCT if lev3 else ADDON_TRIGGER_1X_PCT
                    on_vwap_side = (vwap_diff >= 0.0) if direction == "LONG" else (vwap_diff <= 0.0)
                    if gross_ret_pct >= add_trig and on_vwap_side:
                        cur_cash = cte._safe_float(ledger.get("cash"), 0.0)
                        est_eq = max(cte._safe_float(ledger.get("total_equity"), INITIAL_CAPITAL), cur_cash + alloc)
                        add_alloc = round(min(cur_cash, est_eq * ADDON_WEIGHT), 2)
                        if add_alloc >= 10.0:
                            orig_ep = cte._safe_float(pos.get("orig_entry_price"), entry_px)
                            sh1 = alloc / entry_px
                            sh2 = add_alloc / cur_px
                            new_avg = round((alloc + add_alloc) / (sh1 + sh2), 4)
                            ledger["cash"] = round(cur_cash - add_alloc, 2)
                            pos["orig_entry_price"] = round(orig_ep, 2)
                            pos["add_price"] = round(cur_px, 2)
                            pos["added_at_et"] = cur_et.strftime("%Y-%m-%d %H:%M:%S")
                            pos["entry_price"] = round(new_avg, 2)
                            entry_px = pos["entry_price"]
                            pos["allocated_capital"] = round(alloc + add_alloc, 2)
                            alloc = pos["allocated_capital"]
                            pos["slot_weight"] = FULL_WEIGHT
                            pos["peak_slot_weight"] = FULL_WEIGHT
                            pos["stage"] = 2
                            pos["can_add"] = False
                            if direction == "LONG":
                                pos["sl_price"] = max(sl_px, round(orig_ep * (1.0 - ADDON_SL_BUFFER), 2))
                            else:
                                pos["sl_price"] = min(sl_px, round(orig_ep * (1.0 + ADDON_SL_BUFFER), 2))
                            sl_px = pos["sl_price"]
                            net_ret = cte._calc_net_return(direction, entry_px, cur_px)
                            ret_pct = net_ret * 100.0
                            gross_ret_pct = (
                                ((cur_px - entry_px) / entry_px) if direction == "LONG" else ((entry_px - cur_px) / entry_px)
                            ) * 100.0
                            addons_now.append(dict(pos))

                # (1c) V5.7 Neo Lee 快推保本 BE26 (+2.6% / 3x +4.0%)：先平 25% 落袋现金 + 止损无条件推至 +0.3% 保本
                if (not exit_reason) and (not be_active) and (not partial_done):
                    fast_be_trig = FAST_BE_3X_PCT if lev3 else FAST_BE_1X_PCT
                    if gross_ret_pct >= fast_be_trig:
                        close_alloc = round(alloc * FAST_BE_TRIM_FRAC, 2)
                        rem_alloc = round(alloc - close_alloc, 2)
                        part_pnl = round(close_alloc * net_ret, 2)
                        returned_cash = max(0.0, round(close_alloc + part_pnl, 2))
                        ledger["cash"] = round(cte._safe_float(ledger.get("cash"), 0.0) + returned_cash, 2)

                        pos["allocated_capital"] = rem_alloc
                        pos["slot_weight"] = round(cte._safe_float(pos.get("slot_weight"), STARTER_WEIGHT) * (1.0 - FAST_BE_TRIM_FRAC), 3)
                        pos["fast_be_trimmed"] = True
                        pos["be_stop_active"] = True
                        pos["can_add"] = False
                        pos["fast_be_exit_price"] = round(cur_px, 2)
                        pos["fast_be_exit_time_et"] = cur_et.strftime("%Y-%m-%d %H:%M:%S")
                        pos["fast_be_realized_pnl_usd"] = part_pnl
                        pos["partial_closed_capital"] = round(cte._safe_float(pos.get("partial_closed_capital"), 0.0) + close_alloc, 2)
                        pos["partial_realized_pnl_usd"] = round(cte._safe_float(pos.get("partial_realized_pnl_usd"), 0.0) + part_pnl, 2)
                        if direction == "LONG":
                            pos["sl_price"] = max(sl_px, round(entry_px * (1.0 + BE_STOP_BUFFER), 2))
                        else:
                            pos["sl_price"] = min(sl_px, round(entry_px * (1.0 - BE_STOP_BUFFER), 2))
                        be_active = True
                        alloc = rem_alloc
                        sl_px = pos["sl_price"]
                        fast_be_now.append(dict(pos))

                # (1d) 一阶阶梯止盈 (+4.0% / 3x +6.5%)：若已 BE26 减过 25%，此处仅减余仓 1/3 -> 永远保留 50% 完整尾仓奔跑
                if not exit_reason and not partial_done:
                    p_trig = PARTIAL_TP_3X_PCT if lev3 else PARTIAL_TP_1X_PCT
                    if gross_ret_pct >= p_trig:
                        t_frac = PARTIAL_AFTER_BE_TRIM_FRAC if bool(pos.get("fast_be_trimmed", False)) else 0.50
                        close_alloc = round(alloc * t_frac, 2)
                        rem_alloc = round(alloc - close_alloc, 2)
                        part_pnl = round(close_alloc * net_ret, 2)
                        returned_cash = max(0.0, round(close_alloc + part_pnl, 2))
                        ledger["cash"] = round(cte._safe_float(ledger.get("cash"), 0.0) + returned_cash, 2)

                        pos["allocated_capital"] = rem_alloc
                        pos["slot_weight"] = round(cte._safe_float(pos.get("slot_weight"), STARTER_WEIGHT) * (1.0 - t_frac), 3)
                        pos["partial_tp_done"] = True
                        pos["can_add"] = False
                        pos["be_stop_active"] = True
                        pos["partial_exit_price"] = round(cur_px, 2)
                        pos["partial_exit_time_et"] = cur_et.strftime("%Y-%m-%d %H:%M:%S")
                        pos["partial_closed_capital"] = round(cte._safe_float(pos.get("partial_closed_capital"), 0.0) + close_alloc, 2)
                        pos["partial_realized_pnl_usd"] = round(cte._safe_float(pos.get("partial_realized_pnl_usd"), 0.0) + part_pnl, 2)
                        if direction == "LONG":
                            pos["sl_price"] = max(sl_px, round(entry_px * (1.0 + BE_STOP_BUFFER), 2))
                        else:
                            pos["sl_price"] = min(sl_px, round(entry_px * (1.0 - BE_STOP_BUFFER), 2))
                        pos["best_price"] = round(cur_px, 2)
                        partial_done = True
                        be_active = True
                        alloc = rem_alloc
                        sl_px = pos["sl_price"]
                        partials_now.append(dict(pos))

                # (1e) 二阶抛物线卖强 (CLIMAX_TP) 与移动追踪止盈 (TRAIL_TP)
                if not exit_reason and partial_done:
                    climax_ret_min = CLIMAX_PROFIT_3X_PCT if lev3 else CLIMAX_PROFIT_1X_PCT
                    is_climax = (
                        ((direction == "LONG" and rsi_5m >= CLIMAX_RSI_HIGH) or (direction == "SHORT" and rsi_5m <= CLIMAX_RSI_LOW))
                        and rvol_5m >= CLIMAX_RVOL_MIN
                        and gross_ret_pct >= climax_ret_min
                    )
                    if is_climax:
                        exit_reason = "CLIMAX_TP"
                    else:
                        trail_dd_limit = TRAIL_DRAWDOWN_3X_PCT if lev3 else TRAIL_DRAWDOWN_1X_PCT
                        if direction == "LONG" and best_px > 0:
                            dd_from_best = ((best_px - cur_px) / best_px) * 100.0
                        elif direction == "SHORT" and best_px > 0:
                            dd_from_best = ((cur_px - best_px) / best_px) * 100.0
                        else:
                            dd_from_best = 0.0
                        if dd_from_best >= trail_dd_limit:
                            exit_reason = "TRAIL_TP"

                # (1f) T+2 不及预期早撤体检 + T+3 到期强平（已 BE26 保本/一阶落袋仓位不再早撤）
                if not exit_reason:
                    try:
                        entry_d = datetime.strptime(str(pos.get("entry_date"))[:10], "%Y-%m-%d").date()
                    except Exception:
                        entry_d = today_d
                    try:
                        exp_d = datetime.strptime(str(pos.get("expiry_date"))[:10], "%Y-%m-%d").date()
                    except Exception:
                        exp_d = today_d

                    if (today_d == exp_d and et_min >= t3_force_exit_min) or (today_d > exp_d):
                        exit_reason = "T3_EXPIRE"
                    elif (not partial_done) and (not be_active) and today_d > entry_d:
                        days_held = _count_trading_days_between(entry_d, today_d)
                        if days_held >= WEAK_EXIT_MIN_TRADING_DAYS:
                            entry_dt = _parse_et_timestamp(str(pos.get("entry_time_et") or ""), cur_et)
                            entry_min = (entry_dt.hour * 60 + entry_dt.minute) if entry_dt else 10 * 60
                            check_min = (
                                min(EARLY_CHECK_LATE_MIN, close_min - 15)
                                if (days_held == WEAK_EXIT_MIN_TRADING_DAYS and entry_min > LATE_ENTRY_CUTOFF_MIN)
                                else EARLY_CHECK_NORMAL_MIN
                            )
                            if et_min >= check_min:
                                wrong_side_vwap = (vwap_diff < 0.0) if direction == "LONG" else (vwap_diff > 0.0)
                                weak_stall = (gross_ret_pct < 0.8) and wrong_side_vwap
                                profit_fade = (mfe_pct >= 3.0) and (gross_ret_pct < 0.5)
                                if weak_stall or profit_fade:
                                    exit_reason = "WEAK_EARLY_EXIT"

            # (1e) 执行平仓结算（常规盘中 或 延展时段2轮确认止损）
            if exit_reason:
                runner_pnl = round(alloc * net_ret, 2)
                returned_cash = max(0.0, round(alloc + runner_pnl, 2))
                ledger["cash"] = round(cte._safe_float(ledger.get("cash"), 0.0) + returned_cash, 2)

                part_pnl = cte._safe_float(pos.get("partial_realized_pnl_usd"), 0.0)
                part_cap = cte._safe_float(pos.get("partial_closed_capital"), 0.0)
                tot_alloc = round(alloc + part_cap, 2)
                tot_pnl = round(part_pnl + runner_pnl, 2)
                tot_ret_pct = round((tot_pnl / tot_alloc) * 100.0, 2) if tot_alloc > 0 else round(ret_pct, 2)

                if exit_reason == "T3_EXPIRE":
                    outcome = "T3_WIN" if tot_pnl > 0 else "T3_LOSS"
                else:
                    outcome = exit_reason

                trade_rec = {
                    "trade_id": pos.get("trade_id") or f"KOL_{tk}_{direction}_{pos.get('entry_date', today_str)}",
                    "ticker": tk,
                    "direction": direction,
                    "setup": pos.get("setup"),
                    "entry_tag": pos.get("entry_tag", "NORMAL"),
                    "stage": pos.get("stage", 1),
                    "slot_weight": pos.get("peak_slot_weight") or pos.get("slot_weight", STARTER_WEIGHT),
                    "allocated_capital": tot_alloc,
                    "entry_date": pos.get("entry_date"),
                    "entry_time_et": pos.get("entry_time_et"),
                    "orig_entry_price": pos.get("orig_entry_price", round(entry_px, 2)),
                    "entry_price": round(entry_px, 2),
                    "sl_price": round(sl_px, 2),
                    "fast_be_trimmed": bool(pos.get("fast_be_trimmed", False)),
                    "partial_tp_done": partial_done,
                    "partial_exit_price": pos.get("partial_exit_price"),
                    "expiry_date": pos.get("expiry_date"),
                    "exit_date": today_str,
                    "exit_time_et": cur_et.strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_session": norm_session,
                    "ext_confirmed_close": ext_confirmed_close,
                    "exit_price": round(cur_px, 2),
                    "exit_reason": exit_reason,
                    "outcome": outcome,
                    "net_return_pct": tot_ret_pct,
                    "pnl_usd": tot_pnl,
                    "calibrated_prob_pct": pos.get("calibrated_prob_pct"),
                }
                ledger.setdefault("closed_trades", []).append(trade_rec)
                closed_now.append(trade_rec)
                ledger["last_close_time_et"] = cur_et.strftime("%Y-%m-%d %H:%M:%S")
                ledger.setdefault("last_exit_by_ticker", {})[tk] = {
                    "direction": direction,
                    "exit_date": today_str,
                    "exit_time_et": cur_et.strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_reason": exit_reason,
                }
                continue

            still_open.append(pos)

        ledger["open_positions"] = still_open

    # 先重算净值以便新开仓计算准确仓位额度
    _recalculate_kol_equity(ledger, live_quotes, cur_et, record_curve_point=False, is_extended=is_extended_session)

    # 2. 仅常规盘中 09:45 ~ entry_end_min (正常 15:20 / 半日市 12:20 ET，且严格 < close_min - 10) 开放盘中滚动狙击新开仓
    in_sniper_window = bool(is_regular_session and (ENTRY_START_MIN <= et_min <= entry_end_min))
    entries_today = int(ledger.get("entries_today_count") or 0)
    last_close_dt = _parse_et_timestamp(str(ledger.get("last_close_time_et") or ""), cur_et)
    slot_cd_ok = True
    if entries_today >= MAX_POSITIONS and last_close_dt is not None:
        slot_cd_ok = ((cur_et - last_close_dt).total_seconds() / 60.0) >= SLOT_COOLDOWN_MINUTES

    if in_sniper_window and entries_today < MAX_NEW_ENTRIES_PER_DAY and slot_cd_ok:
        free_cap_today = MAX_NEW_ENTRIES_PER_DAY - entries_today
        cands = screen_kol_sniper_candidates(
            ai_predictions=ai_predictions,
            live_quotes=live_quotes,
            open_positions=ledger.get("open_positions") or [],
            cur_et=cur_et,
            earnings_map=earnings_map,
            market_regime=market_regime,
            vix_regime=vix_regime,
            futures_data=futures_data,
            resilient_leaders=resilient_leaders,
            daily_sr_anchors=daily_sr_anchors,
            intraday_path_payload=intraday_path_payload,
            last_exit_by_ticker=ledger.get("last_exit_by_ticker"),
            max_slots=int(ledger.get("max_positions") or MAX_POSITIONS),
        )[:free_cap_today]

        expiry_str = cte.add_us_trading_days(today_d, HOLD_TRADING_DAYS).isoformat()
        for c in cands:
            cur_eq = cte._safe_float(ledger.get("total_equity"), INITIAL_CAPITAL)
            cur_cash = cte._safe_float(ledger.get("cash"), 0.0)
            wt = cte._safe_float(c.get("slot_weight"), STARTER_WEIGHT)
            alloc = round(min(cur_cash, cur_eq * wt), 2)
            if alloc < 10.0:
                continue
            ledger["cash"] = round(cur_cash - alloc, 2)
            tk = c["ticker"]

            pos_rec = {
                "trade_id": f"KOL_{tk}_{c['direction']}_{today_str}_{entries_today + 1}",
                "ticker": tk,
                "direction": c["direction"],
                "setup": c["setup"],
                "entry_tag": c["entry_tag"],
                "stage": 1,
                "can_add": bool(c.get("can_add", False)),
                "slot_weight": wt,
                "peak_slot_weight": wt,
                "allocated_capital": alloc,
                "entry_date": today_str,
                "entry_time_et": cur_et.strftime("%Y-%m-%d %H:%M:%S"),
                "orig_entry_price": c["entry_price"],
                "entry_price": c["entry_price"],
                "last_price": c["entry_price"],
                "best_price": c["entry_price"],
                "sl_price": c["sl_price"],
                "ext_sl_breach_count": 0,
                "ext_sl_first_breach_ts": "",
                "fast_be_trimmed": False,
                "partial_tp_done": False,
                "be_stop_active": False,
                "partial_closed_capital": 0.0,
                "partial_realized_pnl_usd": 0.0,
                "max_favorable_return_pct": round(-ROUND_TRIP_SLIPPAGE * 100.0, 2),
                "expiry_date": expiry_str,
                "remaining_trading_days": HOLD_TRADING_DAYS,
                "calibrated_prob_pct": c["calibrated_prob_pct"],
                "opp_prob_pct": c["opp_prob_pct"],
                "mirror_group": c["mirror_group"],
                "unrealized_return_pct": round(-ROUND_TRIP_SLIPPAGE * 100.0, 2),
                "unrealized_pnl_usd": round(-alloc * ROUND_TRIP_SLIPPAGE, 2),
            }
            ledger.setdefault("open_positions", []).append(pos_rec)
            opened_now.append(pos_rec)
            entries_today += 1

        ledger["entries_today_count"] = entries_today

    # 2b. V5.7 夜盘 OVERNIGHT_APAC_PILOT 10% 轻仓特批通道（独立于主槽位，连续 2 轮 >=60s 确认）
    if norm_session == "OVERNIGHT":
        ov_cand = screen_overnight_apac_pilot_candidate(
            ai_predictions=ai_predictions,
            live_quotes=live_quotes,
            open_positions=ledger.get("open_positions") or [],
            cur_et=cur_et,
            apac_chip_sentiment=apac_chip_sentiment,
            earnings_map=earnings_map,
            market_regime=market_regime,
            vix_regime=vix_regime,
            daily_sr_anchors=daily_sr_anchors,
        )
        pending = ledger.get("overnight_pending_confirm") if isinstance(ledger.get("overnight_pending_confirm"), dict) else {}
        if ov_cand is None:
            ledger["overnight_pending_confirm"] = {}
        else:
            cand_key = f"{ov_cand['ticker']}:{ov_cand['direction']}"
            prev_key = str(pending.get("key") or "")
            first_dt = _parse_et_timestamp(str(pending.get("first_ts_et") or ""), cur_et) if prev_key == cand_key else None
            if prev_key != cand_key or first_dt is None:
                ledger["overnight_pending_confirm"] = {
                    "key": cand_key,
                    "ticker": ov_cand["ticker"],
                    "direction": ov_cand["direction"],
                    "first_ts_et": cur_et.strftime("%Y-%m-%d %H:%M:%S"),
                    "count": 1,
                }
            else:
                elapsed_ov = (cur_et - first_dt).total_seconds()
                if elapsed_ov >= OVERNIGHT_PILOT_CONFIRM_SECONDS:
                    cur_eq = cte._safe_float(ledger.get("total_equity"), INITIAL_CAPITAL)
                    cur_cash = cte._safe_float(ledger.get("cash"), 0.0)
                    alloc = round(min(cur_cash, cur_eq * OVERNIGHT_PILOT_WEIGHT), 2)
                    if alloc >= 10.0:
                        ledger["cash"] = round(cur_cash - alloc, 2)
                        expiry_str = cte.add_us_trading_days(today_d, HOLD_TRADING_DAYS).isoformat()
                        tk_ov = ov_cand["ticker"]
                        ov_pos = {
                            "trade_id": f"KOL_OV_{tk_ov}_{ov_cand['direction']}_{today_str}",
                            "ticker": tk_ov,
                            "direction": ov_cand["direction"],
                            "setup": ov_cand["setup"],
                            "entry_tag": "OVERNIGHT_APAC_PILOT",
                            "entry_session": "OVERNIGHT",
                            "apac_chip_bias": ov_cand.get("apac_chip_bias"),
                            "apac_chip_score": ov_cand.get("apac_chip_score"),
                            "stage": 1,
                            "can_add": False,
                            "slot_weight": OVERNIGHT_PILOT_WEIGHT,
                            "peak_slot_weight": OVERNIGHT_PILOT_WEIGHT,
                            "allocated_capital": alloc,
                            "entry_date": today_str,
                            "entry_time_et": cur_et.strftime("%Y-%m-%d %H:%M:%S"),
                            "orig_entry_price": ov_cand["entry_price"],
                            "entry_price": ov_cand["entry_price"],
                            "last_price": ov_cand["entry_price"],
                            "best_price": ov_cand["entry_price"],
                            "sl_price": ov_cand["sl_price"],
                            "ext_sl_breach_count": 0,
                            "ext_sl_first_breach_ts": "",
                            "fast_be_trimmed": False,
                            "partial_tp_done": False,
                            "be_stop_active": False,
                            "partial_closed_capital": 0.0,
                            "partial_realized_pnl_usd": 0.0,
                            "max_favorable_return_pct": round(-ROUND_TRIP_SLIPPAGE * 100.0, 2),
                            "expiry_date": expiry_str,
                            "remaining_trading_days": HOLD_TRADING_DAYS,
                            "calibrated_prob_pct": ov_cand["calibrated_prob_pct"],
                            "opp_prob_pct": ov_cand["opp_prob_pct"],
                            "mirror_group": ov_cand["mirror_group"],
                            "unrealized_return_pct": round(-ROUND_TRIP_SLIPPAGE * 100.0, 2),
                            "unrealized_pnl_usd": round(-alloc * ROUND_TRIP_SLIPPAGE, 2),
                        }
                        ledger.setdefault("open_positions", []).append(ov_pos)
                        opened_now.append(ov_pos)
                        ledger["overnight_pending_confirm"] = {}
    else:
        ledger["overnight_pending_confirm"] = {}

    # 3. 最终净值、浮盈与曲线落表
    _recalculate_kol_equity(ledger, live_quotes, cur_et, record_curve_point=(is_regular_session or bool(closed_now) or bool(opened_now)), is_extended=is_extended_session)

    # 4. 仅非干跑模式下推送 KOL 事件提醒并原子落盘
    if not eff_dry_run:
        if alert_sender is not None:
            for tr in closed_now:
                dkey = f"KOL_CLOSE:{today_str}:{tr.get('trade_id')}:{tr.get('outcome')}"
                if dkey not in dedupe_set:
                    t_title, t_body = format_kol_sniper_event_alert("CLOSE", tr, ledger)
                    try:
                        alert_sender(t_title, t_body, dkey, 0x10B981 if tr.get("pnl_usd", 0) >= 0 else 0xEF4444)
                        dedupe_set.add(dkey)
                        alerts_sent.append({"type": "CLOSE", "ticker": tr["ticker"], "title": t_title})
                    except Exception:
                        pass
            for fb in fast_be_now:
                dkey = f"KOL_FASTBE:{today_str}:{fb.get('trade_id')}"
                if dkey not in dedupe_set:
                    t_title, t_body = format_kol_sniper_event_alert("FAST_BE", fb, ledger)
                    try:
                        alert_sender(t_title, t_body, dkey, 0x10B981)
                        dedupe_set.add(dkey)
                        alerts_sent.append({"type": "FAST_BE", "ticker": fb["ticker"], "title": t_title})
                    except Exception:
                        pass
            for pt in partials_now:
                dkey = f"KOL_PARTIAL:{today_str}:{pt.get('trade_id')}"
                if dkey not in dedupe_set:
                    t_title, t_body = format_kol_sniper_event_alert("PARTIAL_TP", pt, ledger)
                    try:
                        alert_sender(t_title, t_body, dkey, 0x10B981)
                        dedupe_set.add(dkey)
                        alerts_sent.append({"type": "PARTIAL_TP", "ticker": pt["ticker"], "title": t_title})
                    except Exception:
                        pass
            for ad in addons_now:
                dkey = f"KOL_ADDON:{today_str}:{ad.get('trade_id')}"
                if dkey not in dedupe_set:
                    t_title, t_body = format_kol_sniper_event_alert("ADDON", ad, ledger)
                    try:
                        alert_sender(t_title, t_body, dkey, 0xF59E0B)
                        dedupe_set.add(dkey)
                        alerts_sent.append({"type": "ADDON", "ticker": ad["ticker"], "title": t_title})
                    except Exception:
                        pass
            for op in opened_now:
                dkey = f"KOL_OPEN:{today_str}:{op.get('trade_id')}"
                if dkey not in dedupe_set:
                    t_title, t_body = format_kol_sniper_event_alert("OPEN", op, ledger)
                    try:
                        alert_sender(t_title, t_body, dkey, 0x38BDF8 if op["direction"] == "LONG" else 0xF59E0B)
                        dedupe_set.add(dkey)
                        alerts_sent.append({"type": "OPEN", "ticker": op["ticker"], "title": t_title})
                    except Exception:
                        pass

        ledger["alert_dedupe_keys"] = sorted(dedupe_set)[-250:]
        save_kol_ledger(ledger, ledger_path=ledger_path)

    return {
        "ledger": ledger,
        "opened_now": opened_now,
        "addons_now": addons_now,
        "fast_be_now": fast_be_now,
        "partials_now": partials_now,
        "closed_now": closed_now,
        "alerts_sent": alerts_sent,
        "dry_run": eff_dry_run,
        "persisted": not eff_dry_run,
    }


def build_dual_track_copy_embed_dict(
    steady_ledger: Optional[Dict[str, Any]] = None,
    kol_ledger: Optional[Dict[str, Any]] = None,
    steady_path: Optional[str] = None,
    kol_path: Optional[str] = None,
    now_et: Optional[datetime] = None,
) -> Dict[str, Any]:
    """生成 Discord `/copy` 双轨同台 PK 卡片（🛡️ V5.4 稳健轨 vs ⚡ V5.5 激进 KOL 狙击轨，严禁“建议股数”）。"""
    cur_et = cte._to_et_datetime(now_et)
    s_doc = steady_ledger if isinstance(steady_ledger, dict) else cte.load_copy_ledger(ledger_path=steady_path, now_et=cur_et)
    k_doc = kol_ledger if isinstance(kol_ledger, dict) else load_kol_ledger(ledger_path=kol_path, now_et=cur_et)

    def _fmt_wr(v: Any) -> str:
        return f"{float(v):.1f}%" if v is not None else "--"

    def _fmt_pf(v: Any) -> str:
        return f"{float(v):.2f}" if v is not None else "--"

    s_st = s_doc.get("stats") or cte._compute_stats(s_doc.get("closed_trades") or [])
    k_st = k_doc.get("stats") or _compute_kol_stats(k_doc.get("closed_trades") or [])

    s_eq = cte._safe_float(s_doc.get("total_equity"), INITIAL_CAPITAL)
    s_ret = cte._safe_float(s_doc.get("total_return_pct"), 0.0)
    s_dd = cte._safe_float(s_doc.get("max_drawdown_pct"), 0.0)
    s_open = s_doc.get("open_positions") or []

    k_eq = cte._safe_float(k_doc.get("total_equity"), INITIAL_CAPITAL)
    k_ret = cte._safe_float(k_doc.get("total_return_pct"), 0.0)
    k_dd = cte._safe_float(k_doc.get("max_drawdown_pct"), 0.0)
    k_open = k_doc.get("open_positions") or []
    k_closed = k_doc.get("closed_trades") or []
    s_closed = s_doc.get("closed_trades") or []

    pk_lines = [
        f"• **🛡️ V5.4 稳健轨 (4槽×25% · T+5)**：净值 **${s_eq:,.2f}** (`{s_ret:+.2f}%`) | 回撤 `-{s_dd:.2f}%` | 胜率 `{_fmt_wr(s_st.get('overall_win_rate_pct'))}` ({s_st.get('closed_count', 0)}笔) | PF `{_fmt_pf(s_st.get('profit_factor'))}` | 持仓 `{len(s_open)}/4`",
        f"• **⚡ V5.7 精简 KOL 轨 (2槽×50% · BE26 · T+3)**：净值 **${k_eq:,.2f}** (`{k_ret:+.2f}%`) | 回撤 `-{k_dd:.2f}%` | 胜率 `{_fmt_wr(k_st.get('overall_win_rate_pct'))}` ({k_st.get('closed_count', 0)}笔) | PF `{_fmt_pf(k_st.get('profit_factor'))}` | 持仓 `{len(k_open)}/2`",
        f"• **KOL 退出拆解**：追踪/卖强 `{k_st.get('tp_hit_count', 0)}` · 保本 `{k_st.get('be_stop_count', 0)}` · 早撤 `{k_st.get('weak_early_exit_count', 0)}` · 止损 `{k_st.get('sl_hit_count', 0)}` · 加仓 `{k_st.get('addon_trade_count', 0)}` · 夜盘特批 `{k_st.get('overnight_pilot_count', 0)}`",
    ]

    def _fmt_open_list(positions: List[Dict[str, Any]], is_kol: bool) -> List[str]:
        if not positions:
            return ["• 空仓观望中（等待过关结构信号）"]
        out = []
        for p in positions:
            d_ic = "🔴空" if str(p.get("direction")).upper() == "SHORT" else "🟢多"
            wt = int(round(cte._safe_float(p.get("slot_weight"), 0.25) * 100))
            ep = cte._safe_float(p.get("entry_price"), 0.0)
            lp = cte._safe_float(p.get("last_price"), ep)
            ur = cte._safe_float(p.get("unrealized_return_pct"), 0.0)
            sl = cte._safe_float(p.get("sl_price"), 0.0)
            if is_kol:
                if str(p.get("entry_tag") or "").upper() == "OVERNIGHT_APAC_PILOT":
                    stage_s = f"夜盘亚太特批{wt}%"
                elif p.get("partial_tp_done"):
                    stage_s = "50%尾仓追踪"
                elif p.get("fast_be_trimmed"):
                    stage_s = "BE26保本75%"
                elif int(p.get("stage") or 1) >= 2:
                    stage_s = "已加满50%"
                else:
                    stage_s = f"{wt}%底仓"
                out.append(f"• {d_ic} **{p.get('ticker')}** ({stage_s}) | `${ep:.2f}`→`${lp:.2f}` (`{ur:+.2f}%`) | 止损 `${sl:.2f}`")
            else:
                tp = cte._safe_float(p.get("tp_price"), 0.0)
                out.append(f"• {d_ic} **{p.get('ticker')}** ({wt}%) | `${ep:.2f}`→`${lp:.2f}` (`{ur:+.2f}%`) | 止盈 `${tp:.2f}`/止损 `${sl:.2f}`")
        return out

    hist_lines = []
    for tag, tr_list in (("⚡KOL", k_closed[-3:]), ("🛡️V5.4", s_closed[-3:])):
        for tr in reversed(tr_list):
            d_ic = "🔴空" if str(tr.get("direction")).upper() == "SHORT" else "🟢多"
            rp = cte._safe_float(tr.get("net_return_pct"), 0.0)
            pu = cte._safe_float(tr.get("pnl_usd"), 0.0)
            ps = "+" if pu >= 0 else "-"
            hist_lines.append(
                f"• `{tr.get('exit_date', '--')}` {tag} {d_ic} **{tr.get('ticker')}** `{tr.get('outcome')}` | `{rp:+.2f}%` ({ps}${abs(pu):.2f})"
            )
    if not hist_lines:
        hist_lines = ["• 暂无历史平仓记录（$1,000.00 双轨起跑线 · 等待首个闭环结算）"]

    best_ret = max(s_ret, k_ret)
    color = 0x10B981 if best_ret > 0 else (0xEF4444 if best_ret < 0 else 0x38BDF8)
    return {
        "title": f"📒 $1,000 双轨模拟跟单账本 PK（稳健 {len(s_open)}/4 · KOL {len(k_open)}/2）",
        "description": "双轨独立记账 · 单边 1.5 bps 滑点 · 与主实盘账本 100% 物理隔离（纯仓位比例口径）",
        "color": color,
        "fields": [
            {"name": "🏆 双轨净值与胜率对比", "value": "\n".join(pk_lines)[:1024], "inline": False},
            {"name": f"⚡ V5.7 精简 KOL 在途持仓 ({len(k_open)}/2)", "value": "\n".join(_fmt_open_list(k_open, True))[:1024], "inline": False},
            {"name": f"🛡️ V5.4 稳健基准在途持仓 ({len(s_open)}/4)", "value": "\n".join(_fmt_open_list(s_open, False))[:1024], "inline": False},
            {"name": "🧾 最近双轨已平仓记录", "value": "\n".join(hist_lines)[:1024], "inline": False},
        ],
        "footer": {"text": f"更新于 {cur_et.strftime('%Y-%m-%d %H:%M:%S')} ET · V5.4 稳健 vs V5.7 精简 KOL 双轨同台验证"},
    }


DUAL_COPY_DRAWER_EXTRA_CSS = """
.ct-pk-strip {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    margin-bottom: 10px;
}
.ct-pk-card {
    background: rgba(15, 23, 42, 0.72);
    border: 1px solid #263248;
    border-radius: 8px;
    padding: 8px 10px;
    min-width: 0;
}
.ct-pk-card.is-kol {
    border-color: rgba(245, 158, 11, 0.45);
    background: rgba(245, 158, 11, 0.06);
}
.ct-pk-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 4px;
    font-size: 11px;
    font-weight: 700;
    color: #94a3b8;
    margin-bottom: 3px;
}
.ct-pk-val {
    font-size: 14px;
    font-weight: 800;
    font-variant-numeric: tabular-nums;
}
.ct-pk-sub {
    font-size: 10.5px;
    color: #94a3b8;
    margin-top: 2px;
    font-variant-numeric: tabular-nums;
}
.ct-track-tabs {
    display: flex;
    gap: 6px;
    margin-bottom: 10px;
    flex-wrap: wrap;
}
.ct-track-tab {
    flex: 1 1 120px;
    padding: 6px 10px;
    border-radius: 7px;
    border: 1px solid #263248;
    background: rgba(15, 23, 42, 0.6);
    color: #94a3b8;
    font-size: 11.5px;
    font-weight: 700;
    cursor: pointer;
    text-align: center;
}
.ct-track-tab.is-active {
    background: rgba(56, 189, 248, 0.16);
    border-color: #38bdf8;
    color: #e6edf3;
}
.ct-track-tab[data-ct-tab="kol"].is-active {
    background: rgba(245, 158, 11, 0.16);
    border-color: #f59e0b;
    color: #fde68a;
}
.ct-track-panel { display: none; }
.ct-track-panel.is-active { display: block; }
:root[data-theme="light"] .ct-pk-card,
html[data-theme="light"] .ct-pk-card {
    background: #f8fafc;
    border-color: #e2e8f0;
}
:root[data-theme="light"] .ct-pk-card.is-kol,
html[data-theme="light"] .ct-pk-card.is-kol {
    background: #fffbeb;
    border-color: #fcd34d;
}
:root[data-theme="light"] .ct-track-tab,
html[data-theme="light"] .ct-track-tab {
    background: #f1f5f9;
    border-color: #cbd5e1;
    color: #475569;
}
:root[data-theme="light"] .ct-track-tab.is-active,
html[data-theme="light"] .ct-track-tab.is-active {
    background: #e0f2fe;
    border-color: #0284c7;
    color: #0c4a6e;
}
:root[data-theme="light"] .ct-track-tab[data-ct-tab="kol"].is-active,
html[data-theme="light"] .ct-track-tab[data-ct-tab="kol"].is-active {
    background: #fef3c7;
    border-color: #d97706;
    color: #78350f;
}
@media (max-width: 640px) {
    .ct-pk-strip { grid-template-columns: 1fr; }
}
"""

DUAL_COPY_DRAWER_CSS = cte.COPY_TRADE_DRAWER_CSS + DUAL_COPY_DRAWER_EXTRA_CSS


def _render_single_track_panel(doc: Dict[str, Any], is_kol: bool) -> str:
    st = doc.get("stats") or (_compute_kol_stats(doc.get("closed_trades") or []) if is_kol else cte._compute_stats(doc.get("closed_trades") or []))
    eq = cte._safe_float(doc.get("total_equity"), INITIAL_CAPITAL)
    tot_ret = cte._safe_float(doc.get("total_return_pct"), 0.0)
    cash = cte._safe_float(doc.get("cash"), INITIAL_CAPITAL)
    u_pnl = cte._safe_float(doc.get("unrealized_pnl"), 0.0)
    max_dd = cte._safe_float(doc.get("max_drawdown_pct"), 0.0)
    open_pos = doc.get("open_positions") or []
    closed_tr = doc.get("closed_trades") or []
    ret_cls = "ct-up" if tot_ret > 0 else ("ct-down" if tot_ret < 0 else "ct-neu")
    upnl_cls = "ct-up" if u_pnl > 0 else ("ct-down" if u_pnl < 0 else "ct-neu")

    def _wr(v: Any) -> str:
        return f"{float(v):.1f}%" if v is not None else "--"

    pf_str = f"{float(st['profit_factor']):.2f}" if st.get("profit_factor") is not None else "--"
    svg_html = cte._build_equity_curve_svg(doc)

    if open_pos:
        open_rows = []
        for p in open_pos:
            d_u = str(p.get("direction") or "LONG").upper()
            d_badge = '<span class="ct-pill-short">空 SHORT</span>' if d_u == "SHORT" else '<span class="ct-pill-long">多 LONG</span>'
            wt = int(round(cte._safe_float(p.get("slot_weight"), 0.25) * 100))
            ep = cte._safe_float(p.get("entry_price"), 0.0)
            lp = cte._safe_float(p.get("last_price"), ep)
            ur = cte._safe_float(p.get("unrealized_return_pct"), 0.0)
            sl = cte._safe_float(p.get("sl_price"), 0.0)
            ur_cls = "ct-up" if ur > 0 else ("ct-down" if ur < 0 else "ct-neu")
            if is_kol:
                st_txt = "半仓保本追踪" if p.get("partial_tp_done") else ("已加满50%" if int(p.get("stage") or 1) >= 2 else f"{wt}%底仓")
                extra_col = f"{_html.escape(st_txt)} · 止损 ${sl:.2f}"
            else:
                tp = cte._safe_float(p.get("tp_price"), 0.0)
                extra_col = f"${tp:.2f} / ${sl:.2f}"
            open_rows.append(
                f"<tr><td><b>{_html.escape(str(p.get('ticker', '')))}</b></td>"
                f"<td>{d_badge}</td><td>{wt}%</td>"
                f"<td>${ep:.2f}</td><td>${lp:.2f}</td>"
                f"<td class=\"{ur_cls}\">{ur:+.2f}%</td>"
                f"<td>{extra_col}</td></tr>"
            )
        open_tbody = "".join(open_rows)
    else:
        open_tbody = '<tr><td colspan="7" class="ct-empty">暂无在途持仓（主动空仓等待高确定性信号）</td></tr>'

    if closed_tr:
        closed_rows = []
        for tr in reversed(closed_tr[-15:]):
            d_u = str(tr.get("direction") or "LONG").upper()
            d_badge = '<span class="ct-pill-short">空</span>' if d_u == "SHORT" else '<span class="ct-pill-long">多</span>'
            rp = cte._safe_float(tr.get("net_return_pct"), 0.0)
            pu = cte._safe_float(tr.get("pnl_usd"), 0.0)
            rc = "ct-up" if pu > 0 else ("ct-down" if pu < 0 else "ct-neu")
            ps = "+" if pu >= 0 else "-"
            closed_rows.append(
                f"<tr><td>{_html.escape(str(tr.get('exit_date', '--')))}</td>"
                f"<td><b>{_html.escape(str(tr.get('ticker', '')))}</b> {d_badge}</td>"
                f"<td>{_html.escape(str(tr.get('outcome', '--')))}</td>"
                f"<td>${cte._safe_float(tr.get('entry_price'), 0):.2f}→${cte._safe_float(tr.get('exit_price'), 0):.2f}</td>"
                f"<td class=\"{rc}\">{rp:+.2f}% ({ps}${abs(pu):.2f})</td></tr>"
            )
        closed_tbody = "".join(closed_rows)
    else:
        closed_tbody = '<tr><td colspan="5" class="ct-empty">$1,000.00 起跑线 · 等待首个闭环结算</td></tr>'

    if is_kol:
        exit_note = (
            f"V5.7 精简版 · 个股55%/10% · 7大ETF 52%/6% · 禁空反向ETF · BE26快推保本 · 夜盘10%特批 · "
            f"追踪/卖强 {st.get('tp_hit_count', 0)} · 保本 {st.get('be_stop_count', 0)} · 早撤 {st.get('weak_early_exit_count', 0)} · 止损 {st.get('sl_hit_count', 0)}"
        )
        col_hdr = "状态 / 止损"
    else:
        exit_note = (
            f"4槽×25% · 09:35锁价 · T+5三阈值 · "
            f"TP {st.get('tp_hit_count', 0)} · T+5赚 {st.get('t5_win_count', 0)} · T+5亏 {st.get('t5_loss_count', 0)} · SL {st.get('sl_hit_count', 0)}"
        )
        col_hdr = "止盈 / 止损"

    return (
        f'<div class="ct-kpi-grid">'
        f'<div class="ct-kpi"><div class="ct-kpi-lbl">账户总净值</div><div class="ct-kpi-val {ret_cls}">${eq:,.2f} ({tot_ret:+.2f}%)</div></div>'
        f'<div class="ct-kpi"><div class="ct-kpi-lbl">现金 / 浮盈</div><div class="ct-kpi-val {upnl_cls}">${cash:,.2f} / {u_pnl:+.2f}</div></div>'
        f'<div class="ct-kpi"><div class="ct-kpi-lbl">最大回撤</div><div class="ct-kpi-val ct-down">-{max_dd:.2f}%</div></div>'
        f'<div class="ct-kpi"><div class="ct-kpi-lbl">总胜率 / PF</div><div class="ct-kpi-val">{_wr(st.get("overall_win_rate_pct"))} / {pf_str}</div></div>'
        f'<div class="ct-kpi"><div class="ct-kpi-lbl">多头 / 空头胜率</div><div class="ct-kpi-val">{_wr(st.get("long_win_rate_pct"))} / {_wr(st.get("short_win_rate_pct"))}</div></div>'
        f'</div>'
        f'<div class="ct-svg-wrap">{svg_html}</div>'
        f'<div class="ct-sec-title">📌 在途持仓 ({len(open_pos)}/{MAX_POSITIONS if is_kol else cte.MAX_POSITIONS}) · {_html.escape(exit_note)}</div>'
        f'<div class="ct-table-wrap"><table class="ct-table"><thead><tr><th>标的</th><th>方向</th><th>仓位</th><th>均价</th><th>现价</th><th>浮盈%</th><th>{col_hdr}</th></tr></thead><tbody>{open_tbody}</tbody></table></div>'
        f'<div class="ct-sec-title">🧾 历史已平仓 ({st.get("closed_count", 0)} 笔)</div>'
        f'<div class="ct-table-wrap"><table class="ct-table"><thead><tr><th>平仓日</th><th>标的</th><th>结果</th><th>均价→平仓</th><th>净收益</th></tr></thead><tbody>{closed_tbody}</tbody></table></div>'
    )


def render_dual_track_copy_drawer_html(
    steady_ledger: Optional[Dict[str, Any]] = None,
    kol_ledger: Optional[Dict[str, Any]] = None,
    steady_path: Optional[str] = None,
    kol_path: Optional[str] = None,
    compact: bool = False,
    drawer_id: str = "copyTradeLedgerDrawer",
) -> str:
    """渲染双轨同台对比可折叠抽屉（🛡️ V5.4 稳健轨 vs ⚡ V5.5 激进 KOL 狙击轨，严禁出现“建议股数”）。"""
    s_doc = steady_ledger if isinstance(steady_ledger, dict) else cte.load_copy_ledger(ledger_path=steady_path)
    k_doc = kol_ledger if isinstance(kol_ledger, dict) else load_kol_ledger(ledger_path=kol_path)

    s_eq = cte._safe_float(s_doc.get("total_equity"), INITIAL_CAPITAL)
    s_ret = cte._safe_float(s_doc.get("total_return_pct"), 0.0)
    s_dd = cte._safe_float(s_doc.get("max_drawdown_pct"), 0.0)
    s_st = s_doc.get("stats") or cte._compute_stats(s_doc.get("closed_trades") or [])
    s_open_n = len(s_doc.get("open_positions") or [])

    k_eq = cte._safe_float(k_doc.get("total_equity"), INITIAL_CAPITAL)
    k_ret = cte._safe_float(k_doc.get("total_return_pct"), 0.0)
    k_dd = cte._safe_float(k_doc.get("max_drawdown_pct"), 0.0)
    k_st = k_doc.get("stats") or _compute_kol_stats(k_doc.get("closed_trades") or [])
    k_open_n = len(k_doc.get("open_positions") or [])

    s_cls = "ct-up" if s_ret > 0 else ("ct-down" if s_ret < 0 else "ct-neu")
    k_cls = "ct-up" if k_ret > 0 else ("ct-down" if k_ret < 0 else "ct-neu")
    s_wr = f"{float(s_st['overall_win_rate_pct']):.1f}%" if s_st.get("overall_win_rate_pct") is not None else "--"
    k_wr = f"{float(k_st['overall_win_rate_pct']):.1f}%" if k_st.get("overall_win_rate_pct") is not None else "--"

    compact_cls = " ct-compact" if compact else ""
    safe_id = _html.escape(drawer_id)
    panel_kol = _render_single_track_panel(k_doc, is_kol=True)
    panel_steady = _render_single_track_panel(s_doc, is_kol=False)

    # 内联轻量化切页 onclick，零全局变量冲突，兼容多个抽屉 ID
    js_switch = (
        "(function(btn){"
        "var root=btn.closest('.ct-drawer');if(!root)return;"
        "var tab=btn.getAttribute('data-ct-tab');"
        "root.querySelectorAll('.ct-track-tab').forEach(function(b){b.classList.toggle('is-active',b===btn);});"
        "root.querySelectorAll('.ct-track-panel').forEach(function(p){p.classList.toggle('is-active',p.getAttribute('data-ct-panel')===tab);});"
        "})(this)"
    )

    return (
        f'<details id="{safe_id}" class="ct-drawer{compact_cls}">'
        f'<summary>'
        f'<span>📒 $1,000 双轨模拟跟单账本（稳健 vs KOL 狙击）</span>'
        f'<span class="ct-summary-meta">'
        f'⚡KOL <b class="{k_cls}">${k_eq:,.2f} ({k_ret:+.2f}%)</b> · 🛡️V5.4 <b class="{s_cls}">${s_eq:,.2f} ({s_ret:+.2f}%)</b> ▾'
        f'</span>'
        f'</summary>'
        f'<div class="ct-body">'
        f'<div class="ct-pk-strip">'
        f'<div class="ct-pk-card is-kol">'
        f'<div class="ct-pk-head"><span>⚡ V5.7 精简 KOL 狙击轨</span><span>持仓 {k_open_n}/2</span></div>'
        f'<div class="ct-pk-val {k_cls}">${k_eq:,.2f} ({k_ret:+.2f}%)</div>'
        f'<div class="ct-pk-sub">最大回撤 -{k_dd:.2f}% · 胜率 {k_wr} ({k_st.get("closed_count", 0)}笔)</div>'
        f'</div>'
        f'<div class="ct-pk-card">'
        f'<div class="ct-pk-head"><span>🛡️ V5.4 稳健基准轨</span><span>持仓 {s_open_n}/4</span></div>'
        f'<div class="ct-pk-val {s_cls}">${s_eq:,.2f} ({s_ret:+.2f}%)</div>'
        f'<div class="ct-pk-sub">最大回撤 -{s_dd:.2f}% · 胜率 {s_wr} ({s_st.get("closed_count", 0)}笔)</div>'
        f'</div>'
        f'</div>'
        f'<div class="ct-track-tabs">'
        f'<button type="button" class="ct-track-tab is-active" data-ct-tab="kol" onclick="{js_switch}">⚡ V5.7 精简 KOL 轨明细</button>'
        f'<button type="button" class="ct-track-tab" data-ct-tab="steady" onclick="{js_switch}">🛡️ V5.4 稳健基准轨明细</button>'
        f'</div>'
        f'<div class="ct-track-panel is-active" data-ct-panel="kol">{panel_kol}</div>'
        f'<div class="ct-track-panel" data-ct-panel="steady">{panel_steady}</div>'
        f'</div></details>'
    )
