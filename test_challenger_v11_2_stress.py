"""
Empirical Challenger Test Suite for R3 and R4
Agent: challenger_v11_2 (role: teamwork_challenger)
Target: ml_predictor_5y.py (Return metrics, Table 4 sector linkage, macro weather)

Validates:
1. Return metrics calculation (Realized_Return.mean(), win_loss_ratio, profit_factor)
   across extreme payoff distributions: 100% wins, 100% losses, zero trades,
   all zero returns, alternating trades, asymmetric payoffs.
2. Table 4 sector linkage: win rate boundary conditions (44.90% vs 44.99% vs 45.00% vs 45.01%),
   warning badge application, uncorrupted ranking scores and probabilities.
3. evaluate_macro_weather() exhaustive truth table and edge cases.
4. Process isolation of integrated_market_radar.py.
"""

import os
import sys
import unittest
import numpy as np
import pandas as pd

# Add parent path to import ml_predictor_5y
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from ml_predictor_5y import (
    evaluate_macro_weather,
    generate_report_tables,
    SECTOR_MAP,
    SECTOR_CHINESE_MAP,
    FEATURE_COLS,
)


class DummyModel:
    """Mock model providing predict_proba or feature_importances_ for test isolation"""
    def __init__(self, fixed_probs=None, n_features=len(FEATURE_COLS)):
        self.fixed_probs = fixed_probs
        self.feature_importances_ = np.ones(n_features)

    def predict_proba(self, X):
        n = len(X)
        if self.fixed_probs is not None:
            probs = np.array(self.fixed_probs[:n])
        else:
            probs = np.linspace(0.80, 0.40, n)
        return np.column_stack([1.0 - probs, probs])


class TestR3ReturnMetricsStress(unittest.TestCase):
    """
    Stress test Realized_Return.mean(), win_loss_ratio, and profit_factor
    across extreme and adversarial payoff distributions.
    """

    def setUp(self):
        self.model = DummyModel()
        self.feature_cols = FEATURE_COLS

    def _create_mock_oos_df(self, realized_returns, labels=None, prob=0.75, sector='Semiconductors'):
        """Helper to construct synthetic oos_df with specified returns"""
        n = len(realized_returns)
        if labels is None:
            labels = [1.0 if r > 0 else 0.0 for r in realized_returns]
        return pd.DataFrame({
            'Ticker': ['NVDA'] * n,
            'Sector': [sector] * n,
            'Prob_1': [prob] * n,
            'Label': labels,
            'Realized_Return': realized_returns,
            'Max_Gain_5d': realized_returns
        })

    def _create_mock_latest_stocks(self, n=10):
        """Helper to create minimal latest_features_df"""
        tickers = list(SECTOR_MAP.keys())[:n]
        data = {
            'Ticker': tickers,
            'Sector': [SECTOR_MAP[t] for t in tickers],
            'Close': [100.0] * n,
            'ATR_Pct': [2.0] * n,
            'QQQM_Bias_MA50': [1.0] * n,
            'SMH_Bias_MA50': [1.0] * n,
            'VIXY_Pos_60': [0.4] * n,
            'SMH_IGV_Ratio_Bias': [0.5] * n,
        }
        for col in self.feature_cols:
            if col not in data:
                data[col] = [1.0] * n
        return pd.DataFrame(data)

    def test_100pct_wins_payoff(self):
        """
        Payoff scenario: 100% wins.
        Realized returns: [+3.5%, +4.2%, +5.1%, +10.0%]
        Expectations:
        - avg_realized_return_pct == 5.70%
        - win_rate_pct == 100.0%
        - win_loss_ratio == 99.99 (infinite ceiling, no ZeroDivisionError)
        - profit_factor == 99.99 (infinite ceiling, no ZeroDivisionError)
        """
        returns = [3.5, 4.2, 5.1, 10.0]
        oos_df = self._create_mock_oos_df(returns, prob=0.75)
        latest_stocks = self._create_mock_latest_stocks()

        tables = generate_report_tables(
            oos_df=oos_df,
            model=self.model,
            feature_cols=self.feature_cols,
            latest_features_df=latest_stocks
        )

        t1_row = [r for r in tables['table1'] if r['threshold_label'] == '>= 70%'][0]
        self.assertEqual(t1_row['trigger_count'], 4)
        self.assertEqual(t1_row['win_rate_pct'], 100.0)
        self.assertAlmostEqual(t1_row['avg_realized_return_pct'], 5.70, places=2)
        self.assertEqual(t1_row['win_loss_ratio'], 99.99)
        self.assertEqual(t1_row['profit_factor'], 99.99)

        # Check Table 2 sector row
        t2_semi = [r for r in tables['table2'] if r['sector_key'] == 'Semiconductors'][0]
        self.assertEqual(t2_semi['high_conviction_win_rate_pct'], 100.0)
        self.assertAlmostEqual(t2_semi['avg_realized_return_pct'], 5.70, places=2)
        self.assertEqual(t2_semi['win_loss_ratio'], 99.99)
        self.assertEqual(t2_semi['profit_factor'], 99.99)

    def test_100pct_losses_payoff(self):
        """
        Payoff scenario: 100% losses.
        Realized returns: [-2.2%, -2.2%, -3.5%, -5.0%]
        Expectations:
        - avg_realized_return_pct == -3.23% (mean is -3.225%, round(-3.225, 2) is -3.23)
        - win_rate_pct == 0.0%
        - win_loss_ratio == 0.00
        - profit_factor == 0.00
        """
        returns = [-2.0, -2.5, -3.5, -4.0]
        oos_df = self._create_mock_oos_df(returns, labels=[0.0]*4, prob=0.75)
        latest_stocks = self._create_mock_latest_stocks()

        tables = generate_report_tables(
            oos_df=oos_df,
            model=self.model,
            feature_cols=self.feature_cols,
            latest_features_df=latest_stocks
        )

        t1_row = [r for r in tables['table1'] if r['threshold_label'] == '>= 70%'][0]
        self.assertEqual(t1_row['trigger_count'], 4)
        self.assertEqual(t1_row['win_rate_pct'], 0.0)
        self.assertEqual(t1_row['avg_realized_return_pct'], round(np.mean(returns), 2))
        self.assertEqual(t1_row['win_loss_ratio'], 0.0)
        self.assertEqual(t1_row['profit_factor'], 0.0)

        t2_semi = [r for r in tables['table2'] if r['sector_key'] == 'Semiconductors'][0]
        self.assertEqual(t2_semi['high_conviction_win_rate_pct'], 0.0)
        self.assertEqual(t2_semi['avg_realized_return_pct'], round(np.mean(returns), 2))
        self.assertEqual(t2_semi['win_loss_ratio'], 0.0)
        self.assertEqual(t2_semi['profit_factor'], 0.0)

    def test_zero_trades_empty_sub(self):
        """
        Payoff scenario: zero trades trigger a threshold.
        Expectations:
        - trigger_count == 0
        - win_rate_pct == 0.0
        - avg_realized_return_pct == 0.0
        - win_loss_ratio == 0.0
        - profit_factor == 0.0
        - No unhandled exception or crash.
        """
        returns = [1.0, 2.0]
        oos_df = self._create_mock_oos_df(returns, prob=0.50)
        latest_stocks = self._create_mock_latest_stocks()

        tables = generate_report_tables(
            oos_df=oos_df,
            model=self.model,
            feature_cols=self.feature_cols,
            latest_features_df=latest_stocks
        )

        for row in tables['table1']:
            self.assertEqual(row['trigger_count'], 0)
            self.assertEqual(row['win_rate_pct'], 0.0)
            self.assertEqual(row['avg_realized_return_pct'], 0.0)
            self.assertEqual(row['win_loss_ratio'], 0.0)
            self.assertEqual(row['profit_factor'], 0.0)

    def test_all_zero_flatline_trades(self):
        """
        Payoff scenario: all trades exit at break-even (Realized_Return == 0.0).
        Expectations:
        - avg_realized_return_pct == 0.00%
        - win_loss_ratio == 1.00 (neutral default when neither win nor loss occurs)
        - profit_factor == 1.00
        """
        returns = [0.0, 0.0, 0.0]
        oos_df = self._create_mock_oos_df(returns, labels=[0.0]*3, prob=0.75)
        latest_stocks = self._create_mock_latest_stocks()

        tables = generate_report_tables(
            oos_df=oos_df,
            model=self.model,
            feature_cols=self.feature_cols,
            latest_features_df=latest_stocks
        )

        t1_row = [r for r in tables['table1'] if r['threshold_label'] == '>= 70%'][0]
        self.assertEqual(t1_row['avg_realized_return_pct'], 0.0)
        self.assertEqual(t1_row['win_loss_ratio'], 1.00)
        self.assertEqual(t1_row['profit_factor'], 1.00)

    def test_alternating_trades_balanced(self):
        """
        Payoff scenario: alternating [+4.0%, -2.0%, +4.0%, -2.0%]
        Expectations:
        - avg_win = 4.0%, avg_loss = 2.0%
        - win_loss_ratio = 4.0 / 2.0 = 2.00
        - pos_sum = 8.0, neg_sum = 4.0
        - profit_factor = 8.0 / 4.0 = 2.00
        - avg_realized_return_pct = (4 - 2 + 4 - 2) / 4 = 1.00%
        """
        returns = [4.0, -2.0, 4.0, -2.0]
        labels = [1.0, 0.0, 1.0, 0.0]
        oos_df = self._create_mock_oos_df(returns, labels=labels, prob=0.75)
        latest_stocks = self._create_mock_latest_stocks()

        tables = generate_report_tables(
            oos_df=oos_df,
            model=self.model,
            feature_cols=self.feature_cols,
            latest_features_df=latest_stocks
        )

        t1_row = [r for r in tables['table1'] if r['threshold_label'] == '>= 70%'][0]
        self.assertEqual(t1_row['win_rate_pct'], 50.0)
        self.assertAlmostEqual(t1_row['avg_realized_return_pct'], 1.00, places=2)
        self.assertAlmostEqual(t1_row['win_loss_ratio'], 2.00, places=2)
        self.assertAlmostEqual(t1_row['profit_factor'], 2.00, places=2)

    def test_asymmetric_payoffs(self):
        """
        Payoff scenario: 9 small wins of +1.0% each, 1 big loss of -9.0%.
        - win_rate = 90.0%
        - avg_realized_ret = (9*1.0 - 9.0) / 10 = 0.00%
        - avg_win = 1.0%, avg_loss = 9.0% -> win_loss_ratio = 1.0 / 9.0 = 0.11
        - pos_sum = 9.0, neg_sum = 9.0 -> profit_factor = 9.0 / 9.0 = 1.00
        """
        returns = [1.0] * 9 + [-9.0]
        labels = [1.0] * 9 + [0.0]
        oos_df = self._create_mock_oos_df(returns, labels=labels, prob=0.75)
        latest_stocks = self._create_mock_latest_stocks()

        tables = generate_report_tables(
            oos_df=oos_df,
            model=self.model,
            feature_cols=self.feature_cols,
            latest_features_df=latest_stocks
        )

        t1_row = [r for r in tables['table1'] if r['threshold_label'] == '>= 70%'][0]
        self.assertEqual(t1_row['win_rate_pct'], 90.0)
        self.assertAlmostEqual(t1_row['avg_realized_return_pct'], 0.00, places=2)
        self.assertAlmostEqual(t1_row['win_loss_ratio'], 0.11, places=2)
        self.assertAlmostEqual(t1_row['profit_factor'], 1.00, places=2)


class TestR4Table4SectorLinkageAndBoundary(unittest.TestCase):
    """
    Stress test Table 4 sector historical win rate linkage,
    testing precise boundary conditions (44.90% vs 44.99% vs 45.00% vs 45.01%),
    action badge application, and verifying ranking scores/probabilities are uncorrupted.
    """

    def setUp(self):
        self.feature_cols = FEATURE_COLS

    def _setup_pipeline_for_sector_wr(self, sector_wr_map: dict, stock_probs: list, n_samples: int = 1000):
        """
        Constructs an oos_df that generates exactly the specified sector win rates in Table 2,
        and latest_features_df with specified probabilities.
        """
        oos_rows = []
        for sec_key, wr in sector_wr_map.items():
            n_wins = int(round((wr / 100.0) * n_samples))
            n_losses = n_samples - n_wins
            for _ in range(n_wins):
                oos_rows.append({
                    'Ticker': 'MOCK',
                    'Sector': sec_key,
                    'Prob_1': 0.70,
                    'Label': 1.0,
                    'Realized_Return': 3.0,
                    'Max_Gain_5d': 3.0
                })
            for _ in range(n_losses):
                oos_rows.append({
                    'Ticker': 'MOCK',
                    'Sector': sec_key,
                    'Prob_1': 0.70,
                    'Label': 0.0,
                    'Realized_Return': -2.2,
                    'Max_Gain_5d': 0.5
                })

        oos_df = pd.DataFrame(oos_rows)

        # Build latest_features_df
        tickers = ['NVDA', 'MU', 'COHR', 'VRT', 'PLTR', 'APP', 'MSTR', 'AMD', 'AVGO', 'TSM']
        latest_rows = []
        for i, tk in enumerate(tickers):
            sec = SECTOR_MAP.get(tk, 'Semiconductors')
            prob = stock_probs[i]
            row = {
                'Ticker': tk,
                'Sector': sec,
                'Close': 100.0 + i * 10,
                'ATR_Pct': 2.0,
                'Prob_1': prob,
                'QQQM_Bias_MA50': 1.0,
                'SMH_Bias_MA50': 1.0,
                'VIXY_Pos_60': 0.4,
                'SMH_IGV_Ratio_Bias': 0.5
            }
            for col in self.feature_cols:
                if col not in row:
                    row[col] = 1.0
            latest_rows.append(row)

        latest_df = pd.DataFrame(latest_rows)
        model = DummyModel(fixed_probs=stock_probs)

        return generate_report_tables(
            oos_df=oos_df,
            model=model,
            feature_cols=self.feature_cols,
            latest_features_df=latest_df
        )

    def test_win_rate_boundary_precision_44_9_vs_45_0(self):
        """
        Verify that a sector with win rate 44.9% triggers the warning,
        and sectors with >= 45.0% do NOT trigger the warning.
        """
        sector_wrs = {
            'Semiconductors': 44.9,       # 449 / 1000 = 44.9% -> MUST trigger warning
            'Memory': 45.0,              # 450 / 1000 = 45.0% -> MUST NOT trigger warning
            'Optical_Interconnect': 45.1,# 451 / 1000 = 45.1% -> MUST NOT trigger warning
            'DataCenter_Power': 65.0,
            'AI_Software': 70.0,
            'AdTech_ECommerce': 60.0,
            'Crypto_Proxy': 40.0
        }
        stock_probs = [0.80, 0.78, 0.75, 0.72, 0.68, 0.65, 0.62, 0.58, 0.55, 0.52]

        tables = self._setup_pipeline_for_sector_wr(sector_wrs, stock_probs, n_samples=1000)
        t4 = tables['table4']

        nvda_row = [r for r in t4 if r['ticker'] == 'NVDA'][0]
        mu_row = [r for r in t4 if r['ticker'] == 'MU'][0]
        cohr_row = [r for r in t4 if r['ticker'] == 'COHR'][0]

        # NVDA is in Semiconductors (44.9% < 45.0%) -> MUST trigger warning
        self.assertIn("⚠️ 赛道历史胜率偏低·观望", nvda_row['action'])
        self.assertEqual(nvda_row['action_tag'], "action-warning")

        # MU is in Memory (45.0% >= 45.0%) -> MUST NOT trigger warning
        self.assertNotIn("⚠️", mu_row['action'])
        self.assertEqual(mu_row['action_tag'], "action-buy")

        # COHR is in Optical (45.1% > 45.0%) -> MUST NOT trigger warning
        self.assertNotIn("⚠️", cohr_row['action'])
        self.assertEqual(cohr_row['action_tag'], "action-buy")

    def test_win_rate_rounding_boundary_44_99_loss_of_precision(self):
        """
        Adversarial Boundary Analysis:
        Tests what happens when true sector win rate is 44.99% (4499 / 10000).
        Empirical observation:
        In Table 2, high_conviction_win_rate_pct is computed as round(high_win_rate, 1) = 45.0%.
        Table 4 builds sector_oos_wr_map from Table 2's high_conviction_win_rate_pct.
        Therefore, at wr = 44.99%, Table 4 reads 45.0%, and condition (45.0 < 45.0) evaluates to False!
        This confirms an empirical loss of precision: the warning filter triggers on wr < 44.95%,
        rather than true wr < 45.00%.
        """
        sector_wrs = {
            'Semiconductors': 44.99,      # 4499 / 10000 = 44.99%
            'Memory': 45.00,
            'Optical_Interconnect': 45.01,
            'DataCenter_Power': 65.0,
            'AI_Software': 70.0,
            'AdTech_ECommerce': 60.0,
            'Crypto_Proxy': 40.0
        }
        stock_probs = [0.80, 0.78, 0.75, 0.72, 0.68, 0.65, 0.62, 0.58, 0.55, 0.52]

        tables = self._setup_pipeline_for_sector_wr(sector_wrs, stock_probs, n_samples=10000)
        t2_semi = [r for r in tables['table2'] if r['sector_key'] == 'Semiconductors'][0]
        t4_nvda = [r for r in tables['table4'] if r['ticker'] == 'NVDA'][0]

        # Table 2 rounds 44.99% to 45.0%
        self.assertEqual(t2_semi['high_conviction_win_rate_pct'], 45.0)

        # Because sector_oos_wr_map is built from Table 2's 1-decimal rounded values,
        # NVDA (Semiconductors) receives NO warning at 44.99%!
        # This empirically proves that the active threshold in implementation is < 44.95% (rounded to 44.9%).
        has_warning_at_44_99 = ("⚠️" in t4_nvda['action'])
        # Document empirical behavior:
        print(f"\n[Empirical Finding] At wr=44.99%: Table2 WR={t2_semi['high_conviction_win_rate_pct']}%, Action={t4_nvda['action']}")
        self.assertFalse(
            has_warning_at_44_99,
            "Empirical verification: 44.99% is rounded to 45.0% by Table 2, evading the < 45.0% warning"
        )

    def test_ranking_scores_and_probabilities_uncorrupted(self):
        """
        Verify that applying the warning badge NEVER alters:
        1. Ranking order (Rank 1 remains Rank 1)
        2. Probability values (predicted_win_rate_pct exactly reflects raw model Prob_1)
        3. Sorting monotonicity
        4. Target/stop prices and percentages
        """
        sector_wrs = {
            'Semiconductors': 30.0,  # Rank 1 (NVDA) has prob 0.85 -> triggers warning
            'Memory': 65.0,         # Rank 2 (MU) has prob 0.80 -> no warning
            'Optical_Interconnect': 70.0,
            'DataCenter_Power': 60.0,
            'AI_Software': 60.0,
            'AdTech_ECommerce': 60.0,
            'Crypto_Proxy': 35.0
        }
        stock_probs = [0.85, 0.80, 0.75, 0.70, 0.68, 0.65, 0.60, 0.58, 0.55, 0.52]

        tables = self._setup_pipeline_for_sector_wr(sector_wrs, stock_probs, n_samples=100)
        t4 = tables['table4']

        # Assert NVDA is STILL rank 1
        self.assertEqual(t4[0]['rank'], 1)
        self.assertEqual(t4[0]['ticker'], 'NVDA')
        self.assertEqual(t4[0]['predicted_win_rate_pct'], 85.0)
        self.assertEqual(t4[0]['action'], "⚠️ 赛道历史胜率偏低·观望")
        self.assertEqual(t4[0]['action_tag'], "action-warning")

        # Assert MU is STILL rank 2
        self.assertEqual(t4[1]['rank'], 2)
        self.assertEqual(t4[1]['ticker'], 'MU')
        self.assertEqual(t4[1]['predicted_win_rate_pct'], 80.0)
        self.assertEqual(t4[1]['action'], "🔥 极高确定性右侧做多")
        self.assertEqual(t4[1]['action_tag'], "action-buy")

        # Verify strict probability monotonicity down Table 4
        all_probs = [r['predicted_win_rate_pct'] for r in t4]
        for i in range(len(all_probs) - 1):
            self.assertGreaterEqual(
                all_probs[i], all_probs[i+1],
                f"Table 4 probabilities must strictly decrease or equal: {all_probs}"
            )

        # Verify target and stop mathematical integrity for NVDA (price=100.0, ATR=2.0%)
        self.assertEqual(t4[0]['target_profit_price'], 103.00)
        self.assertEqual(t4[0]['target_profit_pct'], 3.0)
        self.assertEqual(t4[0]['stop_loss_price'], 97.80)
        self.assertEqual(t4[0]['stop_loss_pct'], 2.2)


class TestR4MacroWeatherMatrix(unittest.TestCase):
    """
    Stress test evaluate_macro_weather() across full parameter matrix:
    - Dual breakdown: QQQM < -3.0% AND SMH < -3.0% -> 🌧️ 暴雨空仓
    - Single side deviation: -2.9% & -3.1% -> ⛅ 阴天防守
    - Bull base: QQQM >= 0.25% AND SMH >= 0.25% AND VIXY_Pos < 0.75 -> ☀️ 晴天做多
    - Bull base with VIX panic: VIXY_Pos >= 0.75 -> ⛅ 阴天防守
    """

    def test_macro_weather_truth_table(self):
        matrix_cases = [
            # Case 1: Dual breakdown clear
            {"qqqm": -3.1, "smh": -3.1, "vix": 0.50, "exp_status": "🌧️ 暴雨空仓", "exp_badge": "weather-rainy"},
            # Case 2: Extreme flash crash
            {"qqqm": -15.0, "smh": -20.0, "vix": 0.95, "exp_status": "🌧️ 暴雨空仓", "exp_badge": "weather-rainy"},
            # Case 3: Marginal dual breakdown (-3.01%)
            {"qqqm": -3.01, "smh": -3.01, "vix": 0.50, "exp_status": "🌧️ 暴雨空仓", "exp_badge": "weather-rainy"},
            # Case 4: Strict boundary at -3.00% (not strictly < -3.0) -> cloudy
            {"qqqm": -3.00, "smh": -3.00, "vix": 0.50, "exp_status": "⛅ 阴天防守", "exp_badge": "weather-cloudy"},
            # Case 5: Asymmetric QQQM safe, SMH breakdown (-2.9% & -3.1%)
            {"qqqm": -2.90, "smh": -3.10, "vix": 0.50, "exp_status": "⛅ 阴天防守", "exp_badge": "weather-cloudy"},
            # Case 6: Asymmetric QQQM breakdown, SMH safe (-3.1% & -2.9%)
            {"qqqm": -3.10, "smh": -2.90, "vix": 0.50, "exp_status": "⛅ 阴天防守", "exp_badge": "weather-cloudy"},
            # Case 7: Flat / neutral
            {"qqqm": 0.00, "smh": 0.00, "vix": 0.50, "exp_status": "⛅ 阴天防守", "exp_badge": "weather-cloudy"},
            # Case 8: Bull boundary just below threshold (0.24% & 0.25%)
            {"qqqm": 0.24, "smh": 0.25, "vix": 0.50, "exp_status": "⛅ 阴天防守", "exp_badge": "weather-cloudy"},
            # Case 9: Bull boundary (0.25% & 0.25%, VIX=0.50)
            {"qqqm": 0.25, "smh": 0.25, "vix": 0.50, "exp_status": "☀️ 晴天做多", "exp_badge": "weather-sunny"},
            # Case 10: Clear bull (0.50% & 0.50%, VIX=0.50)
            {"qqqm": 0.50, "smh": 0.50, "vix": 0.50, "exp_status": "☀️ 晴天做多", "exp_badge": "weather-sunny"},
            # Case 11: Strong bull with VIX right below panic (VIX=0.74)
            {"qqqm": 2.00, "smh": 3.00, "vix": 0.74, "exp_status": "☀️ 晴天做多", "exp_badge": "weather-sunny"},
            # Case 12: Strong bull with VIX panic veto (VIX=0.75)
            {"qqqm": 2.00, "smh": 3.00, "vix": 0.75, "exp_status": "⛅ 阴天防守", "exp_badge": "weather-cloudy"},
            # Case 13: Strong bull with high VIX panic (VIX=0.90)
            {"qqqm": 2.00, "smh": 3.00, "vix": 0.90, "exp_status": "⛅ 阴天防守", "exp_badge": "weather-cloudy"},
        ]

        for idx, case in enumerate(matrix_cases, 1):
            res = evaluate_macro_weather(
                qqqm_bias=case["qqqm"],
                smh_bias=case["smh"],
                vixy_pos=case["vix"],
                latest_date="2026-11-02"
            )
            self.assertEqual(
                res['status'], case['exp_status'],
                f"Case {idx} failed: inputs ({case['qqqm']}, {case['smh']}, {case['vix']}) "
                f"expected {case['exp_status']} but got {res['status']}"
            )
            self.assertEqual(
                res['badge_class'], case['exp_badge'],
                f"Case {idx} badge failed: expected {case['exp_badge']} but got {res['badge_class']}"
            )


class TestR5ProcessIsolation(unittest.TestCase):
    """
    Verify process isolation:
    1. integrated_market_radar.py file is completely unmodified in git working directory.
    2. integrated_market_radar.py background processes are active.
    """

    def test_integrated_market_radar_file_clean(self):
        """Verify git status of integrated_market_radar.py is clean"""
        import subprocess
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain", "integrated_market_radar.py"],
                cwd=CURRENT_DIR,
                capture_output=True,
                text=True,
                check=True
            )
            self.assertEqual(
                res.stdout.strip(), "",
                f"integrated_market_radar.py has git working tree modifications: {res.stdout}"
            )
        except FileNotFoundError:
            self.skipTest("git command not found")

    def test_daemon_processes_active(self):
        """Verify integrated_market_radar.py processes exist"""
        import subprocess
        ps_cmd = 'Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*integrated_market_radar.py*" } | Select-Object -ExpandProperty ProcessId'
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True
        )
        pids = [p.strip() for p in res.stdout.splitlines() if p.strip()]
        self.assertGreater(
            len(pids), 0,
            "Expected at least one active integrated_market_radar.py daemon process"
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
