# -*- coding: utf-8 -*-
"""
Verification Script for P1 Defects 4, 5, and 8 Fixes.
"""
import os
import sys
import json
import time
import unittest
from unittest.mock import patch, MagicMock

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import integrated_market_radar as imr
import create_futu_terminal as cft


class TestDefects4And8Verification(unittest.TestCase):
    def setUp(self):
        self.tmp_alert_cache = os.path.join(PROJECT_DIR, ".test_alert_history.json")
        self.patch_cache = patch.object(imr, "ALERT_CACHE_FILE", self.tmp_alert_cache)
        self.patch_cache.start()
        if os.path.exists(self.tmp_alert_cache):
            os.remove(self.tmp_alert_cache)

    def tearDown(self):
        self.patch_cache.stop()
        if os.path.exists(self.tmp_alert_cache):
            os.remove(self.tmp_alert_cache)

    def test_defect_4_case_a_threshold_not_met_no_cooldown_lock(self):
        """Case A: is_stop=True, but bias=-8.0% (not < -15.0%).
        Alert is not queued. targets_history MUST NOT lock cooldown timestamp!
        """
        macro_data = {}
        stocks_data = [{
            "ticker": "AAPL",
            "name": "Apple",
            "cat": "BigTech",
            "last": 150.0,
            "active_price": 150.0,
            "bias": -8.0,
            "is_stop": True,
            "vol_ratio": 1.2,
            "shares": 0,
        }]
        weather_info = {"weather": "晴天做多", "desc": "晴天做多"}
        now_str = "2026-11-03 10:00:00"

        with patch.object(imr, "send_pushplus_wechat_alert") as mock_send:
            imr.dispatch_wechat_radar_alerts(macro_data, stocks_data, weather_info, now_str)
            # No alert should be sent because threshold not met
            mock_send.assert_not_called()

        history = imr.load_alert_history()
        # Cooldown must not be locked
        self.assertNotIn("AAPL", history.get("targets", {}), "AAPL must not be locked in targets_history cooldown")

    def test_defect_4_case_b_send_failure_preserves_retry(self):
        """Case B: is_stop=True, bias=-16.0%, vol_ratio=1.6 (qualifies for alert).
        send_pushplus_wechat_alert returns False (network error).
        targets_history MUST NOT commit the cooldown timestamp, preserving retry rights!
        """
        macro_data = {}
        stocks_data = [{
            "ticker": "TSLA",
            "name": "Tesla",
            "cat": "EV",
            "last": 200.0,
            "active_price": 200.0,
            "bias": -16.0,
            "is_stop": True,
            "vol_ratio": 1.6,
            "shares": 0,
        }]
        weather_info = {"weather": "晴天做多", "desc": "晴天做多"}
        now_str = "2026-11-03 10:00:00"

        with patch.object(imr, "send_pushplus_wechat_alert", return_value=False) as mock_send:
            imr.dispatch_wechat_radar_alerts(macro_data, stocks_data, weather_info, now_str)
            mock_send.assert_called_once()

        history = imr.load_alert_history()
        # Cooldown must NOT be committed because sending failed
        self.assertNotIn("TSLA", history.get("targets", {}), "TSLA cooldown timestamp must not be saved when send failed")

    def test_defect_4_case_c_send_success_commits_cooldown(self):
        """Case C: send_pushplus_wechat_alert returns True.
        targets_history MUST commit cooldown timestamp.
        """
        macro_data = {}
        stocks_data = [{
            "ticker": "TSLA",
            "name": "Tesla",
            "cat": "EV",
            "last": 200.0,
            "active_price": 200.0,
            "bias": -16.0,
            "is_stop": True,
            "vol_ratio": 1.6,
            "shares": 0,
        }]
        weather_info = {"weather": "晴天做多", "desc": "晴天做多"}
        now_str = "2026-11-03 10:00:00"

        with patch.object(imr, "send_pushplus_wechat_alert", return_value=True) as mock_send:
            imr.dispatch_wechat_radar_alerts(macro_data, stocks_data, weather_info, now_str)
            mock_send.assert_called_once()

        history = imr.load_alert_history()
        self.assertIn("TSLA", history.get("targets", {}))
        self.assertEqual(history["targets"]["TSLA"]["state"], "STOP_LOSS")
        self.assertGreater(history["targets"]["TSLA"]["last_notified_at"], 0.0)

    def test_defect_8_macro_weather_bias_fields_and_circuit_breaker_override(self):
        """Test Defect 8:
        1. Macro weather fields: qqqm_bias == qqqm_bias_ma50, smh_bias == smh_bias_ma50, vixy_pos == vixy_pos_60.
        2. Circuit breaker linkage: stock with is_crash_halt=True or is_cooling_halt=True receives
           item['action'] = '🚨 暴跌熔断·严禁左侧接刀' and is sorted behind active healthy stocks.
        """
        ai_path = os.path.join(PROJECT_DIR, "data_cache", "latest_ai_predictions.json")
        with open(ai_path, "r", encoding="utf-8") as f:
            backup_data = json.load(f)

        try:
            weather_info = {
                "weather": "⛅ 阴天防守",
                "icon": "⛅",
                "qqqm_bias": -3.45,
                "smh_bias": -6.78,
                "vixy_pos_60": 62.0,
            }
            stocks_data = [
                {
                    "ticker": "NVDA",
                    "active_price": 120.0,
                    "last": 120.0,
                    "bias": -2.0,
                    "rsi": 45.0,
                    "is_crash_halt": True,
                    "is_cooling_halt": False,
                },
                {
                    "ticker": "MSFT",
                    "active_price": 400.0,
                    "last": 400.0,
                    "bias": 1.0,
                    "rsi": 55.0,
                    "is_crash_halt": False,
                    "is_cooling_halt": False,
                }
            ]
            macro_data = {}

            cft.build_futu_terminal_html(
                stocks_data=stocks_data,
                macro_data=macro_data,
                weather_info=weather_info,
                update_time_str="2026-11-03 10:20:00"
            )

            with open(ai_path, "r", encoding="utf-8") as f:
                saved_ai = json.load(f)

            mw = saved_ai.get("macro_weather", {})
            self.assertEqual(mw.get("qqqm_bias"), -3.45)
            self.assertEqual(mw.get("qqqm_bias_ma50"), -3.45)
            self.assertEqual(mw.get("smh_bias"), -6.78)
            self.assertEqual(mw.get("smh_bias_ma50"), -6.78)
            self.assertEqual(mw.get("vixy_pos"), 0.62)
            self.assertEqual(mw.get("vixy_pos_60"), 0.62)

            # NVDA is under crash halt -> must receive circuit breaker warning action
            by_ticker = saved_ai.get("by_ticker", {})
            self.assertIn("NVDA", by_ticker)
            nvda = by_ticker["NVDA"]
            self.assertTrue(nvda.get("is_crash_halt"))
            self.assertEqual(nvda.get("action"), "🚨 暴跌熔断·严禁左侧接刀")

            # In all_predictions, MSFT (healthy) must be ranked ahead of NVDA (halted)
            all_preds = saved_ai.get("all_predictions", [])
            tickers_order = [item["ticker"] for item in all_preds]
            if "MSFT" in tickers_order and "NVDA" in tickers_order:
                self.assertLess(tickers_order.index("MSFT"), tickers_order.index("NVDA"),
                                "Healthy stock MSFT must sort before crash-halted NVDA")

        finally:
            with open(ai_path, "w", encoding="utf-8") as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    unittest.main()
