# -*- coding: utf-8 -*-
"""
V4.4 精准风控中枢：当日 S1/S2/R1/R2 静态锚定 + 对称顶底互换状态机 + 真实筹码/ATR估算区分 + 4H见底绿标压制

核心保证：
1. 当日静态锚定 (`data_cache/daily_sr_anchors.json`)：
   每个美东交易日首次基于昨收价与历史 60 日真实结构锁定 anchor_s1/s2/r1/r2 及各自来源标记
   (REAL_CHIPS vs ATR_ESTIMATE)，盘中轮询绝不随现价偷偷漂移，仅跨日或除权/拆股修复时重建。
2. 对称五态顶底互换状态机 (S/R Flip)：
   INTACT / S1_BREACHED / ALL_SUPPORT_BREACHED / R1_BROKEN / ALL_RESISTANCE_BROKEN。
3. 熔断保护期 / VIX 倒挂期强制压制 `🟢 4H 底背离拐点 (见底信号)` 绿标。
"""

import json
import math
import os
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from zoneinfo import ZoneInfo
    _NY_TZ = ZoneInfo("America/New_York")
except Exception:
    _NY_TZ = timezone(timedelta(hours=-5))


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SR_ANCHORS_PATH = os.path.join(BASE_DIR, "data_cache", "daily_sr_anchors.json")

SOURCE_REAL_CHIPS = "REAL_CHIPS"
SOURCE_ATR_ESTIMATE = "ATR_ESTIMATE"

STATE_INTACT = "INTACT"
STATE_S1_BREACHED = "S1_BREACHED"
STATE_ALL_SUPPORT_BREACHED = "ALL_SUPPORT_BREACHED"
STATE_R1_BROKEN = "R1_BROKEN"
STATE_ALL_RESISTANCE_BROKEN = "ALL_RESISTANCE_BROKEN"

_TIER1_LOW_VOL_TICKERS = frozenset({"XAUUSD", "GLD", "TLT"})
_ALL_BREACH_STATES = (
    STATE_INTACT,
    STATE_S1_BREACHED,
    STATE_ALL_SUPPORT_BREACHED,
    STATE_R1_BROKEN,
    STATE_ALL_RESISTANCE_BROKEN,
)


def _opt_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        fv = float(val)
        return fv if math.isfinite(fv) else None
    except Exception:
        return None


def _compute_eff_atr(atr_pct: float) -> float:
    raw = max(0.012, float(atr_pct or 2.5) / 100.0)
    if raw > 0.040:
        return 0.040 + 0.50 * (min(raw, 0.100) - 0.040)
    return raw


def _compute_vp_poc(df: pd.DataFrame, window: int = 20, bins: int = 30) -> Optional[float]:
    try:
        if df is None or df.empty or "Close" not in df.columns:
            return None
        sub = df.tail(window)
        c = pd.to_numeric(sub["Close"], errors="coerce").dropna().astype(float).values
        if len(c) < 3:
            return None
        if "High" in sub.columns and "Low" in sub.columns:
            h = pd.to_numeric(sub["High"], errors="coerce").fillna(sub["Close"]).astype(float).values[-len(c):]
            l = pd.to_numeric(sub["Low"], errors="coerce").fillna(sub["Close"]).astype(float).values[-len(c):]
        else:
            h, l = c, c
        v = (
            pd.to_numeric(sub["Volume"], errors="coerce").fillna(1.0).astype(float).values[-len(c):]
            if "Volume" in sub.columns
            else np.ones(len(c))
        )
        min_p = float(np.min(l))
        max_p = float(np.max(h))
        if not (math.isfinite(min_p) and math.isfinite(max_p)):
            return None
        if max_p - min_p < 1e-4:
            return round(float(c[-1]), 2)
        edges = np.linspace(min_p, max_p, bins + 1)
        bvols = np.zeros(bins)
        typ = (h + l + c) / 3.0
        idxs = np.clip(np.digitize(typ, edges) - 1, 0, bins - 1)
        for bi, vol in zip(idxs, v):
            bvols[bi] += max(0.0, float(vol))
        max_i = int(np.argmax(bvols))
        poc = (edges[max_i] + edges[max_i + 1]) / 2.0
        return round(float(poc), 2)
    except Exception:
        return None


def resolve_trading_date_et(now_et: Optional[Any] = None, session_code: str = "") -> str:
    """返回当前美东交易日键 (`YYYY-MM-DD`)，周末回落至周五，Sun~Thu >=20:00 ET 归属下一交易日。"""
    if isinstance(now_et, datetime):
        cur_et = now_et.replace(tzinfo=_NY_TZ) if now_et.tzinfo is None else now_et.astimezone(_NY_TZ)
    elif isinstance(now_et, date):
        cur_et = datetime.combine(now_et, dt_time(12, 0), tzinfo=_NY_TZ)
    else:
        cur_et = datetime.now(_NY_TZ)

    sess = str(session_code or "").strip().upper()
    d = cur_et.date()
    hm = cur_et.hour * 60 + cur_et.minute
    wd = d.weekday()

    if sess == "WEEKEND" or wd == 5 or (wd == 6 and hm < 20 * 60) or (wd == 4 and hm >= 20 * 60):
        # 回到最近一个周五
        while d.weekday() != 4:
            d -= timedelta(days=1)
        return d.isoformat()

    if wd == 6 and hm >= 20 * 60:
        return (d + timedelta(days=1)).isoformat()

    if hm >= 20 * 60 and wd < 4:
        return (d + timedelta(days=1)).isoformat()

    return d.isoformat()


def _prior_history_slice(df: Optional[pd.DataFrame], date_et: str) -> Optional[pd.DataFrame]:
    """严格因果切片：若 df 最后几行已包含 date_et 当日及之后K线且前置样本>=10日，仅取 < date_et 的已结算历史。"""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty or "Close" not in df.columns:
        return None
    try:
        idx_dt = pd.to_datetime(df.index, errors="coerce")
        valid_mask = idx_dt.notna()
        if not valid_mask.any():
            return df
        df_clean = df[valid_mask].copy()
        idx_dt = idx_dt[valid_mask]
        if getattr(idx_dt, "tz", None) is not None:
            idx_dt = idx_dt.tz_localize(None)
        cutoff = datetime.strptime(str(date_et)[:10], "%Y-%m-%d").date()
        date_vals = np.array([x.date() for x in idx_dt])
        prior = df_clean[date_vals < cutoff]
        if len(prior) >= 10:
            return prior
        # 若周末或单测试数据以 date_et 当天收尾且前序不满 10 根，保留不含当日末尾的退避或全量已收盘样本
        up_to = df_clean[date_vals <= cutoff]
        return up_to if not up_to.empty else df_clean
    except Exception:
        return df


def compute_static_sr_levels_with_sources(
    ticker: str,
    ref_price: Optional[float] = None,
    atr_pct: float = 2.5,
    df_history: Optional[pd.DataFrame] = None,
    low_60d: Optional[float] = None,
    high_60d: Optional[float] = None,
    poc_20d: Optional[float] = None,
    ema20: Optional[float] = None,
    put_wall: Optional[float] = None,
    call_wall: Optional[float] = None,
) -> Dict[str, Any]:
    """
    计算基于基准价 (`prev_close`) 与真实历史筹码/波段结构的 4 级静态锚点 (`S2 < S1 < ref_price < R1 < R2`)，
    严格区分真实历史筹码 (`REAL_CHIPS`) 与 ATR/数学估算兜底 (`ATR_ESTIMATE`)。
    """
    tk_u = str(ticker or "").strip().upper()
    ref_f = _opt_float(ref_price)
    if (ref_f is None or ref_f <= 0) and df_history is not None and not df_history.empty and "Close" in df_history.columns:
        ref_f = _opt_float(df_history["Close"].iloc[-1])
    base_px = round(max(0.01, float(ref_f or 100.0)), 2)

    eff_atr = _compute_eff_atr(atr_pct)
    is_tier1 = tk_u in _TIER1_LOW_VOL_TICKERS
    t1_mult = float(np.clip(0.50 * eff_atr, 0.008, 0.013)) if is_tier1 else 0.0

    support_real_cands: List[float] = []
    resist_real_cands: List[float] = []

    has_real_hist = df_history is not None and isinstance(df_history, pd.DataFrame) and len(df_history) >= 10 and "Close" in df_history.columns
    if has_real_hist:
        sub60 = df_history.tail(60)
        sub20 = df_history.tail(20)
        c60 = pd.to_numeric(sub60["Close"], errors="coerce").dropna()
        c20 = pd.to_numeric(sub20["Close"], errors="coerce").dropna()
        h60_ser = pd.to_numeric(sub60["High"], errors="coerce").dropna() if "High" in sub60.columns else c60
        l60_ser = pd.to_numeric(sub60["Low"], errors="coerce").dropna() if "Low" in sub60.columns else c60
        h20_ser = pd.to_numeric(sub20["High"], errors="coerce").dropna() if "High" in sub20.columns else c20
        l20_ser = pd.to_numeric(sub20["Low"], errors="coerce").dropna() if "Low" in sub20.columns else c20

        if not l60_ser.empty and low_60d is None:
            prior_l60 = l60_ser.iloc[:-1] if len(l60_ser) >= 10 else l60_ser
            low_60d = _opt_float(prior_l60.min())
        if not h60_ser.empty and high_60d is None:
            prior_h60 = h60_ser.iloc[:-1] if len(h60_ser) >= 10 else h60_ser
            high_60d = _opt_float(prior_h60.max())
        prior_l20 = l20_ser.iloc[:-1] if len(l20_ser) >= 10 else l20_ser
        prior_h20 = h20_ser.iloc[:-1] if len(h20_ser) >= 10 else h20_ser
        low_20d = _opt_float(prior_l20.min()) if not prior_l20.empty else None
        high_20d = _opt_float(prior_h20.max()) if not prior_h20.empty else None
        if ema20 is None and len(df_history) >= 5:
            ema20 = _opt_float(pd.to_numeric(df_history["Close"], errors="coerce").dropna().ewm(span=20, adjust=False).mean().iloc[-1])
        ma50 = _opt_float(pd.to_numeric(df_history["Close"], errors="coerce").dropna().tail(50).mean()) if len(df_history) >= 20 else None
        if poc_20d is None:
            poc_20d = _compute_vp_poc(df_history, window=20)
        poc_60d = _compute_vp_poc(df_history, window=60)

        for lvl in (low_60d, low_20d, ema20, ma50, poc_20d, poc_60d):
            lv = _opt_float(lvl)
            if lv is not None and lv > 0:
                if lv < base_px * 0.996:
                    support_real_cands.append(round(lv, 2))
                elif lv > base_px * 1.004:
                    resist_real_cands.append(round(lv, 2))

        for lvl in (high_20d, high_60d):
            lv = _opt_float(lvl)
            if lv is not None and lv > base_px * 1.004:
                resist_real_cands.append(round(lv, 2))

        if low_60d is not None and high_60d is not None and high_60d > low_60d:
            rng = float(high_60d) - float(low_60d)
            for fib in (float(high_60d) - 0.382 * rng, float(high_60d) - 0.618 * rng):
                if 0 < fib < base_px * 0.996:
                    support_real_cands.append(round(fib, 2))
                elif base_px * 1.004 < fib <= base_px * 1.50:
                    resist_real_cands.append(round(fib, 2))
    else:
        for lvl in (low_60d, ema20, poc_20d):
            lv = _opt_float(lvl)
            if lv is not None and lv > 0:
                if lv < base_px * 0.996:
                    support_real_cands.append(round(lv, 2))
                elif lv > base_px * 1.004:
                    resist_real_cands.append(round(lv, 2))
        hv = _opt_float(high_60d)
        if hv is not None and hv > base_px * 1.004:
            resist_real_cands.append(round(hv, 2))

    pw = _opt_float(put_wall)
    if pw is not None and 0.70 * base_px <= pw < base_px * 0.996:
        support_real_cands.append(round(pw, 2))
    cw = _opt_float(call_wall)
    if cw is not None and base_px * 1.004 < cw <= 1.50 * base_px:
        resist_real_cands.append(round(cw, 2))

    min_step = max(0.01, round(base_px * 0.005, 2))
    if is_tier1:
        max_s2_dist = 0.055
        s1_est_default = round(base_px * (1.0 - t1_mult), 2)
        s2_est_default = round(base_px * (1.0 - max(0.022, eff_atr)), 2)
    else:
        max_s2_dist = min(0.085, max(0.048, 1.55 * eff_atr))
        s1_est_default = round(base_px * (1.0 - max(0.025, 0.75 * eff_atr)), 2)
        s2_est_default = round(base_px * (1.0 - max(0.045, min(max_s2_dist, 1.45 * eff_atr))), 2)

    valid_sup = sorted({
        round(x, 2)
        for x in support_real_cands
        if (base_px * (1.0 - max_s2_dist)) <= x <= (base_px * 0.995)
    }, reverse=True)

    # S1 选择：优先真实历史筹码/结构位，否则 ATR 估算
    if is_tier1:
        # 低波防御工具若无 2% 内的真实结构位，使用窄幅 ATR 估算防线并诚实标注 ATR_ESTIMATE
        tight_sup = [x for x in valid_sup if (base_px * 0.975) <= x <= (base_px * 0.995)]
        if tight_sup:
            s1 = tight_sup[0]
            s1_source = SOURCE_REAL_CHIPS
        else:
            s1 = s1_est_default
            s1_source = SOURCE_ATR_ESTIMATE
    elif valid_sup:
        # 从近期真实支撑中挑选合理波段位（优先距现价至少 1.2% 的真实结构位，否则取最近真实位）
        deep_enough = [x for x in valid_sup if x <= base_px * 0.988]
        s1 = deep_enough[0] if deep_enough else valid_sup[0]
        s1_source = SOURCE_REAL_CHIPS
    else:
        s1 = s1_est_default
        s1_source = SOURCE_ATR_ESTIMATE

    if s1 >= base_px:
        s1 = round(base_px - min_step, 2)
        s1_source = SOURCE_ATR_ESTIMATE

    # S2 选择：必须严格低于 S1 - min_step；无更深真实结构位时使用 ATR 估算兜底
    deeper_sup = [x for x in valid_sup if x <= round(s1 - max(min_step, base_px * 0.012), 2)]
    if not deeper_sup:
        deeper_sup = [x for x in valid_sup if x <= round(s1 - min_step, 2)]

    if deeper_sup:
        s2 = deeper_sup[0]
        s2_source = SOURCE_REAL_CHIPS
    else:
        s2_atr = min(s2_est_default, round(s1 - max(min_step, base_px * 0.018), 2))
        floor_s2 = round(base_px * (1.0 - max_s2_dist), 2)
        s2 = round(max(floor_s2, s2_atr), 2)
        if s2 >= s1:
            s2 = round(s1 - min_step, 2)
        s2_source = SOURCE_ATR_ESTIMATE

    # R1 / R2 选择：优先真实历史套牢筹码/均线/前高/Call墙，否则 ATR 估算
    valid_res = sorted({
        round(x, 2)
        for x in resist_real_cands
        if (base_px * 1.005) <= x <= (base_px * 1.50)
    })
    near_res = [x for x in valid_res if x <= base_px * 1.25]
    if near_res:
        r1 = near_res[0]
        r1_source = SOURCE_REAL_CHIPS
    else:
        r1 = round(base_px * (1.0 + max(0.030, 0.85 * eff_atr)), 2)
        r1_source = SOURCE_ATR_ESTIMATE

    if r1 <= base_px:
        r1 = round(base_px + min_step, 2)
        r1_source = SOURCE_ATR_ESTIMATE

    higher_res = [x for x in valid_res if x >= round(r1 + max(min_step, base_px * 0.015), 2)]
    if not higher_res:
        higher_res = [x for x in valid_res if x >= round(r1 + min_step, 2)]
    if higher_res:
        r2 = higher_res[-1] if len(higher_res) > 1 and higher_res[-1] <= base_px * 1.35 else higher_res[0]
        r2_source = SOURCE_REAL_CHIPS
    else:
        r2 = round(max(r1 + max(min_step, base_px * 0.025), base_px * (1.0 + max(0.060, 1.65 * eff_atr))), 2)
        r2_source = SOURCE_ATR_ESTIMATE

    if r2 <= r1:
        r2 = round(r1 + min_step, 2)
        r2_source = SOURCE_ATR_ESTIMATE

    sl = round(min(s2 * (0.992 if is_tier1 else 0.985), s2 - min_step), 2)
    return {
        "prev_close": base_px,
        "anchor_s1": round(float(s1), 2),
        "anchor_s2": round(float(s2), 2),
        "anchor_sl": round(float(sl), 2),
        "anchor_r1": round(float(r1), 2),
        "anchor_r2": round(float(r2), 2),
        "s1_source": s1_source,
        "s2_source": s2_source,
        "r1_source": r1_source,
        "r2_source": r2_source,
    }


def load_daily_sr_anchors(anchors_path: Optional[str] = None) -> Dict[str, Any]:
    target = os.path.abspath(anchors_path or DEFAULT_SR_ANCHORS_PATH)
    if not os.path.exists(target):
        return {"version": 1, "date_et": "", "anchors": {}}
    try:
        with open(target, "r", encoding="utf-8") as f:
            doc = json.load(f)
        if isinstance(doc, dict) and isinstance(doc.get("anchors"), dict):
            return doc
    except Exception:
        pass
    return {"version": 1, "date_et": "", "anchors": {}}


def save_daily_sr_anchors(doc: Dict[str, Any], anchors_path: Optional[str] = None) -> str:
    target = os.path.abspath(anchors_path or DEFAULT_SR_ANCHORS_PATH)
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


def _anchor_valid_for_reuse(
    existing: Optional[Dict[str, Any]],
    prev_close: Optional[float],
    split_detected: bool = False,
) -> bool:
    if split_detected or not isinstance(existing, dict):
        return False
    needed = ("anchor_s1", "anchor_s2", "anchor_r1", "anchor_r2", "anchor_sl", "prev_close")
    if not all(_opt_float(existing.get(k)) for k in needed):
        return False
    s2 = float(existing["anchor_s2"])
    s1 = float(existing["anchor_s1"])
    r1 = float(existing["anchor_r1"])
    r2 = float(existing["anchor_r2"])
    if not (0 < s2 < s1 < r1 < r2):
        return False
    ref_old = float(existing["prev_close"])
    ref_new = _opt_float(prev_close)
    if ref_new is not None and ref_new > 0 and ref_old > 0:
        ratio = ref_new / ref_old
        if ratio < 0.65 or ratio > 1.55:
            return False
    return True


def get_or_lock_daily_sr_anchor(
    ticker: str,
    date_et: str,
    prev_close: Optional[float] = None,
    atr_pct: float = 2.5,
    df_history: Optional[pd.DataFrame] = None,
    put_wall: Optional[float] = None,
    call_wall: Optional[float] = None,
    split_detected: bool = False,
    anchors_path: Optional[str] = None,
) -> Dict[str, Any]:
    """获取或锁定单标的当日静态锚点；同日内绝不随现价漂移，仅跨日或拆股对齐时重建。"""
    tk_u = str(ticker or "").strip().upper()
    d_key = str(date_et or resolve_trading_date_et())
    doc = load_daily_sr_anchors(anchors_path)
    if str(doc.get("date_et") or "") != d_key:
        doc = {"version": 1, "date_et": d_key, "updated_at_utc": "", "anchors": {}}
    anchors = doc.setdefault("anchors", {})
    existing = anchors.get(tk_u)

    if _anchor_valid_for_reuse(existing, prev_close=prev_close, split_detected=split_detected):
        return dict(existing)

    prior_df = _prior_history_slice(df_history, d_key)
    ref_use = _opt_float(prev_close)
    if (ref_use is None or ref_use <= 0) and prior_df is not None and not prior_df.empty:
        ref_use = _opt_float(prior_df["Close"].iloc[-1])

    calc = compute_static_sr_levels_with_sources(
        ticker=tk_u,
        ref_price=ref_use,
        atr_pct=atr_pct,
        df_history=prior_df,
        put_wall=put_wall,
        call_wall=call_wall,
    )
    calc["ticker"] = tk_u
    calc["date_et"] = d_key
    calc["locked_at_utc"] = datetime.now(timezone.utc).isoformat()
    anchors[tk_u] = calc
    doc["updated_at_utc"] = calc["locked_at_utc"]
    save_daily_sr_anchors(doc, anchors_path=anchors_path)
    return dict(calc)


def update_daily_sr_anchors_batch(
    items: Iterable[Dict[str, Any]],
    date_et: Optional[str] = None,
    anchors_path: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    批量锁定/复用全市场 79 只标的当日静态锚点（单次读写原子持久化）。
    每条 item 支持键: ticker, prev_close, atr_pct, df_history, put_wall, call_wall, split_detected。
    """
    d_key = str(date_et or resolve_trading_date_et())
    doc = load_daily_sr_anchors(anchors_path)
    changed = False
    if str(doc.get("date_et") or "") != d_key:
        doc = {"version": 1, "date_et": d_key, "updated_at_utc": "", "anchors": {}}
        changed = True
    anchors = doc.setdefault("anchors", {})

    for it in items:
        if not isinstance(it, dict):
            continue
        tk_u = str(it.get("ticker") or "").strip().upper()
        if not tk_u:
            continue
        prev_close = _opt_float(it.get("prev_close"))
        split_detected = bool(it.get("split_detected", False))
        existing = anchors.get(tk_u)
        if _anchor_valid_for_reuse(existing, prev_close=prev_close, split_detected=split_detected):
            continue

        df_h = it.get("df_history")
        prior_df = _prior_history_slice(df_h, d_key)
        ref_use = prev_close
        if (ref_use is None or ref_use <= 0) and prior_df is not None and not prior_df.empty:
            ref_use = _opt_float(prior_df["Close"].iloc[-1])
        calc = compute_static_sr_levels_with_sources(
            ticker=tk_u,
            ref_price=ref_use,
            atr_pct=float(it.get("atr_pct") or 2.5),
            df_history=prior_df,
            put_wall=_opt_float(it.get("put_wall")),
            call_wall=_opt_float(it.get("call_wall")),
        )
        calc["ticker"] = tk_u
        calc["date_et"] = d_key
        calc["locked_at_utc"] = datetime.now(timezone.utc).isoformat()
        anchors[tk_u] = calc
        changed = True

    if changed:
        doc["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        save_daily_sr_anchors(doc, anchors_path=anchors_path)
    return {k: dict(v) for k, v in anchors.items() if isinstance(v, dict)}


def evaluate_sr_breach_state(
    price: Any,
    anchor: Dict[str, Any],
    is_halt: bool = False,
    is_short_covering: bool = False,
    is_broken: bool = False,
    bias_ma50: float = 0.0,
    vwap: Optional[float] = None,
    base_script: str = "",
) -> Dict[str, Any]:
    """
    对称五态顶底互换状态机 (S/R Flip):
    - INTACT: anchor_s1 <= price <= anchor_r1
    - S1_BREACHED: anchor_s2 <= price < anchor_s1
    - ALL_SUPPORT_BREACHED: price < anchor_s2
    - R1_BROKEN: anchor_r1 < price <= anchor_r2
    - ALL_RESISTANCE_BROKEN: price > anchor_r2
    """
    s1 = round(float(anchor.get("anchor_s1") or anchor.get("s1") or 0.0), 2)
    s2 = round(float(anchor.get("anchor_s2") or anchor.get("s2") or 0.0), 2)
    sl = round(float(anchor.get("anchor_sl") or anchor.get("sl") or (s2 * 0.985 if s2 > 0 else 0.0)), 2)
    r1 = round(float(anchor.get("anchor_r1") or anchor.get("r1") or 0.0), 2)
    r2 = round(float(anchor.get("anchor_r2") or anchor.get("r2") or 0.0), 2)
    px = _opt_float(price)
    if px is None or px <= 0:
        px = _opt_float(anchor.get("prev_close")) or ((s1 + r1) / 2.0 if (s1 > 0 and r1 > 0) else 0.0)

    s1_src = str(anchor.get("s1_source") or SOURCE_ATR_ESTIMATE)
    s2_src = str(anchor.get("s2_source") or SOURCE_ATR_ESTIMATE)
    r1_src = str(anchor.get("r1_source") or SOURCE_ATR_ESTIMATE)
    r2_src = str(anchor.get("r2_source") or SOURCE_ATR_ESTIMATE)
    vwap_f = _opt_float(vwap)
    vwap_str = f"日内 VWAP (${vwap_f:.2f})" if (vwap_f and vwap_f > 0) else "当日VWAP"
    halt_prefix = "【暴跌熔断风控剧本】触发熔断保护期；" if is_halt else ""

    if s2 > 0 and px < s2:
        state = STATE_ALL_SUPPORT_BREACHED
        badge = f"⚠️ 今日原始支撑全线击穿 (原S1:${s1:.2f} / 原S2:${s2:.2f}) · 真空寻底"
        script = (
            f"{halt_prefix}【支撑全线击穿 · 真空寻底】现价已跌破当日全部原始支撑 S1 (${s1:.2f}) / S2 (${s2:.2f})；"
            f"原 S2 (${s2:.2f}) 已反转为上方第一压力（上方前压 R1 (${r1:.2f}) / R2 (${r2:.2f})）；"
            f"绝不左侧猜底，仅认放量收回原 S2 或重夺 {vwap_str} 的右侧确认；防守止损位 ${sl:.2f}"
        )
    elif s1 > 0 and px < s1:
        state = STATE_S1_BREACHED
        badge = f"S1:${s1:.2f} 已击穿转压 ➔ 退守 S2:${s2:.2f}"
        s2_lbl = "估算防线 S2" if s2_src == SOURCE_ATR_ESTIMATE else "S2"
        script = (
            f"{halt_prefix}【S1击穿转压 · 退守下轨】原支撑 S1 (${s1:.2f}) 已有效击穿并转为反抽压力，"
            f"半空严禁盲目接刀，退守 {s2_lbl} (${s2:.2f})；"
            f"反抽优先测试原 S1 (${s1:.2f}) 与 R1 (${r1:.2f})（高阻 R2 (${r2:.2f})）；防守止损位 ${sl:.2f}"
        )
    elif r2 > 0 and px > r2:
        state = STATE_ALL_RESISTANCE_BROKEN
        badge = f"🚀 突破今日全线阻力 (原R1:${r1:.2f} / 原R2:${r2:.2f}) · 趋势延伸"
        script = (
            f"【阻力全线突破 · 移动止盈托管】现价放量突破原 R1 (${r1:.2f}) / R2 (${r2:.2f})；"
            f"原 R2 (${r2:.2f}) 已转为下方强防守底线（原支撑 S1 (${s1:.2f}) / S2 (${s2:.2f})）；"
            f"上方不设假天花板，自动切换为沿 {vwap_str} / EMA9 移动止盈托管，不追高、防假突破回踩原 R2"
        )
    elif r1 > 0 and px > r1:
        state = STATE_R1_BROKEN
        badge = f"R1:${r1:.2f} (已突破 ➔ 转为支撑) · 上看 R2:${r2:.2f}"
        script = (
            f"【R1突破转支撑 · 看高R2】原阻力 R1 (${r1:.2f}) 已突破并反转为下方支撑，"
            f"目标上看原定 R2 (${r2:.2f})（原下轨 S1 (${s1:.2f}) / S2 (${s2:.2f})）；"
            f"不追第一根冲高针，等回踩原 R1 (${r1:.2f}) 或 {vwap_str} 企稳后再顺势关注"
        )
    else:
        state = STATE_INTACT
        badge = "今日原始支撑/阻力完好"
        if base_script and (f"S1 (${s1:.2f})" in base_script or is_halt or is_short_covering or is_broken):
            import re
            synced = str(base_script)
            for _lbl, _v in (("S1", s1), ("S2", s2), ("R1", r1), ("R2", r2)):
                synced = re.sub(rf"({_lbl}\s*\(\$)[0-9]+(?:\.[0-9]+)?", rf"\g<1>{_v:.2f}", synced)
            synced = re.sub(
                r"((?:防守止损位|止损位|关键防守位|防守位)\s*\$)[0-9]+(?:\.[0-9]+)?",
                rf"\g<1>{sl:.2f}",
                synced,
            )
            script = synced
        else:
            s_lbl = "估算防线 S1" if s1_src == SOURCE_ATR_ESTIMATE else "支撑 S1"
            r_lbl = "估算压力 R1" if r1_src == SOURCE_ATR_ESTIMATE else "阻力 R1"
            if is_halt:
                script = f"【暴跌熔断风控剧本】触发熔断保护期，下方关注 {s_lbl} (${s1:.2f}) / S2 (${s2:.2f})；反抽 {r_lbl} (${r1:.2f}) / R2 (${r2:.2f}) 慎防诱多；防守止损位 ${sl:.2f}，严禁左侧接刀"
            elif is_broken:
                script = f"【破位防守剧本】均线空头承压，关注 {s_lbl} (${s1:.2f}) / S2 (${s2:.2f})；上方 {r_lbl} (${r1:.2f}) / R2 (${r2:.2f}) 逢高减仓；防守止损位 ${sl:.2f}，严禁左侧接刀"
            elif bias_ma50 > 4.0:
                script = f"【右侧主升剧本】依托 {s_lbl} (${s1:.2f}) / S2 (${s2:.2f}) 防守做多，上看 {r_lbl} (${r1:.2f}) 与扩展目标 R2 (${r2:.2f})；关键防守位 ${sl:.2f}"
            else:
                script = f"【战术推演剧本】下方防守 S1 (${s1:.2f}) / S2 (${s2:.2f})，上方阻力 R1 (${r1:.2f}) / R2 (${r2:.2f})；有效跌破防守止损位 ${sl:.2f} 严格离场"

    return {
        "anchor_s1": s1,
        "anchor_s2": s2,
        "anchor_sl": sl,
        "anchor_r1": r1,
        "anchor_r2": r2,
        "s1": s1,
        "s2": s2,
        "sl": sl,
        "r1": r1,
        "r2": r2,
        "s1_source": s1_src,
        "s2_source": s2_src,
        "r1_source": r1_src,
        "r2_source": r2_src,
        "sr_breach_state": state,
        "sr_status_badge": badge,
        "sr_anchor_locked": True,
        "scenario_script": script,
    }


def _is_vix_backwardation_active(vix_regime: Optional[Dict[str, Any]]) -> bool:
    vr = vix_regime if isinstance(vix_regime, dict) else {}
    if bool(vr.get("vix_hard_brake", False)):
        return True
    if str(vr.get("vix_state") or "").upper() == "BACKWARDATION_BRAKE":
        return True
    ratio = _opt_float(vr.get("vix_term_ratio"))
    if ratio is not None and ratio >= 1.00:
        return True
    return False


def suppress_resonance_badge(
    four_h_div: Any,
    four_h_tag: Any = "div-neutral",
    is_crash_halt: bool = False,
    is_cooling_halt: bool = False,
    vix_regime: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    当标的处于暴跌熔断保护期（Crash_3d / ATR 自适应暴跌熔断）或大盘处于 VIX 期限倒挂硬刹车时，
    强制降级压制任何「见底信号 / 底背离拐点 / 抄底共振」绿色标签，消除视觉左右互搏。
    """
    orig_text = str(four_h_div or "➖ 4H 均线震荡纠缠").strip() or "➖ 4H 均线震荡纠缠"
    orig_tag = str(four_h_tag or "div-neutral").strip() or "div-neutral"

    already_suppressed = (
        orig_tag == "div-suppressed"
        or "4H 潜在背离" in orig_text
        or "熔断锁定·未确认" in orig_text
        or "VIX倒挂压制" in orig_text
    )

    is_bull_bottom_signal = (
        already_suppressed
        or orig_tag == "div-bull"
        or "见底信号" in orig_text
        or "底背离拐点" in orig_text
        or "抄底共振" in orig_text
    )

    if is_bull_bottom_signal:
        if bool(is_crash_halt) or bool(is_cooling_halt):
            suppressed_txt = "⚠️ 4H 潜在背离 (熔断锁定·未确认)"
            return {
                "four_h_div": suppressed_txt,
                "four_h_tag": "div-suppressed",
                "resonance_badge_text": suppressed_txt,
                "resonance_badge_tag": "div-suppressed",
                "resonance_suppressed": True,
                "resonance_suppress_reason": "CRASH_HALT",
            }
        if _is_vix_backwardation_active(vix_regime):
            suppressed_txt = "⚠️ 4H 潜在背离 (VIX倒挂压制)"
            return {
                "four_h_div": suppressed_txt,
                "four_h_tag": "div-suppressed",
                "resonance_badge_text": suppressed_txt,
                "resonance_badge_tag": "div-suppressed",
                "resonance_suppressed": True,
                "resonance_suppress_reason": "VIX_BACKWARDATION",
            }
        if already_suppressed:
            reason_prev = "CRASH_HALT" if "熔断锁定" in orig_text else ("VIX_BACKWARDATION" if "VIX倒挂" in orig_text else "SUPPRESSED")
            return {
                "four_h_div": orig_text,
                "four_h_tag": "div-suppressed",
                "resonance_badge_text": orig_text,
                "resonance_badge_tag": "div-suppressed",
                "resonance_suppressed": True,
                "resonance_suppress_reason": reason_prev,
            }

    return {
        "four_h_div": orig_text,
        "four_h_tag": orig_tag,
        "resonance_badge_text": orig_text,
        "resonance_badge_tag": orig_tag,
        "resonance_suppressed": False,
        "resonance_suppress_reason": "",
    }
