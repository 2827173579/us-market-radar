# -*- coding: utf-8 -*-
"""
V5.7 「做减法」精简对照组专项回测 (run_v57_slim_ablation.py)
严格以现役 V55_D_Hybrid 的高质选股与 RTH 09:45~15:20 纪律为底座，
逐一增量叠加核心有效升级模块，在 9-10 月震荡市与 11 月暴跌反抽双时段做严格防穿越检验（25 bps & 3 bps）。
绝不触碰任何生产代码或账本。
"""

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

import backtest_v55_kol_60d as bt55
import copy_trade_engine as cte
import copy_trade_kol_engine as kol
import run_v57_backtest as v57

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
REPORT_SLIM_JSON = os.path.join(REPORTS_DIR, "v57_slim_ablation_60d.json")
REPORT_SLIM_MD = os.path.join(REPORTS_DIR, "v57_slim_ablation_60d.md")

SLIM_CORE_ETFS = frozenset({"SOXX", "SOXL", "SOXS", "QQQ", "TQQQ", "SQQQ", "KSTR"})
SLIM_NO_SHORT_INVERSE = frozenset({"SOXS", "SQQQ", "VIXY"})
SLIM_PARENT_MAP = {"SOXL": "SOXX", "SOXS": "SOXX", "TQQQ": "QQQ", "SQQQ": "QQQ"}


@dataclass
class SlimConfig:
    name: str
    label: str
    max_positions: int = 2
    starter_weight_1x: float = 0.30
    addon_weight_1x: float = 0.20
    starter_weight_3x: float = 0.30
    addon_weight_3x: float = 0.20
    # 沿用 V55_D_Hybrid 止损与一阶止盈底座
    sl_min_1x: float = 0.020
    sl_max_1x: float = 0.035
    sl_min_3x: float = 0.025
    sl_max_3x: float = 0.045
    addon_sl_buffer: float = 0.01
    weak_exit_min_days: int = 2
    partial_1x_pct: float = 4.0
    partial_3x_pct: float = 6.5
    # 增量升级开关（完全正交单变量拆分）
    use_etf_52: bool = False          # 7只核心ETF放宽至 52%/6%
    no_short_inv: bool = False        # 严禁做空反向/波动率ETF (SOXS/SQQQ/VIXY)
    use_fast_be: bool = False         # 快推保本 (先平25%锁现金 + SL推+0.3%)
    fast_be_1x_pct: float = 2.6
    fast_be_3x_pct: float = 4.0
    fast_be_trim_frac: float = 0.25
    use_corridor_def: bool = False    # 走廊盈利防守 (浮盈>=1.2% + 连续2根反向破走廊 + 失守VWAP)
    use_swap: bool = False            # 动态换仓 (持仓>=4h/跨日 + 弱滞[-1.8%,-0.2%] + 失守VWAP + 得分领先>=10 + <=1/日)
    swap_score_gap: float = 10.0
    use_raw_temp_bounce: bool = False # 纯未截断 raw_macro_bottom_score>=45 右侧反抽 (不依赖 vix_hard_brake)


def _resolve_sl_slim(cfg: SlimConfig, tk: str, direction: str, entry_px: float, day_low: float, day_high: float, pm_low: float, pm_high: float, s1: float, r1: float) -> float:
    """与 V55_D_Hybrid 完全一致的结构止损口径 ([2.0%, 3.5%] 1x / [2.5%, 4.5%] 3x)。"""
    lev3 = kol.is_3x_leveraged(tk)
    sl_min = cfg.sl_min_3x if lev3 else cfg.sl_min_1x
    sl_max = cfg.sl_max_3x if lev3 else cfg.sl_max_1x
    pad = 0.010 if lev3 else 0.005
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


def run_slim(
    cfg: SlimConfig,
    rt_slippage: float,
    valid_dates: List[date],
    day_timestamps: Dict[date, List[datetime]],
    bars_by_day_tk: Dict[Tuple[date, str], Dict[datetime, Dict[str, float]]],
    corridor_bands: Dict[Tuple[date, str], Dict[int, Tuple[float, float]]],
    pm_bounds: Dict[Tuple[date, str], Dict[str, float]],
    macro_ctx: Dict[date, Dict[str, Any]],
    daily_ctx: Dict[date, Dict[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    cash = bt55.INITIAL_CAPITAL
    peak_equity = bt55.INITIAL_CAPITAL
    max_dd = 0.0
    open_positions: List[Dict[str, Any]] = []
    closed_trades: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []

    last_close_dt: Optional[datetime] = None
    last_exit_by_tk: Dict[str, Dict[str, Any]] = {}
    corridor_def_triggers = 0
    swap_count = 0
    temp_bounce_entries = 0

    for d_idx, d_cur in enumerate(valid_dates):
        d_str = d_cur.isoformat()
        m_info = macro_ctx.get(d_cur) or {}
        d_tks = daily_ctx.get(d_cur) or {}
        regime = str(m_info.get("market_regime") or "NEUTRAL_CHOP")
        vix_brake = bool(m_info.get("vix_hard_brake", False))
        panic_clear = bool(m_info.get("panic_clearing_active", False))
        raw_mb_score = float(m_info.get("raw_macro_bottom_score", 0.0))
        long_blocked = bool((regime == "BEAR_DEFENSE" or vix_brake) and (not panic_clear))

        entries_today = 0
        swaps_today = 0
        max_entries_day = max(kol.MAX_NEW_ENTRIES_PER_DAY, cfg.max_positions + 2)
        ts_list = day_timestamps.get(d_cur) or []

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

                # 触发用盘中真实毛涨跌幅，实际平仓/落袋 100% 扣 rt_slippage
                gross_ret_pct = (((c_px - entry_px) / entry_px) if direction == "LONG" else ((entry_px - c_px) / entry_px)) * 100.0
                ret_c = bt55._net_ret(direction, entry_px, c_px, rt_slippage)
                pos["max_favorable_return_pct"] = max(pos["max_favorable_return_pct"], gross_ret_pct)
                if direction == "LONG":
                    pos["best_price"] = max(pos["best_price"], c_px)
                else:
                    pos["best_price"] = min(pos["best_price"], c_px)

                # (1b) 金字塔顺势加仓（与 V55_D_Hybrid 一致，优先于 Fast-BE 触发，绝不阻断 S2 主升浪加仓）
                if (not exit_reason) and pos["stage"] == 1 and pos["can_add"] and (not partial_done):
                    add_trig = kol.ADDON_TRIGGER_3X_PCT if lev3 else kol.ADDON_TRIGGER_1X_PCT
                    on_vwap = (vwap_diff >= 0.0) if direction == "LONG" else (vwap_diff <= 0.0)
                    if gross_ret_pct >= add_trig and on_vwap:
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

                # (1c) 可选升级：Neo Lee 快推保本 (先落袋 25% 现金 + 止损推 +0.3%，防 2~3% 浮盈倒亏)
                if (not exit_reason) and cfg.use_fast_be and (not be_active) and (not partial_done):
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

                # (1d) 可选升级：走廊盈利防守 (仅已有浮盈 >= +1.2% 且连续 2 根反向破走廊 + 失守 VWAP)
                if (not exit_reason) and cfg.use_corridor_def and (not pos.get("corridor_def_done")):
                    tk_atr = float((d_tks.get(tk) or {}).get("atr_pct", 3.0))
                    c_self = v57._corridor_status(d_cur, tk, ts, b, tk_atr, corridor_bands)
                    parent_sym = SLIM_PARENT_MAP.get(tk)
                    c_parent = ""
                    if parent_sym:
                        pb = (bars_by_day_tk.get((d_cur, parent_sym)) or {}).get(ts)
                        p_atr = float((d_tks.get(parent_sym) or {}).get("atr_pct", 1.8))
                        if pb:
                            c_parent = v57._corridor_status(d_cur, parent_sym, ts, pb, p_atr, corridor_bands)
                            if tk in cte.INVERSE_ETFS:
                                c_parent = "BELOW_P25" if c_parent == "ABOVE_P75" else ("ABOVE_P75" if c_parent == "BELOW_P25" else c_parent)
                    opp_breach = (
                        (direction == "SHORT" and (c_self == "ABOVE_P75" or c_parent == "ABOVE_P75"))
                        or (direction == "LONG" and (c_self == "BELOW_P25" or c_parent == "BELOW_P25"))
                    )
                    lost_vw = (vwap_diff < -0.15) if direction == "LONG" else (vwap_diff > 0.15)
                    pos["cor_streak"] = (pos.get("cor_streak", 0) + 1) if (opp_breach and lost_vw) else 0
                    if pos["cor_streak"] >= 2 and gross_ret_pct >= 1.20:
                        pos["corridor_def_done"] = True
                        pos["corridor_def_triggered"] = True
                        corridor_def_triggers += 1
                        if not partial_done:
                            t_f = 0.333 if pos.get("fast_be_trimmed") else 0.50
                            close_alloc = round(alloc * t_f, 2)
                            rem_alloc = round(alloc - close_alloc, 2)
                            part_pnl = round(close_alloc * ret_c, 2)
                            cash = round(cash + max(0.0, close_alloc + part_pnl), 2)
                            pos["allocated_capital"] = rem_alloc
                            pos["slot_weight"] = round(pos["slot_weight"] * (1.0 - t_f), 3)
                            pos["partial_tp_done"] = True
                            pos["can_add"] = False
                            pos["partial_closed_capital"] = round(pos["partial_closed_capital"] + close_alloc, 2)
                            pos["partial_realized_pnl_usd"] = round(pos["partial_realized_pnl_usd"] + part_pnl, 2)
                            partial_done = True
                            alloc = rem_alloc
                        pos["be_stop_active"] = True
                        pos["trail_mult"] = min(pos.get("trail_mult", 1.0), 0.65)
                        be_active = True
                        if direction == "LONG":
                            pos["sl_price"] = max(sl_px, round(entry_px * (1.0 + kol.BE_STOP_BUFFER), 2))
                        else:
                            pos["sl_price"] = min(sl_px, round(entry_px * (1.0 - kol.BE_STOP_BUFFER), 2))
                        sl_px = pos["sl_price"]

                # (1e) 一阶分批落袋 (+4.0% 1x / +6.5% 3x，与 V55_D 完全一致；若已 Fast-BE 减过 25%，此处仅再减余仓 1/3 -> 永远保留 50% 完整尾仓!)
                if (not exit_reason) and (not partial_done):
                    p_trig = cfg.partial_3x_pct if lev3 else cfg.partial_1x_pct
                    if gross_ret_pct >= p_trig:
                        t_frac = 0.333 if pos.get("fast_be_trimmed") else 0.50
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
                        trail_dd = (kol.TRAIL_DRAWDOWN_3X_PCT if lev3 else kol.TRAIL_DRAWDOWN_1X_PCT) * pos.get("trail_mult", 1.0)
                        bp = pos["best_price"]
                        dd_best = ((bp - c_px) / bp * 100.0) if direction == "LONG" else ((c_px - bp) / bp * 100.0)
                        if dd_best >= trail_dd:
                            exit_reason = "CORRIDOR_TRAIL_TP" if pos.get("corridor_def_triggered") else "TRAIL_TP"
                            exit_fill_px = c_px

                # (1g) T+2 早撤体检 + T+3 到期（与 V55_D 一致）
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
                    outcome = ("T3_WIN" if tot_pnl > 0 else "T3_LOSS") if exit_reason == "T3_EXPIRE" else exit_reason
                    closed_trades.append({
                        "ticker": tk, "is_3x": lev3, "direction": direction,
                        "entry_tag": pos.get("entry_tag", "NORMAL"), "setup": pos.get("setup", "V55"),
                        "stage": pos.get("stage", 1), "swapped_in": bool(pos.get("swapped_in", False)),
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
                        "hold_hours": round((ts - pos["entry_dt"]).total_seconds() / 3600.0, 2),
                        "v54_tp_price": None,
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

            # 3) 开仓扫描（100% 沿用 V55_D_Hybrid 的 RTH 09:45~15:20 与高质选股排序）
            in_win = (kol.ENTRY_START_MIN <= et_min <= kol.ENTRY_END_MIN)
            slot_cd_ok = True
            if entries_today >= cfg.max_positions and last_close_dt is not None:
                slot_cd_ok = ((ts - last_close_dt).total_seconds() / 60.0) >= kol.SLOT_COOLDOWN_MINUTES

            has_free = len(open_positions) < cfg.max_positions
            can_swap = (not has_free) and cfg.use_swap and swaps_today < 1
            if in_win and (has_free or can_swap) and entries_today < max_entries_day and slot_cd_ok:
                qqq_b = (bars_by_day_tk.get((d_cur, "QQQ")) or {}).get(ts)
                qqq_pc = float((d_tks.get("QQQ") or {}).get("prev_close", 0.0))
                qqq_pct = ((qqq_b["close"] - qqq_pc) / qqq_pc * 100.0) if (qqq_b and qqq_pc > 0) else 0.0
                nq_vwap_ok = bool(qqq_b and qqq_b["vwap"] > 0 and qqq_b["close"] >= qqq_b["vwap"])

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
                    pc = info["prev_close"]
                    if pc <= 0:
                        continue
                    pct_chg = (px - pc) / pc * 100.0
                    gap_pct = ((b_cur["day_open"] - pc) / pc * 100.0) if b_cur["day_open"] > 0 else pct_chg
                    lev3 = kol.is_3x_leveraged(tk)
                    is_core_etf = cfg.use_etf_52 and (tk in SLIM_CORE_ETFS)
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

                    p_l = info["calibrated_win_rate_pct"]
                    p_s = info["calibrated_short_win_rate_pct"]
                    is_crash = info["is_crash_halt"]
                    is_deep_pb = info["is_deep_pullback"]
                    is_broken = info["is_broken_bearish"]

                    last_ex = last_exit_by_tk.get(tk)
                    elapsed_ex = ((ts - last_ex["exit_dt"]).total_seconds() / 60.0) if (last_ex and last_ex["exit_date"] == d_str) else 9999.0

                    def _slim_cd_ok(tdir: str) -> bool:
                        if not last_ex or last_ex["exit_date"] != d_str:
                            return True
                        if last_ex["direction"] == tdir:
                            return elapsed_ex >= kol.SLOT_COOLDOWN_MINUTES
                        # 继承 V55_D_Hybrid 同日止损/早撤后禁止反手报复开仓护栏 (allow_flip=False)
                        if last_ex["exit_reason"] in ("SL_HIT", "WEAK_EARLY_EXIT"):
                            return False
                        return elapsed_ex >= kol.SLOT_COOLDOWN_MINUTES

                    min_p = 52.0 if is_core_etf else 55.0
                    min_sp = 6.0 if is_core_etf else 10.0
                    pri = 2.0 if tk in leaders_set else (1.0 if lev3 else 0.0)
                    st_wt = cfg.starter_weight_3x if lev3 else cfg.starter_weight_1x

                    # A. LONG 通道（与 V55_D 完全同源）
                    if _slim_cd_ok("LONG") and gap_pct <= max_gap and (not is_crash):
                        norm_l = (not long_blocked) or (tk in cte.SAFE_HAVEN_LONG_EXEMPT)
                        # 纯未截断温度计右侧反抽（不依赖 vix_hard_brake！仅需 raw_mb_score >= 45 + QQQ 翻红 >=+0.5% 站稳 VWAP）
                        temp_bounce_ok = (
                            cfg.use_raw_temp_bounce
                            and long_blocked
                            and (tk not in cte.SAFE_HAVEN_LONG_EXEMPT)
                            and (raw_mb_score >= 45.0)
                            and nq_vwap_ok
                            and (qqq_pct >= 0.50)
                            and (tk in leaders_set or tk in ("QQQ", "SPY", "SOXX", "NVDA"))
                        )
                        if norm_l and p_l >= min_p and (p_l - p_s) >= min_sp:
                            swept = (
                                (s1 > 0 and ((day_lo <= s1 * 1.006 and px >= s1) or abs(px - s1) / s1 <= 0.012))
                                or (pm_lo > 0 and day_lo <= pm_lo * 1.005 and px >= pm_lo)
                                or is_deep_pb or corr_div == "BELOW_P25"
                            )
                            resil = (pct_chg >= qqq_pct - 0.5) or (tk in leaders_set)
                            sweep_ok = swept and (vwap_diff >= -0.20) and (rvol_5m >= 1.3) and resil
                            brk_ok = (vwap_diff >= 0.0) and ((r1 > 0 and px >= r1 * 0.998) or corr_div == "ABOVE_P75") and (rvol_5m >= 1.5) and (50.0 <= rsi_5m <= 76.0)
                            if sweep_ok or brk_ok:
                                sl_px = _resolve_sl_slim(cfg, tk, "LONG", px, day_lo, day_hi, pm_lo, pm_hi, s1, r1)
                                raw_cands.append({
                                    "ticker": tk, "direction": "LONG",
                                    "setup": "SWEEP_RECLAIM_LONG" if sweep_ok else "BREAKOUT_MOMENTUM_LONG",
                                    "entry_tag": "MAIN_AI_ETF" if is_core_etf else "NORMAL",
                                    "slot_weight": st_wt, "can_add": True, "temp_bounce": False,
                                    "score": p_l + (p_l - p_s) * 0.25 + pri + (2.0 if sweep_ok else 1.0),
                                    "prob": p_l, "entry_price": round(px, 2), "sl_price": sl_px, "mg": mg,
                                })
                        elif temp_bounce_ok and p_l >= 45.0 and vwap_diff >= 0.05 and rvol_5m >= 1.35 and (corr_div == "ABOVE_P75" or is_deep_pb):
                            sl_px = _resolve_sl_slim(cfg, tk, "LONG", px, day_lo, day_hi, pm_lo, pm_hi, s1, r1)
                            raw_cands.append({
                                "ticker": tk, "direction": "LONG", "setup": "TEMP_RAW_BOUNCE_LONG",
                                "entry_tag": "TEMP_BOUNCE", "slot_weight": round(st_wt * 0.50, 3),
                                "can_add": True, "temp_bounce": True,
                                "score": p_l + pri, "prob": p_l,
                                "entry_price": round(px, 2), "sl_price": sl_px, "mg": mg,
                            })

                    # B. SHORT 通道（与 V55_D 完全同源；开启 use_etf_tier 时严禁做空反向/波动率 ETF）
                    inv_surge = bool(tk in cte.INVERSE_ETFS and (gap_pct >= 8.0 or pct_chg >= 8.0 or info["rsi_14"] >= 68.0))
                    no_short_inv = cfg.no_short_inv and (tk in SLIM_NO_SHORT_INVERSE)
                    if _slim_cd_ok("SHORT") and (tk not in cte.NON_SHORTABLE_TICKERS) and (not no_short_inv) and (not inv_surge) and (-gap_pct) <= max_gap:
                        if p_s >= min_p and (p_s - p_l) >= min_sp:
                            b_ctx = is_broken or (regime == "BEAR_DEFENSE") or vix_brake or (pct_chg < 0) or (corr_div == "BELOW_P25")
                            near_rej = ((-1.5 <= vwap_diff <= 0.40) or (r1 > 0 and abs(px - r1) / r1 <= 0.012)) and (vwap_diff <= 0.25) and (rsi_5m <= 58.0)
                            if b_ctx and near_rej:
                                sl_px = _resolve_sl_slim(cfg, tk, "SHORT", px, day_lo, day_hi, pm_lo, pm_hi, s1, r1)
                                raw_cands.append({
                                    "ticker": tk, "direction": "SHORT", "setup": "REBOUND_REJECT_SHORT",
                                    "entry_tag": "MAIN_AI_ETF" if is_core_etf else "NORMAL",
                                    "slot_weight": st_wt, "can_add": True, "temp_bounce": False,
                                    "score": p_s + (p_s - p_l) * 0.25 + pri + (2.5 if long_blocked else 0.0),
                                    "prob": p_s, "entry_price": round(px, 2), "sl_price": sl_px, "mg": mg,
                                })

                raw_cands.sort(key=lambda c: (-c["score"], -c["prob"], c["ticker"]))
                used_t, used_g = set(held_tks), set(held_grps)
                exp_idx = min(len(valid_dates) - 1, d_idx + kol.HOLD_TRADING_DAYS)

                # (3a) 空槽开仓
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
                    if c.get("temp_bounce"):
                        temp_bounce_entries += 1
                    open_positions.append({
                        "ticker": tk, "direction": c["direction"], "setup": c["setup"],
                        "entry_tag": c["entry_tag"], "entry_score": c["score"],
                        "stage": 1, "can_add": c["can_add"],
                        "slot_weight": c["slot_weight"], "peak_slot_weight": c["slot_weight"],
                        "allocated_capital": alloc, "entry_date": d_str, "entry_day_idx": d_idx,
                        "entry_dt": ts, "entry_min": et_min, "expiry_idx": exp_idx,
                        "orig_entry_price": c["entry_price"], "entry_price": c["entry_price"],
                        "last_price": c["entry_price"], "best_price": c["entry_price"],
                        "sl_price": c["sl_price"], "partial_tp_done": False, "be_stop_active": False,
                        "fast_be_trimmed": False, "trail_mult": 1.0, "cor_streak": 0,
                        "corridor_def_done": False, "corridor_def_triggered": False,
                        "swapped_in": False, "temp_bounce": bool(c.get("temp_bounce")),
                        "partial_closed_capital": 0.0, "partial_realized_pnl_usd": 0.0,
                        "max_favorable_return_pct": 0.0, "cur_ret_pct": 0.0, "cur_vwap_diff": 0.0,
                    })
                    used_t.add(tk)
                    if mg:
                        used_g.add(mg)
                    entries_today += 1

                # (3b) 可选升级：严苛防磨损动态换仓（满4h/跨日 + 弱滞[-1.8%,-0.2%] + 失守VWAP + 新标领先>=10分 + <=1次/日）
                if can_swap and raw_cands and len(open_positions) >= cfg.max_positions:
                    stag = []
                    for idx_p, p in enumerate(open_positions):
                        held_h = (ts - p["entry_dt"]).total_seconds() / 3600.0
                        cur_r = float(p.get("cur_ret_pct", 0.0))
                        vw_p = float(p.get("cur_vwap_diff", 0.0))
                        lost_vw_p = (vw_p < -0.20) if p["direction"] == "LONG" else (vw_p > 0.20)
                        if (held_h >= 4.0 or d_idx > p["entry_day_idx"]) and (-1.8 <= cur_r <= -0.2) and lost_vw_p and (not p.get("be_stop_active")) and (not p.get("partial_tp_done")):
                            stag.append((float(p.get("entry_score", 60.0)), cur_r, idx_p, p))
                    if stag:
                        stag.sort(key=lambda x: (x[0], x[1]))
                        old_sc, _, old_idx, old_pos = stag[0]
                        best_c = None
                        for c in raw_cands:
                            if c["ticker"] in used_t:
                                continue
                            other_grps = {cte.get_mirror_group(p["ticker"]) for j, p in enumerate(open_positions) if j != old_idx and cte.get_mirror_group(p["ticker"])}
                            if c["mg"] and c["mg"] in other_grps:
                                continue
                            if (c["score"] - old_sc) >= cfg.swap_score_gap:
                                best_c = c
                                break
                        if best_c is not None:
                            b_old = (bars_by_day_tk.get((d_cur, old_pos["ticker"])) or {}).get(ts)
                            old_ex_px = b_old["close"] if b_old else old_pos["last_price"]
                            old_ret = bt55._net_ret(old_pos["direction"], old_pos["entry_price"], old_ex_px, rt_slippage)
                            old_pnl = round(old_pos["allocated_capital"] * old_ret, 2)
                            cash = round(cash + max(0.0, old_pos["allocated_capital"] + old_pnl), 2)
                            closed_trades.append({
                                "ticker": old_pos["ticker"], "is_3x": kol.is_3x_leveraged(old_pos["ticker"]),
                                "direction": old_pos["direction"], "entry_tag": old_pos.get("entry_tag", "NORMAL"),
                                "setup": old_pos.get("setup", "V55"), "stage": old_pos.get("stage", 1),
                                "swapped_in": bool(old_pos.get("swapped_in", False)),
                                "temp_bounce": bool(old_pos.get("temp_bounce", False)),
                                "corridor_def_triggered": bool(old_pos.get("corridor_def_triggered", False)),
                                "max_favorable_return_pct": round(old_pos.get("max_favorable_return_pct", 0.0), 2),
                                "entry_date": old_pos["entry_date"], "entry_month": old_pos["entry_date"][:7],
                                "entry_time_et": old_pos["entry_dt"].strftime("%Y-%m-%d %H:%M:%S"),
                                "orig_entry_price": old_pos["orig_entry_price"], "entry_price": old_pos["entry_price"],
                                "exit_date": d_str, "exit_time_et": ts.strftime("%Y-%m-%d %H:%M:%S"),
                                "exit_price": round(old_ex_px, 2), "outcome": "SWAP_OUT", "exit_reason": "SWAP_OUT",
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
                                    "entry_tag": best_c["entry_tag"], "entry_score": best_c["score"],
                                    "stage": 1, "can_add": best_c["can_add"],
                                    "slot_weight": best_c["slot_weight"], "peak_slot_weight": best_c["slot_weight"],
                                    "allocated_capital": alloc, "entry_date": d_str, "entry_day_idx": d_idx,
                                    "entry_dt": ts, "entry_min": et_min, "expiry_idx": exp_idx,
                                    "orig_entry_price": best_c["entry_price"], "entry_price": best_c["entry_price"],
                                    "last_price": best_c["entry_price"], "best_price": best_c["entry_price"],
                                    "sl_price": best_c["sl_price"], "partial_tp_done": False, "be_stop_active": False,
                                    "fast_be_trimmed": False, "trail_mult": 1.0, "cor_streak": 0,
                                    "corridor_def_done": False, "corridor_def_triggered": False,
                                    "swapped_in": True, "temp_bounce": False,
                                    "partial_closed_capital": 0.0, "partial_realized_pnl_usd": 0.0,
                                    "max_favorable_return_pct": 0.0, "cur_ret_pct": 0.0, "cur_vwap_diff": 0.0,
                                })
                                swaps_today += 1
                                swap_count += 1
                                entries_today += 1

        # 日末权益
        eod_dep = 0.0
        for pos in open_positions:
            tmap = bars_by_day_tk.get((d_cur, pos["ticker"])) or {}
            lp = pos.get("last_price", pos["entry_price"])
            for ts_rev in reversed(ts_list):
                if ts_rev in tmap:
                    lp = tmap[ts_rev]["close"]
                    break
            r_u = bt55._net_ret(pos["direction"], pos["entry_price"], lp, rt_slippage)
            eod_dep += max(0.0, pos["allocated_capital"] * (1.0 + r_u))
        eod_eq = round(cash + eod_dep, 2)
        peak_equity = max(peak_equity, eod_eq)
        max_dd = max(max_dd, (peak_equity - eod_eq) / peak_equity * 100.0 if peak_equity > 0 else 0.0)
        equity_curve.append({"date": d_str, "equity": eod_eq})

    # 期末平仓
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
        closed_trades.append({
            "ticker": pos["ticker"], "is_3x": kol.is_3x_leveraged(pos["ticker"]),
            "direction": pos["direction"], "entry_tag": pos.get("entry_tag", "NORMAL"),
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
            "gap_sl_slip": False, "allocated_capital": ta, "pnl_usd": tp,
            "net_return_pct": round((tp / ta) * 100.0, 2) if ta > 0 else 0.0,
            "hold_hours": round((last_ts - pos["entry_dt"]).total_seconds() / 3600.0, 2),
            "v54_tp_price": None,
        })

    end_eq = round(cash, 2)
    peak_equity = max(peak_equity, end_eq)
    max_dd = round(max(max_dd, (peak_equity - end_eq) / peak_equity * 100.0 if peak_equity > 0 else 0.0), 2)
    w23 = v57._count_win23_turned_loss(closed_trades)

    return {
        "arm": cfg.name,
        "label": cfg.label,
        "ending_equity": end_eq,
        "net_return_pct": round((end_eq - bt55.INITIAL_CAPITAL) / bt55.INITIAL_CAPITAL * 100.0, 2),
        "max_drawdown_pct": max_dd,
        "summary": bt55._summarize_trades(closed_trades),
        "exit_breakdown": bt55._breakdown_exits(closed_trades),
        "monthly_attribution": bt55._breakdown_monthly(closed_trades),
        "diagnostics": {
            "win23_turned_loss": w23,
            "corridor_def_triggers": corridor_def_triggers,
            "corridor_def_trades": bt55._summarize_trades([t for t in closed_trades if t.get("corridor_def_triggered")]),
            "swap_count": swap_count,
            "swap_out_trades": bt55._summarize_trades([t for t in closed_trades if t.get("exit_reason") == "SWAP_OUT"]),
            "swap_in_trades": bt55._summarize_trades([t for t in closed_trades if t.get("swapped_in")]),
            "temp_bounce_entries": temp_bounce_entries,
            "temp_bounce_trades": bt55._summarize_trades([t for t in closed_trades if t.get("temp_bounce")]),
            "avg_pnl_per_trade": round(sum(t["pnl_usd"] for t in closed_trades) / max(1, len(closed_trades)), 2),
        },
        "trades": closed_trades,
    }


def main():
    df5 = pd.read_parquet(bt55.PARQUET_5M)
    daily_df = bt55.load_daily_panels()
    dt_s = pd.to_datetime(df5["Datetime"])
    hhmm = dt_s.dt.hour * 60 + dt_s.dt.minute
    rth_dates = df5[(hhmm >= 9 * 60 + 30) & (hhmm <= 15 * 60 + 55)].groupby(dt_s.dt.date)["Datetime"].nunique()
    cand_dates = sorted([d for d, cnt in rth_dates.items() if cnt >= 20])
    macro_ctx, daily_ctx = bt55.precompute_causal_daily_context(daily_df, cand_dates)
    v57.enrich_macro_with_raw_temp(macro_ctx, daily_ctx)
    valid_dates, day_timestamps, bars_by_day_tk, pm_bounds = bt55.build_causal_5m_market_replay(df5, daily_ctx)
    _, _, corridor_bands = v57.build_extended_and_corridor(df5, valid_dates, bars_by_day_tk)

    slim_arms = [
        # --- 第一组：严格单变量正交拆解（均从精确 V55_D_Hybrid 2槽 55%/10% 底座出发） ---
        SlimConfig(name="Iso_0_ExactV55_Base", label="校验基线：精确 V55_D 2槽底座（零改动）", max_positions=2),
        SlimConfig(name="Iso_1_Only_NoShortInv", label="单变量①：仅禁空反向ETF (SOXS/SQQQ/VIXY)", max_positions=2, no_short_inv=True),
        SlimConfig(name="Iso_2_Only_ETF52", label="单变量②：仅7只核心ETF放宽至 52%/6%", max_positions=2, use_etf_52=True),
        SlimConfig(name="Iso_3_Only_FastBE26", label="单变量③：仅加 Neo Lee 快推保本(+2.6%/+4.0%, 25%落袋)", max_positions=2, use_fast_be=True, fast_be_1x_pct=2.6, fast_be_3x_pct=4.0),
        SlimConfig(name="Iso_4_Only_CorDef", label="单变量④：仅加走廊盈利防守 (CORRIDOR_DEFENSE)", max_positions=2, use_corridor_def=True),
        SlimConfig(name="Iso_5_Only_Swap", label="单变量⑤：仅加防磨损动态换仓 (SWAP)", max_positions=2, use_swap=True),
        SlimConfig(name="Iso_6_Only_3Slot", label="单变量⑥：仅2槽扩3槽 (3×33%: 22%+11%)", max_positions=3, starter_weight_1x=0.22, addon_weight_1x=0.11, starter_weight_3x=0.22, addon_weight_3x=0.11),
        # --- 第二组：精简强强组合（严格剔除低胜率/高磨损项，仅保留有效模块） ---
        SlimConfig(name="Slim_2S_BE26_ETF52", label="精简2槽：V5.5底座 + BE26 + 7只ETF(52%/6%)", max_positions=2, use_etf_52=True, use_fast_be=True, fast_be_1x_pct=2.6, fast_be_3x_pct=4.0),
        SlimConfig(name="Slim_3S_BE26", label="精简3槽A：3槽×33% + 快推保本BE26", max_positions=3, starter_weight_1x=0.22, addon_weight_1x=0.11, starter_weight_3x=0.22, addon_weight_3x=0.11, use_fast_be=True, fast_be_1x_pct=2.6, fast_be_3x_pct=4.0),
        SlimConfig(name="Slim_3S_BE26_ETF52", label="精简3槽B：3槽×33% + BE26 + 7只ETF(52%/6%)", max_positions=3, starter_weight_1x=0.22, addon_weight_1x=0.11, starter_weight_3x=0.22, addon_weight_3x=0.11, use_etf_52=True, use_fast_be=True, fast_be_1x_pct=2.6, fast_be_3x_pct=4.0),
        SlimConfig(name="Slim_3S_BE26_ETF_Cor", label="精简3槽C：3槽×33% + BE26 + 7只ETF + 走廊防守", max_positions=3, starter_weight_1x=0.22, addon_weight_1x=0.11, starter_weight_3x=0.22, addon_weight_3x=0.11, use_etf_52=True, use_fast_be=True, fast_be_1x_pct=2.6, fast_be_3x_pct=4.0, use_corridor_def=True),
        SlimConfig(name="Slim_3S_AllClean", label="精简3槽D：3槽×33% + BE26 + 7只ETF + 禁空反向 + 走廊防守", max_positions=3, starter_weight_1x=0.22, addon_weight_1x=0.11, starter_weight_3x=0.22, addon_weight_3x=0.11, use_etf_52=True, no_short_inv=True, use_fast_be=True, fast_be_1x_pct=2.6, fast_be_3x_pct=4.0, use_corridor_def=True),
        SlimConfig(name="Slim_3S_AllClean_Swap", label="精简3槽E：精简3槽D + 动态换仓Swap (验证Swap增量)", max_positions=3, starter_weight_1x=0.22, addon_weight_1x=0.11, starter_weight_3x=0.22, addon_weight_3x=0.11, use_etf_52=True, no_short_inv=True, use_fast_be=True, fast_be_1x_pct=2.6, fast_be_3x_pct=4.0, use_corridor_def=True, use_swap=True),
    ]
    base_arms = [a for a in bt55.PRE_REGISTERED_ARMS if a.name in ("Baseline_V54", "V55_D_Hybrid")]

    results_by_slip: Dict[str, List[Dict[str, Any]]] = {}
    for slip_key, rt_slip, _ in bt55.SLIPPAGE_REGIMES:
        arm_res = []
        for bcfg in base_arms:
            r = bt55.run_arm_replay(bcfg, rt_slip, valid_dates, day_timestamps, bars_by_day_tk, pm_bounds, macro_ctx, daily_ctx)
            w23 = 0
            for tr in r["trades"]:
                ed, xd = date.fromisoformat(tr["entry_date"]), date.fromisoformat(tr["exit_date"])
                et_s = datetime.strptime(tr["entry_time_et"], "%Y-%m-%d %H:%M:%S")
                xt_s = datetime.strptime(tr["exit_time_et"], "%Y-%m-%d %H:%M:%S")
                ep = float(tr["orig_entry_price"])
                mfe_m = 0.0
                for d_c in valid_dates:
                    if d_c < ed or d_c > xd:
                        continue
                    for ts_b, b in (bars_by_day_tk.get((d_c, tr["ticker"])) or {}).items():
                        if ts_b < et_s or ts_b > xt_s:
                            continue
                        g = ((b["close"] - ep) / ep * 100.0) if tr["direction"] == "LONG" else ((ep - b["close"]) / ep * 100.0)
                        if g > mfe_m:
                            mfe_m = g
                if mfe_m >= (3.5 if tr.get("is_3x") else 2.3) and tr["pnl_usd"] < 0:
                    w23 += 1
            r["diagnostics"] = {
                "win23_turned_loss": w23,
                "avg_pnl_per_trade": round(sum(t["pnl_usd"] for t in r["trades"]) / max(1, len(r["trades"])), 2),
            }
            arm_res.append(r)
        for scfg in slim_arms:
            arm_res.append(run_slim(scfg, rt_slip, valid_dates, day_timestamps, bars_by_day_tk, corridor_bands, pm_bounds, macro_ctx, daily_ctx))
        results_by_slip[slip_key] = arm_res

    print(f"=== V5.7 SLIM SUBTRACTION ABLATION ({valid_dates[0]} ~ {valid_dates[-1]}) ===")
    for slip_key in ("25bps", "3bps"):
        print(f"\n--- SLIPPAGE: {slip_key} ---")
        for r in results_by_slip[slip_key]:
            s = r["summary"]
            ma = r["monthly_attribution"]
            m910 = ma["2026-09_to_10_BullChop"]["net_pnl_usd"]
            m11 = ma["2026-11_CrashSelloff"]["net_pnl_usd"]
            dg = r["diagnostics"]
            print(f"  {r['arm']:22s} | n={s['count']:3d} | WR={s['win_rate_pct']:5.1f}% | PF={s['profit_factor']:4.2f} | ret={r['net_return_pct']:+6.2f}% | DD=-{r['max_drawdown_pct']:5.2f}% | avg$={dg['avg_pnl_per_trade']:+5.2f} | 9-10m={m910:+7.2f} | Nov={m11:+7.2f} | w23L={dg['win23_turned_loss']:2d}")

    print("\n--- SLIM DIAGNOSTICS (25 bps) ---")
    for r in results_by_slip["25bps"]:
        if not (r["arm"].startswith("Slim_") or r["arm"].startswith("Iso_")):
            continue
        dg = r["diagnostics"]
        eb = r["exit_breakdown"]
        ex_str = {k: (v["count"], v["avg_ret_pct"], v["net_pnl_usd"]) for k, v in eb.items() if v.get("count", 0) > 0}
        print(f"\n[{r['arm']}] ret={r['net_return_pct']:+.2f}% PF={r['summary']['profit_factor']:.2f} WR={r['summary']['win_rate_pct']:.1f}% DD=-{r['max_drawdown_pct']:.2f}%")
        print("  Exits:", ex_str)
        print(f"  CorDef={dg['corridor_def_triggers']} {dg['corridor_def_trades']} | Swap={dg['swap_count']} Out={dg['swap_out_trades']['net_pnl_usd']:+.2f} In={dg['swap_in_trades']['net_pnl_usd']:+.2f} (WR {dg['swap_in_trades']['win_rate_pct']:.1f}%) | TempB={dg['temp_bounce_entries']} {dg['temp_bounce_trades']['net_pnl_usd']:+.2f}")

    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(REPORT_SLIM_JSON, "w", encoding="utf-8") as f:
        json.dump({"generated_at_utc": datetime.now(timezone.utc).isoformat(), "results": results_by_slip}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
