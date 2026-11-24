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
SOURCE_GAP_PULLBACK = "GAP_PULLBACK"
SOURCE_ATR_ESTIMATE = "ATR_ESTIMATE"

STATE_INTACT = "INTACT"
STATE_S1_BREACHED = "S1_BREACHED"
STATE_ALL_SUPPORT_BREACHED = "ALL_SUPPORT_BREACHED"
STATE_R1_BROKEN = "R1_BROKEN"
STATE_ALL_RESISTANCE_BROKEN = "ALL_RESISTANCE_BROKEN"

_TIER1_LOW_VOL_TICKERS = frozenset({"XAUUSD", "GLD", "TLT"})
_INVERSE_HEDGE_TICKERS = frozenset({"SOXS", "SQQQ", "VIXY"})
_NON_US_TICKERS = frozenset({"KOSPI", "BTC-USD", "XAUUSD"})
_ALL_BREACH_STATES = (
    STATE_INTACT,
    STATE_S1_BREACHED,
    STATE_ALL_SUPPORT_BREACHED,
    STATE_R1_BROKEN,
    STATE_ALL_RESISTANCE_BROKEN,
)


def compute_sr_min_step_pct(atr_pct: float) -> float:
    """
    ATR 自适应最小档位间距防扎堆：
    min_step_pct = min(0.055, max(0.025, 0.45 * atr_f))
    - 1x 低波标的 >= 2.5%
    - 3x 高波杠杆标的约 4.0% ~ 5.5%
    """
    raw = _opt_float(atr_pct)
    if raw is None or raw <= 0:
        atr_f = 0.025
    else:
        atr_f = (raw / 100.0) if raw > 1.0 else raw
    return float(min(0.055, max(0.025, 0.45 * atr_f)))


def compute_sr_max_r2_step_pct(atr_pct: float) -> float:
    """
    真实候选 R2 相对 R1 的最大波段跨度上限（防中段筹码真空一脚飞到远期前高）：
    max_r2_step_pct = min(0.22, max(0.10, 1.5 * atr_f))
    - 低波 1x 标的 R2 距 R1 不超过 10%
    - 高波 3x 杠杆不超过 18% ~ 22%
    """
    raw = _opt_float(atr_pct)
    if raw is None or raw <= 0:
        atr_f = 0.025
    else:
        atr_f = (raw / 100.0) if raw > 1.0 else raw
    return float(min(0.22, max(0.10, 1.5 * atr_f)))


def _synthesize_bounded_r2(
    r1: float,
    atr_dec: float,
    min_step_pct: float,
    max_r2_step_pct: float,
    min_abs_step: float = 0.01,
) -> float:
    """
    在 [R1*(1+min_step_pct), R1*(1+max_r2_step_pct)] 合理波段区间内合成估算二压 R2，
    若区间内存在紧贴估算值的整十/整五心理关口（如 $50.00）则优先吸附，确保 R2 严格 >= R1*(1+min_step_pct)。
    """
    r2_min = max(r1 * (1.0 + min_step_pct), r1 + max(min_abs_step, 0.01))
    r2_max = max(r1 * (1.0 + max_r2_step_pct), r2_min)
    synth_step = max(min_step_pct, min(0.12, 1.0 * atr_dec))
    synth_step = min(synth_step, max_r2_step_pct)
    r2_raw = max(r2_min, min(r2_max, r1 * (1.0 + synth_step)))

    grid = 50.0 if r1 >= 1000.0 else (10.0 if r1 >= 200.0 else (5.0 if r1 >= 25.0 else (1.0 if r1 >= 5.0 else 0.0)))
    if grid > 0:
        k_lo = math.ceil((r2_min - 1e-9) / grid)
        k_hi = math.floor((r2_max + 1e-9) / grid)
        round_cands = [round(k * grid, 2) for k in range(k_lo, k_hi + 1) if r2_min <= k * grid <= r2_max]
        if round_cands:
            best_round = min(round_cands, key=lambda x: abs(x - r2_raw))
            if abs(best_round - r2_raw) / max(r2_raw, 0.01) <= 0.025:
                return round(float(best_round), 2)
    return round(float(r2_raw), 2)


def extract_gap_pullback_support_candidates(
    df_history: Optional[pd.DataFrame],
    ref_price: float,
    atr_pct: float = 2.5,
    prev_close_override: Optional[float] = None,
    swing_high_or_pmh: Optional[float] = None,
    pml: Optional[float] = None,
    is_us_ticker: bool = True,
) -> List[float]:
    """
    大跳空/大阳线缺口回撤候选入池：
    近 2 个交易日内出现向上大跳空 (gap_pct >= max(0.05, 1.2*atr_f)) 或单日巨阳 (day_ret >= max(0.10, 1.8*atr_f)) 时，
    在 (prev_close, active_price) 区间自动注入：
    1) 缺口 0.382 回撤位: swing_anchor_close - (swing_anchor_close - prev_close) * 0.382
    2) 缺口 0.50 半幅回撤位: prev_close + (swing_high_or_pmh - prev_close) * 0.50
    3) 近期有效盘前低点 PML（仅限美股标的且落在区间内）
    """
    if ref_price <= 0:
        return []
    raw_atr = _opt_float(atr_pct)
    atr_f = (raw_atr / 100.0) if (raw_atr is not None and raw_atr > 1.0) else (raw_atr if (raw_atr is not None and raw_atr > 0) else 0.025)
    atr_f = max(0.012, min(0.25, atr_f))
    gap_thresh = max(0.05, 1.2 * atr_f)
    surge_thresh = max(0.10, 1.8 * atr_f)

    triggered_prev_c: Optional[float] = None
    triggered_anchor_c: Optional[float] = None
    triggered_hi: Optional[float] = None

    pmh_f = _opt_float(swing_high_or_pmh)
    prev_ov = _opt_float(prev_close_override)
    if prev_ov is not None and prev_ov > 0 and ref_price > prev_ov:
        ov_ret = (ref_price - prev_ov) / prev_ov
        if ov_ret >= gap_thresh or ov_ret >= surge_thresh:
            triggered_prev_c = prev_ov
            triggered_anchor_c = ref_price
            triggered_hi = max(ref_price, pmh_f) if (pmh_f is not None and pmh_f > 0) else ref_price

    if triggered_prev_c is None and df_history is not None and isinstance(df_history, pd.DataFrame) and len(df_history) >= 2 and "Close" in df_history.columns:
        try:
            tail = df_history.tail(4).copy()
            closes = pd.to_numeric(tail["Close"], errors="coerce").dropna().astype(float).values
            opens = pd.to_numeric(tail["Open"], errors="coerce").fillna(tail["Close"]).astype(float).values if "Open" in tail.columns else closes
            highs = pd.to_numeric(tail["High"], errors="coerce").fillna(tail["Close"]).astype(float).values if "High" in tail.columns else closes
            n_b = min(len(closes), len(opens), len(highs))
            if n_b >= 2:
                # 检查最近 2 个交易日
                check_idxs = [n_b - 1] + ([n_b - 2] if n_b >= 3 else [])
                for idx in check_idxs:
                    pc = float(closes[idx - 1])
                    op = float(opens[idx])
                    cl = float(closes[idx])
                    hi = float(highs[idx])
                    if pc <= 0 or cl <= pc:
                        continue
                    g_pct = (op - pc) / pc
                    d_ret = (cl - pc) / pc
                    if g_pct >= gap_thresh or d_ret >= surge_thresh:
                        triggered_prev_c = pc
                        triggered_anchor_c = max(cl, ref_price) if (idx == n_b - 1 and ref_price >= cl * 0.98) else cl
                        triggered_hi = max(hi, triggered_anchor_c, pmh_f or 0.0)
                        break
        except Exception:
            pass

    if triggered_prev_c is None or triggered_anchor_c is None or triggered_anchor_c <= triggered_prev_c:
        return []

    hi_use = max(triggered_anchor_c, triggered_hi or triggered_anchor_c)
    raw_cands: List[float] = [
        round(triggered_anchor_c - (triggered_anchor_c - triggered_prev_c) * 0.382, 2),
        round(triggered_prev_c + (hi_use - triggered_prev_c) * 0.50, 2),
    ]
    pml_f = _opt_float(pml)
    if is_us_ticker and pml_f is not None and triggered_prev_c < pml_f < ref_price * 0.996:
        raw_cands.append(round(pml_f, 2))

    valid_gap_cands = sorted(
        {c for c in raw_cands if triggered_prev_c < c < ref_price * 0.996},
        reverse=True,
    )
    return valid_gap_cands


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
    extra_support_cands: Optional[Iterable[float]] = None,
    extra_resist_cands: Optional[Iterable[float]] = None,
    gap_prev_close: Optional[float] = None,
    swing_high_or_pmh: Optional[float] = None,
    pml: Optional[float] = None,
) -> Dict[str, Any]:
    """
    计算基于基准价 (`prev_close`) 与真实历史筹码/波段结构的 4 级静态锚点 (`S2 < S1 < ref_price < R1 < R2`)，
    严格区分真实历史筹码 (`REAL_CHIPS`)、缺口回撤 (`GAP_PULLBACK`) 与 ATR/数学估算兜底 (`ATR_ESTIMATE`)，
    并强制执行 ATR 自适应最小档位间距防扎堆 + 大跳空缺口回撤入池。
    """
    tk_u = str(ticker or "").strip().upper()
    ref_f = _opt_float(ref_price)
    if (ref_f is None or ref_f <= 0) and df_history is not None and not df_history.empty and "Close" in df_history.columns:
        ref_f = _opt_float(df_history["Close"].iloc[-1])
    base_px = round(max(0.01, float(ref_f or 100.0)), 2)

    raw_atr_opt = _opt_float(atr_pct)
    atr_dec = (raw_atr_opt / 100.0) if (raw_atr_opt is not None and raw_atr_opt > 1.0) else (raw_atr_opt if (raw_atr_opt is not None and raw_atr_opt > 0) else 0.025)
    atr_dec = max(0.012, min(0.25, atr_dec))
    eff_atr = _compute_eff_atr(atr_dec * 100.0)
    is_tier1 = tk_u in _TIER1_LOW_VOL_TICKERS
    is_us_tk = tk_u not in _NON_US_TICKERS
    t1_mult = float(np.clip(0.50 * eff_atr, 0.008, 0.013)) if is_tier1 else 0.0
    min_step_pct = compute_sr_min_step_pct(atr_dec)
    min_step_pct_eff = min_step_pct if not is_tier1 else max(0.015, min_step_pct * 0.6)

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

    for ex_s in (extra_support_cands or []):
        ex_f = _opt_float(ex_s)
        if ex_f is not None and 0 < ex_f < base_px * 0.996:
            support_real_cands.append(round(ex_f, 2))
    for ex_r in (extra_resist_cands or []):
        ex_f = _opt_float(ex_r)
        if ex_f is not None and base_px * 1.004 < ex_f <= base_px * 1.50:
            resist_real_cands.append(round(ex_f, 2))

    # 规则 B：极端向上大跳空 / 巨阳缺口回撤支撑候选自动入池
    gap_pullback_cands = extract_gap_pullback_support_candidates(
        df_history=df_history,
        ref_price=base_px,
        atr_pct=atr_dec,
        prev_close_override=gap_prev_close,
        swing_high_or_pmh=swing_high_or_pmh,
        pml=pml,
        is_us_ticker=is_us_tk,
    )
    if gap_pullback_cands:
        support_real_cands.extend(gap_pullback_cands)

    def _is_gap_cand(val: float) -> bool:
        return bool(gap_pullback_cands and any(abs(val - g) <= 0.02 for g in gap_pullback_cands))

    min_step = max(0.01, round(base_px * 0.005, 2))
    if is_tier1:
        max_s2_dist = 0.055
        s1_est_default = round(base_px * (1.0 - t1_mult), 2)
        s2_est_default = round(base_px * (1.0 - max(0.022, eff_atr)), 2)
    else:
        max_s2_dist = min(0.085, max(0.048, 1.55 * eff_atr))
        if gap_pullback_cands:
            max_s2_dist = max(max_s2_dist, min(0.20, max(0.14, 1.8 * atr_dec)))
        s1_est_default = round(base_px * (1.0 - max(0.025, 0.75 * eff_atr)), 2)
        s2_est_default = round(base_px * (1.0 - max(0.045, min(max_s2_dist, 1.45 * eff_atr))), 2)

    valid_sup = sorted({
        round(x, 2)
        for x in support_real_cands
        if ((base_px * (1.0 - max_s2_dist)) <= x <= (base_px * 0.995)) or _is_gap_cand(round(x, 2))
    }, reverse=True)

    # S1 选择：优先真实历史筹码/结构位，否则 ATR 估算
    if is_tier1:
        # 低波防御工具若无 2% 内的真实结构位，使用窄幅 ATR 估算防线并诚实标注 ATR_ESTIMATE
        tight_sup = [x for x in valid_sup if (base_px * 0.975) <= x <= (base_px * 0.995)]
        if tight_sup:
            s1 = tight_sup[0]
            s1_source = SOURCE_GAP_PULLBACK if _is_gap_cand(s1) else SOURCE_REAL_CHIPS
        else:
            s1 = s1_est_default
            s1_source = SOURCE_ATR_ESTIMATE
    elif valid_sup:
        # 从近期真实支撑中挑选合理波段位（优先距现价至少 1.2% 的真实结构位，否则取最近真实位）
        deep_enough = [x for x in valid_sup if x <= base_px * 0.988]
        s1 = deep_enough[0] if deep_enough else valid_sup[0]
        s1_source = SOURCE_GAP_PULLBACK if _is_gap_cand(s1) else SOURCE_REAL_CHIPS
    else:
        s1 = s1_est_default
        s1_source = SOURCE_ATR_ESTIMATE

    if s1 >= base_px:
        s1 = round(base_px - min_step, 2)
        s1_source = SOURCE_ATR_ESTIMATE

    # 规则 A：S2 选择强制执行 ATR 自适应最小档位间距 (S1 - cand) / S1 >= min_step_pct_eff，跳过近邻扎堆候选
    deeper_sup = [
        x for x in valid_sup
        if x < s1 and ((s1 - x) / max(s1, 0.01)) >= min_step_pct_eff
    ]

    if deeper_sup:
        s2 = deeper_sup[0]
        s2_source = SOURCE_GAP_PULLBACK if _is_gap_cand(s2) else SOURCE_REAL_CHIPS
    else:
        synth_step = max(min_step_pct_eff, min(0.08, 0.80 * atr_dec))
        s2 = round(s1 * (1.0 - synth_step), 2)
        if s2 >= s1:
            s2 = round(s1 - max(min_step, round(s1 * min_step_pct_eff, 2)), 2)
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

    # 规则 A（阻力对称防扎堆）+ 规则 C（远期离群 R2 上限收紧）：
    # 真实候选 R2 必须落在 [R1*(1+min_step_pct_eff), R1*(1+max_r2_step_pct)] 合理波段区间内
    max_r2_step_pct = compute_sr_max_r2_step_pct(atr_dec)
    r2_min_bound = r1 * (1.0 + min_step_pct_eff)
    r2_max_bound = r1 * (1.0 + max_r2_step_pct)
    higher_res = [
        x for x in valid_res
        if r2_min_bound <= x <= r2_max_bound
    ]
    if higher_res:
        r2 = higher_res[-1] if len(higher_res) > 1 and higher_res[-1] <= base_px * 1.35 else higher_res[0]
        r2_source = SOURCE_REAL_CHIPS
    else:
        r2 = _synthesize_bounded_r2(
            r1=r1,
            atr_dec=atr_dec,
            min_step_pct=min_step_pct_eff,
            max_r2_step_pct=max_r2_step_pct,
            min_abs_step=min_step,
        )
        r2_source = SOURCE_ATR_ESTIMATE

    if r2 <= r1:
        r2 = round(max(r1 * (1.0 + min_step_pct_eff), r1 + max(min_step, 0.01)), 2)
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
        "min_step_pct": round(min_step_pct_eff, 4),
        "max_r2_step_pct": round(max_r2_step_pct, 4),
        "gap_pullback_candidates": gap_pullback_cands,
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
    # V5.3.1 防扎堆 + 远期离群 R2 校验：若旧缓存缺少 min_step_pct / max_r2_step_pct 或超出合理区间，触发单次安全重建
    if existing.get("min_step_pct") is None or existing.get("max_r2_step_pct") is None:
        return False
    ms = float(existing.get("min_step_pct") or 0.025)
    mr2 = float(existing.get("max_r2_step_pct") or 0.22)
    if ((s1 - s2) / max(s1, 0.01)) < (ms * 0.92) or ((r2 - r1) / max(r1, 0.01)) < (ms * 0.92):
        return False
    if ((r2 - r1) / max(r1, 0.01)) > (mr2 * 1.03):
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
    gap_prev_close: Optional[float] = None,
    swing_high_or_pmh: Optional[float] = None,
    pml: Optional[float] = None,
) -> Dict[str, Any]:
    """获取或锁定单标的当日静态锚点；同日内绝不随现价漂移，仅跨日或拆股对齐时重建。"""
    tk_u = str(ticker or "").strip().upper()
    d_key = str(date_et or resolve_trading_date_et())
    doc = load_daily_sr_anchors(anchors_path)
    if str(doc.get("date_et") or "") != d_key:
        doc = {"version": 2, "date_et": d_key, "updated_at_utc": "", "anchors": {}}
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
        gap_prev_close=gap_prev_close,
        swing_high_or_pmh=swing_high_or_pmh,
        pml=pml,
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
    批量锁定/复用全市场标的当日静态锚点（单次读写原子持久化）。
    每条 item 支持键: ticker, prev_close, atr_pct, df_history, put_wall, call_wall, split_detected, gap_prev_close, swing_high_or_pmh, pml。
    """
    d_key = str(date_et or resolve_trading_date_et())
    doc = load_daily_sr_anchors(anchors_path)
    changed = False
    if str(doc.get("date_et") or "") != d_key:
        doc = {"version": 2, "date_et": d_key, "updated_at_utc": "", "anchors": {}}
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
            gap_prev_close=_opt_float(it.get("gap_prev_close")),
            swing_high_or_pmh=_opt_float(it.get("swing_high_or_pmh")),
            pml=_opt_float(it.get("pml")),
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


def _fmt_sr_price(val: float, is_pts: bool = False) -> str:
    if is_pts:
        return f"{val:,.2f}"
    return f"${val:.2f}"


def _build_suppression_reason_clause(
    ticker: str = "",
    is_inverse: bool = False,
    is_daily_breakdown: bool = False,
    is_vix_backwardation: bool = False,
    is_bear_dominant: bool = False,
    is_inverse_decay: bool = False,
) -> Tuple[str, str]:
    """
    按标的类别动态拼接真实触发的 4H 压制原因（彻底消除正反/指数串味）：
    - 非反向标的 (KOSPI/QQQ/SOXL/个股)：严禁出现「反向衰减 / 反向波动损耗」；
    - 反向对冲 ETF (SOXS/SQQQ/VIXY)：严禁出现「VIX倒挂 / VIX倒挂压制」。
    """
    tk_u = str(ticker or "").strip().upper()
    inv = bool(is_inverse or (tk_u in _INVERSE_HEDGE_TICKERS))
    reasons: List[str] = []
    short_tags: List[str] = []

    if is_daily_breakdown:
        reasons.append("日线破位EMA5")
        short_tags.append("日线破位")

    if inv:
        if is_inverse_decay or not reasons:
            reasons.append("反向波动损耗")
            short_tags.append("反向衰减")
        if is_bear_dominant:
            reasons.append("空头占优")
            short_tags.append("空头占优")
    else:
        if is_vix_backwardation:
            reasons.append("VIX倒挂压制")
            short_tags.append("VIX倒挂")
        if is_bear_dominant:
            reasons.append("空头占优")
            short_tags.append("空头占优")

    if not reasons:
        reasons.append("空头占优")
        short_tags.append("空头占优")

    uniq_reasons = list(dict.fromkeys(reasons))
    uniq_short = list(dict.fromkeys(short_tags))[:2]
    return "/".join(uniq_reasons), "/".join(uniq_short)


def evaluate_sr_breach_state(
    price: Any,
    anchor: Dict[str, Any],
    is_halt: bool = False,
    is_short_covering: bool = False,
    is_broken: bool = False,
    bias_ma50: float = 0.0,
    vwap: Optional[float] = None,
    base_script: str = "",
    bear_suppressed: bool = False,
    swing_tp: Optional[float] = None,
    swing_sl: Optional[float] = None,
    atr_pct: Optional[float] = None,
    is_daily_breakdown: bool = False,
    is_inverse_decay: bool = False,
    price_unit: str = "usd",
    ticker: str = "",
    is_vix_backwardation: Optional[bool] = None,
    is_bear_dominant: Optional[bool] = None,
    is_inverse: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    对称五态顶底互换状态机 (S/R Flip) + 体制门禁 / 离群 R2 收口 / 止损一致性保护 + 按标的类别动态压制文案:
    - INTACT: anchor_s1 <= price <= anchor_r1
    - S1_BREACHED: anchor_s2 <= price < anchor_s1
    - ALL_SUPPORT_BREACHED: price < anchor_s2
    - R1_BROKEN: anchor_r1 < price <= anchor_r2
    - ALL_RESISTANCE_BROKEN: price > anchor_r2
    """
    tk_up = str(ticker or anchor.get("ticker") or "").strip().upper()
    unit_norm = str(price_unit or anchor.get("price_unit") or "usd").strip().lower()
    is_pts = bool(unit_norm in ("pts", "pt", "points", "index") or tk_up == "KOSPI" or bool(anchor.get("is_index", False)))
    is_inv_asset = bool(is_inverse if is_inverse is not None else (tk_up in _INVERSE_HEDGE_TICKERS))
    fp = lambda v: _fmt_sr_price(float(v), is_pts)

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
    vwap_str = f"日内 VWAP ({fp(vwap_f)})" if (vwap_f and vwap_f > 0) else "当日VWAP"
    halt_prefix = "【暴跌熔断风控剧本】触发熔断保护期；" if is_halt else ""

    # 离群 R2 波段合理跨度保护（防止远期前高脱节当成短线目标）
    atr_raw = _opt_float(atr_pct)
    atr_f = (atr_raw / 100.0) if (atr_raw is not None and atr_raw > 1.0) else (atr_raw if (atr_raw is not None and atr_raw > 0) else 0.025)
    atr_f = max(0.012, min(0.15, atr_f))
    swing_tp_f = _opt_float(swing_tp) or 0.0
    swing_sl_f = _opt_float(swing_sl) or 0.0
    r2_is_far_historical = False
    r2_swing_cap = r2
    if px > 0 and r2 > px:
        max_r2_by_atr = px * (1.0 + max(0.10, 3.5 * atr_f))
        r2_gain = (r2 - px) / px
        tp_gain = ((swing_tp_f - px) / px) if (swing_tp_f > px) else 0.0
        far_by_atr = r2 > max_r2_by_atr
        far_by_tp = (tp_gain > 0.01 and r2_gain > max(0.10, 2.5 * tp_gain))
        if far_by_atr or far_by_tp:
            r2_is_far_historical = True
            r2_swing_cap = round(min(r2, max(swing_tp_f, px * (1.0 + max(0.08, 2.5 * atr_f)))), 2)

    valid_swing_sl = swing_sl_f if (swing_sl_f > 0 and (px <= 0 or swing_sl_f < px)) else 0.0
    # V5.3.1 硬防呆锁：4H 剧本文案中的「防守止损位」严禁 >= active_price (px)
    # 当处于全线跌穿态 (px < s2) 或原始静态 sl >= px 时，强制对齐顶部动态 swing_sl（无有效 swing_sl 时按动态 ATR 合成）
    if px > 0 and ((s2 > 0 and px < s2) or sl >= px):
        if valid_swing_sl > 0:
            script_sl = round(valid_swing_sl, 2)
        else:
            dyn_sl_step = max(0.025, min(0.08, 1.0 * atr_f))
            script_sl = round(min(px * (1.0 - dyn_sl_step), px - max(0.01, round(px * 0.005, 2))), 2)
    else:
        script_sl = round(max(sl, valid_swing_sl), 2) if valid_swing_sl > 0 else sl
        if px > 0 and script_sl >= px:
            script_sl = round(valid_swing_sl, 2) if valid_swing_sl > 0 else round(
                min(px * (1.0 - max(0.025, min(0.08, 1.0 * atr_f))), px - max(0.01, round(px * 0.005, 2))),
                2,
            )
    eff_sl = script_sl
    s1_below_swing_sl = bool(valid_swing_sl > 0 and s1 > 0 and s1 < valid_swing_sl)

    eff_vix_back = bool(
        False
        if is_inv_asset
        else (is_vix_backwardation if is_vix_backwardation is not None else bool(bear_suppressed))
    )
    eff_bear_dom = bool(is_bear_dominant if is_bear_dominant is not None else bool(bear_suppressed))
    eff_inv_decay = bool(is_inverse_decay) if is_inv_asset else False
    offensive_veto = bool(bear_suppressed or is_daily_breakdown or eff_inv_decay)

    sup_reason_full, sup_reason_short = _build_suppression_reason_clause(
        ticker=tk_up,
        is_inverse=is_inv_asset,
        is_daily_breakdown=bool(is_daily_breakdown),
        is_vix_backwardation=eff_vix_back,
        is_bear_dominant=eff_bear_dom,
        is_inverse_decay=eff_inv_decay,
    )

    bear_suppressed_r1_break = False

    if s2 > 0 and px < s2:
        state = STATE_ALL_SUPPORT_BREACHED
        badge = f"⚠️ 今日原定支撑全线跌穿 (原S1:{fp(s1)} / 原S2:{fp(s2)}) · 下方无真实支撑，别猜底"
        script = (
            f"{halt_prefix}【今日原定支撑全线跌穿 · 下方无真实支撑，别猜底】现价已跌破当日全部原定支撑 S1 ({fp(s1)}) / S2 ({fp(s2)})；"
            f"原 S2 ({fp(s2)}) 已反转为上方第一压力（上方前压 R1 ({fp(r1)}) / R2 ({fp(r2)})）；"
            f"严禁左侧猜底，仅认放量收回原 S2 或重夺 {vwap_str} 的右侧确认；防守止损位 {fp(eff_sl)}"
        )
    elif s1 > 0 and px < s1:
        state = STATE_S1_BREACHED
        badge = f"S1:{fp(s1)} 已击穿转压 ➔ 退守 S2:{fp(s2)}"
        s2_lbl = "估算防线 S2" if s2_src == SOURCE_ATR_ESTIMATE else ("缺口回撤 S2" if s2_src == SOURCE_GAP_PULLBACK else "S2")
        script = (
            f"{halt_prefix}【S1击穿转压 · 退守下轨】原支撑 S1 ({fp(s1)}) 已有效击穿并转为反抽压力，"
            f"半空严禁盲目接刀，退守 {s2_lbl} ({fp(s2)})；"
            f"反抽优先测试原 S1 ({fp(s1)}) 与 R1 ({fp(r1)})（高阻 R2 ({fp(r2)})）；防守止损位 {fp(eff_sl)}"
        )
    elif r2 > 0 and px > r2:
        state = STATE_ALL_RESISTANCE_BROKEN
        if offensive_veto:
            bear_suppressed_r1_break = True
            badge = f"⚠️ 跳空越过旧R1/R2 (原R1:{fp(r1)} / 原R2:{fp(r2)}) · 仍受{sup_reason_short}压制"
            script = (
                f"【跳空越过旧R1/R2 · 仍受{sup_reason_short}压制】原静态 R1 ({fp(r1)}) / R2 ({fp(r2)}) 已被跳空越过，"
                f"但当前受{sup_reason_full}约束，暂不按多头突破追高；"
                f"短线以 {vwap_str} 为多空分水岭，风控压制解除前严禁盲目追涨"
            )
        else:
            badge = f"🚀 突破今日全线阻力 (原R1:{fp(r1)} / 原R2:{fp(r2)}) · 趋势延伸"
            if valid_swing_sl > 0 and r2 < valid_swing_sl:
                r2_def_clause = f"以波段止损线 ({fp(valid_swing_sl)}) 为硬防守底线（下方原 R2 ({fp(r2)}) 仅为次级参考，不可破止损硬扛）"
            else:
                r2_def_clause = f"原 R2 ({fp(r2)}) 已转为下方强防守底线（原支撑 S1 ({fp(s1)}) / S2 ({fp(s2)})）"
            script = (
                f"【阻力全线突破 · 移动止盈托管】现价放量突破原 R1 ({fp(r1)}) / R2 ({fp(r2)})；"
                f"{r2_def_clause}；"
                f"上方不设假天花板，自动切换为沿 {vwap_str} / EMA9 移动止盈托管，不追高、防假突破"
            )
    elif r1 > 0 and px > r1:
        state = STATE_R1_BROKEN
        if offensive_veto:
            bear_suppressed_r1_break = True
            r2_note = f"（远期前高参考 {fp(r2)}，非短线目标）" if r2_is_far_historical else ""
            badge = f"⚠️ 跳空越过旧R1:{fp(r1)} · 仍受{sup_reason_short}压制"
            script = (
                f"【跳空越过旧R1 · 仍受{sup_reason_short}压制】原静态 R1 ({fp(r1)}) 已被跳空越过，"
                f"但当前受{sup_reason_full}约束，暂不按多头突破追高{r2_note}；"
                f"短线以 {vwap_str} 为多空分水岭，反抽阻力区优先逢高降险，风控压制解除前严禁盲目看高 R2"
            )
        else:
            r2_disp_str = (
                f"波段目标 {fp(r2_swing_cap)}（远期前高参考 {fp(r2)}，非短线目标）"
                if r2_is_far_historical
                else f"原定 R2 ({fp(r2)})"
            )
            badge = (
                f"R1:{fp(r1)} (已突破 ➔ 转为支撑) · 波段目标:{fp(r2_swing_cap)}"
                if r2_is_far_historical
                else f"R1:{fp(r1)} (已突破 ➔ 转为支撑) · 上看 R2:{fp(r2)}"
            )
            # 点位一致性校验：严禁推荐低于波段防守止损 (swing_sl) 的原 R1 作为回踩买点
            if valid_swing_sl > 0 and r1 < valid_swing_sl:
                pullback_clause = (
                    f"不追第一根冲高针，等回踩 {vwap_str} 或波段防守区（止损线 {fp(valid_swing_sl)} 上方）企稳后再顺势关注"
                    f"（下方原 R1 {fp(r1)} 仅为破位后次级参考，不可跌破上方止损线硬扛）"
                )
            else:
                pullback_clause = f"不追第一根冲高针，等回踩原 R1 ({fp(r1)}) 或 {vwap_str} 企稳后再顺势关注"
            script = (
                f"【R1突破转支撑 · 看高R2】原阻力 R1 ({fp(r1)}) 已突破并反转为下方支撑，"
                f"目标上看{r2_disp_str}（原下轨 S1 ({fp(s1)}) / S2 ({fp(s2)})）；"
                f"{pullback_clause}"
            )
    else:
        state = STATE_INTACT
        badge = "今日原始支撑/阻力完好"
        s_lbl = "估算防线 S1" if s1_src == SOURCE_ATR_ESTIMATE else ("缺口回撤 S1" if s1_src == SOURCE_GAP_PULLBACK else "支撑 S1")
        s2_tag_txt = f"缺口回撤 S2 ({fp(s2)})" if s2_src == SOURCE_GAP_PULLBACK else f"S2 ({fp(s2)})"
        r_lbl = "估算压力 R1" if r1_src == SOURCE_ATR_ESTIMATE else "阻力 R1"
        r2_intact_str = (
            f"波段上沿 {fp(r2_swing_cap)}（远期前高参考 {fp(r2)}，非短线目标）"
            if r2_is_far_historical
            else f"R2 ({fp(r2)})"
        )
        base_has_bull_offense = bool(
            base_script and any(k in str(base_script) for k in ("右侧主升", "做多剧本", "逢低做多", "防守做多"))
        )
        if is_halt and not is_short_covering:
            script = (
                f"【暴跌熔断风控剧本】触发熔断保护期，下方关注 {s_lbl} ({fp(s1)}) / {s2_tag_txt}；"
                f"反抽 {r_lbl} ({fp(r1)}) / {r2_intact_str} 慎防诱多；防守止损位 {fp(eff_sl)}，严禁左侧接刀"
            )
        elif is_broken:
            script = (
                f"【破位防守剧本】均线空头承压，关注 {s_lbl} ({fp(s1)}) / {s2_tag_txt}；"
                f"上方 {r_lbl} ({fp(r1)}) / {r2_intact_str} 逢高减仓；防守止损位 {fp(eff_sl)}，严禁左侧接刀"
            )
        elif offensive_veto:
            bear_suppressed_r1_break = True
            if s1_below_swing_sl:
                sl_tail = (
                    f"上方防守止损线 {fp(valid_swing_sl)}（下方 {s_lbl} ({fp(s1)}) / {s2_tag_txt} 仅为破位后次级参考，不可跌破上方止损线硬扛）"
                )
            else:
                sl_tail = f"下方观察 {s_lbl} ({fp(s1)}) / {s2_tag_txt}，跌破防守止损位 {fp(eff_sl)} 严格离场"
            script = (
                f"【跳空/滞后均线受大级别压制】当前受{sup_reason_full}约束，暂不按多头突破追高；"
                f"以 {vwap_str} 为短线分水岭，反抽 {r_lbl} ({fp(r1)})（{r2_intact_str}）优先逢高降险；{sl_tail}"
            )
        elif (
            base_script
            and (not is_pts)
            and (not s1_below_swing_sl)
            and (not r2_is_far_historical)
            and (not (offensive_veto and base_has_bull_offense))
            and (s2_src != SOURCE_GAP_PULLBACK)
            and (f"S1 (${s1:.2f})" in base_script or is_short_covering)
        ):
            import re
            synced = str(base_script)
            for _lbl, _v in (("S1", s1), ("S2", s2), ("R1", r1), ("R2", r2)):
                synced = re.sub(rf"({_lbl}\s*\(\$)[0-9]+(?:\.[0-9]+)?", rf"\g<1>{_v:.2f}", synced)
            synced = re.sub(
                r"((?:防守止损位|止损位|关键防守位|防守位)\s*\$)[0-9]+(?:\.[0-9]+)?",
                rf"\g<1>{eff_sl:.2f}",
                synced,
            )
            script = synced
        else:
            if s1_below_swing_sl:
                if vwap_f and vwap_f >= valid_swing_sl and (px <= 0 or vwap_f <= px * 1.03):
                    sup_clause = (
                        f"依托 {vwap_str} 与波段止损线 ({fp(valid_swing_sl)}) 防守"
                        f"（下方 {s_lbl} ({fp(s1)}) / {s2_tag_txt} 仅为破位后次级参考，不可跌破上方止损线硬扛）"
                    )
                else:
                    sup_clause = (
                        f"以波段止损线 ({fp(valid_swing_sl)}) 为硬防守底线"
                        f"（下方 {s_lbl} ({fp(s1)}) / {s2_tag_txt} 仅为破位后次级参考，不可跌破上方止损线硬扛）"
                    )
                if is_short_covering:
                    script = f"【空头回补剧本】{sup_clause}；上看 {r_lbl} ({fp(r1)}) 空头回补反抽(不过就跑)"
                elif bias_ma50 > 4.0:
                    script = f"【右侧主升剧本】{sup_clause}，上看 {r_lbl} ({fp(r1)}) 与 {r2_intact_str}"
                else:
                    script = f"【战术推演剧本】{sup_clause}，上方阻力 {r_lbl} ({fp(r1)}) / {r2_intact_str}"
            else:
                if is_short_covering:
                    script = (
                        f"【空头回补剧本】依托 {s_lbl} ({fp(s1)}) / {s2_tag_txt} 博反抽，"
                        f"上看 {r_lbl} ({fp(r1)}) 空头回补反抽(不过就跑)；击穿防守止损位 {fp(eff_sl)} 严格离场"
                    )
                elif bias_ma50 > 4.0:
                    script = (
                        f"【右侧主升剧本】依托 {s_lbl} ({fp(s1)}) / {s2_tag_txt} 防守做多，"
                        f"上看 {r_lbl} ({fp(r1)}) 与扩展目标 {r2_intact_str}；关键防守位 {fp(eff_sl)}"
                    )
                else:
                    script = (
                        f"【战术推演剧本】下方防守 {s_lbl} ({fp(s1)}) / {s2_tag_txt}，"
                        f"上方阻力 {r_lbl} ({fp(r1)}) / {r2_intact_str}；有效跌破防守止损位 {fp(eff_sl)} 严格离场"
                    )

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
        "bear_suppressed_r1_break": bear_suppressed_r1_break,
        "r2_is_far_historical": r2_is_far_historical,
        "r2_swing_cap": r2_swing_cap,
        "script_stop_loss": eff_sl,
        "suppression_reason_text": sup_reason_full if bear_suppressed_r1_break else "",
        "suppression_short_tag": sup_reason_short if bear_suppressed_r1_break else "",
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
    is_daily_breakdown: bool = False,
    is_inverse_decay_veto: bool = False,
    is_bear_dominant: bool = False,
    ticker: str = "",
    is_inverse: bool = False,
) -> Dict[str, Any]:
    """
    4H 共振徽标硬门禁（按标的类别动态拼接真实压制原因，零串味）：
    - 非反向标的 (KOSPI/QQQ/SOXL/个股)：绝不出现「反向衰减 / 反向波动损耗」；
    - 反向对冲 ETF (SOXS/SQQQ/VIXY)：绝不把「VIX倒挂压制」作为利空压制原因，改用「日线破位EMA5 / 反向波动损耗 / 空头占优」。
    """
    orig_text = str(four_h_div or "➖ 4H 均线震荡纠缠").strip() or "➖ 4H 均线震荡纠缠"
    orig_tag = str(four_h_tag or "div-neutral").strip() or "div-neutral"
    tk_u = str(ticker or "").strip().upper()
    inv_asset = bool(is_inverse or (tk_u in _INVERSE_HEDGE_TICKERS) or bool(is_inverse_decay_veto))

    already_bottom_suppressed = (
        "4H 潜在背离" in orig_text
        or "熔断锁定·未确认" in orig_text
    )
    already_trend_suppressed = (
        "4H 滞后均线" in orig_text
    )
    already_suppressed = (
        orig_tag == "div-suppressed"
        or already_bottom_suppressed
        or already_trend_suppressed
        or "VIX倒挂压制" in orig_text
    )

    is_bull_bottom_signal = (
        already_bottom_suppressed
        or orig_tag == "div-bull"
        or "见底信号" in orig_text
        or "底背离拐点" in orig_text
        or "抄底共振" in orig_text
        or (already_suppressed and not already_trend_suppressed)
    )

    is_bull_trend_signal = (
        already_trend_suppressed
        or orig_tag == "div-trend"
        or "多头共振" in orig_text
        or "右侧主升" in orig_text
    )

    vix_back = False if inv_asset else _is_vix_backwardation_active(vix_regime)

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
        if vix_back:
            suppressed_txt = "⚠️ 4H 潜在背离 (VIX倒挂压制)"
            return {
                "four_h_div": suppressed_txt,
                "four_h_tag": "div-suppressed",
                "resonance_badge_text": suppressed_txt,
                "resonance_badge_tag": "div-suppressed",
                "resonance_suppressed": True,
                "resonance_suppress_reason": "VIX_BACKWARDATION",
            }
        if (inv_asset and bool(is_inverse_decay_veto)) or bool(is_daily_breakdown) or bool(is_bear_dominant):
            if inv_asset:
                reason_lbl = (
                    "日线破位EMA5/反向波动损耗"
                    if (is_daily_breakdown and is_inverse_decay_veto)
                    else ("日线破位EMA5压制" if is_daily_breakdown else ("反向波动损耗压制" if is_inverse_decay_veto else "空头占优压制"))
                )
                reason_code = "DAILY_BREAKDOWN" if is_daily_breakdown else ("INVERSE_DECAY" if is_inverse_decay_veto else "BEAR_DOMINANT")
            else:
                reason_lbl = "日线破位EMA5压制" if is_daily_breakdown else "空头占优压制"
                reason_code = "DAILY_BREAKDOWN" if is_daily_breakdown else "BEAR_DOMINANT"
            suppressed_txt = f"⚠️ 4H 潜在背离 ({reason_lbl})"
            return {
                "four_h_div": suppressed_txt,
                "four_h_tag": "div-suppressed",
                "resonance_badge_text": suppressed_txt,
                "resonance_badge_tag": "div-suppressed",
                "resonance_suppressed": True,
                "resonance_suppress_reason": reason_code,
            }
        if already_suppressed:
            clean_txt = orig_text
            if inv_asset and "VIX倒挂" in clean_txt:
                clean_txt = "⚠️ 4H 潜在背离 (反向波动损耗压制)"
            elif (not inv_asset) and ("反向衰减" in clean_txt or "反向波动损耗" in clean_txt):
                clean_txt = "⚠️ 4H 潜在背离 (空头占优压制)"
            reason_prev = "CRASH_HALT" if "熔断锁定" in clean_txt else ("VIX_BACKWARDATION" if "VIX倒挂" in clean_txt else "SUPPRESSED")
            return {
                "four_h_div": clean_txt,
                "four_h_tag": "div-suppressed",
                "resonance_badge_text": clean_txt,
                "resonance_badge_tag": "div-suppressed",
                "resonance_suppressed": True,
                "resonance_suppress_reason": reason_prev,
            }

    if is_bull_trend_signal:
        if bool(is_crash_halt) or bool(is_cooling_halt):
            suppressed_txt = "⚠️ 4H 滞后均线 (熔断锁定·严禁追多)"
            reason_code = "CRASH_HALT"
        elif inv_asset and (bool(is_daily_breakdown) or bool(is_inverse_decay_veto)):
            if bool(is_daily_breakdown) and bool(is_inverse_decay_veto):
                suppressed_txt = "⚠️ 4H 滞后均线 (日线破位EMA5·反向波动损耗)"
                reason_code = "INVERSE_DECAY"
            elif bool(is_daily_breakdown):
                suppressed_txt = "⚠️ 4H 滞后均线 (日线破位EMA5压制)"
                reason_code = "DAILY_BREAKDOWN"
            else:
                suppressed_txt = "⚠️ 4H 滞后均线 (反向波动损耗·防衰减压制)"
                reason_code = "INVERSE_DECAY"
        elif bool(is_daily_breakdown):
            suppressed_txt = "⚠️ 4H 滞后均线 (日线破位EMA5压制)"
            reason_code = "DAILY_BREAKDOWN"
        elif vix_back:
            suppressed_txt = "⚠️ 4H 滞后均线 (VIX倒挂压制)"
            reason_code = "VIX_BACKWARDATION"
        elif bool(is_bear_dominant):
            suppressed_txt = "⚠️ 4H 滞后均线 (空头占优压制)"
            reason_code = "BEAR_DOMINANT"
        elif already_trend_suppressed:
            suppressed_txt = orig_text
            if inv_asset and "VIX倒挂" in suppressed_txt:
                suppressed_txt = "⚠️ 4H 滞后均线 (反向波动损耗·防衰减压制)"
            elif (not inv_asset) and ("反向衰减" in suppressed_txt or "反向波动损耗" in suppressed_txt):
                suppressed_txt = "⚠️ 4H 滞后均线 (空头占优压制)"
            reason_code = "SUPPRESSED"
        else:
            suppressed_txt = ""
            reason_code = ""

        if suppressed_txt:
            return {
                "four_h_div": suppressed_txt,
                "four_h_tag": "div-suppressed",
                "resonance_badge_text": suppressed_txt,
                "resonance_badge_tag": "div-suppressed",
                "resonance_suppressed": True,
                "resonance_suppress_reason": reason_code,
            }

    return {
        "four_h_div": orig_text,
        "four_h_tag": orig_tag,
        "resonance_badge_text": orig_text,
        "resonance_badge_tag": orig_tag,
        "resonance_suppressed": False,
        "resonance_suppress_reason": "",
    }
