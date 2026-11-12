#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_adversarial_challenger_r2.py
Comprehensive empirical audit and adversarial stress tests for Challenger R2_1.
Audits all 21 tickers in:
1. data_cache/latest_ai_predictions.json
2. predict_radar_5y.html

Author: Challenger R2_1 (Empirical Verifier)
"""

import os
import sys
import json
import unittest
import numpy as np

PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
JSON_PATH = os.path.join(PROJECT_DIR, 'data_cache', 'latest_ai_predictions.json')
HTML_PATH = os.path.join(PROJECT_DIR, 'predict_radar_5y.html')


def load_json_projections():
    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('time_cycle_projections', {})


def load_html_projections():
    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        content = f.read()
    key = 'const timeCycleData = '
    idx = content.find(key)
    if idx == -1:
        raise ValueError(f"Could not find '{key}' in {HTML_PATH}")
    json_str = content[idx + len(key):]
    data, _ = json.JSONDecoder().raw_decode(json_str)
    return data


class TestAll21TickersAdversarialAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.json_tcp = load_json_projections()
        cls.html_tcp = load_html_projections()
        cls.expected_tickers = [
            'SOXL', 'SOXX', 'SOXS', 'SMH', 'QQQM', 'SQQQ', 'NVDA', 'TSM', 'MU',
            'AMD', 'AVGO', 'MRVL', 'INTC', 'SNDK', 'KORU', 'IBIT', 'TSLA', 'PLTR',
            'META', 'AAPL', 'AMZN'
        ]

    def test_ticker_coverage(self):
        """Assert both JSON and HTML contain exactly the 21 expected tickers."""
        self.assertEqual(sorted(self.json_tcp.keys()), sorted(self.expected_tickers))
        self.assertEqual(sorted(self.html_tcp.keys()), sorted(self.expected_tickers))
        print(f"\n[Coverage] Verified all 21 tickers present in both JSON and HTML.")

    def _audit_dataset(self, dataset: dict, source_name: str):
        total_windows = 0
        total_peaks = 0
        total_valleys = 0
        total_cautions = 0

        for ticker in self.expected_tickers:
            with self.subTest(ticker=ticker, source=source_name):
                item = dataset.get(ticker)
                self.assertIsNotNone(item, f"{ticker} missing from {source_name}")

                cur_p = float(item.get('latest_price') or item.get('current_price') or 0.0)
                r1 = float(item.get('r1') or 0.0) if item.get('r1') is not None else None
                r2 = float(item.get('r2') or 0.0) if item.get('r2') is not None else None
                projs = item.get('projection', [])
                windows = item.get('cycle_windows', [])

                self.assertEqual(len(projs), 15, f"{ticker} {source_name} does not have 15 forward projections")
                proj_prices = [float(p['price']) for p in projs]
                max_proj = max(proj_prices)

                # Invariant 1: Dynamic R2 envelope calibration
                if r2 is not None and r2 > 0:
                    self.assertLessEqual(
                        max_proj, r2,
                        f"[{source_name}] {ticker}: max projection ${max_proj:.2f} floats above R2 ${r2:.2f}"
                    )

                # Invariant 2: Ladder monotonicity
                if r1 is not None and r2 is not None and r1 > 0 and r2 > 0:
                    self.assertGreater(
                        r2, r1,
                        f"[{source_name}] {ticker}: R2 (${r2:.2f}) <= R1 (${r1:.2f})"
                    )

                # Verify each window
                for w in windows:
                    total_windows += 1
                    w_type = w.get('type')
                    w_day = w.get('day')
                    w_price = float(w.get('price'))
                    d_idx = int(w_day.replace('T+', ''))

                    # Invariant 3: No endpoint turning windows (T+1 or T+15)
                    self.assertNotIn(
                        w_day, ['T+1', 'T+15'],
                        f"[{source_name}] {ticker}: window at forbidden boundary {w_day}"
                    )
                    self.assertTrue(2 <= d_idx <= 14, f"[{source_name}] {ticker}: invalid day {w_day}")

                    # Check against projection price
                    expected_p = proj_prices[d_idx - 1]
                    self.assertAlmostEqual(
                        w_price, expected_p, places=2,
                        msg=f"[{source_name}] {ticker}: window price {w_price} mismatch with proj {expected_p} at {w_day}"
                    )

                    if w_type == 'top':
                        total_peaks += 1
                        # Invariant 4: Peak > cur_p
                        self.assertGreater(
                            w_price, cur_p,
                            f"[{source_name}] {ticker}: Peak price ${w_price:.2f} <= cur_p ${cur_p:.2f} at {w_day}"
                        )
                        # Check local extrema condition (radius 2)
                        p_t = proj_prices[d_idx - 1]
                        p_prev = proj_prices[d_idx - 2]
                        p_next = proj_prices[d_idx]
                        self.assertGreater(
                            p_t, p_prev,
                            f"[{source_name}] {ticker}: Peak at {w_day} (${p_t}) not > P[t-1] (${p_prev})"
                        )
                        self.assertGreaterEqual(
                            p_t, p_next,
                            f"[{source_name}] {ticker}: Peak at {w_day} (${p_t}) not >= P[t+1] (${p_next})"
                        )

                    elif w_type in ['bottom', 'caution']:
                        if w_type == 'bottom':
                            total_valleys += 1
                        else:
                            total_cautions += 1

                        # Check local extrema condition
                        p_t = proj_prices[d_idx - 1]
                        p_prev = proj_prices[d_idx - 2]
                        p_next = proj_prices[d_idx]
                        self.assertLess(
                            p_t, p_prev,
                            f"[{source_name}] {ticker}: Valley at {w_day} (${p_t}) not < P[t-1] (${p_prev})"
                        )
                        self.assertLessEqual(
                            p_t, p_next,
                            f"[{source_name}] {ticker}: Valley at {w_day} (${p_t}) not <= P[t+1] (${p_next})"
                        )

                        # Invariant 5: Caution window override
                        is_over_r1 = (r1 is not None and r1 > 0 and w_price >= r1)
                        is_over_r2 = (r2 is not None and r2 > 0 and w_price >= r2)

                        if is_over_r1 or is_over_r2:
                            self.assertEqual(
                                w_type, 'caution',
                                f"[{source_name}] {ticker}: Valley at {w_day} (${w_price:.2f}) >= R1(${r1}) / R2(${r2}) "
                                f"must be 'caution', but got '{w_type}'"
                            )
                            self.assertIn(
                                "⚠️ 高位强势休整窗 (已超R1/R2阻力·勿追高)", w.get('desc', ''),
                                f"[{source_name}] {ticker}: Caution desc mismatch: {w.get('desc')}"
                            )
                        else:
                            self.assertEqual(
                                w_type, 'bottom',
                                f"[{source_name}] {ticker}: Valley at {w_day} (${w_price:.2f}) < R1(${r1}) "
                                f"must be 'bottom', but got '{w_type}'"
                            )
                            self.assertIn(
                                "🔵 周期底回踩窗 (关注空头回补企稳)", w.get('desc', ''),
                                f"[{source_name}] {ticker}: Bottom desc mismatch: {w.get('desc')}"
                            )

                # Invariant 6: Minimum interval >= 2 trading days between adjacent windows
                for i in range(1, len(windows)):
                    d_prev = int(windows[i-1]['day'].replace('T+', ''))
                    d_curr = int(windows[i]['day'].replace('T+', ''))
                    self.assertGreaterEqual(
                        d_curr - d_prev, 2,
                        f"[{source_name}] {ticker}: Adjacent windows {windows[i-1]['day']} and {windows[i]['day']} "
                        f"have interval {d_curr - d_prev} < 2"
                    )

                # Invariant 7: Peak > adjacent local valley in same wave (no price inversion)
                for i in range(len(windows) - 1):
                    w1 = windows[i]
                    w2 = windows[i+1]
                    if w1['type'] == 'top' and w2['type'] in ['bottom', 'caution']:
                        self.assertGreater(
                            float(w1['price']), float(w2['price']),
                            f"[{source_name}] {ticker}: Price inversion: Peak {w1['day']} (${w1['price']}) "
                            f"<= Valley {w2['day']} (${w2['price']})"
                        )
                    elif w1['type'] in ['bottom', 'caution'] and w2['type'] == 'top':
                        self.assertGreater(
                            float(w2['price']), float(w1['price']),
                            f"[{source_name}] {ticker}: Price inversion: Valley {w1['day']} (${w1['price']}) "
                            f">= Peak {w2['day']} (${w2['price']})"
                        )

        print(f"[{source_name}] Audit Passed: {len(self.expected_tickers)} tickers verified. "
              f"Windows: {total_windows} (Peaks: {total_peaks}, Valleys: {total_valleys}, Cautions: {total_cautions}).")

    def test_production_json_audit(self):
        """Run full audit on data_cache/latest_ai_predictions.json."""
        self._audit_dataset(self.json_tcp, "latest_ai_predictions.json")

    def test_production_html_audit(self):
        """Run full audit on predict_radar_5y.html."""
        self._audit_dataset(self.html_tcp, "predict_radar_5y.html")

    def test_json_and_html_synchronization(self):
        """Assert exact parity between JSON and HTML time cycle datasets."""
        for ticker in self.expected_tickers:
            j_item = self.json_tcp[ticker]
            h_item = self.html_tcp[ticker]

            self.assertEqual(j_item.get('r1'), h_item.get('r1'), f"{ticker} R1 mismatch")
            self.assertEqual(j_item.get('r2'), h_item.get('r2'), f"{ticker} R2 mismatch")
            self.assertEqual(len(j_item.get('projection', [])), len(h_item.get('projection', [])), f"{ticker} proj len mismatch")
            self.assertEqual(len(j_item.get('cycle_windows', [])), len(h_item.get('cycle_windows', [])), f"{ticker} windows len mismatch")

            for j_w, h_w in zip(j_item.get('cycle_windows', []), h_item.get('cycle_windows', [])):
                self.assertEqual(j_w['day'], h_w['day'], f"{ticker} window day mismatch")
                self.assertEqual(j_w['type'], h_w['type'], f"{ticker} window type mismatch")
                self.assertEqual(j_w['price'], h_w['price'], f"{ticker} window price mismatch")
                self.assertEqual(j_w['desc'], h_w['desc'], f"{ticker} window desc mismatch")


if __name__ == '__main__':
    unittest.main(verbosity=2)
