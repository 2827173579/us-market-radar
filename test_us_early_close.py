# -*- coding: utf-8 -*-
"""
美股半日市 (Early Close) 自动适配隔离单测 (test_us_early_close.py)
覆盖 2026-11-27 黑色星期五 (13:00 ET 收盘 / 17:00 ET 盘后结束)、12-24 平安夜、07-03 独立日前日：
1. 统一日历源判定；
2. V5.4 T+5 强平与禁开新仓于 12:50 ET 触发；
3. V5.5.1 T+3 强平于 12:50 ET 触发、尾盘禁开新仓、13:01 ET 切入 AFTER_HOURS；
4. 主雷达 get_us_market_session / AI 盘后重算 (13:05 ET) / 60d 5m 缓存刷新 (13:15 ET) / 走廊锁定 (13:01 ET)。
"""

import json
import os
import tempfile
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import ai_commentary_engine as ace
import copy_trade_engine as cte
import copy_trade_kol_engine as kol
import integrated_market_radar as imr
import intraday_path_predictor as ipp
import us_market_calendar as umc

ET = ZoneInfo("America/New_York")


def _et(y: int, m: int, d: int, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=ET)


class TestUsEarlyCloseAdaptation(unittest.TestCase):
    def test_01_calendar_identifies_black_friday_christmas_eve_and_july3(self):
        # 2026-11-27 黑色星期五 & 2026-12-24 平安夜 -> 半日市 (13:00 / 17:00 ET)
        self.assertTrue(umc.is_us_early_close_day(date(2026, 11, 27)))
        self.assertEqual(umc.get_us_market_close_minutes(date(2026, 11, 27)), 13 * 60)
        self.assertEqual(umc.get_us_after_hours_end_minutes(date(2026, 11, 27)), 17 * 60)

        self.assertTrue(umc.is_us_early_close_day(date(2026, 12, 24)))
        self.assertEqual(umc.get_us_market_close_minutes(date(2026, 12, 24)), 13 * 60)

        # 2025-07-03 & 2028-07-03 为半日市；2026-07-03 为独立日调休全天休市 (非半日市)
        self.assertTrue(umc.is_us_early_close_day(date(2025, 7, 3)))
        self.assertTrue(umc.is_us_early_close_day(date(2028, 7, 3)))
        self.assertFalse(umc.is_us_trading_day(date(2026, 7, 3)))
        self.assertFalse(umc.is_us_early_close_day(date(2026, 7, 3)))

        # 正常交易日 2026-11-25 -> 16:00 / 20:00 ET
        self.assertFalse(umc.is_us_early_close_day(date(2026, 11, 25)))
        self.assertEqual(umc.get_us_market_close_minutes(date(2026, 11, 25)), 16 * 60)
        self.assertEqual(umc.get_us_after_hours_end_minutes(date(2026, 11, 25)), 20 * 60)

    def test_02_v54_t5_force_exit_and_entry_cutoff_at_1250_et_on_2026_11_27(self):
        with tempfile.TemporaryDirectory() as td:
            lp = os.path.join(td, "v54_halfday.json")
            ai = {"market_regime": "BULL_EXPANSION", "by_ticker": {
                "NVDA": {"calibrated_win_rate_pct": 68.0, "calibrated_short_win_rate_pct": 20.0},
                "MSFT": {"calibrated_win_rate_pct": 66.0, "calibrated_short_win_rate_pct": 22.0},
            }}
            q = {
                "NVDA": {"active_price": 105.0, "prev_close": 104.0, "pct_change": 0.96},
                "MSFT": {"active_price": 400.0, "prev_close": 398.0, "pct_change": 0.5},
            }
            # 构造 1 笔到期日恰为 2026-11-27 的持仓
            ldg = cte.create_empty_copy_ledger(now_et=_et(2026, 11, 19, 10, 0))
            ldg["cash"] = 750.0
            ldg["last_entry_date_et"] = "2026-11-19"
            ldg["open_positions"] = [{
                "trade_id": "NVDA_LONG_2026-11-19",
                "ticker": "NVDA",
                "direction": "LONG",
                "slot_weight": 0.25,
                "allocated_capital": 250.0,
                "entry_date": "2026-11-19",
                "entry_time_et": "2026-11-19 10:00:00",
                "entry_price": 100.0,
                "last_price": 105.0,
                "tp_price": 115.0,
                "sl_price": 95.0,
                "expiry_date": "2026-11-27",
                "calibrated_prob_pct": 68.0,
            }]
            cte.save_copy_ledger(ldg, lp)

            # 12:49 ET -> 尚未到 12:50 强平线，NVDA 仍持有
            r_49 = cte.run_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 27, 12, 49), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_49["closed"]), 0)

            # 12:50 ET -> 立即触发 T5_EXPIRE 强平，且尾盘禁开新仓（MSFT 不得在 12:50 后开仓）
            # 先把 last_entry_date_et 置旧以确保唯一拦截来源是 12:50 尾盘禁开窗
            cur_l = cte.load_copy_ledger(lp)
            cur_l["last_entry_date_et"] = "2026-11-25"
            cur_l["open_positions"] = [p for p in cur_l["open_positions"] if p["ticker"] == "NVDA"]
            cte.save_copy_ledger(cur_l, lp)

            r_50 = cte.run_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 27, 12, 50), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_50["closed"]), 1)
            self.assertEqual(r_50["closed"][0]["exit_reason"], "T5_EXPIRE")
            self.assertEqual(r_50["closed"][0]["outcome"], "T5_WIN")
            self.assertEqual(len(r_50["opened"]), 0)

    def test_03_v551_t3_force_exit_at_1250_and_after_hours_at_1301_on_2026_11_27(self):
        with tempfile.TemporaryDirectory() as td:
            lp = os.path.join(td, "v551_halfday.json")
            ai = {"market_regime": "BULL_EXPANSION", "by_ticker": {
                "NVDA": {"calibrated_win_rate_pct": 68.0, "calibrated_short_win_rate_pct": 20.0},
                "MU": {"calibrated_win_rate_pct": 66.0, "calibrated_short_win_rate_pct": 22.0},
            }}
            q = {
                "QQQ": {"active_price": 500.0, "pct_change": 0.4},
                "NVDA": {"active_price": 103.0, "prev_close": 102.0, "pct_change": 0.98, "vwap": 102.5, "vwap_diff_pct": 0.49, "rsi_5m": 58.0, "rvol_5m": 1.8},
                "MU": {"active_price": 100.0, "prev_close": 99.0, "pct_change": 1.0, "vwap": 99.5, "vwap_diff_pct": 0.5, "rsi_5m": 60.0, "rvol_5m": 2.0, "anchor_r1": 99.8},
            }
            ldg = kol.create_empty_kol_ledger(now_et=_et(2026, 11, 23, 10, 0))
            ldg["cash"] = 700.0
            ldg["open_positions"] = [{
                "trade_id": "KOL_NVDA_LONG_2026-11-23_1",
                "ticker": "NVDA",
                "direction": "LONG",
                "setup": "BREAKOUT_MOMENTUM_LONG",
                "entry_tag": "NORMAL",
                "stage": 1,
                "can_add": False,
                "slot_weight": 0.3,
                "peak_slot_weight": 0.3,
                "allocated_capital": 300.0,
                "entry_date": "2026-11-23",
                "entry_time_et": "2026-11-23 10:00:00",
                "orig_entry_price": 100.0,
                "entry_price": 100.0,
                "last_price": 103.0,
                "best_price": 103.0,
                "sl_price": 96.5,
                "partial_tp_done": False,
                "be_stop_active": False,
                "expiry_date": "2026-11-27",
                "calibrated_prob_pct": 68.0,
            }]
            kol.save_kol_ledger(ldg, lp)

            # 12:49 ET -> 未到 12:50 强平线，且已过半日市尾盘禁开窗 (12:20 ET)，MU 不开新仓
            r_49 = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 27, 12, 49), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_49["closed_now"]), 0)
            self.assertEqual(len(r_49["opened_now"]), 0)

            # 12:50 ET -> 立即触发 T3_EXPIRE 强平，且严禁新开仓
            r_50 = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q, market_regime="BULL_EXPANSION",
                session_code="REGULAR", now_et=_et(2026, 11, 27, 12, 50), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_50["closed_now"]), 1)
            self.assertEqual(r_50["closed_now"][0]["exit_reason"], "T3_EXPIRE")
            self.assertEqual(r_50["closed_now"][0]["outcome"], "T3_WIN")
            self.assertEqual(len(r_50["opened_now"]), 0)

            # 13:01 ET -> 无论是自动推断还是误传 REGULAR，均切入 AFTER_HOURS（非 REGULAR）
            is_reg1, is_ext1, code1 = kol._classify_market_session("", _et(2026, 11, 27, 13, 1))
            self.assertFalse(is_reg1)
            self.assertTrue(is_ext1)
            self.assertEqual(code1, "AFTER_HOURS")

            is_reg2, is_ext2, code2 = kol._classify_market_session("REGULAR", _et(2026, 11, 27, 13, 1))
            self.assertFalse(is_reg2)
            self.assertTrue(is_ext2)
            self.assertEqual(code2, "AFTER_HOURS")

    def test_04_session_corridor_and_postclose_refresh_schedules_on_2026_11_27(self):
        # 1) 统一日历源与主雷达会话切换
        s_1259, _ = umc.classify_us_market_session(_et(2026, 11, 27, 12, 59))
        s_1301, _ = umc.classify_us_market_session(_et(2026, 11, 27, 13, 1))
        s_1700, _ = umc.classify_us_market_session(_et(2026, 11, 27, 17, 0))
        self.assertEqual(s_1259, "REGULAR")
        self.assertEqual(s_1301, "AFTER_HOURS")
        self.assertEqual(s_1700, "WEEKEND")

        r_1259, _ = imr.get_us_market_session(_et(2026, 11, 27, 12, 59))
        r_1301, _ = imr.get_us_market_session(_et(2026, 11, 27, 13, 1))
        r_1700, _ = imr.get_us_market_session(_et(2026, 11, 27, 17, 0))
        self.assertEqual(r_1259, "REGULAR")
        self.assertEqual(r_1301, "POST_MARKET")
        self.assertEqual(r_1700, "WEEKEND")

        # 2) 主雷达盘后 AI 重算锚点：13:04 ET 尚未触发，13:05 ET 触发今日 2026-11-27
        self.assertNotEqual(imr._latest_closed_us_day_et(_et(2026, 11, 27, 13, 4)), date(2026, 11, 27))
        self.assertEqual(imr._latest_closed_us_day_et(_et(2026, 11, 27, 13, 5)), date(2026, 11, 27))

        # 3) 60 日 5m 缓存刷新：13:14 ET False，13:15 ET True
        with tempfile.TemporaryDirectory() as td:
            pq_p = os.path.join(td, "intraday.parquet")
            meta_p = os.path.join(td, "meta.json")
            with open(pq_p, "wb") as f:
                f.write(b"PAR1")
            with open(meta_p, "w", encoding="utf-8") as f:
                json.dump({"last_refresh_date": "2026-11-27", "last_refresh_slot": "premarket"}, f)
            self.assertFalse(ipp.should_refresh_60d_cache(pq_p, meta_p, now_et=_et(2026, 11, 27, 13, 14)))
            self.assertTrue(ipp.should_refresh_60d_cache(pq_p, meta_p, now_et=_et(2026, 11, 27, 13, 15)))

        # 4) 盘中推演走廊锁定模式：12:55 ET INTRADAY_LOCKED，13:01 ET 切入 POST_MARKET_LOCKED
        ex_doc = {"forecast_date": "2026-11-27"}
        _, _, mode_1255 = ipp._resolve_target_date_and_lock(None, ex_doc, _et(2026, 11, 27, 12, 55))
        _, _, mode_1301 = ipp._resolve_target_date_and_lock(None, ex_doc, _et(2026, 11, 27, 13, 1))
        self.assertEqual(mode_1255, "INTRADAY_LOCKED")
        self.assertEqual(mode_1301, "POST_MARKET_LOCKED")

        # 5) V5.1 AI 解盘自动刷新窗口：13:15 ET True，13:16 ET False
        self.assertTrue(ace._is_in_web_auto_window(_et(2026, 11, 27, 13, 15)))
        self.assertFalse(ace._is_in_web_auto_window(_et(2026, 11, 27, 13, 16)))


if __name__ == "__main__":
    unittest.main()
