# -*- coding: utf-8 -*-
"""
V5.7 灵活版 KOL 狙击引擎 — 52 日纯离线防穿越全维度对照与消融回测 (run_v57_backtest.py)
严格隔离：绝不改动生产代码、绝不触碰任何 data_cache/*ledger*.json。
"""

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import backtest_v55_kol_60d as bt55
import copy_trade_engine as cte
import copy_trade_kol_engine as kol

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
REPORT_V57_MD = os.path.join(REPORTS_DIR, "v57_backtest_60d.md")
REPORT_V57_JSON = os.path.join(REPORTS_DIR, "v57_backtest_60d.json")

V57_SECTOR_AND_3X_ETFS = frozenset({
    "SOXX", "SOXL", "SOXS", "QQQ", "TQQQ", "SQQQ", "KSTR",
})
# 反向/波动率 ETF (SOXS / SQQQ / VIXY) 严禁做空（实盘高借券费 + 暴跌逼空风险）；看空半导体/纳指应直接做多 SOXS/SQQQ 或做空 SOXX/QQQ
V57_NO_SHORT_INVERSE_ETFS = frozenset({"SOXS", "SQQQ", "VIXY"})
V57_EXTENDED_LIQUID_WHITELIST = frozenset({
    "SOXX", "SOXL", "SOXS", "QQQ", "TQQQ", "SQQQ", "SPY", "SMH",
    "NVDA", "TSM", "AMD", "AVGO", "MU", "TSLA", "META", "MSFT", "AAPL", "AMZN", "GOOGL", "PLTR",
})
V57_PARENT_INDEX_MAP = {
    "SOXL": "SOXX",
    "SOXS": "SOXX",
    "TQQQ": "QQQ",
    "SQQQ": "QQQ",
}
V57_MARK_MAINLINE_TICKERS = frozenset({
    "NVDA", "TSM", "AVGO", "MU", "AMD", "AMAT", "MRVL", "SNDK", "ASML", "ANET",
    "LITE", "COHR", "AAOI", "CRDO", "ALAB", "VRT", "SOXX", "SOXL", "SOXS", "SMH", "KSTR",
})


@dataclass
class V57Config:
    name: str
    label: str
    max_positions: int = 3
    starter_weight_1x: float = 0.22
    addon_weight_1x: float = 0.11
    starter_weight_3x: float = 0.22
    addon_weight_3x: float = 0.11
    sl_min_1x: float = 0.020
    sl_max_1x: float = 0.045
    sl_min_3x: float = 0.025
    sl_max_3x: float = 0.045
    addon_sl_buffer: float = 0.01
    weak_exit_min_days: int = 2
    # V5.7 核心消融开关
    use_temp: bool = True
    use_corridor_def: bool = True
    use_swap: bool = True
    soxs_eod_trim: bool = False           # 消融实测：SOXS 尾盘减半开关（默认关闭，单独做消融对照）
    use_extended_win: bool = True
    allow_open15: bool = True             # V5.7 核心：09:35~09:44 开盘高确信半仓试探（防 09:30 首根竞价毛刺）
    # 阶梯锁利 + Neo Lee 保本：先让金字塔加仓(+1.8%/+2.8%)自然触发，
    # 达到 +2.6% (1x) / +4.0% (3x) 先平 25% 真实落袋 + 推保本损 (+0.3%)，保留 75% 冲刺主升浪；
    # 达到 +4.8% (1x) / +7.2% (3x) 再平余仓 1/3 (=原仓 25%)，保留完整 50% 尾仓移动追踪让大肉奔跑！
    fast_be_1x_pct: float = 2.6
    fast_be_3x_pct: float = 4.0
    fast_be_trim_frac: float = 0.25
    partial_1x_pct: float = 4.8
    partial_3x_pct: float = 7.2
    partial_trim_frac: float = 0.333
    trail_1x_pct: float = 2.5
    trail_3x_pct: float = 4.2
    # AI 软权重 + 双通道门槛
    stock_min_prob: float = 55.0
    stock_min_spread: float = 10.0
    etf_min_prob: float = 52.0
    etf_min_spread: float = 6.0
    pilot_min_prob: float = 48.0
    swap_score_gap: float = 14.0
    max_swaps_per_day: int = 1


def enrich_macro_with_raw_temp(
    macro_ctx: Dict[date, Dict[str, Any]],
    daily_ctx: Dict[date, Dict[str, Dict[str, Any]]],
) -> None:
    """为每个交易日补充严格因果未截断的 raw_macro_bottom_score（去硬接刀锁 <=50 截断）。"""
    def _lin(val: float, lo: float, hi: float, max_pts: float) -> float:
        if abs(hi - lo) < 1e-9:
            return 0.0
        return max(0.0, min(1.0, (val - lo) / (hi - lo))) * max_pts

    for d_cur, m in macro_ctx.items():
        day_rows = daily_ctx.get(d_cur) or {}
        qqq_r = day_rows.get("QQQ") or {}
        spy_r = day_rows.get("SPY") or {}
        vixy_r = day_rows.get("VIXY") or {}
        qqq_b50 = float(qqq_r.get("bias_ma50", 0.0))
        spy_b50 = float(spy_r.get("bias_ma50", qqq_b50))
        qqq_b20 = float(qqq_r.get("bias_ema20", 0.0))
        spy_b20 = float(spy_r.get("bias_ema20", qqq_b20))
        avg_b50 = (qqq_b50 + spy_b50) / 2.0
        avg_b20 = (qqq_b20 + spy_b20) / 2.0
        breadth = float(m.get("breadth_ma20_pct", 50.0))
        vixy_pos = float(vixy_r.get("vixy_pos60", 0.5))
        vixy_dd = float(vixy_r.get("vixy_dd_from_peak", 0.0))
        qqq_r5 = float(qqq_r.get("ret_5d", 0.0))

        l1 = max(0.0, min(60.0,
            _lin(vixy_pos, 0.35, 0.95, 22.0)
            + _lin(-avg_b20, 1.0, 10.0, 10.0)
            + _lin(-avg_b50, 2.0, 14.0, 8.0)
            + _lin(breadth, 50.0, 12.0, 12.0)
            + _lin(-qqq_r5, 1.0, 6.0, 8.0)
        ))
        l2 = 0.0
        if bool(m.get("panic_clearing_active")):
            l2 += 24.0
        elif vixy_dd >= 8.0:
            l2 += 16.0
        elif vixy_dd >= 4.0:
            l2 += 8.0
        if float(qqq_r.get("ret_1d", 0.0)) >= 0.6 or float(spy_r.get("ret_1d", 0.0)) >= 0.6:
            l2 += 12.0
        m["raw_macro_bottom_layer1"] = round(l1, 1)
        m["raw_macro_bottom_score"] = round(max(0.0, min(100.0, l1 + min(40.0, l2))), 1)


def build_extended_and_corridor(
    df5: pd.DataFrame,
    valid_dates: List[date],
    bars_by_day_tk: Dict[Tuple[date, str], Dict[datetime, Dict[str, float]]],
):
    valid_set = set(valid_dates)
    df = df5.copy()
    df["Ticker"] = df["Ticker"].astype(str).str.strip().str.upper()
    df = df[~df["Ticker"].isin({"KOSPI", "BTC-USD", "XAUUSD"})].copy()
    dt_col = pd.to_datetime(df["Datetime"])
    df["dt"] = dt_col
    df["d"] = dt_col.dt.date
    df["hhmm"] = dt_col.dt.hour * 60 + dt_col.dt.minute
    df = df[df["d"].isin(valid_set)].copy()

    ext_df = df[
        ((df["hhmm"] >= 7 * 60 + 30) & (df["hhmm"] <= 9 * 60 + 25))
        | ((df["hhmm"] >= 9 * 60 + 30) & (df["hhmm"] <= 15 * 60 + 55))
        | ((df["hhmm"] >= 16 * 60) & (df["hhmm"] <= 17 * 60 + 30))
    ].sort_values(["Ticker", "dt"]).copy()

    ext_timestamps: Dict[date, List[datetime]] = {}
    for d_cur, grp in ext_df.groupby("d"):
        ext_timestamps[d_cur] = sorted(grp["dt"].unique().tolist())

    ext_bars: Dict[Tuple[date, str], Dict[datetime, Dict[str, float]]] = {}
    for tk, tgrp in ext_df.groupby("Ticker"):
        tg = tgrp.sort_values("dt").reset_index(drop=True)
        closes = tg["Close"].astype(float).values
        vols = tg["Volume"].astype(float).values
        rsi5 = bt55._wilder_rsi(closes, 14)
        vol_ma20 = pd.Series(vols).shift(1).rolling(20, min_periods=5).mean().values
        valid_v = (~np.isnan(vol_ma20)) & (vol_ma20 > 1.0)
        rvol_raw = np.ones_like(vols, dtype=float)
        np.divide(vols, vol_ma20, out=rvol_raw, where=valid_v)
        rvol5 = np.clip(rvol_raw, 0.2, 8.0)
        tg["rsi_5m"] = rsi5
        tg["rvol_5m"] = rvol5

        for d_cur, dg in tg.groupby("d"):
            rth_map = bars_by_day_tk.get((d_cur, tk)) or {}
            pm_pv, pm_v, pm_tp_sum, pm_n = 0.0, 0.0, 0.0, 0
            pm_hi, pm_lo = 0.0, float("inf")
            rth_open = 0.0
            last_rth_vwap = 0.0
            last_rth_lo = float("inf")
            last_rth_hi = 0.0
            tmap: Dict[datetime, Dict[str, float]] = {}
            for row in dg.itertuples(index=False):
                hm = int(row.hhmm)
                o, h, l, c = float(row.Open), float(row.High), float(row.Low), float(row.Close)
                v = max(0.0, float(row.Volume))
                if hm < 9 * 60 + 30:
                    tp = (h + l + c) / 3.0
                    pm_pv += tp * v
                    pm_v += v
                    pm_tp_sum += tp
                    pm_n += 1
                    ext_vwap = (pm_pv / pm_v) if pm_v > 0 else (pm_tp_sum / pm_n)
                    pm_hi = max(pm_hi, h)
                    pm_lo = min(pm_lo, l)
                    tmap[row.dt] = {
                        "open": o, "high": h, "low": l, "close": c, "volume": v,
                        "vwap": ext_vwap, "day_open": 0.0, "day_low": pm_lo, "day_high": pm_hi,
                        "pm_high_sofar": pm_hi, "pm_low_sofar": pm_lo if pm_lo < 1e12 else 0.0,
                        "rsi_5m": float(row.rsi_5m), "rvol_5m": float(row.rvol_5m),
                        "session": "PRE_MARKET",
                    }
                elif hm <= 15 * 60 + 55:
                    rb = rth_map.get(row.dt)
                    if rb:
                        rth_open = rb["day_open"]
                        last_rth_vwap = rb["vwap"]
                        last_rth_lo = rb["day_low"]
                        last_rth_hi = rb["day_high"]
                        tmap[row.dt] = {
                            **rb,
                            "pm_high_sofar": pm_hi,
                            "pm_low_sofar": pm_lo if pm_lo < 1e12 else 0.0,
                            "session": "OPEN_15M" if hm < 9 * 60 + 45 else "RTH",
                        }
                else:
                    vw = last_rth_vwap if last_rth_vwap > 0 else c
                    tmap[row.dt] = {
                        "open": o, "high": h, "low": l, "close": c, "volume": v,
                        "vwap": vw, "day_open": rth_open or o,
                        "day_low": min(last_rth_lo, l) if last_rth_lo < 1e12 else l,
                        "day_high": max(last_rth_hi, h),
                        "pm_high_sofar": pm_hi,
                        "pm_low_sofar": pm_lo if pm_lo < 1e12 else 0.0,
                        "rsi_5m": float(row.rsi_5m), "rvol_5m": float(row.rvol_5m),
                        "session": "AFTER_HOURS",
                    }
            ext_bars[(d_cur, tk)] = tmap

    corridor_bands: Dict[Tuple[date, str], Dict[int, Tuple[float, float]]] = {}
    all_tks = sorted({tk for (_, tk) in bars_by_day_tk.keys()})
    for tk in all_tks:
        hist_by_slot: Dict[int, List[float]] = defaultdict(list)
        for d_cur in valid_dates:
            day_band: Dict[int, Tuple[float, float]] = {}
            for slot_m, vals in hist_by_slot.items():
                if len(vals) >= 6:
                    arr = vals[-15:]
                    day_band[slot_m] = (float(np.percentile(arr, 25)), float(np.percentile(arr, 75)))
            corridor_bands[(d_cur, tk)] = day_band
            tmap = bars_by_day_tk.get((d_cur, tk)) or {}
            for ts_b, b in tmap.items():
                do = b.get("day_open", 0.0)
                if do > 0:
                    slot_m = ts_b.hour * 60 + ts_b.minute
                    hist_by_slot[slot_m].append((b["close"] - do) / do * 100.0)

    return ext_timestamps, ext_bars, corridor_bands


def _corridor_status(d_cur: date, tk: str, ts: datetime, bar: Dict[str, float], atr_pct: float, corridor_bands) -> str:
    do = float(bar.get("day_open", 0.0))
    if do <= 0:
        return ""
    ret_o = (bar["close"] - do) / do * 100.0
    slot_m = ts.hour * 60 + ts.minute
    band = (corridor_bands.get((d_cur, tk)) or {}).get(slot_m)
    if band is not None:
        p25, p75 = band
        min_half = max(0.45, 0.35 * atr_pct)
        if ret_o >= max(p75, min_half):
            return "ABOVE_P75"
        if ret_o <= min(p25, -min_half):
            return "BELOW_P25"
        return "IN_CORRIDOR"
    elapsed_frac = max(0.15, min(1.0, (slot_m - (9 * 60 + 30)) / 390.0))
    thresh = max(0.55, 0.60 * atr_pct * math.sqrt(elapsed_frac))
    if ret_o >= thresh:
        return "ABOVE_P75"
    if ret_o <= -thresh:
        return "BELOW_P25"
    return "IN_CORRIDOR"


def _intraday_temp(d_cur: date, ts: datetime, d_tks, ext_bars) -> Tuple[float, float]:
    def _lin(val: float, lo: float, hi: float, max_pts: float) -> float:
        if abs(hi - lo) < 1e-9:
            return 0.0
        return max(0.0, min(1.0, (val - lo) / (hi - lo))) * max_pts

    legs = []
    for sym in ("QQQ", "SPY"):
        b = (ext_bars.get((d_cur, sym)) or {}).get(ts)
        info = d_tks.get(sym) or {}
        pc = float(info.get("prev_close", 0.0))
        if b and pc > 0 and b["close"] > 0:
            pct = (b["close"] - pc) / pc * 100.0
            vw = b["vwap"]
            vw_d = ((b["close"] - vw) / vw * 100.0) if vw > 0 else 0.0
            atrp = max(0.5, float(info.get("atr_pct", 1.3)))
            legs.append((pct, vw_d, float(b["rsi_5m"]), atrp, b["close"] >= vw))
    if not legs:
        return 0.0, 0.0
    intra_pct = sum(x[0] for x in legs) / len(legs)
    intra_vw = sum(x[1] for x in legs) / len(legs)
    intra_rsi = sum(x[2] for x in legs) / len(legs)
    intra_atr = sum(x[3] for x in legs) / len(legs)
    reclaimed = any(x[4] for x in legs)

    ib = _lin(max(0.0, intra_vw) / max(intra_atr, 0.4), 0.0, 0.90, 45.0) + _lin(intra_rsi, 52.0, 78.0, 30.0) + _lin(max(0.0, intra_pct) / max(intra_atr, 0.4), 0.0, 1.25, 25.0)
    if intra_pct <= 0.0 or intra_vw < 0.0:
        ib = min(ib, 25.0)
    ib = round(max(0.0, min(100.0, ib)), 1)

    ibt_l1 = _lin(max(0.0, -intra_vw) / max(intra_atr, 0.4), 0.10, 0.95, 26.0) + _lin(intra_rsi, 42.0, 22.0, 18.0) + _lin(max(0.0, -intra_pct) / max(intra_atr, 0.4), 0.20, 1.15, 16.0)
    ibt_l2 = 24.0 if (reclaimed and intra_pct <= -0.3) else (8.0 if reclaimed else 0.0)
    ibt = round(max(0.0, min(100.0, min(60.0, ibt_l1) + ibt_l2)), 1)
    return ib, ibt


def _resolve_sl_v57(cfg: V57Config, tk: str, direction: str, entry_px: float, atr_pct: float, day_low: float, day_high: float, pm_low: float, pm_high: float, s1: float, r1: float) -> float:
    lev3 = kol.is_3x_leveraged(tk)
    sl_min = cfg.sl_min_3x if lev3 else cfg.sl_min_1x
    sl_max = cfg.sl_max_3x if lev3 else cfg.sl_max_1x
    # 吸收 V5.4 呼吸空间：在 [sl_min, sl_max] 间参考 0.85*ATR 下限
    sl_floor = min(sl_max, max(sl_min, (atr_pct / 100.0) * (0.60 if lev3 else 0.78)))
    pad = 0.010 if lev3 else 0.006

    if direction == "LONG":
        cands = [x * (1.0 - pad) for x in (day_low, pm_low, s1) if 0 < x < entry_px]
        lo_cap = entry_px * (1.0 - sl_max)
        hi_cap = entry_px * (1.0 - sl_floor)
        if cands:
            in_band = [v for v in cands if lo_cap <= v <= hi_cap]
            raw = max(in_band) if in_band else max(lo_cap, min(hi_cap, max(cands)))
        else:
            raw = entry_px * (1.0 - (sl_floor + sl_max) * 0.5)
        return max(0.01, round(max(lo_cap, min(hi_cap, raw)), 2))

    cands = [x * (1.0 + pad) for x in (day_high, pm_high, r1) if x > entry_px]
    lo_cap = entry_px * (1.0 + sl_floor)
    hi_cap = entry_px * (1.0 + sl_max)
    if cands:
        in_band = [v for v in cands if lo_cap <= v <= hi_cap]
        raw = min(in_band) if in_band else max(lo_cap, min(hi_cap, min(cands)))
    else:
        raw = entry_px * (1.0 + (sl_floor + sl_max) * 0.5)
    return max(round(entry_px + 0.01, 2), round(max(lo_cap, min(hi_cap, raw)), 2))


def run_v57(
    cfg: V57Config,
    rt_slippage: float,
    valid_dates: List[date],
    ext_timestamps,
    ext_bars,
    corridor_bands,
    pm_bounds,
    macro_ctx,
    daily_ctx,
    day_timestamps,
    bars_by_day_tk,
) -> Dict[str, Any]:
    cash = bt55.INITIAL_CAPITAL
    peak_equity = bt55.INITIAL_CAPITAL
    max_dd = 0.0
    open_positions: List[Dict[str, Any]] = []
    closed_trades: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []

    last_exit_by_tk: Dict[str, Dict[str, Any]] = {}
    corridor_def_triggers = 0
    corridor_def_partial_locks = 0
    corridor_def_sl_tightens = 0
    swap_count = 0
    soxs_eod_trims = 0
    temp_bounce_entries = 0
    temp_short_tightens = 0
    temp_bubble_blocks = 0

    for d_idx, d_cur in enumerate(valid_dates):
        d_str = d_cur.isoformat()
        m_info = macro_ctx.get(d_cur) or {}
        d_tks = daily_ctx.get(d_cur) or {}
        regime = str(m_info.get("market_regime") or "NEUTRAL_CHOP")
        vix_brake = bool(m_info.get("vix_hard_brake", False))
        panic_clear = bool(m_info.get("panic_clearing_active", False))
        raw_mb_score = float(m_info.get("raw_macro_bottom_score", 0.0))
        long_blocked_base = bool((regime == "BEAR_DEFENSE" or vix_brake) and (not panic_clear))
        temp_oversold_active = bool(cfg.use_temp and raw_mb_score >= 40.0)

        entries_today = 0
        open15_entries_today = 0
        ext_entries_today = 0
        swaps_today = 0
        max_entries_day = cfg.max_positions + 2
        ts_list = ext_timestamps.get(d_cur) or (day_timestamps.get(d_cur) or [])

        for ts in ts_list:
            et_min = ts.hour * 60 + ts.minute
            is_rth = (9 * 60 + 30 <= et_min <= 15 * 60 + 55)
            is_open15 = (9 * 60 + 30 <= et_min < 9 * 60 + 45)
            is_pm = (7 * 60 + 30 <= et_min <= 9 * 60 + 25)
            is_ah = (16 * 60 <= et_min <= 17 * 60 + 30)
            is_first_rth_bar = (et_min == 9 * 60 + 30)
            if (is_pm or is_ah) and (not cfg.use_extended_win):
                continue

            intra_bubble, intra_bottom = _intraday_temp(d_cur, ts, d_tks, ext_bars) if cfg.use_temp else (0.0, 0.0)
            bubble_hot = bool(cfg.use_temp and intra_bubble >= 75.0)
            oversold_hot = bool(cfg.use_temp and (raw_mb_score >= 40.0 or intra_bottom >= 55.0))

            # 1) 在途持仓管理
            still_open: List[Dict[str, Any]] = []
            for pos in open_positions:
                tk = pos["ticker"]
                direction = pos["direction"]
                b = (ext_bars.get((d_cur, tk)) or {}).get(ts)
                if not b:
                    still_open.append(pos)
                    continue

                o_px, h_px, l_px, c_px = b["open"], b["high"], b["low"], b["close"]
                vwap_px = b["vwap"]
                vwap_diff = ((c_px - vwap_px) / vwap_px * 100.0) if vwap_px > 0 else 0.0
                rsi_5m = b["rsi_5m"]
                rvol_5m = b["rvol_5m"]

                entry_px = pos["entry_price"]
                sl_px = pos["sl_price"]
                alloc = pos["allocated_capital"]
                lev3 = kol.is_3x_leveraged(tk)

                exit_reason = ""
                exit_fill_px = c_px
                gap_sl_slip = False
                partial_done = bool(pos["partial_tp_done"])
                be_active = bool(pos["be_stop_active"])

                # (1a) 止损判定（常规盘即时；延展时段连续 2 根防插针；开盘跳空按 o_px 真实成交）
                if is_rth:
                    if direction == "LONG":
                        if o_px <= sl_px:
                            exit_reason = "BE_STOP" if be_active else "SL_HIT"
                            exit_fill_px = o_px
                            gap_sl_slip = is_first_rth_bar
                        elif l_px <= sl_px:
                            exit_reason = "BE_STOP" if be_active else "SL_HIT"
                            exit_fill_px = sl_px
                    else:
                        if o_px >= sl_px:
                            exit_reason = "BE_STOP" if be_active else "SL_HIT"
                            exit_fill_px = o_px
                            gap_sl_slip = is_first_rth_bar
                        elif h_px >= sl_px:
                            exit_reason = "BE_STOP" if be_active else "SL_HIT"
                            exit_fill_px = sl_px
                    pos["ext_sl_breach_cnt"] = 0
                else:
                    breached_ext = (c_px <= sl_px) if direction == "LONG" else (c_px >= sl_px)
                    if breached_ext:
                        pos["ext_sl_breach_cnt"] = pos.get("ext_sl_breach_cnt", 0) + 1
                        if pos["ext_sl_breach_cnt"] >= 2:
                            exit_reason = "BE_STOP" if be_active else "SL_HIT"
                            exit_fill_px = c_px
                    else:
                        pos["ext_sl_breach_cnt"] = 0

                # 实盘口径：触发阈值按盘中真实价格涨跌幅 (gross_ret_pct) 判定，确保 3bps 与 25bps 决策路径一致；
                # 所有已实现落袋盈亏、尾仓平仓盈亏、M2M 估值仍 100% 全额扣减 rt_slippage (25 bps / 3 bps)！
                gross_ret_pct = (((c_px - entry_px) / entry_px) if direction == "LONG" else ((entry_px - c_px) / entry_px)) * 100.0
                ret_c = bt55._net_ret(direction, entry_px, c_px, rt_slippage)
                ret_c_pct = ret_c * 100.0
                pos["max_favorable_return_pct"] = max(pos["max_favorable_return_pct"], gross_ret_pct)
                if direction == "LONG":
                    pos["best_price"] = max(pos["best_price"], c_px)
                else:
                    pos["best_price"] = min(pos["best_price"], c_px)

                # (1b-pre) 金字塔顺势加仓 (1x +1.8% / 3x +2.8%，优先于 fast-BE 判定，仅站稳 VWAP 且绝不浮亏摊平)
                if (not exit_reason) and is_rth and pos["stage"] == 1 and pos["can_add"] and (not partial_done):
                    add_trig = kol.ADDON_TRIGGER_3X_PCT if lev3 else kol.ADDON_TRIGGER_1X_PCT
                    on_vwap = (vwap_diff >= 0.0) if direction == "LONG" else (vwap_diff <= 0.0)
                    if gross_ret_pct >= add_trig and on_vwap and (not (direction == "LONG" and bubble_hot)):
                        target_full = (cfg.starter_weight_3x + cfg.addon_weight_3x) if lev3 else (cfg.starter_weight_1x + cfg.addon_weight_1x)
                        add_wt = max(0.05, round(target_full - pos["slot_weight"], 3))
                        est_eq = cash + sum(p["allocated_capital"] for p in open_positions)
                        add_alloc = round(min(cash, est_eq * add_wt), 2)
                        if add_alloc >= 10.0:
                            orig_ep = pos["orig_entry_price"]
                            sh1 = alloc / entry_px
                            sh2 = add_alloc / c_px
                            new_avg = round((alloc + add_alloc) / (sh1 + sh2), 4)
                            cash = round(cash - add_alloc, 2)
                            pos["entry_price"] = round(new_avg, 2)
                            entry_px = pos["entry_price"]
                            pos["allocated_capital"] = round(alloc + add_alloc, 2)
                            alloc = pos["allocated_capital"]
                            pos["slot_weight"] = round(pos["slot_weight"] + add_wt, 3)
                            pos["peak_slot_weight"] = pos["slot_weight"]
                            pos["stage"] = 2
                            pos["can_add"] = False
                            if direction == "LONG":
                                pos["sl_price"] = max(sl_px, round(orig_ep * (1.0 - cfg.addon_sl_buffer), 2))
                            else:
                                pos["sl_price"] = min(sl_px, round(orig_ep * (1.0 + cfg.addon_sl_buffer), 2))
                            sl_px = pos["sl_price"]
                            gross_ret_pct = (((c_px - entry_px) / entry_px) if direction == "LONG" else ((entry_px - c_px) / entry_px)) * 100.0
                            ret_c = bt55._net_ret(direction, entry_px, c_px, rt_slippage)
                            ret_c_pct = ret_c * 100.0

                # (1b) Neo Lee 阶梯快推保本（1x >= +2.5% / 3x >= +3.8%）：
                #      先落袋 25% 真实兑现现金（彻底防隔夜跳空把 2~3% 浮盈打回亏损），余仓 75% 止损推至 +0.3% 保本继续奔跑！
                if (not exit_reason) and (not be_active):
                    fast_be_trig = cfg.fast_be_3x_pct if lev3 else cfg.fast_be_1x_pct
                    if gross_ret_pct >= fast_be_trig:
                        if cfg.fast_be_trim_frac > 0 and (not partial_done):
                            close_alloc = round(alloc * cfg.fast_be_trim_frac, 2)
                            rem_alloc = round(alloc - close_alloc, 2)
                            part_pnl = round(close_alloc * ret_c, 2)
                            cash = round(cash + max(0.0, close_alloc + part_pnl), 2)
                            pos["allocated_capital"] = rem_alloc
                            pos["slot_weight"] = round(pos["slot_weight"] * (1.0 - cfg.fast_be_trim_frac), 3)
                            pos["partial_closed_capital"] = round(pos["partial_closed_capital"] + close_alloc, 2)
                            pos["partial_realized_pnl_usd"] = round(pos["partial_realized_pnl_usd"] + part_pnl, 2)
                            pos["can_add"] = False
                            alloc = rem_alloc
                        pos["be_stop_active"] = True
                        be_active = True
                        if direction == "LONG":
                            pos["sl_price"] = max(sl_px, round(entry_px * (1.0 + kol.BE_STOP_BUFFER), 2))
                        else:
                            pos["sl_price"] = min(sl_px, round(entry_px * (1.0 - kol.BE_STOP_BUFFER), 2))
                        sl_px = pos["sl_price"]

                # (1c) 盘中推演走廊反向破位防守 (CORRIDOR_DEFENSE)：须连续 2 根 5m 确认反向破走廊 + 失守 VWAP
                if (not exit_reason) and is_rth and cfg.use_corridor_def and (not pos.get("corridor_def_done")):
                    tk_atr = float((d_tks.get(tk) or {}).get("atr_pct", 3.0))
                    c_stat_self = _corridor_status(d_cur, tk, ts, b, tk_atr, corridor_bands)
                    parent_sym = V57_PARENT_INDEX_MAP.get(tk)
                    c_stat_parent = ""
                    if parent_sym:
                        pb = (ext_bars.get((d_cur, parent_sym)) or {}).get(ts)
                        p_atr = float((d_tks.get(parent_sym) or {}).get("atr_pct", 1.8))
                        if pb:
                            c_stat_parent = _corridor_status(d_cur, parent_sym, ts, pb, p_atr, corridor_bands)
                            if tk in cte.INVERSE_ETFS:
                                c_stat_parent = "BELOW_P25" if c_stat_parent == "ABOVE_P75" else ("ABOVE_P75" if c_stat_parent == "BELOW_P25" else c_stat_parent)

                    opp_breach = (
                        (direction == "SHORT" and (c_stat_self == "ABOVE_P75" or c_stat_parent == "ABOVE_P75"))
                        or (direction == "LONG" and (c_stat_self == "BELOW_P25" or c_stat_parent == "BELOW_P25"))
                    )
                    lost_vwap = (vwap_diff < -0.15) if direction == "LONG" else (vwap_diff > 0.15)
                    if opp_breach and lost_vwap:
                        pos["cor_streak"] = pos.get("cor_streak", 0) + 1
                    else:
                        pos["cor_streak"] = 0

                    if pos.get("cor_streak", 0) >= 2 and gross_ret_pct >= 1.20:
                        pos["corridor_def_done"] = True
                        pos["corridor_def_triggered"] = True
                        corridor_def_triggers += 1
                        # 已有浮盈 + 连续 2 根反向破走廊 + 失守 VWAP：半仓锁利 + 推保本 + 收紧追踪，保住浮盈防反杀；
                        # 绝不误杀无浮盈仓位的初始呼吸止损垫或后续顺势加仓权限！
                        if not partial_done:
                            close_alloc = round(alloc * 0.50, 2)
                            rem_alloc = round(alloc - close_alloc, 2)
                            part_pnl = round(close_alloc * ret_c, 2)
                            cash = round(cash + max(0.0, close_alloc + part_pnl), 2)
                            pos["allocated_capital"] = rem_alloc
                            pos["slot_weight"] = round(pos["slot_weight"] * 0.50, 3)
                            pos["partial_tp_done"] = True
                            pos["can_add"] = False
                            pos["partial_closed_capital"] = round(pos["partial_closed_capital"] + close_alloc, 2)
                            pos["partial_realized_pnl_usd"] = round(pos["partial_realized_pnl_usd"] + part_pnl, 2)
                            partial_done = True
                            alloc = rem_alloc
                        pos["be_stop_active"] = True
                        pos["trail_armed"] = True
                        pos["trail_mult"] = min(pos.get("trail_mult", 1.0), 0.65)
                        be_active = True
                        if direction == "LONG":
                            pos["sl_price"] = max(sl_px, round(entry_px * (1.0 + kol.BE_STOP_BUFFER), 2))
                        else:
                            pos["sl_price"] = min(sl_px, round(entry_px * (1.0 - kol.BE_STOP_BUFFER), 2))
                        sl_px = pos["sl_price"]
                        corridor_def_partial_locks += 1

                # (1d) 一阶锁利 (+5.0% 1x / +7.5% 3x)：再平余仓 1/3 (=原仓 25%)，保留完整 50% 尾仓移动追踪让大肉奔跑
                if (not exit_reason) and (not partial_done):
                    p_trig = cfg.partial_3x_pct if lev3 else cfg.partial_1x_pct
                    if gross_ret_pct >= p_trig:
                        t_frac = cfg.partial_trim_frac if be_active else 0.50
                        close_alloc = round(alloc * t_frac, 2)
                        rem_alloc = round(alloc - close_alloc, 2)
                        part_pnl = round(close_alloc * ret_c, 2)
                        cash = round(cash + max(0.0, close_alloc + part_pnl), 2)
                        pos["allocated_capital"] = rem_alloc
                        pos["slot_weight"] = round(pos["slot_weight"] * (1.0 - t_frac), 3)
                        pos["partial_tp_done"] = True
                        pos["can_add"] = False
                        pos["be_stop_active"] = True
                        pos["trail_armed"] = True
                        pos["partial_closed_capital"] = round(pos["partial_closed_capital"] + close_alloc, 2)
                        pos["partial_realized_pnl_usd"] = round(pos["partial_realized_pnl_usd"] + part_pnl, 2)
                        if direction == "LONG":
                            pos["sl_price"] = max(sl_px, round(entry_px * (1.0 + kol.BE_STOP_BUFFER), 2))
                        else:
                            pos["sl_price"] = min(sl_px, round(entry_px * (1.0 - kol.BE_STOP_BUFFER), 2))
                        pos["best_price"] = c_px
                        partial_done = True
                        be_active = True
                        alloc = rem_alloc
                        sl_px = pos["sl_price"]

                # (1e) 放量卖强 (CLIMAX_TP) & 阶梯移动止盈 (仅在一阶落袋或走廊/温度计确认反转时启动追踪)
                is_short_exp = (direction == "SHORT") or (tk in cte.INVERSE_ETFS and direction == "LONG")
                qqq_b_m = (ext_bars.get((d_cur, "QQQ")) or {}).get(ts)
                qqq_pc_m = float((d_tks.get("QQQ") or {}).get("prev_close", 0.0))
                qqq_pct_m = ((qqq_b_m["close"] - qqq_pc_m) / qqq_pc_m * 100.0) if (qqq_b_m and qqq_pc_m > 0) else 0.0
                qqq_reclaim_now = bool(qqq_b_m and qqq_b_m["vwap"] > 0 and qqq_b_m["close"] >= qqq_b_m["vwap"] and qqq_pct_m >= 0.45)
                # 仅在「大盘深度超卖 >=45 且 QQQ 真实转红翻涨 >=+0.45% 并站稳 VWAP」时收紧已大赚 >=4.5% 的空单，绝不在单边主跌浪里过早砍掉空单
                short_vrev_warn = bool(cfg.use_temp and is_short_exp and raw_mb_score >= 45.0 and qqq_reclaim_now and gross_ret_pct >= 4.5)
                long_bub_warn = bool(cfg.use_temp and (not is_short_exp) and bubble_hot and gross_ret_pct >= 3.0)
                temp_active_trail = short_vrev_warn or long_bub_warn
                if (not exit_reason) and (pos.get("trail_armed") or partial_done or temp_active_trail):
                    climax_min = kol.CLIMAX_PROFIT_3X_PCT if lev3 else kol.CLIMAX_PROFIT_1X_PCT
                    is_climax = (
                        ((direction == "LONG" and rsi_5m >= kol.CLIMAX_RSI_HIGH) or (direction == "SHORT" and rsi_5m <= kol.CLIMAX_RSI_LOW))
                        and rvol_5m >= kol.CLIMAX_RVOL_MIN
                        and gross_ret_pct >= climax_min
                    )
                    if is_climax:
                        exit_reason, exit_fill_px = "CLIMAX_TP", c_px
                    else:
                        base_trail = cfg.trail_3x_pct if lev3 else cfg.trail_1x_pct
                        t_mult = pos.get("trail_mult", 1.0)
                        if temp_active_trail:
                            t_mult = min(t_mult, 0.70)
                            if short_vrev_warn:
                                pos["temp_short_tightened"] = True
                        eff_trail = max(1.1, base_trail * t_mult)
                        bp = pos["best_price"]
                        dd_best = ((bp - c_px) / bp * 100.0) if direction == "LONG" else ((c_px - bp) / bp * 100.0)
                        if dd_best >= eff_trail:
                            exit_reason = "CORRIDOR_TRAIL_TP" if pos.get("corridor_def_triggered") else "TRAIL_TP"
                            exit_fill_px = c_px
                            if pos.get("temp_short_tightened"):
                                temp_short_tightens += 1

                # (1f) 3x 反向 ETF (SOXS / SQQQ) 尾盘 15:50 ET 锁利/减半不过夜
                if (
                    (not exit_reason)
                    and cfg.soxs_eod_trim
                    and (tk in cte.INVERSE_ETFS)
                    and lev3
                    and et_min == 15 * 60 + 50
                    and (pos.get("eod_trimmed_date") != d_str)
                    and (not partial_done)
                ):
                    close_alloc = round(alloc * 0.50, 2)
                    rem_alloc = round(alloc - close_alloc, 2)
                    part_pnl = round(close_alloc * ret_c, 2)
                    cash = round(cash + max(0.0, close_alloc + part_pnl), 2)
                    pos["allocated_capital"] = rem_alloc
                    pos["slot_weight"] = round(pos["slot_weight"] * 0.50, 3)
                    pos["partial_tp_done"] = True
                    pos["can_add"] = False
                    pos["eod_trimmed_date"] = d_str
                    pos["partial_closed_capital"] = round(pos["partial_closed_capital"] + close_alloc, 2)
                    pos["partial_realized_pnl_usd"] = round(pos["partial_realized_pnl_usd"] + part_pnl, 2)
                    if ret_c_pct > 0.5:
                        pos["be_stop_active"] = True
                        pos["trail_armed"] = True
                        pos["sl_price"] = max(sl_px, round(entry_px * (1.0 + kol.BE_STOP_BUFFER), 2)) if direction == "LONG" else min(sl_px, round(entry_px * (1.0 - kol.BE_STOP_BUFFER), 2))
                    alloc = rem_alloc
                    partial_done = True
                    soxs_eod_trims += 1

                # (1g) T+2 早撤体检 + T+3 到期
                if not exit_reason and is_rth:
                    if (d_idx >= pos["expiry_idx"] and et_min >= kol.T3_FORCE_EXIT_MIN) or (d_idx > pos["expiry_idx"]):
                        exit_reason, exit_fill_px = "T3_EXPIRE", c_px
                    elif (not partial_done) and (not be_active) and (d_idx - pos["entry_day_idx"]) >= cfg.weak_exit_min_days:
                        days_held = d_idx - pos["entry_day_idx"]
                        check_min = kol.EARLY_CHECK_LATE_MIN if (days_held == cfg.weak_exit_min_days and pos["entry_min"] > kol.LATE_ENTRY_CUTOFF_MIN) else kol.EARLY_CHECK_NORMAL_MIN
                        if et_min >= check_min:
                            wrong_vwap = (vwap_diff < 0.0) if direction == "LONG" else (vwap_diff > 0.0)
                            if gross_ret_pct < 0.8 and wrong_vwap:
                                exit_reason, exit_fill_px = "WEAK_EARLY_EXIT", c_px

                if exit_reason:
                    runner_ret = bt55._net_ret(direction, pos["entry_price"], exit_fill_px, rt_slippage)
                    runner_pnl = round(pos["allocated_capital"] * runner_ret, 2)
                    cash = round(cash + max(0.0, pos["allocated_capital"] + runner_pnl), 2)
                    part_pnl = pos.get("partial_realized_pnl_usd", 0.0)
                    part_cap = pos.get("partial_closed_capital", 0.0)
                    tot_alloc = round(pos["allocated_capital"] + part_cap, 2)
                    tot_pnl = round(part_pnl + runner_pnl, 2)
                    tot_ret_pct = round((tot_pnl / tot_alloc) * 100.0, 2) if tot_alloc > 0 else round(runner_ret * 100.0, 2)
                    outcome = ("T3_WIN" if tot_pnl > 0 else "T3_LOSS") if exit_reason == "T3_EXPIRE" else exit_reason
                    hold_hours = round((ts - pos["entry_dt"]).total_seconds() / 3600.0, 2)
                    closed_trades.append({
                        "ticker": tk, "is_3x": lev3, "direction": direction,
                        "entry_tag": pos.get("entry_tag", "MAIN_AI"),
                        "entry_channel": pos.get("entry_channel", "MAIN_AI"),
                        "entry_window": pos.get("entry_window", "RTH"),
                        "setup": pos.get("setup", "V57"), "stage": pos.get("stage", 1),
                        "swapped_in": bool(pos.get("swapped_in", False)),
                        "temp_bounce": bool(pos.get("temp_bounce", False)),
                        "corridor_def_triggered": bool(pos.get("corridor_def_triggered", False)),
                        "max_favorable_return_pct": round(pos.get("max_favorable_return_pct", 0.0), 2),
                        "entry_date": pos["entry_date"], "entry_month": pos["entry_date"][:7],
                        "entry_time_et": pos["entry_dt"].strftime("%Y-%m-%d %H:%M:%S"),
                        "orig_entry_price": pos["orig_entry_price"], "entry_price": pos["entry_price"],
                        "exit_date": d_str, "exit_time_et": ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "exit_price": round(exit_fill_px, 2), "outcome": outcome, "exit_reason": exit_reason,
                        "gap_sl_slip": gap_sl_slip, "allocated_capital": tot_alloc,
                        "pnl_usd": tot_pnl, "net_return_pct": tot_ret_pct,
                        "hold_hours": hold_hours, "v54_tp_price": None,
                    })
                    last_exit_by_tk[tk] = {"direction": direction, "exit_date": d_str, "exit_dt": ts, "exit_reason": exit_reason}
                else:
                    pos["last_price"] = c_px
                    pos["cur_ret_pct"] = gross_ret_pct
                    pos["cur_vwap_diff"] = vwap_diff
                    still_open.append(pos)

            open_positions = still_open

            # 2) 当前权益估值
            deployed_val = 0.0
            for pos in open_positions:
                b_cur = (ext_bars.get((d_cur, pos["ticker"])) or {}).get(ts)
                lp = b_cur["close"] if b_cur else pos.get("last_price", pos["entry_price"])
                r_u = bt55._net_ret(pos["direction"], pos["entry_price"], lp, rt_slippage)
                deployed_val += max(0.0, pos["allocated_capital"] * (1.0 + r_u))
            cur_equity = round(cash + deployed_val, 2)

            # 3) 开仓扫描 + 严苛防磨损换仓
            # 盘前黄金窗 07:30~09:25 保留；常规盘 09:45~15:25（若 allow_open15=True 则 09:35~09:44 高确信半仓试探）
            rth_start_min = (9 * 60 + 35) if cfg.allow_open15 else (9 * 60 + 45)
            can_scan_win = (
                (is_rth and (et_min >= rth_start_min) and et_min <= 15 * 60 + 25)
                or (cfg.use_extended_win and is_pm and ext_entries_today < 1)
            )
            if is_open15 and open15_entries_today >= 1:
                can_scan_win = False

            has_free_slot = len(open_positions) < cfg.max_positions
            can_try_swap = (
                (not has_free_slot)
                and cfg.use_swap
                and is_rth
                and (not is_open15)
                and swaps_today < cfg.max_swaps_per_day
            )

            if can_scan_win and (has_free_slot or can_try_swap) and entries_today < max_entries_day:
                qqq_b = (ext_bars.get((d_cur, "QQQ")) or {}).get(ts)
                qqq_pc = float((d_tks.get("QQQ") or {}).get("prev_close", 0.0))
                qqq_pct = ((qqq_b["close"] - qqq_pc) / qqq_pc * 100.0) if (qqq_b and qqq_pc > 0) else 0.0
                nq_vwap_ok = bool(qqq_b and qqq_b["vwap"] > 0 and qqq_b["close"] >= qqq_b["vwap"])

                lead_scores = []
                if is_rth:
                    for ltk, linfo in d_tks.items():
                        if ltk in cte.INVERSE_ETFS or ltk in ("GLD", "TLT", "QQQ", "SPY", "SOXX", "SMH"):
                            continue
                        lb = (ext_bars.get((d_cur, ltk)) or {}).get(ts)
                        if not lb or linfo["prev_close"] <= 0:
                            continue
                        lpct = (lb["close"] - linfo["prev_close"]) / linfo["prev_close"] * 100.0
                        if lb["close"] >= lb["vwap"] and lpct > qqq_pct:
                            lead_scores.append((lpct - qqq_pct, ltk))
                    lead_scores.sort(reverse=True)
                leaders_set = {x[1] for x in lead_scores[:5]}

                held_tks = {p["ticker"] for p in open_positions}
                held_grps = {cte.get_mirror_group(tk) for tk in held_tks if cte.get_mirror_group(tk)}
                raw_cands = []

                for tk, info in d_tks.items():
                    if tk in held_tks or tk in cte.NON_TRADABLE_DIRECT_TICKERS:
                        continue
                    if (is_pm or is_ah) and (tk not in V57_EXTENDED_LIQUID_WHITELIST):
                        continue
                    if is_rth and (tk not in kol.KOL_CORE_WHITELIST) and (tk not in leaders_set):
                        continue
                    mg = cte.get_mirror_group(tk)
                    if mg and mg in held_grps:
                        continue
                    b_cur = (ext_bars.get((d_cur, tk)) or {}).get(ts)
                    if not b_cur or b_cur["close"] <= 0:
                        continue

                    px = b_cur["close"]
                    pc = info["prev_close"]
                    if pc <= 0:
                        continue
                    lev3 = kol.is_3x_leveraged(tk)
                    is_etf = tk in V57_SECTOR_AND_3X_ETFS

                    # 盘前/盘后黄金窗点差代理护栏 (过滤宽毛刺)
                    if is_pm or is_ah:
                        bar_rng_pct = (b_cur["high"] - b_cur["low"]) / px
                        if bar_rng_pct > (0.010 if lev3 else 0.005):
                            continue

                    pct_chg = (px - pc) / pc * 100.0
                    gap_pct = ((b_cur["day_open"] - pc) / pc * 100.0) if b_cur.get("day_open", 0.0) > 0 else pct_chg
                    vwap_px = b_cur["vwap"]
                    vwap_diff = ((px - vwap_px) / vwap_px * 100.0) if vwap_px > 0 else 0.0
                    rsi_5m = b_cur["rsi_5m"]
                    rvol_5m = b_cur["rvol_5m"]
                    day_lo = b_cur["day_low"]
                    day_hi = b_cur["day_high"]
                    pm_lo = float(b_cur.get("pm_low_sofar") or (pm_bounds.get((d_cur, tk)) or {}).get("pm_low", 0.0))
                    pm_hi = float(b_cur.get("pm_high_sofar") or (pm_bounds.get((d_cur, tk)) or {}).get("pm_high", 0.0))
                    s1, r1 = info["s1"], info["r1"]
                    corr_div = _corridor_status(d_cur, tk, ts, b_cur, info["atr_pct"], corridor_bands) if is_rth else ""

                    p_l = info["calibrated_win_rate_pct"]
                    p_s = info["calibrated_short_win_rate_pct"]
                    is_crash = info["is_crash_halt"]
                    is_deep_pb = info["is_deep_pullback"]
                    is_broken = info["is_broken_bearish"]

                    last_ex = last_exit_by_tk.get(tk)
                    if last_ex and last_ex["exit_date"] == d_str:
                        if ((ts - last_ex["exit_dt"]).total_seconds() / 60.0) < 45.0:
                            continue

                    main_min_p = cfg.etf_min_prob if is_etf else cfg.stock_min_prob
                    main_min_sp = cfg.etf_min_spread if is_etf else cfg.stock_min_spread
                    base_st_wt = cfg.starter_weight_3x if lev3 else cfg.starter_weight_1x
                    slot_full = (cfg.starter_weight_3x + cfg.addon_weight_3x) if lev3 else (cfg.starter_weight_1x + cfg.addon_weight_1x)
                    pilot_wt = round(slot_full * 0.50, 3)
                    mainline_bonus = 2.5 if tk in V57_MARK_MAINLINE_TICKERS else (2.0 if tk in leaders_set else 0.0)

                    # ---------- A. LONG 通道 ----------
                    # 放开昨收涨跌幅一刀切死限；但高跳空 (>5% 1x / >9% 3x) 必须回踩贴近 VWAP 才准进，防接盘尖峰！
                    high_gap_l = gap_pct > (9.0 if lev3 else 5.0)
                    soxl_chase_block = (tk == "SOXL" and gap_pct >= 2.5 and vwap_diff > 0.9)
                    spike_chase_l = (vwap_diff > (2.2 if lev3 else 1.4)) or (high_gap_l and abs(vwap_diff) > 0.7) or soxl_chase_block
                    if bubble_hot and vwap_diff > 0.3:
                        temp_bubble_blocks += 1
                        spike_chase_l = True

                    allow_l_normal = (not long_blocked_base) or (tk in cte.SAFE_HAVEN_LONG_EXEMPT)
                    # 温度计未截断 raw_macro_bottom_score >= 40 解锁右侧反抽多：
                    # 必须在非 VIX 极端熔断下（或盘中 QQQ 真实翻红 >=+0.5% + 站稳 VWAP 的明确右侧反抽日），杜绝主跌浪徒手接飞刀！
                    allow_l_temp_bounce = bool(
                        long_blocked_base
                        and temp_oversold_active
                        and is_rth
                        and (not is_open15)
                        and (tk not in cte.SAFE_HAVEN_LONG_EXEMPT)
                        and nq_vwap_ok
                        and (qqq_pct >= 0.50)
                        and (not vix_brake)
                        and (raw_mb_score >= 40.0)
                        and (tk in leaders_set or tk in ("QQQ", "SPY", "SOXX", "SOXL", "NVDA", "TSM", "AVGO"))
                    )

                    if (not is_crash) and (not spike_chase_l) and (allow_l_normal or allow_l_temp_bounce):
                        anti_trend_ok_l = (p_l >= 45.0 and (p_s - p_l) < 10.0) or (allow_l_temp_bounce and p_l >= 42.0)
                        # Mark/Kova 纪律：常规做多绝不接破位下跌飞刀 (not is_broken)；仅显式超卖右侧反抽或避险/反向ETF豁免
                        trend_intact_l = (not is_broken) or (tk in cte.SAFE_HAVEN_LONG_EXEMPT) or allow_l_temp_bounce
                        if anti_trend_ok_l and trend_intact_l:
                            swept_l = (
                                (s1 > 0 and ((day_lo <= s1 * 1.006 and px >= s1) or abs(px - s1) / s1 <= 0.012))
                                or (pm_lo > 0 and day_lo <= pm_lo * 1.005 and px >= pm_lo)
                                or is_deep_pb or corr_div == "BELOW_P25"
                            )
                            resil_l = (pct_chg >= qqq_pct - 0.5) or (tk in leaders_set) or (tk in cte.SAFE_HAVEN_LONG_EXEMPT)
                            sweep_ok = swept_l and (vwap_diff >= -0.15) and (rvol_5m >= (1.15 if (is_pm or is_ah) else 1.35)) and resil_l
                            # 突破做多必须大盘日内顺风 (nq_vwap_ok) 或避险ETF，杜绝逆红盘追高假突破
                            brk_ok = (nq_vwap_ok or tk in cte.SAFE_HAVEN_LONG_EXEMPT) and (vwap_diff >= 0.0) and ((r1 > 0 and px >= r1 * 0.998) or corr_div == "ABOVE_P75" or ((is_pm or is_open15) and pm_hi > 0 and px >= pm_hi * 0.998)) and (rvol_5m >= (1.2 if (is_pm or is_ah) else 1.50)) and (50.0 <= rsi_5m <= 74.0)
                            # OPEN_15M (09:35~09:44) 必须高确信主线 + 强量比 + 真实结构突破，杜绝早盘假针
                            if is_open15:
                                struct_strict = (pm_lo > 0 and day_lo <= pm_lo * 1.003 and px >= pm_lo and vwap_diff >= 0.05) or (pm_hi > 0 and px >= pm_hi * 1.001 and vwap_diff >= 0.05)
                                if not (struct_strict and rvol_5m >= 1.8 and p_l >= 58.0):
                                    sweep_ok = False
                                    brk_ok = False
                            elif is_pm or is_ah:
                                struct_strict = (pm_lo > 0 and day_lo <= pm_lo * 1.004 and px >= pm_lo) or (pm_hi > 0 and px >= pm_hi * 0.999) or (s1 > 0 and abs(px - s1) / s1 <= 0.008)
                                if not struct_strict:
                                    sweep_ok = False
                                    brk_ok = False

                            temp_right_ok = allow_l_temp_bounce and (vwap_diff >= 0.10) and (corr_div == "ABOVE_P75" or sweep_ok) and (rvol_5m >= 1.35)

                            if sweep_ok or brk_ok or temp_right_ok or (is_rth and (not is_open15) and allow_l_normal and (not is_broken) and corr_div == "ABOVE_P75" and vwap_diff >= 0.0 and rvol_5m >= 1.50 and nq_vwap_ok and info["rs_20d"] >= 2.0):
                                is_main_ai = allow_l_normal and (sweep_ok or brk_ok) and (p_l >= main_min_p) and ((p_l - p_s) >= main_min_sp)
                                # INTRADAY_PA_PILOT (中性AI 48%~54.9% 或 温度计右侧反抽): 必须强相对强势 (rs_20d >= +2.0) + 顺风 + 放量走廊突破/Sweep -> 50%先手轻仓
                                is_pa_pilot = (not is_main_ai) and is_rth and (not is_open15) and (
                                    (allow_l_normal and (not is_broken) and p_l >= cfg.pilot_min_prob and (p_l >= p_s) and info["rs_20d"] >= 2.0 and (regime == "BULL_EXPANSION" or nq_vwap_ok) and ((corr_div == "ABOVE_P75" and vwap_diff >= 0.0 and rvol_5m >= 1.50) or (sweep_ok and rvol_5m >= 1.55)))
                                    or temp_right_ok
                                )
                                if is_main_ai or is_pa_pilot:
                                    # 归一化 AI 软权重 (35% 有效方差: [45%, 80%] -> 0~35分) + 65% 价格结构/VWAP/量比/走廊/大盘主线
                                    ai_comp = max(0.0, min(35.0, (p_l - 45.0) * 1.0)) + min(8.0, max(0.0, (p_l - p_s) * 0.25))
                                    struct_pts = (14.0 if sweep_ok else 11.0) + (6.0 if corr_div == "ABOVE_P75" else 0.0)
                                    vwap_pts = 10.0 if (0.0 <= vwap_diff <= 0.9) else (6.0 if vwap_diff >= -0.2 else 2.0)
                                    vol_pts = min(10.0, rvol_5m * 4.5)
                                    macro_pts = (10.0 if regime == "BULL_EXPANSION" else (10.0 if temp_right_ok else 5.0)) + mainline_bonus
                                    comp_score = round(ai_comp + struct_pts + vwap_pts + vol_pts + macro_pts, 2)
                                    # 盘前黄金窗要求更高确信度，防止薄盘占满正盘黄金槽位
                                    pm_ok = (not is_pm) or (is_main_ai and p_l >= 58.0 and (p_l - p_s) >= 12.0 and comp_score >= 54.0)
                                    if comp_score >= 42.0 and pm_ok:
                                        use_pilot = is_pa_pilot or is_open15 or is_pm or is_ah or temp_right_ok
                                        sl_px = _resolve_sl_v57(cfg, tk, "LONG", px, info["atr_pct"], day_lo, day_hi, pm_lo, pm_hi, s1, r1)
                                        raw_cands.append({
                                            "ticker": tk, "direction": "LONG",
                                            "setup": "TEMP_OVERSOLD_BOUNCE" if temp_right_ok else ("SWEEP_RECLAIM_LONG" if sweep_ok else "BREAKOUT_MOMENTUM_LONG"),
                                            "entry_channel": "MAIN_AI" if is_main_ai else "INTRADAY_PA_PILOT",
                                            "entry_tag": ("MAIN_AI_ETF" if is_etf else "MAIN_AI_STOCK") if is_main_ai else "INTRADAY_PA_PILOT",
                                            "entry_window": "PRE_MARKET" if is_pm else ("AFTER_HOURS" if is_ah else ("OPEN_15M" if is_open15 else "RTH")),
                                            "slot_weight": pilot_wt if use_pilot else base_st_wt,
                                            "can_add": True, "temp_bounce": temp_right_ok,
                                            "score": comp_score, "prob": p_l,
                                            "entry_price": round(px, 2), "sl_price": sl_px, "mg": mg,
                                        })

                    # ---------- B. SHORT 通道 ----------
                    inv_surge = bool(tk in cte.INVERSE_ETFS and (gap_pct >= 8.0 or pct_chg >= 8.0 or info["rsi_14"] >= 68.0))
                    high_gap_s = (-gap_pct) > (9.0 if lev3 else 5.0)
                    spike_chase_s = (vwap_diff < (-2.2 if lev3 else -1.4)) or (high_gap_s and abs(vwap_diff) > 0.7)
                    if (tk not in cte.NON_SHORTABLE_TICKERS) and (tk not in V57_NO_SHORT_INVERSE_ETFS) and (not inv_surge) and (not spike_chase_s):
                        if p_s >= 45.0 and (p_l - p_s) < 10.0:
                            b_ctx = is_broken or (regime == "BEAR_DEFENSE") or vix_brake or (pct_chg < 0) or (corr_div == "BELOW_P25")
                            near_rej = ((-1.4 <= vwap_diff <= 0.35) or (r1 > 0 and abs(px - r1) / r1 <= 0.012)) and (vwap_diff <= 0.25) and (rsi_5m <= 56.0)
                            brk_dn = (vwap_diff <= 0.0) and (corr_div == "BELOW_P25") and (rvol_5m >= 1.5) and (rsi_5m <= 48.0)
                            if is_open15:
                                struct_strict_s = (pm_hi > 0 and day_hi >= pm_hi * 0.997 and px <= pm_hi and vwap_diff <= -0.05) or (pm_lo > 0 and px <= pm_lo * 0.999 and vwap_diff <= -0.05)
                                if not (struct_strict_s and rvol_5m >= 1.8 and p_s >= 58.0):
                                    near_rej = False
                                    brk_dn = False
                            elif is_pm or is_ah:
                                struct_strict_s = (pm_hi > 0 and day_hi >= pm_hi * 0.996 and px <= pm_hi) or (pm_lo > 0 and px <= pm_lo * 1.001) or (r1 > 0 and abs(px - r1) / r1 <= 0.008)
                                if not struct_strict_s:
                                    near_rej = False
                                    brk_dn = False
                            if (b_ctx and near_rej) or brk_dn or (is_rth and (not is_open15) and corr_div == "BELOW_P25" and vwap_diff <= 0.0 and rvol_5m >= 1.50 and (long_blocked_base or (not nq_vwap_ok)) and info["rs_20d"] <= -2.0):
                                is_main_ai_s = ((b_ctx and near_rej) or brk_dn) and (p_s >= main_min_p) and ((p_s - p_l) >= main_min_sp)
                                # INTRADAY_PA_PILOT (空头中性AI 48%~54.9%): 相对弱势 (rs_20d <= -2.0) + 顺势做空 + 放量走廊下破 P25 或反抽压制 -> 50%先手轻仓
                                is_pa_pilot_s = (not is_main_ai_s) and is_rth and (not is_open15) and (p_s >= cfg.pilot_min_prob) and (p_s >= p_l) and (info["rs_20d"] <= -2.0) and (long_blocked_base or (not nq_vwap_ok)) and ((corr_div == "BELOW_P25" and vwap_diff <= 0.0 and rvol_5m >= 1.50) or (near_rej and rvol_5m >= 1.60))
                                if is_main_ai_s or is_pa_pilot_s:
                                    ai_comp = max(0.0, min(35.0, (p_s - 45.0) * 1.0)) + min(8.0, max(0.0, (p_s - p_l) * 0.25))
                                    struct_pts = (14.0 if (brk_dn or corr_div == "BELOW_P25") else 11.0) + (5.0 if is_broken else 0.0)
                                    vwap_pts = 10.0 if (-0.9 <= vwap_diff <= 0.1) else 6.0
                                    vol_pts = min(10.0, rvol_5m * 4.5)
                                    macro_pts = (11.0 if long_blocked_base else 5.0) + mainline_bonus
                                    # 仅在「深度超卖 >=48 且 QQQ 真实转涨翻红 >=+0.5% 站稳 VWAP」的右侧反抽时降低做空分，绝不在单边崩盘主跌浪削弱做空
                                    if cfg.use_temp and raw_mb_score >= 48.0 and nq_vwap_ok and qqq_pct >= 0.50:
                                        macro_pts -= 8.0
                                    comp_score = round(ai_comp + struct_pts + vwap_pts + vol_pts + macro_pts, 2)
                                    pm_ok_s = (not is_pm) or (is_main_ai_s and p_s >= 58.0 and (p_s - p_l) >= 12.0 and comp_score >= 54.0)
                                    if comp_score >= 42.0 and pm_ok_s:
                                        use_pilot = is_pa_pilot_s or is_open15 or is_pm or is_ah
                                        sl_px = _resolve_sl_v57(cfg, tk, "SHORT", px, info["atr_pct"], day_lo, day_hi, pm_lo, pm_hi, s1, r1)
                                        raw_cands.append({
                                            "ticker": tk, "direction": "SHORT",
                                            "setup": "CORRIDOR_BREAKDOWN_SHORT" if brk_dn else "REBOUND_REJECT_SHORT",
                                            "entry_channel": "MAIN_AI" if is_main_ai_s else "INTRADAY_PA_PILOT",
                                            "entry_tag": ("MAIN_AI_ETF" if is_etf else "MAIN_AI_STOCK") if is_main_ai_s else "INTRADAY_PA_PILOT",
                                            "entry_window": "PRE_MARKET" if is_pm else ("AFTER_HOURS" if is_ah else ("OPEN_15M" if is_open15 else "RTH")),
                                            "slot_weight": pilot_wt if use_pilot else base_st_wt,
                                            "can_add": True, "temp_bounce": False,
                                            "score": comp_score, "prob": p_s,
                                            "entry_price": round(px, 2), "sl_price": sl_px, "mg": mg,
                                        })

                raw_cands.sort(key=lambda c: (-c["score"], -c["prob"], c["ticker"]))
                used_t, used_g = set(held_tks), set(held_grps)
                exp_idx = min(len(valid_dates) - 1, d_idx + kol.HOLD_TRADING_DAYS)

                # (3a) 空槽开仓
                for c in raw_cands:
                    if len(open_positions) >= cfg.max_positions or entries_today >= max_entries_day:
                        break
                    if is_open15 and open15_entries_today >= 1:
                        break
                    if (is_pm or is_ah) and ext_entries_today >= 1:
                        break
                    tk, mg = c["ticker"], c["mg"]
                    if tk in used_t or (mg and mg in used_g):
                        continue
                    alloc = round(min(cash, cur_equity * c["slot_weight"]), 2)
                    if alloc < 10.0:
                        continue
                    cash = round(cash - alloc, 2)
                    if c.get("temp_bounce"):
                        temp_bounce_entries += 1
                    open_positions.append({
                        "ticker": tk, "direction": c["direction"], "setup": c["setup"],
                        "entry_channel": c["entry_channel"], "entry_tag": c["entry_tag"], "entry_window": c["entry_window"],
                        "entry_score": c["score"], "stage": 1, "can_add": c["can_add"],
                        "slot_weight": c["slot_weight"], "peak_slot_weight": c["slot_weight"],
                        "allocated_capital": alloc, "entry_date": d_str, "entry_day_idx": d_idx,
                        "entry_dt": ts, "entry_min": et_min, "expiry_idx": exp_idx,
                        "orig_entry_price": c["entry_price"], "entry_price": c["entry_price"],
                        "last_price": c["entry_price"], "best_price": c["entry_price"],
                        "sl_price": c["sl_price"], "partial_tp_done": False, "be_stop_active": False,
                        "trail_armed": False, "trail_mult": 1.0, "cor_streak": 0,
                        "corridor_def_done": False, "corridor_def_triggered": False,
                        "swapped_in": False, "temp_bounce": bool(c.get("temp_bounce")),
                        "partial_closed_capital": 0.0, "partial_realized_pnl_usd": 0.0,
                        "max_favorable_return_pct": -rt_slippage * 100.0, "cur_ret_pct": -rt_slippage * 100.0,
                        "cur_vwap_diff": 0.0,
                    })
                    used_t.add(tk)
                    if mg:
                        used_g.add(mg)
                    entries_today += 1
                    if is_open15:
                        open15_entries_today += 1
                    if is_pm or is_ah:
                        ext_entries_today += 1

                # (3b) 防磨损动态换仓：旧仓跨日或持仓满 4h + 明确弱滞 [-1.8%, -0.2%] + 失守 VWAP；新标得分领先 >= swap_score_gap；每日 <=1 次
                if can_try_swap and raw_cands and len(open_positions) >= cfg.max_positions:
                    stagnant_pool = []
                    for idx_p, p in enumerate(open_positions):
                        held_h = (ts - p["entry_dt"]).total_seconds() / 3600.0
                        cur_r = float(p.get("cur_ret_pct", 0.0))
                        vw_d_p = float(p.get("cur_vwap_diff", 0.0))
                        lost_vw_p = (vw_d_p < -0.20) if p["direction"] == "LONG" else (vw_d_p > 0.20)
                        if (held_h >= 4.0 or d_idx > p["entry_day_idx"]) and (-1.8 <= cur_r <= -0.2) and lost_vw_p and (not p.get("be_stop_active")) and (not p.get("partial_tp_done")):
                            stagnant_pool.append((float(p.get("entry_score", 50.0)), cur_r, idx_p, p))
                    if stagnant_pool:
                        stagnant_pool.sort(key=lambda x: (x[0], x[1]))
                        old_sc, _, old_idx, old_pos = stagnant_pool[0]
                        best_c = None
                        for c in raw_cands:
                            if c["ticker"] in used_t or c["entry_channel"] != "MAIN_AI":
                                continue
                            other_grps = {cte.get_mirror_group(p["ticker"]) for j, p in enumerate(open_positions) if j != old_idx and cte.get_mirror_group(p["ticker"])}
                            if c["mg"] and c["mg"] in other_grps:
                                continue
                            if (c["score"] - old_sc) >= cfg.swap_score_gap:
                                best_c = c
                                break
                        if best_c is not None:
                            b_old = (ext_bars.get((d_cur, old_pos["ticker"])) or {}).get(ts)
                            old_exit_px = b_old["close"] if b_old else old_pos["last_price"]
                            old_ret = bt55._net_ret(old_pos["direction"], old_pos["entry_price"], old_exit_px, rt_slippage)
                            old_pnl = round(old_pos["allocated_capital"] * old_ret, 2)
                            cash = round(cash + max(0.0, old_pos["allocated_capital"] + old_pnl), 2)
                            closed_trades.append({
                                "ticker": old_pos["ticker"], "is_3x": kol.is_3x_leveraged(old_pos["ticker"]),
                                "direction": old_pos["direction"], "entry_tag": old_pos.get("entry_tag", "MAIN_AI"),
                                "entry_channel": old_pos.get("entry_channel", "MAIN_AI"),
                                "entry_window": old_pos.get("entry_window", "RTH"),
                                "setup": old_pos.get("setup", "V57"), "stage": old_pos.get("stage", 1),
                                "swapped_in": bool(old_pos.get("swapped_in", False)),
                                "temp_bounce": bool(old_pos.get("temp_bounce", False)),
                                "corridor_def_triggered": bool(old_pos.get("corridor_def_triggered", False)),
                                "max_favorable_return_pct": round(old_pos.get("max_favorable_return_pct", 0.0), 2),
                                "entry_date": old_pos["entry_date"], "entry_month": old_pos["entry_date"][:7],
                                "entry_time_et": old_pos["entry_dt"].strftime("%Y-%m-%d %H:%M:%S"),
                                "orig_entry_price": old_pos["orig_entry_price"], "entry_price": old_pos["entry_price"],
                                "exit_date": d_str, "exit_time_et": ts.strftime("%Y-%m-%d %H:%M:%S"),
                                "exit_price": round(old_exit_px, 2), "outcome": "SWAP_OUT", "exit_reason": "SWAP_OUT",
                                "gap_sl_slip": False, "allocated_capital": old_pos["allocated_capital"],
                                "pnl_usd": old_pnl, "net_return_pct": round(old_ret * 100.0, 2),
                                "hold_hours": round((ts - old_pos["entry_dt"]).total_seconds() / 3600.0, 2),
                                "v54_tp_price": None,
                            })
                            last_exit_by_tk[old_pos["ticker"]] = {"direction": old_pos["direction"], "exit_date": d_str, "exit_dt": ts, "exit_reason": "SWAP_OUT"}
                            open_positions.pop(old_idx)

                            alloc = round(min(cash, cur_equity * best_c["slot_weight"]), 2)
                            if alloc >= 10.0:
                                cash = round(cash - alloc, 2)
                                open_positions.append({
                                    "ticker": best_c["ticker"], "direction": best_c["direction"], "setup": best_c["setup"],
                                    "entry_channel": best_c["entry_channel"], "entry_tag": best_c["entry_tag"], "entry_window": best_c["entry_window"],
                                    "entry_score": best_c["score"], "stage": 1, "can_add": best_c["can_add"],
                                    "slot_weight": best_c["slot_weight"], "peak_slot_weight": best_c["slot_weight"],
                                    "allocated_capital": alloc, "entry_date": d_str, "entry_day_idx": d_idx,
                                    "entry_dt": ts, "entry_min": et_min, "expiry_idx": exp_idx,
                                    "orig_entry_price": best_c["entry_price"], "entry_price": best_c["entry_price"],
                                    "last_price": best_c["entry_price"], "best_price": best_c["entry_price"],
                                    "sl_price": best_c["sl_price"], "partial_tp_done": False, "be_stop_active": False,
                                    "trail_armed": False, "trail_mult": 1.0, "cor_streak": 0,
                                    "corridor_def_done": False, "corridor_def_triggered": False,
                                    "swapped_in": True, "temp_bounce": False,
                                    "partial_closed_capital": 0.0, "partial_realized_pnl_usd": 0.0,
                                    "max_favorable_return_pct": -rt_slippage * 100.0, "cur_ret_pct": -rt_slippage * 100.0,
                                    "cur_vwap_diff": 0.0,
                                })
                                swaps_today += 1
                                swap_count += 1
                                entries_today += 1

        # 日末权益与回撤
        eod_deployed = 0.0
        for pos in open_positions:
            tmap = ext_bars.get((d_cur, pos["ticker"])) or {}
            lp = pos.get("last_price", pos["entry_price"])
            for ts_rev in reversed(ts_list):
                if ts_rev in tmap:
                    lp = tmap[ts_rev]["close"]
                    break
            r_u = bt55._net_ret(pos["direction"], pos["entry_price"], lp, rt_slippage)
            eod_deployed += max(0.0, pos["allocated_capital"] * (1.0 + r_u))
        eod_eq = round(cash + eod_deployed, 2)
        peak_equity = max(peak_equity, eod_eq)
        dd_now = ((peak_equity - eod_eq) / peak_equity * 100.0) if peak_equity > 0 else 0.0
        max_dd = max(max_dd, dd_now)
        equity_curve.append({"date": d_str, "equity": eod_eq, "regime": regime, "raw_mb": raw_mb_score})

    last_d = valid_dates[-1]
    last_ts = (ext_timestamps.get(last_d) or day_timestamps[last_d])[-1]
    for pos in open_positions:
        tmap = ext_bars.get((last_d, pos["ticker"])) or {}
        lp = pos.get("last_price", pos["entry_price"])
        for ts_rev in reversed(ext_timestamps.get(last_d) or day_timestamps[last_d]):
            if ts_rev in tmap:
                lp = tmap[ts_rev]["close"]
                break
        runner_ret = bt55._net_ret(pos["direction"], pos["entry_price"], lp, rt_slippage)
        runner_pnl = round(pos["allocated_capital"] * runner_ret, 2)
        cash = round(cash + max(0.0, pos["allocated_capital"] + runner_pnl), 2)
        tot_alloc = round(pos["allocated_capital"] + pos.get("partial_closed_capital", 0.0), 2)
        tot_pnl = round(pos.get("partial_realized_pnl_usd", 0.0) + runner_pnl, 2)
        tot_ret_pct = round((tot_pnl / tot_alloc) * 100.0, 2) if tot_alloc > 0 else 0.0
        closed_trades.append({
            "ticker": pos["ticker"], "is_3x": kol.is_3x_leveraged(pos["ticker"]),
            "direction": pos["direction"], "entry_tag": pos.get("entry_tag", "MAIN_AI"),
            "entry_channel": pos.get("entry_channel", "MAIN_AI"),
            "entry_window": pos.get("entry_window", "RTH"),
            "setup": pos.get("setup", "M2M"), "stage": pos.get("stage", 1),
            "swapped_in": bool(pos.get("swapped_in", False)),
            "temp_bounce": bool(pos.get("temp_bounce", False)),
            "corridor_def_triggered": bool(pos.get("corridor_def_triggered", False)),
            "max_favorable_return_pct": round(pos.get("max_favorable_return_pct", 0.0), 2),
            "entry_date": pos["entry_date"], "entry_month": pos["entry_date"][:7],
            "entry_time_et": pos["entry_dt"].strftime("%Y-%m-%d %H:%M:%S"),
            "orig_entry_price": pos["orig_entry_price"], "entry_price": pos["entry_price"],
            "exit_date": last_d.isoformat(), "exit_time_et": last_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "exit_price": round(lp, 2), "outcome": "M2M_END", "exit_reason": "M2M_END",
            "gap_sl_slip": False, "allocated_capital": tot_alloc, "pnl_usd": tot_pnl,
            "net_return_pct": tot_ret_pct,
            "hold_hours": round((last_ts - pos["entry_dt"]).total_seconds() / 3600.0, 2),
            "v54_tp_price": None,
        })

    for tr in closed_trades:
        tr["sl_whipsaw_24h"] = bt55._check_sl_whipsaw_24h(tr, valid_dates, day_timestamps, bars_by_day_tk)

    end_eq = round(cash, 2)
    peak_equity = max(peak_equity, end_eq)
    max_dd = round(max(max_dd, (peak_equity - end_eq) / peak_equity * 100.0 if peak_equity > 0 else 0.0), 2)

    win23_turned_loss = sum(
        1 for t in closed_trades
        if t.get("max_favorable_return_pct", 0.0) >= (3.5 if t["is_3x"] else 2.3) and t["pnl_usd"] < 0
    )
    cor_trades = [t for t in closed_trades if t.get("corridor_def_triggered")]
    swap_out_trades = [t for t in closed_trades if t.get("exit_reason") == "SWAP_OUT"]
    swap_in_trades = [t for t in closed_trades if t.get("swapped_in")]

    return {
        "arm": cfg.name,
        "label": cfg.label,
        "rt_slippage_bps": round(rt_slippage * 10000, 1),
        "ending_equity": end_eq,
        "net_return_pct": round((end_eq - bt55.INITIAL_CAPITAL) / bt55.INITIAL_CAPITAL * 100.0, 2),
        "max_drawdown_pct": max_dd,
        "summary": bt55._summarize_trades(closed_trades),
        "exit_breakdown": bt55._breakdown_exits(closed_trades),
        "asset_class_attribution": bt55._breakdown_asset_class(closed_trades),
        "direction_attribution": bt55._breakdown_direction(closed_trades),
        "monthly_attribution": bt55._breakdown_monthly(closed_trades),
        "channel_attribution": {
            "MAIN_AI": bt55._summarize_trades([t for t in closed_trades if t.get("entry_channel") == "MAIN_AI"]),
            "MAIN_AI_STOCK": bt55._summarize_trades([t for t in closed_trades if t.get("entry_tag") == "MAIN_AI_STOCK"]),
            "MAIN_AI_ETF": bt55._summarize_trades([t for t in closed_trades if t.get("entry_tag") == "MAIN_AI_ETF"]),
            "INTRADAY_PA_PILOT": bt55._summarize_trades([t for t in closed_trades if t.get("entry_channel") == "INTRADAY_PA_PILOT"]),
        },
        "window_attribution": {
            w: bt55._summarize_trades([t for t in closed_trades if t.get("entry_window") == w])
            for w in ("PRE_MARKET", "OPEN_15M", "RTH", "AFTER_HOURS")
        },
        "v57_diagnostics": {
            "corridor_def_triggers": corridor_def_triggers,
            "corridor_def_partial_locks": corridor_def_partial_locks,
            "corridor_def_sl_tightens": corridor_def_sl_tightens,
            "corridor_def_trades": bt55._summarize_trades(cor_trades),
            "swap_count": swap_count,
            "swap_out_trades": bt55._summarize_trades(swap_out_trades),
            "swap_in_trades": bt55._summarize_trades(swap_in_trades),
            "soxs_eod_trims": soxs_eod_trims,
            "temp_bounce_entries": temp_bounce_entries,
            "temp_bounce_trades": bt55._summarize_trades([t for t in closed_trades if t.get("temp_bounce")]),
            "temp_short_tightens": temp_short_tightens,
            "temp_bubble_blocks": temp_bubble_blocks,
            "win23_turned_loss_count": win23_turned_loss,
        },
        "gap_sl_events": sum(1 for t in closed_trades if t.get("gap_sl_slip")),
        "equity_curve": equity_curve,
        "trades": closed_trades,
    }


def _count_win23_turned_loss(trades: List[Dict[str, Any]]) -> int:
    cnt = 0
    for t in trades:
        mfe = float(t.get("max_favorable_return_pct", 0.0) or 0.0)
        thr = 3.5 if t.get("is_3x") else 2.3
        if mfe >= thr and float(t.get("pnl_usd", 0.0)) < 0:
            cnt += 1
    return cnt


def render_v57_markdown(valid_dates: List[date], results_by_slip: Dict[str, List[Dict[str, Any]]]) -> str:
    lines = [
        "# V5.7 灵活版 KOL 狙击引擎 — 52 日严格防穿越离线回测与全维度消融报告",
        "",
        f"- **回测样本区间**：`{valid_dates[0].isoformat()} ~ {valid_dates[-1].isoformat()}`（共 `{len(valid_dates)}` 个完整常规交易日，复用 `data_cache/intraday_5m_60d.parquet`）",
        "- **严格防穿越口径**：所有日线 AI 概率/均线/撑压、盘中走廊 `P25/P75`（仅用 `< D` 的近 15 日历史）、`raw_macro_bottom_score`、日内 `VWAP / RSI / RVOL` 均严格因果；隔夜跳空按次日开盘价真实成交；未编造任何夜盘 (`OVERNIGHT_APAC_PILOT`) 历史成交。",
        "- **滑点口径**：同时报告 **写实 `25 bps`（主验收口径）** 与 **名义 `3 bps`** 往返滑点；盘中加仓/保本/止盈触发均按真实盘中价格毛幅判定，所有已实现平仓与分批落袋 100% 全额扣减往返滑点。",
        "",
    ]
    for slip_key, title in (("25bps", "## 1. 主验收对比表（写实 `25 bps` 往返滑点）"), ("3bps", "## 2. 基准对比表（名义 `3 bps` 往返滑点）")):
        lines.append(title)
        lines.append("")
        lines.append("| 策略组 | 净收益 | 盈亏比 PF | 最大回撤 | 胜率 WR | 笔数 | 均持仓 | 9-10月震荡 | 11月暴跌反抽 | 2~3%浮盈倒亏 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in results_by_slip[slip_key]:
            s = r["summary"]
            ma = r["monthly_attribution"]
            m910 = ma["2026-09_to_10_BullChop"]["net_pnl_usd"]
            m11 = ma["2026-11_CrashSelloff"]["net_pnl_usd"]
            w23 = r.get("v57_diagnostics", {}).get("win23_turned_loss_count")
            if w23 is None:
                w23 = _count_win23_turned_loss(r.get("trades", []))
            lines.append(
                f"| `{r['arm']}` | **{r['net_return_pct']:+.2f}%** | {s['profit_factor']:.2f} | -{r['max_drawdown_pct']:.2f}% | "
                f"{s['win_rate_pct']:.1f}% | {s['count']} | {s['avg_hold_hours']:.1f}h | "
                f"${m910:+.2f} | ${m11:+.2f} | {w23} |"
            )
        lines.append("")

    by25 = {r["arm"]: r for r in results_by_slip["25bps"]}
    v3 = by25["V57_Flexible_3Slot"]
    v4 = by25["V57_Flexible_4Slot"]
    lines.append("## 3. V5.7 通道与时段拆解（`25 bps`，3槽主推组 vs 4槽对照组）")
    lines.append("")
    lines.append("| 组别 | 维度 | 笔数 | 胜率 | PF | 净盈亏 ($) | 均笔收益 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for arm_name, r in (("V57_Flexible_3Slot", v3), ("V57_Flexible_4Slot", v4)):
        for ck in ("MAIN_AI_STOCK", "MAIN_AI_ETF", "INTRADAY_PA_PILOT"):
            x = r["channel_attribution"][ck]
            lines.append(f"| `{arm_name}` | 通道 `{ck}` | {x['count']} | {x['win_rate_pct']:.1f}% | {x['profit_factor']:.2f} | ${x['net_pnl_usd']:+.2f} | {x['avg_ret_pct']:+.2f}% |")
        for wk in ("PRE_MARKET", "OPEN_15M", "RTH"):
            x = r["window_attribution"][wk]
            lines.append(f"| `{arm_name}` | 时段 `{wk}` | {x['count']} | {x['win_rate_pct']:.1f}% | {x['profit_factor']:.2f} | ${x['net_pnl_usd']:+.2f} | {x['avg_ret_pct']:+.2f}% |")
    lines.append("")

    lines.append("## 4. 六维机制专项消融（以 `V57_Flexible_3Slot` @ `25 bps` 为基准）")
    lines.append("")
    lines.append("| 消融项 | 完整版 (`3Slot`) | 关闭/切换后 | 收益差 (完整 - 消融) | 回撤差 | 11月暴跌反抽差 | 专项诊断指标 |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    b_ret = v3["net_return_pct"]
    b_dd = v3["max_drawdown_pct"]
    b_nov = v3["monthly_attribution"]["2026-11_CrashSelloff"]["net_pnl_usd"]
    d3 = v3["v57_diagnostics"]
    ab_rows = [
        ("① 大盘/日内温度计", "V57_3Slot_NoTemp", f"泡沫拦截={d3['temp_bubble_blocks']}, 右侧反抽={d3['temp_bounce_entries']}, 空单收紧={d3['temp_short_tightens']}"),
        ("② 走廊反向防守 (`CORRIDOR_DEFENSE`)", "V57_3Slot_NoCorDef", f"触发={d3['corridor_def_triggers']}次, 胜率={d3['corridor_def_trades']['win_rate_pct']:.0f}%, 盈亏=${d3['corridor_def_trades']['net_pnl_usd']:+.2f}"),
        ("③ 动态换仓 (`SWAP_OUT/IN`)", "V57_3Slot_NoSwap", f"换仓={d3['swap_count']}次, 换出=${d3['swap_out_trades']['net_pnl_usd']:+.2f}, 换入=${d3['swap_in_trades']['net_pnl_usd']:+.2f} (WR {d3['swap_in_trades']['win_rate_pct']:.1f}%)"),
        ("④ `SOXS/SQQQ` 尾盘减半", "V57_3Slot_WithSoxsTrim", f"开启尾盘减半次数={by25['V57_3Slot_WithSoxsTrim']['v57_diagnostics']['soxs_eod_trims']}"),
        ("⑤ `09:35~09:44` 开盘半仓", "V57_3Slot_NoOpen15m", f"开盘半仓={v3['window_attribution']['OPEN_15M']['count']}笔, 盈亏=${v3['window_attribution']['OPEN_15M']['net_pnl_usd']:+.2f}"),
        ("⑥ `07:30~09:25` 盘前黄金窗", "V57_3Slot_NoExtWin", f"盘前={v3['window_attribution']['PRE_MARKET']['count']}笔, 盈亏=${v3['window_attribution']['PRE_MARKET']['net_pnl_usd']:+.2f}"),
    ]
    for title, ab_name, note in ab_rows:
        ab = by25[ab_name]
        ab_ret = ab["net_return_pct"]
        ab_dd = ab["max_drawdown_pct"]
        ab_nov = ab["monthly_attribution"]["2026-11_CrashSelloff"]["net_pnl_usd"]
        lines.append(
            f"| {title} | {b_ret:+.2f}% (DD -{b_dd:.2f}%) | {ab_ret:+.2f}% (DD -{ab_dd:.2f}%) | "
            f"{(b_ret - ab_ret):+.2f}% | {(ab_dd - b_dd):+.2f}% | ${(b_nov - ab_nov):+.2f} | {note} |"
        )
    lines.append("")
    lines.append("> **夜盘通道说明**：`OVERNIGHT_APAC_PILOT` 绝不在离线回测中编造夜盘成交；将在阶段三通过独立单元测试与实盘独立标签审计验证。")
    return "\n".join(lines) + "\n"


def main():
    df5 = pd.read_parquet(bt55.PARQUET_5M)
    daily_df = bt55.load_daily_panels()
    dt_s = pd.to_datetime(df5["Datetime"])
    hhmm = dt_s.dt.hour * 60 + dt_s.dt.minute
    rth_dates = df5[(hhmm >= 9 * 60 + 30) & (hhmm <= 15 * 60 + 55)].groupby(dt_s.dt.date)["Datetime"].nunique()
    cand_dates = sorted([d for d, cnt in rth_dates.items() if cnt >= 20])
    macro_ctx, daily_ctx = bt55.precompute_causal_daily_context(daily_df, cand_dates)
    enrich_macro_with_raw_temp(macro_ctx, daily_ctx)
    valid_dates, day_timestamps, bars_by_day_tk, pm_bounds = bt55.build_causal_5m_market_replay(df5, daily_ctx)
    ext_timestamps, ext_bars, corridor_bands = build_extended_and_corridor(df5, valid_dates, bars_by_day_tk)

    v57_arms = [
        V57Config(name="V57_Flexible_3Slot", label="V5.7 灵活KOL (3槽×33% · 全特性完整版)", max_positions=3, starter_weight_1x=0.22, addon_weight_1x=0.11, starter_weight_3x=0.22, addon_weight_3x=0.11),
        V57Config(name="V57_Flexible_4Slot", label="V5.7 灵活KOL (4槽×25% · 全特性完整版)", max_positions=4, starter_weight_1x=0.16, addon_weight_1x=0.09, starter_weight_3x=0.16, addon_weight_3x=0.09),
        V57Config(name="V57_3Slot_NoTemp", label="V5.7 消融①：关闭温度计 (3槽)", max_positions=3, starter_weight_1x=0.22, addon_weight_1x=0.11, starter_weight_3x=0.22, addon_weight_3x=0.11, use_temp=False),
        V57Config(name="V57_3Slot_NoCorDef", label="V5.7 消融②：关闭走廊防守 (3槽)", max_positions=3, starter_weight_1x=0.22, addon_weight_1x=0.11, starter_weight_3x=0.22, addon_weight_3x=0.11, use_corridor_def=False),
        V57Config(name="V57_3Slot_NoSwap", label="V5.7 消融③：关闭动态换仓 (3槽)", max_positions=3, starter_weight_1x=0.22, addon_weight_1x=0.11, starter_weight_3x=0.22, addon_weight_3x=0.11, use_swap=False),
        V57Config(name="V57_3Slot_WithSoxsTrim", label="V5.7 消融④：开启SOXS尾盘减半 (3槽)", max_positions=3, starter_weight_1x=0.22, addon_weight_1x=0.11, starter_weight_3x=0.22, addon_weight_3x=0.11, soxs_eod_trim=True),
        V57Config(name="V57_3Slot_NoOpen15m", label="V5.7 消融⑤：关闭 09:35~09:44 开盘半仓 (3槽)", max_positions=3, starter_weight_1x=0.22, addon_weight_1x=0.11, starter_weight_3x=0.22, addon_weight_3x=0.11, allow_open15=False),
        V57Config(name="V57_3Slot_NoExtWin", label="V5.7 消融⑥：关闭盘前黄金窗 (仅正盘)", max_positions=3, starter_weight_1x=0.22, addon_weight_1x=0.11, starter_weight_3x=0.22, addon_weight_3x=0.11, use_extended_win=False),
    ]
    base_arms = [a for a in bt55.PRE_REGISTERED_ARMS if a.name in ("Baseline_V54", "V55_D_Hybrid")]

    results_by_slip: Dict[str, List[Dict[str, Any]]] = {}
    for slip_key, rt_slip, _ in bt55.SLIPPAGE_REGIMES:
        arm_res = []
        for bcfg in base_arms:
            r = bt55.run_arm_replay(bcfg, rt_slip, valid_dates, day_timestamps, bars_by_day_tk, pm_bounds, macro_ctx, daily_ctx)
            # 补充 Baseline_V54 / V55_D_Hybrid 的精确 2~3% 浮盈倒亏笔数 (逐根 5m 还原 MFE)
            w23 = 0
            for tr in r["trades"]:
                ed = date.fromisoformat(tr["entry_date"])
                xd = date.fromisoformat(tr["exit_date"])
                et_s = datetime.strptime(tr["entry_time_et"], "%Y-%m-%d %H:%M:%S")
                xt_s = datetime.strptime(tr["exit_time_et"], "%Y-%m-%d %H:%M:%S")
                ep = float(tr["orig_entry_price"])
                mfe_max = 0.0
                for d_cur in valid_dates:
                    if d_cur < ed or d_cur > xd:
                        continue
                    tmap = bars_by_day_tk.get((d_cur, tr["ticker"])) or {}
                    for ts_b, b in tmap.items():
                        if ts_b < et_s or ts_b > xt_s:
                            continue
                        g = ((b["close"] - ep) / ep * 100.0) if tr["direction"] == "LONG" else ((ep - b["close"]) / ep * 100.0)
                        if g > mfe_max:
                            mfe_max = g
                tr["max_favorable_return_pct"] = round(mfe_max, 2)
                if mfe_max >= (3.5 if tr.get("is_3x") else 2.3) and tr["pnl_usd"] < 0:
                    w23 += 1
            r["win23_turned_loss_count"] = w23
            r.setdefault("v57_diagnostics", {})["win23_turned_loss_count"] = w23
            arm_res.append(r)
        for vcfg in v57_arms:
            r = run_v57(vcfg, rt_slip, valid_dates, ext_timestamps, ext_bars, corridor_bands, pm_bounds, macro_ctx, daily_ctx, day_timestamps, bars_by_day_tk)
            arm_res.append(r)
        results_by_slip[slip_key] = arm_res

    print(f"=== 52-DAY BACKTEST ({valid_dates[0]} ~ {valid_dates[-1]}) ===")
    for slip_key in ("3bps", "25bps"):
        print(f"\n--- SLIPPAGE: {slip_key} ---")
        for r in results_by_slip[slip_key]:
            s = r["summary"]
            ma = r["monthly_attribution"]
            m910 = ma["2026-09_to_10_BullChop"]
            m11 = ma["2026-11_CrashSelloff"]
            w23 = r.get("v57_diagnostics", {}).get("win23_turned_loss_count", 0)
            print(f"  {r['arm']:22s} | n={s['count']:3d} | WR={s['win_rate_pct']:5.1f}% | PF={s['profit_factor']:4.2f} | ret={r['net_return_pct']:+6.2f}% | DD=-{r['max_drawdown_pct']:5.2f}% | hold={s['avg_hold_hours']:4.1f}h | 9-10m={m910['net_pnl_usd']:+6.2f} | Nov={m11['net_pnl_usd']:+6.2f} | w23L={w23:2d}")

    print("\n--- DETAILED V5.7 DIAGNOSTICS (25 bps) ---")
    for r in results_by_slip["25bps"]:
        if not r["arm"].startswith("V57"):
            continue
        ch = r["channel_attribution"]
        w = r["window_attribution"]
        d = r["v57_diagnostics"]
        eb = r["exit_breakdown"]
        print(f"\n[{r['arm']}] ret={r['net_return_pct']:+.2f}% PF={r['summary']['profit_factor']:.2f} WR={r['summary']['win_rate_pct']:.1f}% DD=-{r['max_drawdown_pct']:.2f}% n={r['summary']['count']}")
        print("  Exits:", {k: (v["count"], v["avg_ret_pct"], v["net_pnl_usd"]) for k, v in eb.items() if v.get("count", 0) > 0})
        for ck in ("MAIN_AI", "MAIN_AI_STOCK", "MAIN_AI_ETF", "INTRADAY_PA_PILOT"):
            x = ch[ck]
            print(f"  Channel {ck:18s}: n={x['count']:3d} WR={x['win_rate_pct']:5.1f}% PF={x['profit_factor']:5.2f} PnL={x['net_pnl_usd']:+7.2f} avg={x['avg_ret_pct']:+5.2f}%")
        print("  Windows:", {wk: (wv["count"], wv["win_rate_pct"], wv["net_pnl_usd"]) for wk, wv in w.items()})
        print("  Diag:", {k: v for k, v in d.items() if not isinstance(v, dict)})
        print("  CorDefTrades:", d["corridor_def_trades"], "| SwapOut:", d["swap_out_trades"], "| SwapIn:", d["swap_in_trades"], "| TempBounce:", d["temp_bounce_trades"])

    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(REPORT_V57_MD, "w", encoding="utf-8") as f:
        f.write(render_v57_markdown(valid_dates, results_by_slip))
    with open(REPORT_V57_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "sample_start": valid_dates[0].isoformat(),
            "sample_end": valid_dates[-1].isoformat(),
            "trading_days": len(valid_dates),
            "results": results_by_slip,
        }, f, ensure_ascii=False, indent=2)
    print(f"\nSaved: {REPORT_V57_MD} & {REPORT_V57_JSON}")


if __name__ == "__main__":
    main()
