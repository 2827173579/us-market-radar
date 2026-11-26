# -*- coding: utf-8 -*-
"""
V5.5.1 激进 KOL 狙击轨离线隔离单元测试 (test_copy_trade_kol_engine.py)
全程使用 tempfile.TemporaryDirectory()，严禁触碰任何生产账本或发送真实外部消息。
"""

import os
import tempfile
import unittest
from datetime import datetime

import copy_trade_engine as cte
import copy_trade_kol_engine as kol


def _et(y: int, m: int, d: int, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=cte._NY_TZ)


class TestCopyTradeKolEngineV551(unittest.TestCase):
    def test_01_patch_a_3x_etf_sl_tightened_2_5_to_4_5_and_1x_2_to_4_5(self):
        """补丁 A：3x 杠杆 ETF 结构硬止损严格收紧在 [2.5%, 4.5%]；1x 股收紧在 [2.0%, 4.5%]。"""
        self.assertAlmostEqual(
            kol.resolve_kol_structural_sl("SOXL", "LONG", 100.0, live_quote={"day_low": 85.0}, sr_anchor={"s1": 84.0}),
            95.50, places=2,
        )
        self.assertAlmostEqual(
            kol.resolve_kol_structural_sl("SOXL", "LONG", 100.0, live_quote={"day_low": 99.6}),
            97.50, places=2,
        )
        self.assertAlmostEqual(
            kol.resolve_kol_structural_sl("SQQQ", "SHORT", 100.0, live_quote={"day_high": 112.0}),
            104.50, places=2,
        )
        self.assertAlmostEqual(
            kol.resolve_kol_structural_sl("NVDA", "LONG", 100.0, live_quote={"day_low": 99.7}),
            98.00, places=2,
        )
        self.assertAlmostEqual(
            kol.resolve_kol_structural_sl("NVDA", "LONG", 100.0, live_quote={"day_low": 90.0}),
            96.50, places=2,
        )

    def test_02_patch_b_v2_addon_30_to_50_avg_entry_and_1pct_buffer_stop(self):
        """补丁 B-v2：30% 底仓浮亏绝不摊平；顺势 +20% 加仓后重算均价，且止损移至原底仓外侧 1.0% 呼吸缓冲 (orig*0.99)。"""
        with tempfile.TemporaryDirectory() as td:
            lp = os.path.join(td, "kol.json")
            ai = {
                "market_regime": "BULL_EXPANSION",
                "by_ticker": {
                    "NVDA": {"calibrated_win_rate_pct": 62.0, "calibrated_short_win_rate_pct": 28.0},
                },
            }
            q_open = {
                "QQQ": {"active_price": 500.0, "pct_change": -0.5},
                "NVDA": {
                    "active_price": 100.0, "prev_close": 99.5, "pct_change": 0.5,
                    "day_low": 96.5, "vwap": 99.8, "vwap_diff_pct": 0.2,
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
            orig_sl = p1["sl_price"]
            self.assertLess(orig_sl, 98.5)

            # 浮亏 -1.5% -> 严禁逆势加仓
            q_dip = {"NVDA": {"active_price": 98.5, "vwap": 99.5, "vwap_diff_pct": -1.0, "rsi_5m": 42.0, "rvol_5m": 1.2}}
            r_dip = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_dip, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 10, 20), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_dip["addons_now"]), 0)

            # 走强至 $102.00 (+1.97% net) 站稳 VWAP -> 触发 +20% 加仓，止损移至 $99.00 (orig $100 * 0.99)
            q_add = {"NVDA": {"active_price": 102.0, "vwap": 101.0, "vwap_diff_pct": 1.0, "rsi_5m": 62.0, "rvol_5m": 1.5}}
            r_add = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_add, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 10, 45), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_add["addons_now"]), 1)
            p2 = r_add["ledger"]["open_positions"][0]
            self.assertEqual(p2["stage"], 2)
            self.assertAlmostEqual(p2["slot_weight"], 0.50, places=2)
            self.assertAlmostEqual(p2["entry_price"], 100.79, places=2)
            self.assertAlmostEqual(p2["sl_price"], 99.00, places=2)
            self.assertGreaterEqual(p2["sl_price"], orig_sl)

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

            # V5.7 精简版先执行金字塔加仓 (+1.8% -> 均价 ~$101.64)，随后在 $106.00 (+4.3%) 触发 BE26 + 一阶落袋并保留 50% 尾仓
            q_add = {
                "NVDA": {"active_price": 102.00, "vwap": 101.0, "vwap_diff_pct": 1.0, "rsi_5m": 62.0, "rvol_5m": 1.4},
                "MU": {"active_price": 102.00, "vwap": 101.0, "vwap_diff_pct": 1.0, "rsi_5m": 62.0, "rvol_5m": 1.4},
            }
            r_add = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_add, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 10, 30), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_add["addons_now"]), 2)

            q_part = {
                "NVDA": {"active_price": 105.20, "vwap": 102.5, "vwap_diff_pct": 2.6, "rsi_5m": 66.0, "rvol_5m": 1.4},
                "MU": {"active_price": 105.20, "vwap": 102.5, "vwap_diff_pct": 2.6, "rsi_5m": 66.0, "rvol_5m": 1.4},
            }
            r_p = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_part, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 11, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_p["partials_now"]), 2)
            for p in r_p["ledger"]["open_positions"]:
                self.assertTrue(p["partial_tp_done"])
                self.assertTrue(p["be_stop_active"])
                self.assertAlmostEqual(p["sl_price"], round(p["entry_price"] * 1.003, 2), places=2)

            q_peak = {
                "NVDA": {"active_price": 110.00, "vwap": 105.0, "vwap_diff_pct": 4.7, "rsi_5m": 71.0, "rvol_5m": 1.6},
                "MU": {"active_price": 108.00, "vwap": 104.0, "vwap_diff_pct": 3.8, "rsi_5m": 80.5, "rvol_5m": 2.8},
            }
            r_pk = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_peak, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 12, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(r_pk["closed_now"][0]["ticker"], "MU")
            self.assertEqual(r_pk["closed_now"][0]["outcome"], "CLIMAX_TP")

            q_tr = {"NVDA": {"active_price": 107.36, "vwap": 105.5, "vwap_diff_pct": 1.7, "rsi_5m": 58.0, "rvol_5m": 1.1}}
            r_tr = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_tr, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 24, 13, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(r_tr["closed_now"][0]["outcome"], "TRAIL_TP")
            self.assertAlmostEqual(r_tr["ledger"]["cash"], r_tr["ledger"]["total_equity"], places=2)

    def test_04_v551_strict_55_10_gate_and_removed_bear_bounce_and_flip(self):
        """V5.5.1：入场门槛严格 55%/10%；空头期 15% 反抽已彻底关闭；止损后同日反向 FLIP 已彻底关闭。"""
        # 1) 54.9% 或 差值 9.9% 必须被拦截；55.0% 且差值 10.0% 才放行
        ai_gate = {
            "market_regime": "BULL_EXPANSION",
            "by_ticker": {
                "NVDA": {"calibrated_win_rate_pct": 54.9, "calibrated_short_win_rate_pct": 30.0},  # < 55% 拦截
                "AMD": {"calibrated_win_rate_pct": 56.0, "calibrated_short_win_rate_pct": 46.5},   # 差 9.5% < 10% 拦截
                "MU": {"calibrated_win_rate_pct": 55.0, "calibrated_short_win_rate_pct": 45.0},    # 55% & 差10% 放行
            },
        }
        q_gate = {
            "QQQ": {"active_price": 500.0, "pct_change": 0.0},
            "NVDA": {"active_price": 100.0, "prev_close": 99.5, "pct_change": 0.5, "vwap": 99.6, "vwap_diff_pct": 0.4, "rsi_5m": 58.0, "rvol_5m": 1.8},
            "AMD": {"active_price": 100.0, "prev_close": 99.5, "pct_change": 0.5, "vwap": 99.6, "vwap_diff_pct": 0.4, "rsi_5m": 58.0, "rvol_5m": 1.8},
            "MU": {"active_price": 100.0, "prev_close": 99.5, "pct_change": 0.5, "vwap": 99.6, "vwap_diff_pct": 0.4, "rsi_5m": 58.0, "rvol_5m": 1.8},
        }
        anchors = {k: {"s1": 98.0, "r1": 99.8} for k in ("NVDA", "AMD", "MU")}
        cands = kol.screen_kol_sniper_candidates(
            ai_predictions=ai_gate, live_quotes=q_gate, open_positions=[], cur_et=_et(2026, 11, 24, 10, 30),
            daily_sr_anchors=anchors, market_regime="BULL_EXPANSION",
        )
        self.assertEqual([c["ticker"] for c in cands], ["MU"])

        # 2) 空头期即使 NQ=F 站回 VWAP 且个股极度超跌，也不再放行 15% 逆势反抽多单
        ai_bear = {
            "market_regime": "BEAR_DEFENSE",
            "by_ticker": {
                "NVDA": {"calibrated_win_rate_pct": 46.0, "calibrated_short_win_rate_pct": 48.0},
                "TSLA": {"calibrated_win_rate_pct": 25.0, "calibrated_short_win_rate_pct": 63.0},
            },
        }
        vr = {"vix_hard_brake": True, "vix_state": "BACKWARDATION_BRAKE", "panic_clearing_active": False}
        q_bear = {
            "QQQ": {"active_price": 600.0, "pct_change": -2.0},
            "NVDA": {"active_price": 135.0, "prev_close": 141.0, "pct_change": -4.25, "is_deep_pullback": True, "vwap": 135.1, "vwap_diff_pct": -0.07, "rsi_5m": 31.0, "rvol_5m": 1.6},
            "TSLA": {"active_price": 250.0, "prev_close": 254.0, "pct_change": -1.57, "is_broken_bearish": True, "vwap": 250.5, "vwap_diff_pct": -0.2, "rsi_5m": 46.0, "rvol_5m": 1.3},
        }
        c_bear = kol.screen_kol_sniper_candidates(
            ai_predictions=ai_bear, live_quotes=q_bear, open_positions=[], cur_et=_et(2026, 11, 24, 11, 0),
            market_regime="BEAR_DEFENSE", vix_regime=vr,
            futures_data={"NQ=F": {"reclaim_vwap": True, "vwap_diff_pct": 0.2}},
        )
        self.assertEqual([(c["ticker"], c["direction"]) for c in c_bear], [("TSLA", "SHORT")])

        # 3) 止损后同日即使冷却 60 分钟且反向胜率 68%，FLIP 已彻底移除不得触发
        c_no_flip = kol.screen_kol_sniper_candidates(
            ai_predictions={"market_regime": "BEAR_DEFENSE", "by_ticker": {"SOXL": {"calibrated_win_rate_pct": 20.0, "calibrated_short_win_rate_pct": 68.0}}},
            live_quotes={"SOXL": {"active_price": 37.8, "prev_close": 39.5, "pct_change": -4.3, "is_broken_bearish": True, "vwap": 38.8, "vwap_diff_pct": -1.0, "rsi_5m": 38.0, "rvol_5m": 2.0}},
            open_positions=[],
            cur_et=_et(2026, 11, 24, 11, 20),
            market_regime="BEAR_DEFENSE",
            last_exit_by_ticker={"SOXL": {"direction": "LONG", "exit_date": "2026-11-24", "exit_time_et": "2026-11-24 10:15:00", "exit_reason": "SL_HIT"}},
        )
        self.assertEqual(len(c_no_flip), 0)

    def test_05_patch_d_v2_weak_early_exit_deferred_to_t2_with_late_entry_buffer(self):
        """补丁 D-v2：T+1 不触发早撤；T+2 11:30 ET 常规单触发 WEAK_EARLY_EXIT，14:00 后尾盘仓顺延至 T+2 13:30 ET。"""
        with tempfile.TemporaryDirectory() as td:
            lp = os.path.join(td, "kol.json")
            ai = {"market_regime": "BULL_EXPANSION", "by_ticker": {
                "NVDA": {"calibrated_win_rate_pct": 62.0, "calibrated_short_win_rate_pct": 28.0},
                "AMD": {"calibrated_win_rate_pct": 61.0, "calibrated_short_win_rate_pct": 29.0},
            }}
            q1 = {
                "QQQ": {"active_price": 500.0, "pct_change": 0.1},
                "NVDA": {"active_price": 100.0, "prev_close": 99.5, "pct_change": 0.5, "vwap": 99.6, "vwap_diff_pct": 0.4, "rsi_5m": 58.0, "rvol_5m": 1.6},
            }
            # Day 0 (Mon 11-23): 10:30 开 NVDA，14:30 开 AMD
            kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q1, daily_sr_anchors={"NVDA": {"s1": 98.0, "r1": 99.8}},
                market_regime="BULL_EXPANSION", session_code="REGULAR",
                now_et=_et(2026, 11, 23, 10, 30), ledger_path=lp, dry_run=False,
            )
            q2 = {
                "QQQ": {"active_price": 500.0, "pct_change": 0.1},
                "NVDA": {"active_price": 100.5, "prev_close": 99.5, "pct_change": 1.0, "vwap": 100.2, "vwap_diff_pct": 0.3, "rsi_5m": 55.0, "rvol_5m": 1.2},
                "AMD": {"active_price": 150.0, "prev_close": 149.0, "pct_change": 0.67, "vwap": 149.5, "vwap_diff_pct": 0.3, "rsi_5m": 57.0, "rvol_5m": 1.7},
            }
            kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q2, daily_sr_anchors={"AMD": {"s1": 147.0, "r1": 149.8}},
                market_regime="BULL_EXPANSION", session_code="REGULAR",
                now_et=_et(2026, 11, 23, 14, 30), ledger_path=lp, dry_run=False,
            )

            q_weak = {
                "NVDA": {"active_price": 99.6, "vwap": 100.1, "vwap_diff_pct": -0.5, "rsi_5m": 44.0, "rvol_5m": 1.0},
                "AMD": {"active_price": 149.5, "vwap": 150.2, "vwap_diff_pct": -0.47, "rsi_5m": 44.0, "rvol_5m": 1.0},
            }
            # T+1 (Tue 11-24) 14:00 ET：两笔均未满 T+2 -> 绝不早撤！
            r_t1 = kol.run_kol_copy_trade_cycle(
                ai_predictions={"market_regime": "NEUTRAL_CHOP", "by_ticker": {}}, live_quotes=q_weak,
                market_regime="NEUTRAL_CHOP", session_code="REGULAR",
                now_et=_et(2026, 11, 24, 14, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_t1["closed_now"]), 0)

            # T+2 (Wed 11-25) 11:40 ET：NVDA 触发 WEAK_EARLY_EXIT；AMD 尾盘仓顺延至 13:30 ET 继续保留！
            r_t2_am = kol.run_kol_copy_trade_cycle(
                ai_predictions={"market_regime": "NEUTRAL_CHOP", "by_ticker": {}}, live_quotes=q_weak,
                market_regime="NEUTRAL_CHOP", session_code="REGULAR",
                now_et=_et(2026, 11, 25, 11, 40), ledger_path=lp, dry_run=False,
            )
            self.assertEqual([c["ticker"] for c in r_t2_am["closed_now"]], ["NVDA"])
            self.assertEqual(r_t2_am["closed_now"][0]["outcome"], "WEAK_EARLY_EXIT")
            self.assertEqual([p["ticker"] for p in r_t2_am["ledger"]["open_positions"]], ["AMD"])

            # T+2 13:20 ET AMD 仍受缓冲保护；13:35 ET 触发 WEAK_EARLY_EXIT
            r_t2_mid = kol.run_kol_copy_trade_cycle(
                ai_predictions={"market_regime": "NEUTRAL_CHOP", "by_ticker": {}}, live_quotes=q_weak,
                market_regime="NEUTRAL_CHOP", session_code="REGULAR",
                now_et=_et(2026, 11, 25, 13, 20), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_t2_mid["closed_now"]), 0)

            r_t2_pm = kol.run_kol_copy_trade_cycle(
                ai_predictions={"market_regime": "NEUTRAL_CHOP", "by_ticker": {}}, live_quotes=q_weak,
                market_regime="NEUTRAL_CHOP", session_code="REGULAR",
                now_et=_et(2026, 11, 25, 13, 35), ledger_path=lp, dry_run=False,
            )
            self.assertEqual([c["ticker"] for c in r_t2_pm["closed_now"]], ["AMD"])
            self.assertEqual(r_t2_pm["closed_now"][0]["outcome"], "WEAK_EARLY_EXIT")

    def test_06_extended_hours_anti_wick_2_poll_confirm_reset_and_no_new_entries(self):
        """V5.5.1 延展时段防插针保护止损：第1轮击穿不平仓、回弹清零、连续2轮(>=60s)击穿触发平仓；延展时段严禁开新仓或加仓。"""
        with tempfile.TemporaryDirectory() as td:
            lp = os.path.join(td, "kol_ext.json")
            ai = {"market_regime": "BULL_EXPANSION", "by_ticker": {
                "NVDA": {"calibrated_win_rate_pct": 64.0, "calibrated_short_win_rate_pct": 25.0},
                "MU": {"calibrated_win_rate_pct": 64.0, "calibrated_short_win_rate_pct": 25.0},
            }}
            q_open = {
                "QQQ": {"active_price": 500.0, "pct_change": 0.2},
                "NVDA": {"active_price": 100.0, "prev_close": 99.5, "pct_change": 0.5, "day_low": 97.5, "vwap": 99.6, "vwap_diff_pct": 0.4, "rsi_5m": 58.0, "rvol_5m": 1.8},
            }
            # 1) 盘中正常开 1 笔 NVDA (SL ~ $96.91)
            r0 = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_open, daily_sr_anchors={"NVDA": {"s1": 98.0, "r1": 99.8}},
                market_regime="BULL_EXPANSION", market_session="REGULAR",
                now_et=_et(2026, 11, 24, 14, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r0["opened_now"]), 1)
            sl_px = r0["opened_now"][0]["sl_price"]

            # 2) 盘后 (AFTER_HOURS) 强涨至 $103.00 且 MU 也满足开仓条件 -> 严禁加仓、严禁开新仓！
            q_ext_rally = {
                "QQQ": {"ext_price": 505.0, "active_price": 505.0, "pct_change": 1.0},
                "NVDA": {"ext_price": 103.0, "active_price": 103.0, "vwap": 100.0, "vwap_diff_pct": 3.0, "rsi_5m": 65.0, "rvol_5m": 1.8},
                "MU": {"ext_price": 100.0, "active_price": 100.0, "prev_close": 99.0, "pct_change": 1.0, "vwap": 99.5, "vwap_diff_pct": 0.5, "rsi_5m": 58.0, "rvol_5m": 1.9},
            }
            r_no_act = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_ext_rally, daily_sr_anchors={"MU": {"s1": 98.0, "r1": 99.8}},
                market_regime="BULL_EXPANSION", market_session="AFTER_HOURS",
                now_et=_et(2026, 11, 24, 16, 5, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_no_act["opened_now"]), 0)
            self.assertEqual(len(r_no_act["addons_now"]), 0)
            self.assertEqual(len(r_no_act["closed_now"]), 0)

            # 3) 盘后 16:10:00 第 1 轮假插针击穿止损 -> 记录 count=1，绝不立即平仓！
            q_wick = {"NVDA": {"ext_price": sl_px - 0.50, "active_price": sl_px - 0.50, "has_realtime": True}}
            r_b1 = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_wick, market_regime="BULL_EXPANSION",
                market_session="AFTER_HOURS", now_et=_et(2026, 11, 24, 16, 10, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_b1["closed_now"]), 0)
            self.assertEqual(r_b1["ledger"]["open_positions"][0]["ext_sl_breach_count"], 1)

            # 4) 仅隔 40 秒 (< 60s) 仍击穿 -> 尚未满 60 秒间隔，不提前平仓
            r_too_fast = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_wick, market_regime="BULL_EXPANSION",
                market_session="AFTER_HOURS", now_et=_et(2026, 11, 24, 16, 10, 40), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_too_fast["closed_now"]), 0)

            # 5) 16:11:30 价格回弹至安全区 -> 计数器自动清零！
            q_safe = {"NVDA": {"ext_price": sl_px + 0.80, "active_price": sl_px + 0.80, "has_realtime": True}}
            r_reset = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_safe, market_regime="BULL_EXPANSION",
                market_session="AFTER_HOURS", now_et=_et(2026, 11, 24, 16, 11, 30), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_reset["closed_now"]), 0)
            self.assertEqual(r_reset["ledger"]["open_positions"][0]["ext_sl_breach_count"], 0)
            self.assertEqual(r_reset["ledger"]["open_positions"][0]["ext_sl_first_breach_ts"], "")

            # 6) 陈旧停盘价 (is_stale=True) 即使低于止损也不得计入击穿
            q_stale = {"NVDA": {"ext_price": sl_px - 2.0, "is_stale": True}}
            r_stale = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_stale, market_regime="BULL_EXPANSION",
                market_session="OVERNIGHT", now_et=_et(2026, 11, 24, 21, 0, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(r_stale["ledger"]["open_positions"][0]["ext_sl_breach_count"], 0)

            # 7) 盘前 (PRE_MARKET) 08:15:00 再次真实击穿 (count=1) -> 08:16:05 (间隔 65s >= 60s) 第 2 轮确认 -> 正式 SL_HIT 平仓！
            r_real1 = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_wick, market_regime="BULL_EXPANSION",
                market_session="PRE_MARKET", now_et=_et(2026, 11, 25, 8, 15, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_real1["closed_now"]), 0)
            self.assertEqual(r_real1["ledger"]["open_positions"][0]["ext_sl_breach_count"], 1)

            r_real2 = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_wick, market_regime="BULL_EXPANSION",
                market_session="PRE_MARKET", now_et=_et(2026, 11, 25, 8, 16, 5), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_real2["closed_now"]), 1)
            tr = r_real2["closed_now"][0]
            self.assertEqual(tr["outcome"], "SL_HIT")
            self.assertTrue(tr["ext_confirmed_close"])
            self.assertEqual(tr["exit_session"], "PRE_MARKET")
            self.assertEqual(len(r_real2["ledger"]["open_positions"]), 0)

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
            self.assertIn("V5.7 精简 KOL 轨", emb_s)
            self.assertNotIn("建议股数", emb_s)
            self.assertNotIn("\U0001fa99", emb_s)

            drawer = kol.render_dual_track_copy_drawer_html(steady_path=sp, kol_path=lp, drawer_id="dualDrawerTest")
            self.assertIn("V5.7 精简 KOL 狙击轨", drawer)
            self.assertIn("V5.4 稳健基准轨", drawer)
            self.assertIn("<svg", drawer)
            self.assertNotIn("建议股数", drawer)
            self.assertNotIn("\U0001fa99", drawer)

    def test_08_copy_trade_webhook_routing_and_fallback(self):
        """路由分流单测：配置 DISCORD_COPY_TRADE_WEBHOOK_URL 时走专属通道，未配置时回退原通道，干跑严格拦截。"""
        from unittest import mock
        import integrated_market_radar as imr

        orig_copy = os.environ.get("DISCORD_COPY_TRADE_WEBHOOK_URL")
        orig_claw = os.environ.get("DISCORD_CLAW_WEBHOOK_URL")
        orig_dry = os.environ.get("RADAR_DISABLE_EXTERNAL_ALERTS")
        try:
            # 1) 配置专属 Webhook -> 走新通道，原买点频道不变
            os.environ["DISCORD_CLAW_WEBHOOK_URL"] = "https://discord.com/api/webhooks/111/claw_default"
            os.environ["DISCORD_COPY_TRADE_WEBHOOK_URL"] = "https://discord.com/api/webhooks/222/copy_only"
            os.environ.pop("RADAR_DISABLE_EXTERNAL_ALERTS", None)
            self.assertEqual(imr.get_copy_trade_webhook_url(), "https://discord.com/api/webhooks/222/copy_only")
            self.assertEqual(imr.get_claw_webhook_url(), "https://discord.com/api/webhooks/111/claw_default")

            with mock.patch.object(imr, "send_discord_claw_alert", return_value=True) as m_send:
                ok = imr.send_discord_copy_trade_alert("测试标题", "测试内容", dedupe_key="k1", color_override=0x10B981)
                self.assertTrue(ok)
                m_send.assert_called_once_with(
                    title="测试标题",
                    markdown_content="测试内容",
                    webhook_url="https://discord.com/api/webhooks/222/copy_only",
                    dedupe_key="k1",
                    dedupe_max_age=86400,
                    color_override=0x10B981,
                )

            # 2) 未配置专属 Webhook -> 自动回退至原 DISCORD_CLAW_WEBHOOK_URL
            os.environ["DISCORD_COPY_TRADE_WEBHOOK_URL"] = ""
            self.assertEqual(imr.get_copy_trade_webhook_url(), "https://discord.com/api/webhooks/111/claw_default")
            with mock.patch.object(imr, "send_discord_claw_alert", return_value=True) as m_fb:
                imr.send_discord_copy_trade_alert("回退测试", "内容", dedupe_key="k2")
                self.assertEqual(m_fb.call_args.kwargs["webhook_url"], "https://discord.com/api/webhooks/111/claw_default")

            # 3) 干跑模式 -> 硬拦截零外发
            os.environ["RADAR_DISABLE_EXTERNAL_ALERTS"] = "1"
            with mock.patch.object(imr, "send_discord_claw_alert", return_value=True) as m_dry:
                self.assertFalse(imr.send_discord_copy_trade_alert("干跑拦截", "内容", dedupe_key="k3"))
                m_dry.assert_not_called()
        finally:
            for k, v in (
                ("DISCORD_COPY_TRADE_WEBHOOK_URL", orig_copy),
                ("DISCORD_CLAW_WEBHOOK_URL", orig_claw),
                ("RADAR_DISABLE_EXTERNAL_ALERTS", orig_dry),
            ):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_09_live_radar_field_compatibility_corridor_dict_rth_low_pml_broken_bearish(self):
        """验证线上主雷达真实字段命名 (divergence dict / rth_low / pml / pmh / broken_bearish) 完全生效。"""
        # 1) ABOVE_P75 嵌套字典触发顺势突破多（即使 R1 很远）
        ai_long = {"market_regime": "BULL_EXPANSION", "by_ticker": {
            "NVDA": {"calibrated_win_rate_pct": 66.0, "calibrated_short_win_rate_pct": 25.0},
        }}
        q_break = {
            "QQQ": {"active_price": 500.0, "pct_change": 0.4},
            "NVDA": {
                "active_price": 102.0, "prev_close": 100.0, "pct_change": 2.0,
                "vwap": 101.2, "vwap_diff_pct": 0.79, "rsi_5m": 62.0, "rvol_5m": 1.8,
                "rth_low": 99.6, "rth_high": 102.3,
            },
        }
        ip_p75 = {"by_ticker": {"NVDA": {"divergence": {"status": "ABOVE_P75"}, "pml": 95.0, "pmh": 101.5}}}
        c_break = kol.screen_kol_sniper_candidates(
            ai_predictions=ai_long, live_quotes=q_break, open_positions=[],
            cur_et=_et(2026, 11, 25, 10, 15), market_regime="BULL_EXPANSION",
            daily_sr_anchors={"NVDA": {"s1": 90.0, "r1": 120.0}},
            intraday_path_payload=ip_p75,
        )
        self.assertEqual(len(c_break), 1)
        self.assertEqual(c_break[0]["setup"], "BREAKOUT_MOMENTUM_LONG")
        # 结构止损应锚定 rth_low=99.6 / pml=99.5 外侧 0.6% -> ~99.00 (约 -2.94%，落在 [2%, 4.5%])
        self.assertAlmostEqual(c_break[0]["sl_price"], round(99.6 * (1.0 - 0.006), 2), places=2)

        # 2) rth_low 扫穿 pml 后收回触发 SWEEP_RECLAIM_LONG（无 S1 靠近）
        q_sweep = {
            "QQQ": {"active_price": 500.0, "pct_change": -0.3},
            "NVDA": {
                "active_price": 100.2, "prev_close": 100.0, "pct_change": 0.2,
                "vwap": 100.1, "vwap_diff_pct": 0.10, "rsi_5m": 48.0, "rvol_5m": 1.4,
                "rth_low": 99.2,
            },
        }
        ip_pml = {"by_ticker": {"NVDA": {"divergence": {"status": "IN_CORRIDOR"}, "pml": 99.5}}}
        c_sweep = kol.screen_kol_sniper_candidates(
            ai_predictions=ai_long, live_quotes=q_sweep, open_positions=[],
            cur_et=_et(2026, 11, 25, 10, 20), market_regime="BULL_EXPANSION",
            daily_sr_anchors={"NVDA": {"s1": 88.0, "r1": 120.0}},
            intraday_path_payload=ip_pml,
        )
        self.assertEqual(len(c_sweep), 1)
        self.assertEqual(c_sweep[0]["setup"], "SWEEP_RECLAIM_LONG")

        # 3) 牛市微涨日依靠 BELOW_P25 字典或 broken_bearish=True 触发 REBOUND_REJECT_SHORT，且空单止损锚定 rth_high/pmh
        ai_short = {"market_regime": "BULL_EXPANSION", "by_ticker": {
            "META": {"calibrated_win_rate_pct": 20.0, "calibrated_short_win_rate_pct": 64.0},
            "AMD": {"calibrated_win_rate_pct": 20.0, "calibrated_short_win_rate_pct": 62.0},
        }}
        q_short = {
            "QQQ": {"active_price": 500.0, "pct_change": 0.5},
            "META": {
                "active_price": 500.0, "prev_close": 499.0, "pct_change": 0.2,
                "vwap": 501.0, "vwap_diff_pct": -0.20, "rsi_5m": 44.0, "rvol_5m": 1.0,
                "rth_high": 511.0,
            },
            "AMD": {
                "active_price": 200.0, "prev_close": 199.5, "pct_change": 0.25,
                "vwap": 200.5, "vwap_diff_pct": -0.25, "rsi_5m": 45.0, "rvol_5m": 1.0,
                "broken_bearish": True, "rth_high": 205.0,
            },
        }
        ip_p25 = {"by_ticker": {
            "META": {"divergence": {"status": "BELOW_P25"}, "pmh": 515.0},
            "AMD": {"divergence": {"status": "IN_CORRIDOR"}, "pmh": 206.0},
        }}
        c_short = kol.screen_kol_sniper_candidates(
            ai_predictions=ai_short, live_quotes=q_short, open_positions=[],
            cur_et=_et(2026, 11, 25, 10, 30), market_regime="BULL_EXPANSION",
            daily_sr_anchors={"META": {"s1": 470.0, "r1": 560.0}, "AMD": {"s1": 180.0, "r1": 240.0}},
            intraday_path_payload=ip_p25,
        )
        self.assertEqual([c["ticker"] for c in c_short], ["META", "AMD"])
        self.assertAlmostEqual(c_short[0]["sl_price"], round(511.0 * (1.0 + 0.006), 2), places=2)


if __name__ == "__main__":
    unittest.main()
