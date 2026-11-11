# -*- coding: utf-8 -*-
"""
Milestone 2 Empirical Adversarial Stress Test Suite
Author: Empirical Challenger (Argon Reviewer)
Roles: Critic, Specialist
Target: Milestone 2 (Defects 4, 5, 8)

Verifies:
1. Circuit Breaker & `unlock_a` Boundary & Falling Knife Stress Tests
   - Exact float boundaries: -3.80%, -3.80001%, -3.79999%
   - Extended hours boundaries: -4.50%, -4.50001%, -4.49999%
   - ext_pct is None vs present
   - Prior green bounce followed by today gap-down plunge
   - 1000-sample parametric sweep ensuring zero buy orders during halts
2. WeChat Alert State Machine Adversarial Stress Tests
   - 100-ticker non-qualifying alert storm without cooldown poisoning
   - PushPlus network failure retry preservation & transactional commit
   - Weather change retry preservation on send failure
3. Schema & Consistency Validation of `latest_ai_predictions.json`
   - macro_weather bias alias equality (qqqm_bias == qqqm_bias_ma50, smh_bias == smh_bias_ma50)
   - Circuit breaker action override ("🚨 暴跌熔断·严禁左侧接刀")
   - Ranking invariant: Halted stocks strictly ranked behind healthy stocks
"""

import os
import sys
import json
import time
import random
import unittest
from unittest.mock import patch, MagicMock

# UTF-8 stdout configuration for Windows PowerShell
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import integrated_market_radar as imr
import create_futu_terminal as cft


class TestMilestone2AdversarialCircuitBreaker(unittest.TestCase):
    """Area 1: Adversarial Stress Testing of Circuit Breaker & unlock_a logic."""

    def setUp(self):
        self.default_intra = {
            "vwap": 100.0,
            "rsi_5m": 35.0,
            "prev_rsi_5m": 34.0,
            "min_rsi_5m_30m": 30.0,
            "rvol_5m": 1.2,
            "is_5m_green": False,
            "is_5m_higher_low": False,
            "rth_low": 98.0,
            "rth_high": 102.0,
            "rth_close": 100.0,
        }

    def test_border_pct_change_380_boundary(self):
        """Precision Boundary Matrix for pct_change:
        -3.80001%, -3.80000%, -3.79999%, -3.800000001, -3.799999999.
        With ATR%=2.5% (crash_thresh = -5.0%), is_base_crash is False.
        When prev_crash_3d=True and prev_is_right_turn=True:
        - pct_change <= -3.80: unlock_a MUST evaluate to False -> is_cooling_halt=True
        - pct_change > -3.80: unlock_a evaluates to True -> is_cooling_halt=False
        """
        # Test case: <= -3.80% (must NOT unlock)
        halted_values = [-3.80001, -3.80000, -3.800000001, -3.800000000000001]
        for pct in halted_values:
            with self.subTest(pct=pct):
                res = imr.evaluate_intraday_entry(
                    active_price=100.0,
                    fib_low=95.0,
                    fib_high=105.0,
                    ma50=100.0,
                    is_pit=True,
                    is_stop=False,
                    dist_to_pit=0.0,
                    intra_info=self.default_intra,
                    pct_change=pct,
                    atr=2.5,  # atr_pct = 2.5% -> crash_thresh = -5.0%
                    prev_crash_3d=True,
                    crash_3d=False,
                    prev_is_right_turn=True,
                    ema5=101.0,
                    prev_crash_body=5.0,
                    prev_crash_close=103.0,
                )
                self.assertFalse(res.get("is_crash_halt"), f"pct={pct}% should not trigger is_crash_halt with crash_thresh=-5.0%")
                self.assertTrue(res.get("is_cooling_halt"), f"pct={pct}% <= -3.8% MUST NOT unlock cooling halt!")
                self.assertEqual(res.get("optimal_limit"), 0.0, f"pct={pct}% optimal_limit must be 0.0")
                self.assertFalse(res.get("intraday_entry_ready"), f"pct={pct}% intraday_entry_ready must be False")

        # Test case: > -3.80% (unlocks condition a)
        unlocked_values = [-3.79999, -3.799999999, -3.70, 0.0, 1.5]
        for pct in unlocked_values:
            with self.subTest(pct=pct):
                res = imr.evaluate_intraday_entry(
                    active_price=100.0,
                    fib_low=95.0,
                    fib_high=105.0,
                    ma50=100.0,
                    is_pit=True,
                    is_stop=False,
                    dist_to_pit=0.0,
                    intra_info=self.default_intra,
                    pct_change=pct,
                    atr=2.5,
                    prev_crash_3d=True,
                    crash_3d=False,
                    prev_is_right_turn=True,
                    ema5=101.0,
                    prev_crash_body=5.0,
                    prev_crash_close=103.0,
                )
                self.assertFalse(res.get("is_crash_halt"))
                self.assertFalse(res.get("is_cooling_halt"), f"pct={pct}% > -3.8% should unlock cooling halt via condition a")
                self.assertGreater(res.get("optimal_limit"), 0.0, f"pct={pct}% optimal_limit must be positive when unlocked")

    def test_border_ext_pct_450_boundary(self):
        """Precision Boundary Matrix for ext_pct:
        -4.50001%, -4.50000%, -4.49999%, -4.500000001, -4.499999999.
        - ext_pct <= -4.50: Triggers Section 0 is_crash_halt=True (extended hours crash)
        - ext_pct > -4.50: is_crash_halt=False, and unlock_a allows unlock if pct_change > -3.8%
        """
        halted_ext = [-4.50001, -4.50000, -4.500000001]
        for ext in halted_ext:
            with self.subTest(ext=ext):
                res = imr.evaluate_intraday_entry(
                    active_price=100.0,
                    fib_low=95.0,
                    fib_high=105.0,
                    ma50=100.0,
                    is_pit=True,
                    is_stop=False,
                    dist_to_pit=0.0,
                    intra_info=self.default_intra,
                    pct_change=-1.0,
                    ext_pct=ext,
                    atr=2.0,
                    prev_crash_3d=True,
                    prev_is_right_turn=True,
                )
                self.assertTrue(res.get("is_crash_halt"), f"ext={ext}% <= -4.5% must trigger is_crash_halt")
                self.assertEqual(res.get("optimal_limit"), 0.0)
                self.assertFalse(res.get("intraday_entry_ready"))

        unlocked_ext = [-4.49999, -4.499999999, -4.0, -1.0, 0.0, 2.0]
        for ext in unlocked_ext:
            with self.subTest(ext=ext):
                res = imr.evaluate_intraday_entry(
                    active_price=100.0,
                    fib_low=95.0,
                    fib_high=105.0,
                    ma50=100.0,
                    is_pit=True,
                    is_stop=False,
                    dist_to_pit=0.0,
                    intra_info=self.default_intra,
                    pct_change=-1.0,
                    ext_pct=ext,
                    atr=2.5,
                    prev_crash_3d=True,
                    prev_is_right_turn=True,
                )
                self.assertFalse(res.get("is_crash_halt"), f"ext={ext}% > -4.5% should not trigger crash halt")
                self.assertFalse(res.get("is_cooling_halt"), f"ext={ext}% with pct=-1.0% should unlock cooling halt")
                self.assertGreater(res.get("optimal_limit"), 0.0)

    def test_ext_pct_none_vs_present_safety(self):
        """ext_pct is None vs present:
        Verify no TypeError/UnboundLocalError, and correct logical evaluation.
        """
        # ext_pct is None with pct_change=-1.0% -> unlock_a is True
        res_none = imr.evaluate_intraday_entry(
            active_price=100.0,
            fib_low=95.0,
            fib_high=105.0,
            ma50=100.0,
            is_pit=True,
            is_stop=False,
            dist_to_pit=0.0,
            intra_info=self.default_intra,
            pct_change=-1.0,
            ext_pct=None,
            atr=2.5,
            prev_crash_3d=True,
            prev_is_right_turn=True,
        )
        self.assertFalse(res_none.get("is_crash_halt"))
        self.assertFalse(res_none.get("is_cooling_halt"))
        self.assertGreater(res_none.get("optimal_limit"), 0.0)

        # ext_pct combined with pct_change: pct_change = -3.6%, ext_pct = -2.0% -> sum = -5.6% <= -5.5%
        res_combined = imr.evaluate_intraday_entry(
            active_price=100.0,
            fib_low=95.0,
            fib_high=105.0,
            ma50=100.0,
            is_pit=True,
            is_stop=False,
            dist_to_pit=0.0,
            intra_info=self.default_intra,
            pct_change=-3.6,
            ext_pct=-2.0,
            atr=2.5,
            prev_crash_3d=True,
            prev_is_right_turn=True,
        )
        self.assertTrue(res_combined.get("is_crash_halt"), "Combined pct (-3.6%) + ext (-2.0%) = -5.6% <= -5.5% must trigger crash halt")
        self.assertEqual(res_combined.get("optimal_limit"), 0.0)

    def test_adversarial_falling_knife_scenarios(self):
        """Verify that circuit breaker NEVER unlocks prematurely on falling knives."""
        # Knife Scenario 1: Yesterday green bounce (+2.0%), today plunges -6.0% (ATR base crash)
        res1 = imr.evaluate_intraday_entry(
            active_price=94.0,
            fib_low=95.0,
            fib_high=105.0,
            ma50=100.0,
            is_pit=True,
            is_stop=False,
            dist_to_pit=0.0,
            intra_info=self.default_intra,
            pct_change=-6.0,
            atr=2.0,
            prev_crash_3d=True,
            prev_is_right_turn=True,
        )
        self.assertTrue(res1.get("is_crash_halt"))
        self.assertEqual(res1.get("optimal_limit"), 0.0)
        self.assertFalse(res1.get("intraday_entry_ready"))

        # Knife Scenario 2: Yesterday green bounce, today opened flat, then executioner's blade flush -7.0%
        flush_intra = dict(self.default_intra)
        flush_intra["rth_high"] = 100.0
        res2 = imr.evaluate_intraday_entry(
            active_price=93.0,  # (93 - 100) / 100 = -7.0% from rth_high
            fib_low=90.0,
            fib_high=105.0,
            ma50=100.0,
            is_pit=True,
            is_stop=False,
            dist_to_pit=0.0,
            intra_info=flush_intra,
            pct_change=-2.0,
            atr=2.0,
            rth_high=100.0,
            prev_crash_3d=True,
            prev_is_right_turn=True,
        )
        self.assertTrue(res2.get("is_crash_halt"), "Intraday flush of -7.0% must trigger executioner's blade crash halt")
        self.assertEqual(res2.get("optimal_limit"), 0.0)

        # Knife Scenario 3: Crash happened TODAY (crash_3d=True, prev_crash_3d=False)
        # Even if yesterday had a green candle, unlock_a requires prev_crash_3d=True!
        res3 = imr.evaluate_intraday_entry(
            active_price=97.0,
            fib_low=95.0,
            fib_high=105.0,
            ma50=100.0,
            is_pit=True,
            is_stop=False,
            dist_to_pit=0.0,
            intra_info=self.default_intra,
            pct_change=-3.0,
            atr=2.5,
            prev_crash_3d=False,
            crash_3d=True,
            prev_is_right_turn=True,
        )
        self.assertTrue(res3.get("is_cooling_halt"), "Today's crash (prev_crash_3d=False, crash_3d=True) must NOT unlock via unlock_a")
        self.assertEqual(res3.get("optimal_limit"), 0.0)

    def test_parametric_stress_sweep_1000_samples(self):
        """Randomized stress sweep (1000 trials):
        Invariant: If is_crash_halt or is_cooling_halt is True,
        optimal_limit MUST BE 0.0 and intraday_entry_ready MUST BE False.
        """
        rng = random.Random(42)
        for i in range(1000):
            price = rng.uniform(10.0, 500.0)
            pct = rng.uniform(-15.0, 15.0)
            ext = rng.choice([None, rng.uniform(-10.0, 10.0)])
            atr = rng.uniform(0.5, 15.0)
            prev_crash = rng.choice([True, False])
            prev_turn = rng.choice([True, False])
            curr_crash = rng.choice([True, False])

            res = imr.evaluate_intraday_entry(
                active_price=price,
                fib_low=price * 0.95,
                fib_high=price * 1.05,
                ma50=price,
                is_pit=True,
                is_stop=False,
                dist_to_pit=0.0,
                intra_info=self.default_intra,
                pct_change=pct,
                ext_pct=ext,
                atr=atr,
                prev_crash_3d=prev_crash,
                crash_3d=curr_crash,
                prev_is_right_turn=prev_turn,
            )

            is_halted = res.get("is_crash_halt") or res.get("is_cooling_halt")
            if is_halted:
                self.assertEqual(
                    res.get("optimal_limit"),
                    0.0,
                    f"Trial {i}: Halted stock MUST have optimal_limit=0.0 (got {res.get('optimal_limit')})",
                )
                self.assertFalse(
                    res.get("intraday_entry_ready"),
                    f"Trial {i}: Halted stock MUST have intraday_entry_ready=False",
                )


class TestMilestone2AdversarialAlertStateMachine(unittest.TestCase):
    """Area 2: Adversarial Testing of WeChat Alert State Machine."""

    def setUp(self):
        self.tmp_alert_cache = os.path.join(PROJECT_DIR, ".test_m2_alert_history.json")
        self.patch_cache = patch.object(imr, "ALERT_CACHE_FILE", self.tmp_alert_cache)
        self.patch_cache.start()
        if os.path.exists(self.tmp_alert_cache):
            os.remove(self.tmp_alert_cache)

    def tearDown(self):
        self.patch_cache.stop()
        if os.path.exists(self.tmp_alert_cache):
            os.remove(self.tmp_alert_cache)

    def test_alert_storm_non_qualifying_signals_no_cooldown_poisoning(self):
        """Simulate an alert storm with 100 tickers generating non-qualifying signals:
        - 25 tickers with is_stop=True, but bias = -8.0% (fails bias < -15.0)
        - 25 tickers with is_stop=True, bias = -16.0%, but vol_ratio = 1.1 (fails vol_ratio >= 1.5)
        - 25 tickers with is_watched=True, but intraday_ready = False
        - 25 tickers with is_pit=True, but is_crash_halt = True
        Run through 10 polling cycles.
        Verify:
        1. No alerts are dispatched.
        2. Zero cooldown locks are recorded in targets_history.
        3. In cycle 11, when 5 tickers transition to fully qualify, they alert IMMEDIATELY.
        """
        storm_stocks = []
        for i in range(25):
            storm_stocks.append({
                "ticker": f"STOP_BIAS_{i}", "name": f"StopBias{i}", "cat": "Tech",
                "last": 100.0, "active_price": 100.0, "bias": -8.0, "is_stop": True,
                "vol_ratio": 2.0, "shares": 0
            })
        for i in range(25):
            storm_stocks.append({
                "ticker": f"STOP_VOL_{i}", "name": f"StopVol{i}", "cat": "Tech",
                "last": 100.0, "active_price": 100.0, "bias": -18.0, "is_stop": True,
                "vol_ratio": 1.1, "shares": 0
            })
        for i in range(25):
            storm_stocks.append({
                "ticker": f"WATCH_{i}", "name": f"Watch{i}", "cat": "Tech",
                "last": 100.0, "active_price": 100.0, "bias": -2.0, "is_stop": False,
                "intraday_entry_ready": False, "shares": 0
            })
        for i in range(25):
            storm_stocks.append({
                "ticker": f"PIT_HALT_{i}", "name": f"PitHalt{i}", "cat": "Tech",
                "last": 100.0, "active_price": 100.0, "bias": -5.0, "is_stop": False,
                "is_pit": True, "is_crash_halt": True, "shares": 0
            })

        weather = {"weather": "晴天做多", "desc": "晴天"}

        # Patch target watchlist so WATCH_* tickers are recognized as watched
        with patch.object(imr, "load_target_watchlist", return_value=[f"WATCH_{i}" for i in range(25)]):
            with patch.object(imr, "send_pushplus_wechat_alert") as mock_send:
                # Run 10 simulated 3-minute polling cycles
                for cycle in range(10):
                    now_str = f"2026-11-03 10:{cycle:02d}:00"
                    imr.dispatch_wechat_radar_alerts({}, storm_stocks, weather, now_str)

                mock_send.assert_not_called()

            # Verify that none of the 100 tickers are poisoned in targets_history cooldown
            history = imr.load_alert_history()
            targets_hist = history.get("targets", {})
            for s in storm_stocks:
                tk = s["ticker"]
                self.assertNotIn(
                    tk,
                    targets_hist,
                    f"Ticker {tk} must NOT be recorded in targets_history cooldown during non-qualifying storm"
                )

            # Cycle 11: 5 tickers suddenly meet full qualification
            for i in range(5):
                storm_stocks[i]["bias"] = -16.5  # STOP_BIAS_0..4 now qualifies
                storm_stocks[i]["vol_ratio"] = 1.8

            with patch.object(imr, "send_pushplus_wechat_alert", return_value=True) as mock_send_11:
                now_str = "2026-11-03 10:33:00"
                imr.dispatch_wechat_radar_alerts({}, storm_stocks, weather, now_str)
                self.assertEqual(mock_send_11.call_count, 1, "Cycle 11 MUST dispatch alert immediately without cooldown lock")
                # Title should mention 5破位
                title_arg = mock_send_11.call_args[0][0]
                self.assertIn("5只破位", title_arg)

    def test_pushplus_network_outage_preserves_retry_and_transactional_commit(self):
        """Simulate PushPlus Network Failure:
        1. Qualifying alert for TSLA and NVDA.
        2. Cycle 1: send_pushplus_wechat_alert returns False (HTTP 500 / timeout).
           Assert: targets_history has NO timestamps for TSLA/NVDA.
        3. Cycle 2: Network still down. Assert: Retry attempted, cooldown still uncommitted.
        4. Cycle 3: Network recovers (returns True).
           Assert: Cooldown timestamps committed.
        5. Cycle 4: Same inputs.
           Assert: Alert suppressed by active cooldown.
        """
        stocks = [
            {
                "ticker": "TSLA", "name": "Tesla", "cat": "EV",
                "last": 200.0, "active_price": 200.0, "bias": -16.0,
                "is_stop": True, "vol_ratio": 1.6, "shares": 0
            },
            {
                "ticker": "NVDA", "name": "Nvidia", "cat": "Semi",
                "last": 120.0, "active_price": 120.0, "bias": -17.0,
                "is_stop": True, "vol_ratio": 2.0, "shares": 0
            }
        ]
        weather = {"weather": "晴天做多", "desc": "晴天"}

        # Cycle 1: Network Failure
        with patch.object(imr, "send_pushplus_wechat_alert", return_value=False) as mock_send_fail:
            imr.dispatch_wechat_radar_alerts({}, stocks, weather, "2026-11-03 10:00:00")
            mock_send_fail.assert_called_once()

        hist_1 = imr.load_alert_history()
        self.assertNotIn("TSLA", hist_1.get("targets", {}), "TSLA must retain retry right on failure")
        self.assertNotIn("NVDA", hist_1.get("targets", {}), "NVDA must retain retry right on failure")

        # Cycle 2: Network Still Down
        with patch.object(imr, "send_pushplus_wechat_alert", return_value=False) as mock_send_fail2:
            imr.dispatch_wechat_radar_alerts({}, stocks, weather, "2026-11-03 10:03:00")
            mock_send_fail2.assert_called_once()

        hist_2 = imr.load_alert_history()
        self.assertNotIn("TSLA", hist_2.get("targets", {}))
        self.assertNotIn("NVDA", hist_2.get("targets", {}))

        # Cycle 3: Network Recovers (returns True)
        with patch.object(imr, "send_pushplus_wechat_alert", return_value=True) as mock_send_ok:
            imr.dispatch_wechat_radar_alerts({}, stocks, weather, "2026-11-03 10:06:00")
            mock_send_ok.assert_called_once()

        hist_3 = imr.load_alert_history()
        self.assertIn("TSLA", hist_3.get("targets", {}), "TSLA cooldown must be committed on successful send")
        self.assertIn("NVDA", hist_3.get("targets", {}), "NVDA cooldown must be committed on successful send")
        self.assertGreater(hist_3["targets"]["TSLA"]["last_notified_at"], 0.0)

        # Cycle 4: Same inputs (within 4-hour cooldown) -> Suppressed!
        with patch.object(imr, "send_pushplus_wechat_alert") as mock_send_suppressed:
            imr.dispatch_wechat_radar_alerts({}, stocks, weather, "2026-11-03 10:09:00")
            mock_send_suppressed.assert_not_called()

    def test_weather_change_retry_preservation_on_send_failure(self):
        """Verify that macro weather transition retains retry rights if send fails."""
        # Initial history with sunny weather
        init_hist = {"weather": "晴天做多", "targets": {}}
        imr.save_alert_history(init_hist)

        weather_storm = {"weather": "🌧️ 暴雨空仓", "desc": "暴雨"}

        # Attempt to alert weather change, but network fails
        with patch.object(imr, "send_pushplus_wechat_alert", return_value=False) as mock_send:
            imr.dispatch_wechat_radar_alerts({}, [], weather_storm, "2026-11-03 10:00:00")
            mock_send.assert_called_once()

        # weather must NOT be updated to storm in history on disk!
        hist_after_fail = imr.load_alert_history()
        self.assertEqual(
            hist_after_fail.get("weather"),
            "晴天做多",
            "Weather in history must NOT be updated when pushplus send fails"
        )

        # Next cycle: network succeeds
        with patch.object(imr, "send_pushplus_wechat_alert", return_value=True) as mock_send_ok:
            imr.dispatch_wechat_radar_alerts({}, [], weather_storm, "2026-11-03 10:03:00")
            mock_send_ok.assert_called_once()

        hist_after_ok = imr.load_alert_history()
        self.assertEqual(
            hist_after_ok.get("weather"),
            "🌧️ 暴雨空仓",
            "Weather in history must be updated after successful send"
        )


class TestMilestone2AdversarialAiPredictionsSchemaAndRanking(unittest.TestCase):
    """Area 3: Validation of latest_ai_predictions.json schema, aliases, and ranking."""

    def test_schema_and_bias_aliases_and_circuit_breaker_ranking_invariant(self):
        """Empirically test:
        1. qqqm_bias == qqqm_bias_ma50 and smh_bias == smh_bias_ma50 and vixy_pos == vixy_pos_60.
        2. All halted stocks (crash or cooling halt) receive action '🚨 暴跌熔断·严禁左侧接刀'.
        3. Invariant: Healthy stocks are strictly sorted ahead of halted stocks,
           regardless of how high the halted stock's AI win rate is.
        """
        ai_path = os.path.join(PROJECT_DIR, "data_cache", "latest_ai_predictions.json")
        with open(ai_path, "r", encoding="utf-8") as f:
            backup_data = json.load(f)

        try:
            weather_info = {
                "weather": "⛅ 阴天防守",
                "icon": "⛅",
                "qqqm_bias": -4.12,
                "smh_bias": -5.67,
                "vixy_pos_60": 58.5,
            }

            # Create an adversarial test dataset:
            # - MSFT: Healthy stock, model win rate = 52.0%
            # - AAPL: Healthy stock, model win rate = 55.0%
            # - NVDA: is_crash_halt=True, model win rate = 98.0% (EXTREME HIGH)
            # - AMD:  is_cooling_halt=True, model win rate = 95.0% (EXTREME HIGH)
            # - INTC: is_crash_halt=True and is_cooling_halt=True, win rate = 90.0%
            stocks_data = [
                {
                    "ticker": "NVDA", "active_price": 115.0, "last": 115.0, "bias": -3.5, "rsi": 40.0,
                    "is_crash_halt": True, "is_cooling_halt": False,
                },
                {
                    "ticker": "AMD", "active_price": 140.0, "last": 140.0, "bias": -4.0, "rsi": 38.0,
                    "is_crash_halt": False, "is_cooling_halt": True,
                },
                {
                    "ticker": "INTC", "active_price": 20.0, "last": 20.0, "bias": -6.0, "rsi": 32.0,
                    "is_crash_halt": True, "is_cooling_halt": True,
                },
                {
                    "ticker": "MSFT", "active_price": 410.0, "last": 410.0, "bias": 0.5, "rsi": 52.0,
                    "is_crash_halt": False, "is_cooling_halt": False,
                },
                {
                    "ticker": "AAPL", "active_price": 180.0, "last": 180.0, "bias": 1.2, "rsi": 54.0,
                    "is_crash_halt": False, "is_cooling_halt": False,
                },
            ]

            cft.build_futu_terminal_html(
                stocks_data=stocks_data,
                macro_data={},
                weather_info=weather_info,
                update_time_str="2026-11-03 10:30:00"
            )

            with open(ai_path, "r", encoding="utf-8") as f:
                saved = json.load(f)

            # Invariant 1: Macro weather bias alias equality
            mw = saved.get("macro_weather", {})
            self.assertEqual(mw.get("qqqm_bias"), -4.12)
            self.assertEqual(mw.get("qqqm_bias_ma50"), -4.12)
            self.assertEqual(mw.get("smh_bias"), -5.67)
            self.assertEqual(mw.get("smh_bias_ma50"), -5.67)
            self.assertEqual(mw.get("vixy_pos"), 0.58)
            self.assertEqual(mw.get("vixy_pos_60"), 0.58)

            # Invariant 2: Circuit breaker action override
            by_ticker = saved.get("by_ticker", {})
            for halted_tk in ["NVDA", "AMD", "INTC"]:
                self.assertIn(halted_tk, by_ticker)
                item = by_ticker[halted_tk]
                self.assertEqual(
                    item.get("action"),
                    "🚨 暴跌熔断·严禁左侧接刀",
                    f"Ticker {halted_tk} has crash/cooling halt, action must be '🚨 暴跌熔断·严禁左侧接刀'"
                )

            # Invariant 3: Sorting & Ranking invariant
            all_preds = saved.get("all_predictions", [])
            ticker_order = [p["ticker"] for p in all_preds]

            # Healthy stocks (MSFT, AAPL) MUST precede halted stocks (NVDA, AMD, INTC)
            for healthy_tk in ["MSFT", "AAPL"]:
                for halted_tk in ["NVDA", "AMD", "INTC"]:
                    if healthy_tk in ticker_order and halted_tk in ticker_order:
                        healthy_idx = ticker_order.index(healthy_tk)
                        halted_idx = ticker_order.index(halted_tk)
                        self.assertLess(
                            healthy_idx,
                            halted_idx,
                            f"Healthy stock {healthy_tk} (index {healthy_idx}) MUST rank before halted stock {halted_tk} (index {halted_idx})"
                        )

            # Check Top 10 stocks: Halted stocks must NOT rank higher than healthy stocks
            top10 = saved.get("top10", [])
            top10_tickers = [p["ticker"] for p in top10]
            if "MSFT" in top10_tickers and "NVDA" in top10_tickers:
                self.assertLess(top10_tickers.index("MSFT"), top10_tickers.index("NVDA"))

        finally:
            with open(ai_path, "w", encoding="utf-8") as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2)
            # Restore sister cache to preserve SHA256 parity
            sister_ai_path = os.path.join(PROJECT_DIR, "data_cache", "latest_ai_predictions.json")
            if os.path.exists(os.path.dirname(sister_ai_path)):
                with open(sister_ai_path, "w", encoding="utf-8") as f:
                    json.dump(backup_data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    unittest.main()
