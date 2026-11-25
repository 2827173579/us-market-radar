# -*- coding: utf-8 -*-
"""
V5.5 激进 KOL 狙击轨 60 日纯离线防穿越对照回测 (backtest_v55_kol_60d.py)

绝对隔离声明：
- 纯离线只读脚本，绝不修改任何生产代码、绝不触碰任何 data_cache/*ledger*.json、绝不影响守护进程。
- 严格零未来函数穿越：所有日线指标 (MA50/EMA20/ATR/RSI14/RS20d/S1/S2/R1/R2/大盘体制/代理概率)
  严格仅使用 date < current_date 的历史日线；所有盘中指标 (VWAP/RSI5m/RVOL5m/抗跌Top5/QQQ VWAP)
  严格仅使用 <= current_bar 的 5m K 线。
- 真实隔夜跳空冲击建模：若隔夜开盘价直接跳空越过止损价，强制按实际开盘价 (bar.Open) 成交，绝不按理想止损价幻想成交。
- 预注册 4 组对照 × 2 档滑点 (3 bps 名义 / 25 bps 写实压力) 同步回放，结果输出至
  reports/v55_backtest_60d.md 与 reports/v55_backtest_60d.json。
"""

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import copy_trade_engine as cte
import copy_trade_kol_engine as kol

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARQUET_5M = os.path.join(BASE_DIR, "data_cache", "intraday_5m_60d.parquet")
PARQUET_DAILY_MAIN = os.path.join(BASE_DIR, "data_cache", "ml_panel_5y.parquet")
PARQUET_DAILY_EXT = os.path.join(BASE_DIR, "data_cache", "ml_extended_panel.parquet")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
REPORT_MD_PATH = os.path.join(REPORTS_DIR, "v55_backtest_60d.md")
REPORT_JSON_PATH = os.path.join(REPORTS_DIR, "v55_backtest_60d.json")

INITIAL_CAPITAL = 1000.00
SLIPPAGE_REGIMES = [
    ("3bps", 0.0003, "名义滑点 (双边 3 bps)"),
    ("25bps", 0.0025, "写实压力滑点 (双边 25 bps)"),
]


@dataclass
class ArmConfig:
    name: str
    label: str
    is_v54: bool = False
    max_positions: int = 2
    starter_weight_1x: float = 0.30
    addon_weight_1x: float = 0.20
    starter_weight_3x: float = 0.30
    addon_weight_3x: float = 0.20
    sl_min_1x: float = 0.020
    sl_max_1x: float = 0.045
    sl_min_3x: float = 0.025
    sl_max_3x: float = 0.045
    allow_bear_bounce: bool = True
    bear_bounce_weight: float = 0.15
    allow_flip: bool = True
    flip_cooldown_min: int = 45
    flip_weight: float = 0.25
    min_prob_long: float = 50.0
    min_prob_short: float = 52.0
    min_spread: float = 6.0
    addon_sl_buffer: float = 0.0          # 0.0=死贴 orig_entry；0.01=外侧 1.0% 呼吸缓冲
    weak_exit_min_days: int = 1           # 1=T+1 早撤；2=推迟至 T+2 早撤


PRE_REGISTERED_ARMS: List[ArmConfig] = [
    ArmConfig(
        name="Baseline_V54",
        label="Baseline V5.4 (4槽×25% · T+5)",
        is_v54=True,
        max_positions=4,
    ),
    ArmConfig(
        name="V55_A_Live",
        label="V5.5-A 线上现行 (2槽×50% · 3x止损2.5%~4.5% · 含15%反抽)",
        is_v54=False,
        max_positions=2,
        starter_weight_1x=0.30,
        addon_weight_1x=0.20,
        starter_weight_3x=0.30,
        addon_weight_3x=0.20,
        sl_min_1x=0.020,
        sl_max_1x=0.045,
        sl_min_3x=0.025,
        sl_max_3x=0.045,
        allow_bear_bounce=True,
        bear_bounce_weight=0.15,
    ),
    ArmConfig(
        name="V55_B_Wide3x",
        label="V5.5-B 3x宽止损+等风险降仓 (3x止损4.5%~7.5% · 3x满仓30%)",
        is_v54=False,
        max_positions=2,
        starter_weight_1x=0.30,
        addon_weight_1x=0.20,
        starter_weight_3x=0.18,
        addon_weight_3x=0.12,
        sl_min_1x=0.020,
        sl_max_1x=0.045,
        sl_min_3x=0.045,
        sl_max_3x=0.075,
        allow_bear_bounce=True,
        bear_bounce_weight=0.15,
    ),
    ArmConfig(
        name="V55_C_NoBearBounce",
        label="V5.5-C B组+关闭空头反抽 (熊市纯顺势空/反向ETF)",
        is_v54=False,
        max_positions=2,
        starter_weight_1x=0.30,
        addon_weight_1x=0.20,
        starter_weight_3x=0.18,
        addon_weight_3x=0.12,
        sl_min_1x=0.020,
        sl_max_1x=0.045,
        sl_min_3x=0.045,
        sl_max_3x=0.075,
        allow_bear_bounce=False,
        bear_bounce_weight=0.0,
    ),
    ArmConfig(
        name="V55_D_Hybrid",
        label="V5.5-D 混合提纯 (55%/10%门禁 · 去反抽/FLIP · 加仓-1%缓冲 · T+2早撤)",
        is_v54=False,
        max_positions=2,
        starter_weight_1x=0.30,
        addon_weight_1x=0.20,
        starter_weight_3x=0.30,
        addon_weight_3x=0.20,
        sl_min_1x=0.020,
        sl_max_1x=0.045,
        sl_min_3x=0.025,
        sl_max_3x=0.045,
        allow_bear_bounce=False,
        bear_bounce_weight=0.0,
        allow_flip=False,
        min_prob_long=55.0,
        min_prob_short=55.0,
        min_spread=10.0,
        addon_sl_buffer=0.01,
        weak_exit_min_days=2,
    ),
]


# ============================================================================
# 1. 严格零穿越的历史日线与 5m 微结构预计算引擎
# ============================================================================

def _wilder_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    out = np.full(n, 50.0, dtype=float)
    if n <= period:
        return out
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = float(np.mean(gains[:period]))
    avg_l = float(np.mean(losses[:period]))
    for i in range(period, n):
        if i > period:
            avg_g = (avg_g * (period - 1) + gains[i - 1]) / period
            avg_l = (avg_l * (period - 1) + losses[i - 1]) / period
        if avg_l < 1e-12:
            out[i] = 100.0 if avg_g > 1e-12 else 50.0
        else:
            rs = avg_g / avg_l
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def load_daily_panels() -> pd.DataFrame:
    d1 = pd.read_parquet(PARQUET_DAILY_MAIN)[["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"]]
    d2 = pd.read_parquet(PARQUET_DAILY_EXT)[["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"]]
    df = pd.concat([d1, d2], ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"]).dt.date
    df["Ticker"] = df["Ticker"].astype(str).str.strip().str.upper()
    df = df.sort_values(["Ticker", "Date"]).drop_duplicates(["Ticker", "Date"], keep="last").reset_index(drop=True)
    return df


def precompute_causal_daily_context(
    daily_df: pd.DataFrame,
    trading_dates: List[date],
) -> Tuple[Dict[date, Dict[str, Any]], Dict[date, Dict[str, Dict[str, Any]]]]:
    """
    对每个交易日 D，严格仅使用 date < D 的日线数据，计算：
    - macro_ctx[D]: market_regime, vix_hard_brake, panic_clearing, breadth_ma20_pct, vixy_5d_pct
    - ticker_ctx[D][tk]: prev_close, ma50, ema20, atr_pct, rsi_14, rs_20d, s1, s2, r1, r2,
                         is_broken_bearish, is_deep_pullback, is_crash_halt,
                         calibrated_win_rate_pct, calibrated_short_win_rate_pct
    """
    # 按标的预计算日线序列（每条记录的指标都是截至当日收盘，使用时严格取 < D 的最后一条！）
    per_tk_rows: Dict[str, pd.DataFrame] = {}
    spy_ret20_by_date: Dict[date, float] = {}

    for tk, grp in daily_df.groupby("Ticker"):
        g = grp.sort_values("Date").reset_index(drop=True).copy()
        c = g["Close"].astype(float).values
        h = g["High"].astype(float).values
        l = g["Low"].astype(float).values
        n = len(g)
        if n < 25:
            continue
        s_close = pd.Series(c)
        ma50 = s_close.rolling(50, min_periods=20).mean().values
        ema20 = s_close.ewm(span=20, adjust=False).mean().values
        prev_c = np.roll(c, 1)
        prev_c[0] = c[0]
        tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
        atr14 = pd.Series(tr).rolling(14, min_periods=5).mean().values
        atr_pct = np.where(c > 0, (atr14 / c) * 100.0, 3.0)
        rsi14 = _wilder_rsi(c, 14)
        ret5 = s_close.pct_change(5).fillna(0.0).values * 100.0
        ret20 = s_close.pct_change(20).fillna(0.0).values * 100.0
        ret1 = s_close.pct_change(1).fillna(0.0).values * 100.0
        low5 = pd.Series(l).rolling(5, min_periods=3).min().values
        high5 = pd.Series(h).rolling(5, min_periods=3).max().values
        low20 = pd.Series(l).rolling(20, min_periods=5).min().values
        high20 = pd.Series(h).rolling(20, min_periods=5).max().values
        vixy_min60 = pd.Series(c).rolling(60, min_periods=20).min().values
        vixy_max60 = pd.Series(c).rolling(60, min_periods=20).max().values
        vixy_pos60 = np.where((vixy_max60 - vixy_min60) > 1e-6, (c - vixy_min60) / (vixy_max60 - vixy_min60), 0.5)
        vixy_peak10 = pd.Series(h).rolling(10, min_periods=5).max().values
        vixy_dd_from_peak = np.where(vixy_peak10 > 0, (vixy_peak10 - c) / vixy_peak10 * 100.0, 0.0)

        g["ma50"] = ma50
        g["ema20"] = ema20
        g["atr_pct"] = atr_pct
        g["rsi14"] = rsi14
        g["ret1"] = ret1
        g["ret5"] = ret5
        g["ret20"] = ret20
        g["low5"] = low5
        g["high5"] = high5
        g["low20"] = low20
        g["high20"] = high20
        g["vixy_pos60"] = vixy_pos60
        g["vixy_dd_from_peak"] = vixy_dd_from_peak
        per_tk_rows[tk] = g
        if tk == "SPY":
            for dt_v, r20_v in zip(g["Date"].values, ret20):
                spy_ret20_by_date[dt_v] = float(r20_v)

    macro_ctx: Dict[date, Dict[str, Any]] = {}
    ticker_ctx: Dict[date, Dict[str, Dict[str, Any]]] = {}

    for d_cur in trading_dates:
        # 严格只取 date < d_cur 的最新日线行
        day_rows: Dict[str, Dict[str, Any]] = {}
        above_ema20_cnt = 0
        total_cnt = 0
        for tk, g in per_tk_rows.items():
            idx = int(np.searchsorted(g["Date"].values, d_cur, side="left")) - 1
            if idx < 0:
                continue
            row = g.iloc[idx]
            pc = float(row["Close"])
            if pc <= 0:
                continue
            ma50 = float(row["ma50"]) if not math.isnan(float(row["ma50"])) else pc
            ema20 = float(row["ema20"]) if not math.isnan(float(row["ema20"])) else pc
            atr_p = max(1.0, min(18.0, float(row["atr_pct"]) if not math.isnan(float(row["atr_pct"])) else 3.0))
            rsi14 = float(row["rsi14"])
            r1 = float(row["ret1"])
            r5 = float(row["ret5"])
            r20 = float(row["ret20"])
            spy_r20 = spy_ret20_by_date.get(row["Date"], 0.0)
            rs_20d = r20 - spy_r20

            # 经典 Pivot + 5d/20d 结构锚点（严格 < d_cur）
            ph = float(row["High"])
            pl = float(row["Low"])
            piv = (ph + pl + pc) / 3.0
            piv_s1 = 2.0 * piv - ph
            piv_r1 = 2.0 * piv - pl
            s_cands = [x for x in (piv_s1, float(row["low5"]), float(row["low20"]), ema20, ma50) if 0 < x < pc * 0.996]
            r_cands = [x for x in (piv_r1, float(row["high5"]), float(row["high20"]), ema20, ma50) if x > pc * 1.004]
            min_step = pc * max(0.015, min(0.06, atr_p / 100.0 * 0.65))
            s1 = max(s_cands) if s_cands else round(pc * (1.0 - atr_p / 100.0 * 0.9), 2)
            s2_list = [x for x in s_cands if x <= s1 - min_step]
            s2 = max(s2_list) if s2_list else round(s1 - min_step * 1.2, 2)
            r1_lvl = min(r_cands) if r_cands else round(pc * (1.0 + atr_p / 100.0 * 0.9), 2)
            r2_list = [x for x in r_cands if x >= r1_lvl + min_step]
            r2_lvl = min(r2_list) if r2_list else round(r1_lvl + min_step * 1.2, 2)

            if tk not in cte.INVERSE_ETFS and tk not in ("GLD", "TLT"):
                total_cnt += 1
                if pc > ema20:
                    above_ema20_cnt += 1

            bias_ma50 = ((pc - ma50) / ma50 * 100.0) if ma50 > 0 else 0.0
            bias_ema20 = ((pc - ema20) / ema20 * 100.0) if ema20 > 0 else 0.0

            day_rows[tk] = {
                "prev_date": str(row["Date"]),
                "prev_close": round(pc, 4),
                "ma50": round(ma50, 4),
                "ema20": round(ema20, 4),
                "bias_ma50": round(bias_ma50, 2),
                "bias_ema20": round(bias_ema20, 2),
                "atr_pct": round(atr_p, 2),
                "rsi_14": round(rsi14, 1),
                "ret_1d": round(r1, 2),
                "ret_5d": round(r5, 2),
                "ret_20d": round(r20, 2),
                "rs_20d": round(rs_20d, 2),
                "s1": round(s1, 2),
                "s2": round(s2, 2),
                "r1": round(r1_lvl, 2),
                "r2": round(r2_lvl, 2),
                "is_broken_bearish": bool(pc < ma50 and pc < ema20),
                "is_deep_pullback": bool(r5 <= -5.5 or rsi14 <= 34.0),
                "is_crash_halt": bool(r1 <= (-11.0 if kol.is_3x_leveraged(tk) else -7.5)),
                "vixy_pos60": float(row["vixy_pos60"]),
                "vixy_dd_from_peak": float(row["vixy_dd_from_peak"]),
            }

        breadth = (above_ema20_cnt / total_cnt * 100.0) if total_cnt > 0 else 50.0
        qqq_r = day_rows.get("QQQ") or {}
        smh_r = day_rows.get("SMH") or day_rows.get("SOXX") or {}
        vixy_r = day_rows.get("VIXY") or {}

        qqq_b50 = float(qqq_r.get("bias_ma50", 0.0))
        smh_b50 = float(smh_r.get("bias_ma50", 0.0))
        qqq_b20 = float(qqq_r.get("bias_ema20", 0.0))
        vixy_r5 = float(vixy_r.get("ret_5d", 0.0))
        vixy_r1 = float(vixy_r.get("ret_1d", 0.0))
        vixy_pos = float(vixy_r.get("vixy_pos60", 0.5))
        vixy_dd = float(vixy_r.get("vixy_dd_from_peak", 0.0))

        # 严格因果大盘体制与 VIXY 倒挂硬刹车代理
        vix_hard_brake = bool((vixy_r5 >= 10.0 and vixy_pos >= 0.72) or (vixy_pos >= 0.85 and vixy_r1 >= 0.0))
        panic_clearing = bool((not vix_hard_brake) and vixy_dd >= 7.0 and vixy_r1 <= 2.0 and qqq_b20 > -3.5)
        if vix_hard_brake or (qqq_b50 < -2.0 and smh_b50 < -2.0) or (qqq_b20 < -2.5 and breadth < 35.0):
            regime = "BEAR_DEFENSE"
        elif qqq_b50 > 0.5 and breadth >= 52.0:
            regime = "BULL_EXPANSION"
        else:
            regime = "NEUTRAL_CHOP"

        macro_ctx[d_cur] = {
            "market_regime": regime,
            "vix_hard_brake": vix_hard_brake,
            "panic_clearing_active": panic_clearing,
            "vix_state": "BACKWARDATION_BRAKE" if vix_hard_brake else ("PANIC_CLEARING" if panic_clearing else "NORMAL"),
            "breadth_ma20_pct": round(breadth, 1),
            "qqq_bias_ma50": round(qqq_b50, 2),
            "smh_bias_ma50": round(smh_b50, 2),
            "vixy_ret_5d": round(vixy_r5, 2),
        }

        # 计算严格因果代理胜率 (与实盘 V4.6/V5.3 尺度与镜像对齐)
        raw_probs: Dict[str, Tuple[float, float]] = {}
        for tk, info in day_rows.items():
            # 多头基础打分 (严格基于 < d_cur 特征)
            score_l = 45.0
            if info["bias_ma50"] > 0:
                score_l += min(8.0, info["bias_ma50"] * 0.8)
            else:
                score_l += max(-12.0, info["bias_ma50"] * 1.1)
            if info["bias_ema20"] > 0:
                score_l += min(6.0, info["bias_ema20"] * 0.9)
            else:
                score_l += max(-9.0, info["bias_ema20"] * 1.0)
            score_l += max(-7.0, min(8.0, info["rs_20d"] * 0.45))
            if 42.0 <= info["rsi_14"] <= 64.0:
                score_l += 3.5
            elif info["rsi_14"] > 74.0:
                score_l -= 5.0
            elif info["rsi_14"] < 32.0:
                score_l += 2.0 if regime != "BEAR_DEFENSE" else -3.0

            # 空头基础打分
            score_s = 43.0
            if info["bias_ma50"] < 0:
                score_s += min(10.0, abs(info["bias_ma50"]) * 1.0)
            else:
                score_s -= min(10.0, info["bias_ma50"] * 0.8)
            if info["bias_ema20"] < 0:
                score_s += min(8.0, abs(info["bias_ema20"]) * 1.0)
            else:
                score_s -= min(8.0, info["bias_ema20"] * 0.9)
            score_s += max(-7.0, min(8.0, -info["rs_20d"] * 0.45))
            if info["ret_5d"] < -2.0:
                score_s += min(5.0, abs(info["ret_5d"]) * 0.5)

            if regime == "BULL_EXPANSION":
                score_l += 4.5
                score_s -= 4.0
            elif regime == "BEAR_DEFENSE":
                if tk in cte.SAFE_HAVEN_LONG_EXEMPT:
                    score_l += 7.5
                    score_s -= 6.0
                else:
                    score_l -= 6.5
                    score_s += 6.5

            p_l = max(18.0, min(72.0, score_l))
            p_s = max(18.0, min(72.0, score_s))
            if p_l + p_s > 98.0:
                scale = 98.0 / (p_l + p_s)
                p_l *= scale
                p_s *= scale
            raw_probs[tk] = (round(p_l, 1), round(p_s, 1))

        # 3x 杠杆镜像锚定 (SOXL/SOXS <-> SOXX, SQQQ <-> QQQ)
        if "SOXX" in raw_probs:
            sl, ss = raw_probs["SOXX"]
            if "SOXL" in raw_probs:
                raw_probs["SOXL"] = (sl, ss)
            if "SOXS" in raw_probs:
                raw_probs["SOXS"] = (ss, sl)
        if "QQQ" in raw_probs and "SQQQ" in raw_probs:
            ql, qs = raw_probs["QQQ"]
            raw_probs["SQQQ"] = (qs, ql)

        # BEAR_DEFENSE 多头封顶 48% (避险/反向 ETF 豁免)
        for tk, info in day_rows.items():
            p_l, p_s = raw_probs[tk]
            if (regime == "BEAR_DEFENSE" or vix_hard_brake) and (not panic_clearing) and (tk not in cte.SAFE_HAVEN_LONG_EXEMPT):
                p_l = min(48.0, p_l)
            info["calibrated_win_rate_pct"] = round(p_l, 1)
            info["calibrated_short_win_rate_pct"] = round(p_s, 1)
            # V5.4 默认三阈值 ATR 目标/止损百分比
            atrp = info["atr_pct"]
            info["target_profit_pct"] = round(max(3.0, min(12.0, 1.35 * atrp)), 2)
            info["stop_loss_pct"] = round(max(2.2, min(8.5, 1.00 * atrp)), 2)
            info["short_target_profit_pct"] = info["target_profit_pct"]
            info["short_stop_loss_pct"] = info["stop_loss_pct"]

        ticker_ctx[d_cur] = day_rows

    return macro_ctx, ticker_ctx


def build_causal_5m_market_replay(
    df5: pd.DataFrame,
    daily_ctx: Dict[date, Dict[str, Dict[str, Any]]],
) -> Tuple[List[date], Dict[date, List[datetime]], Dict[Tuple[date, str], Dict[datetime, Dict[str, float]]], Dict[Tuple[date, str], Dict[str, float]]]:
    """
    将 5m parquet 预组织为按交易日/时间戳严格因果推进的字典，同时预计算：
    - 盘前高低 (pm_high, pm_low, 严格 04:00~09:29 ET)
    - 每根 RTH 5m K 线截至当前的 cumulative day_low, day_high, vwap, rsi_5m, rvol_5m
    """
    df = df5.copy()
    df["Ticker"] = df["Ticker"].astype(str).str.strip().str.upper()
    # 只保留白名单 + 抗跌计算所需标的，跳过不可直接交易的 KOSPI
    df = df[~df["Ticker"].isin({"KOSPI", "BTC-USD", "XAUUSD"})].copy()
    dt_col = pd.to_datetime(df["Datetime"])
    df["dt"] = dt_col
    df["d"] = dt_col.dt.date
    df["hhmm"] = dt_col.dt.hour * 60 + dt_col.dt.minute

    # 盘前高低 (04:00 <= hhmm < 09:30)
    pm_df = df[(df["hhmm"] >= 4 * 60) & (df["hhmm"] < 9 * 60 + 30)]
    pm_bounds: Dict[Tuple[date, str], Dict[str, float]] = {}
    if not pm_df.empty:
        grp_pm = pm_df.groupby(["d", "Ticker"]).agg(pm_high=("High", "max"), pm_low=("Low", "min")).reset_index()
        for row in grp_pm.itertuples(index=False):
            pm_bounds[(row.d, row.Ticker)] = {"pm_high": float(row.pm_high), "pm_low": float(row.pm_low)}

    # 仅保留常规盘 RTH (09:30 <= hhmm <= 15:55)
    rth = df[(df["hhmm"] >= 9 * 60 + 30) & (df["hhmm"] <= 15 * 60 + 55)].sort_values(["Ticker", "dt"]).copy()
    # 过滤掉 5m 行数极少的异常日（如非完整交易日）
    date_bar_counts = rth.groupby("d")["dt"].nunique()
    valid_dates = sorted([d for d, cnt in date_bar_counts.items() if cnt >= 20])
    rth = rth[rth["d"].isin(valid_dates)].copy()

    bars_by_day_tk: Dict[Tuple[date, str], Dict[datetime, Dict[str, float]]] = {}
    day_timestamps: Dict[date, List[datetime]] = {}

    for d_cur, d_grp in rth.groupby("d"):
        day_timestamps[d_cur] = sorted(d_grp["dt"].unique().tolist())

    # 逐标的按时间连续计算严格因果 rsi_5m 与 rvol_5m（跨日连续，绝无未来穿越）
    for tk, tgrp in rth.groupby("Ticker"):
        tg = tgrp.sort_values("dt").reset_index(drop=True)
        closes = tg["Close"].astype(float).values
        vols = tg["Volume"].astype(float).values
        rsi5 = _wilder_rsi(closes, 14)
        # 滚动过去 20 根 5m 成交量均值（shift(1) 严格因果）
        vol_ma20 = pd.Series(vols).shift(1).rolling(20, min_periods=5).mean().values
        rvol5 = np.where((~np.isnan(vol_ma20)) & (vol_ma20 > 1.0), vols / vol_ma20, 1.0)
        rvol5 = np.clip(rvol5, 0.2, 8.0)

        tg["rsi_5m"] = rsi5
        tg["rvol_5m"] = rvol5

        for d_cur, dg in tg.groupby("d"):
            cum_pv = 0.0
            cum_v = 0.0
            cum_tp_sum = 0.0
            cum_n = 0
            day_lo = float("inf")
            day_hi = 0.0
            day_open = float(dg.iloc[0]["Open"])
            tmap: Dict[datetime, Dict[str, float]] = {}
            for row in dg.itertuples(index=False):
                o = float(row.Open)
                h = float(row.High)
                l = float(row.Low)
                c = float(row.Close)
                v = max(0.0, float(row.Volume))
                tp = (h + l + c) / 3.0
                cum_pv += tp * v
                cum_v += v
                cum_tp_sum += tp
                cum_n += 1
                vwap = (cum_pv / cum_v) if cum_v > 0 else (cum_tp_sum / cum_n)
                day_lo = min(day_lo, l)
                day_hi = max(day_hi, h)
                tmap[row.dt] = {
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": v,
                    "vwap": vwap,
                    "day_open": day_open,
                    "day_low": day_lo,
                    "day_high": day_hi,
                    "rsi_5m": float(row.rsi_5m),
                    "rvol_5m": float(row.rvol_5m),
                }
            bars_by_day_tk[(d_cur, tk)] = tmap

    return valid_dates, day_timestamps, bars_by_day_tk, pm_bounds


# ============================================================================
# 2. 严格防穿越逐 Bar 撮合模拟器 (Baseline V5.4 & V5.5 A/B/C)
# ============================================================================

def _net_ret(direction: str, entry_px: float, exit_px: float, rt_slippage: float) -> float:
    if entry_px <= 0 or exit_px <= 0:
        return 0.0
    if direction == "SHORT":
        return ((entry_px - exit_px) / entry_px) - rt_slippage
    return ((exit_px - entry_px) / entry_px) - rt_slippage


def _resolve_sl_arm(
    cfg: ArmConfig,
    tk: str,
    direction: str,
    entry_px: float,
    day_low: float,
    day_high: float,
    pm_low: float,
    pm_high: float,
    s1: float,
    r1: float,
) -> float:
    lev3 = kol.is_3x_leveraged(tk)
    sl_min = cfg.sl_min_3x if lev3 else cfg.sl_min_1x
    sl_max = cfg.sl_max_3x if lev3 else cfg.sl_max_1x
    pad = 0.010 if lev3 else 0.006

    if direction == "LONG":
        cands = [x * (1.0 - pad) for x in (day_low, pm_low, s1) if 0 < x < entry_px]
        lo_cap = entry_px * (1.0 - sl_max)
        hi_cap = entry_px * (1.0 - sl_min)
        if cands:
            in_band = [v for v in cands if lo_cap <= v <= hi_cap]
            raw = max(in_band) if in_band else max(lo_cap, min(hi_cap, max(cands)))
        else:
            raw = entry_px * (1.0 - (sl_min + sl_max) * 0.5)
        return max(0.01, round(max(lo_cap, min(hi_cap, raw)), 2))

    cands = [x * (1.0 + pad) for x in (day_high, pm_high, r1) if x > entry_px]
    lo_cap = entry_px * (1.0 + sl_min)
    hi_cap = entry_px * (1.0 + sl_max)
    if cands:
        in_band = [v for v in cands if lo_cap <= v <= hi_cap]
        raw = min(in_band) if in_band else max(lo_cap, min(hi_cap, min(cands)))
    else:
        raw = entry_px * (1.0 + (sl_min + sl_max) * 0.5)
    return max(round(entry_px + 0.01, 2), round(max(lo_cap, min(hi_cap, raw)), 2))


def _check_sl_whipsaw_24h(
    tr: Dict[str, Any],
    valid_dates: List[date],
    day_timestamps: Dict[date, List[datetime]],
    bars_by_day_tk: Dict[Tuple[date, str], Dict[datetime, Dict[str, float]]],
) -> bool:
    """
    纯事后诊断（严禁被策略调用）：统计 SL_HIT 止损后 24 小时（即当日剩余盘中 + 下 1 个完整交易日）内，
    价格是否按原交易方向走回原一阶止盈目标 (+4.0% 1x / +6.5% 3x；V5.4 用原 tp_price)。
    """
    if str(tr.get("outcome") or "").upper() != "SL_HIT":
        return False
    tk = str(tr["ticker"])
    direction = str(tr["direction"]).upper()
    entry_px = float(tr["orig_entry_price"])
    exit_dt = datetime.strptime(tr["exit_time_et"], "%Y-%m-%d %H:%M:%S")
    exit_d = exit_dt.date()

    if tr.get("v54_tp_price"):
        target_px = float(tr["v54_tp_price"])
    else:
        tp_pct = (kol.PARTIAL_TP_3X_PCT if kol.is_3x_leveraged(tk) else kol.PARTIAL_TP_1X_PCT) / 100.0
        target_px = entry_px * (1.0 + tp_pct) if direction == "LONG" else entry_px * (1.0 - tp_pct)

    try:
        idx = valid_dates.index(exit_d)
    except ValueError:
        return False
    check_dates = valid_dates[idx: min(len(valid_dates), idx + 2)]
    for d_chk in check_dates:
        tmap = bars_by_day_tk.get((d_chk, tk)) or {}
        for ts in day_timestamps.get(d_chk) or []:
            if ts <= exit_dt:
                continue
            b = tmap.get(ts)
            if not b:
                continue
            if direction == "LONG" and b["high"] >= target_px:
                return True
            if direction == "SHORT" and b["low"] <= target_px:
                return True
    return False


def run_arm_replay(
    cfg: ArmConfig,
    rt_slippage: float,
    valid_dates: List[date],
    day_timestamps: Dict[date, List[datetime]],
    bars_by_day_tk: Dict[Tuple[date, str], Dict[datetime, Dict[str, float]]],
    pm_bounds: Dict[Tuple[date, str], Dict[str, float]],
    macro_ctx: Dict[date, Dict[str, Any]],
    daily_ctx: Dict[date, Dict[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    cash = INITIAL_CAPITAL
    peak_equity = INITIAL_CAPITAL
    max_dd = 0.0
    open_positions: List[Dict[str, Any]] = []
    closed_trades: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []

    last_entry_date_v54 = ""
    last_close_dt: Optional[datetime] = None
    last_exit_by_tk: Dict[str, Dict[str, Any]] = {}

    # 交易日索引映射用于精确 T+3 / T+5（直接用真实本地交易日序列，无需猜测休市日）
    date_to_idx = {d: i for i, d in enumerate(valid_dates)}

    for d_idx, d_cur in enumerate(valid_dates):
        d_str = d_cur.isoformat()
        m_info = macro_ctx.get(d_cur) or {}
        d_tks = daily_ctx.get(d_cur) or {}
        regime = str(m_info.get("market_regime") or "NEUTRAL_CHOP")
        vix_brake = bool(m_info.get("vix_hard_brake", False))
        panic_clear = bool(m_info.get("panic_clearing_active", False))
        long_blocked = bool((regime == "BEAR_DEFENSE" or vix_brake) and (not panic_clear))

        entries_today = 0
        flip_cnt_today: Dict[str, int] = {}
        ts_list = day_timestamps.get(d_cur) or []

        for bar_i, ts in enumerate(ts_list):
            et_min = ts.hour * 60 + ts.minute
            is_first_bar_of_day = (bar_i == 0)

            # 1) 优先处理所有在途持仓的隔夜跳空止损/止盈、盘中加仓/分批止盈/追踪/早撤/到期
            still_open: List[Dict[str, Any]] = []
            for pos in open_positions:
                tk = pos["ticker"]
                direction = pos["direction"]
                b = (bars_by_day_tk.get((d_cur, tk)) or {}).get(ts)
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

                if cfg.is_v54:
                    # ---------- Baseline V5.4 规则（真实隔夜跳空成交）----------
                    tp_px = pos["tp_price"]
                    if direction == "LONG":
                        if o_px <= sl_px:
                            exit_reason, exit_fill_px, gap_sl_slip = "SL_HIT", o_px, is_first_bar_of_day
                        elif o_px >= tp_px:
                            exit_reason, exit_fill_px = "TP_HIT", o_px
                        elif l_px <= sl_px:
                            exit_reason, exit_fill_px = "SL_HIT", sl_px
                        elif h_px >= tp_px:
                            exit_reason, exit_fill_px = "TP_HIT", tp_px
                    else:
                        if o_px >= sl_px:
                            exit_reason, exit_fill_px, gap_sl_slip = "SL_HIT", o_px, is_first_bar_of_day
                        elif o_px <= tp_px:
                            exit_reason, exit_fill_px = "TP_HIT", o_px
                        elif h_px >= sl_px:
                            exit_reason, exit_fill_px = "SL_HIT", sl_px
                        elif l_px <= tp_px:
                            exit_reason, exit_fill_px = "TP_HIT", tp_px

                    if not exit_reason:
                        if (d_idx >= pos["expiry_idx"] and et_min >= 15 * 60 + 50) or (d_idx > pos["expiry_idx"]):
                            exit_reason, exit_fill_px = "T5_EXPIRE", c_px
                else:
                    # ---------- V5.5 激进 KOL 规则（真实隔夜跳空成交 + 补丁 A/B/C/D）----------
                    partial_done = bool(pos["partial_tp_done"])
                    be_active = bool(pos["be_stop_active"])

                    # (1a) 先查开盘跳空止损（按实际 o_px 成交！）再查盘中针尖止损
                    if direction == "LONG":
                        if o_px <= sl_px:
                            exit_reason = "BE_STOP" if be_active else "SL_HIT"
                            exit_fill_px = o_px
                            gap_sl_slip = is_first_bar_of_day
                        elif l_px <= sl_px:
                            exit_reason = "BE_STOP" if be_active else "SL_HIT"
                            exit_fill_px = sl_px
                    else:
                        if o_px >= sl_px:
                            exit_reason = "BE_STOP" if be_active else "SL_HIT"
                            exit_fill_px = o_px
                            gap_sl_slip = is_first_bar_of_day
                        elif h_px >= sl_px:
                            exit_reason = "BE_STOP" if be_active else "SL_HIT"
                            exit_fill_px = sl_px

                    # 更新盘中 MFE 与 best_price (使用收盘价做状态机推进，保守避免未来穿插)
                    ret_c = _net_ret(direction, entry_px, c_px, rt_slippage)
                    ret_c_pct = ret_c * 100.0
                    pos["max_favorable_return_pct"] = max(pos["max_favorable_return_pct"], ret_c_pct)
                    if direction == "LONG":
                        pos["best_price"] = max(pos["best_price"], c_px)
                    else:
                        pos["best_price"] = min(pos["best_price"], c_px)

                    # (1b) 一阶分批止盈 (+4.0% 1x / +6.5% 3x) + 推保本损
                    if not exit_reason and not partial_done:
                        p_trig = kol.PARTIAL_TP_3X_PCT if lev3 else kol.PARTIAL_TP_1X_PCT
                        if ret_c_pct >= p_trig:
                            close_alloc = round(alloc * 0.50, 2)
                            rem_alloc = round(alloc - close_alloc, 2)
                            part_pnl = round(close_alloc * ret_c, 2)
                            cash = round(cash + max(0.0, close_alloc + part_pnl), 2)
                            pos["allocated_capital"] = rem_alloc
                            pos["slot_weight"] = round(pos["slot_weight"] * 0.50, 3)
                            pos["partial_tp_done"] = True
                            pos["can_add"] = False
                            pos["be_stop_active"] = True
                            pos["partial_exit_price"] = round(c_px, 2)
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

                    # (1c) 二阶抛物线卖强 (CLIMAX_TP) 与移动追踪止盈 (TRAIL_TP)
                    if not exit_reason and partial_done:
                        climax_min = kol.CLIMAX_PROFIT_3X_PCT if lev3 else kol.CLIMAX_PROFIT_1X_PCT
                        is_climax = (
                            ((direction == "LONG" and rsi_5m >= kol.CLIMAX_RSI_HIGH) or (direction == "SHORT" and rsi_5m <= kol.CLIMAX_RSI_LOW))
                            and rvol_5m >= kol.CLIMAX_RVOL_MIN
                            and ret_c_pct >= climax_min
                        )
                        if is_climax:
                            exit_reason, exit_fill_px = "CLIMAX_TP", c_px
                        else:
                            trail_dd = kol.TRAIL_DRAWDOWN_3X_PCT if lev3 else kol.TRAIL_DRAWDOWN_1X_PCT
                            bp = pos["best_price"]
                            dd_best = ((bp - c_px) / bp * 100.0) if direction == "LONG" else ((c_px - bp) / bp * 100.0)
                            if dd_best >= trail_dd:
                                exit_reason, exit_fill_px = "TRAIL_TP", c_px

                    # (1d) 补丁 D: 「该强不强」早撤体检 (尾盘仓顺延至 13:30 ET) + T+3 到期
                    if not exit_reason:
                        if (d_idx >= pos["expiry_idx"] and et_min >= kol.T3_FORCE_EXIT_MIN) or (d_idx > pos["expiry_idx"]):
                            exit_reason, exit_fill_px = "T3_EXPIRE", c_px
                        elif (not partial_done) and (d_idx - pos["entry_day_idx"]) >= cfg.weak_exit_min_days:
                            days_held = d_idx - pos["entry_day_idx"]
                            check_min = kol.EARLY_CHECK_LATE_MIN if (days_held == cfg.weak_exit_min_days and pos["entry_min"] > kol.LATE_ENTRY_CUTOFF_MIN) else kol.EARLY_CHECK_NORMAL_MIN
                            if et_min >= check_min:
                                wrong_vwap = (vwap_diff < 0.0) if direction == "LONG" else (vwap_diff > 0.0)
                                weak_stall = (ret_c_pct < 0.8) and wrong_vwap
                                profit_fade = (pos["max_favorable_return_pct"] >= 3.0) and (ret_c_pct < 0.5)
                                if weak_stall or profit_fade:
                                    exit_reason, exit_fill_px = "WEAK_EARLY_EXIT", c_px

                    # (1e) 补丁 B: 底仓顺势加仓 + 重算股数加权均价 + 硬止损移至原底仓价 (可带外侧呼吸缓冲)
                    if (not exit_reason) and pos["stage"] == 1 and pos["can_add"] and (not partial_done):
                        add_trig = kol.ADDON_TRIGGER_3X_PCT if lev3 else kol.ADDON_TRIGGER_1X_PCT
                        on_vwap = (vwap_diff >= 0.0) if direction == "LONG" else (vwap_diff <= 0.0)
                        if ret_c_pct >= add_trig and on_vwap:
                            add_wt = cfg.addon_weight_3x if lev3 else cfg.addon_weight_1x
                            est_eq = cash + sum(p["allocated_capital"] for p in open_positions)
                            add_alloc = round(min(cash, est_eq * add_wt), 2)
                            if add_alloc >= 10.0:
                                orig_ep = pos["orig_entry_price"]
                                sh1 = alloc / entry_px
                                sh2 = add_alloc / c_px
                                new_avg = round((alloc + add_alloc) / (sh1 + sh2), 4)
                                cash = round(cash - add_alloc, 2)
                                pos["entry_price"] = round(new_avg, 2)
                                pos["allocated_capital"] = round(alloc + add_alloc, 2)
                                pos["slot_weight"] = round(pos["slot_weight"] + add_wt, 3)
                                pos["peak_slot_weight"] = pos["slot_weight"]
                                pos["stage"] = 2
                                pos["can_add"] = False
                                if direction == "LONG":
                                    pos["sl_price"] = max(sl_px, round(orig_ep * (1.0 - cfg.addon_sl_buffer), 2))
                                else:
                                    pos["sl_price"] = min(sl_px, round(orig_ep * (1.0 + cfg.addon_sl_buffer), 2))

                if exit_reason:
                    runner_ret = _net_ret(direction, pos["entry_price"], exit_fill_px, rt_slippage)
                    runner_pnl = round(pos["allocated_capital"] * runner_ret, 2)
                    cash = round(cash + max(0.0, pos["allocated_capital"] + runner_pnl), 2)
                    part_pnl = pos.get("partial_realized_pnl_usd", 0.0)
                    part_cap = pos.get("partial_closed_capital", 0.0)
                    tot_alloc = round(pos["allocated_capital"] + part_cap, 2)
                    tot_pnl = round(part_pnl + runner_pnl, 2)
                    tot_ret_pct = round((tot_pnl / tot_alloc) * 100.0, 2) if tot_alloc > 0 else round(runner_ret * 100.0, 2)

                    if exit_reason == "T5_EXPIRE":
                        outcome = "T5_WIN" if tot_pnl > 0 else "T5_LOSS"
                    elif exit_reason == "T3_EXPIRE":
                        outcome = "T3_WIN" if tot_pnl > 0 else "T3_LOSS"
                    else:
                        outcome = exit_reason

                    hold_hours = round((ts - pos["entry_dt"]).total_seconds() / 3600.0, 2)
                    tr = {
                        "ticker": tk,
                        "is_3x": lev3,
                        "direction": direction,
                        "entry_tag": pos.get("entry_tag", "NORMAL"),
                        "setup": pos.get("setup", "V54"),
                        "stage": pos.get("stage", 1),
                        "entry_date": pos["entry_date"],
                        "entry_month": pos["entry_date"][:7],
                        "entry_time_et": pos["entry_dt"].strftime("%Y-%m-%d %H:%M:%S"),
                        "orig_entry_price": pos["orig_entry_price"],
                        "entry_price": pos["entry_price"],
                        "exit_date": d_str,
                        "exit_time_et": ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "exit_price": round(exit_fill_px, 2),
                        "outcome": outcome,
                        "exit_reason": exit_reason,
                        "gap_sl_slip": gap_sl_slip,
                        "allocated_capital": tot_alloc,
                        "pnl_usd": tot_pnl,
                        "net_return_pct": tot_ret_pct,
                        "hold_hours": hold_hours,
                        "v54_tp_price": pos.get("tp_price"),
                    }
                    closed_trades.append(tr)
                    last_close_dt = ts
                    last_exit_by_tk[tk] = {
                        "direction": direction,
                        "exit_date": d_str,
                        "exit_dt": ts,
                        "exit_reason": exit_reason,
                    }
                else:
                    pos["last_price"] = c_px
                    still_open.append(pos)

            open_positions = still_open

            # 2) 计算当前时刻权益 (用于仓位比例计算)
            deployed_val = 0.0
            for pos in open_positions:
                b_cur = (bars_by_day_tk.get((d_cur, pos["ticker"])) or {}).get(ts)
                lp = b_cur["close"] if b_cur else pos.get("last_price", pos["entry_price"])
                r_u = _net_ret(pos["direction"], pos["entry_price"], lp, rt_slippage)
                deployed_val += max(0.0, pos["allocated_capital"] * (1.0 + r_u))
            cur_equity = round(cash + deployed_val, 2)

            # 3) 开仓扫描
            if cfg.is_v54:
                # V5.4 仅在 09:35 ET (或当日首根 >= 09:35 的 bar) 锁一次开仓
                if et_min >= 9 * 60 + 35 and et_min < 15 * 60 + 50 and last_entry_date_v54 != d_str:
                    free_slots = max(0, cfg.max_positions - len(open_positions))
                    if free_slots > 0:
                        held_tks = {p["ticker"] for p in open_positions}
                        held_grps = {cte.get_mirror_group(tk) for tk in held_tks if cte.get_mirror_group(tk)}
                        cands = []
                        for tk, info in d_tks.items():
                            if tk in held_tks or tk in cte.NON_TRADABLE_DIRECT_TICKERS:
                                continue
                            mg = cte.get_mirror_group(tk)
                            if mg and mg in held_grps:
                                continue
                            b_cur = (bars_by_day_tk.get((d_cur, tk)) or {}).get(ts)
                            if not b_cur:
                                continue
                            px = b_cur["close"]
                            pc = info["prev_close"]
                            gap = ((px - pc) / pc * 100.0) if pc > 0 else 0.0
                            p_l = info["calibrated_win_rate_pct"]
                            p_s = info["calibrated_short_win_rate_pct"]
                            long_ok = (not long_blocked) or (tk in cte.SAFE_HAVEN_LONG_EXEMPT)
                            if long_ok and (not info["is_crash_halt"]) and p_l >= 55.0 and (p_l - p_s) >= 10.0 and gap <= 4.0:
                                tp_px = round(px * (1.0 + info["target_profit_pct"] / 100.0), 2)
                                sl_px = round(px * (1.0 - info["stop_loss_pct"] / 100.0), 2)
                                cands.append((p_l, p_l - p_s, tk, "LONG", px, tp_px, sl_px, mg))
                            inv_surge = bool(tk in cte.INVERSE_ETFS and (gap >= 8.0 or info["rsi_14"] >= 68.0))
                            if (tk not in cte.NON_SHORTABLE_TICKERS) and (not inv_surge) and p_s >= 55.0 and (p_s - p_l) >= 10.0 and (-gap) <= 4.0:
                                tp_px = round(px * (1.0 - info["short_target_profit_pct"] / 100.0), 2)
                                sl_px = round(px * (1.0 + info["short_stop_loss_pct"] / 100.0), 2)
                                cands.append((p_s, p_s - p_l, tk, "SHORT", px, tp_px, sl_px, mg))

                        cands.sort(key=lambda x: (-x[0], -x[1], x[2]))
                        used_t, used_g = set(held_tks), set(held_grps)
                        exp_idx = min(len(valid_dates) - 1, d_idx + 5)
                        for prob, sprd, tk, direction, px, tp_px, sl_px, mg in cands:
                            if len(open_positions) >= cfg.max_positions:
                                break
                            if tk in used_t or (mg and mg in used_g):
                                continue
                            alloc = round(min(cash, cur_equity * 0.25), 2)
                            if alloc < 10.0:
                                continue
                            cash = round(cash - alloc, 2)
                            open_positions.append({
                                "ticker": tk, "direction": direction, "setup": "V54_0935",
                                "entry_tag": "NORMAL", "stage": 1, "can_add": False,
                                "slot_weight": 0.25, "peak_slot_weight": 0.25, "allocated_capital": alloc,
                                "entry_date": d_str, "entry_day_idx": d_idx, "entry_dt": ts, "entry_min": et_min,
                                "expiry_idx": exp_idx, "orig_entry_price": round(px, 2), "entry_price": round(px, 2),
                                "last_price": px, "best_price": px, "tp_price": tp_px, "sl_price": sl_px,
                                "partial_tp_done": False, "be_stop_active": False,
                                "partial_closed_capital": 0.0, "partial_realized_pnl_usd": 0.0,
                                "max_favorable_return_pct": -rt_slippage * 100.0,
                            })
                            used_t.add(tk)
                            if mg:
                                used_g.add(mg)
                    last_entry_date_v54 = d_str
            else:
                # V5.5 盘中滚动狙击窗口: 09:45 ~ 15:20 ET
                in_win = (kol.ENTRY_START_MIN <= et_min <= kol.ENTRY_END_MIN)
                slot_cd_ok = True
                if entries_today >= cfg.max_positions and last_close_dt is not None:
                    slot_cd_ok = ((ts - last_close_dt).total_seconds() / 60.0) >= kol.SLOT_COOLDOWN_MINUTES

                if in_win and len(open_positions) < cfg.max_positions and entries_today < kol.MAX_NEW_ENTRIES_PER_DAY and slot_cd_ok:
                    qqq_b = (bars_by_day_tk.get((d_cur, "QQQ")) or {}).get(ts)
                    qqq_pc = float((d_tks.get("QQQ") or {}).get("prev_close", 0.0))
                    qqq_pct = ((qqq_b["close"] - qqq_pc) / qqq_pc * 100.0) if (qqq_b and qqq_pc > 0) else 0.0
                    # 用 QQQ 盘中站回 VWAP 作为 NQ=F 站回 VWAP 的严格因果历史代理
                    nq_vwap_ok = bool(qqq_b and qqq_b["vwap"] > 0 and qqq_b["close"] >= qqq_b["vwap"])

                    # 严格因果计算当前时刻抗跌领涨 Top 5
                    lead_scores = []
                    for ltk, linfo in d_tks.items():
                        if ltk in cte.INVERSE_ETFS or ltk in ("GLD", "TLT", "QQQ", "SPY", "SOXX", "SMH"):
                            continue
                        lb = (bars_by_day_tk.get((d_cur, ltk)) or {}).get(ts)
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
                        if tk not in kol.KOL_CORE_WHITELIST and tk not in leaders_set:
                            continue
                        mg = cte.get_mirror_group(tk)
                        if mg and mg in held_grps:
                            continue
                        b_cur = (bars_by_day_tk.get((d_cur, tk)) or {}).get(ts)
                        if not b_cur:
                            continue

                        px = b_cur["close"]
                        pc = info["prev_close"]
                        if pc <= 0 or px <= 0:
                            continue
                        pct_chg = (px - pc) / pc * 100.0
                        gap_pct = ((b_cur["day_open"] - pc) / pc * 100.0) if pc > 0 else pct_chg
                        lev3 = kol.is_3x_leveraged(tk)
                        max_gap = 9.0 if lev3 else 5.0

                        vwap_px = b_cur["vwap"]
                        vwap_diff = ((px - vwap_px) / vwap_px * 100.0) if vwap_px > 0 else 0.0
                        rsi_5m = b_cur["rsi_5m"]
                        rvol_5m = b_cur["rvol_5m"]
                        day_lo = b_cur["day_low"]
                        day_hi = b_cur["day_high"]
                        pmb = pm_bounds.get((d_cur, tk)) or {}
                        pm_lo = float(pmb.get("pm_low", 0.0))
                        pm_hi = float(pmb.get("pm_high", 0.0))
                        s1, r1 = info["s1"], info["r1"]

                        # 严格因果走廊状态代理
                        intraday_from_open = ((px - b_cur["day_open"]) / b_cur["day_open"] * 100.0) if b_cur["day_open"] > 0 else 0.0
                        corr_div = "ABOVE_P75" if intraday_from_open >= 0.55 * info["atr_pct"] else ("BELOW_P25" if intraday_from_open <= -0.55 * info["atr_pct"] else "")

                        p_l = info["calibrated_win_rate_pct"]
                        p_s = info["calibrated_short_win_rate_pct"]
                        is_crash = info["is_crash_halt"]
                        is_deep_pb = info["is_deep_pullback"]
                        is_broken = info["is_broken_bearish"]

                        last_ex = last_exit_by_tk.get(tk)
                        elapsed_ex = ((ts - last_ex["exit_dt"]).total_seconds() / 60.0) if (last_ex and last_ex["exit_date"] == d_str) else 9999.0

                        def _cd_check(tdir: str) -> Tuple[bool, bool]:
                            if not last_ex or last_ex["exit_date"] != d_str:
                                return True, False
                            if last_ex["direction"] == tdir:
                                return elapsed_ex >= kol.SLOT_COOLDOWN_MINUTES, False
                            if last_ex["exit_reason"] in ("SL_HIT", "WEAK_EARLY_EXIT"):
                                if not cfg.allow_flip:
                                    return False, True
                                if flip_cnt_today.get(tk, 0) >= 1:
                                    return False, True
                                if elapsed_ex < cfg.flip_cooldown_min:
                                    return False, True
                                return True, True
                            return elapsed_ex >= kol.SLOT_COOLDOWN_MINUTES, False

                        pri = 2.0 if tk in leaders_set else (1.0 if lev3 else 0.0)
                        st_wt = cfg.starter_weight_3x if lev3 else cfg.starter_weight_1x

                        # A. LONG 通道
                        ok_l, is_flip_l = _cd_check("LONG")
                        if ok_l and gap_pct <= max_gap and (not is_crash):
                            if is_flip_l:
                                if cfg.allow_flip:
                                    allow_fl = (not long_blocked) or (tk in cte.SAFE_HAVEN_LONG_EXEMPT)
                                    if allow_fl and p_l >= 58.0 and vwap_diff >= 0.0 and rvol_5m >= 1.5:
                                        sl_px = _resolve_sl_arm(cfg, tk, "LONG", px, day_lo, day_hi, pm_lo, pm_hi, s1, r1)
                                        raw_cands.append({
                                            "ticker": tk, "direction": "LONG", "setup": "FLIP_LONG", "entry_tag": "FLIP",
                                            "slot_weight": cfg.flip_weight, "can_add": False,
                                            "score": p_l + pri + 3.0, "prob": p_l, "entry_price": round(px, 2), "sl_price": sl_px, "mg": mg,
                                        })
                            else:
                                norm_l = (not long_blocked) or (tk in cte.SAFE_HAVEN_LONG_EXEMPT)
                                if norm_l and p_l >= cfg.min_prob_long and (p_l - p_s) >= cfg.min_spread:
                                    swept = (
                                        (s1 > 0 and ((day_lo <= s1 * 1.006 and px >= s1) or abs(px - s1) / s1 <= 0.012))
                                        or (pm_lo > 0 and day_lo <= pm_lo * 1.005 and px >= pm_lo)
                                        or is_deep_pb or corr_div == "BELOW_P25"
                                    )
                                    resil = (pct_chg >= qqq_pct - 0.5) or (tk in leaders_set)
                                    sweep_ok = swept and (vwap_diff >= -0.20) and (rvol_5m >= 1.3) and resil
                                    brk_ok = (vwap_diff >= 0.0) and ((r1 > 0 and px >= r1 * 0.998) or corr_div == "ABOVE_P75") and (rvol_5m >= 1.5) and (50.0 <= rsi_5m <= 76.0)
                                    if sweep_ok or brk_ok:
                                        sl_px = _resolve_sl_arm(cfg, tk, "LONG", px, day_lo, day_hi, pm_lo, pm_hi, s1, r1)
                                        raw_cands.append({
                                            "ticker": tk, "direction": "LONG",
                                            "setup": "SWEEP_RECLAIM_LONG" if sweep_ok else "BREAKOUT_MOMENTUM_LONG",
                                            "entry_tag": "NORMAL", "slot_weight": st_wt, "can_add": True,
                                            "score": p_l + (p_l - p_s) * 0.25 + pri + (2.0 if sweep_ok else 1.0),
                                            "prob": p_l, "entry_price": round(px, 2), "sl_price": sl_px, "mg": mg,
                                        })
                                elif long_blocked and cfg.allow_bear_bounce and (tk not in cte.SAFE_HAVEN_LONG_EXEMPT):
                                    oversold = (rsi_5m <= 34.0) or is_deep_pb or (pct_chg <= -3.0) or (corr_div == "BELOW_P25")
                                    if nq_vwap_ok and oversold and (vwap_diff >= -0.25) and (rvol_5m >= 1.2) and p_l >= 42.0:
                                        sl_px = _resolve_sl_arm(cfg, tk, "LONG", px, day_lo, day_hi, pm_lo, pm_hi, s1, r1)
                                        raw_cands.append({
                                            "ticker": tk, "direction": "LONG", "setup": "BEAR_OVERSOLD_BOUNCE_LONG",
                                            "entry_tag": "BEAR_BOUNCE", "slot_weight": cfg.bear_bounce_weight, "can_add": False,
                                            "score": p_l + pri - 5.0, "prob": p_l, "entry_price": round(px, 2), "sl_price": sl_px, "mg": mg,
                                        })

                        # B. SHORT 通道
                        inv_surge = bool(tk in cte.INVERSE_ETFS and (gap_pct >= 8.0 or pct_chg >= 8.0 or info["rsi_14"] >= 68.0))
                        ok_s, is_flip_s = _cd_check("SHORT")
                        if ok_s and (tk not in cte.NON_SHORTABLE_TICKERS) and (not inv_surge) and (-gap_pct) <= max_gap:
                            if is_flip_s:
                                if cfg.allow_flip and p_s >= 58.0 and vwap_diff <= 0.0 and rvol_5m >= 1.5:
                                    sl_px = _resolve_sl_arm(cfg, tk, "SHORT", px, day_lo, day_hi, pm_lo, pm_hi, s1, r1)
                                    raw_cands.append({
                                        "ticker": tk, "direction": "SHORT", "setup": "FLIP_SHORT", "entry_tag": "FLIP",
                                        "slot_weight": cfg.flip_weight, "can_add": False,
                                        "score": p_s + pri + 3.0, "prob": p_s, "entry_price": round(px, 2), "sl_price": sl_px, "mg": mg,
                                    })
                            else:
                                if p_s >= cfg.min_prob_short and (p_s - p_l) >= cfg.min_spread:
                                    b_ctx = is_broken or (regime == "BEAR_DEFENSE") or vix_brake or (pct_chg < 0) or (corr_div == "BELOW_P25")
                                    near_rej = ((-1.5 <= vwap_diff <= 0.40) or (r1 > 0 and abs(px - r1) / r1 <= 0.012)) and (vwap_diff <= 0.25) and (rsi_5m <= 58.0)
                                    if b_ctx and near_rej:
                                        sl_px = _resolve_sl_arm(cfg, tk, "SHORT", px, day_lo, day_hi, pm_lo, pm_hi, s1, r1)
                                        raw_cands.append({
                                            "ticker": tk, "direction": "SHORT", "setup": "REBOUND_REJECT_SHORT",
                                            "entry_tag": "NORMAL", "slot_weight": st_wt, "can_add": True,
                                            "score": p_s + (p_s - p_l) * 0.25 + pri + (2.5 if long_blocked else 0.0),
                                            "prob": p_s, "entry_price": round(px, 2), "sl_price": sl_px, "mg": mg,
                                        })

                    raw_cands.sort(key=lambda c: (-c["score"], -c["prob"], c["ticker"]))
                    used_t, used_g = set(held_tks), set(held_grps)
                    exp_idx = min(len(valid_dates) - 1, d_idx + kol.HOLD_TRADING_DAYS)
                    for c in raw_cands:
                        if len(open_positions) >= cfg.max_positions or entries_today >= kol.MAX_NEW_ENTRIES_PER_DAY:
                            break
                        tk, mg = c["ticker"], c["mg"]
                        if tk in used_t or (mg and mg in used_g):
                            continue
                        alloc = round(min(cash, cur_equity * c["slot_weight"]), 2)
                        if alloc < 10.0:
                            continue
                        cash = round(cash - alloc, 2)
                        if c["entry_tag"] == "FLIP":
                            flip_cnt_today[tk] = flip_cnt_today.get(tk, 0) + 1
                        open_positions.append({
                            "ticker": tk, "direction": c["direction"], "setup": c["setup"],
                            "entry_tag": c["entry_tag"], "stage": 1, "can_add": c["can_add"],
                            "slot_weight": c["slot_weight"], "peak_slot_weight": c["slot_weight"],
                            "allocated_capital": alloc, "entry_date": d_str, "entry_day_idx": d_idx,
                            "entry_dt": ts, "entry_min": et_min, "expiry_idx": exp_idx,
                            "orig_entry_price": c["entry_price"], "entry_price": c["entry_price"],
                            "last_price": c["entry_price"], "best_price": c["entry_price"],
                            "sl_price": c["sl_price"], "partial_tp_done": False, "be_stop_active": False,
                            "partial_closed_capital": 0.0, "partial_realized_pnl_usd": 0.0,
                            "max_favorable_return_pct": -rt_slippage * 100.0,
                        })
                        used_t.add(tk)
                        if mg:
                            used_g.add(mg)
                        entries_today += 1

        # 日末权益与最大回撤记录
        eod_deployed = 0.0
        for pos in open_positions:
            tmap = bars_by_day_tk.get((d_cur, pos["ticker"])) or {}
            lp = pos.get("last_price", pos["entry_price"])
            if ts_list:
                for ts_rev in reversed(ts_list):
                    if ts_rev in tmap:
                        lp = tmap[ts_rev]["close"]
                        break
            r_u = _net_ret(pos["direction"], pos["entry_price"], lp, rt_slippage)
            eod_deployed += max(0.0, pos["allocated_capital"] * (1.0 + r_u))
        eod_eq = round(cash + eod_deployed, 2)
        peak_equity = max(peak_equity, eod_eq)
        dd_now = ((peak_equity - eod_eq) / peak_equity * 100.0) if peak_equity > 0 else 0.0
        max_dd = max(max_dd, dd_now)
        equity_curve.append({"date": d_str, "equity": eod_eq, "regime": regime})

    # 最后一个交易日收盘按市价平仓结算未完结持仓，保证期末口径完全一致
    last_d = valid_dates[-1]
    last_ts = day_timestamps[last_d][-1]
    for pos in open_positions:
        tmap = bars_by_day_tk.get((last_d, pos["ticker"])) or {}
        lp = pos.get("last_price", pos["entry_price"])
        for ts_rev in reversed(day_timestamps[last_d]):
            if ts_rev in tmap:
                lp = tmap[ts_rev]["close"]
                break
        runner_ret = _net_ret(pos["direction"], pos["entry_price"], lp, rt_slippage)
        runner_pnl = round(pos["allocated_capital"] * runner_ret, 2)
        cash = round(cash + max(0.0, pos["allocated_capital"] + runner_pnl), 2)
        tot_alloc = round(pos["allocated_capital"] + pos.get("partial_closed_capital", 0.0), 2)
        tot_pnl = round(pos.get("partial_realized_pnl_usd", 0.0) + runner_pnl, 2)
        tot_ret_pct = round((tot_pnl / tot_alloc) * 100.0, 2) if tot_alloc > 0 else 0.0
        closed_trades.append({
            "ticker": pos["ticker"], "is_3x": kol.is_3x_leveraged(pos["ticker"]),
            "direction": pos["direction"], "entry_tag": pos.get("entry_tag", "NORMAL"),
            "setup": pos.get("setup", "M2M"), "stage": pos.get("stage", 1),
            "entry_date": pos["entry_date"], "entry_month": pos["entry_date"][:7],
            "entry_time_et": pos["entry_dt"].strftime("%Y-%m-%d %H:%M:%S"),
            "orig_entry_price": pos["orig_entry_price"], "entry_price": pos["entry_price"],
            "exit_date": last_d.isoformat(), "exit_time_et": last_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "exit_price": round(lp, 2), "outcome": "M2M_END", "exit_reason": "M2M_END",
            "gap_sl_slip": False, "allocated_capital": tot_alloc, "pnl_usd": tot_pnl,
            "net_return_pct": tot_ret_pct,
            "hold_hours": round((last_ts - pos["entry_dt"]).total_seconds() / 3600.0, 2),
            "v54_tp_price": pos.get("tp_price"),
        })

    # 事后诊断每笔 SL_HIT 是否在 24h 内被误杀
    for tr in closed_trades:
        tr["sl_whipsaw_24h"] = _check_sl_whipsaw_24h(tr, valid_dates, day_timestamps, bars_by_day_tk)

    end_eq = round(cash, 2)
    peak_equity = max(peak_equity, end_eq)
    max_dd = round(max(max_dd, (peak_equity - end_eq) / peak_equity * 100.0 if peak_equity > 0 else 0.0), 2)

    return {
        "arm": cfg.name,
        "label": cfg.label,
        "rt_slippage_bps": round(rt_slippage * 10000, 1),
        "ending_equity": end_eq,
        "net_return_pct": round((end_eq - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100.0, 2),
        "max_drawdown_pct": max_dd,
        "summary": _summarize_trades(closed_trades),
        "exit_breakdown": _breakdown_exits(closed_trades),
        "asset_class_attribution": _breakdown_asset_class(closed_trades),
        "direction_attribution": _breakdown_direction(closed_trades),
        "monthly_attribution": _breakdown_monthly(closed_trades),
        "gap_sl_events": sum(1 for t in closed_trades if t.get("gap_sl_slip")),
        "equity_curve": equity_curve,
        "trades": closed_trades,
    }


def _summarize_trades(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"count": 0, "win_rate_pct": 0.0, "profit_factor": 0.0, "net_pnl_usd": 0.0, "avg_ret_pct": 0.0, "avg_hold_hours": 0.0}
    wins = sum(1 for t in trades if t["pnl_usd"] > 0)
    gp = sum(t["pnl_usd"] for t in trades if t["pnl_usd"] > 0)
    gl = sum(abs(t["pnl_usd"]) for t in trades if t["pnl_usd"] < 0)
    pf = round(gp / gl, 2) if gl > 1e-6 else (99.9 if gp > 0 else 0.0)
    return {
        "count": n,
        "wins": wins,
        "win_rate_pct": round(wins / n * 100.0, 1),
        "profit_factor": pf,
        "net_pnl_usd": round(sum(t["pnl_usd"] for t in trades), 2),
        "avg_ret_pct": round(sum(t["net_return_pct"] for t in trades) / n, 2),
        "avg_hold_hours": round(sum(t["hold_hours"] for t in trades) / n, 2),
    }


def _breakdown_exits(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    n = max(1, len(trades))
    keys = ["SL_HIT", "BE_STOP", "TRAIL_TP", "CLIMAX_TP", "WEAK_EARLY_EXIT", "T3_EXPIRE", "TP_HIT", "T5_EXPIRE", "M2M_END"]
    out: Dict[str, Dict[str, Any]] = {}
    for k in keys:
        sub = [t for t in trades if t["exit_reason"] == k]
        if sub or k in ("SL_HIT", "BE_STOP", "TRAIL_TP", "CLIMAX_TP", "WEAK_EARLY_EXIT", "T3_EXPIRE"):
            out[k] = {
                "count": len(sub),
                "share_pct": round(len(sub) / n * 100.0, 1),
                "avg_ret_pct": round(sum(t["net_return_pct"] for t in sub) / len(sub), 2) if sub else 0.0,
                "net_pnl_usd": round(sum(t["pnl_usd"] for t in sub), 2),
            }
    # 单独统计 FLIP 反手入场与 ADDON 二段加仓子集
    flips = [t for t in trades if t.get("entry_tag") == "FLIP"]
    out["FLIP_SUBSET"] = {
        "count": len(flips),
        "share_pct": round(len(flips) / n * 100.0, 1),
        "avg_ret_pct": round(sum(t["net_return_pct"] for t in flips) / len(flips), 2) if flips else 0.0,
        "net_pnl_usd": round(sum(t["pnl_usd"] for t in flips), 2),
    }
    addons = [t for t in trades if int(t.get("stage") or 1) >= 2]
    out["ADDON_SUBSET"] = {
        "count": len(addons),
        "share_pct": round(len(addons) / n * 100.0, 1),
        "avg_ret_pct": round(sum(t["net_return_pct"] for t in addons) / len(addons), 2) if addons else 0.0,
        "net_pnl_usd": round(sum(t["pnl_usd"] for t in addons), 2),
    }
    return out


def _breakdown_asset_class(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for label, flag in (("1x_Stocks_ETFs", False), ("3x_Leveraged_ETFs", True)):
        sub = [t for t in trades if bool(t["is_3x"]) == flag]
        s = _summarize_trades(sub)
        sl_sub = [t for t in sub if t["outcome"] == "SL_HIT"]
        whip = sum(1 for t in sl_sub if t.get("sl_whipsaw_24h"))
        s["sl_hit_count"] = len(sl_sub)
        s["sl_whipsaw_24h_count"] = whip
        s["sl_whipsaw_24h_pct"] = round(whip / len(sl_sub) * 100.0, 1) if sl_sub else 0.0
        out[label] = s
    return out


def _breakdown_direction(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {
        "LONG_NORMAL": _summarize_trades([t for t in trades if t["direction"] == "LONG" and t["entry_tag"] != "BEAR_BOUNCE"]),
        "SHORT_ALL": _summarize_trades([t for t in trades if t["direction"] == "SHORT"]),
        "BEAR_BOUNCE_15PCT": _summarize_trades([t for t in trades if t["entry_tag"] == "BEAR_BOUNCE"]),
    }


def _breakdown_monthly(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    sep_oct = [t for t in trades if t["entry_month"] in ("2026-09", "2026-10")]
    nov = [t for t in trades if t["entry_month"] == "2026-11"]
    return {
        "2026-09_to_10_BullChop": _summarize_trades(sep_oct),
        "2026-11_CrashSelloff": _summarize_trades(nov),
        "2026-09": _summarize_trades([t for t in trades if t["entry_month"] == "2026-09"]),
        "2026-10": _summarize_trades([t for t in trades if t["entry_month"] == "2026-10"]),
        "2026-11": _summarize_trades(nov),
    }


# ============================================================================
# 3. 报告生成 (Markdown + JSON)
# ============================================================================

def generate_markdown_report(
    valid_dates: List[date],
    macro_ctx: Dict[date, Dict[str, Any]],
    results_by_slip: Dict[str, List[Dict[str, Any]]],
) -> str:
    reg_cnts = defaultdict(int)
    for d in valid_dates:
        reg_cnts[macro_ctx[d]["market_regime"]] += 1

    lines: List[str] = [
        "# V5.5 激进 KOL 狙击轨 · 60 日纯离线防穿越对照回测报告",
        "",
        f"- **样本区间**：`{valid_dates[0]}` 至 `{valid_dates[-1]}`（共 **{len(valid_dates)} 个完整美股交易日**，5m K 线精确逐根撮合）",
        f"- **历史大盘体制分布**（严格 `< current_date` 因果判定）：`BULL_EXPANSION` {reg_cnts['BULL_EXPANSION']} 日 · `NEUTRAL_CHOP` {reg_cnts['NEUTRAL_CHOP']} 日 · `BEAR_DEFENSE` {reg_cnts['BEAR_DEFENSE']} 日",
        "- **防穿越与真实成交口径**：",
        "  - 全部日线特征（`MA50`/`EMA20`/`ATR`/`S1/S2/R1/R2`/代理胜率）严格取 `< current_date`；全部 5m 指标（`VWAP`/`RSI5m`/`RVOL5m`/抗跌 Top5）严格取 `<= current_bar`；",
        "  - 隔夜跳空越过止损价强制按次日 **实际开盘价 `bar.Open`** 成交，绝不按理想止损价结算；",
        "  - 因本地 parquet 无历史 `NQ=F` 5m 与 `TQQQ`/`CRCL` 数据，采用 `QQQ` 盘中站回 VWAP 作为 `NQ=F` 因果代理，覆盖 78 标的全池（含 `SOXL/SOXS/SQQQ/KORU` 等全部 28 只核心白名单标的）。",
        "",
        "---",
        "",
        "## 1. 四组预注册对照总战绩表（3 bps 名义 vs 25 bps 写实压力滑点）",
        "",
    ]

    for slip_key, _, slip_cn in SLIPPAGE_REGIMES:
        lines.append(f"### 1.{1 if slip_key == '3bps' else 2} {slip_cn}")
        lines.append("")
        lines.append("| 策略组 | 总笔数 | 胜率 | Profit Factor | 净收益率 | 期末权益 | 最大回撤 MaxDD | 平均持仓(h) | 隔夜跳空击穿损(次) |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in results_by_slip[slip_key]:
            s = r["summary"]
            lines.append(
                f"| **{r['arm']}** | {s['count']} | {s['win_rate_pct']:.1f}% | {s['profit_factor']:.2f} | "
                f"{r['net_return_pct']:+.2f}% | \\${r['ending_equity']:,.2f} | -{r['max_drawdown_pct']:.2f}% | "
                f"{s['avg_hold_hours']:.1f}h | {r['gap_sl_events']} |"
            )
        lines.append("")

    # 2. 退出类型拆解 (以 25 bps 写实压力为主，同步列出 3 bps)
    lines.extend(["---", "", "## 2. 退出类型拆解表（笔数 / 占比 / 平均单笔净收益%，25 bps 写实滑点口径）", ""])
    lines.append("| 策略组 | `SL_HIT` | `BE_STOP` | `TRAIL_TP` | `CLIMAX_TP` | `WEAK_EARLY_EXIT` | `T3/T5_EXPIRE` | `TP_HIT(V5.4)` | `FLIP(反手子集)` | `ADDON(加仓子集)` |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in results_by_slip["25bps"]:
        eb = r["exit_breakdown"]
        def _cell(k: str) -> str:
            v = eb.get(k)
            if not v or v["count"] == 0:
                return "0"
            return f"{v['count']} ({v['share_pct']:.0f}%, {v['avg_ret_pct']:+.2f}%)"
        t_exp = eb.get("T5_EXPIRE") if r["arm"] == "Baseline_V54" else eb.get("T3_EXPIRE")
        t_cell = f"{t_exp['count']} ({t_exp['share_pct']:.0f}%, {t_exp['avg_ret_pct']:+.2f}%)" if (t_exp and t_exp["count"] > 0) else "0"
        lines.append(
            f"| **{r['arm']}** | {_cell('SL_HIT')} | {_cell('BE_STOP')} | {_cell('TRAIL_TP')} | {_cell('CLIMAX_TP')} | "
            f"{_cell('WEAK_EARLY_EXIT')} | {t_cell} | {_cell('TP_HIT')} | {_cell('FLIP_SUBSET')} | {_cell('ADDON_SUBSET')} |"
        )
    lines.append("")

    # 3. 分组归因拆解 (25 bps 写实口径 + 3 bps 净盈亏对照)
    lines.extend(["---", "", "## 3. 分组归因拆解（25 bps 写实滑点口径）", ""])

    lines.append("### 3.1 1x 正股/ETF vs 3x 杠杆 ETF（含止损后 24h 误杀率诊断）")
    lines.append("")
    lines.append("| 策略组 | 1x 笔数/胜率/PF/净盈亏 | 1x `SL_HIT` 误杀率 (24h走回止盈) | 3x 笔数/胜率/PF/净盈亏 | 3x `SL_HIT` 误杀率 (24h走回止盈) |")
    lines.append("|---|---|---|---|---|")
    for r in results_by_slip["25bps"]:
        a1 = r["asset_class_attribution"]["1x_Stocks_ETFs"]
        a3 = r["asset_class_attribution"]["3x_Leveraged_ETFs"]
        lines.append(
            f"| **{r['arm']}** | {a1['count']}笔 · {a1['win_rate_pct']:.1f}% · PF {a1['profit_factor']:.2f} · {a1['net_pnl_usd']:+.2f} | "
            f"{a1['sl_whipsaw_24h_count']}/{a1['sl_hit_count']} ({a1['sl_whipsaw_24h_pct']:.1f}%) | "
            f"{a3['count']}笔 · {a3['win_rate_pct']:.1f}% · PF {a3['profit_factor']:.2f} · {a3['net_pnl_usd']:+.2f} | "
            f"{a3['sl_whipsaw_24h_count']}/{a3['sl_hit_count']} ({a3['sl_whipsaw_24h_pct']:.1f}%) |"
        )
    lines.append("")

    lines.append("### 3.2 多头 vs 空头 vs 空头期 15% 超跌反抽")
    lines.append("")
    lines.append("| 策略组 | 常规多头 (笔/胜率/PF/盈亏) | 做空通道 (笔/胜率/PF/盈亏) | 空头期 15% 反抽 (笔/胜率/PF/盈亏) |")
    lines.append("|---|---|---|---|")
    for r in results_by_slip["25bps"]:
        da = r["direction_attribution"]
        def _dcell(d: Dict[str, Any]) -> str:
            if d["count"] == 0:
                return "0 笔"
            return f"{d['count']}笔 · {d['win_rate_pct']:.1f}% · PF {d['profit_factor']:.2f} · {d['net_pnl_usd']:+.2f}"
        lines.append(f"| **{r['arm']}** | {_dcell(da['LONG_NORMAL'])} | {_dcell(da['SHORT_ALL'])} | {_dcell(da['BEAR_BOUNCE_15PCT'])} |")
    lines.append("")

    lines.append("### 3.3 分月行情表现（9~10 月偏强震荡期 vs 11 月急杀期）")
    lines.append("")
    lines.append("| 策略组 | 9~10 月偏强期 (笔/胜率/PF/盈亏) | 11 月急杀期 (笔/胜率/PF/盈亏) | 2026-09 盈亏 | 2026-10 盈亏 | 2026-11 盈亏 |")
    lines.append("|---|---|---|---:|---:|---:|")
    for r in results_by_slip["25bps"]:
        ma = r["monthly_attribution"]
        m910 = ma["2026-09_to_10_BullChop"]
        m11 = ma["2026-11_CrashSelloff"]
        lines.append(
            f"| **{r['arm']}** | {m910['count']}笔 · {m910['win_rate_pct']:.1f}% · PF {m910['profit_factor']:.2f} · {m910['net_pnl_usd']:+.2f} | "
            f"{m11['count']}笔 · {m11['win_rate_pct']:.1f}% · PF {m11['profit_factor']:.2f} · {m11['net_pnl_usd']:+.2f} | "
            f"{ma['2026-09']['net_pnl_usd']:+.2f} | {ma['2026-10']['net_pnl_usd']:+.2f} | {ma['2026-11']['net_pnl_usd']:+.2f} |"
        )
    lines.append("")

    # 4. 自动生成客观体检结论 + V55_D_Hybrid 闸门判定
    r25 = {r["arm"]: r for r in results_by_slip["25bps"]}
    r3 = {r["arm"]: r for r in results_by_slip["3bps"]}
    best_25 = max(results_by_slip["25bps"], key=lambda x: (x["net_return_pct"], -x["max_drawdown_pct"]))
    best_3 = max(results_by_slip["3bps"], key=lambda x: (x["net_return_pct"], -x["max_drawdown_pct"]))
    d25 = r25.get("V55_D_Hybrid")
    b25 = r25.get("Baseline_V54")
    a25 = r25.get("V55_A_Live")
    gate_ret_ok = bool(d25 and a25 and d25["net_return_pct"] >= 18.0 and d25["net_return_pct"] > a25["net_return_pct"])
    gate_pf_ok = bool(d25 and d25["summary"]["profit_factor"] >= 1.35)
    gate_dd_ok = bool(d25 and b25 and d25["max_drawdown_pct"] <= b25["max_drawdown_pct"])
    gate_pass = gate_ret_ok and gate_pf_ok and gate_dd_ok

    lines.extend([
        "---",
        "",
        "## 4. 客观体检与 `V55_D_Hybrid` 闸门判定",
        "",
        f"1. **胜出组合**：在 `25 bps` 写实压力滑点下，**`{best_25['arm']}`** 胜出（净收益 `{best_25['net_return_pct']:+.2f}%`、PF `{best_25['summary']['profit_factor']:.2f}`、最大回撤 `-{best_25['max_drawdown_pct']:.2f}%`）；在 `3 bps` 名义滑点下，**`{best_3['arm']}`** 居首（净收益 `{best_3['net_return_pct']:+.2f}%`）。",
        f"2. **25 bps 写实滑点抗压性**：`Baseline_V54` (`{r3['Baseline_V54']['net_return_pct']:+.2f}%` → `{r25['Baseline_V54']['net_return_pct']:+.2f}%`)，`V55_A_Live` (`{r3['V55_A_Live']['net_return_pct']:+.2f}%` → `{r25['V55_A_Live']['net_return_pct']:+.2f}%`)，`V55_B_Wide3x` (`{r3['V55_B_Wide3x']['net_return_pct']:+.2f}%` → `{r25['V55_B_Wide3x']['net_return_pct']:+.2f}%`)，`V55_C_NoBearBounce` (`{r3['V55_C_NoBearBounce']['net_return_pct']:+.2f}%` → `{r25['V55_C_NoBearBounce']['net_return_pct']:+.2f}%`)，`V55_D_Hybrid` (`{r3['V55_D_Hybrid']['net_return_pct']:+.2f}%` → `{r25['V55_D_Hybrid']['net_return_pct']:+.2f}%`)。",
        f"3. **`V55_D_Hybrid` 闸门硬红线审计（`25 bps` 口径）**：",
        f"   - 条件 ① 净收益率 `>= +18.0%` 且显著高于 `V55_A_Live (+{a25['net_return_pct']:.2f}%)`：实测 **`{d25['net_return_pct']:+.2f}%`** → `{'PASS' if gate_ret_ok else 'FAIL'}`",
        f"   - 条件 ② Profit Factor `>= 1.35`：实测 **`{d25['summary']['profit_factor']:.2f}`** → `{'PASS' if gate_pf_ok else 'FAIL'}`",
        f"   - 条件 ③ 最大回撤 `<= Baseline_V54 ({b25['max_drawdown_pct']:.2f}%)`：实测 **`-{d25['max_drawdown_pct']:.2f}%`** → `{'PASS' if gate_dd_ok else 'FAIL'}`",
        f"   - **闸门最终裁定**：**`{'✅ PASS — 准许进入第二阶段落地线上 V5.5.1' if gate_pass else '🛑 FAIL — 终止第二阶段，保持线上代码与进程不变'}`**",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    print("[*] 加载本地 5m K 线与 5 年日线数据集 (纯离线只读)...")
    df5 = pd.read_parquet(PARQUET_5M)
    daily_df = load_daily_panels()

    # 提取有效交易日列表
    dt_s = pd.to_datetime(df5["Datetime"])
    hhmm = dt_s.dt.hour * 60 + dt_s.dt.minute
    rth_dates = df5[(hhmm >= 9 * 60 + 30) & (hhmm <= 15 * 60 + 55)].groupby(dt_s.dt.date)["Datetime"].nunique()
    cand_dates = sorted([d for d, cnt in rth_dates.items() if cnt >= 20])
    print(f"[+] 发现 {len(cand_dates)} 个完整交易日 ({cand_dates[0]} ~ {cand_dates[-1]})，开始零穿越预计算...")

    macro_ctx, daily_ctx = precompute_causal_daily_context(daily_df, cand_dates)
    valid_dates, day_timestamps, bars_by_day_tk, pm_bounds = build_causal_5m_market_replay(df5, daily_ctx)
    print(f"[+] 因果 5m 市场重放索引就绪: {len(valid_dates)} 交易日 × {len(bars_by_day_tk)} 日-标的分片。")

    results_by_slip: Dict[str, List[Dict[str, Any]]] = {}
    for slip_key, rt_slip, slip_cn in SLIPPAGE_REGIMES:
        arm_results = []
        for cfg in PRE_REGISTERED_ARMS:
            res = run_arm_replay(
                cfg=cfg,
                rt_slippage=rt_slip,
                valid_dates=valid_dates,
                day_timestamps=day_timestamps,
                bars_by_day_tk=bars_by_day_tk,
                pm_bounds=pm_bounds,
                macro_ctx=macro_ctx,
                daily_ctx=daily_ctx,
            )
            arm_results.append(res)
            print(
                f"  [{slip_key:5s}] {cfg.name:20s} | 笔数={res['summary']['count']:3d} | "
                f"胜率={res['summary']['win_rate_pct']:5.1f}% | PF={res['summary']['profit_factor']:5.2f} | "
                f"净收益={res['net_return_pct']:+6.2f}% | MaxDD=-{res['max_drawdown_pct']:5.2f}% | "
                f"均持仓={res['summary']['avg_hold_hours']:4.1f}h"
            )
        results_by_slip[slip_key] = arm_results

    os.makedirs(REPORTS_DIR, exist_ok=True)
    md_text = generate_markdown_report(valid_dates, macro_ctx, results_by_slip)
    with open(REPORT_MD_PATH, "w", encoding="utf-8") as f:
        f.write(md_text)

    # JSON 落盘 (含完整拆解与逐笔明细，便于二次审计)
    json_payload = {
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "sample_start": valid_dates[0].isoformat(),
        "sample_end": valid_dates[-1].isoformat(),
        "trading_days": len(valid_dates),
        "macro_regime_by_date": {d.isoformat(): macro_ctx[d] for d in valid_dates},
        "results": results_by_slip,
    }
    with open(REPORT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, ensure_ascii=False, indent=2)

    print(f"\n[+] ✅ 报告已生成:\n    - {REPORT_MD_PATH}\n    - {REPORT_JSON_PATH}")


if __name__ == "__main__":
    main()
