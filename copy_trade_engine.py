# -*- coding: utf-8 -*-
"""
V5.4 独立模块：$1,000 多空双轨 KOL 模拟跟单账本引擎 (copy_trade_engine.py)

核心保障：
1. 与主实盘账本 data_cache/signal_ledger.json 100% 物理隔离，独立持久化至 data_cache/copy_trade_ledger.json。
2. 精简 KOL 仓位口径：初始 $1,000.00，最多同时持仓 4 笔，每笔固定 25% 仓位占比，严禁输出任何“建议股数”。
3. 100% 同源对齐 live_prediction_scorecard.json 门禁与 T+5 三阈值闭环：
   - 做多 LONG：非 BEAR_DEFENSE 封顶拦截（或恐慌出清/避险豁免）、校准多胜率 >= 55% 且多空差 >= 10%、7日无财报、无熔断；
   - 做空 SHORT：可融券标的、校准空胜率 >= 55% 且空多差 >= 10%、7日无财报、不对暴涨极端反向 ETF 重复追空；
   - 同标的防重 + 杠杆镜像组 ({SOXX,SOXL,SOXS}, {QQQ,QQQM,TQQQ,SQQQ}, {KOSPI,KORU}) 互斥去重；
   - 防开盘极端追单：相对前收同向跳空 > 4% 跳过；
   - 开/平仓各扣 1.5 bps (0.015%) 单边滑点（往返 3 bps = 0.03%）；
   - 盘中实时触发 TP_HIT / SL_HIT，第 5 个美股交易日 >= 15:50 ET 强平 T5_EXPIRE (拆分 T5_WIN / T5_LOSS)。
4. 干跑零污染保护：RADAR_DISABLE_EXTERNAL_ALERTS=1 或 dry_run=True 时严禁落盘修改生产账本且绝不外发消息。
"""

import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
    _NY_TZ = ZoneInfo("America/New_York")
except Exception:
    _NY_TZ = timezone(timedelta(hours=-5))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_COPY_LEDGER_PATH = os.path.join(BASE_DIR, "data_cache", "copy_trade_ledger.json")
DEFAULT_AI_PREDICTIONS_PATH = os.path.join(BASE_DIR, "data_cache", "latest_ai_predictions.json")

INITIAL_CAPITAL = 1000.00
MAX_POSITIONS = 4
SLOT_WEIGHT = 0.25
ONE_WAY_SLIPPAGE = 0.00015  # 1.5 bps
ROUND_TRIP_SLIPPAGE = 0.0003  # 3.0 bps (0.03%)
MIN_PROB_THRESHOLD = 55.0
MIN_SPREAD_THRESHOLD = 10.0
MAX_SAME_DIR_GAP_PCT = 4.0
HOLD_TRADING_DAYS = 5

MIRROR_GROUPS: Tuple[Tuple[str, frozenset], ...] = (
    ("SEMI_MIRROR", frozenset({"SOXX", "SOXL", "SOXS"})),
    ("NDX_MIRROR", frozenset({"QQQ", "QQQM", "TQQQ", "SQQQ"})),
    ("KRX_MIRROR", frozenset({"KOSPI", "KORU"})),
)

SAFE_HAVEN_LONG_EXEMPT = frozenset({"GLD", "TLT", "XAUUSD", "SOXS", "SQQQ", "VIXY"})
INVERSE_ETFS = frozenset({"SOXS", "SQQQ", "VIXY"})
NON_SHORTABLE_TICKERS = frozenset({"KOSPI", "BTC-USD", "XAUUSD"})
NON_TRADABLE_DIRECT_TICKERS = frozenset({"KOSPI"})


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or isinstance(val, bool):
            return default
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default


def _to_et_datetime(now_et: Optional[datetime] = None) -> datetime:
    if now_et is None:
        return datetime.now(_NY_TZ)
    if now_et.tzinfo is None:
        try:
            return now_et.replace(tzinfo=_NY_TZ)
        except Exception:
            return now_et
    return now_et.astimezone(_NY_TZ)


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm for Easter Sunday (to compute Good Friday)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed_date(d: date) -> date:
    if d.weekday() == 5:  # Saturday -> Friday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday -> Monday
        return d + timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1) - timedelta(days=1)
    offset = (nxt.weekday() - weekday) % 7
    return nxt - timedelta(days=offset)


def get_us_market_holidays(year: int) -> frozenset:
    """返回指定年份美股 NYSE 常规休市日集合。"""
    hols = {
        _observed_date(date(year, 1, 1)),          # New Year's Day
        _nth_weekday(year, 1, 0, 3),               # MLK Day (3rd Mon Jan)
        _nth_weekday(year, 2, 0, 3),               # Presidents' Day (3rd Mon Feb)
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),                 # Memorial Day (last Mon May)
        _observed_date(date(year, 6, 19)),         # Juneteenth
        _observed_date(date(year, 7, 4)),          # Independence Day
        _nth_weekday(year, 9, 0, 1),               # Labor Day (1st Mon Sep)
        _nth_weekday(year, 11, 3, 4),              # Thanksgiving (4th Thu Nov)
        _observed_date(date(year, 12, 25)),        # Christmas Day
    }
    # If Jan 1 of next year is Saturday, observed on Dec 31 of this year
    if date(year + 1, 1, 1).weekday() == 5:
        hols.add(date(year, 12, 31))
    return frozenset(hols)


def is_us_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in get_us_market_holidays(d.year)


def add_us_trading_days(start_date: date, trading_days: int = HOLD_TRADING_DAYS) -> date:
    """严格跳过周末与美股休市日，返回第 N 个美股交易日。"""
    cur = start_date
    added = 0
    steps = max(1, int(trading_days))
    while added < steps:
        cur += timedelta(days=1)
        if is_us_trading_day(cur):
            added += 1
    return cur


def count_remaining_trading_days(from_date: date, expiry_date: date) -> int:
    if from_date >= expiry_date:
        return 0
    cur = from_date
    cnt = 0
    while cur < expiry_date:
        cur += timedelta(days=1)
        if is_us_trading_day(cur):
            cnt += 1
    return cnt


def get_mirror_group(ticker: str) -> Optional[str]:
    tk = str(ticker or "").strip().upper()
    for gname, members in MIRROR_GROUPS:
        if tk in members:
            return gname
    return None


def is_external_alerts_disabled() -> bool:
    return str(os.environ.get("RADAR_DISABLE_EXTERNAL_ALERTS", "")).strip().lower() in ("1", "true", "yes", "on")


def create_empty_copy_ledger(initial_capital: float = INITIAL_CAPITAL, now_et: Optional[datetime] = None) -> Dict[str, Any]:
    cur_et = _to_et_datetime(now_et)
    cap = round(float(initial_capital), 2)
    return {
        "schema_version": "v5.4-copy-trade-ledger",
        "initial_capital": cap,
        "max_positions": MAX_POSITIONS,
        "slot_weight": SLOT_WEIGHT,
        "one_way_slippage_bps": 1.5,
        "cash": cap,
        "unrealized_pnl": 0.0,
        "total_equity": cap,
        "total_return_pct": 0.0,
        "peak_equity": cap,
        "max_drawdown_pct": 0.0,
        "last_entry_date_et": "",
        "updated_at_et": cur_et.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "open_positions": [],
        "closed_trades": [],
        "equity_curve": [],
        "stats": _compute_stats([]),
        "alert_dedupe_keys": [],
    }


def load_copy_ledger(ledger_path: Optional[str] = None, now_et: Optional[datetime] = None) -> Dict[str, Any]:
    target = os.path.abspath(ledger_path or DEFAULT_COPY_LEDGER_PATH)
    if os.path.exists(target):
        try:
            with open(target, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if isinstance(doc, dict) and "initial_capital" in doc:
                doc.setdefault("open_positions", [])
                doc.setdefault("closed_trades", [])
                doc.setdefault("equity_curve", [])
                doc.setdefault("alert_dedupe_keys", [])
                doc.setdefault("max_positions", MAX_POSITIONS)
                doc.setdefault("slot_weight", SLOT_WEIGHT)
                doc.setdefault("stats", _compute_stats(doc.get("closed_trades") or []))
                return doc
        except Exception:
            pass
    return create_empty_copy_ledger(now_et=now_et)


def save_copy_ledger(doc: Dict[str, Any], ledger_path: Optional[str] = None) -> str:
    target = os.path.abspath(ledger_path or DEFAULT_COPY_LEDGER_PATH)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{target}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
        os.replace(tmp, target)
        return target
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        raise


def _compute_stats(closed_trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    tp_cnt = 0
    sl_cnt = 0
    t5_win_cnt = 0
    t5_loss_cnt = 0
    long_total = 0
    long_wins = 0
    short_total = 0
    short_wins = 0
    gross_profit = 0.0
    gross_loss = 0.0

    for tr in closed_trades or []:
        if not isinstance(tr, dict):
            continue
        outcome = str(tr.get("outcome") or "").upper()
        direction = str(tr.get("direction") or "LONG").upper()
        pnl = _safe_float(tr.get("pnl_usd"), 0.0)
        is_win = False
        if outcome == "TP_HIT":
            tp_cnt += 1
            is_win = True
        elif outcome == "SL_HIT":
            sl_cnt += 1
            is_win = False
        elif outcome == "T5_WIN" or (outcome == "T5_EXPIRE" and pnl > 0):
            t5_win_cnt += 1
            is_win = True
        else:
            t5_loss_cnt += 1
            is_win = False

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

    closed_cnt = tp_cnt + sl_cnt + t5_win_cnt + t5_loss_cnt
    win_cnt = tp_cnt + t5_win_cnt
    loss_cnt = sl_cnt + t5_loss_cnt
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
        "t5_win_count": t5_win_cnt,
        "t5_loss_count": t5_loss_cnt,
        "sl_hit_count": sl_cnt,
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


def _has_earnings_within_7d(earnings_info: Optional[Dict[str, Any]], today_d: date) -> bool:
    if not isinstance(earnings_info, dict) or not earnings_info:
        return False
    if bool(earnings_info.get("within_7d")) or bool(earnings_info.get("is_earnings_blackout")):
        return True
    dte = earnings_info.get("days_to_earnings")
    if dte is not None:
        try:
            d_i = int(float(dte))
            if 0 <= d_i <= 7:
                return True
        except Exception:
            pass
    dt_str = str(earnings_info.get("next_earnings_date") or earnings_info.get("earnings_date") or "").strip()
    if len(dt_str) >= 10:
        try:
            ed = datetime.strptime(dt_str[:10], "%Y-%m-%d").date()
            diff = (ed - today_d).days
            if 0 <= diff <= 7:
                return True
        except Exception:
            pass
    return False


def _calc_net_return(direction: str, entry_price: float, exit_price: float) -> float:
    if entry_price <= 0 or exit_price <= 0:
        return 0.0
    if str(direction).upper() == "SHORT":
        return ((entry_price - exit_price) / entry_price) - ROUND_TRIP_SLIPPAGE
    return ((exit_price - entry_price) / entry_price) - ROUND_TRIP_SLIPPAGE


def _resolve_tp_sl_for_entry(
    direction: str,
    entry_price: float,
    ai_item: Dict[str, Any],
) -> Tuple[float, float]:
    """按 latest_ai_predictions.json 的三阈值参数锁死止盈/止损价，确保单调不变量。"""
    atr_pct = max(1.5, min(15.0, _safe_float(ai_item.get("atr_pct"), 3.5)))
    dir_u = str(direction).upper()

    if dir_u == "SHORT":
        raw_tp = _safe_float(ai_item.get("short_target_profit_price"), 0.0)
        raw_sl = _safe_float(ai_item.get("short_stop_loss_price"), 0.0)
        tp_pct = _safe_float(ai_item.get("short_target_profit_pct"), 0.0)
        sl_pct = _safe_float(ai_item.get("short_stop_loss_pct"), 0.0)
        if tp_pct <= 0.5:
            tp_pct = max(3.0, min(12.0, 1.35 * atr_pct))
        if sl_pct <= 0.5:
            sl_pct = max(2.2, min(8.5, 1.00 * atr_pct))

        tp = round(raw_tp, 2) if (0 < raw_tp < entry_price * 0.992) else round(entry_price * (1.0 - tp_pct / 100.0), 2)
        sl = round(raw_sl, 2) if (raw_sl > entry_price * 1.008) else round(entry_price * (1.0 + sl_pct / 100.0), 2)
        # 严格单调: 0 < tp < entry_price < sl
        if tp >= entry_price:
            tp = round(entry_price * (1.0 - max(0.03, tp_pct / 100.0)), 2)
        if sl <= entry_price:
            sl = round(entry_price * (1.0 + max(0.022, sl_pct / 100.0)), 2)
        return max(0.01, tp), max(round(entry_price + 0.01, 2), sl)

    # LONG
    raw_tp = _safe_float(ai_item.get("target_profit_price"), 0.0)
    raw_sl = _safe_float(ai_item.get("stop_loss_price"), 0.0)
    tp_pct = _safe_float(ai_item.get("target_profit_pct"), 0.0)
    sl_pct = _safe_float(ai_item.get("stop_loss_pct"), 0.0)
    if tp_pct <= 0.5:
        tp_pct = max(3.0, min(12.0, 1.35 * atr_pct))
    if sl_pct <= 0.5:
        sl_pct = max(2.2, min(8.5, 1.00 * atr_pct))

    tp = round(raw_tp, 2) if (raw_tp > entry_price * 1.008) else round(entry_price * (1.0 + tp_pct / 100.0), 2)
    sl = round(raw_sl, 2) if (0 < raw_sl < entry_price * 0.992) else round(entry_price * (1.0 - sl_pct / 100.0), 2)
    # 严格单调: 0 < sl < entry_price < tp
    if tp <= entry_price:
        tp = round(entry_price * (1.0 + max(0.03, tp_pct / 100.0)), 2)
    if sl >= entry_price:
        sl = round(entry_price * (1.0 - max(0.022, sl_pct / 100.0)), 2)
    return max(round(entry_price + 0.01, 2), tp), max(0.01, sl)


def screen_copy_trade_candidates(
    ai_predictions: Dict[str, Any],
    live_quotes: Dict[str, Dict[str, Any]],
    open_positions: List[Dict[str, Any]],
    today_date: date,
    earnings_map: Optional[Dict[str, Dict[str, Any]]] = None,
    market_regime: str = "",
    vix_regime: Optional[Dict[str, Any]] = None,
    max_slots: int = MAX_POSITIONS,
) -> List[Dict[str, Any]]:
    """
    严格对齐 live_prediction_scorecard.json 门禁筛选多空双轨模拟跟单候选：
    - 最多填满剩余空槽位（至多 4 笔）
    - 同标的防重 + 杠杆镜像组互斥去重
    - 相对前收同向跳空 > 4% 跳过防追单
    """
    free_slots = max(0, int(max_slots) - len(open_positions))
    if free_slots <= 0:
        return []

    by_ticker = ai_predictions.get("by_ticker") if isinstance(ai_predictions, dict) else None
    if not isinstance(by_ticker, dict) or not by_ticker:
        return []

    regime_u = str(market_regime or ai_predictions.get("market_regime") or "NEUTRAL_CHOP").strip().upper()
    vr = vix_regime if isinstance(vix_regime, dict) else (ai_predictions.get("vix_regime") if isinstance(ai_predictions.get("vix_regime"), dict) else {})
    vix_hard_brake = bool(vr.get("vix_hard_brake", False)) or (str(vr.get("vix_state") or "").upper() == "BACKWARDATION_BRAKE")
    panic_clearing = bool(vr.get("panic_clearing_active", False)) or (str(vr.get("vix_state") or "").upper() == "PANIC_CLEARING")
    long_regime_blocked = bool((regime_u == "BEAR_DEFENSE" or vix_hard_brake) and (not panic_clearing))

    held_tickers = {str(p.get("ticker") or "").strip().upper() for p in open_positions}
    held_groups = {get_mirror_group(tk) for tk in held_tickers if get_mirror_group(tk)}

    raw_cands: List[Dict[str, Any]] = []
    for tk_raw, ai_item in by_ticker.items():
        if not isinstance(ai_item, dict):
            continue
        tk = str(tk_raw or ai_item.get("ticker") or "").strip().upper()
        if not tk or tk in NON_TRADABLE_DIRECT_TICKERS or tk in held_tickers:
            continue
        mgrp = get_mirror_group(tk)
        if mgrp and mgrp in held_groups:
            continue

        lv = (live_quotes or {}).get(tk) if isinstance(live_quotes, dict) else None
        if not isinstance(lv, dict):
            continue
        active_px = _safe_float(lv.get("active_price") or lv.get("last"), 0.0)
        if active_px <= 0:
            continue
        prev_c = _safe_float(lv.get("prev_close"), 0.0)
        pct_chg = _safe_float(lv.get("pct_change"), 0.0)
        if prev_c <= 0 and abs(pct_chg) < 95.0:
            prev_c = active_px / (1.0 + pct_chg / 100.0) if (1.0 + pct_chg / 100.0) > 0 else 0.0
        gap_pct = ((active_px - prev_c) / prev_c) * 100.0 if prev_c > 0 else pct_chg

        earn_info = (earnings_map or {}).get(tk) if isinstance(earnings_map, dict) else None
        if _has_earnings_within_7d(earn_info, today_date):
            continue

        p_long = _safe_float(ai_item.get("calibrated_win_rate_pct"), 0.0)
        p_short = _safe_float(ai_item.get("calibrated_short_win_rate_pct"), 0.0)
        is_crash = bool(ai_item.get("is_crash_halt", False) or lv.get("is_crash_halt", False))
        is_cooling = bool(ai_item.get("is_cooling_halt", False) or lv.get("is_cooling_halt", False))
        is_tp_ext = bool(lv.get("is_take_profit", False))
        rsi_val = _safe_float(lv.get("rsi") or ai_item.get("rsi_14"), 50.0)

        # 1) LONG 通道检查
        long_allowed = (not long_regime_blocked) or (tk in SAFE_HAVEN_LONG_EXEMPT)
        if (
            long_allowed
            and (not is_crash)
            and (not is_cooling)
            and p_long >= MIN_PROB_THRESHOLD
            and (p_long - p_short) >= MIN_SPREAD_THRESHOLD
            and gap_pct <= MAX_SAME_DIR_GAP_PCT
        ):
            tp_px, sl_px = _resolve_tp_sl_for_entry("LONG", active_px, ai_item)
            raw_cands.append({
                "ticker": tk,
                "direction": "LONG",
                "calibrated_prob_pct": round(p_long, 1),
                "opp_prob_pct": round(p_short, 1),
                "prob_spread_pct": round(p_long - p_short, 1),
                "entry_price": round(active_px, 2),
                "tp_price": tp_px,
                "sl_price": sl_px,
                "gap_pct": round(gap_pct, 2),
                "mirror_group": mgrp,
            })

        # 2) SHORT 通道检查
        inv_extreme_surge = bool(tk in INVERSE_ETFS and (gap_pct >= 8.0 or pct_chg >= 8.0 or is_tp_ext or rsi_val >= 68.0))
        if (
            tk not in NON_SHORTABLE_TICKERS
            and (not inv_extreme_surge)
            and p_short >= MIN_PROB_THRESHOLD
            and (p_short - p_long) >= MIN_SPREAD_THRESHOLD
            and (-gap_pct) <= MAX_SAME_DIR_GAP_PCT
        ):
            tp_px, sl_px = _resolve_tp_sl_for_entry("SHORT", active_px, ai_item)
            raw_cands.append({
                "ticker": tk,
                "direction": "SHORT",
                "calibrated_prob_pct": round(p_short, 1),
                "opp_prob_pct": round(p_long, 1),
                "prob_spread_pct": round(p_short - p_long, 1),
                "entry_price": round(active_px, 2),
                "tp_price": tp_px,
                "sl_price": sl_px,
                "gap_pct": round(gap_pct, 2),
                "mirror_group": mgrp,
            })

    # 按校准胜率降序、多空差降序、代码升序挑选，同时做同标的与镜像组互斥去重
    raw_cands.sort(key=lambda c: (-c["calibrated_prob_pct"], -c["prob_spread_pct"], c["ticker"]))
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


def format_kol_open_alert(pos: Dict[str, Any]) -> Tuple[str, str]:
    """生成 KOL 风格开仓提醒 (严禁包含“建议股数”)。"""
    tk = str(pos.get("ticker") or "")
    direction = str(pos.get("direction") or "LONG").upper()
    dir_icon = "🔴 做空" if direction == "SHORT" else "🟢 做多"
    entry = _safe_float(pos.get("entry_price"), 0.0)
    tp = _safe_float(pos.get("tp_price"), 0.0)
    sl = _safe_float(pos.get("sl_price"), 0.0)
    weight_pct = int(round(_safe_float(pos.get("slot_weight"), SLOT_WEIGHT) * 100))
    tp_pct = ((tp - entry) / entry) * 100.0 if entry > 0 else 0.0
    sl_pct = ((sl - entry) / entry) * 100.0 if entry > 0 else 0.0
    exp_d = str(pos.get("expiry_date") or "--")
    prob = _safe_float(pos.get("calibrated_prob_pct"), 0.0)

    line = (
        f"📋 [模拟跟单·开仓] {dir_icon} {tk} ({weight_pct}%仓位) | "
        f"入场 ${entry:.2f} | 止盈 ${tp:.2f} ({tp_pct:+.1f}%) | "
        f"止损 ${sl:.2f} ({sl_pct:+.1f}%) | 第5交易日强平: {exp_d}"
    )
    title = f"📋 [模拟跟单·开仓] {dir_icon} {tk} ({weight_pct}%仓位)"
    body = f"{line}\n• AI 校准胜率：**{prob:.1f}%** · 滑点口径：单边 1.5 bps（$1,000 模拟账本独立记账）"
    return title, body


def format_kol_close_alert(trade: Dict[str, Any], ledger: Dict[str, Any]) -> Tuple[str, str]:
    """生成 KOL 风格平仓结算提醒 (严禁包含“建议股数”)。"""
    tk = str(trade.get("ticker") or "")
    direction = str(trade.get("direction") or "LONG").upper()
    dir_icon = "🔴 做空" if direction == "SHORT" else "🟢 做多"
    outcome = str(trade.get("outcome") or "T5_LOSS").upper()
    if outcome == "TP_HIT":
        tag = "✅ [模拟跟单·止盈平仓]"
    elif outcome == "SL_HIT":
        tag = "🛑 [模拟跟单·止损平仓]"
    elif outcome == "T5_WIN":
        tag = "⏱️ [模拟跟单·T+5到期获利]"
    else:
        tag = "⏱️ [模拟跟单·T+5到期强平]"

    ret_pct = _safe_float(trade.get("net_return_pct"), 0.0)
    pnl_usd = _safe_float(trade.get("pnl_usd"), 0.0)
    eq = _safe_float(ledger.get("total_equity"), INITIAL_CAPITAL)
    tot_ret = _safe_float(ledger.get("total_return_pct"), 0.0)
    entry = _safe_float(trade.get("entry_price"), 0.0)
    exit_px = _safe_float(trade.get("exit_price"), 0.0)

    pnl_sign = "+" if pnl_usd >= 0 else "-"
    line = (
        f"{tag} {dir_icon} {tk} | {ret_pct:+.2f}% ({pnl_sign}${abs(pnl_usd):.2f}) | "
        f"模拟账户净值: ${eq:,.2f} ({tot_ret:+.2f}%)"
    )
    title = f"{tag} {dir_icon} {tk} ({ret_pct:+.2f}%)"
    body = f"{line}\n• 成交回溯：入场 ${entry:.2f} ➔ 平仓 ${exit_px:.2f}（已扣往返 3 bps 滑点）"
    return title, body


def _recalculate_account_equity(
    ledger: Dict[str, Any],
    live_quotes: Dict[str, Dict[str, Any]],
    cur_et: datetime,
    record_curve_point: bool = True,
) -> Dict[str, Any]:
    init_cap = _safe_float(ledger.get("initial_capital"), INITIAL_CAPITAL)
    cash = round(_safe_float(ledger.get("cash"), init_cap), 2)
    today_d = cur_et.date()
    today_str = today_d.isoformat()

    unrealized_total = 0.0
    deployed_value = 0.0
    for pos in ledger.get("open_positions") or []:
        tk = str(pos.get("ticker") or "").upper()
        entry_px = _safe_float(pos.get("entry_price"), 0.0)
        alloc = _safe_float(pos.get("allocated_capital"), 0.0)
        lv = (live_quotes or {}).get(tk) if isinstance(live_quotes, dict) else None
        cur_px = _safe_float((lv or {}).get("active_price") or (lv or {}).get("last"), 0.0)
        if cur_px <= 0:
            cur_px = _safe_float(pos.get("last_price"), entry_px)
        net_ret = _calc_net_return(str(pos.get("direction") or "LONG"), entry_px, cur_px)
        u_pnl = round(alloc * net_ret, 2)
        pos["last_price"] = round(cur_px, 2)
        pos["unrealized_return_pct"] = round(net_ret * 100.0, 2)
        pos["unrealized_pnl_usd"] = u_pnl
        try:
            exp_d = datetime.strptime(str(pos.get("expiry_date"))[:10], "%Y-%m-%d").date()
            pos["remaining_trading_days"] = count_remaining_trading_days(today_d, exp_d)
        except Exception:
            pos["remaining_trading_days"] = 0
        unrealized_total += u_pnl
        deployed_value += max(0.0, alloc + u_pnl)

    total_equity = round(cash + deployed_value, 2)
    total_ret_pct = round(((total_equity - init_cap) / init_cap) * 100.0, 2) if init_cap > 0 else 0.0
    prev_peak = max(init_cap, _safe_float(ledger.get("peak_equity"), init_cap))
    peak_equity = round(max(prev_peak, total_equity), 2)
    cur_dd = ((peak_equity - total_equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0
    max_dd = round(max(_safe_float(ledger.get("max_drawdown_pct"), 0.0), cur_dd), 2)

    ledger["cash"] = cash
    ledger["unrealized_pnl"] = round(unrealized_total, 2)
    ledger["total_equity"] = total_equity
    ledger["total_return_pct"] = total_ret_pct
    ledger["peak_equity"] = peak_equity
    ledger["max_drawdown_pct"] = max_dd
    ledger["updated_at_et"] = cur_et.strftime("%Y-%m-%d %H:%M:%S")
    ledger["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    ledger["stats"] = _compute_stats(ledger.get("closed_trades") or [])

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


def run_copy_trade_cycle(
    ai_predictions: Dict[str, Any],
    live_quotes: Dict[str, Dict[str, Any]],
    earnings_map: Optional[Dict[str, Dict[str, Any]]] = None,
    market_regime: str = "",
    vix_regime: Optional[Dict[str, Any]] = None,
    session_code: str = "REGULAR",
    now_et: Optional[datetime] = None,
    ledger_path: Optional[str] = None,
    dry_run: Optional[bool] = None,
    alert_sender: Optional[Callable[[str, str, str, int], Any]] = None,
) -> Dict[str, Any]:
    """
    执行单轮 $1,000 多空双轨模拟跟单状态机：
    1. 常规盘中优先检查所有 OPEN 持仓的 TP_HIT / SL_HIT / T5_EXPIRE 平仓结算；
    2. 美东 >= 09:35 ET 且 < 15:50 ET 且当日尚未完成开仓锁定时，筛选新信号开仓至满 4 槽；
    3. 实时重算净值、浮盈、最大回撤与去水分统计；
    4. 严格执行干跑保护：dry_run=True 或 RADAR_DISABLE_EXTERNAL_ALERTS=1 时绝不落盘、绝不外发。
    """
    cur_et = _to_et_datetime(now_et)
    today_d = cur_et.date()
    today_str = today_d.isoformat()
    et_min = cur_et.hour * 60 + cur_et.minute
    sess_u = str(session_code or "").strip().upper()
    is_regular_session = (sess_u in ("REGULAR", "RTH", "INTRADAY") or (is_us_trading_day(today_d) and 9 * 60 + 30 <= et_min < 16 * 60))

    eff_dry_run = bool(dry_run) if dry_run is not None else is_external_alerts_disabled()
    ledger = load_copy_ledger(ledger_path=ledger_path, now_et=cur_et)

    closed_now: List[Dict[str, Any]] = []
    opened_now: List[Dict[str, Any]] = []
    alerts_sent: List[Dict[str, str]] = []
    dedupe_set = set(ledger.get("alert_dedupe_keys") or [])

    # 1. 盘中平仓与 T+5 到期结算检查
    if is_regular_session and ledger.get("open_positions"):
        still_open: List[Dict[str, Any]] = []
        for pos in ledger["open_positions"]:
            tk = str(pos.get("ticker") or "").upper()
            direction = str(pos.get("direction") or "LONG").upper()
            entry_px = _safe_float(pos.get("entry_price"), 0.0)
            tp_px = _safe_float(pos.get("tp_price"), 0.0)
            sl_px = _safe_float(pos.get("sl_price"), 0.0)
            alloc = _safe_float(pos.get("allocated_capital"), 0.0)
            lv = (live_quotes or {}).get(tk) if isinstance(live_quotes, dict) else None
            cur_px = _safe_float((lv or {}).get("active_price") or (lv or {}).get("last"), 0.0)
            if cur_px <= 0:
                still_open.append(pos)
                continue

            try:
                exp_d = datetime.strptime(str(pos.get("expiry_date"))[:10], "%Y-%m-%d").date()
            except Exception:
                exp_d = today_d

            exit_reason = ""
            if direction == "LONG":
                if tp_px > 0 and cur_px >= tp_px:
                    exit_reason = "TP_HIT"
                elif sl_px > 0 and cur_px <= sl_px:
                    exit_reason = "SL_HIT"
            else:
                if tp_px > 0 and cur_px <= tp_px:
                    exit_reason = "TP_HIT"
                elif sl_px > 0 and cur_px >= sl_px:
                    exit_reason = "SL_HIT"

            if not exit_reason:
                if (today_d == exp_d and et_min >= 15 * 60 + 50) or (today_d > exp_d):
                    exit_reason = "T5_EXPIRE"

            if exit_reason:
                net_ret = _calc_net_return(direction, entry_px, cur_px)
                pnl_usd = round(alloc * net_ret, 2)
                returned_cash = max(0.0, round(alloc + pnl_usd, 2))
                ledger["cash"] = round(_safe_float(ledger.get("cash"), 0.0) + returned_cash, 2)
                if exit_reason == "T5_EXPIRE":
                    outcome = "T5_WIN" if pnl_usd > 0 else "T5_LOSS"
                else:
                    outcome = exit_reason
                trade_rec = {
                    "trade_id": pos.get("trade_id") or f"{tk}_{direction}_{pos.get('entry_date', today_str)}",
                    "ticker": tk,
                    "direction": direction,
                    "slot_weight": pos.get("slot_weight", SLOT_WEIGHT),
                    "allocated_capital": round(alloc, 2),
                    "entry_date": pos.get("entry_date"),
                    "entry_time_et": pos.get("entry_time_et"),
                    "entry_price": round(entry_px, 2),
                    "tp_price": round(tp_px, 2),
                    "sl_price": round(sl_px, 2),
                    "expiry_date": pos.get("expiry_date"),
                    "exit_date": today_str,
                    "exit_time_et": cur_et.strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_price": round(cur_px, 2),
                    "exit_reason": exit_reason,
                    "outcome": outcome,
                    "net_return_pct": round(net_ret * 100.0, 2),
                    "pnl_usd": pnl_usd,
                    "calibrated_prob_pct": pos.get("calibrated_prob_pct"),
                }
                ledger.setdefault("closed_trades", []).append(trade_rec)
                closed_now.append(trade_rec)
            else:
                still_open.append(pos)

        ledger["open_positions"] = still_open

    # 先重算净值以便新开仓准确计算 25% 仓位额度
    _recalculate_account_equity(ledger, live_quotes, cur_et, record_curve_point=False)

    # 2. 正规盘 >= 09:35 ET 且 < 15:50 ET 且当日未锁定时执行开仓扫描
    in_entry_window = bool(is_regular_session and (9 * 60 + 35 <= et_min < 15 * 60 + 50))
    if in_entry_window and str(ledger.get("last_entry_date_et") or "") != today_str:
        cands = screen_copy_trade_candidates(
            ai_predictions=ai_predictions,
            live_quotes=live_quotes,
            open_positions=ledger.get("open_positions") or [],
            today_date=today_d,
            earnings_map=earnings_map,
            market_regime=market_regime,
            vix_regime=vix_regime,
            max_slots=int(ledger.get("max_positions") or MAX_POSITIONS),
        )
        expiry_str = add_us_trading_days(today_d, HOLD_TRADING_DAYS).isoformat()
        for c in cands:
            cur_eq = _safe_float(ledger.get("total_equity"), INITIAL_CAPITAL)
            cur_cash = _safe_float(ledger.get("cash"), 0.0)
            alloc = round(min(cur_cash, cur_eq * SLOT_WEIGHT), 2)
            if alloc < 10.0:
                continue
            ledger["cash"] = round(cur_cash - alloc, 2)
            pos_rec = {
                "trade_id": f"{c['ticker']}_{c['direction']}_{today_str}",
                "ticker": c["ticker"],
                "direction": c["direction"],
                "slot_weight": SLOT_WEIGHT,
                "allocated_capital": alloc,
                "entry_date": today_str,
                "entry_time_et": cur_et.strftime("%Y-%m-%d %H:%M:%S"),
                "entry_price": c["entry_price"],
                "last_price": c["entry_price"],
                "tp_price": c["tp_price"],
                "sl_price": c["sl_price"],
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

        ledger["last_entry_date_et"] = today_str

    # 3. 最终净值、浮盈与曲线落表
    _recalculate_account_equity(ledger, live_quotes, cur_et, record_curve_point=is_regular_session)

    # 4. KOL 提醒派发（严格受 dry_run 与当日去重保护）
    if not eff_dry_run and alert_sender is not None:
        for tr in closed_now:
            dkey = f"copy_close:{today_str}:{tr['trade_id']}:{tr['outcome']}"
            if dkey in dedupe_set:
                continue
            title, body = format_kol_close_alert(tr, ledger)
            color = 0x10B981 if _safe_float(tr.get("pnl_usd"), 0.0) >= 0 else 0xEF4444
            try:
                alert_sender(title, body, dkey, color)
                dedupe_set.add(dkey)
                alerts_sent.append({"kind": "CLOSE", "title": title, "body": body, "dedupe_key": dkey})
            except Exception:
                pass

        for pos in opened_now:
            dkey = f"copy_open:{today_str}:{pos['trade_id']}"
            if dkey in dedupe_set:
                continue
            title, body = format_kol_open_alert(pos)
            color = 0xEF4444 if pos.get("direction") == "SHORT" else 0x10B981
            try:
                alert_sender(title, body, dkey, color)
                dedupe_set.add(dkey)
                alerts_sent.append({"kind": "OPEN", "title": title, "body": body, "dedupe_key": dkey})
            except Exception:
                pass

    ledger["alert_dedupe_keys"] = sorted(dedupe_set)[-200:]

    persisted = False
    if not eff_dry_run:
        save_copy_ledger(ledger, ledger_path=ledger_path)
        persisted = True

    return {
        "ledger": ledger,
        "opened": opened_now,
        "closed": closed_now,
        "alerts_sent": alerts_sent,
        "dry_run": eff_dry_run,
        "persisted": persisted,
    }


def build_copy_trade_embed_dict(
    ledger_path: Optional[str] = None,
    ledger_override: Optional[Dict[str, Any]] = None,
    now_et: Optional[datetime] = None,
) -> Dict[str, Any]:
    """构建 Discord `/copy` 富文本 Embed 字典（绝不包含“建议股数”）。"""
    cur_et = _to_et_datetime(now_et)
    ledger = ledger_override if isinstance(ledger_override, dict) else load_copy_ledger(ledger_path=ledger_path, now_et=cur_et)
    init_cap = _safe_float(ledger.get("initial_capital"), INITIAL_CAPITAL)
    eq = _safe_float(ledger.get("total_equity"), init_cap)
    cash = _safe_float(ledger.get("cash"), init_cap)
    tot_ret = _safe_float(ledger.get("total_return_pct"), 0.0)
    u_pnl = _safe_float(ledger.get("unrealized_pnl"), 0.0)
    max_dd = _safe_float(ledger.get("max_drawdown_pct"), 0.0)
    stats = ledger.get("stats") if isinstance(ledger.get("stats"), dict) else _compute_stats(ledger.get("closed_trades") or [])
    open_pos = ledger.get("open_positions") or []
    closed_tr = ledger.get("closed_trades") or []

    def _fmt_wr(val: Optional[float], wins: int, tot: int) -> str:
        if val is None or tot <= 0:
            return "待结算 (0笔)"
        return f"**{val:.1f}%** ({wins}/{tot}笔)"

    pf_v = stats.get("profit_factor")
    pf_s = f"**{pf_v:.2f}**" if pf_v is not None else "待统计"
    u_sign = "+" if u_pnl >= 0 else "-"

    overview_lines = [
        f"• 💰 **总净值**：**${eq:,.2f}**（累计 `{tot_ret:+.2f}%` · 初始 ${init_cap:,.0f}）",
        f"• 💵 **可用现金**：${cash:,.2f} ｜ **持仓浮盈**：`{u_sign}${abs(u_pnl):.2f}` ｜ **最大回撤**：`-{max_dd:.2f}%`",
        f"• 📊 **总胜率**：{_fmt_wr(stats.get('overall_win_rate_pct'), stats.get('win_count', 0), stats.get('closed_count', 0))} ｜ **Profit Factor**：{pf_s}",
        f"• 🧭 **多空分拆**：🟢 多头 {_fmt_wr(stats.get('long_win_rate_pct'), stats.get('long_wins', 0), stats.get('long_total', 0))} ｜ 🔴 空头 {_fmt_wr(stats.get('short_win_rate_pct'), stats.get('short_wins', 0), stats.get('short_total', 0))}",
        f"• 🎯 **出场结构**：真止盈 TP `{stats.get('tp_hit_count', 0)}` · T+5小赚 `{stats.get('t5_win_count', 0)}` · T+5小亏 `{stats.get('t5_loss_count', 0)}` · 硬止损 SL `{stats.get('sl_hit_count', 0)}`",
    ]

    if open_pos:
        pos_lines = []
        for p in open_pos[:MAX_POSITIONS]:
            d_icon = "🔴 空" if str(p.get("direction")).upper() == "SHORT" else "🟢 多"
            wt = int(round(_safe_float(p.get("slot_weight"), SLOT_WEIGHT) * 100))
            ep = _safe_float(p.get("entry_price"), 0.0)
            lp = _safe_float(p.get("last_price"), ep)
            ur = _safe_float(p.get("unrealized_return_pct"), 0.0)
            tp = _safe_float(p.get("tp_price"), 0.0)
            sl = _safe_float(p.get("sl_price"), 0.0)
            rem = int(p.get("remaining_trading_days") or 0)
            pos_lines.append(
                f"• {d_icon} **{p.get('ticker')}** ({wt}%仓位) | 入场 `${ep:.2f}` → 现价 `${lp:.2f}` (`{ur:+.2f}%`) | 止盈 `${tp:.2f}` / 止损 `${sl:.2f}` | 剩 `{rem}` 交易日"
            )
    else:
        pos_lines = ["• 暂无在途持仓（当前 0/4 槽位占用，等待过关高胜率信号触发）"]

    if closed_tr:
        hist_lines = []
        for tr in reversed(closed_tr[-5:]):
            d_icon = "🔴 空" if str(tr.get("direction")).upper() == "SHORT" else "🟢 多"
            oc = str(tr.get("outcome") or "").upper()
            oc_cn = {"TP_HIT": "✅止盈", "SL_HIT": "🛑止损", "T5_WIN": "⏱️T+5赚", "T5_LOSS": "⏱️T+5亏"}.get(oc, oc)
            rp = _safe_float(tr.get("net_return_pct"), 0.0)
            pu = _safe_float(tr.get("pnl_usd"), 0.0)
            ps = "+" if pu >= 0 else "-"
            hist_lines.append(
                f"• `{tr.get('exit_date', '--')}` {d_icon} **{tr.get('ticker')}** {oc_cn} | `{rp:+.2f}%` ({ps}${abs(pu):.2f})"
            )
    else:
        hist_lines = ["• 暂无历史平仓记录（$1,000.00 起跑线 · 等待首个闭环结算）"]

    color = 0x10B981 if tot_ret > 0 else (0xEF4444 if tot_ret < 0 else 0x38BDF8)
    return {
        "title": f"📒 $1,000 多空双轨 KOL 模拟跟单账本（{len(open_pos)}/{MAX_POSITIONS} 持仓）",
        "description": "规则：最多 4 槽 × 25% 仓位 · 单边 1.5 bps 滑点 · T+5 三阈值闭环（与主实盘账本 100% 物理隔离）",
        "color": color,
        "fields": [
            {"name": "📈 账户净值与去水分统计", "value": "\n".join(overview_lines)[:1024], "inline": False},
            {"name": f"📌 当前在途持仓 ({len(open_pos)}/{MAX_POSITIONS})", "value": "\n".join(pos_lines)[:1024], "inline": False},
            {"name": "🧾 最近 5 笔已平仓记录", "value": "\n".join(hist_lines)[:1024], "inline": False},
        ],
        "footer": {"text": f"更新于 {ledger.get('updated_at_et', cur_et.strftime('%Y-%m-%d %H:%M:%S'))} ET · 模拟跟单仅供策略验证"},
    }


COPY_TRADE_DRAWER_CSS = """
.ct-drawer {
    background: #121826;
    border: 1px solid #263248;
    border-radius: 12px;
    margin: 14px 0;
    color: #e6edf3;
    overflow: hidden;
    flex-shrink: 0;
    max-width: 100%;
    box-sizing: border-box;
}
.ct-drawer.ct-compact {
    margin: 0;
    border-radius: 10px;
}
.ct-drawer > summary {
    list-style: none;
    cursor: pointer;
    padding: 12px 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
    font-size: 13.5px;
    font-weight: 700;
    user-select: none;
    background: rgba(56, 189, 248, 0.06);
}
.ct-drawer.ct-compact > summary {
    padding: 9px 12px;
    font-size: 12.5px;
}
.ct-drawer > summary::-webkit-details-marker { display: none; }
.ct-summary-meta {
    font-size: 12px;
    font-weight: 600;
    color: #94a3b8;
    font-variant-numeric: tabular-nums;
}
.ct-body {
    padding: 12px 16px 16px;
    border-top: 1px solid #263248;
    display: flex;
    flex-direction: column;
    gap: 12px;
}
.ct-drawer.ct-compact .ct-body {
    padding: 10px 12px 12px;
    gap: 10px;
}
.ct-kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(125px, 1fr));
    gap: 8px;
}
.ct-drawer.ct-compact .ct-kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
}
.ct-kpi-tile {
    background: #182032;
    border: 1px solid #263248;
    border-radius: 8px;
    padding: 8px 10px;
}
.ct-kpi-lbl {
    font-size: 11px;
    color: #94a3b8;
    margin-bottom: 3px;
}
.ct-kpi-val {
    font-size: 14px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    color: #f1f5f9;
}
.ct-up { color: #10b981 !important; }
.ct-down { color: #ef4444 !important; }
.ct-muted { color: #94a3b8 !important; }
.ct-svg-box {
    background: #0d1320;
    border: 1px solid #263248;
    border-radius: 8px;
    padding: 6px 8px;
    width: 100%;
    box-sizing: border-box;
}
.ct-svg-box svg {
    width: 100%;
    height: auto;
    display: block;
}
.ct-sub-title {
    font-size: 12px;
    font-weight: 700;
    color: #cbd5e1;
    margin-bottom: 4px;
}
.ct-table-wrap {
    width: 100%;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
}
.ct-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11.5px;
    font-variant-numeric: tabular-nums;
    min-width: 320px;
}
.ct-table th, .ct-table td {
    padding: 6px 8px;
    border-bottom: 1px solid #1e293b;
    text-align: left;
    white-space: nowrap;
}
.ct-table th {
    color: #94a3b8;
    font-weight: 600;
    background: rgba(15, 23, 42, 0.55);
}
.ct-empty-hint {
    font-size: 12px;
    color: #94a3b8;
    padding: 8px 10px;
    background: rgba(148, 163, 184, 0.06);
    border-radius: 6px;
}
[data-theme="light"] .ct-drawer {
    background: #ffffff;
    border-color: #d8dee6;
    color: #1f2937;
}
[data-theme="light"] .ct-drawer > summary {
    background: #f8fafc;
    color: #0f172a;
}
[data-theme="light"] .ct-summary-meta,
[data-theme="light"] .ct-kpi-lbl,
[data-theme="light"] .ct-empty-hint,
[data-theme="light"] .ct-table th {
    color: #64748b;
}
[data-theme="light"] .ct-body {
    border-top-color: #e2e8f0;
}
[data-theme="light"] .ct-kpi-tile {
    background: #f8fafc;
    border-color: #e2e8f0;
}
[data-theme="light"] .ct-kpi-val,
[data-theme="light"] .ct-sub-title {
    color: #0f172a;
}
[data-theme="light"] .ct-svg-box {
    background: #f8fafc;
    border-color: #e2e8f0;
}
[data-theme="light"] .ct-table th {
    background: #f1f5f9;
}
[data-theme="light"] .ct-table td {
    border-bottom-color: #e2e8f0;
    color: #1e293b;
}
[data-theme="light"] .ct-up { color: #059669 !important; }
[data-theme="light"] .ct-down { color: #dc2626 !important; }
"""


def _esc_html(text: Any) -> str:
    s = str(text if text is not None else "")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _build_equity_curve_svg(ledger: Dict[str, Any]) -> str:
    init_cap = _safe_float(ledger.get("initial_capital"), INITIAL_CAPITAL)
    curve = ledger.get("equity_curve") or []
    closed_tr = ledger.get("closed_trades") or []
    W, H = 600, 108
    pad_l, pad_r, pad_t, pad_b = 52, 18, 14, 22
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b

    valid_pts = [
        (str(pt.get("date") or ""), _safe_float(pt.get("equity"), init_cap))
        for pt in curve
        if isinstance(pt, dict) and _safe_float(pt.get("equity"), 0.0) > 0
    ]

    if len(valid_pts) < 2 or not closed_tr:
        y_mid = pad_t + plot_h * 0.5
        return (
            f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="模拟跟单净值曲线">'
            f'<line x1="{pad_l}" y1="{y_mid:.1f}" x2="{W - pad_r}" y2="{y_mid:.1f}" stroke="#38bdf8" stroke-width="1.6" stroke-dasharray="6 4" opacity="0.85" />'
            f'<text x="{pad_l - 6}" y="{y_mid + 3.5:.1f}" text-anchor="end" fill="#94a3b8" font-size="10" font-family="monospace">${init_cap:,.0f}</text>'
            f'<text x="{(pad_l + W - pad_r) / 2:.1f}" y="{y_mid - 10:.1f}" text-anchor="middle" fill="#38bdf8" font-size="11.5" font-weight="700">${init_cap:,.2f} 起跑线 · 等待首个过关信号</text>'
            f'<text x="{(pad_l + W - pad_r) / 2:.1f}" y="{y_mid + 18:.1f}" text-anchor="middle" fill="#94a3b8" font-size="10">每笔 25% 仓位 · 最多 4 槽 · 单边 1.5 bps 滑点 · T+5 三阈值自动结算</text>'
            f'</svg>'
        )

    vals = [v for _, v in valid_pts] + [init_cap]
    v_min = min(vals)
    v_max = max(vals)
    span = max(v_max - v_min, init_cap * 0.02)
    y_lo = v_min - span * 0.15
    y_hi = v_max + span * 0.15

    def _x(i: int) -> float:
        return pad_l + (i / max(1, len(valid_pts) - 1)) * plot_w

    def _y(v: float) -> float:
        return pad_t + ((y_hi - v) / max(1e-6, y_hi - y_lo)) * plot_h

    coords = [f"{_x(i):.1f},{_y(v):.1f}" for i, (_, v) in enumerate(valid_pts)]
    last_eq = valid_pts[-1][1]
    line_clr = "#10b981" if last_eq >= init_cap else "#ef4444"
    fill_clr = "rgba(16,185,129,0.14)" if last_eq >= init_cap else "rgba(239,68,68,0.14)"
    base_y = _y(init_cap)
    poly_pts = f"{_x(0):.1f},{H - pad_b:.1f} " + " ".join(coords) + f" {_x(len(valid_pts) - 1):.1f},{H - pad_b:.1f}"

    return (
        f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="模拟跟单净值曲线">'
        f'<line x1="{pad_l}" y1="{base_y:.1f}" x2="{W - pad_r}" y2="{base_y:.1f}" stroke="#64748b" stroke-width="1" stroke-dasharray="4 4" opacity="0.7" />'
        f'<polygon points="{poly_pts}" fill="{fill_clr}" />'
        f'<polyline points="{" ".join(coords)}" fill="none" stroke="{line_clr}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" />'
        f'<circle cx="{_x(len(valid_pts) - 1):.1f}" cy="{_y(last_eq):.1f}" r="3.6" fill="{line_clr}" />'
        f'<text x="{pad_l - 6}" y="{pad_t + 8}" text-anchor="end" fill="#94a3b8" font-size="9.5" font-family="monospace">${y_hi:,.0f}</text>'
        f'<text x="{pad_l - 6}" y="{H - pad_b}" text-anchor="end" fill="#94a3b8" font-size="9.5" font-family="monospace">${y_lo:,.0f}</text>'
        f'<text x="{pad_l}" y="{H - 5}" text-anchor="start" fill="#94a3b8" font-size="9.5">{_esc_html(valid_pts[0][0])}</text>'
        f'<text x="{W - pad_r}" y="{H - 5}" text-anchor="end" fill="#94a3b8" font-size="9.5">{_esc_html(valid_pts[-1][0])}</text>'
        f'</svg>'
    )


def render_copy_trade_drawer_html(
    ledger: Optional[Dict[str, Any]] = None,
    ledger_path: Optional[str] = None,
    compact: bool = False,
    drawer_id: str = "copyTradeLedgerDrawer",
) -> str:
    """生成可折叠的「📒 $1,000 多空模拟跟单账本」HTML 抽屉（兼容深浅色与移动端零溢出，绝不含“建议股数”）。"""
    doc = ledger if isinstance(ledger, dict) else load_copy_ledger(ledger_path=ledger_path)
    init_cap = _safe_float(doc.get("initial_capital"), INITIAL_CAPITAL)
    eq = _safe_float(doc.get("total_equity"), init_cap)
    cash = _safe_float(doc.get("cash"), init_cap)
    tot_ret = _safe_float(doc.get("total_return_pct"), 0.0)
    max_dd = _safe_float(doc.get("max_drawdown_pct"), 0.0)
    stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else _compute_stats(doc.get("closed_trades") or [])
    open_pos = doc.get("open_positions") or []
    closed_tr = doc.get("closed_trades") or []

    ret_cls = "ct-up" if tot_ret > 0 else ("ct-down" if tot_ret < 0 else "ct-muted")
    l_wr = stats.get("long_win_rate_pct")
    s_wr = stats.get("short_win_rate_pct")
    l_str = f"{l_wr:.1f}% ({stats.get('long_wins', 0)}/{stats.get('long_total', 0)})" if l_wr is not None else "待结算"
    s_str = f"{s_wr:.1f}% ({stats.get('short_wins', 0)}/{stats.get('short_total', 0)})" if s_wr is not None else "待结算"
    pf_v = stats.get("profit_factor")
    pf_str = f"{pf_v:.2f}" if pf_v is not None else "--"

    if open_pos:
        pos_rows = []
        for p in open_pos:
            d_u = str(p.get("direction") or "LONG").upper()
            d_badge = '<span class="ct-down">🔴 做空</span>' if d_u == "SHORT" else '<span class="ct-up">🟢 做多</span>'
            wt = int(round(_safe_float(p.get("slot_weight"), SLOT_WEIGHT) * 100))
            ur = _safe_float(p.get("unrealized_return_pct"), 0.0)
            ur_cls = "ct-up" if ur >= 0 else "ct-down"
            pos_rows.append(
                f"<tr>"
                f"<td><b>{_esc_html(p.get('ticker'))}</b></td>"
                f"<td>{d_badge}</td>"
                f"<td>{wt}%仓位</td>"
                f"<td>${_safe_float(p.get('entry_price')):.2f}</td>"
                f'<td class="{ur_cls}">${_safe_float(p.get("last_price")):.2f} ({ur:+.2f}%)</td>'
                f"<td>${_safe_float(p.get('tp_price')):.2f} / ${_safe_float(p.get('sl_price')):.2f}</td>"
                f"<td>{int(p.get('remaining_trading_days') or 0)}天</td>"
                f"</tr>"
            )
        open_html = (
            '<div class="ct-table-wrap"><table class="ct-table">'
            "<thead><tr><th>标的</th><th>方向</th><th>仓位</th><th>入场价</th><th>现价/浮盈</th><th>止盈/止损</th><th>剩余</th></tr></thead>"
            f"<tbody>{''.join(pos_rows)}</tbody></table></div>"
        )
    else:
        open_html = '<div class="ct-empty-hint">当前 0/4 持仓空槽 · 等待 09:35 ET 后过关高胜率多空信号自动锁定入场</div>'

    if closed_tr:
        hist_rows = []
        for tr in reversed(closed_tr[-10:]):
            d_u = str(tr.get("direction") or "LONG").upper()
            d_badge = '<span class="ct-down">🔴 空</span>' if d_u == "SHORT" else '<span class="ct-up">🟢 多</span>'
            oc = str(tr.get("outcome") or "").upper()
            oc_cn = {"TP_HIT": "✅止盈", "SL_HIT": "🛑止损", "T5_WIN": "⏱️T+5赚", "T5_LOSS": "⏱️T+5亏"}.get(oc, oc)
            rp = _safe_float(tr.get("net_return_pct"), 0.0)
            pu = _safe_float(tr.get("pnl_usd"), 0.0)
            r_cls = "ct-up" if pu >= 0 else "ct-down"
            ps = "+" if pu >= 0 else "-"
            hist_rows.append(
                f"<tr>"
                f"<td>{_esc_html(tr.get('exit_date', '--'))}</td>"
                f"<td><b>{_esc_html(tr.get('ticker'))}</b> {d_badge}</td>"
                f"<td>{oc_cn}</td>"
                f"<td>${_safe_float(tr.get('entry_price')):.2f} ➔ ${_safe_float(tr.get('exit_price')):.2f}</td>"
                f'<td class="{r_cls}">{rp:+.2f}% ({ps}${abs(pu):.2f})</td>'
                f"</tr>"
            )
        hist_html = (
            '<div class="ct-table-wrap"><table class="ct-table">'
            "<thead><tr><th>平仓日</th><th>标的/方向</th><th>出场</th><th>成交回溯</th><th>净收益/盈亏</th></tr></thead>"
            f"<tbody>{''.join(hist_rows)}</tbody></table></div>"
        )
    else:
        hist_html = '<div class="ct-empty-hint">暂无历史已平仓成交 · 闭环后自动拆解 TP 止盈 / T+5 到期 / SL 止损明细</div>'

    svg_html = _build_equity_curve_svg(doc)
    cls_extra = " ct-compact" if compact else ""
    return (
        f'<details id="{_esc_html(drawer_id)}" class="ct-drawer{cls_extra}">'
        f"<summary>"
        f"<span>📒 $1,000 多空模拟跟单账本（点击展开）</span>"
        f'<span class="ct-summary-meta">净值 <b class="{ret_cls}">${eq:,.2f} ({tot_ret:+.2f}%)</b> · 持仓 {len(open_pos)}/{MAX_POSITIONS}</span>'
        f"</summary>"
        f'<div class="ct-body">'
        f'<div class="ct-kpi-grid">'
        f'<div class="ct-kpi-tile"><div class="ct-kpi-lbl">账户总净值 / 现金</div><div class="ct-kpi-val {ret_cls}">${eq:,.2f} <span class="ct-muted" style="font-size:11px;">(现 ${cash:,.0f})</span></div></div>'
        f'<div class="ct-kpi-tile"><div class="ct-kpi-lbl">累计收益 / 最大回撤</div><div class="ct-kpi-val"><span class="{ret_cls}">{tot_ret:+.2f}%</span> <span class="ct-muted" style="font-size:11px;">/ -{max_dd:.2f}%</span></div></div>'
        f'<div class="ct-kpi-tile"><div class="ct-kpi-lbl">多头 / 空头胜率</div><div class="ct-kpi-val" style="font-size:12px;">多 {l_str} · 空 {s_str}</div></div>'
        f'<div class="ct-kpi-tile"><div class="ct-kpi-lbl">出场结构 (PF {pf_str})</div><div class="ct-kpi-val" style="font-size:12px;">TP {stats.get("tp_hit_count", 0)} · T5赚 {stats.get("t5_win_count", 0)} · T5亏 {stats.get("t5_loss_count", 0)} · SL {stats.get("sl_hit_count", 0)}</div></div>'
        f"</div>"
        f'<div class="ct-svg-box">{svg_html}</div>'
        f'<div><div class="ct-sub-title">📌 当前在途持仓 ({len(open_pos)}/{MAX_POSITIONS} · 每笔 25% 仓位)</div>{open_html}</div>'
        f'<div><div class="ct-sub-title">🧾 历史已平仓记录（最近 10 笔 · 已扣 3 bps 往返滑点）</div>{hist_html}</div>'
        f"</div>"
        f"</details>"
    )
