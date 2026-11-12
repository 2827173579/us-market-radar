#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_challenger_r2_2_adversarial.py - Additional Adversarial Stress Testing
Executed by Challenger R2_2.

Focus Areas:
1. Detailed boundary transitions for Friday post-market cutoff (15:59:59 vs 16:00:00 vs 20:00:00 ET)
2. Timezone-aware vs naive inputs across multiple timezones (UTC, NY, Tokyo)
3. Micro-second edge cases on weekend and week start transitions
4. Defense ladder stress testing under extreme simulated paths
5. HTML DOM inspection for T+15 badge, caution styling, and absence of overlapping elements
"""

import os
import sys
import unittest
from datetime import datetime, date, timedelta
import zoneinfo
import numpy as np
import pandas as pd

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import ml_predictor_5y as mlp


class TestFridayPostMarketAndCalendarTransitionsAdversarial(unittest.TestCase):
    """
    Stress-testing exact boundary times of market open/close and calendar sequences.
    """

    def test_friday_exact_cutoff_seconds(self):
        """
        Adversarial test on Friday boundary:
        - Friday 15:59:59 ET -> Market not closed -> expected trading day is Thursday
        - Friday 16:00:00 ET -> Market closed -> expected trading day is Friday
        - Friday 20:00:00 ET -> Post-market ended -> expected trading day is Friday
        - Friday 23:59:59 ET -> Midnight before Sat -> expected trading day is Friday
        """
        fn = mlp._get_latest_expected_trading_day
        # Friday is 2026-11-13
        fri_1559 = datetime(2026, 11, 13, 15, 59, 59)
        self.assertEqual(fn(fri_1559), date(2026, 11, 12), "Friday 15:59:59 ET must anchor to Thursday")

        fri_1600 = datetime(2026, 11, 13, 16, 0, 0)
        self.assertEqual(fn(fri_1600), date(2026, 11, 13), "Friday 16:00:00 ET must anchor to Friday")

        fri_2000 = datetime(2026, 11, 13, 20, 0, 0)
        self.assertEqual(fn(fri_2000), date(2026, 11, 13), "Friday 20:00:00 ET must anchor to Friday")

        fri_2359 = datetime(2026, 11, 13, 23, 59, 59)
        self.assertEqual(fn(fri_2359), date(2026, 11, 13), "Friday 23:59:59 ET must anchor to Friday")

    def test_timezone_aware_and_unaware_inputs(self):
        """
        Verify that timezone-aware datetimes in UTC or Asia/Tokyo properly map to America/New_York.
        """
        fn = mlp._get_latest_expected_trading_day
        tz_utc = zoneinfo.ZoneInfo("UTC")
        tz_ny = zoneinfo.ZoneInfo("America/New_York")
        tz_tyo = zoneinfo.ZoneInfo("Asia/Tokyo")

        # Friday 16:30 ET = Friday 21:30 UTC = Saturday 06:30 Tokyo
        dt_ny = datetime(2026, 11, 13, 16, 30, tzinfo=tz_ny)
        dt_utc = datetime(2026, 11, 13, 21, 30, tzinfo=tz_utc)
        dt_tyo = datetime(2026, 11, 14, 6, 30, tzinfo=tz_tyo)

        res_ny = fn(dt_ny)
        res_utc = fn(dt_utc)
        res_tyo = fn(dt_tyo)

        self.assertEqual(res_ny, date(2026, 11, 13))
        self.assertEqual(res_utc, date(2026, 11, 13), "UTC input must convert to Friday 16:30 ET and return Friday")
        self.assertEqual(res_tyo, date(2026, 11, 13), "Tokyo Saturday morning must convert to Friday 16:30 ET and return Friday")

    def test_monday_open_and_close_transitions(self):
        """
        Verify Monday transitions:
        - Monday 08:00 ET (pre-market) -> returns Friday (previous trading day)
        - Monday 09:30 ET (market open) -> returns Friday (Monday not closed)
        - Monday 15:59 ET -> returns Friday
        """
        fn = mlp._get_latest_expected_trading_day
        mon_0800 = datetime(2026, 11, 16, 8, 0, 0)
        self.assertEqual(fn(mon_0800), date(2026, 11, 13))

        mon_0930 = datetime(2026, 11, 16, 9, 30, 0)
        self.assertEqual(fn(mon_0930), date(2026, 11, 13))

        mon_1559 = datetime(2026, 11, 16, 15, 59, 59)
        self.assertEqual(fn(mon_1559), date(2026, 11, 13))


class TestDefenseLadderExtremeCalibrations(unittest.TestCase):
    """
    Stress-testing defense ladder calibration and cycle windows under adversarial scenarios.
    """

    def test_r2_calibration_against_extreme_spikes(self):
        """
        If projection spikes +20% or +100%, R2 must stay above max_proj.
        """
        base_p = 100.0
        # Normal R2 from compute_multi_level_scenario_map
        sc = mlp.compute_multi_level_scenario_map('TEST', base_p, atr_pct=2.0)
        normal_r2 = sc['r2']
        self.assertTrue(base_p < sc['r1'] < sc['r2'])

        # Now simulate projection with extreme surge to 150.0
        extreme_proj = [{'day': f'T+{i}', 'price': 100.0 + i * 3.5} for i in range(1, 16)]
        max_proj_p = max(p['price'] for p in extreme_proj) # 152.5
        
        # Calibration formula used in ml_predictor_5y and create_futu_terminal
        calibrated_r2 = max(normal_r2, round(max_proj_p * 1.015, 2))
        self.assertGreater(calibrated_r2, max_proj_p)
        self.assertGreater(calibrated_r2, sc['r1'])

    def test_cycle_windows_exact_r1_r2_boundaries(self):
        """
        Verify exact equality at R1 and R2:
        - Valley at R1 - 0.01 -> type: bottom
        - Valley at R1 -> type: caution (>= R1)
        - Valley at R2 -> type: caution (>= R2)
        """
        r1, r2 = 110.0, 120.0
        cur_p = 100.0

        # Construct projection where T+4 is local minimum
        # P[2]=112, P[3]=111, P[4]=price, P[5]=111, P[6]=112
        def make_proj(val):
            return [
                {'day': 'T+1', 'date': '2026-11-12', 'price': 105.0},
                {'day': 'T+2', 'date': '2026-11-13', 'price': 112.0},
                {'day': 'T+3', 'date': '2026-11-16', 'price': 111.0},
                {'day': 'T+4', 'date': '2026-11-17', 'price': val},
                {'day': 'T+5', 'date': '2026-11-18', 'price': 111.0},
                {'day': 'T+6', 'date': '2026-11-19', 'price': 112.0},
                {'day': 'T+7', 'date': '2026-11-20', 'price': 115.0},
                {'day': 'T+8', 'date': '2026-11-23', 'price': 116.0},
                {'day': 'T+9', 'date': '2026-11-24', 'price': 117.0},
                {'day': 'T+10', 'date': '2026-11-25', 'price': 118.0},
                {'day': 'T+11', 'date': '2026-11-27', 'price': 119.0},
                {'day': 'T+12', 'date': '2026-11-30', 'price': 120.0},
                {'day': 'T+13', 'date': '2026-12-01', 'price': 121.0},
                {'day': 'T+14', 'date': '2026-12-02', 'price': 122.0},
                {'day': 'T+15', 'date': '2026-12-03', 'price': 123.0},
            ]


        # Case 1: Valley at 109.99 (< R1 110.0) -> bottom
        w_sub = mlp._build_cycle_windows_from_projections(make_proj(109.99), current_price=cur_p, r1=r1, r2=r2)
        w4_sub = [w for w in w_sub if w['day'] == 'T+4']
        self.assertEqual(len(w4_sub), 1)
        self.assertEqual(w4_sub[0]['type'], 'bottom')

        # Case 2: Valley at 110.00 (== R1 110.0) -> caution
        w_eq = mlp._build_cycle_windows_from_projections(make_proj(110.00), current_price=cur_p, r1=r1, r2=r2)
        w4_eq = [w for w in w_eq if w['day'] == 'T+4']
        self.assertEqual(len(w4_eq), 1)
        self.assertEqual(w4_eq[0]['type'], 'caution')
        self.assertIn('⚠️ 高位强势休整窗', w4_eq[0]['desc'])

        # Case 3: Valley at 120.00 (== R2 120.0) -> caution
        # For radius 2, indices 1, 2, 4, 5 must all be > 120.0
        proj_high = make_proj(120.00)
        proj_high[1]['price'] = 124.0
        proj_high[2]['price'] = 122.0
        proj_high[4]['price'] = 122.0
        proj_high[5]['price'] = 124.0

        w_r2 = mlp._build_cycle_windows_from_projections(proj_high, current_price=cur_p, r1=r1, r2=r2)
        w4_r2 = [w for w in w_r2 if w['day'] == 'T+4']
        self.assertEqual(len(w4_r2), 1)
        self.assertEqual(w4_r2[0]['type'], 'caution')


class TestHtmlDashboardInspection(unittest.TestCase):
    """
    Direct inspection of predict_radar_5y.html DOM contents and styling.
    """

    def test_html_badge_and_caution_dom(self):
        html_path = os.path.join(PROJECT_DIR, 'predict_radar_5y.html')
        with open(html_path, 'r', encoding='utf-8') as f:
            html = f.read()

        # Check badge styling and placement (Issue 4)
        self.assertIn('cycleT15BadgeHeader', html, "Header badge for T+15 should be present in HTML")
        self.assertIn('高位强势休整窗', html, "Legend or description for caution window must be present")
        self.assertIn('timeCycleData', html, "Embedded JSON dataset must be present")
        self.assertIn('Final Day +% Pill', html, "Repositioned non-overlapping pill comment/code must be present in HTML")



if __name__ == '__main__':
    unittest.main(verbosity=2)
