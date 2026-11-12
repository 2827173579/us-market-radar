#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_adversarial_cycle_extrema.py - Adversarial Stress-Testing Harness
Empirical verification of Extrema Detection & Cycle Window Classification in ml_predictor_5y.py

Author: Challenger 1 (Adversarial Verifier)
Target: ml_predictor_5y._build_cycle_windows_from_projections & generate_time_cycle_projections
"""

import os
import sys
import unittest
from datetime import datetime
from typing import List, Dict, Any
import numpy as np
import pandas as pd

PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import ml_predictor_5y as ml_mod


def make_projections(prices: List[float], start_day: int = 1, current_price: float = 50.0) -> List[Dict[str, Any]]:
    """Helper to convert a list of prices into projections format."""
    projs = []
    for i, p in enumerate(prices):
        d_num = start_day + i
        pct = round((p - current_price) / current_price * 100.0, 1)
        projs.append({
            'day': f"T+{d_num}",
            'date': f"2026-11-{10 + i:02d}",
            'price': round(float(p), 2),
            'pct': pct
        })
    return projs


# ============================================================================
# Suite 1: Purely Monotonic Upward Trends with Micro-Ripples / Noise
# ============================================================================

class TestMonotonicUpwardStress(unittest.TestCase):
    """Stress-test monotonic upward trends: verify ZERO false peak/valley signals."""

    def test_strictly_monotonic_linear_upward(self):
        """Purely linear upward sequence must yield exactly 0 cycle windows."""
        cur_p = 50.0
        prices = [cur_p + 1.5 * i for i in range(1, 16)]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)
        self.assertEqual(len(windows), 0, f"Expected 0 windows for linear rise, got {len(windows)}: {windows}")

    def test_strictly_monotonic_exponential_upward(self):
        """Exponential upward sequence (parabolic surge) must yield 0 windows."""
        cur_p = 50.0
        prices = [cur_p * (1.03 ** i) for i in range(1, 16)]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)
        self.assertEqual(len(windows), 0, f"Expected 0 windows for exponential rise, got {windows}")

    def test_monotonic_upward_with_2day_plateaus(self):
        """Monotonic upward sequence with 2-day flat consolidation steps must yield 0 windows."""
        cur_p = 50.0
        prices = [51.0, 52.0, 52.0, 54.0, 55.0, 55.0, 57.0, 58.0, 58.0, 60.0, 61.0, 61.0, 63.0, 64.0, 65.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)
        self.assertEqual(len(windows), 0, f"2-day flat plateaus in uptrend should not trigger extrema: {windows}")

    def test_monotonic_upward_3day_plateau_radius_limit_characterization(self):
        """Empirically document that a 3-day flat plateau (length >= radius+1) triggers a peak candidate under radius=2."""
        cur_p = 50.0
        # A 3-day flat plateau [52, 52, 52] fills the entire forward window of radius 2.
        # Under P[t] >= P[t+1] and max(win_slice), the radius-2 window cannot see day t+3 (54.0).
        prices = [51.0, 52.0, 52.0, 52.0, 54.0, 55.0, 55.0, 56.0, 58.0, 58.0, 58.0, 60.0, 61.0, 61.0, 63.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)
        # Empirically verify that exactly 2 top windows are emitted due to the 3-day plateau exceeding radius 2
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0]['day'], 'T+2')
        self.assertEqual(windows[1]['day'], 'T+9')

    def test_monotonic_upward_micro_ripples_below_radius_2(self):
        """Uptrend with micro-ripples that do NOT form radius-2 extrema must yield 0 windows."""
        cur_p = 50.0
        # Alternating micro-steps: 50 -> 52 -> 51.9 -> 54 -> 53.9 -> 56 -> 55.9 ...
        # Although 52 > 51.9, in [50, 52, 51.9, 54], 54 is higher, so 52 is not max in radius 2!
        prices = [52.0, 51.9, 54.0, 53.9, 56.0, 55.9, 58.0, 57.9, 60.0, 59.9, 62.0, 61.9, 64.0, 63.9, 66.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)
        self.assertEqual(len(windows), 0, f"Micro-ripples below radius 2 should be completely rejected: {windows}")

    def test_stochastic_monte_carlo_monotonic_upward_1000_runs(self):
        """Monte Carlo: 1,000 random strictly increasing paths must have ZERO false signals."""
        rng = np.random.RandomState(42)
        for trial in range(1000):
            cur_p = rng.uniform(10.0, 500.0)
            steps = rng.uniform(0.1, 5.0, size=15)
            prices = list(cur_p + np.cumsum(steps))
            projs = make_projections(prices, current_price=cur_p)
            windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)
            self.assertEqual(len(windows), 0, f"Trial {trial}: strictly rising path produced false windows: {windows}")


# ============================================================================
# Suite 2: Purely Monotonic Downward Trends
# ============================================================================

class TestMonotonicDownwardStress(unittest.TestCase):
    """Stress-test monotonic downward trends: verify ZERO false peak signals."""

    def test_strictly_monotonic_linear_downward_below_cur_p(self):
        """Linear falling path below current price must yield 0 peak and 0 valley signals."""
        cur_p = 100.0
        prices = [cur_p - 2.0 * i for i in range(1, 16)]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)
        top_windows = [w for w in windows if w['type'] == 'top']
        self.assertEqual(len(top_windows), 0, f"Downtrend produced false peak: {top_windows}")
        self.assertEqual(len(windows), 0, f"Strict downtrend produced false windows: {windows}")

    def test_strictly_monotonic_linear_downward_starting_above_cur_p(self):
        """Falling path starting above current price but monotonic down must yield ZERO peaks."""
        cur_p = 50.0
        # Starts at 90, falls monotonically to 60 (all > cur_p)
        prices = [90.0 - 2.0 * i for i in range(15)]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)
        top_windows = [w for w in windows if w['type'] == 'top']
        self.assertEqual(len(top_windows), 0, f"Falling path above cur_p produced false peak: {top_windows}")

    def test_monotonic_downward_micro_rebounds(self):
        """Downward trend with 1-day micro-rebounds below radius 2 must yield ZERO peaks."""
        cur_p = 100.0
        prices = [90.0, 90.1, 88.0, 88.1, 86.0, 86.1, 84.0, 84.1, 82.0, 82.1, 80.0, 80.1, 78.0, 78.1, 76.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)
        top_windows = [w for w in windows if w['type'] == 'top']
        self.assertEqual(len(top_windows), 0, f"Micro-rebounds in downtrend produced false peak: {top_windows}")

    def test_stochastic_monte_carlo_monotonic_downward_1000_runs(self):
        """Monte Carlo: 1,000 random strictly decreasing paths must have ZERO false peak signals."""
        rng = np.random.RandomState(123)
        for trial in range(1000):
            cur_p = rng.uniform(50.0, 500.0)
            drops = rng.uniform(0.1, 4.0, size=15)
            prices = list(cur_p - np.cumsum(drops))
            projs = make_projections(prices, current_price=cur_p)
            windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)
            top_windows = [w for w in windows if w['type'] == 'top']
            self.assertEqual(len(top_windows), 0, f"Trial {trial}: downtrend produced false peak: {top_windows}")


# ============================================================================
# Suite 3: Rapid Zigzag Patterns & Boundary / Spacing Enforcement
# ============================================================================

class TestZigzagSpacingAndBoundaryStress(unittest.TestCase):
    """Stress-test rapid zigzags: verify spacing >= 2 trading days and exclusion of T+1 / T+15."""

    def test_rapid_alternating_zigzag_strict_spacing(self):
        """Daily alternating peaks and troughs must enforce spacing >= 2 and exclude T+1 and T+15."""
        cur_p = 50.0
        prices = [52.0, 70.0, 45.0, 75.0, 42.0, 80.0, 40.0, 85.0, 38.0, 90.0, 35.0, 95.0, 32.0, 100.0, 30.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)

        self.assertGreater(len(windows), 0, "Extreme zigzag should detect valid extrema")
        # Invariant 1: No T+1 or T+15
        for w in windows:
            self.assertNotIn(w['day'], ['T+1', 'T+15'], f"Window at endpoint {w['day']} is forbidden")

        # Invariant 2: Strictly >= 2 days between adjacent windows
        for i in range(1, len(windows)):
            d_prev = int(windows[i-1]['day'].replace('T+', ''))
            d_curr = int(windows[i]['day'].replace('T+', ''))
            self.assertGreaterEqual(
                d_curr - d_prev, 2,
                f"Adjacent windows {windows[i-1]['day']} and {windows[i]['day']} have spacing < 2"
            )

    def test_extreme_boundary_spikes_at_t1_and_t15(self):
        """Massive spike or crater at T+1 and T+15 must NEVER be tagged as turning windows."""
        cur_p = 50.0
        # Case A: Massive spike at T+1 (999.0) and T+15 (999.0)
        prices_spikes = [999.0, 52.0, 54.0, 53.0, 55.0, 56.0, 55.0, 57.0, 58.0, 57.0, 59.0, 60.0, 59.0, 61.0, 999.0]
        projs_spikes = make_projections(prices_spikes, current_price=cur_p)
        windows_spikes = ml_mod._build_cycle_windows_from_projections(projs_spikes, current_price=cur_p)
        days = [w['day'] for w in windows_spikes]
        self.assertNotIn('T+1', days, "T+1 spike must be excluded")
        self.assertNotIn('T+15', days, "T+15 spike must be excluded")

        # Case B: Massive crater at T+1 (1.0) and T+15 (1.0)
        prices_craters = [1.0, 52.0, 54.0, 53.0, 55.0, 56.0, 55.0, 57.0, 58.0, 57.0, 59.0, 60.0, 59.0, 61.0, 1.0]
        projs_craters = make_projections(prices_craters, current_price=cur_p)
        windows_craters = ml_mod._build_cycle_windows_from_projections(projs_craters, current_price=cur_p)
        days_c = [w['day'] for w in windows_craters]
        self.assertNotIn('T+1', days_c, "T+1 crater must be excluded")
        self.assertNotIn('T+15', days_c, "T+15 crater must be excluded")

    def test_boundary_t2_and_t14_validity(self):
        """T+2 and T+14 can be valid extrema when mathematically confirmed by radius 2."""
        cur_p = 50.0
        # T+2 peak: cur_p=50, T+1=52, T+2=65, T+3=58, T+4=55 (65 is max in [50, 52, 65, 58, 55])
        # T+14 peak: T+12=56, T+13=60, T+14=75, T+15=70 (75 is max in [56, 60, 75, 70])
        prices = [52.0, 65.0, 58.0, 55.0, 54.0, 53.0, 52.0, 53.0, 54.0, 55.0, 56.0, 60.0, 62.0, 75.0, 70.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)
        days = [w['day'] for w in windows]
        self.assertIn('T+2', days, "T+2 should be detected as valid peak")
        self.assertIn('T+14', days, "T+14 should be detected as valid peak")

    def test_stochastic_monte_carlo_zigzag_1000_runs(self):
        """Monte Carlo: 1,000 highly volatile / oscillating paths must strictly obey spacing and boundary invariants."""
        rng = np.random.RandomState(456)
        for trial in range(1000):
            cur_p = rng.uniform(20.0, 200.0)
            # Generate random walk with large steps to produce chaotic zigzags
            steps = rng.normal(loc=0.0, scale=5.0, size=15)
            prices = [max(1.0, cur_p + np.sum(steps[:i+1])) for i in range(15)]
            projs = make_projections(prices, current_price=cur_p)
            windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)

            # Invariant 1: No T+1 or T+15
            for w in windows:
                self.assertNotIn(
                    w['day'], ['T+1', 'T+15'],
                    f"Trial {trial}: Found boundary node {w['day']}"
                )

            # Invariant 2: Spacing >= 2
            for i in range(1, len(windows)):
                d_prev = int(windows[i-1]['day'].replace('T+', ''))
                d_curr = int(windows[i]['day'].replace('T+', ''))
                self.assertGreaterEqual(
                    d_curr - d_prev, 2,
                    f"Trial {trial}: Adjacent nodes {windows[i-1]['day']} and {windows[i]['day']} have spacing < 2"
                )

            # Invariant 3: Max count <= 6
            self.assertLessEqual(len(windows), 6, f"Trial {trial}: Too many windows ({len(windows)})")


# ============================================================================
# Suite 4: Price Inversion Stress-Test
# ============================================================================

class TestPriceInversionStress(unittest.TestCase):
    """Stress-test price inversion: Peak price MUST strictly exceed cur_p and local valleys."""

    def test_peak_below_current_price_rejected(self):
        """A local peak occurring below current price (bear rally) must NEVER be tagged as 'top'."""
        cur_p = 100.0
        # Future prices fluctuate between 70 and 85 (local peak at 85 on T+4), but all < 100
        prices = [75.0, 80.0, 82.0, 85.0, 80.0, 78.0, 75.0, 72.0, 70.0, 72.0, 74.0, 73.0, 72.0, 71.0, 70.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)
        top_windows = [w for w in windows if w['type'] == 'top']
        self.assertEqual(len(top_windows), 0, f"Local peak below current price was tagged as top: {top_windows}")

    def test_peak_barely_above_current_price(self):
        """Peak price cur_p + 0.05 is accepted, but cur_p - 0.05 is rejected."""
        cur_p = 50.0
        # Peak at T+3 = 50.05 (> cur_p)
        prices_above = [48.0, 49.0, 50.05, 49.0, 48.0, 47.0, 46.0, 45.0, 44.0, 43.0, 42.0, 41.0, 40.0, 39.0, 38.0]
        projs_a = make_projections(prices_above, current_price=cur_p)
        wins_a = ml_mod._build_cycle_windows_from_projections(projs_a, current_price=cur_p)
        top_a = [w for w in wins_a if w['type'] == 'top']
        self.assertEqual(len(top_a), 1, "cur_p + 0.05 should be accepted as top")
        self.assertGreater(top_a[0]['price'], cur_p)

        # Peak at T+3 = 49.95 (< cur_p)
        prices_below = [48.0, 49.0, 49.95, 49.0, 48.0, 47.0, 46.0, 45.0, 44.0, 43.0, 42.0, 41.0, 40.0, 39.0, 38.0]
        projs_b = make_projections(prices_below, current_price=cur_p)
        wins_b = ml_mod._build_cycle_windows_from_projections(projs_b, current_price=cur_p)
        top_b = [w for w in wins_b if w['type'] == 'top']
        self.assertEqual(len(top_b), 0, "cur_p - 0.05 must NOT be accepted as top")

    def test_peak_strictly_higher_than_local_valleys(self):
        """In cyclical trajectories, each Peak must be strictly higher than adjacent local valleys."""
        cur_p = 50.0
        # Wave: rise to 65 (T+3), fall to 55 (T+6), rise to 75 (T+10), fall to 62 (T+13)
        prices = [55.0, 60.0, 65.0, 60.0, 57.0, 55.0, 58.0, 65.0, 70.0, 75.0, 70.0, 65.0, 62.0, 66.0, 68.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)

        for i in range(len(windows) - 1):
            w1 = windows[i]
            w2 = windows[i+1]
            if w1['type'] == 'top' and w2['type'] in ['bottom', 'caution']:
                self.assertGreater(
                    w1['price'], w2['price'],
                    f"Price inversion: Peak {w1['day']} (${w1['price']}) <= Valley {w2['day']} (${w2['price']})"
                )
            elif w1['type'] in ['bottom', 'caution'] and w2['type'] == 'top':
                self.assertGreater(
                    w2['price'], w1['price'],
                    f"Price inversion: Valley {w1['day']} (${w1['price']}) >= Peak {w2['day']} (${w2['price']})"
                )

    def test_stochastic_monte_carlo_cyclical_no_local_inversion_1000_runs(self):
        """Monte Carlo: 1,000 cyclical paths (sine + trend + noise) must satisfy Peak > cur_p and no local wave inversion."""
        rng = np.random.RandomState(789)
        t = np.arange(15)
        for trial in range(1000):
            cur_p = rng.uniform(30.0, 150.0)
            amp = rng.uniform(5.0, 25.0)
            period = rng.uniform(4.0, 8.0)
            drift = rng.uniform(-0.5, 1.5)
            noise = rng.normal(0, 0.8, size=15)
            wave = cur_p + drift * t + amp * np.sin(2 * np.pi * t / period) + noise
            prices = [max(1.0, p) for p in wave]
            projs = make_projections(prices, current_price=cur_p)
            windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)

            # Assert: Every peak > cur_p
            for w in windows:
                if w['type'] == 'top':
                    self.assertGreater(
                        w['price'], cur_p,
                        f"Trial {trial}: Peak price {w['price']} not > cur_p {cur_p}"
                    )

            # Assert: No adjacent peak/valley inversion in the same wave
            for i in range(len(windows) - 1):
                w1, w2 = windows[i], windows[i+1]
                if w1['type'] == 'top' and w2['type'] in ['bottom', 'caution']:
                    self.assertGreater(
                        w1['price'], w2['price'],
                        f"Trial {trial}: Inversion Peak {w1['price']} <= Valley {w2['price']}"
                    )
                elif w1['type'] in ['bottom', 'caution'] and w2['type'] == 'top':
                    self.assertGreater(
                        w2['price'], w1['price'],
                        f"Trial {trial}: Inversion Valley {w1['price']} >= Peak {w2['price']}"
                    )


# ============================================================================
# Suite 5: High-Level Rest Window Override (R1 / R2)
# ============================================================================

class TestHighLevelRestWindowOverride(unittest.TestCase):
    """Stress-test high-level rest window override when valley price >= R1 or >= R2."""

    def test_valley_above_r2_override(self):
        """Valley price >= R2 must be tagged as 'caution' and '⚠️ 高位强势休整窗 (已超R1/R2阻力·勿追高)'."""
        cur_p = 50.0
        r1, r2 = 60.0, 65.0
        # Valley at T+5 = 66.0 (>= R2)
        prices = [60.0, 68.0, 72.0, 70.0, 66.0, 71.0, 75.0, 76.0, 78.0, 77.0, 79.0, 80.0, 81.0, 82.0, 83.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=r1, r2=r2)

        cautions = [w for w in windows if w['type'] == 'caution']
        self.assertGreaterEqual(len(cautions), 1, "Valley >= R2 must trigger caution window")
        for cw in cautions:
            self.assertIn("⚠️ 高位强势休整窗 (已超R1/R2阻力·勿追高)", cw['desc'])
            self.assertIn("⚠️ 高位强势休整窗（已超R1/R2阻力·勿追高）", cw['tag'])
            self.assertNotIn("周期底回踩窗", cw['desc'])

    def test_valley_between_r1_and_r2_override(self):
        """Valley price R1 <= P < R2 must be tagged as 'caution' and '⚠️ 高位强势休整窗'."""
        cur_p = 50.0
        r1, r2 = 60.0, 65.0
        # Valley at T+5 = 62.0 (>= R1 and < R2)
        prices = [58.0, 64.0, 67.0, 65.0, 62.0, 66.0, 69.0, 70.0, 71.0, 70.0, 72.0, 73.0, 74.0, 75.0, 76.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=r1, r2=r2)

        cautions = [w for w in windows if w['type'] == 'caution']
        self.assertGreaterEqual(len(cautions), 1, "Valley between R1 and R2 must trigger caution window")
        for cw in cautions:
            self.assertIn("高位强势休整窗", cw['desc'])
            self.assertNotIn("周期底回踩窗", cw['desc'])

    def test_valley_strictly_below_r1_normal_bottom(self):
        """Valley price P < R1 must remain normal 'bottom' tagged as '🔵 周期底回踩窗 (关注空头回补企稳)'."""
        cur_p = 50.0
        r1, r2 = 60.0, 65.0
        # Valley at T+5 = 54.0 (< R1)
        prices = [52.0, 56.0, 62.0, 58.0, 54.0, 57.0, 61.0, 63.0, 64.0, 63.0, 65.0, 66.0, 67.0, 68.0, 69.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=r1, r2=r2)

        bottoms = [w for w in windows if w['type'] == 'bottom']
        self.assertGreaterEqual(len(bottoms), 1, "Valley < R1 must remain normal bottom window")
        for bw in bottoms:
            self.assertIn("🔵 周期底回踩窗 (关注空头回补企稳)", bw['desc'])
            self.assertIn("周期底回踩窗 (企稳关注)", bw['tag'])
            self.assertNotIn("高位强势休整窗", bw['desc'])

    def test_exact_boundary_conditions_r1_and_r2(self):
        """Exact equality boundary tests: P == R1 and P == R2 must trigger caution."""
        cur_p = 50.0
        r1, r2 = 60.0, 65.0

        # Subtest 1: P == R1 (60.0) exactly
        prices_r1 = [55.0, 62.0, 65.0, 63.0, 60.0, 64.0, 67.0, 68.0, 69.0, 68.0, 70.0, 71.0, 72.0, 73.0, 74.0]
        projs_r1 = make_projections(prices_r1, current_price=cur_p)
        wins_r1 = ml_mod._build_cycle_windows_from_projections(projs_r1, current_price=cur_p, r1=r1, r2=r2)
        c_r1 = [w for w in wins_r1 if w['day'] == 'T+5']
        self.assertEqual(len(c_r1), 1)
        self.assertEqual(c_r1[0]['type'], 'caution', "P == R1 must trigger caution")

        # Subtest 2: P == R2 (65.0) exactly
        prices_r2 = [60.0, 68.0, 72.0, 69.0, 65.0, 68.0, 72.0, 73.0, 74.0, 73.0, 75.0, 76.0, 77.0, 78.0, 79.0]
        projs_r2 = make_projections(prices_r2, current_price=cur_p)
        wins_r2 = ml_mod._build_cycle_windows_from_projections(projs_r2, current_price=cur_p, r1=r1, r2=r2)
        c_r2 = [w for w in wins_r2 if w['day'] == 'T+5']
        self.assertEqual(len(c_r2), 1)
        self.assertEqual(c_r2[0]['type'], 'caution', "P == R2 must trigger caution")

        # Subtest 3: P == R1 - 0.01 (59.99) -> must be bottom
        prices_just_below = [55.0, 62.0, 65.0, 63.0, 59.99, 64.0, 67.0, 68.0, 69.0, 68.0, 70.0, 71.0, 72.0, 73.0, 74.0]
        projs_jb = make_projections(prices_just_below, current_price=cur_p)
        wins_jb = ml_mod._build_cycle_windows_from_projections(projs_jb, current_price=cur_p, r1=r1, r2=r2)
        b_jb = [w for w in wins_jb if w['day'] == 'T+5']
        self.assertEqual(len(b_jb), 1)
        self.assertEqual(b_jb[0]['type'], 'bottom', "P == R1 - 0.01 must remain normal bottom")

    def test_resistance_none_zero_or_negative_resilience(self):
        """When R1/R2 are None, 0, or negative, must degrade gracefully to 'bottom' without error."""
        cur_p = 50.0
        prices = [52.0, 56.0, 62.0, 58.0, 54.0, 57.0, 61.0, 63.0, 64.0, 63.0, 65.0, 66.0, 67.0, 68.0, 69.0]
        projs = make_projections(prices, current_price=cur_p)

        # None values
        wins_none = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=None, r2=None)
        self.assertTrue(any(w['type'] == 'bottom' for w in wins_none))

        # Zero values
        wins_zero = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=0.0, r2=0.0)
        self.assertTrue(any(w['type'] == 'bottom' for w in wins_zero))

        # Negative values
        wins_neg = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=-10.0, r2=-5.0)
        self.assertTrue(any(w['type'] == 'bottom' for w in wins_neg))

    def test_pipeline_integration_generate_time_cycle_projections(self):
        """End-to-end integration: generate_time_cycle_projections calibrates R2 and tags caution."""
        dates = pd.date_range(end=datetime.now(), periods=100, freq='B')
        mock_panel = pd.DataFrame({
            'Ticker': ['SOXL'] * 100,
            'Date': list(dates),
            'Close': [50.0 + (i * 0.2) for i in range(100)]
        })
        # Scenario map with tight R1 and low static R2
        sc_map = {
            'SOXL': {
                'latest_price': 69.8,
                'r1': 70.5,
                'r2': 71.0,
                's1': 66.0,
                's2': 64.0,
                'sl': 62.0
            }
        }
        res = ml_mod.generate_time_cycle_projections(
            raw_panel=mock_panel,
            target_tickers=['SOXL'],
            forecast_days=15,
            scenario_map_by_ticker=sc_map
        )
        soxl = res['SOXL']
        # 1. Calibrated R2 must be >= max projection * 1.015
        max_proj = max(p['price'] for p in soxl['projection'])
        self.assertGreaterEqual(soxl['r2'], round(max_proj * 1.015, 2))
        self.assertGreaterEqual(soxl['r2'], max_proj)

        # 2. Check cycle windows tags
        for w in soxl['cycle_windows']:
            if w['type'] == 'caution':
                self.assertIn("高位强势休整窗", w['desc'])
                self.assertGreaterEqual(w['price'], soxl['r1'])

    def test_nan_and_inf_resilience(self):
        """NaN values in projection prices must be handled safely without unhandled exception."""
        cur_p = 50.0
        prices = [50.0, float('nan'), 55.0, 60.0, 58.0, 55.0, 54.0, 53.0, 52.0, 51.0, 50.0, 49.0, 48.0, 47.0, 46.0]
        projs = make_projections(prices, current_price=cur_p)
        try:
            wins = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p)
            self.assertIsInstance(wins, list)
        except Exception as e:
            self.fail(f"NaN price caused unexpected crash: {e}")

    def test_cur_p_zero_or_none_fallback(self):
        """When cur_p is 0.0 or None, fallback to first projection price must work seamlessly."""
        prices = [52.0, 56.0, 62.0, 58.0, 54.0, 57.0, 61.0, 63.0, 64.0, 63.0, 65.0, 66.0, 67.0, 68.0, 69.0]
        projs = make_projections(prices, current_price=52.0)
        wins_zero = ml_mod._build_cycle_windows_from_projections(projs, current_price=0.0)
        self.assertIsInstance(wins_zero, list)
        wins_none = ml_mod._build_cycle_windows_from_projections(projs, current_price=None)
        self.assertIsInstance(wins_none, list)


# ============================================================================
# Main Execution Runner
# ============================================================================

def run_adversarial_suite():
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()

    suite.addTests(loader.loadTestsFromTestCase(TestMonotonicUpwardStress))
    suite.addTests(loader.loadTestsFromTestCase(TestMonotonicDownwardStress))
    suite.addTests(loader.loadTestsFromTestCase(TestZigzagSpacingAndBoundaryStress))
    suite.addTests(loader.loadTestsFromTestCase(TestPriceInversionStress))
    suite.addTests(loader.loadTestsFromTestCase(TestHighLevelRestWindowOverride))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result


if __name__ == '__main__':
    result = run_adversarial_suite()
    sys.exit(0 if result.wasSuccessful() else 1)
