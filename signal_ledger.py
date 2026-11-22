# -*- coding: utf-8 -*-
"""
美股量化雷达实盘 Discord 推送信号自动记账本 + 每周六复盘周报引擎 (signal_ledger.py)

核心原则：
1. 静默自动记账：挂接在各路 Discord 真实推送成功回调之后，记录 INTRADAY_5M_BUY / 策略 A / 策略 B / 盘中走廊 / 刹车与去杠杆预警；
2. 绝对 Zero Lookahead（零未来函数）：
   - 仅当美东常规盘满 60 分钟后回填 +1h 收益；
   - 仅当美东当日 16:05 ET 收盘结算后回填 EOD 收益；
   - 仅当满 3/5 个已结算交易日后回填 T+3 / T+5 收益并标记 COMPLETE；
3. 严禁混算胜率：LONG 类统计多头盈利胜率，BEAR_DEFENSE 类独立统计防守拦截有效率；
4. 干跑与无推送测试铁律：RADAR_DISABLE_EXTERNAL_ALERTS=1 或 --no-alerts 时既不推送也不落盘生产账本。
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None

try:
    import shared_alert_state
    _SHARED_ALERTS = True
except Exception:  # pragma: no cover
    shared_alert_state = None
    _SHARED_ALERTS = False

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LEDGER_PATH = os.path.join(WORK_DIR, "data_cache", "signal_ledger.json")
BENCHMARK_TICKERS: Tuple[str, ...] = ("SPY", "QQQ", "SOXL")

SIGNAL_DIRECTION_MAP: Dict[str, str] = {
    "INTRADAY_5M_BUY": "LONG",
    "VIX_STRATEGY_A_BOTTOM": "LONG",
    "VIX_STRATEGY_B_REBOUND": "LONG",
    "CORRIDOR_BREAK_ABOVE": "LONG",
    "CORRIDOR_BREAK_BELOW": "BEAR_DEFENSE",
    "VIX_BACKWARDATION_BRAKE": "BEAR_DEFENSE",
    "TECH_DELEVERAGE_ALERT": "BEAR_DEFENSE",
}

SIGNAL_LABELS_CN: Dict[str, str] = {
    "INTRADAY_5M_BUY": "5m 日内买点",
    "VIX_STRATEGY_A_BOTTOM": "策略 A · 恐慌出清抄底窗",
    "VIX_STRATEGY_B_REBOUND": "策略 B · 盘中真退潮反抽",
    "CORRIDOR_BREAK_ABOVE": "盘中走廊上破",
    "CORRIDOR_BREAK_BELOW": "盘中走廊下破",
    "VIX_BACKWARDATION_BRAKE": "VIX 倒挂硬刹车",
    "TECH_DELEVERAGE_ALERT": "科技/VXN 去杠杆预警",
}

WEEKLY_GROUP_SPECS: List[Tuple[str, str, str, Tuple[str, ...]]] = [
    ("STRATEGY_A", "🟢 策略 A（恐慌出清抄底窗）", "LONG", ("VIX_STRATEGY_A_BOTTOM",)),
    ("STRATEGY_B", "⚡ 策略 B（盘中真退潮反抽）", "LONG", ("VIX_STRATEGY_B_REBOUND",)),
    ("INTRADAY_5M", "🎯 5m 日内买点", "LONG", ("INTRADAY_5M_BUY",)),
    ("CORRIDOR_UP", "🚀 盘中走廊上破", "LONG", ("CORRIDOR_BREAK_ABOVE",)),
    ("CORRIDOR_DOWN", "📉 盘中走廊下破", "BEAR_DEFENSE", ("CORRIDOR_BREAK_BELOW",)),
    ("DEFENSE_BRAKE", "🔴 风控刹车 / 去杠杆预警", "BEAR_DEFENSE", ("VIX_BACKWARDATION_BRAKE", "TECH_DELEVERAGE_ALERT")),
]

_LEDGER_LOCK = threading.Lock()


def _get_et_tz():
    if ZoneInfo is not None:
        try:
            return ZoneInfo("America/New_York")
        except Exception:
            pass
    return timezone(timedelta(hours=-5))


def is_ledger_disabled() -> bool:
    """Dry-Run / --no-alerts 守卫：禁用外部推送时同样禁止写入生产账本与发送周报。"""
    env_flag = os.environ.get("RADAR_DISABLE_EXTERNAL_ALERTS", "").strip().lower()
    if env_flag in ("1", "true", "yes", "on"):
        return True
    if "--no-alerts" in sys.argv:
        return True
    return False


def resolve_ledger_path(ledger_path: Optional[str] = None) -> str:
    if ledger_path:
        return str(ledger_path)
    env_p = os.environ.get("SIGNAL_LEDGER_PATH", "").strip()
    if env_p:
        return env_p
    return DEFAULT_LEDGER_PATH


def _default_ledger_payload() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": "",
        "signals": [],
    }


def load_ledger(ledger_path: Optional[str] = None) -> Dict[str, Any]:
    path = resolve_ledger_path(ledger_path)
    if not path or not os.path.exists(path):
        return _default_ledger_payload()
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if not isinstance(obj, dict):
            return _default_ledger_payload()
        sigs = obj.get("signals")
        if not isinstance(sigs, list):
            sigs = []
        return {
            "version": int(obj.get("version") or 1),
            "updated_at": str(obj.get("updated_at") or ""),
            "signals": sigs,
        }
    except Exception:
        return _default_ledger_payload()


def save_ledger(data: Dict[str, Any], ledger_path: Optional[str] = None) -> bool:
    path = resolve_ledger_path(ledger_path)
    if not path:
        return False
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        payload = {
            "version": int((data or {}).get("version") or 1),
            "updated_at": _coerce_et_dt(None).isoformat(),
            "signals": list((data or {}).get("signals") or []),
        }
        tmp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return True
    except Exception:
        return False


def _coerce_et_dt(ts: Optional[Any] = None) -> datetime:
    et_tz = _get_et_tz()
    if ts is None:
        return datetime.now(et_tz)
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            if ZoneInfo is not None and hasattr(et_tz, "key"):
                return ts.replace(tzinfo=et_tz)
            return ts.replace(tzinfo=et_tz)
        return ts.astimezone(et_tz)
    if isinstance(ts, date):
        return datetime(ts.year, ts.month, ts.day, 12, 0, tzinfo=et_tz)
    raw = str(ts).strip()
    if not raw:
        return datetime.now(et_tz)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=et_tz)
        return parsed.astimezone(et_tz)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw[:19], fmt)
            return parsed.replace(tzinfo=et_tz)
        except Exception:
            continue
    return datetime.now(et_tz)


def _opt_round(val: Any, digits: int = 2) -> Optional[float]:
    try:
        if val is None:
            return None
        fv = float(val)
        if not math.isfinite(fv):
            return None
        return round(fv, digits)
    except Exception:
        return None


def record_signal(
    signal_type: str,
    ticker: str,
    entry_price: Any,
    direction: Optional[str] = None,
    timestamp_et: Optional[Any] = None,
    vix_at_entry: Any = None,
    vix_ratio_at_entry: Any = None,
    meta: Optional[Dict[str, Any]] = None,
    ledger_path: Optional[str] = None,
    allow_when_disabled: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    静默记录单条 Discord 推送实盘信号；同日同信号类型同标的 ({date_et}:{signal_type}:{ticker}) 自动去重。
    """
    if (not allow_when_disabled) and is_ledger_disabled():
        return None

    sig_type = str(signal_type or "").strip().upper()
    tk = str(ticker or "").strip().upper()
    if not sig_type or not tk:
        return None

    try:
        px = float(entry_price)
    except Exception:
        return None
    if not (math.isfinite(px) and px > 0.0):
        return None

    dir_val = str(direction or SIGNAL_DIRECTION_MAP.get(sig_type, "LONG")).strip().upper()
    if dir_val not in ("LONG", "BEAR_DEFENSE"):
        dir_val = SIGNAL_DIRECTION_MAP.get(sig_type, "LONG")

    dt_et = _coerce_et_dt(timestamp_et)
    date_et = dt_et.strftime("%Y-%m-%d")
    dedupe_key = f"{date_et}:{sig_type}:{tk}"

    rec = {
        "dedupe_key": dedupe_key,
        "timestamp_et": dt_et.isoformat(),
        "date_et": date_et,
        "signal_type": sig_type,
        "signal_label_cn": SIGNAL_LABELS_CN.get(sig_type, sig_type),
        "direction": dir_val,
        "ticker": tk,
        "entry_price": round(px, 4),
        "vix_at_entry": _opt_round(vix_at_entry, 2),
        "vix_ratio_at_entry": _opt_round(vix_ratio_at_entry, 3),
        "eval": {
            "ret_1h_pct": None,
            "ret_eod_pct": None,
            "ret_t3_pct": None,
            "ret_t5_pct": None,
            "mae_pct": None,
            "mfe_pct": None,
            "last_eval_at": None,
        },
        "status": "OPEN",
        "meta": dict(meta) if isinstance(meta, dict) else {},
    }

    with _LEDGER_LOCK:
        ledger = load_ledger(ledger_path)
        existing_keys = {str(s.get("dedupe_key") or "") for s in ledger["signals"] if isinstance(s, dict)}
        if dedupe_key in existing_keys:
            return None
        ledger["signals"].append(rec)
        if not save_ledger(ledger, ledger_path):
            return None
    return rec


def _extract_quote_price(item: Any) -> Optional[float]:
    if not isinstance(item, dict):
        return None
    for k in ("active_price", "last", "rth_close", "ext_price", "price", "Close"):
        try:
            v = item.get(k)
            if v is not None:
                fv = float(v)
                if math.isfinite(fv) and fv > 0.0:
                    return fv
        except Exception:
            continue
    return None


def record_benchmark_signals(
    signal_type: str,
    quote_lookup: Optional[Dict[str, Any]] = None,
    vix_regime: Optional[Dict[str, Any]] = None,
    tickers: Tuple[str, ...] = BENCHMARK_TICKERS,
    timestamp_et: Optional[Any] = None,
    vix_at_entry: Any = None,
    vix_ratio_at_entry: Any = None,
    meta: Optional[Dict[str, Any]] = None,
    ledger_path: Optional[str] = None,
    allow_when_disabled: bool = False,
) -> List[Dict[str, Any]]:
    """
    为大盘级/恐慌级信号（策略 A / VIX 倒挂刹车 / 科技去杠杆预警）同步记录 SPY / QQQ / SOXL 基准价。
    """
    if (not allow_when_disabled) and is_ledger_disabled():
        return []
    vr = vix_regime if isinstance(vix_regime, dict) else {}
    vix_v = vix_at_entry if vix_at_entry is not None else vr.get("vix_spot")
    ratio_v = vix_ratio_at_entry if vix_ratio_at_entry is not None else vr.get("vix_term_ratio")
    ql = quote_lookup if isinstance(quote_lookup, dict) else {}

    out: List[Dict[str, Any]] = []
    for tk in tickers:
        px = _extract_quote_price(ql.get(tk))
        if px is None or px <= 0.0:
            continue
        rec = record_signal(
            signal_type=signal_type,
            ticker=tk,
            entry_price=px,
            timestamp_et=timestamp_et,
            vix_at_entry=vix_v,
            vix_ratio_at_entry=ratio_v,
            meta=meta,
            ledger_path=ledger_path,
            allow_when_disabled=allow_when_disabled,
        )
        if rec is not None:
            out.append(rec)
    return out


def _normalize_daily_df(df: Any, max_settled_date: date):
    if pd is None or df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    if "Close" not in df.columns:
        return None
    try:
        idx = pd.to_datetime(df.index, errors="coerce")
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        dates = idx.date
        sub = df.copy()
        sub["_d_et"] = dates
        sub = sub[sub["_d_et"].notna()]
        sub = sub[sub["_d_et"] <= max_settled_date]
        if sub.empty:
            return None
        sub = sub.sort_values("_d_et").groupby("_d_et", as_index=True).last()
        return sub
    except Exception:
        return None


def backfill_open_signals(
    live_quotes: Optional[Dict[str, Any]] = None,
    history_dict: Optional[Dict[str, Any]] = None,
    intraday_map: Optional[Dict[str, Any]] = None,
    now_et: Optional[Any] = None,
    ledger_path: Optional[str] = None,
    allow_when_disabled: bool = False,
) -> Dict[str, int]:
    """
    严格 Zero Lookahead 因果自动回填未完成信号的 +1h / EOD / T+3 / T+5 收益与 MAE/MFE：
    - 仅复用主循环已经拉取的行情与日线，绝不额外发起网络请求；
    - 仅用 <= settled_cutoff (美东当日 16:05 ET 后才计当日为已结算收盘日) 的历史日线结算 EOD/T+3/T+5。
    """
    if (not allow_when_disabled) and is_ledger_disabled():
        return {"updated": 0, "completed": 0, "skipped_disabled": 1}

    cur_et = _coerce_et_dt(now_et)
    et_tz = _get_et_tz()
    hm_et = cur_et.hour * 60 + cur_et.minute
    if cur_et.weekday() >= 5 or hm_et >= (16 * 60 + 5):
        settled_cutoff = cur_et.date()
    else:
        settled_cutoff = cur_et.date() - timedelta(days=1)

    ql = live_quotes if isinstance(live_quotes, dict) else {}
    hdict = history_dict if isinstance(history_dict, dict) else {}
    imap = intraday_map if isinstance(intraday_map, dict) else {}

    updated_cnt = 0
    completed_cnt = 0

    with _LEDGER_LOCK:
        ledger = load_ledger(ledger_path)
        signals = ledger.get("signals") or []
        if not signals:
            return {"updated": 0, "completed": 0, "skipped_disabled": 0}

        daily_cache: Dict[str, Any] = {}
        dirty = False

        for rec in signals:
            if not isinstance(rec, dict):
                continue
            if str(rec.get("status") or "OPEN").upper() == "COMPLETE":
                continue

            tk = str(rec.get("ticker") or "").strip().upper()
            try:
                entry_px = float(rec.get("entry_price") or 0.0)
            except Exception:
                continue
            if not tk or not (math.isfinite(entry_px) and entry_px > 0.0):
                continue

            entry_dt = _coerce_et_dt(rec.get("timestamp_et") or rec.get("date_et"))
            if cur_et < entry_dt:
                continue
            entry_date = entry_dt.date()

            ev = rec.get("eval")
            if not isinstance(ev, dict):
                ev = {
                    "ret_1h_pct": None,
                    "ret_eod_pct": None,
                    "ret_t3_pct": None,
                    "ret_t5_pct": None,
                    "mae_pct": None,
                    "mfe_pct": None,
                    "last_eval_at": None,
                }
                rec["eval"] = ev

            rec_changed = False

            def _update_mae_mfe(ret_pct_val: float) -> None:
                nonlocal rec_changed
                rv = round(float(ret_pct_val), 2)
                if not math.isfinite(rv):
                    return
                cur_mae = ev.get("mae_pct")
                cur_mfe = ev.get("mfe_pct")
                new_mae = min(0.0, rv) if cur_mae is None else min(float(cur_mae), rv)
                new_mfe = max(0.0, rv) if cur_mfe is None else max(float(cur_mfe), rv)
                if cur_mae is None or abs(float(cur_mae) - new_mae) > 1e-6:
                    ev["mae_pct"] = round(new_mae, 2)
                    rec_changed = True
                if cur_mfe is None or abs(float(cur_mfe) - new_mfe) > 1e-6:
                    ev["mfe_pct"] = round(new_mfe, 2)
                    rec_changed = True

            # 1) 盘中 5m 序列回填（同日触发后至当前时刻，绝不跨越 cur_et）
            intra_info = imap.get(tk) if isinstance(imap.get(tk), dict) else None
            if intra_info and str(intra_info.get("rth_date") or "") == entry_date.isoformat():
                series_5m = intra_info.get("rth_5m_series") or []
                rth_open_dt = datetime(entry_date.year, entry_date.month, entry_date.day, 9, 30, tzinfo=et_tz)
                target_1h_dt = entry_dt + timedelta(hours=1)
                best_1h_ret = None
                for pt in series_5m:
                    if not (isinstance(pt, (list, tuple)) and len(pt) >= 2):
                        continue
                    try:
                        slot_idx = int(pt[0])
                        slot_px = float(pt[1])
                    except Exception:
                        continue
                    if slot_px <= 0 or not math.isfinite(slot_px):
                        continue
                    slot_dt = rth_open_dt + timedelta(minutes=5 * slot_idx)
                    if slot_dt < entry_dt or slot_dt > cur_et:
                        continue
                    s_ret = ((slot_px - entry_px) / entry_px) * 100.0
                    _update_mae_mfe(s_ret)
                    if ev.get("ret_1h_pct") is None and best_1h_ret is None and slot_dt >= target_1h_dt:
                        best_1h_ret = round(s_ret, 2)
                if ev.get("ret_1h_pct") is None and best_1h_ret is not None:
                    ev["ret_1h_pct"] = best_1h_ret
                    rec_changed = True

            # 2) 已结算日线序列（严格 <= settled_cutoff，防止盘中未收盘 K 线充当 EOD/T+3）
            if tk not in daily_cache:
                daily_cache[tk] = _normalize_daily_df(hdict.get(tk), settled_cutoff)
            df_daily = daily_cache[tk]

            post_bars_cnt = 0
            if df_daily is not None and not df_daily.empty:
                if entry_date <= settled_cutoff and entry_date in df_daily.index:
                    eod_px = float(df_daily.loc[entry_date, "Close"])
                    if math.isfinite(eod_px) and eod_px > 0:
                        eod_ret = round(((eod_px - entry_px) / entry_px) * 100.0, 2)
                        _update_mae_mfe(eod_ret)
                        if ev.get("ret_eod_pct") is None:
                            ev["ret_eod_pct"] = eod_ret
                            rec_changed = True

                post_df = df_daily[df_daily.index > entry_date]
                post_bars_cnt = len(post_df)
                if not post_df.empty:
                    horizon_df = post_df.iloc[:5]
                    for _, row in horizon_df.iterrows():
                        for col in ("Low", "High", "Close"):
                            if col in row:
                                try:
                                    bv = float(row[col])
                                    if math.isfinite(bv) and bv > 0:
                                        _update_mae_mfe(((bv - entry_px) / entry_px) * 100.0)
                                except Exception:
                                    pass
                    if len(post_df) >= 3 and ev.get("ret_t3_pct") is None:
                        c3 = float(post_df["Close"].iloc[2])
                        if math.isfinite(c3) and c3 > 0:
                            ev["ret_t3_pct"] = round(((c3 - entry_px) / entry_px) * 100.0, 2)
                            rec_changed = True
                    if len(post_df) >= 5 and ev.get("ret_t5_pct") is None:
                        c5 = float(post_df["Close"].iloc[4])
                        if math.isfinite(c5) and c5 > 0:
                            ev["ret_t5_pct"] = round(((c5 - entry_px) / entry_px) * 100.0, 2)
                            rec_changed = True

            # 3) 最新实时报价：同日满 60 分钟兜底回填 +1h；并在 T+5 完结前持续追踪实时极值
            live_px = _extract_quote_price(ql.get(tk))
            if live_px is not None and post_bars_cnt < 5:
                live_ret = ((live_px - entry_px) / entry_px) * 100.0
                _update_mae_mfe(live_ret)
                elapsed_s = (cur_et - entry_dt).total_seconds()
                if ev.get("ret_1h_pct") is None and elapsed_s >= 3600.0 and cur_et.date() == entry_date:
                    ev["ret_1h_pct"] = round(live_ret, 2)
                    rec_changed = True

            # 4) 满 5 个交易日正式结算 COMPLETE
            if ev.get("ret_t5_pct") is not None and rec.get("status") != "COMPLETE":
                rec["status"] = "COMPLETE"
                rec_changed = True
                completed_cnt += 1

            if rec_changed:
                ev["last_eval_at"] = cur_et.isoformat()
                updated_cnt += 1
                dirty = True

        if dirty:
            save_ledger(ledger, ledger_path)

    return {"updated": updated_cnt, "completed": completed_cnt, "skipped_disabled": 0}


def evaluate_signal_outcome(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    区分多头胜率 (LONG) 与防守拦截有效率 (BEAR_DEFENSE)：
    - LONG：对应周期收益率 > 0 计为盈利胜率；
    - BEAR_DEFENSE：发出预警后标的走弱 (< 0) 或触发期后最大下跌 MAE <= -1.0% 计为有效防守拦截。
    """
    ev = (rec.get("eval") or {}) if isinstance(rec, dict) else {}
    direction = str((rec or {}).get("direction") or "LONG").upper()

    primary_ret = None
    ret_source = None
    for k in ("ret_t3_pct", "ret_eod_pct", "ret_1h_pct", "ret_t5_pct"):
        v = ev.get(k)
        if v is not None:
            try:
                fv = float(v)
                if math.isfinite(fv):
                    primary_ret = fv
                    ret_source = k
                    break
            except Exception:
                continue

    mae_val = None
    if ev.get("mae_pct") is not None:
        try:
            mv = float(ev.get("mae_pct"))
            if math.isfinite(mv):
                mae_val = mv
        except Exception:
            mae_val = None

    if direction == "BEAR_DEFENSE":
        settled = (primary_ret is not None) or (mae_val is not None)
        if not settled:
            hit = None
        else:
            hit = bool((primary_ret is not None and primary_ret < 0.0) or (mae_val is not None and mae_val <= -1.0))
    else:
        settled = primary_ret is not None
        hit = bool(primary_ret > 0.0) if settled else None

    return {
        "direction": direction,
        "settled": settled,
        "hit": hit,
        "primary_ret_pct": primary_ret,
        "ret_source": ret_source,
        "eod_pct": _opt_round(ev.get("ret_eod_pct"), 2),
        "t3_pct": _opt_round(ev.get("ret_t3_pct"), 2),
        "t5_pct": _opt_round(ev.get("ret_t5_pct"), 2),
        "mae_pct": mae_val,
        "mfe_pct": _opt_round(ev.get("mfe_pct"), 2),
    }


def _avg_or_none(vals: List[Optional[float]]) -> Optional[float]:
    clean = [float(v) for v in vals if v is not None and math.isfinite(float(v))]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 2)


def get_recent_summary(
    days: int = 30,
    max_recent: int = 10,
    now_et: Optional[Any] = None,
    ledger_path: Optional[str] = None,
) -> Dict[str, Any]:
    """生成网页端『📒 实盘推送信号记账本（近 30 日）』轻量汇总数据。"""
    cur_et = _coerce_et_dt(now_et)
    cutoff_d = (cur_et.date() - timedelta(days=max(1, int(days)) - 1)).isoformat()
    ledger = load_ledger(ledger_path)
    raw_sigs = [s for s in (ledger.get("signals") or []) if isinstance(s, dict)]
    recent_pool = [s for s in raw_sigs if str(s.get("date_et") or "") >= cutoff_d]
    recent_pool.sort(key=lambda x: (str(x.get("timestamp_et") or ""), str(x.get("dedupe_key") or "")), reverse=True)

    long_recs: List[Dict[str, Any]] = []
    def_recs: List[Dict[str, Any]] = []
    enriched: List[Dict[str, Any]] = []

    for rec in recent_pool:
        out = evaluate_signal_outcome(rec)
        if out["direction"] == "BEAR_DEFENSE":
            def_recs.append({"rec": rec, "out": out})
        else:
            long_recs.append({"rec": rec, "out": out})
        enriched.append({
            "dedupe_key": rec.get("dedupe_key", ""),
            "timestamp_et": rec.get("timestamp_et", ""),
            "date_et": rec.get("date_et", ""),
            "signal_type": rec.get("signal_type", ""),
            "signal_label_cn": rec.get("signal_label_cn") or SIGNAL_LABELS_CN.get(str(rec.get("signal_type") or ""), str(rec.get("signal_type") or "")),
            "direction": out["direction"],
            "ticker": rec.get("ticker", ""),
            "entry_price": rec.get("entry_price"),
            "status": rec.get("status", "OPEN"),
            "settled": out["settled"],
            "hit": out["hit"],
            "ret_1h_pct": _opt_round((rec.get("eval") or {}).get("ret_1h_pct"), 2),
            "ret_eod_pct": out["eod_pct"],
            "ret_t3_pct": out["t3_pct"],
            "ret_t5_pct": out["t5_pct"],
            "mae_pct": out["mae_pct"],
            "mfe_pct": out["mfe_pct"],
        })

    long_settled = [x for x in long_recs if x["out"]["settled"]]
    long_wins = [x for x in long_settled if x["out"]["hit"] is True]
    long_wr = round((len(long_wins) / len(long_settled)) * 100.0, 1) if long_settled else None

    def_settled = [x for x in def_recs if x["out"]["settled"]]
    def_hits = [x for x in def_settled if x["out"]["hit"] is True]
    def_eff = round((len(def_hits) / len(def_settled)) * 100.0, 1) if def_settled else None

    return {
        "days": int(days),
        "total_records": len(recent_pool),
        "settled_total": len(long_settled) + len(def_settled),
        "long_total": len(long_recs),
        "long_settled": len(long_settled),
        "long_wins": len(long_wins),
        "long_win_rate_pct": long_wr,
        "defense_total": len(def_recs),
        "defense_settled": len(def_settled),
        "defense_hits": len(def_hits),
        "defense_eff_rate_pct": def_eff,
        "recent_records": enriched[:max(1, int(max_recent))],
    }


def is_weekly_recap_window(now_et: Optional[Any] = None) -> Tuple[bool, str, date, date]:
    """
    判定当前是否处于周六实盘复盘周报触发窗口（美东周五 18:30 ET ~ 周六 12:00 ET，
    对应韩国时间周六早 08:30 ~ 周日凌晨），并生成跨设备去重键 WEEKLY_LEDGER_RECAP:{iso_year}-W{iso_week:02d}。
    """
    cur_et = _coerce_et_dt(now_et)
    wd = cur_et.weekday()  # 0=Mon ... 4=Fri, 5=Sat
    hm = cur_et.hour * 60 + cur_et.minute

    in_window = (wd == 4 and hm >= (18 * 60 + 30)) or (wd == 5 and hm <= (12 * 60))
    if wd == 4:
        week_fri = cur_et.date()
    elif wd == 5:
        week_fri = cur_et.date() - timedelta(days=1)
    else:
        week_fri = cur_et.date() + timedelta(days=(4 - wd))
    week_mon = week_fri - timedelta(days=4)
    iso = week_fri.isocalendar()
    week_key = f"WEEKLY_LEDGER_RECAP:{iso.year}-W{iso.week:02d}"
    return in_window, week_key, week_mon, week_fri


def _fmt_pct_str(val: Optional[float]) -> str:
    if val is None or not math.isfinite(float(val)):
        return "--"
    return f"{float(val):+.2f}%"


def build_weekly_recap_payload(
    ledger_data: Optional[Dict[str, Any]] = None,
    now_et: Optional[Any] = None,
    ledger_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    构建每周六 Discord『实盘信号复盘周报』紧凑 Embed 卡片（绝无裸 `---` 横线）。
    """
    cur_et = _coerce_et_dt(now_et)
    _, week_key, week_mon, week_fri = is_weekly_recap_window(cur_et)
    ledger = ledger_data if isinstance(ledger_data, dict) else load_ledger(ledger_path)
    all_sigs = [s for s in (ledger.get("signals") or []) if isinstance(s, dict)]

    mon_s = week_mon.isoformat()
    fri_s = week_fri.isoformat()
    week_sigs = [s for s in all_sigs if mon_s <= str(s.get("date_et") or "") <= fri_s]

    long_items = []
    def_items = []
    for rec in week_sigs:
        out = evaluate_signal_outcome(rec)
        item = {"rec": rec, "out": out}
        if out["direction"] == "BEAR_DEFENSE":
            def_items.append(item)
        else:
            long_items.append(item)

    long_settled = [x for x in long_items if x["out"]["settled"]]
    long_wins = [x for x in long_settled if x["out"]["hit"] is True]
    long_wr = (len(long_wins) / len(long_settled) * 100.0) if long_settled else None

    def_settled = [x for x in def_items if x["out"]["settled"]]
    def_hits = [x for x in def_settled if x["out"]["hit"] is True]
    def_eff = (len(def_hits) / len(def_settled) * 100.0) if def_settled else None

    title = f"📒【美股量化雷达 · 本周实盘信号复盘周报 ({week_mon.strftime('%m/%d')}~{week_fri.strftime('%m/%d')})】"
    lines: List[str] = [
        f"### {title}",
        f"> 📅 **统计周期**：`{mon_s}` ~ `{fri_s}` ｜ **本周累计记账**：`{len(week_sigs)}` 笔",
    ]

    if len(long_items) == 0:
        lines.append(
            f"> 🛡️ **本周全周风控防守零做多误触**（共触发 **`{len(def_items)}`** 次刹车/预警"
            + (f" ｜ 防守拦截有效率 **`{def_eff:.1f}%`** (`{len(def_hits)}/{len(def_settled)}`)" if def_eff is not None else "")
            + "）"
        )
    else:
        wr_s = f"`{long_wr:.1f}%` (`{len(long_wins)}/{len(long_settled)}`)" if long_wr is not None else "待结算"
        eff_s = f"`{def_eff:.1f}%` (`{len(def_hits)}/{len(def_settled)}`)" if def_eff is not None else "无/待结算"
        lines.append(f"> 📈 **做多信号 (`{len(long_items)}` 笔)**：实盘胜率 {wr_s}")
        lines.append(f"> 🛡️ **防守拦截 (`{len(def_items)}` 笔)**：拦截有效率 {eff_s}")

    lines.append("")
    lines.append("**📊 分组战绩汇总**")
    any_group = False
    for _, grp_label, grp_dir, grp_types in WEEKLY_GROUP_SPECS:
        grp_items = [
            x for x in (long_items if grp_dir == "LONG" else def_items)
            if str(x["rec"].get("signal_type") or "") in grp_types
        ]
        if not grp_items:
            continue
        any_group = True
        g_settled = [x for x in grp_items if x["out"]["settled"]]
        g_hits = [x for x in g_settled if x["out"]["hit"] is True]
        rate_lbl = "胜率" if grp_dir == "LONG" else "有效率"
        rate_str = f"{(len(g_hits)/len(g_settled)*100.0):.1f}%" if g_settled else "待结算"
        avg_eod = _avg_or_none([x["out"]["eod_pct"] for x in grp_items])
        avg_t3 = _avg_or_none([x["out"]["t3_pct"] for x in grp_items])
        avg_mae = _avg_or_none([x["out"]["mae_pct"] for x in grp_items])
        lines.append(
            f"> • **{grp_label}**：触发 `{len(grp_items)}` 次 ｜ {rate_lbl} `{rate_str}` "
            f"｜ 均值 当日 `{_fmt_pct_str(avg_eod)}` / T+3 `{_fmt_pct_str(avg_t3)}` ｜ 均MAE `{_fmt_pct_str(avg_mae)}`"
        )
    if not any_group:
        lines.append("> • 本周美股休市或未触发任何需记账的 Discord 实盘推送信号。")

    # 红黑榜复盘：优先从做多信号评估盈亏表现；若做多信号为 0，展示防守有效拦截榜或空榜提示
    if long_items:
        lines.append("")
        lines.append("**🏅 本周红黑榜复盘**")
        rank_pool = [x for x in long_items if x["out"]["primary_ret_pct"] is not None]
        if rank_pool:
            best_top2 = sorted(rank_pool, key=lambda x: float(x["out"]["primary_ret_pct"]), reverse=True)[:2]
            worst_top2 = sorted(rank_pool, key=lambda x: float(x["out"]["primary_ret_pct"]))[:2]
            lines.append("> **🏆 本周最佳信号 Top 2**")
            for idx, x in enumerate(best_top2, 1):
                r = x["rec"]
                o = x["out"]
                lines.append(
                    f"> {idx}. **`{r['ticker']}`** ({r.get('signal_label_cn') or r['signal_type']} · `{r['date_et']}`) "
                    f"触发 `${float(r['entry_price']):.2f}` ➔ 实盘 **`{_fmt_pct_str(o['primary_ret_pct'])}`** (MAE `{_fmt_pct_str(o['mae_pct'])}`)"
                )
            lines.append("> **🩹 最差踩坑复盘 Top 2**")
            for idx, x in enumerate(worst_top2, 1):
                r = x["rec"]
                o = x["out"]
                lines.append(
                    f"> {idx}. **`{r['ticker']}`** ({r.get('signal_label_cn') or r['signal_type']} · `{r['date_et']}`) "
                    f"触发 `${float(r['entry_price']):.2f}` ➔ 实盘 **`{_fmt_pct_str(o['primary_ret_pct'])}`** (MAE `{_fmt_pct_str(o['mae_pct'])}`)"
                )
        else:
            lines.append("> • 本周做多信号尚在持仓观测期（未满结算周期），下轮自动更新红黑榜。")
    elif def_items:
        def_rank = [x for x in def_items if x["out"]["mae_pct"] is not None or x["out"]["primary_ret_pct"] is not None]
        if def_rank:
            lines.append("")
            lines.append("**🛡️ 本周防守拦截重点回放**")
            top_def = sorted(
                def_rank,
                key=lambda x: float(x["out"]["mae_pct"] if x["out"]["mae_pct"] is not None else (x["out"]["primary_ret_pct"] or 0.0)),
            )[:2]
            for idx, x in enumerate(top_def, 1):
                r = x["rec"]
                o = x["out"]
                lines.append(
                    f"> {idx}. **`{r['ticker']}`** ({r.get('signal_label_cn') or r['signal_type']} · `{r['date_et']}`) "
                    f"警报位 `${float(r['entry_price']):.2f}` ➔ 后续实盘 `{_fmt_pct_str(o['primary_ret_pct'])}` / 最大回撤 `{_fmt_pct_str(o['mae_pct'])}`"
                )

    embed_color = 0x38BDF8 if len(long_items) == 0 else (0x00E676 if (long_wr is not None and long_wr >= 50.0) else 0xF59E0B)
    embed = {
        "title": title,
        "description": "\n".join(lines),
        "color": embed_color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {
            "text": f"美股全周期量化雷达 V4.2 · 实盘记账复盘周报 ({week_key})"
        },
    }
    return {
        "username": "小豆泥·实盘复盘周报",
        "embeds": [embed],
        "_week_key": week_key,
    }


def dispatch_weekly_recap_discord(
    now_et: Optional[Any] = None,
    ledger_path: Optional[str] = None,
    webhook_url: Optional[str] = None,
    force: bool = False,
    device_tag: str = "",
) -> Dict[str, Any]:
    """
    每周美东周五收盘结算后（周五 18:30 ET ~ 周六 12:00 ET）自动向 Discord 定时战报频道发送 1 次复盘周报；
    严格遵守 RADAR_DISABLE_EXTERNAL_ALERTS / --no-alerts 禁发红线与 shared_alert_state 同周跨设备去重锁。
    """
    cur_et = _coerce_et_dt(now_et)
    in_window, week_key, _, _ = is_weekly_recap_window(cur_et)

    if is_ledger_disabled():
        return {"sent": False, "reason": "DISABLED", "week_key": week_key}

    if (not force) and (not in_window):
        return {"sent": False, "reason": "OUTSIDE_WINDOW", "week_key": week_key}

    if _SHARED_ALERTS and shared_alert_state.is_sent(week_key, max_age=None):
        return {"sent": False, "reason": "ALREADY_SENT_WEEKLY", "week_key": week_key}

    url = str(webhook_url or "").strip()
    if not url:
        try:
            import discord_sentinel
            url = str(discord_sentinel.get_sentinel_webhook_url() or "").strip()
        except Exception:
            url = ""
    if not url:
        url = (
            os.environ.get("DISCORD_SENTINEL_WEBHOOK_URL", "").strip()
            or os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
        )
    if not url:
        print("[!] 未配置 Discord 定时战报 Webhook (DISCORD_SENTINEL_WEBHOOK_URL / DISCORD_WEBHOOK_URL)，本轮跳过周报推送不加锁。")
        return {"sent": False, "reason": "NO_WEBHOOK", "week_key": week_key}

    payload_full = build_weekly_recap_payload(now_et=cur_et, ledger_path=ledger_path)
    payload_send = {k: v for k, v in payload_full.items() if not str(k).startswith("_")}

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload_send).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 204):
                if _SHARED_ALERTS:
                    shared_alert_state.mark(week_key, device_tag or "PC")
                print(f"  [+] 📒 Discord 周六实盘复盘周报推送成功 ({week_key})")
                return {"sent": True, "reason": "OK", "week_key": week_key}
            print(f"  [!] 📒 Discord 周报推送返回非预期状态码: {resp.status}")
            return {"sent": False, "reason": f"HTTP_{resp.status}", "week_key": week_key}
    except Exception as e:
        print(f"  [!] 📒 Discord 周报推送异常 (保留重试资格不加锁): {e}")
        return {"sent": False, "reason": f"ERROR:{e}", "week_key": week_key}
