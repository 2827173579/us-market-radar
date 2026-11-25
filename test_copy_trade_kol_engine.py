# -*- coding: utf-8 -*-
"""
V5.5 激进 KOL 狙击轨离线隔离单元测试 (test_copy_trade_kol_engine.py)
全程使用 tempfile.TemporaryDirectory()，严禁触碰任何生产账本或发送真实外部消息。
"""

import os
import tempfile
import unittest
from datetime import datetime

import copy_trade_engine as cte
import copy_trade_kol_engine as kol


def _et(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, 0, tzinfo=cte._NY_TZ)


class TestCopyTradeKolEngineV55(unittest.TestCase):
    def test_01_patch_a_3x_etf_sl_tightened_2_5_to_4_5_and_1x_2_to_4_5(self):
        """补丁 A：3x 杠杆 ETF 结构硬止损严格收紧在 [2.5%, 4.5%]；1x 股收紧在 [2.0%, 4.5%]。"""
        # 3x SOXL 结构低点极深 (-15%) -> 必须被硬夹在 -4.5% ($95.50)
        sl_soxl_deep = kol.resolve_kol_structural_sl(
            "SOXL", "LONG", 100.0,
            live_quote={"day_low": 85.0}, sr_anchor={"s1": 84.0},
        )
        self.assertAlmostEqual(sl_soxl_deep, 95.50, places=2)

        # 3x SOXL 结构低点极近 (-0.4%) -> 必须被硬夹在 -2.5% ($97.50)
        sl_soxl_tight = kol.resolve_kol_structural_sl(
            "SOXL", "LONG", 100.0,
            live_quote={"day_low": 99.6},
        )
        self.assertAlmostEqual(sl_soxl_tight, 97.50, places=2)

        # 3x SQQQ 做空结构高点极高 (+12%) -> 必须被硬夹在 +4.5% ($104.50)
        sl_sqqq_short = kol.resolve_kol_structural_sl(
            "SQQQ", "SHORT", 100.0,
            live_quote={"day_high": 112.0},
        )
        self.assertAlmostEqual(sl_sqqq_short, 104.50, places=2)

        # 1x NVDA 结构低点极近 (-0.3%) -> 硬夹在 -2.0% ($98.00)；极远 (-10%) -> 硬夹在 -4.5% ($95.50)
        self.assertAlmostEqual(kol.resolve_kol_structural_sl("NVDA", "LONG", 100.0, live_quote={"day_low": 99.7}), 98.00, places=2)
        self.assertAlmostEqual(kol.resolve_kol_structural_sl("NVDA", "LONG", 100.0, live_quote={"day_low": 90.0}), 95.50, places=2)

    def test_02_patch_b_two_stage_pyramiding_30_to_50_avg_entry_and_stop_move_to_orig(self):
        """补丁 B：30% 底仓浮亏绝不摊平；浮盈 >=+1.8% 且站稳 VWAP 触发 +20% 顺势加仓，重算均价且止损移至原底仓价。"""
        with tempfile.TemporaryDirectory() as td:
            lp = os.path.join(td, "kol.json")
            ai = {
                "market_regime": "BULL_EXPANSION",
                "by_ticker": {
                    "NVDA": {"calibrated_win_rate_pct": 62.0, "calibrated_short_win_rate_pct": 28.0},
                },
            }
            # 1) 10:00 ET Sweep 收回开 30% 底仓 ($300 @ $100.00)
            q_open = {
                "QQQ": {"active_price": 500.0, "pct_change": -0.5},
                "NVDA": {
                    "active_price": 100.0, "prev_close": 99.5, "pct_change": 0.5,
                    "day_low": 98.5, "vwap": 99.8, "vwap_diff_pct": 0.2,
                    "rsi_5m": 55.0, "rvol_5m": 1.6,
                },
            }
            r1 = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_open, daily_sr_anchors={"NVDA": {"s1": 99.0, "r1": 104.0}},
                market_regime="BULL_EXPANSION", session_code="REGULAR",
                now_et=_et(2026, 11, 24, 10, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r1["opened_now"]), 1)
            p1 = r1["ledger"]["open_positions"][0]
            self.assertAlmostEqual(p1["slot_weight"], 0.30, places=2)
            self.assertAlmostEqual(p1["allocated_capital"], 300.0, places=2)
            orig_sl = p1["sl_price"]
            self.assertLess(orig_sl, 98.1)

            # 2) 浮亏 -1.5% ($98.50) -> 严禁逆势摊平加仓！
            q_dip = {"NVDA": {"active_price": 98.5, "vwap": 99.5, "vwap_diff_pct": -1.0, "rsi_5m": 42.0, "rvol_5m": 1.2}}
            r_dip = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_dip, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 10, 20), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_dip["addons_now"]), 0)
            self.assertEqual(r_dip["ledger"]["open_positions"][0]["stage"], 1)

            # 3) 走强至 $102.00 (+1.97% net >= 1.8%) 且站稳 VWAP -> 触发 +20% 顺势加仓 + 补丁 B 止损移至 $100.00
            q_add = {"NVDA": {"active_price": 102.0, "vwap": 101.0, "vwap_diff_pct": 1.0, "rsi_5m": 62.0, "rvol_5m": 1.5}}
            r_add = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_add, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 10, 45), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_add["addons_now"]), 1)
            p2 = r_add["ledger"]["open_positions"][0]
            self.assertEqual(p2["stage"], 2)
            self.assertAlmostEqual(p2["slot_weight"], 0.50, places=2)
            # 股数加权均价: (300 + 200) / (300/100 + 200/102) = 500 / 4.960784 = 100.79
            self.assertAlmostEqual(p2["entry_price"], 100.79, places=2)
            # 补丁 B：硬止损上移至原底仓入场价 $100.00
            self.assertAlmostEqual(p2["sl_price"], 100.00, places=2)
            self.assertGreater(p2["sl_price"], orig_sl)

    def test_03_partial_tp_be_stop_trail_tp_and_climax_tp(self):
        """验证 +4% 平半仓落袋 + 推保本损 (cost+0.3%) + 追踪止盈 (TRAIL_TP) 与抛物线卖强 (CLIMAX_TP) 及分毫不差记账。"""
        with tempfile.TemporaryDirectory() as td:
            lp = os.path.join(td, "kol.json")
            ai = {"market_regime": "BULL_EXPANSION", "by_ticker": {
                "NVDA": {"calibrated_win_rate_pct": 63.0, "calibrated_short_win_rate_pct": 25.0},
                "MU": {"calibrated_win_rate_pct": 61.0, "calibrated_short_win_rate_pct": 28.0},
            }}
            q0 = {
                "QQQ": {"active_price": 500.0, "pct_change": 0.2},
                "NVDA": {"active_price": 100.0, "prev_close": 99.0, "pct_change": 1.0, "vwap": 99.5, "vwap_diff_pct": 0.5, "rsi_5m": 58.0, "rvol_5m": 1.7},
                "MU": {"active_price": 100.0, "prev_close": 99.0, "pct_change": 1.0, "vwap": 99.5, "vwap_diff_pct": 0.5, "rsi_5m": 58.0, "rvol_5m": 1.7},
            }
            anchors = {"NVDA": {"s1": 98.0, "r1": 99.5}, "MU": {"s1": 98.0, "r1": 99.5}}
            r0 = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q0, daily_sr_anchors=anchors,
                market_regime="BULL_EXPANSION", session_code="REGULAR",
                now_et=_et(2026, 11, 24, 10, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r0["opened_now"]), 2)

            # 涨至 $104.20 (+4.17% net >= 4.0%) -> 两笔均触发一阶平半仓落袋 + 推保本损 $100.30
            q_part = {
                "NVDA": {"active_price": 104.20, "vwap": 102.0, "vwap_diff_pct": 2.1, "rsi_5m": 66.0, "rvol_5m": 1.4},
                "MU": {"active_price": 104.20, "vwap": 102.0, "vwap_diff_pct": 2.1, "rsi_5m": 66.0, "rvol_5m": 1.4},
            }
            r_p = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_part, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 11, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_p["partials_now"]), 2)
            for p in r_p["ledger"]["open_positions"]:
                self.assertTrue(p["partial_tp_done"])
                self.assertTrue(p["be_stop_active"])
                self.assertAlmostEqual(p["sl_price"], 100.30, places=2)

            # NVDA 飙升至 $110.00 (创最高点)，MU 触发抛物线放量高潮 CLIMAX_TP (rsi_5m=80, rvol=2.8, +7.0%)
            q_peak = {
                "NVDA": {"active_price": 110.00, "vwap": 105.0, "vwap_diff_pct": 4.7, "rsi_5m": 71.0, "rvol_5m": 1.6},
                "MU": {"active_price": 107.00, "vwap": 103.5, "vwap_diff_pct": 3.4, "rsi_5m": 80.5, "rvol_5m": 2.8},
            }
            r_pk = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_peak, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 12, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_pk["closed_now"]), 1)
            self.assertEqual(r_pk["closed_now"][0]["ticker"], "MU")
            self.assertEqual(r_pk["closed_now"][0]["outcome"], "CLIMAX_TP")

            # NVDA 从 $110.00 回撤 -2.4% 至 $107.36 -> 触发 TRAIL_TP
            q_tr = {"NVDA": {"active_price": 107.36, "vwap": 105.5, "vwap_diff_pct": 1.7, "rsi_5m": 58.0, "rvol_5m": 1.1}}
            r_tr = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_tr, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 13, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_tr["closed_now"]), 1)
            self.assertEqual(r_tr["closed_now"][0]["outcome"], "TRAIL_TP")
            self.assertGreater(r_tr["ledger"]["total_equity"], 1030.0)
            self.assertEqual(r_tr["ledger"]["stats"]["tp_hit_count"], 2)
            # 全部清仓后现金 == 总净值，记账分毫不差
            self.assertAlmostEqual(r_tr["ledger"]["cash"], r_tr["ledger"]["total_equity"], places=2)

    def test_04_patch_c_flip_cooldown_45m_25pct_size_and_once_per_day(self):
        """补丁 C：止损后反向 FLIP 冷却必须 >=45 分钟，且仅允许 25% 轻仓试探，单日单标的最多 1 次。"""
        with tempfile.TemporaryDirectory() as td:
            lp = os.path.join(td, "kol.json")
            ai_long = {"market_regime": "BULL_EXPANSION", "by_ticker": {
                "SOXL": {"calibrated_win_rate_pct": 60.0, "calibrated_short_win_rate_pct": 30.0},
            }}
            q_open = {
                "QQQ": {"active_price": 500.0, "pct_change": 0.0},
                "SOXL": {"active_price": 40.0, "prev_close": 39.5, "pct_change": 1.2, "vwap": 39.8, "vwap_diff_pct": 0.5, "rsi_5m": 56.0, "rvol_5m": 1.8},
            }
            r1 = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai_long, live_quotes=q_open, daily_sr_anchors={"SOXL": {"s1": 38.0, "r1": 39.9}},
                market_regime="BULL_EXPANSION", session_code="REGULAR",
                now_et=_et(2026, 11, 24, 10, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r1["opened_now"]), 1)

            # 10:15 ET 跌至 $38.00 (-5%) 触发结构止损 SL_HIT
            q_sl = {"SOXL": {"active_price": 38.0, "prev_close": 39.5, "pct_change": -3.8, "vwap": 39.2, "vwap_diff_pct": -3.0, "rsi_5m": 28.0, "rvol_5m": 2.2}}
            r_sl = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai_long, live_quotes=q_sl, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 10, 15), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(r_sl["closed_now"][0]["outcome"], "SL_HIT")

            # 10:50 ET (冷却仅 35m < 45m)：即使反向空胜率 65% + VWAP 下方 + rvol 2.0，也绝不放行 FLIP！
            ai_flip = {"market_regime": "BEAR_DEFENSE", "by_ticker": {
                "SOXL": {"calibrated_win_rate_pct": 25.0, "calibrated_short_win_rate_pct": 65.0},
            }}
            q_flip = {"SOXL": {"active_price": 37.8, "prev_close": 39.5, "pct_change": -4.3, "vwap": 38.8, "vwap_diff_pct": -2.5, "rsi_5m": 38.0, "rvol_5m": 2.0}}
            r_too_early = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai_flip, live_quotes=q_flip, market_regime="BEAR_DEFENSE",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 10, 50), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_too_early["opened_now"]), 0)

            # 11:05 ET (冷却满 50m >= 45m) -> 放行 FLIP 做空，且仓位严格为 25% 轻仓、不可加仓
            r_flip = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai_flip, live_quotes=q_flip, market_regime="BEAR_DEFENSE",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 11, 5), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_flip["opened_now"]), 1)
            pf = r_flip["opened_now"][0]
            self.assertEqual(pf["direction"], "SHORT")
            self.assertEqual(pf["entry_tag"], "FLIP")
            self.assertAlmostEqual(pf["slot_weight"], 0.25, places=2)
            self.assertFalse(pf["can_add"])

            # 若该反手单 11:30 再次止损，12:30 (超过45m) 同日同标的禁止二次反手！
            q_sl2 = {"SOXL": {"active_price": 39.9, "prev_close": 39.5, "pct_change": 1.0, "vwap": 39.0, "vwap_diff_pct": 2.3, "rsi_5m": 65.0, "rvol_5m": 2.0}}
            kol.run_kol_copy_trade_cycle(
                ai_predictions=ai_long, live_quotes=q_sl2, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 11, 30), ledger_path=lp, dry_run=False,
            )
            r_second_flip = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai_long, live_quotes=q_sl2, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 12, 30), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_second_flip["opened_now"]), 0)

    def test_05_patch_d_weak_early_exit_normal_1130_vs_late_entry_1330_and_t3_expiry(self):
        """补丁 D：常规单次日 11:30 ET 体检早撤；14:00 ET 后开的尾盘仓顺延至次日 13:30 ET 体检；并验证 T+3 到期。"""
        with tempfile.TemporaryDirectory() as td:
            lp = os.path.join(td, "kol.json")
            ai = {"market_regime": "BULL_EXPANSION", "by_ticker": {
                "NVDA": {"calibrated_win_rate_pct": 62.0, "calibrated_short_win_rate_pct": 28.0},
                "AMD": {"calibrated_win_rate_pct": 61.0, "calibrated_short_win_rate_pct": 29.0},
            }}
            # 10:30 ET 开 NVDA（常规单）
            q1 = {
                "QQQ": {"active_price": 500.0, "pct_change": 0.1},
                "NVDA": {"active_price": 100.0, "prev_close": 99.5, "pct_change": 0.5, "vwap": 99.6, "vwap_diff_pct": 0.4, "rsi_5m": 58.0, "rvol_5m": 1.6},
            }
            kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q1, daily_sr_anchors={"NVDA": {"s1": 98.0, "r1": 99.8}},
                market_regime="BULL_EXPANSION", session_code="REGULAR",
                now_et=_et(2026, 11, 23, 10, 30), ledger_path=lp, dry_run=False,
            )
            # 14:30 ET 开 AMD（尾盘仓 > 14:00 ET）
            q2 = {
                "QQQ": {"active_price": 500.0, "pct_change": 0.1},
                "NVDA": {"active_price": 100.5, "prev_close": 99.5, "pct_change": 1.0, "vwap": 100.2, "vwap_diff_pct": 0.3, "rsi_5m": 55.0, "rvol_5m": 1.2},
                "AMD": {"active_price": 150.0, "prev_close": 149.0, "pct_change": 0.67, "vwap": 149.5, "vwap_diff_pct": 0.3, "rsi_5m": 57.0, "rvol_5m": 1.7},
            }
            r_late = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q2, daily_sr_anchors={"AMD": {"s1": 147.0, "r1": 149.8}},
                market_regime="BULL_EXPANSION", session_code="REGULAR",
                now_et=_et(2026, 11, 23, 14, 30), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_late["ledger"]["open_positions"]), 2)

            # 次日 (11-24) 11:40 ET：两者均横盘微亏且在 VWAP 下方
            # 预期：NVDA 立即触发 WEAK_EARLY_EXIT；AMD 尾盘仓受补丁 D 保护继续持有！
            q_next_am = {
                "NVDA": {"active_price": 99.6, "vwap": 100.1, "vwap_diff_pct": -0.5, "rsi_5m": 44.0, "rvol_5m": 1.0},
                "AMD": {"active_price": 149.5, "vwap": 150.2, "vwap_diff_pct": -0.47, "rsi_5m": 44.0, "rvol_5m": 1.0},
            }
            r_am = kol.run_kol_copy_trade_cycle(
                ai_predictions={"market_regime": "NEUTRAL_CHOP", "by_ticker": {}}, live_quotes=q_next_am,
                market_regime="NEUTRAL_CHOP", session_code="REGULAR",
                now_et=_et(2026, 11, 24, 11, 40), ledger_path=lp, dry_run=False,
            )
            self.assertEqual([c["ticker"] for c in r_am["closed_now"]], ["NVDA"])
            self.assertEqual(r_am["closed_now"][0]["outcome"], "WEAK_EARLY_EXIT")
            self.assertEqual([p["ticker"] for p in r_am["ledger"]["open_positions"]], ["AMD"])

            # 次日 13:20 ET (< 13:30 ET) AMD 仍受缓冲保护
            r_mid = kol.run_kol_copy_trade_cycle(
                ai_predictions={"market_regime": "NEUTRAL_CHOP", "by_ticker": {}}, live_quotes=q_next_am,
                market_regime="NEUTRAL_CHOP", session_code="REGULAR",
                now_et=_et(2026, 11, 24, 13, 20), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_mid["closed_now"]), 0)

            # 次日 13:35 ET (>= 13:30 ET) AMD 仍弱势 -> 触发 WEAK_EARLY_EXIT
            r_pm = kol.run_kol_copy_trade_cycle(
                ai_predictions={"market_regime": "NEUTRAL_CHOP", "by_ticker": {}}, live_quotes=q_next_am,
                market_regime="NEUTRAL_CHOP", session_code="REGULAR",
                now_et=_et(2026, 11, 24, 13, 35), ledger_path=lp, dry_run=False,
            )
            self.assertEqual([c["ticker"] for c in r_pm["closed_now"]], ["AMD"])
            self.assertEqual(r_pm["closed_now"][0]["outcome"], "WEAK_EARLY_EXIT")

    def test_06_bear_defense_15pct_oversold_bounce_vs_short_priority(self):
        """空头/VIX 倒挂期开关：NQ=F 未站回 VWAP 禁多；站回 VWAP + 极度超跌允许 15% 轻仓反抽；空单占优先重仓火力。"""
        ai = {
            "market_regime": "BEAR_DEFENSE",
            "by_ticker": {
                "NVDA": {"calibrated_win_rate_pct": 46.0, "calibrated_short_win_rate_pct": 48.0},
                "TSLA": {"calibrated_win_rate_pct": 28.0, "calibrated_short_win_rate_pct": 63.0},
            },
        }
        vr = {"vix_hard_brake": True, "vix_state": "BACKWARDATION_BRAKE", "panic_clearing_active": False}
        quotes = {
            "QQQ": {"active_price": 600.0, "pct_change": -2.0},
            "NVDA": {
                "active_price": 135.0, "prev_close": 141.0, "pct_change": -4.25,
                "is_deep_pullback": True, "vwap": 135.1, "vwap_diff_pct": -0.07,
                "rsi_5m": 31.0, "rvol_5m": 1.5,
            },
            "TSLA": {
                "active_price": 250.0, "prev_close": 254.0, "pct_change": -1.57,
                "is_broken_bearish": True, "vwap": 250.5, "vwap_diff_pct": -0.2,
                "rsi_5m": 46.0, "rvol_5m": 1.3,
            },
        }
        # Case A: NQ=F 在 VWAP 下方 -> NVDA 反抽多被拦截，只放行 TSLA 顺势空单 (30% 底仓)
        c_no_nq = kol.screen_kol_sniper_candidates(
            ai_predictions=ai, live_quotes=quotes, open_positions=[], cur_et=_et(2026, 11, 24, 11, 0),
            market_regime="BEAR_DEFENSE", vix_regime=vr,
            futures_data={"NQ=F": {"reclaim_vwap": False, "vwap_diff_pct": -0.8}},
        )
        self.assertEqual([(c["ticker"], c["direction"], c["slot_weight"]) for c in c_no_nq], [("TSLA", "SHORT", 0.30)])

        # Case B: NQ=F 盘中站回 VWAP -> TSLA 30% 顺势空优先排第 1，NVDA 允许 15% 轻仓超跌反抽 (can_add=False)
        c_with_nq = kol.screen_kol_sniper_candidates(
            ai_predictions=ai, live_quotes=quotes, open_positions=[], cur_et=_et(2026, 11, 24, 11, 0),
            market_regime="BEAR_DEFENSE", vix_regime=vr,
            futures_data={"NQ=F": {"reclaim_vwap": True, "vwap_diff_pct": 0.15}},
        )
        self.assertEqual(len(c_with_nq), 2)
        self.assertEqual(c_with_nq[0]["ticker"], "TSLA")
        self.assertEqual(c_with_nq[0]["slot_weight"], 0.30)
        self.assertEqual(c_with_nq[1]["ticker"], "NVDA")
        self.assertEqual(c_with_nq[1]["entry_tag"], "BEAR_BOUNCE")
        self.assertAlmostEqual(c_with_nq[1]["slot_weight"], 0.15, places=2)
        self.assertFalse(c_with_nq[1]["can_add"])

    def test_07_dry_run_guard_dual_embed_and_dual_drawer_hygiene(self):
        """干跑绝不落盘/不发消息；双轨 /copy 卡片与双轨抽屉绝不含“建议股数”与缺字币符号。"""
        with tempfile.TemporaryDirectory() as td:
            lp = os.path.join(td, "kol_dry.json")
            sp = os.path.join(td, "steady_dry.json")
            sent = []
            ai = {"market_regime": "BULL_EXPANSION", "by_ticker": {
                "NVDA": {"calibrated_win_rate_pct": 65.0, "calibrated_short_win_rate_pct": 25.0},
            }}
            q = {
                "QQQ": {"active_price": 500.0, "pct_change": 0.0},
                "NVDA": {"active_price": 100.0, "prev_close": 99.5, "pct_change": 0.5, "vwap": 99.6, "vwap_diff_pct": 0.4, "rsi_5m": 58.0, "rvol_5m": 1.8},
            }
            old_env = os.environ.get("RADAR_DISABLE_EXTERNAL_ALERTS")
            try:
                os.environ["RADAR_DISABLE_EXTERNAL_ALERTS"] = "1"
                res = kol.run_kol_copy_trade_cycle(
                    ai_predictions=ai, live_quotes=q, daily_sr_anchors={"NVDA": {"s1": 98.0, "r1": 99.8}},
                    market_regime="BULL_EXPANSION", session_code="REGULAR",
                    now_et=_et(2026, 11, 24, 10, 30), ledger_path=lp,
                    alert_sender=lambda *a: sent.append(a),
                )
            finally:
                if old_env is None:
                    os.environ.pop("RADAR_DISABLE_EXTERNAL_ALERTS", None)
                else:
                    os.environ["RADAR_DISABLE_EXTERNAL_ALERTS"] = old_env

            self.assertTrue(res["dry_run"])
            self.assertFalse(res["persisted"])
            self.assertFalse(os.path.exists(lp))
            self.assertEqual(len(sent), 0)

            emb = kol.build_dual_track_copy_embed_dict(steady_path=sp, kol_path=lp)
            emb_s = str(emb)
            self.assertIn("V5.4 稳健轨", emb_s)
            self.assertIn("V5.5 KOL 狙击轨", emb_s)
            self.assertNotIn("建议股数", emb_s)
            self.assertNotIn("\U0001fa99", emb_s)

            drawer = kol.render_dual_track_copy_drawer_html(steady_path=sp, kol_path=lp, drawer_id="dualDrawerTest")
            self.assertIn("V5.5 激进 KOL 狙击轨", drawer)
            self.assertIn("V5.4 稳健基准轨", drawer)
            self.assertIn("<svg", drawer)
            self.assertNotIn("建议股数", drawer)
            self.assertNotIn("\U0001fa99", drawer)


if __name__ == "__main__":
    unittest.main()
