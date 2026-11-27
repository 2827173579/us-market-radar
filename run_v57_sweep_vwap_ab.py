# -*- coding: utf-8 -*-
"""
纯离线研究实验：V5.7 KOL 多头 SWEEP_RECLAIM_LONG 松绑 VWAP 60 日 A/B/C 严格控制变量对照回测
(run_v57_sweep_vwap_ab.py)

绝对只读隔离：
- 复用现有 backtest_v55_kol_60d.py / run_v57_backtest.py / run_v57_slim_ablation.py 的防未来函数 5m 数据管线；
- 绝不修改任何生产代码或生产账本，仅输出 reports/v57_sweep_vwap_ab.{md,json}。

三组严格控制变量设计（除 SWEEP_RECLAIM_LONG 入场/止损/VWAP推保本外，其余 100% 一致）：
- A 组 (A_BASELINE): 现役 V5.7 生产基线（2槽×50%, 7只核心ETF 52%/6%, 禁空反向ETF, Fast-BE26, SWEEP_RECLAIM_LONG 硬卡 vwap_diff >= -0.20% + rvol >= 1.3 + resil）
- B 组 (B_RELAX_VWAP): 纯松绑 VWAP（去掉 SWEEP_RECLAIM_LONG 的 VWAP 硬锁，刺穿 S1/PML/P25 后当根/次根 5m 收回支撑上方且收阳拐头 + rvol >= 1.1 即 30% 底仓入场；止损紧贴 sweep_low 下方 0.25%/0.35% 且不超硬上限；加仓仍保留站稳 VWAP >= 0%）
- C 组 (C_KOL_VWAP_BE): 真 KOL 狙击版（入场同 B 组 + 新增防单边踩踏接飞刀门禁 + 针对水下 SWEEP_RECLAIM_LONG 仓位将 VWAP 改为第一目标/推保本触发器：反弹到 VWAP 附近且浮盈>=0.8%先平35%+移损至保本，若放量站稳 VWAP 则继续保留加仓与尾仓奔跑）
"""

import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

import backtest_v55_kol_60d as bt55
import copy_trade_engine as cte
import copy_trade_kol_engine as kol
import run_v57_backtest as v57
import run_v57_slim_ablation as slim

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
REPORT_JSON = os.path.join(REPORTS_DIR, "v57_sweep_vwap_ab.json")
REPORT_MD = os.path.join(REPORTS_DIR, "v57_sweep_vwap_ab.md")


@dataclass
class SweepABConfig:
    name: str
    label: str
    sweep_mode: str  # "A_BASELINE" | "B_RELAX_VWAP" | "C_KOL_VWAP_BE"
    # 以下全部 100% 锁定与现役生产 V5.7 (copy_trade_kol_engine.py) 完全一致
    max_positions: int = 2
    starter_weight_1x: float = 0.30
    addon_weight_1x: float = 0.20
    starter_weight_3x: float = 0.30
    addon_weight_3x: float = 0.20
    sl_min_1x: float = 0.020
    sl_max_1x: float = 0.035
    sl_min_3x: float = 0.025
    sl_max_3x: float = 0.045
    addon_sl_buffer: float = 0.01
    weak_exit_min_days: int = 2
    partial_1x_pct: float = 4.0
    partial_3x_pct: float = 6.5
    use_etf_52: bool = True
    no_short_inv: bool = True
    use_fast_be: bool = True
    fast_be_1x_pct: float = 2.6
    fast_be_3x_pct: float = 4.0
    fast_be_trim_frac: float = 0.25
    # B/C 组专用窄止损与 VWAP-BE 参数
    sweep_sl_buf_1x: float = 0.0025
    sweep_sl_buf_3x: float = 0.0035
    sweep_sl_min_room: float = 0.0060
    vwap_be_trim_frac: float = 0.35
    vwap_be_min_profit_pct: float = 0.80


def _resolve_tight_sweep_sl(
    cfg: SweepABConfig,
    tk: str,
    entry_px: float,
    sweep_low: float,
    fallback_sl: float,
) -> float:
    """B/C 组扫损针尖窄止损：紧贴本次扫损低点下方 0.25%(1x)/0.35%(3x)，且严格不超过原硬止损上限。"""
    lev3 = kol.is_3x_leveraged(tk)
    sl_max = cfg.sl_max_3x if lev3 else cfg.sl_max_1x
    buf = cfg.sweep_sl_buf_3x if lev3 else cfg.sweep_sl_buf_1x
    lo_cap = entry_px * (1.0 - sl_max)
    hi_cap = entry_px * (1.0 - cfg.sweep_sl_min_room)
    if sweep_low > 0 and sweep_low < entry_px:
        raw = sweep_low * (1.0 - buf)
        clamped = max(lo_cap, min(hi_cap, raw))
        return max(0.01, round(clamped, 2))
    return fallback_sl


def classify_market_regimes(
    valid_dates: List[date],
    day_timestamps: Dict[date, List[datetime]],
    bars_by_day_tk: Dict[Tuple[date, str], Dict[datetime, Dict[str, float]]],
    daily_ctx: Dict[date, Dict[str, Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """按 QQQ 当日真实表现切分 3 类行情日：单边下跌/熊市日、高开低走/震荡日、上涨/反抽日。"""
    regimes: Dict[str, Dict[str, Any]] = {}
    for d_cur in valid_dates:
        d_str = d_cur.isoformat()
        ts_list = day_timestamps.get(d_cur) or []
        qqq_map = bars_by_day_tk.get((d_cur, "QQQ")) or {}
        qqq_pc = float(((daily_ctx.get(d_cur) or {}).get("QQQ") or {}).get("prev_close", 0.0))
        q_bars = [qqq_map[ts] for ts in ts_list if ts in qqq_map]
        if not q_bars or qqq_pc <= 0:
            regimes[d_str] = {
                "regime": "CHOP_FADE",
                "qqq_day_ret_pct": 0.0,
                "qqq_gap_pct": 0.0,
                "qqq_intraday_pct": 0.0,
            }
            continue
        q_open = float(q_bars[0]["open"])
        q_close = float(q_bars[-1]["close"])
        q_ret = (q_close - qqq_pc) / qqq_pc * 100.0
        q_gap = (q_open - qqq_pc) / qqq_pc * 100.0
        q_intra = (q_close - q_open) / q_open * 100.0 if q_open > 0 else 0.0
        high_open_fade = (q_gap >= 0.45 and q_intra <= -0.60)
        if q_ret <= -1.0:
            bucket = "BEAR_DOWN"
        elif q_ret >= 0.60 and (not high_open_fade):
            bucket = "UP_REBOUND"
        else:
            bucket = "CHOP_FADE"
        regimes[d_str] = {
            "regime": bucket,
            "qqq_day_ret_pct": round(q_ret, 2),
            "qqq_gap_pct": round(q_gap, 2),
            "qqq_intraday_pct": round(q_intra, 2),
            "high_open_fade": high_open_fade,
        }
    return regimes


def run_sweep_arm(
    cfg: SweepABConfig,
    rt_slippage: float,
    valid_dates: List[date],
    day_timestamps: Dict[date, List[datetime]],
    bars_by_day_tk: Dict[Tuple[date, str], Dict[datetime, Dict[str, float]]],
    pm_bounds: Dict[Tuple[date, str], Dict[str, float]],
    macro_ctx: Dict[date, Dict[str, Any]],
    daily_ctx: Dict[date, Dict[str, Dict[str, Any]]],
    day_regimes: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    cash = bt55.INITIAL_CAPITAL
    peak_equity = bt55.INITIAL_CAPITAL
    max_dd = 0.0
    open_positions: List[Dict[str, Any]] = []
    closed_trades: List[Dict[str, Any]] = []
    blocked_knife_events: List[Dict[str, Any]] = []

    last_close_dt: Optional[datetime] = None
    last_exit_by_tk: Dict[str, Dict[str, Any]] = {}
    vwap_be_trigger_count = 0

    for d_idx, d_cur in enumerate(valid_dates):
        d_str = d_cur.isoformat()
        m_info = macro_ctx.get(d_cur) or {}
        d_tks = daily_ctx.get(d_cur) or {}
        regime = str(m_info.get("market_regime") or "NEUTRAL_CHOP")
        vix_brake = bool(m_info.get("vix_hard_brake", False))
        panic_clear = bool(m_info.get("panic_clearing_active", False))
        long_blocked = bool((regime == "BEAR_DEFENSE" or vix_brake) and (not panic_clear))

        entries_today = 0
        max_entries_day = max(kol.MAX_NEW_ENTRIES_PER_DAY, cfg.max_positions + 2)
        ts_list = day_timestamps.get(d_cur) or []

        # 逐日维护无前视前一根 5m K 线与连续 P25 破位计数
        prev_bar_by_tk: Dict[str, Dict[str, float]] = {}
        consec_below_p25_by_tk: Dict[str, int] = {}

        for bar_i, ts in enumerate(ts_list):
            et_min = ts.hour * 60 + ts.minute
            is_first_bar_of_day = (bar_i == 0)

            # 1) 在途持仓管理
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
                partial_done = bool(pos["partial_tp_done"])
                be_active = bool(pos["be_stop_active"])

                # (1a) 开盘跳空止损 + 盘中针尖止损
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

                gross_ret_pct = (((c_px - entry_px) / entry_px) if direction == "LONG" else ((entry_px - c_px) / entry_px)) * 100.0
                ret_c = bt55._net_ret(direction, entry_px, c_px, rt_slippage)
                pos["max_favorable_return_pct"] = max(pos["max_favorable_return_pct"], gross_ret_pct)
                if direction == "LONG":
                    pos["best_price"] = max(pos["best_price"], c_px)
                else:
                    pos["best_price"] = min(pos["best_price"], c_px)

                # (1b-C) C / C+ 组专属：水下 SWEEP_RECLAIM_LONG 反弹至 VWAP 附近 -> 推保本 (C平35%, C+平0%)
                if (
                    (not exit_reason)
                    and cfg.sweep_mode in ("C_KOL_VWAP_BE", "C_PLUS_PURE_BE")
                    and direction == "LONG"
                    and pos.get("setup") == "SWEEP_RECLAIM_LONG"
                    and pos.get("entered_underwater", False)
                    and (not pos.get("vwap_be_done", False))
                    and (not partial_done)
                    and (not be_active)
                ):
                    if vwap_diff >= -0.15 and gross_ret_pct >= cfg.vwap_be_min_profit_pct:
                        t_frac = 0.0 if cfg.sweep_mode == "C_PLUS_PURE_BE" else cfg.vwap_be_trim_frac
                        if t_frac > 0.0:
                            close_alloc = round(alloc * t_frac, 2)
                            rem_alloc = round(alloc - close_alloc, 2)
                            part_pnl = round(close_alloc * ret_c, 2)
                            cash = round(cash + max(0.0, close_alloc + part_pnl), 2)
                            pos["allocated_capital"] = rem_alloc
                            pos["slot_weight"] = round(pos["slot_weight"] * (1.0 - t_frac), 3)
                            pos["partial_closed_capital"] = round(pos["partial_closed_capital"] + close_alloc, 2)
                            pos["partial_realized_pnl_usd"] = round(pos["partial_realized_pnl_usd"] + part_pnl, 2)
                            pos["vwap_be_trimmed"] = True
                            alloc = rem_alloc
                        pos["vwap_be_done"] = True
                        pos["vwap_be_triggered"] = True
                        pos["vwap_be_price"] = round(c_px, 2)
                        pos["vwap_be_time_et"] = ts.strftime("%Y-%m-%d %H:%M:%S")
                        pos["be_stop_active"] = True
                        be_active = True
                        pos["sl_price"] = max(sl_px, round(entry_px * (1.0 + kol.BE_STOP_BUFFER), 2))
                        sl_px = pos["sl_price"]
                        vwap_be_trigger_count += 1

                # (1b) 金字塔顺势加仓（四组一致：必须站稳 VWAP >= 0% 且达标浮盈）
                if (not exit_reason) and pos["stage"] == 1 and pos["can_add"] and (not partial_done):
                    add_trig = kol.ADDON_TRIGGER_3X_PCT if lev3 else kol.ADDON_TRIGGER_1X_PCT
                    on_vwap = (vwap_diff >= 0.0) if direction == "LONG" else (vwap_diff <= 0.0)
                    # C 组已减仓 35% 时要求放量站稳 VWAP (+0.15% & rvol>=1.2)；C+ 未减仓，100% 沿用标准站稳 VWAP 加仓
                    c_addon_ok = True
                    if pos.get("vwap_be_done") and cfg.sweep_mode == "C_KOL_VWAP_BE":
                        c_addon_ok = (vwap_diff >= 0.15 and rvol_5m >= 1.2)
                    if gross_ret_pct >= add_trig and on_vwap and c_addon_ok:
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
                            entry_px = pos["entry_price"]
                            pos["allocated_capital"] = round(alloc + add_alloc, 2)
                            alloc = pos["allocated_capital"]
                            pos["slot_weight"] = round(pos["slot_weight"] + add_wt, 3)
                            pos["peak_slot_weight"] = max(pos["peak_slot_weight"], pos["slot_weight"])
                            pos["stage"] = 2
                            pos["can_add"] = False
                            if direction == "LONG":
                                sl_floor = round(entry_px * (1.0 + kol.BE_STOP_BUFFER), 2) if be_active else round(orig_ep * (1.0 - cfg.addon_sl_buffer), 2)
                                pos["sl_price"] = max(sl_px, sl_floor)
                            else:
                                pos["sl_price"] = min(sl_px, round(orig_ep * (1.0 + cfg.addon_sl_buffer), 2))
                            sl_px = pos["sl_price"]
                            gross_ret_pct = (((c_px - entry_px) / entry_px) if direction == "LONG" else ((entry_px - c_px) / entry_px)) * 100.0
                            ret_c = bt55._net_ret(direction, entry_px, c_px, rt_slippage)

                # (1c) V5.7 Neo Lee 快推保本 BE26（C+ 仅纯推 BE 未减仓时，仍严格沿用 V5.7 标准止盈阶梯）
                can_fast_be = (not be_active) or (cfg.sweep_mode == "C_PLUS_PURE_BE" and pos.get("vwap_be_done") and (not pos.get("fast_be_trimmed")))
                if (not exit_reason) and cfg.use_fast_be and can_fast_be and (not partial_done):
                    fbe_trig = cfg.fast_be_3x_pct if lev3 else cfg.fast_be_1x_pct
                    if gross_ret_pct >= fbe_trig:
                        if cfg.fast_be_trim_frac > 0:
                            close_alloc = round(alloc * cfg.fast_be_trim_frac, 2)
                            rem_alloc = round(alloc - close_alloc, 2)
                            part_pnl = round(close_alloc * ret_c, 2)
                            cash = round(cash + max(0.0, close_alloc + part_pnl), 2)
                            pos["allocated_capital"] = rem_alloc
                            pos["slot_weight"] = round(pos["slot_weight"] * (1.0 - cfg.fast_be_trim_frac), 3)
                            pos["partial_closed_capital"] = round(pos["partial_closed_capital"] + close_alloc, 2)
                            pos["partial_realized_pnl_usd"] = round(pos["partial_realized_pnl_usd"] + part_pnl, 2)
                            pos["fast_be_trimmed"] = True
                            alloc = rem_alloc
                        pos["be_stop_active"] = True
                        pos["can_add"] = False
                        be_active = True
                        if direction == "LONG":
                            pos["sl_price"] = max(sl_px, round(entry_px * (1.0 + kol.BE_STOP_BUFFER), 2))
                        else:
                            pos["sl_price"] = min(sl_px, round(entry_px * (1.0 - kol.BE_STOP_BUFFER), 2))
                        sl_px = pos["sl_price"]

                # (1e) 一阶分批落袋 (+4.0% 1x / +6.5% 3x)
                if (not exit_reason) and (not partial_done):
                    p_trig = cfg.partial_3x_pct if lev3 else cfg.partial_1x_pct
                    if gross_ret_pct >= p_trig:
                        if pos.get("vwap_be_trimmed"):
                            t_frac = 0.25  # C 组之前已平 35%，再平余仓 25% (~保留 49% 尾仓)
                        elif pos.get("fast_be_trimmed"):
                            t_frac = 0.333
                        else:
                            t_frac = 0.50
                        close_alloc = round(alloc * t_frac, 2)
                        rem_alloc = round(alloc - close_alloc, 2)
                        part_pnl = round(close_alloc * ret_c, 2)
                        cash = round(cash + max(0.0, close_alloc + part_pnl), 2)
                        pos["allocated_capital"] = rem_alloc
                        pos["slot_weight"] = round(pos["slot_weight"] * (1.0 - t_frac), 3)
                        pos["partial_tp_done"] = True
                        pos["can_add"] = False
                        pos["be_stop_active"] = True
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

                # (1f) 二阶抛物线卖强 (CLIMAX_TP) & 移动追踪 (TRAIL_TP)
                if (not exit_reason) and partial_done:
                    climax_min = kol.CLIMAX_PROFIT_3X_PCT if lev3 else kol.CLIMAX_PROFIT_1X_PCT
                    is_climax = (
                        ((direction == "LONG" and rsi_5m >= kol.CLIMAX_RSI_HIGH) or (direction == "SHORT" and rsi_5m <= kol.CLIMAX_RSI_LOW))
                        and rvol_5m >= kol.CLIMAX_RVOL_MIN
                        and gross_ret_pct >= climax_min
                    )
                    if is_climax:
                        exit_reason, exit_fill_px = "CLIMAX_TP", c_px
                    else:
                        trail_dd = kol.TRAIL_DRAWDOWN_3X_PCT if lev3 else kol.TRAIL_DRAWDOWN_1X_PCT
                        bp = pos["best_price"]
                        dd_best = ((bp - c_px) / bp * 100.0) if direction == "LONG" else ((c_px - bp) / bp * 100.0)
                        if dd_best >= trail_dd:
                            exit_reason, exit_fill_px = "TRAIL_TP", c_px

                # (1g) T+2 早撤体检 + T+3 到期（三组完全一致）
                if not exit_reason:
                    if (d_idx >= pos["expiry_idx"] and et_min >= kol.T3_FORCE_EXIT_MIN) or (d_idx > pos["expiry_idx"]):
                        exit_reason, exit_fill_px = "T3_EXPIRE", c_px
                    elif (not partial_done) and (not be_active) and (d_idx - pos["entry_day_idx"]) >= cfg.weak_exit_min_days:
                        days_held = d_idx - pos["entry_day_idx"]
                        check_min = kol.EARLY_CHECK_LATE_MIN if (days_held == cfg.weak_exit_min_days and pos["entry_min"] > kol.LATE_ENTRY_CUTOFF_MIN) else kol.EARLY_CHECK_NORMAL_MIN
                        if et_min >= check_min:
                            wrong_vwap = (vwap_diff < 0.0) if direction == "LONG" else (vwap_diff > 0.0)
                            weak_stall = (gross_ret_pct < 0.8) and wrong_vwap
                            profit_fade = (pos["max_favorable_return_pct"] >= 3.0) and (gross_ret_pct < 0.5)
                            if weak_stall or profit_fade:
                                exit_reason, exit_fill_px = "WEAK_EARLY_EXIT", c_px

                if exit_reason:
                    runner_ret = bt55._net_ret(direction, pos["entry_price"], exit_fill_px, rt_slippage)
                    runner_pnl = round(pos["allocated_capital"] * runner_ret, 2)
                    cash = round(cash + max(0.0, pos["allocated_capital"] + runner_pnl), 2)
                    tot_alloc = round(pos["allocated_capital"] + pos.get("partial_closed_capital", 0.0), 2)
                    tot_pnl = round(pos.get("partial_realized_pnl_usd", 0.0) + runner_pnl, 2)
                    tot_ret_pct = round((tot_pnl / tot_alloc) * 100.0, 2) if tot_alloc > 0 else round(runner_ret * 100.0, 2)
                    init_risk_pct = float(pos.get("init_risk_pct", 2.5))
                    r_mult = round(tot_ret_pct / init_risk_pct, 2) if init_risk_pct > 0 else 0.0
                    outcome = ("T3_WIN" if tot_pnl > 0 else "T3_LOSS") if exit_reason == "T3_EXPIRE" else exit_reason
                    ed_reg = day_regimes.get(pos["entry_date"], {})
                    closed_trades.append({
                        "ticker": tk, "is_3x": lev3, "direction": direction,
                        "entry_tag": pos.get("entry_tag", "NORMAL"),
                        "setup": pos.get("setup", "V57"),
                        "sweep_trigger": pos.get("sweep_trigger", ""),
                        "stage": pos.get("stage", 1),
                        "entered_underwater": bool(pos.get("entered_underwater", False)),
                        "vwap_be_triggered": bool(pos.get("vwap_be_triggered", False)),
                        "vwap_be_price": pos.get("vwap_be_price"),
                        "max_favorable_return_pct": round(pos.get("max_favorable_return_pct", 0.0), 2),
                        "entry_date": pos["entry_date"], "entry_month": pos["entry_date"][:7],
                        "entry_time_et": pos["entry_dt"].strftime("%Y-%m-%d %H:%M:%S"),
                        "orig_entry_price": pos["orig_entry_price"], "entry_price": pos["entry_price"],
                        "entry_vwap": pos.get("entry_vwap", 0.0),
                        "entry_vwap_diff_pct": pos.get("entry_vwap_diff_pct", 0.0),
                        "init_sl_price": pos.get("init_sl_price", 0.0),
                        "init_risk_pct": round(init_risk_pct, 2),
                        "r_multiple": r_mult,
                        "qqq_entry_pct": pos.get("qqq_entry_pct", 0.0),
                        "entry_day_regime": ed_reg.get("regime", "CHOP_FADE"),
                        "entry_day_qqq_ret": ed_reg.get("qqq_day_ret_pct", 0.0),
                        "exit_date": d_str, "exit_time_et": ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "exit_price": round(exit_fill_px, 2), "outcome": outcome, "exit_reason": exit_reason,
                        "gap_sl_slip": gap_sl_slip, "allocated_capital": tot_alloc,
                        "pnl_usd": tot_pnl, "net_return_pct": tot_ret_pct,
                        "hold_hours": round((ts - pos["entry_dt"]).total_seconds() / 3600.0, 2),
                    })
                    last_close_dt = ts
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
                b_cur = (bars_by_day_tk.get((d_cur, pos["ticker"])) or {}).get(ts)
                lp = b_cur["close"] if b_cur else pos.get("last_price", pos["entry_price"])
                r_u = bt55._net_ret(pos["direction"], pos["entry_price"], lp, rt_slippage)
                deployed_val += max(0.0, pos["allocated_capital"] * (1.0 + r_u))
            cur_equity = round(cash + deployed_val, 2)

            # 更新当日每只标的的连续 BELOW_P25 计数（严格用截至当前 bar 的因果信息）
            for tk_u, info_u in d_tks.items():
                bu = (bars_by_day_tk.get((d_cur, tk_u)) or {}).get(ts)
                if bu and bu["day_open"] > 0:
                    intra_u = (bu["close"] - bu["day_open"]) / bu["day_open"] * 100.0
                    if intra_u <= -0.55 * info_u["atr_pct"]:
                        consec_below_p25_by_tk[tk_u] = consec_below_p25_by_tk.get(tk_u, 0) + 1
                    else:
                        consec_below_p25_by_tk[tk_u] = 0

            # 3) 开仓扫描（RTH 09:45~15:20）
            in_win = (kol.ENTRY_START_MIN <= et_min <= kol.ENTRY_END_MIN)
            slot_cd_ok = True
            if entries_today >= cfg.max_positions and last_close_dt is not None:
                slot_cd_ok = ((ts - last_close_dt).total_seconds() / 60.0) >= kol.SLOT_COOLDOWN_MINUTES

            has_free = len(open_positions) < cfg.max_positions
            if in_win and has_free and entries_today < max_entries_day and slot_cd_ok:
                qqq_b = (bars_by_day_tk.get((d_cur, "QQQ")) or {}).get(ts)
                qqq_pc = float((d_tks.get("QQQ") or {}).get("prev_close", 0.0))
                qqq_pct = ((qqq_b["close"] - qqq_pc) / qqq_pc * 100.0) if (qqq_b and qqq_pc > 0) else 0.0
                qqq_vwap_diff = ((qqq_b["close"] - qqq_b["vwap"]) / qqq_b["vwap"] * 100.0) if (qqq_b and qqq_b["vwap"] > 0) else 0.0
                qqq_intra_open = ((qqq_b["close"] - qqq_b["day_open"]) / qqq_b["day_open"] * 100.0) if (qqq_b and qqq_b["day_open"] > 0) else 0.0

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
                    if not b_cur or b_cur["close"] <= 0:
                        continue

                    px = b_cur["close"]
                    o_px = b_cur["open"]
                    l_px = b_cur["low"]
                    pc = info["prev_close"]
                    if pc <= 0:
                        continue
                    pct_chg = (px - pc) / pc * 100.0
                    gap_pct = ((b_cur["day_open"] - pc) / pc * 100.0) if b_cur["day_open"] > 0 else pct_chg
                    lev3 = kol.is_3x_leveraged(tk)
                    is_core_etf = cfg.use_etf_52 and (tk in slim.SLIM_CORE_ETFS)
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

                    intraday_from_open = ((px - b_cur["day_open"]) / b_cur["day_open"] * 100.0) if b_cur["day_open"] > 0 else 0.0
                    corr_div = "ABOVE_P75" if intraday_from_open >= 0.55 * info["atr_pct"] else ("BELOW_P25" if intraday_from_open <= -0.55 * info["atr_pct"] else "")
                    p25_px = b_cur["day_open"] * (1.0 - 0.55 * info["atr_pct"] / 100.0) if b_cur["day_open"] > 0 else 0.0

                    p_l = info["calibrated_win_rate_pct"]
                    p_s = info["calibrated_short_win_rate_pct"]
                    is_crash = info["is_crash_halt"]
                    is_deep_pb = info["is_deep_pullback"]
                    is_broken = info["is_broken_bearish"]

                    prev_b = prev_bar_by_tk.get(tk)
                    prev_lo = float(prev_b["low"]) if prev_b else l_px
                    prev_c = float(prev_b["close"]) if prev_b else 0.0
                    prev_rsi = float(prev_b["rsi_5m"]) if prev_b else rsi_5m
                    min_2bar_lo = min(l_px, prev_lo)

                    last_ex = last_exit_by_tk.get(tk)
                    elapsed_ex = ((ts - last_ex["exit_dt"]).total_seconds() / 60.0) if (last_ex and last_ex["exit_date"] == d_str) else 9999.0

                    def _cd_ok(tdir: str) -> bool:
                        if not last_ex or last_ex["exit_date"] != d_str:
                            return True
                        if last_ex["direction"] == tdir:
                            return elapsed_ex >= kol.SLOT_COOLDOWN_MINUTES
                        if last_ex["exit_reason"] in ("SL_HIT", "WEAK_EARLY_EXIT"):
                            return False
                        return elapsed_ex >= kol.SLOT_COOLDOWN_MINUTES

                    min_p = 52.0 if is_core_etf else 55.0
                    min_sp = 6.0 if is_core_etf else 10.0
                    pri = 2.0 if tk in leaders_set else (1.0 if lev3 else 0.0)
                    st_wt = cfg.starter_weight_3x if lev3 else cfg.starter_weight_1x

                    # A. LONG 通道
                    if _cd_ok("LONG") and gap_pct <= max_gap and (not is_crash):
                        norm_l = (not long_blocked) or (tk in cte.SAFE_HAVEN_LONG_EXEMPT)
                        if norm_l and p_l >= min_p and (p_l - p_s) >= min_sp:
                            # 突破追强 (BREAKOUT_MOMENTUM_LONG) — 三组 100% 严格一致
                            brk_ok = (
                                (vwap_diff >= 0.0)
                                and ((r1 > 0 and px >= r1 * 0.998) or corr_div == "ABOVE_P75")
                                and (rvol_5m >= 1.5)
                                and (50.0 <= rsi_5m <= 76.0)
                            )

                            # 扫损收回多 (SWEEP_RECLAIM_LONG) — 三组唯一实验变量
                            sweep_ok = False
                            sweep_trigger_tag = ""
                            use_tight_sl = False

                            if cfg.sweep_mode == "A_BASELINE":
                                swept = (
                                    (s1 > 0 and ((day_lo <= s1 * 1.006 and px >= s1) or abs(px - s1) / s1 <= 0.012))
                                    or (pm_lo > 0 and day_lo <= pm_lo * 1.005 and px >= pm_lo)
                                    or is_deep_pb or corr_div == "BELOW_P25"
                                )
                                resil = (pct_chg >= qqq_pct - 0.5) or (tk in leaders_set)
                                sweep_ok = swept and (vwap_diff >= -0.20) and (rvol_5m >= 1.3) and resil
                                if sweep_ok:
                                    sweep_trigger_tag = "A_VWAP_SWEEP"
                            else:
                                # B 组与 C 组共用松绑 VWAP 触发器：
                                # 盘中当根或前一根 5m 向下刺穿 S1 / PML / P25，当前 5m 收盘重新收回支撑上方 + 收阳拐头 + rvol >= 1.1
                                s1_reclaim = (s1 > 0) and (min_2bar_lo <= s1 * 1.003) and (px >= s1)
                                pml_reclaim = (pm_lo > 0) and (min_2bar_lo <= pm_lo * 1.003) and (px >= pm_lo)
                                p25_reclaim = (p25_px > 0) and (min_2bar_lo <= p25_px * 1.003) and (px >= p25_px)
                                green_turn = (px > o_px) and (prev_c <= 0 or px >= prev_c or rsi_5m > prev_rsi)
                                raw_relaxed_sweep = (s1_reclaim or pml_reclaim or p25_reclaim) and green_turn and (rvol_5m >= 1.1)

                                if raw_relaxed_sweep:
                                    tags = []
                                    if s1_reclaim:
                                        tags.append("S1")
                                    if pml_reclaim:
                                        tags.append("PML")
                                    if p25_reclaim:
                                        tags.append("P25")
                                    sweep_trigger_tag = "+".join(tags)

                                    if cfg.sweep_mode == "B_RELAX_VWAP":
                                        sweep_ok = True
                                        use_tight_sl = True
                                    else:
                                        # C / C+ 防单边踩踏接飞刀门禁
                                        is_underwater = (vwap_diff < -0.15)
                                        is_haven_or_inv = (tk in cte.SAFE_HAVEN_LONG_EXEMPT) or (tk in cte.INVERSE_ETFS)
                                        stampede_block = False
                                        block_reason = ""
                                        qqq_flush = (qqq_pct <= -1.0 and qqq_vwap_diff <= -0.40)

                                        if cfg.sweep_mode == "C_PLUS_PURE_BE":
                                            # C+ 组：QQQ_FLUSH 扩大至全部非避险/非反向多头 SWEEP（不限于水下）
                                            if (not is_haven_or_inv) and qqq_flush:
                                                stampede_block, block_reason = True, "QQQ_FLUSH_ALL"
                                            elif is_underwater and (not is_haven_or_inv):
                                                tk_knife = (
                                                    consec_below_p25_by_tk.get(tk, 0) >= 2
                                                    and pct_chg <= (-4.5 if lev3 else -2.5)
                                                    and vwap_diff <= (-2.5 if lev3 else -1.5)
                                                )
                                                rel_bleed = (
                                                    qqq_intra_open <= -0.80
                                                    and pct_chg < qqq_pct - (3.0 if lev3 else 1.5)
                                                )
                                                if tk_knife:
                                                    stampede_block, block_reason = True, "STOCK_P25_KNIFE"
                                                elif rel_bleed:
                                                    stampede_block, block_reason = True, "REL_BLEED_FADE"
                                        else:
                                            # C 组原口径（保持 100% 不变）：仅针对水下入场
                                            if is_underwater and (tk not in cte.SAFE_HAVEN_LONG_EXEMPT):
                                                tk_knife = (
                                                    consec_below_p25_by_tk.get(tk, 0) >= 2
                                                    and pct_chg <= (-4.5 if lev3 else -2.5)
                                                    and vwap_diff <= (-2.5 if lev3 else -1.5)
                                                )
                                                rel_bleed = (
                                                    qqq_intra_open <= -0.80
                                                    and pct_chg < qqq_pct - (3.0 if lev3 else 1.5)
                                                )
                                                if qqq_flush:
                                                    stampede_block, block_reason = True, "QQQ_FLUSH"
                                                elif tk_knife:
                                                    stampede_block, block_reason = True, "STOCK_P25_KNIFE"
                                                elif rel_bleed:
                                                    stampede_block, block_reason = True, "REL_BLEED_FADE"

                                        if stampede_block:
                                            blocked_knife_events.append({
                                                "date": d_str,
                                                "time_et": ts.strftime("%Y-%m-%d %H:%M:%S"),
                                                "ticker": tk,
                                                "price": round(px, 2),
                                                "vwap": round(vwap_px, 2),
                                                "vwap_diff_pct": round(vwap_diff, 2),
                                                "qqq_pct": round(qqq_pct, 2),
                                                "sweep_trigger": sweep_trigger_tag,
                                                "block_reason": block_reason,
                                            })
                                        else:
                                            sweep_ok = True
                                            use_tight_sl = True

                            if sweep_ok or brk_ok:
                                std_sl = slim._resolve_sl_slim(cfg, tk, "LONG", px, day_lo, day_hi, pm_lo, pm_hi, s1, r1)
                                if sweep_ok and use_tight_sl:
                                    sl_px = _resolve_tight_sweep_sl(cfg, tk, px, min_2bar_lo, std_sl)
                                else:
                                    sl_px = std_sl
                                raw_cands.append({
                                    "ticker": tk, "direction": "LONG",
                                    "setup": "SWEEP_RECLAIM_LONG" if sweep_ok else "BREAKOUT_MOMENTUM_LONG",
                                    "sweep_trigger": sweep_trigger_tag if sweep_ok else "BREAKOUT",
                                    "entry_tag": "MAIN_AI_ETF" if is_core_etf else "NORMAL",
                                    "slot_weight": st_wt, "can_add": True,
                                    "score": p_l + (p_l - p_s) * 0.25 + pri + (2.0 if sweep_ok else 1.0),
                                    "prob": p_l, "entry_price": round(px, 2), "sl_price": sl_px, "mg": mg,
                                    "entry_vwap": round(vwap_px, 2), "entry_vwap_diff_pct": round(vwap_diff, 2),
                                    "entered_underwater": bool(sweep_ok and vwap_diff < -0.15),
                                    "qqq_entry_pct": round(qqq_pct, 2),
                                })

                    # B. SHORT 通道 — 三组 100% 严格一致
                    inv_surge = bool(tk in cte.INVERSE_ETFS and (gap_pct >= 8.0 or pct_chg >= 8.0 or info["rsi_14"] >= 68.0))
                    no_short_inv = cfg.no_short_inv and (tk in slim.SLIM_NO_SHORT_INVERSE)
                    if _cd_ok("SHORT") and (tk not in cte.NON_SHORTABLE_TICKERS) and (not no_short_inv) and (not inv_surge) and (-gap_pct) <= max_gap:
                        if p_s >= min_p and (p_s - p_l) >= min_sp:
                            b_ctx = is_broken or (regime == "BEAR_DEFENSE") or vix_brake or (pct_chg < 0) or (corr_div == "BELOW_P25")
                            near_rej = ((-1.5 <= vwap_diff <= 0.40) or (r1 > 0 and abs(px - r1) / r1 <= 0.012)) and (vwap_diff <= 0.25) and (rsi_5m <= 58.0)
                            if b_ctx and near_rej:
                                sl_px = slim._resolve_sl_slim(cfg, tk, "SHORT", px, day_lo, day_hi, pm_lo, pm_hi, s1, r1)
                                raw_cands.append({
                                    "ticker": tk, "direction": "SHORT", "setup": "REBOUND_REJECT_SHORT",
                                    "sweep_trigger": "SHORT_REJECT",
                                    "entry_tag": "MAIN_AI_ETF" if is_core_etf else "NORMAL",
                                    "slot_weight": st_wt, "can_add": True,
                                    "score": p_s + (p_s - p_l) * 0.25 + pri + (2.5 if long_blocked else 0.0),
                                    "prob": p_s, "entry_price": round(px, 2), "sl_price": sl_px, "mg": mg,
                                    "entry_vwap": round(vwap_px, 2), "entry_vwap_diff_pct": round(vwap_diff, 2),
                                    "entered_underwater": False,
                                    "qqq_entry_pct": round(qqq_pct, 2),
                                })

                raw_cands.sort(key=lambda c: (-c["score"], -c["prob"], c["ticker"]))
                used_t, used_g = set(held_tks), set(held_grps)
                exp_idx = min(len(valid_dates) - 1, d_idx + kol.HOLD_TRADING_DAYS)

                for c in raw_cands:
                    if len(open_positions) >= cfg.max_positions or entries_today >= max_entries_day:
                        break
                    tk, mg = c["ticker"], c["mg"]
                    if tk in used_t or (mg and mg in used_g):
                        continue
                    alloc = round(min(cash, cur_equity * c["slot_weight"]), 2)
                    if alloc < 10.0:
                        continue
                    cash = round(cash - alloc, 2)
                    init_risk_pct = abs(c["entry_price"] - c["sl_price"]) / c["entry_price"] * 100.0 if c["entry_price"] > 0 else 2.5
                    open_positions.append({
                        "ticker": tk, "direction": c["direction"], "setup": c["setup"],
                        "sweep_trigger": c["sweep_trigger"],
                        "entry_tag": c["entry_tag"], "entry_score": c["score"],
                        "stage": 1, "can_add": c["can_add"],
                        "slot_weight": c["slot_weight"], "peak_slot_weight": c["slot_weight"],
                        "entry_date": d_str, "entry_day_idx": d_idx, "entry_min": et_min,
                        "entry_dt": ts, "expiry_idx": exp_idx,
                        "orig_entry_price": c["entry_price"], "entry_price": c["entry_price"],
                        "entry_vwap": c["entry_vwap"], "entry_vwap_diff_pct": c["entry_vwap_diff_pct"],
                        "entered_underwater": c["entered_underwater"],
                        "qqq_entry_pct": c["qqq_entry_pct"],
                        "init_sl_price": c["sl_price"], "sl_price": c["sl_price"],
                        "init_risk_pct": init_risk_pct,
                        "allocated_capital": alloc, "partial_tp_done": False,
                        "be_stop_active": False, "fast_be_trimmed": False,
                        "vwap_be_done": False, "vwap_be_triggered": False, "vwap_be_trimmed": False,
                        "vwap_be_price": None, "vwap_be_time_et": None,
                        "partial_closed_capital": 0.0, "partial_realized_pnl_usd": 0.0,
                        "best_price": c["entry_price"], "max_favorable_return_pct": 0.0,
                        "last_price": c["entry_price"], "cur_ret_pct": 0.0,
                        "cur_vwap_diff": c["entry_vwap_diff_pct"],
                    })
                    used_t.add(tk)
                    if mg:
                        used_g.add(mg)
                    entries_today += 1

            # 更新 prev_bar_by_tk（仅在当前 bar 处理完毕后写入，严格防前视）
            for tk_u in d_tks.keys():
                bu = (bars_by_day_tk.get((d_cur, tk_u)) or {}).get(ts)
                if bu:
                    prev_bar_by_tk[tk_u] = bu

            # 4) 记录权益峰值与回撤
            dep_end = 0.0
            for pos in open_positions:
                b_cur = (bars_by_day_tk.get((d_cur, pos["ticker"])) or {}).get(ts)
                lp = b_cur["close"] if b_cur else pos.get("last_price", pos["entry_price"])
                r_u = bt55._net_ret(pos["direction"], pos["entry_price"], lp, rt_slippage)
                dep_end += max(0.0, pos["allocated_capital"] * (1.0 + r_u))
            eq_now = round(cash + dep_end, 2)
            peak_equity = max(peak_equity, eq_now)
            dd_now = ((peak_equity - eq_now) / peak_equity * 100.0) if peak_equity > 0 else 0.0
            max_dd = max(max_dd, dd_now)

    # 期末未平仓按最后可用 5m 收盘 M2M 结算
    last_d = valid_dates[-1]
    last_ts = day_timestamps[last_d][-1]
    for pos in open_positions:
        tmap = bars_by_day_tk.get((last_d, pos["ticker"])) or {}
        lp = pos.get("last_price", pos["entry_price"])
        for ts_rev in reversed(day_timestamps[last_d]):
            if ts_rev in tmap:
                lp = tmap[ts_rev]["close"]
                break
        rr = bt55._net_ret(pos["direction"], pos["entry_price"], lp, rt_slippage)
        rp = round(pos["allocated_capital"] * rr, 2)
        cash = round(cash + max(0.0, pos["allocated_capital"] + rp), 2)
        ta = round(pos["allocated_capital"] + pos.get("partial_closed_capital", 0.0), 2)
        tp = round(pos.get("partial_realized_pnl_usd", 0.0) + rp, 2)
        tot_ret_pct = round((tp / ta) * 100.0, 2) if ta > 0 else 0.0
        init_risk_pct = float(pos.get("init_risk_pct", 2.5))
        r_mult = round(tot_ret_pct / init_risk_pct, 2) if init_risk_pct > 0 else 0.0
        ed_reg = day_regimes.get(pos["entry_date"], {})
        closed_trades.append({
            "ticker": pos["ticker"], "is_3x": kol.is_3x_leveraged(pos["ticker"]),
            "direction": pos["direction"], "entry_tag": pos.get("entry_tag", "NORMAL"),
            "setup": pos.get("setup", "M2M"), "sweep_trigger": pos.get("sweep_trigger", ""),
            "stage": pos.get("stage", 1),
            "entered_underwater": bool(pos.get("entered_underwater", False)),
            "vwap_be_triggered": bool(pos.get("vwap_be_triggered", False)),
            "vwap_be_price": pos.get("vwap_be_price"),
            "max_favorable_return_pct": round(pos.get("max_favorable_return_pct", 0.0), 2),
            "entry_date": pos["entry_date"], "entry_month": pos["entry_date"][:7],
            "entry_time_et": pos["entry_dt"].strftime("%Y-%m-%d %H:%M:%S"),
            "orig_entry_price": pos["orig_entry_price"], "entry_price": pos["entry_price"],
            "entry_vwap": pos.get("entry_vwap", 0.0),
            "entry_vwap_diff_pct": pos.get("entry_vwap_diff_pct", 0.0),
            "init_sl_price": pos.get("init_sl_price", 0.0),
            "init_risk_pct": round(init_risk_pct, 2),
            "r_multiple": r_mult,
            "qqq_entry_pct": pos.get("qqq_entry_pct", 0.0),
            "entry_day_regime": ed_reg.get("regime", "CHOP_FADE"),
            "entry_day_qqq_ret": ed_reg.get("qqq_day_ret_pct", 0.0),
            "exit_date": last_d.isoformat(), "exit_time_et": last_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "exit_price": round(lp, 2), "outcome": "M2M_END", "exit_reason": "M2M_END",
            "gap_sl_slip": False, "allocated_capital": ta, "pnl_usd": tp,
            "net_return_pct": tot_ret_pct,
            "hold_hours": round((last_ts - pos["entry_dt"]).total_seconds() / 3600.0, 2),
        })

    end_eq = round(cash, 2)
    peak_equity = max(peak_equity, end_eq)
    max_dd = round(max(max_dd, (peak_equity - end_eq) / peak_equity * 100.0 if peak_equity > 0 else 0.0), 2)
    net_ret = round((end_eq - bt55.INITIAL_CAPITAL) / bt55.INITIAL_CAPITAL * 100.0, 2)
    calmar = round(net_ret / max_dd, 2) if max_dd > 0 else 0.0

    return {
        "arm": cfg.name,
        "label": cfg.label,
        "sweep_mode": cfg.sweep_mode,
        "ending_equity": end_eq,
        "net_return_pct": net_ret,
        "max_drawdown_pct": max_dd,
        "calmar": calmar,
        "summary": summarize_extended(closed_trades),
        "sweep_only": summarize_sweep_only(closed_trades),
        "by_setup": summarize_by_setup(closed_trades),
        "by_regime": summarize_by_regime(closed_trades),
        "vwap_be_trigger_count": vwap_be_trigger_count,
        "blocked_knife_count": len(blocked_knife_events),
        "blocked_knife_events": blocked_knife_events,
        "trades": closed_trades,
    }


def summarize_extended(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "count": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
            "pf": 0.0, "net_pnl_usd": 0.0, "avg_ret_pct": 0.0,
            "avg_r": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "avg_pnl_usd": 0.0, "sl_hits": 0, "be_stops": 0,
        }
    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] < 0]
    gp = sum(t["pnl_usd"] for t in wins)
    gl = abs(sum(t["pnl_usd"] for t in losses))
    pf = round(gp / gl, 2) if gl > 0 else (99.0 if gp > 0 else 0.0)
    return {
        "count": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / n * 100.0, 1),
        "pf": pf,
        "net_pnl_usd": round(sum(t["pnl_usd"] for t in trades), 2),
        "avg_ret_pct": round(sum(t["net_return_pct"] for t in trades) / n, 2),
        "avg_r": round(sum(t.get("r_multiple", 0.0) for t in trades) / n, 2),
        "avg_win_pct": round(sum(t["net_return_pct"] for t in wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_pct": round(sum(t["net_return_pct"] for t in losses) / len(losses), 2) if losses else 0.0,
        "avg_pnl_usd": round(sum(t["pnl_usd"] for t in trades) / n, 2),
        "sl_hits": sum(1 for t in trades if t["exit_reason"] == "SL_HIT"),
        "be_stops": sum(1 for t in trades if t["exit_reason"] == "BE_STOP"),
    }


def summarize_sweep_only(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    sw = [t for t in trades if t.get("setup") == "SWEEP_RECLAIM_LONG"]
    base = summarize_extended(sw)
    base["underwater_entries"] = sum(1 for t in sw if t.get("entered_underwater"))
    base["vwap_be_triggered"] = sum(1 for t in sw if t.get("vwap_be_triggered"))
    base["addon_stage2"] = sum(1 for t in sw if int(t.get("stage", 1)) >= 2)
    base["avg_init_risk_pct"] = round(sum(t.get("init_risk_pct", 0.0) for t in sw) / len(sw), 2) if sw else 0.0
    return base


def summarize_by_setup(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for st in ("SWEEP_RECLAIM_LONG", "BREAKOUT_MOMENTUM_LONG", "REBOUND_REJECT_SHORT"):
        out[st] = summarize_extended([t for t in trades if t.get("setup") == st])
    return out


def summarize_by_regime(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for reg in ("BEAR_DOWN", "CHOP_FADE", "UP_REBOUND"):
        all_r = [t for t in trades if t.get("entry_day_regime") == reg]
        sw_r = [t for t in all_r if t.get("setup") == "SWEEP_RECLAIM_LONG"]
        out[reg] = {
            "all": summarize_extended(all_r),
            "sweep_only": summarize_extended(sw_r),
        }
    return out


def find_case_studies(res_a: Dict[str, Any], res_b: Dict[str, Any], res_c: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """挑选典型案例：
    1) A 组错过、B/C 组提前吃肉的经典抄底/扫损收回盈利单（3 笔）
    2) B 组盲目接飞刀亏钱、C 组靠门禁避开或靠 VWAP-BE 逃生的案例（3 笔）
    """
    a_sw_keys = {(t["entry_date"], t["ticker"]) for t in res_a["trades"] if t["setup"] == "SWEEP_RECLAIM_LONG"}
    c_sw_by_exact = {(t["entry_time_et"], t["ticker"]): t for t in res_c["trades"] if t["setup"] == "SWEEP_RECLAIM_LONG"}
    c_blk_by_exact = {(e["time_et"], e["ticker"]): e for e in res_c.get("blocked_knife_events", [])}
    c_blk_by_dt = {(e["date"], e["ticker"]): e for e in res_c.get("blocked_knife_events", [])}

    # 类型 1: A 组没买到的水下 SWEEP_RECLAIM_LONG，在 B/C 组大赚
    winners_missed_by_a = []
    for t in sorted(res_c["trades"], key=lambda x: -x["pnl_usd"]):
        if t["setup"] != "SWEEP_RECLAIM_LONG" or t["pnl_usd"] <= 0:
            continue
        if (t["entry_date"], t["ticker"]) not in a_sw_keys and t.get("entry_vwap_diff_pct", 0.0) < -0.20:
            winners_missed_by_a.append(t)
    # 若水下大赚不足 3 笔，补入任意 A 未触发而 C/B 盈利的 SWEEP 单
    if len(winners_missed_by_a) < 3:
        seen = {(t["entry_date"], t["ticker"], t["entry_time_et"]) for t in winners_missed_by_a}
        for src in (res_c["trades"], res_b["trades"]):
            for t in sorted(src, key=lambda x: -x["pnl_usd"]):
                k = (t["entry_date"], t["ticker"], t["entry_time_et"])
                if t["setup"] == "SWEEP_RECLAIM_LONG" and t["pnl_usd"] > 0 and (t["entry_date"], t["ticker"]) not in a_sw_keys and k not in seen:
                    winners_missed_by_a.append(t)
                    seen.add(k)
                    if len(winners_missed_by_a) >= 3:
                        break
            if len(winners_missed_by_a) >= 3:
                break

    # 类型 2: B 组水下接飞刀亏损，而 C 组被门禁拦截 OR 靠 VWAP-BE 明显少亏/转盈（严格按精确入场时间戳+标的对齐）
    b_traps_c_saved = []
    for tb in sorted(res_b["trades"], key=lambda x: x["pnl_usd"]):
        if tb["setup"] != "SWEEP_RECLAIM_LONG" or tb["pnl_usd"] >= 0:
            continue
        exact_k = (tb["entry_time_et"], tb["ticker"])
        day_k = (tb["entry_date"], tb["ticker"])
        if (exact_k in c_blk_by_exact or day_k in c_blk_by_dt) and exact_k not in c_sw_by_exact:
            blk = c_blk_by_exact.get(exact_k) or c_blk_by_dt[day_k]
            b_traps_c_saved.append({
                "save_type": f"C组门禁拦截 ({blk['block_reason']})",
                "b_trade": tb,
                "c_trade": None,
                "c_block": blk,
                "delta_pnl_usd": round(-tb["pnl_usd"], 2),
            })
        elif exact_k in c_sw_by_exact:
            tc = c_sw_by_exact[exact_k]
            if tc["pnl_usd"] > tb["pnl_usd"] + 1.0 and tc.get("vwap_be_triggered"):
                b_traps_c_saved.append({
                    "save_type": "C组VWAP-BE锁盈/保本逃生",
                    "b_trade": tb,
                    "c_trade": tc,
                    "c_block": None,
                    "delta_pnl_usd": round(tc["pnl_usd"] - tb["pnl_usd"], 2),
                })

    # 若不足 3 笔，补入同时间戳 C 组未接（因前序单持仓保护/未触发）的 B 组止损单
    if len(b_traps_c_saved) < 3:
        seen_b = {(x["b_trade"]["entry_time_et"], x["b_trade"]["ticker"]) for x in b_traps_c_saved}
        for tb in sorted(res_b["trades"], key=lambda x: x["pnl_usd"]):
            if tb["setup"] != "SWEEP_RECLAIM_LONG" or tb["pnl_usd"] >= 0:
                continue
            exact_k = (tb["entry_time_et"], tb["ticker"])
            if exact_k in seen_b:
                continue
            tc = c_sw_by_exact.get(exact_k)
            if tc is None:
                b_traps_c_saved.append({
                    "save_type": "C组未接该笔（前序仓位持有/槽位过滤）",
                    "b_trade": tb,
                    "c_trade": None,
                    "c_block": None,
                    "delta_pnl_usd": round(-tb["pnl_usd"], 2),
                })
                seen_b.add(exact_k)
            if len(b_traps_c_saved) >= 3:
                break

    b_traps_c_saved.sort(key=lambda x: -x["delta_pnl_usd"])
    return {
        "winners_missed_by_a": winners_missed_by_a[:3],
        "b_traps_c_saved": b_traps_c_saved[:3],
    }


def compute_ex_top1_sweep_stress(res_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """防单笔运气校验：分别剔除每组 SWEEP_RECLAIM_LONG 盈利最大的单笔交易后，重算 SWEEP 独立指标与全局净收益/PF。"""
    rows: List[Dict[str, Any]] = []
    for r in res_list:
        all_tr = list(r["trades"])
        sw_tr = [t for t in all_tr if t.get("setup") == "SWEEP_RECLAIM_LONG"]
        if sw_tr:
            top1 = max(sw_tr, key=lambda x: x["pnl_usd"])
        else:
            top1 = None

        if top1 is not None:
            # 按对象精确剔除唯一 Top-1
            removed = False
            all_ex = []
            for t in all_tr:
                if (not removed) and t is top1:
                    removed = True
                    continue
                all_ex.append(t)
            sw_ex = [t for t in all_ex if t.get("setup") == "SWEEP_RECLAIM_LONG"]
            top1_label = f"{top1['ticker']} ({top1['entry_date']} {top1['entry_time_et'][11:16]}, ${top1['pnl_usd']:+.2f})"
            top1_pnl = round(top1["pnl_usd"], 2)
        else:
            all_ex = all_tr
            sw_ex = sw_tr
            top1_label = "无"
            top1_pnl = 0.0

        sw_s = summarize_extended(sw_ex)
        all_s = summarize_extended(all_ex)
        global_net_ret_ex = round(all_s["net_pnl_usd"] / bt55.INITIAL_CAPITAL * 100.0, 2)

        rows.append({
            "arm": r["arm"],
            "label": r["label"],
            "top1_removed": top1_label,
            "top1_pnl_usd": top1_pnl,
            "orig_sweep_pf": r["sweep_only"]["pf"],
            "orig_sweep_pnl_usd": r["sweep_only"]["net_pnl_usd"],
            "ex_sweep_count": sw_s["count"],
            "ex_sweep_wr_pct": sw_s["win_rate_pct"],
            "ex_sweep_pf": sw_s["pf"],
            "ex_sweep_pnl_usd": sw_s["net_pnl_usd"],
            "orig_global_net_ret_pct": r["net_return_pct"],
            "orig_global_pf": r["summary"]["pf"],
            "ex_global_pf": all_s["pf"],
            "ex_global_pnl_usd": all_s["net_pnl_usd"],
            "ex_global_net_ret_pct": global_net_ret_ex,
        })
    return rows


def render_markdown_report(
    valid_dates: List[date],
    day_regimes: Dict[str, Dict[str, Any]],
    results_by_slip: Dict[str, List[Dict[str, Any]]],
    ex_top1_25: List[Dict[str, Any]],
    cases_25: Dict[str, List[Dict[str, Any]]],
    cases_3: Dict[str, List[Dict[str, Any]]],
) -> str:
    reg_counts = {"BEAR_DOWN": 0, "CHOP_FADE": 0, "UP_REBOUND": 0}
    for v in day_regimes.values():
        reg_counts[v["regime"]] = reg_counts.get(v["regime"], 0) + 1

    lines: List[str] = []
    lines.append("# V5.7 KOL 多头 `SWEEP_RECLAIM_LONG` 松绑 VWAP 60 日 A/B/C/C+ 对照回测报告")
    lines.append("")
    lines.append(f"- **回测区间**: `{valid_dates[0]}` ~ `{valid_dates[-1]}`（共 `{len(valid_dates)}` 个有效 RTH 交易日；单边下跌日 `{reg_counts['BEAR_DOWN']}` 天 / 高开低走与震荡日 `{reg_counts['CHOP_FADE']}` 天 / 上涨反抽日 `{reg_counts['UP_REBOUND']}` 天）")
    lines.append("- **控制变量承诺**: 四组共享 100% 相同的 2 槽×50%（30%+20%）、`$1,000` 资金、09:45~15:20 ET 正盘窗、7 只核心 ETF 52%/6% 门槛、禁空反向 ETF、Fast-BE26、`BREAKOUT_MOMENTUM_LONG` 与全部 `SHORT` 逻辑、T+2/T+3 规则与防未来函数 5m K 线管线；**仅**变动 `SWEEP_RECLAIM_LONG` 入场/止损/门禁/VWAP推保本。")
    lines.append("")

    # 表 1: 全局表现总表
    lines.append("## 1. 全局表现总表（A vs B vs C vs C+，双滑点 `3 bps` & `25 bps`）")
    lines.append("")
    lines.append("| 滑点口径 | 实验组 | 总交易笔数 | 净收益率 | 胜率 | 盈亏比 (PF) | 均笔 R 倍数 | 均笔收益率 | 最大回撤 (MaxDD) | Calmar (`Net/MaxDD`) | 期末净值 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for slip_key in ("3bps", "25bps"):
        for r in results_by_slip[slip_key]:
            s = r["summary"]
            lines.append(
                f"| `{slip_key}` | **{r['arm']}** ({r['label']}) | {s['count']} | **{r['net_return_pct']:+.2f}%** | {s['win_rate_pct']:.1f}% | **{s['pf']:.2f}** | {s['avg_r']:+.2f}R | {s['avg_ret_pct']:+.2f}% | -{r['max_drawdown_pct']:.2f}% | {r['calmar']:.2f} | ${r['ending_equity']:.2f} |"
            )
    lines.append("")

    # 表 2: SWEEP_RECLAIM_LONG 单独归因表
    lines.append("## 2. `SWEEP_RECLAIM_LONG` 单独归因表（A vs B vs C vs C+，剔除突破多与空头干扰）")
    lines.append("")
    lines.append("| 滑点口径 | 实验组 | 触发笔数 (水下笔数) | 胜率 | 均笔盈利 | 均笔亏损 | 单独 PF | 单独净盈亏 ($) | 初始平均止损宽度 | 触发 SL 次数 | 触发 VWAP-BE 次数 | 站稳 VWAP 加仓次数 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for slip_key in ("3bps", "25bps"):
        for r in results_by_slip[slip_key]:
            sw = r["sweep_only"]
            lines.append(
                f"| `{slip_key}` | **{r['arm']}** | {sw['count']} ({sw['underwater_entries']}) | {sw['win_rate_pct']:.1f}% | {sw['avg_win_pct']:+.2f}% | {sw['avg_loss_pct']:+.2f}% | **{sw['pf']:.2f}** | **${sw['net_pnl_usd']:+.2f}** | {sw['avg_init_risk_pct']:.2f}% | {sw['sl_hits']} | {sw['vwap_be_triggered']} | {sw['addon_stage2']} |"
            )
    lines.append("")

    # 补充分 Setup 总览 (25 bps) 核对控制变量
    lines.append("### 附：分 Setup 净盈亏拆解（`25 bps` 压力口径，验证非实验分支影响）")
    lines.append("")
    lines.append("| 实验组 | `SWEEP_RECLAIM_LONG` (笔数 / WR / 盈亏) | `BREAKOUT_MOMENTUM_LONG` (笔数 / WR / 盈亏) | `REBOUND_REJECT_SHORT` (笔数 / WR / 盈亏) | 飞刀门禁拦截次数 |")
    lines.append("|---|---|---|---|---:|")
    for r in results_by_slip["25bps"]:
        bs = r["by_setup"]
        sw = bs["SWEEP_RECLAIM_LONG"]
        br = bs["BREAKOUT_MOMENTUM_LONG"]
        sh = bs["REBOUND_REJECT_SHORT"]
        lines.append(
            f"| **{r['arm']}** | {sw['count']} 笔 / {sw['win_rate_pct']:.1f}% / ${sw['net_pnl_usd']:+.2f} | {br['count']} 笔 / {br['win_rate_pct']:.1f}% / ${br['net_pnl_usd']:+.2f} | {sh['count']} 笔 / {sh['win_rate_pct']:.1f}% / ${sh['net_pnl_usd']:+.2f} | {r['blocked_knife_count']} |"
        )
    lines.append("")

    # 表 3: 分市场环境压力测试
    lines.append("## 3. 分市场环境压力测试（按 QQQ 当日涨跌幅切分，含 `25 bps` 与 `3 bps`）")
    lines.append("")
    reg_labels = {
        "BEAR_DOWN": f"单边下跌/熊市日 (`QQQ <= -1.0%`, {reg_counts['BEAR_DOWN']}天)",
        "CHOP_FADE": f"高开低走/震荡日 ({reg_counts['CHOP_FADE']}天)",
        "UP_REBOUND": f"上涨/反抽日 (`QQQ >= +0.6%`, {reg_counts['UP_REBOUND']}天)",
    }
    for slip_key in ("25bps", "3bps"):
        lines.append(f"### 滑点口径：`{slip_key}`")
        lines.append("")
        lines.append("| 市场环境 | 实验组 | 全局笔数 / 胜率 / 净盈亏 ($) | `SWEEP_RECLAIM_LONG` 笔数 | `SWEEP` 胜率 | `SWEEP` 均笔收益 | `SWEEP` 净盈亏 ($) | `SWEEP` 止损次数 |")
        lines.append("|---|---|---|---:|---:|---:|---:|---:|")
        for rk in ("BEAR_DOWN", "CHOP_FADE", "UP_REBOUND"):
            for r in results_by_slip[slip_key]:
                ra = r["by_regime"][rk]["all"]
                rs = r["by_regime"][rk]["sweep_only"]
                lines.append(
                    f"| {reg_labels[rk]} | **{r['arm']}** | {ra['count']} 笔 / {ra['win_rate_pct']:.1f}% / ${ra['net_pnl_usd']:+.2f} | {rs['count']} | {rs['win_rate_pct']:.1f}% | {rs['avg_ret_pct']:+.2f}% | **${rs['net_pnl_usd']:+.2f}** | {rs['sl_hits']} |"
                )
        lines.append("")

    # 表 4: 防单笔运气校验（25 bps 剔除 Top-1 盈利单压力测试）
    lines.append("## 4. 防单笔运气校验（`25 bps` 剔除 `SWEEP` Top-1 盈利单压力测试）")
    lines.append("")
    lines.append("| 实验组 | 剔除的 `SWEEP` Top-1 盈利单 | 剔除前 `SWEEP` PF / 净盈亏 | **剔除后 `SWEEP` 笔数 / WR / PF** | **剔除后 `SWEEP` 净盈亏 ($)** | 剔除前全局净收益 / PF | **剔除后全局净收益 / PF** |")
    lines.append("|---|---|---|---:|---:|---|---|")
    for row in ex_top1_25:
        lines.append(
            f"| **{row['arm']}** | `{row['top1_removed']}` | {row['orig_sweep_pf']:.2f} / ${row['orig_sweep_pnl_usd']:+.2f} | {row['ex_sweep_count']} 笔 / {row['ex_sweep_wr_pct']:.1f}% / **{row['ex_sweep_pf']:.2f}** | **${row['ex_sweep_pnl_usd']:+.2f}** | {row['orig_global_net_ret_pct']:+.2f}% / {row['orig_global_pf']:.2f} | **{row['ex_global_net_ret_pct']:+.2f}%** / **{row['ex_global_pf']:.2f}** |"
        )
    lines.append("")

    # 附表 5: 典型交易案例对比 (25 bps 严格实盘口径)
    lines.append("## 5. 典型交易案例对比（`25 bps` 压力口径实盘回放）")
    lines.append("")
    lines.append("### 5.1 A 组因 VWAP 硬锁错过、B/C/C+ 组提前吃肉的经典抄底盈利单")
    lines.append("")
    lines.append("| 标的 | 入场时间 (ET) | 扫损锚点 | 入场价 | 当时 VWAP (偏离) | 窄止损价 (风险宽) | 平仓时间 / 原因 | 平仓价 | 净盈亏 ($) / 收益率 | 触发 VWAP-BE |")
    lines.append("|---|---|---|---:|---:|---:|---|---:|---:|---|")
    for t in cases_25["winners_missed_by_a"]:
        vbe = f"是 (@{t['vwap_be_price']})" if t.get("vwap_be_triggered") else "否"
        lines.append(
            f"| **{t['ticker']}** | `{t['entry_time_et']}` | `{t['sweep_trigger']}` | ${t['orig_entry_price']:.2f} | ${t['entry_vwap']:.2f} (`{t['entry_vwap_diff_pct']:+.2f}%`) | ${t['init_sl_price']:.2f} (`-{t['init_risk_pct']:.2f}%`) | `{t['exit_time_et']}` (`{t['exit_reason']}`) | ${t['exit_price']:.2f} | **${t['pnl_usd']:+.2f}** (`{t['net_return_pct']:+.2f}%`) | {vbe} |"
        )
    lines.append("")

    lines.append("### 5.2 B 组水下接飞刀止损、C+ 组靠门禁拦截或纯 VWAP-BE 逃生案例")
    lines.append("")
    lines.append("| 标的 | B 组入场时间 (ET) | 扫损锚点 | B 组入场价 | 当时 VWAP (偏离) | B 组止损价 -> 平仓价 (原因) | B 组盈亏 ($) | C+ 组处置方式与结果 | C+ 相较 B 组净改善 |")
    lines.append("|---|---|---|---:|---:|---|---:|---|---:|")
    for item in cases_25["b_traps_c_saved"]:
        tb = item["b_trade"]
        tc = item["c_trade"]
        if tc is None:
            c_desc = f"**{item['save_type']}**（0 仓位，成功避坑）"
        else:
            c_desc = f"**{item['save_type']}**：平仓 `${tc['exit_price']:.2f}` (`{tc['exit_reason']}`)，盈亏 `${tc['pnl_usd']:+.2f}` (`{tc['net_return_pct']:+.2f}%`)"
        lines.append(
            f"| **{tb['ticker']}** | `{tb['entry_time_et']}` | `{tb['sweep_trigger']}` | ${tb['orig_entry_price']:.2f} | ${tb['entry_vwap']:.2f} (`{tb['entry_vwap_diff_pct']:+.2f}%`) | ${tb['init_sl_price']:.2f} -> ${tb['exit_price']:.2f} (`{tb['exit_reason']}`) | **${tb['pnl_usd']:+.2f}** (`{tb['net_return_pct']:+.2f}%`) | {c_desc} | **+${item['delta_pnl_usd']:.2f}** |"
        )
    lines.append("")

    return "\n".join(lines)


def main():
    print("[INFO] Loading local 5m parquet & causal daily context (strictly read-only)...")
    df5 = pd.read_parquet(bt55.PARQUET_5M)
    daily_df = bt55.load_daily_panels()
    dt_s = pd.to_datetime(df5["Datetime"])
    hhmm = dt_s.dt.hour * 60 + dt_s.dt.minute
    rth_dates = df5[(hhmm >= 9 * 60 + 30) & (hhmm <= 15 * 60 + 55)].groupby(dt_s.dt.date)["Datetime"].nunique()
    cand_dates = sorted([d for d, cnt in rth_dates.items() if cnt >= 20])
    macro_ctx, daily_ctx = bt55.precompute_causal_daily_context(daily_df, cand_dates)
    v57.enrich_macro_with_raw_temp(macro_ctx, daily_ctx)
    valid_dates, day_timestamps, bars_by_day_tk, pm_bounds = bt55.build_causal_5m_market_replay(df5, daily_ctx)
    day_regimes = classify_market_regimes(valid_dates, day_timestamps, bars_by_day_tk, daily_ctx)

    arms = [
        SweepABConfig(
            name="A_BASELINE",
            label="现役 V5.7 基线 (硬卡 VWAP>=-0.20%)",
            sweep_mode="A_BASELINE",
        ),
        SweepABConfig(
            name="B_RELAX_VWAP",
            label="纯松绑 VWAP + 针尖窄损",
            sweep_mode="B_RELAX_VWAP",
        ),
        SweepABConfig(
            name="C_KOL_VWAP_BE",
            label="KOL 狙击 (水下门禁 + VWAP平35%推BE)",
            sweep_mode="C_KOL_VWAP_BE",
        ),
        SweepABConfig(
            name="C_PLUS_PURE_BE",
            label="C+ 纯推BE (全量非避险QQQ_FLUSH门禁 + 0%减仓纯推BE)",
            sweep_mode="C_PLUS_PURE_BE",
        ),
    ]

    slip_list = [("3bps", 0.0003), ("25bps", 0.0025)]
    results_by_slip: Dict[str, List[Dict[str, Any]]] = {}

    for slip_key, rt_slip in slip_list:
        print(f"[INFO] Running A/B/C/C+ arms under slippage={slip_key} ({rt_slip*10000:.1f} bps)...")
        res_list = []
        for cfg in arms:
            r = run_sweep_arm(cfg, rt_slip, valid_dates, day_timestamps, bars_by_day_tk, pm_bounds, macro_ctx, daily_ctx, day_regimes)
            res_list.append(r)
            s = r["summary"]
            sw = r["sweep_only"]
            print(
                f"  [{slip_key:5s}] {r['arm']:15s} | n={s['count']:3d} | Net={r['net_return_pct']:+6.2f}% | WR={s['win_rate_pct']:5.1f}% | PF={s['pf']:4.2f} | MaxDD=-{r['max_drawdown_pct']:5.2f}% | Calmar={r['calmar']:4.2f} | SWEEP: n={sw['count']:2d} (uw={sw['underwater_entries']:2d}) WR={sw['win_rate_pct']:5.1f}% PF={sw['pf']:4.2f} PnL=${sw['net_pnl_usd']:+7.2f} SL={sw['sl_hits']:2d} VBE={sw['vwap_be_triggered']:2d}"
            )
        results_by_slip[slip_key] = res_list

    ex_top1_25 = compute_ex_top1_sweep_stress(results_by_slip["25bps"])
    print("\n[INFO] Top-1 SWEEP Outlier Removal Stress Test (25 bps):")
    for row in ex_top1_25:
        print(
            f"  {row['arm']:15s} | drop={row['top1_removed']:36s} | SWEEP ex-Top1: n={row['ex_sweep_count']:2d} WR={row['ex_sweep_wr_pct']:5.1f}% PF={row['ex_sweep_pf']:4.2f} PnL=${row['ex_sweep_pnl_usd']:+7.2f} | Global ex-Top1: {row['ex_global_net_ret_pct']:+6.2f}% PF={row['ex_global_pf']:4.2f}"
        )

    cases_25 = find_case_studies(results_by_slip["25bps"][0], results_by_slip["25bps"][1], results_by_slip["25bps"][3])
    cases_3 = find_case_studies(results_by_slip["3bps"][0], results_by_slip["3bps"][1], results_by_slip["3bps"][3])

    os.makedirs(REPORTS_DIR, exist_ok=True)
    md_text = render_markdown_report(valid_dates, day_regimes, results_by_slip, ex_top1_25, cases_25, cases_3)
    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write(md_text)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "date_range": [valid_dates[0].isoformat(), valid_dates[-1].isoformat()],
        "trading_days": len(valid_dates),
        "day_regimes": day_regimes,
        "results": results_by_slip,
        "ex_top1_sweep_25bps": ex_top1_25,
        "case_studies_25bps": cases_25,
        "case_studies_3bps": cases_3,
    }
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n[DONE] Saved markdown report: {REPORT_MD}")
    print(f"[DONE] Saved json report:     {REPORT_JSON}")


if __name__ == "__main__":
    main()
