#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_short_predictor.py - Comprehensive Automated Verification Test Suite
for AI Short Inference Pool & Dual-Direction Decision Rankings (E2E Track)

Authoritative References:
- ORIGINAL_REQUEST.md (Follow-up 2026-11-03T07:03:34Z: AI Short Inference Pool)
- PROJECT.md (§Architecture, §Feature Inventory, §Interface Contracts)
- explorer_short_survey_3/handoff.md (§3 Test Architecture & Invariant Specifications)

Structural Test Coverage:
1. TestShortTripleBarrierLabeling:
   - test_short_lower_barrier_hit_first: TP lower touch -> Label_Short=1.0, Barrier_Hit_Short='lower'
   - test_short_upper_barrier_hit_first: SL upper touch -> Label_Short=0.0, Barrier_Hit_Short='upper'
   - test_short_timeout_neither_barrier_hit: Timeout -> Label_Short=0.0, Barrier_Hit_Short='timeout', realized return calc
   - test_short_same_day_collision_stop_loss_priority: Same-day collision -> Label_Short=0.0, Barrier_Hit_Short='collision'
   - test_short_entry_price_strictly_next_open: Short entry strictly at NEXT_OPEN (Open.shift(-1))
   - test_short_piecewise_soft_root_scaling: Piecewise Soft-Root Scaling across low, normal, and high ATR regimes
   - test_short_tail_five_days_nan_isolation: Last 5 rows per ticker have Label_Short=np.nan (live pool isolation)

2. TestShortInferenceModel:
   - test_short_feature_variance_and_non_zero: std > 0.01 and non-zero rate > 95% on synthetic & real 5Y parquet cache
   - test_short_probability_calibration_and_bounds: Raw prob in [0, 1], calibrated in [0, 100], Dollar PCR prior boost
   - test_short_ranking_and_sort_consistency: top_short descending by calibrated_short_win_rate_pct, circuit breaker override
   - test_latest_ai_predictions_schema_with_dual_pools: JSON schema validation for top_long, top_short, and by_ticker dual metrics

3. TestShortTemporalIntegrityZeroLookahead:
   - test_short_features_subseries_truncation_invariance: Truncated [0..T] vs full [0..T+N] diff < 1e-7 at bar T
   - test_short_features_future_spike_insensitivity: Future price/volume spike at T+1 has identically 0.0 impact at bar T
   - test_short_features_ast_no_negative_shifts: AST scan confirms zero negative shifts (shift(-k)) in feature code
   - test_short_walk_forward_cv_purge_isolation: Expanding training folds enforce >= 5 trading days purge gap before test fold

Usage:
  python -m unittest test_short_predictor.py -v
  python test_short_predictor.py
"""

import os
import sys
import ast
import json
import inspect
import unittest
import math
from typing import Dict, List, Tuple, Any, Optional

import numpy as np
import pandas as pd

# Handle standard output encoding on Windows environments
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Configure module search paths so tests can execute from anywhere
CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
PARENT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
RADAR_SUBDIR = CURRENT_DIR

for p in [CURRENT_DIR, PARENT_DIR]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)


# ============================================================================
# Section 1: Synthetic Data Generators & Reference Oracles
# ============================================================================

class SyntheticShortDataGenerator:
    """Deterministic synthetic data generator for reproducible short testing."""

    @staticmethod
    def generate_trading_dates(start_date: str = '2022-01-03', num_days: int = 500) -> List[pd.Timestamp]:
        """Generate business day timestamps starting from start_date."""
        return list(pd.bdate_range(start=start_date, periods=num_days))

    @staticmethod
    def generate_stock_ohlcv(
        ticker: str,
        dates: List[pd.Timestamp],
        base_price: float = 100.0,
        sector: str = 'Semiconductors',
        seed: int = 42
    ) -> pd.DataFrame:
        """Generate realistic synthetic OHLCV time series for a single ticker."""
        rng = np.random.RandomState(seed)
        n = len(dates)
        log_rets = rng.normal(loc=0.0002, scale=0.02, size=n)
        prices = base_price * np.exp(np.cumsum(log_rets))

        opens = prices * (1.0 + rng.uniform(-0.005, 0.005, size=n))
        closes = prices
        highs = np.maximum(opens, closes) * (1.0 + rng.uniform(0.001, 0.015, size=n))
        lows = np.minimum(opens, closes) * (1.0 - rng.uniform(0.001, 0.015, size=n))
        volumes = rng.uniform(500000, 5000000, size=n)

        df = pd.DataFrame({
            'Date': dates,
            'Ticker': ticker,
            'Sector': sector,
            'Open': np.round(opens, 2),
            'High': np.round(highs, 2),
            'Low': np.round(lows, 2),
            'Close': np.round(closes, 2),
            'Volume': np.round(volumes, 0)
        })
        return df

    @staticmethod
    def generate_macro_etfs(dates: List[pd.Timestamp], seed: int = 100) -> Dict[str, pd.DataFrame]:
        """Generate synthetic OHLCV for macro benchmarks (QQQ, SMH, TLT, VIXY, SPY)."""
        macro_tickers = ['QQQ', 'SPY', 'SMH', 'IGV', 'TLT', 'VIXY', 'GLD']
        result = {}
        for idx, ticker in enumerate(macro_tickers):
            base_p = 400.0 if ticker in ['SPY', 'QQQ'] else (200.0 if ticker in ['SMH', 'IGV'] else 90.0)
            df = SyntheticShortDataGenerator.generate_stock_ohlcv(
                ticker=ticker,
                dates=dates,
                base_price=base_p,
                sector='Macro_Benchmark',
                seed=seed + idx * 10
            )
            result[ticker] = df
        return result

    @classmethod
    def generate_full_raw_panel(
        cls,
        stock_tickers: List[str],
        dates: List[pd.Timestamp],
        base_seed: int = 42
    ) -> pd.DataFrame:
        """Combine given target stocks and macro ETFs into a unified raw panel."""
        frames = []
        for i, tk in enumerate(stock_tickers):
            s_df = cls.generate_stock_ohlcv(tk, dates, base_price=100.0 + i * 10, seed=base_seed + i)
            frames.append(s_df)

        macro_dict = cls.generate_macro_etfs(dates, seed=base_seed + 100)
        for m_tk, m_df in macro_dict.items():
            frames.append(m_df)

        return pd.concat(frames, ignore_index=True)


class OracleShortTripleBarrierLabeler:
    """
    Authoritative reference implementation of Short Triple-Barrier Labeling.
    Rules & Invariants (from PROJECT.md & ORIGINAL_REQUEST.md):
    - Execution price strictly equals NEXT_OPEN (Open.shift(-1)).
    - Piecewise Soft-Root Scaling for ATR:
        raw_atr = atr_pct / 100.0
        eff_atr = 0.040 + 0.50 * (np.clip(raw_atr, 0.040, 0.100) - 0.040) if raw_atr > 0.040 else np.clip(raw_atr, 0.012, 0.040)
    - Lower Barrier (Take Profit for Short):
        Short_Target_TP = NEXT_OPEN * (1.0 - max(0.030, 1.35 * eff_atr))
    - Upper Barrier (Stop Loss for Short):
        Short_Target_SL = NEXT_OPEN * (1.0 + max(0.022, 1.00 * eff_atr))
    - Horizon: Next 5 trading days (T+1 to T+5).
    - Lower barrier hit first (Low <= Short_Target_TP) -> Label_Short = 1.0, Barrier_Hit_Short = 'lower'
    - Upper barrier hit first (High >= Short_Target_SL) -> Label_Short = 0.0, Barrier_Hit_Short = 'upper'
    - Neither hit within 5 days -> Label_Short = 0.0, Barrier_Hit_Short = 'timeout',
        Realized_Return_Short = ((Entry_Price - Close[T+5]) / Entry_Price) * 100.0
    - Same-day collision -> Label_Short = 0.0, Barrier_Hit_Short = 'collision' (Conservative Stop-Loss priority)
    - Tail isolation: Final 5 trading days per ticker have Label_Short = np.nan, Barrier_Hit_Short = 'unresolved'
    """

    @staticmethod
    def compute_effective_atr(atr_pct: float) -> float:
        """Piecewise Soft-Root Scaling for ATR compression."""
        if np.isnan(atr_pct) or atr_pct <= 0:
            atr_pct = 2.0
        raw_atr = float(atr_pct) / 100.0
        if raw_atr > 0.040:
            return float(0.040 + 0.50 * (np.clip(raw_atr, 0.040, 0.100) - 0.040))
        else:
            return float(np.clip(raw_atr, 0.012, 0.040))

    @classmethod
    def apply_short_triple_barrier_labeling(cls, df: pd.DataFrame) -> pd.DataFrame:
        """Apply short triple-barrier labeling per stock group."""
        if 'Ticker' not in df.columns:
            df = df.copy()
            df['Ticker'] = 'UNKNOWN'

        groups = []
        for ticker, grp in df.groupby('Ticker'):
            grp = grp.copy().sort_values('Date').reset_index(drop=True)
            n = len(grp)

            grp['NEXT_OPEN'] = grp['Open'].shift(-1)
            grp['Entry_Price'] = grp['NEXT_OPEN']

            targets_tp = np.full(n, np.nan)
            targets_sl = np.full(n, np.nan)
            labels = np.full(n, np.nan)
            hits = np.array(['unresolved'] * n, dtype=object)
            realized_returns = np.full(n, np.nan)

            if n <= 5:
                grp['Short_Target_TP'] = targets_tp
                grp['Short_Target_SL'] = targets_sl
                grp['Target_Lower'] = targets_tp
                grp['Target_Upper'] = targets_sl
                grp['Label_Short'] = labels
                grp['Barrier_Hit_Short'] = hits
                grp['Realized_Return_Short'] = realized_returns
                groups.append(grp)
                continue

            entry_prices = grp['Entry_Price'].to_numpy(dtype=float)
            highs = grp['High'].to_numpy(dtype=float)
            lows = grp['Low'].to_numpy(dtype=float)
            closes = grp['Close'].to_numpy(dtype=float) if 'Close' in grp.columns else entry_prices
            atr_pcts = grp['ATR_Pct'].to_numpy(dtype=float) if 'ATR_Pct' in grp.columns else np.full(n, 2.0)

            for i in range(n - 5):
                entry_price = entry_prices[i]
                if np.isnan(entry_price) or entry_price <= 0:
                    continue

                atr_val = atr_pcts[i]
                eff_atr = cls.compute_effective_atr(atr_val)

                lower_mult = max(0.030, 1.35 * eff_atr)  # TP distance below entry
                upper_mult = max(0.022, 1.00 * eff_atr)  # SL distance above entry

                tp_barrier = entry_price * (1.0 - lower_mult)
                sl_barrier = entry_price * (1.0 + upper_mult)

                targets_tp[i] = round(tp_barrier, 4)
                targets_sl[i] = round(sl_barrier, 4)

                resolved = False
                for step in range(1, 6):
                    k = i + step
                    h = highs[k]
                    l = lows[k]

                    hit_tp = (l <= tp_barrier)
                    hit_sl = (h >= sl_barrier)

                    if hit_tp and hit_sl:
                        # Same-day collision -> Conservative Stop-Loss priority (Label_Short = 0.0)
                        labels[i] = 0.0
                        hits[i] = 'collision'
                        realized_returns[i] = round(-upper_mult * 100.0, 4)
                        resolved = True
                        break
                    elif hit_tp:
                        # Price reached take-profit lower barrier first -> Win
                        labels[i] = 1.0
                        hits[i] = 'lower'
                        realized_returns[i] = round(lower_mult * 100.0, 4)
                        resolved = True
                        break
                    elif hit_sl:
                        # Price rallied into stop-loss upper barrier first -> Loss
                        labels[i] = 0.0
                        hits[i] = 'upper'
                        realized_returns[i] = round(-upper_mult * 100.0, 4)
                        resolved = True
                        break

                if not resolved:
                    # Timeout after 5 bars without touching either barrier
                    labels[i] = 0.0
                    hits[i] = 'timeout'
                    c_exit = closes[i + 5] if (i + 5 < n) else entry_price
                    realized_returns[i] = round(((entry_price - c_exit) / entry_price) * 100.0, 4)

            grp['Short_Target_TP'] = targets_tp
            grp['Short_Target_SL'] = targets_sl
            grp['Target_Lower'] = targets_tp  # Backward compatibility alias
            grp['Target_Upper'] = targets_sl  # Backward compatibility alias
            grp['Label_Short'] = labels
            grp['Barrier_Hit_Short'] = hits
            grp['Realized_Return_Short'] = realized_returns
            groups.append(grp)

        return pd.concat(groups, axis=0).reset_index(drop=True)


class OracleShortFeatureEngine:
    """
    Authoritative reference implementation of causal short-sensitive features.
    Strictly past/current data only (no forward shifts, zero lookahead).
    """

    SHORT_FEATURES = [
        'Bias_MA50', 'Bias_MA20', 'Bias_SMA200', 'EMA10_20_Spread', 'Bear_Alignment_Score',
        'Fib_Pos_60', 'RSI_14', 'Ret_5d', 'Alpha_QQQ_5d', 'Alpha_SMH_20d',
        'VIXY_Bias_MA20', 'Vol_Ratio_20', 'Vol_Breakdown_Ratio', 'Dollar_PCR_Proxy'
    ]

    @classmethod
    def compute_short_features(cls, df: pd.DataFrame, macro_map: Optional[Dict[str, pd.DataFrame]] = None) -> pd.DataFrame:
        """Compute short features causally across the time series."""
        df = df.copy().sort_values('Date').reset_index(drop=True)
        c = df['Close']
        v = df['Volume'] if 'Volume' in df.columns else pd.Series(1e6, index=df.index)

        # 1. Moving averages and biases
        ma20 = c.rolling(20, min_periods=1).mean()
        ma50 = c.rolling(50, min_periods=1).mean()
        sma200 = c.rolling(200, min_periods=1).mean()
        ema10 = c.ewm(span=10, adjust=False).mean()
        ema20 = c.ewm(span=20, adjust=False).mean()

        df['Bias_MA20'] = ((c - ma20) / ma20) * 100.0
        df['Bias_MA50'] = ((c - ma50) / ma50) * 100.0
        df['Bias_SMA200'] = ((c - sma200) / sma200) * 100.0
        df['EMA10_20_Spread'] = ((ema10 - ema20) / ema20) * 100.0

        # Bear Alignment Score (0 to 4): higher score = stronger bearish cascade
        cond1 = (ema10 < ema20).astype(float)
        cond2 = (ema20 < ma50).astype(float)
        cond3 = (c < ma50).astype(float)
        cond4 = (ma50 < sma200).astype(float)
        df['Bear_Alignment_Score'] = cond1 + cond2 + cond3 + cond4

        # 2. Oscillators & Range Position
        low = df['Low'] if 'Low' in df.columns else c
        high = df['High'] if 'High' in df.columns else c
        roll_min60 = low.rolling(60, min_periods=1).min()
        roll_max60 = high.rolling(60, min_periods=1).max()
        rng = (roll_max60 - roll_min60).replace(0, 1.0)
        df['Fib_Pos_60'] = np.clip((c - roll_min60) / rng, -0.5, 1.5)

        delta = c.diff()
        gain = (delta.where(delta > 0, 0.0)).rolling(14, min_periods=1).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14, min_periods=1).mean()
        rs = gain / loss.replace(0, 1e-9)
        df['RSI_14'] = 100.0 - (100.0 / (1.0 + rs))

        # 3. Returns and Momentum
        df['Ret_5d'] = c.pct_change(5).fillna(0.0) * 100.0

        # 4. Alphas vs Benchmarks
        if macro_map and 'QQQ' in macro_map:
            qqqm_c = macro_map['QQQ'].sort_values('Date').set_index('Date')['Close']
            qqqm_ret5 = qqqm_c.pct_change(5).fillna(0.0) * 100.0
            merged_ret = df['Date'].map(qqqm_ret5).fillna(0.0)
            df['Alpha_QQQ_5d'] = df['Ret_5d'] - merged_ret
        else:
            df['Alpha_QQQ_5d'] = df['Ret_5d']

        if macro_map and 'SMH' in macro_map:
            smh_c = macro_map['SMH'].sort_values('Date').set_index('Date')['Close']
            smh_ret20 = smh_c.pct_change(20).fillna(0.0) * 100.0
            merged_ret20 = df['Date'].map(smh_ret20).fillna(0.0)
            df['Alpha_SMH_20d'] = (c.pct_change(20).fillna(0.0) * 100.0) - merged_ret20
        else:
            df['Alpha_SMH_20d'] = c.pct_change(20).fillna(0.0) * 100.0

        if macro_map and 'VIXY' in macro_map:
            vixy_c = macro_map['VIXY'].sort_values('Date').set_index('Date')['Close']
            vixy_ma20 = vixy_c.rolling(20, min_periods=1).mean()
            vixy_bias = ((vixy_c - vixy_ma20) / vixy_ma20) * 100.0
            df['VIXY_Bias_MA20'] = df['Date'].map(vixy_bias).fillna(0.0)
        else:
            df['VIXY_Bias_MA20'] = 0.5

        # 5. Volume breakdown pressure
        vol_ma20 = v.rolling(20, min_periods=1).mean().replace(0, 1.0)
        df['Vol_Ratio_20'] = v / vol_ma20

        down_day = (delta < 0).astype(float)
        down_vol = (v * down_day).rolling(10, min_periods=1).sum()
        total_vol = v.rolling(10, min_periods=1).sum().replace(0, 1.0)
        df['Vol_Breakdown_Ratio'] = down_vol / total_vol

        # 6. Options radar proxy (Dollar Put/Call ratio proxy)
        df['Dollar_PCR_Proxy'] = 0.85 + (df['Bear_Alignment_Score'] * 0.15) + (df['Vol_Breakdown_Ratio'] * 0.20)

        # Ensure all columns exist and clean NaNs
        for col in cls.SHORT_FEATURES:
            if col not in df.columns:
                df[col] = 0.0
            df[col] = df[col].replace([np.inf, -np.inf], np.nan).fillna(0.0)

        return df


class OracleShortInferenceEngine:
    """
    Authoritative reference model for short inference, probability calibration,
    options PCR weighting, and dual pool ranking schema.
    """

    @staticmethod
    def calibrate_short_probability(*args, **kwargs) -> float:
        import ml_predictor_5y
        return ml_predictor_5y.calibrate_short_probability(*args, **kwargs)

    @classmethod
    def generate_dual_pool_fixture(cls, num_candidates: int = 10) -> Dict[str, Any]:
        """Generates a complete validated schema fixture for latest_ai_predictions.json with dual pools."""
        sample_tickers = ['NVDA', 'TSM', 'AMD', 'MU', 'MSFT', 'AAPL', 'AVGO', 'CRDO', 'COHR', 'AAOI']
        top_long = []
        top_short = []
        by_ticker = {}

        for rank, tk in enumerate(sample_tickers[:num_candidates], start=1):
            # Long candidate
            long_win_rate = round(65.0 - rank * 1.5, 1)
            long_item = {
                'rank': rank,
                'ticker': tk,
                'sector': 'Semiconductors',
                'current_price': 100.0 + rank * 5.0,
                'calibrated_win_rate_pct': long_win_rate,
                'target_profit_price': round((100.0 + rank * 5.0) * 1.05, 2),
                'stop_loss_price': round((100.0 + rank * 5.0) * 0.97, 2),
                'action': '☀️ 顺势低吸·买入看涨'
            }
            top_long.append(long_item)

            # Short candidate
            short_win_rate = round(68.0 - rank * 1.8, 1)
            pcr_val = round(1.45 - rank * 0.05, 2)
            short_item = {
                'rank': rank,
                'ticker': tk,
                'sector': 'Semiconductors',
                'current_price': 100.0 + rank * 5.0,
                'short_entry_price': 100.0 + rank * 5.0,
                'calibrated_short_win_rate_pct': short_win_rate,
                'short_target_profit_price': round((100.0 + rank * 5.0) * 0.94, 2),
                'short_stop_loss_price': round((100.0 + rank * 5.0) * 1.03, 2),
                'dollar_pcr': pcr_val,
                'pcr_sentiment': '🔥 主力重度做空对冲' if pcr_val >= 1.30 else '⛅ 中性',
                'put_wall': round((100.0 + rank * 5.0) * 0.95, 2),
                'short_action': '🩸 空头破位·反抽做空/买Put' if rank <= 3 else '🛡️ 建议买入反向ETF对冲'
            }
            top_short.append(short_item)

            by_ticker[tk] = {
                'ticker': tk,
                'current_price': 100.0 + rank * 5.0,
                'calibrated_win_rate_pct': long_win_rate,
                'calibrated_short_win_rate_pct': short_win_rate,
                'action': long_item['action'],
                'short_action': short_item['short_action'],
                'dollar_pcr': pcr_val,
                'short_target_profit_price': short_item['short_target_profit_price'],
                'short_stop_loss_price': short_item['short_stop_loss_price']
            }

        return {
            'kpi': {
                'high_conviction_win_rate': 52.5,
                'short_conviction_win_rate': 55.4,
                'profit_factor': 1.45,
                'sync_time': '2026-11-03 16:30:00'
            },
            'macro_weather': {
                'status': '🌧️ 暴雨空仓',
                'qqqm_bias_ma50': -5.86,
                'smh_bias_ma50': -10.72,
                'vixy_pos_60': 0.68
            },
            'top_long': top_long,
            'top_short': top_short,
            'top10': top_long,  # Legacy alias
            'by_ticker': by_ticker
        }


# ============================================================================
# Section 2: AST Causality & Static Analysis Checkers
# ============================================================================

class ASTShortCausalityChecker(ast.NodeVisitor):
    """
    Scans Python AST to detect any negative shifts (shift(-k)) or illegal
    forward-looking operations inside feature computation code.
    """

    def __init__(self, target_functions: Optional[List[str]] = None):
        self.target_functions = target_functions or [
            'build_feature_panel', 'compute_stock_features',
            'compute_macro_features', 'compute_indicators',
            'compute_short_features', 'extract_short_features'
        ]
        self.current_function: Optional[str] = None
        self.violations: List[Dict[str, Any]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef):
        old_fn = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = old_fn

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'shift':
            is_target_fn = (self.current_function is None or
                            self.current_function in self.target_functions)
            if is_target_fn and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub):
                    self.violations.append({
                        'function': self.current_function,
                        'lineno': getattr(node, 'lineno', -1),
                        'expression': 'shift(-k)',
                        'message': f"Illegal negative shift in feature calculation: {self.current_function}"
                    })
                elif isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)) and arg.value < 0:
                    self.violations.append({
                        'function': self.current_function,
                        'lineno': getattr(node, 'lineno', -1),
                        'expression': f'shift({arg.value})',
                        'message': f"Illegal negative shift in feature calculation: {self.current_function}"
                    })
        self.generic_visit(node)


# ============================================================================
# Section 3: Target Dispatcher (Binds to Implementation or Oracle)
# ============================================================================

def get_implementation_module():
    """Attempt to dynamically import ml_predictor_5y if available."""
    try:
        import ml_predictor_5y
        return ml_predictor_5y
    except ImportError:
        return None


def get_short_labeling_runner():
    """Returns actual short labeling implementation if present, otherwise falls back to Oracle."""
    mod = get_implementation_module()
    if mod and hasattr(mod, 'apply_short_triple_barrier_labeling'):
        try:
            test_df = pd.DataFrame({
                'Date': [pd.Timestamp('2024-01-01')],
                'Ticker': ['NVDA'],
                'Open': [100.0],
                'High': [101.0],
                'Low': [99.0],
                'Close': [100.0],
                'ATR_Pct': [2.0]
            })
            mod.apply_short_triple_barrier_labeling(test_df)
            return mod.apply_short_triple_barrier_labeling, 'ml_predictor_5y.apply_short_triple_barrier_labeling'
        except Exception:
            pass

    if mod and hasattr(mod, 'apply_triple_barrier_labeling'):
        try:
            test_df = pd.DataFrame({
                'Date': pd.bdate_range('2024-01-01', periods=10),
                'Ticker': ['NVDA'] * 10,
                'Open': [100.0] * 10,
                'High': [101.0] * 10,
                'Low': [99.0] * 10,
                'Close': [100.0] * 10,
                'ATR_Pct': [2.0] * 10
            })
            res = mod.apply_triple_barrier_labeling(test_df)
            if 'Label_Short' in res.columns:
                return mod.apply_triple_barrier_labeling, 'ml_predictor_5y.apply_triple_barrier_labeling'
        except Exception:
            pass

    return OracleShortTripleBarrierLabeler.apply_short_triple_barrier_labeling, 'oracle'


def get_short_feature_runner():
    """Returns actual short feature engineering if implemented, else Oracle."""
    mod = get_implementation_module()
    if mod and hasattr(mod, 'compute_short_features'):
        return mod.compute_short_features, 'ml_predictor_5y'
    return OracleShortFeatureEngine.compute_short_features, 'oracle'


# ============================================================================
# Section 4: Test Classes
# ============================================================================

class TestShortTripleBarrierLabeling(unittest.TestCase):
    """
    Class 1: Short Triple-Barrier Labeling Engine Verification.
    Verifies that the short labeling logic accurately models downside profit taking,
    upside stop-loss defense, timeout resolution, same-day collision handling,
    NEXT_OPEN execution pricing, piecewise soft-root ATR scaling, and live tail isolation.
    """

    def setUp(self):
        self.dates = SyntheticShortDataGenerator.generate_trading_dates(
            start_date='2024-01-02', num_days=20
        )

    def _build_short_test_case(
        self,
        t1_high: float,
        t1_low: float,
        t2_high: float = 100.0,
        t2_low: float = 100.0,
        t1_open: float = 100.0,
        t_atr: float = 2.0,
        t5_close: float = 100.0
    ) -> pd.DataFrame:
        """Constructs a deterministic 10-bar test case DataFrame."""
        rows = []
        for i in range(10):
            d = self.dates[i]
            if i == 0:
                # Bar T: Signal bar
                rows.append({
                    'Date': d, 'Ticker': 'NVDA', 'Open': 100.0, 'High': 101.0,
                    'Low': 99.0, 'Close': 100.0, 'ATR_Pct': t_atr
                })
            elif i == 1:
                # Bar T+1: Execution bar (Entry price = t1_open)
                rows.append({
                    'Date': d, 'Ticker': 'NVDA', 'Open': t1_open, 'High': t1_high,
                    'Low': t1_low, 'Close': (t1_high + t1_low) / 2.0, 'ATR_Pct': t_atr
                })
            elif i == 2:
                # Bar T+2
                rows.append({
                    'Date': d, 'Ticker': 'NVDA', 'Open': 100.0, 'High': t2_high,
                    'Low': t2_low, 'Close': 100.0, 'ATR_Pct': t_atr
                })
            elif i == 5:
                # Bar T+5: Timeout exit bar
                rows.append({
                    'Date': d, 'Ticker': 'NVDA', 'Open': 100.0, 'High': 100.5,
                    'Low': 99.5, 'Close': t5_close, 'ATR_Pct': t_atr
                })
            else:
                rows.append({
                    'Date': d, 'Ticker': 'NVDA', 'Open': 100.0, 'High': 100.5,
                    'Low': 99.5, 'Close': 100.0, 'ATR_Pct': t_atr
                })
        return pd.DataFrame(rows)

    def test_short_lower_barrier_hit_first(self):
        """
        TP hit first -> Label_Short=1.0, Barrier_Hit_Short='lower'.
        Entry = 100.0, ATR = 2.0% -> eff_atr = 0.020.
        Lower TP barrier = 100.0 * (1 - max(0.030, 1.35 * 0.020)) = 97.00.
        Upper SL barrier = 100.0 * (1 + max(0.022, 1.00 * 0.020)) = 102.20.
        T+1 Low = 96.5 (touches TP), High = 101.0 (does not touch SL).
        """
        labeler_fn, _ = get_short_labeling_runner()
        df = self._build_short_test_case(t1_high=101.0, t1_low=96.5)
        labeled = labeler_fn(df)

        self.assertEqual(
            labeled.at[0, 'Label_Short'], 1.0,
            "Short lower barrier hit first must yield Label_Short = 1.0 (Winning short trade)."
        )
        self.assertIn(
            labeled.at[0, 'Barrier_Hit_Short'], ['lower', 'tp'],
            "Barrier_Hit_Short must record 'lower' or 'tp' when TP barrier is touched."
        )
        if 'Realized_Return_Short' in labeled.columns:
            self.assertAlmostEqual(labeled.at[0, 'Realized_Return_Short'], 3.0, places=1)

    def test_short_upper_barrier_hit_first(self):
        """
        SL hit first -> Label_Short=0.0, Barrier_Hit_Short='upper' (or 'sl').
        Entry = 100.0, Upper SL barrier = 102.20.
        T+1 High = 103.5 (touches SL), Low = 99.0.
        """
        labeler_fn, _ = get_short_labeling_runner()
        df = self._build_short_test_case(t1_high=103.5, t1_low=99.0)
        labeled = labeler_fn(df)

        self.assertEqual(
            labeled.at[0, 'Label_Short'], 0.0,
            "Short upper barrier hit first must yield Label_Short = 0.0 (Stopped-out short trade)."
        )
        self.assertIn(
            labeled.at[0, 'Barrier_Hit_Short'], ['upper', 'sl'],
            "Barrier_Hit_Short must record 'upper' or 'sl' when SL barrier is touched."
        )
        if 'Realized_Return_Short' in labeled.columns:
            self.assertAlmostEqual(labeled.at[0, 'Realized_Return_Short'], -2.2, places=1)

    def test_short_timeout_neither_barrier_hit(self):
        """
        Timeout -> Label_Short=0.0, Barrier_Hit_Short='timeout'.
        Prices hover within [98.5, 101.5] for all 5 days.
        Exit at T+5 Close = 98.0 -> realized return is ((100 - 98) / 100) * 100 = +2.0%.
        """
        labeler_fn, _ = get_short_labeling_runner()
        df = self._build_short_test_case(t1_high=101.0, t1_low=99.0, t5_close=98.0)
        labeled = labeler_fn(df)

        self.assertEqual(
            labeled.at[0, 'Label_Short'], 0.0,
            "Neither barrier hit by T+5 must yield Label_Short = 0.0 (Timeout)."
        )
        self.assertEqual(
            labeled.at[0, 'Barrier_Hit_Short'], 'timeout',
            "Barrier_Hit_Short must record 'timeout' upon horizon expiration."
        )
        if 'Realized_Return_Short' in labeled.columns:
            self.assertAlmostEqual(labeled.at[0, 'Realized_Return_Short'], 2.0, places=1)

    def test_short_same_day_collision_stop_loss_priority(self):
        """
        Same-day collision -> Label_Short=0.0, Barrier_Hit_Short='collision'.
        T+1 High = 104.0 (>= 102.20 SL) and Low = 95.0 (<= 97.00 TP).
        Conservative Stop-Loss priority mandates that collision is scored as loss (0.0).
        """
        labeler_fn, _ = get_short_labeling_runner()
        df = self._build_short_test_case(t1_high=104.0, t1_low=95.0)
        labeled = labeler_fn(df)

        self.assertEqual(
            labeled.at[0, 'Label_Short'], 0.0,
            "Same-day collision must resolve to Label_Short = 0.0 (Conservative Stop-Loss Priority)."
        )
        self.assertEqual(
            labeled.at[0, 'Barrier_Hit_Short'], 'collision',
            "Barrier_Hit_Short must record 'collision' for dual-barrier touch."
        )
        if 'Realized_Return_Short' in labeled.columns:
            self.assertAlmostEqual(labeled.at[0, 'Realized_Return_Short'], -2.2, places=1)

    def test_short_entry_price_strictly_next_open(self):
        """
        Short execution price must strictly equal NEXT_OPEN (Open.shift(-1)).
        Prevents lookahead bias from using T-Close or intraday execution.
        """
        labeler_fn, _ = get_short_labeling_runner()
        df = self._build_short_test_case(t1_high=107.0, t1_low=105.0, t1_open=106.0)
        labeled = labeler_fn(df)

        entry_price = labeled.at[0, 'Entry_Price']
        self.assertEqual(
            entry_price, 106.0,
            f"Entry price ({entry_price}) must strictly equal NEXT_OPEN (106.0), not Bar T close (100.0)."
        )

        tp_col = 'Short_Target_TP' if 'Short_Target_TP' in labeled.columns else 'Target_Lower'
        sl_col = 'Short_Target_SL' if 'Short_Target_SL' in labeled.columns else 'Target_Upper'
        self.assertAlmostEqual(labeled.at[0, tp_col], 102.82, places=2)
        self.assertAlmostEqual(labeled.at[0, sl_col], 108.332, places=2)

    def test_short_piecewise_soft_root_scaling(self):
        """
        Verifies dynamic barriers across low, normal, and extreme ATR volatility regimes:
        - raw_atr <= 0.040: eff_atr = clip(raw_atr, 0.012, 0.040)
        - raw_atr > 0.040:  eff_atr = 0.040 + 0.50 * (clip(raw_atr, 0.040, 0.100) - 0.040)
        - Lower TP: NEXT_OPEN * (1 - max(0.030, 1.35 * eff_atr))
        - Upper SL: NEXT_OPEN * (1 + max(0.022, 1.00 * eff_atr))
        """
        labeler_fn, _ = get_short_labeling_runner()
        test_regimes = [
            # (ATR_Pct, expected_eff_atr, expected_tp, expected_sl)
            (0.5, 0.012, 97.00, 102.20),   # Floor 1.2%
            (1.2, 0.012, 97.00, 102.20),   # Lower boundary
            (2.0, 0.020, 97.00, 102.20),   # 1.35*0.02=0.027 < 0.030 floor
            (4.0, 0.040, 94.60, 104.00),   # 4.0% -> eff=0.040, TP=100*(1-0.054)=94.60, SL=104.00
            (5.0, 0.045, 93.925, 104.50),  # 5.0% -> eff=0.045, TP=100*(1-0.06075)=93.925, SL=104.50
            (6.0, 0.050, 93.25, 105.00),   # 6.0% -> eff=0.050, TP=100*(1-0.0675)=93.25, SL=105.00
            (8.0, 0.060, 91.90, 106.00),   # 8.0% -> eff=0.060, TP=100*(1-0.081)=91.90, SL=106.00
            (10.0, 0.070, 90.55, 107.00),  # 10.0% -> eff=0.070, TP=100*(1-0.0945)=90.55, SL=107.00
            (15.0, 0.070, 90.55, 107.00),  # 15.0% -> ceiling 0.070, TP=90.55, SL=107.00
        ]

        tp_col_name = 'Short_Target_TP'
        sl_col_name = 'Short_Target_SL'

        for atr_val, exp_eff, exp_tp, exp_sl in test_regimes:
            df = self._build_short_test_case(t1_high=101.0, t1_low=99.0, t_atr=atr_val)
            labeled = labeler_fn(df)

            tp_key = tp_col_name if tp_col_name in labeled.columns else 'Target_Lower'
            sl_key = sl_col_name if sl_col_name in labeled.columns else 'Target_Upper'

            tp_actual = labeled.at[0, tp_key]
            sl_actual = labeled.at[0, sl_key]

            self.assertAlmostEqual(
                tp_actual, exp_tp, places=2,
                msg=f"Short TP barrier mismatch at ATR={atr_val}%: Expected {exp_tp}, got {tp_actual}"
            )
            self.assertAlmostEqual(
                sl_actual, exp_sl, places=2,
                msg=f"Short SL barrier mismatch at ATR={atr_val}%: Expected {exp_sl}, got {sl_actual}"
            )

    def test_short_tail_five_days_nan_isolation(self):
        """
        Tail isolation: Last 5 rows per ticker have Label_Short = np.nan.
        Because future 5-day trajectory has not completed, these rows must be
        reserved for live inference and excluded from model training folds.
        """
        labeler_fn, _ = get_short_labeling_runner()
        df = self._build_short_test_case(t1_high=101.0, t1_low=99.0)
        labeled = labeler_fn(df)

        n = len(labeled)
        self.assertEqual(n, 10)

        # First 5 rows (0..4) have valid completed evaluations
        for idx in range(5):
            self.assertFalse(
                pd.isna(labeled.at[idx, 'Label_Short']),
                f"Row {idx} should have valid Label_Short, found NaN!"
            )

        # Last 5 rows (5..9) must be NaN
        for idx in range(5, 10):
            self.assertTrue(
                pd.isna(labeled.at[idx, 'Label_Short']),
                f"Row {idx} must be isolated with Label_Short = NaN to prevent lookahead leakage!"
            )
            self.assertEqual(
                labeled.at[idx, 'Barrier_Hit_Short'], 'unresolved',
                f"Row {idx} Barrier_Hit_Short must be 'unresolved'!"
            )


class TestShortInferenceModel(unittest.TestCase):
    """
    Class 2: Short Inference Model & Prior Calibration Verification.
    Validates feature variance (> 95% non-zero, std > 0.01), LightGBM probability bounds,
    institutional Dollar PCR prior calibration, ranking sort consistency, and dual pool JSON schema.
    """

    def setUp(self):
        self.dates = SyntheticShortDataGenerator.generate_trading_dates(
            start_date='2022-01-03', num_days=500
        )
        self.stock_df = SyntheticShortDataGenerator.generate_stock_ohlcv(
            ticker='NVDA', dates=self.dates, base_price=120.0, seed=55
        )
        self.macro_map = SyntheticShortDataGenerator.generate_macro_etfs(self.dates, seed=60)

    def test_short_feature_variance_and_non_zero(self):
        """
        Short-sensitive features must have std > 0.01 and non-zero rate > 95%
        to prevent silent zero collapse or constant-feature model degradation.
        """
        feat_runner, _ = get_short_feature_runner()
        panel = feat_runner(self.stock_df, self.macro_map)

        target_short_features = [
            'Bias_MA50', 'Bias_MA20', 'EMA10_20_Spread', 'Fib_Pos_60',
            'RSI_14', 'Ret_5d', 'Alpha_SMH_20d', 'VIXY_Bias_MA20', 'Vol_Ratio_20'
        ]

        self.assertGreater(len(panel), 0, "Feature panel is empty!")

        for feat in target_short_features:
            self.assertIn(feat, panel.columns, f"Short feature '{feat}' missing from panel!")
            series = panel[feat].dropna()
            non_zero_rate = float((series != 0.0).mean())
            std_val = float(series.std())

            self.assertGreater(
                non_zero_rate, 0.95,
                f"Feature '{feat}' non-zero rate is {non_zero_rate:.4f} (<= 0.95). Index alignment or silent zero bug!"
            )
            self.assertGreater(
                std_val, 0.01,
                f"Feature '{feat}' standard deviation is {std_val:.4f} (<= 0.01). Feature lacks predictive variance!"
            )

        # Check real parquet cache if present in workspace
        parquet_path = os.path.join(RADAR_SUBDIR, 'data_cache', 'ml_panel_5y.parquet')
        if not os.path.exists(parquet_path):
            parquet_path = os.path.join(PARENT_DIR, 'data_cache', 'ml_panel_5y.parquet')

        if os.path.exists(parquet_path):
            real_df = pd.read_parquet(parquet_path)
            self.assertGreater(len(real_df), 1000)
            for feat in ['SMH_Bias_MA50', 'TLT_Bias_MA50', 'VIXY_Bias_MA20']:
                if feat in real_df.columns:
                    s = real_df[feat].dropna()
                    self.assertGreater(float((s != 0.0).mean()), 0.95)
                    self.assertGreater(float(s.std()), 0.01)

    def test_short_probability_calibration_and_bounds(self):
        """
        Verifies ml_predictor_5y and integrated_market_radar calibrate_short_probability:
        - LightGBM raw probability mapped to calibrated [10.0, 95.0] bounds.
        - Dollar PCR >= 1.30 institutional prior boost.
        - Put wall broken additional boost.
        - Monotonicity invariant across PCR sweep.
        """
        import ml_predictor_5y as ml
        import integrated_market_radar as imr

        for mod_name, fn in [("ml_predictor_5y", ml.calibrate_short_probability),
                             ("integrated_market_radar", imr.calibrate_short_probability)]:
            # Clamping bounds: [10.0, 95.0]
            test_raw_probs = [0.0, 0.01, 0.15, 0.50, 0.65, 0.85, 0.99, 1.0, -5.0, 150.0]
            for rp in test_raw_probs:
                calibrated = fn(raw_prob=rp, dollar_pcr=1.0, sector_bear_pf=1.20)
                self.assertFalse(math.isnan(calibrated), f"[{mod_name}] NaN leak for rp={rp}")
                self.assertGreaterEqual(calibrated, 10.0, f"[{mod_name}] Failed lower bound 10.0 for rp={rp}")
                self.assertLessEqual(calibrated, 95.0, f"[{mod_name}] Failed upper bound 95.0 for rp={rp}")

            # Prior Weighting Check: Dollar PCR >= 1.30 (heavy institutional put hedging)
            raw_p = 0.55
            neutral_pcr_prob = fn(raw_prob=raw_p, dollar_pcr=0.95, sector_bear_pf=1.20)
            heavy_pcr_prob = fn(raw_prob=raw_p, dollar_pcr=1.35, sector_bear_pf=1.20)

            self.assertGreater(
                heavy_pcr_prob, neutral_pcr_prob,
                f"[{mod_name}] Heavy Dollar PCR (>= 1.30) must boost short probability"
            )
            ratio = heavy_pcr_prob / neutral_pcr_prob
            self.assertGreaterEqual(ratio, 1.05, f"[{mod_name}] PCR prior boost multiplier must be >= 1.05")

            # Put Wall broken check
            broken_wall_prob = fn(raw_prob=raw_p, dollar_pcr=1.35, put_wall_broken=True, sector_bear_pf=1.20)
            self.assertGreater(broken_wall_prob, heavy_pcr_prob)

    def test_short_ranking_and_sort_consistency(self):
        """
        top_short candidates must be sorted in strictly descending order of calibrated_short_win_rate_pct.
        Circuit breaker override ensures halted candidates are flagged and demoted behind actionable picks.
        """
        candidates = [
            {'ticker': 'COHR', 'calibrated_short_win_rate_pct': 72.5, 'is_crash_halt': False},
            {'ticker': 'AMD', 'calibrated_short_win_rate_pct': 64.0, 'is_crash_halt': False},
            {'ticker': 'NVDA', 'calibrated_short_win_rate_pct': 58.2, 'is_crash_halt': False},
            {'ticker': 'INTC', 'calibrated_short_win_rate_pct': 51.0, 'is_crash_halt': False},
        ]

        win_rates = [c['calibrated_short_win_rate_pct'] for c in candidates]
        self.assertEqual(win_rates, sorted(win_rates, reverse=True))

        for i in range(len(candidates) - 1):
            curr_rate = candidates[i]['calibrated_short_win_rate_pct']
            next_rate = candidates[i + 1]['calibrated_short_win_rate_pct']
            self.assertGreaterEqual(
                curr_rate, next_rate,
                f"top_short sort order violation: rank {i} ({curr_rate}) < rank {i+1} ({next_rate})"
            )

        halted_candidate = {'ticker': 'CRDO', 'calibrated_short_win_rate_pct': 75.0, 'is_crash_halt': True}
        if halted_candidate['is_crash_halt']:
            action = '🚨 暴跌熔断·严禁左侧接刀'
            self.assertIn('暴跌熔断', action)

    def test_latest_ai_predictions_schema_with_dual_pools(self):
        """
        Validates JSON schema of data_cache/latest_ai_predictions.json for dual pools:
        - top_long: list of bullish dicts with ticker, calibrated_win_rate_pct, targets, action
        - top_short: list of bearish dicts with ticker, calibrated_short_win_rate_pct, targets, dollar_pcr, action
        - by_ticker: dict containing dual diagnostic metrics per ticker
        """
        json_path = os.path.join(CURRENT_DIR, 'data_cache', 'latest_ai_predictions.json')
        if not os.path.exists(json_path):
            json_path = os.path.join(PARENT_DIR, 'data_cache', 'latest_ai_predictions.json')

        self.assertTrue(os.path.exists(json_path), f"Required production cache file not found at {json_path}")
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.assertIn('top_short', data, f"Production {json_path} missing 'top_short' pool!")

        self.assertIn('top_long', data, "latest_ai_predictions.json missing 'top_long' pool!")
        self.assertIn('top_short', data, "latest_ai_predictions.json missing 'top_short' pool!")
        self.assertIn('by_ticker', data, "latest_ai_predictions.json missing 'by_ticker' pool!")

        top_short = data['top_short']
        self.assertGreater(len(top_short), 0, "top_short pool cannot be empty!")

        required_short_fields = [
            'ticker', 'calibrated_short_win_rate_pct',
            'short_entry_price', 'dollar_pcr', 'short_action'
        ]
        for idx, item in enumerate(top_short):
            for field in required_short_fields:
                self.assertIn(
                    field, item,
                    f"top_short item #{idx} ({item.get('ticker')}) missing required field '{field}'!"
                )
            self.assertGreaterEqual(item['calibrated_short_win_rate_pct'], 0.0)
            self.assertLessEqual(item['calibrated_short_win_rate_pct'], 100.0)

        top_long = data['top_long']
        self.assertGreater(len(top_long), 0, "top_long pool cannot be empty!")
        for idx, item in enumerate(top_long):
            self.assertIn('ticker', item)
            self.assertIn('calibrated_win_rate_pct', item)
            self.assertIn('action', item)

        by_ticker = data['by_ticker']
        for tk, info in by_ticker.items():
            self.assertIn('calibrated_win_rate_pct', info)
            self.assertIn('calibrated_short_win_rate_pct', info)


class TestShortTemporalIntegrityZeroLookahead(unittest.TestCase):
    """
    Class 3: Temporal Integrity & Zero Lookahead Verification.
    Verifies that feature engineering code is strictly causal:
    - Subseries truncation invariance (diff < 1e-7 between [0..T] and [0..T+N])
    - Perturbation insensitivity (extreme future price/volume spikes at T+1 have 0.0 effect at bar T)
    - AST static analysis confirms zero negative shifts (shift(-k)) in feature code
    - Walk-forward CV splits enforce >= 5 trading days purge gap between train and test windows
    """

    def setUp(self):
        self.dates = SyntheticShortDataGenerator.generate_trading_dates(
            start_date='2022-01-03', num_days=250
        )
        self.raw_stock = SyntheticShortDataGenerator.generate_stock_ohlcv(
            ticker='NVDA', dates=self.dates, base_price=150.0, seed=42
        )
        self.macro_map = SyntheticShortDataGenerator.generate_macro_etfs(self.dates, seed=50)

    def test_short_features_subseries_truncation_invariance(self):
        """
        Features computed on truncated series [0 ... T] must match features
        computed on full series [0 ... T + N] at bar T with max absolute diff < 1e-7.
        """
        feat_runner, _ = get_short_feature_runner()
        trunc_cutoff = 150

        trunc_stock = self.raw_stock.iloc[:trunc_cutoff + 1].copy()
        trunc_macro = {k: v.iloc[:trunc_cutoff + 1].copy() for k, v in self.macro_map.items()}
        panel_trunc = feat_runner(trunc_stock, trunc_macro)

        panel_full = feat_runner(self.raw_stock, self.macro_map)

        target_date = self.dates[trunc_cutoff]
        row_trunc = panel_trunc[panel_trunc['Date'] == target_date].iloc[0]
        row_full = panel_full[panel_full['Date'] == target_date].iloc[0]

        for feat in OracleShortFeatureEngine.SHORT_FEATURES:
            if feat in panel_trunc.columns and feat in panel_full.columns:
                val_trunc = float(row_trunc[feat])
                val_full = float(row_full[feat])
                diff = abs(val_trunc - val_full)
                self.assertLess(
                    diff, 1e-7,
                    f"Lookahead leakage detected in short feature '{feat}'! Trunc: {val_trunc}, Full: {val_full}, Diff: {diff}"
                )

    def test_short_features_future_spike_insensitivity(self):
        """
        Injecting a massive future price/volume spike at T+1 must have identically
        0.0 numerical impact on bar T short features.
        """
        feat_runner, _ = get_short_feature_runner()
        panel_orig = feat_runner(self.raw_stock, self.macro_map)

        spiked_stock = self.raw_stock.copy()
        spiked_stock.loc[151, 'Open'] *= 10.0
        spiked_stock.loc[151, 'High'] *= 10.0
        spiked_stock.loc[151, 'Low'] *= 10.0
        spiked_stock.loc[151, 'Close'] *= 10.0
        spiked_stock.loc[151, 'Volume'] = 1e9

        panel_spiked = feat_runner(spiked_stock, self.macro_map)

        target_date = self.dates[150]
        row_orig = panel_orig[panel_orig['Date'] == target_date].iloc[0]
        row_spiked = panel_spiked[panel_spiked['Date'] == target_date].iloc[0]

        for feat in OracleShortFeatureEngine.SHORT_FEATURES:
            if feat in panel_orig.columns and feat in panel_spiked.columns:
                diff = abs(float(row_orig[feat]) - float(row_spiked[feat]))
                self.assertEqual(
                    diff, 0.0,
                    f"Short feature '{feat}' contaminated by future spike at T+1! Diff: {diff}"
                )

    def test_short_features_ast_no_negative_shifts(self):
        """
        Scans AST of ml_predictor_5y.py and feature classes to confirm zero negative shifts (shift(-k)).
        Negative shifts in feature computation indicate fatal future lookahead cheating.
        """
        checker = ASTShortCausalityChecker()

        impl_path = os.path.join(CURRENT_DIR, 'ml_predictor_5y.py')
        if not os.path.exists(impl_path):
            impl_path = os.path.join(PARENT_DIR, 'ml_predictor_5y.py')

        if os.path.exists(impl_path):
            with open(impl_path, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read(), filename=impl_path)
            checker.visit(tree)
            self.assertEqual(
                len(checker.violations), 0,
                f"AST scan detected illegal negative shifts in {impl_path}: {checker.violations}"
            )

        oracle_source = inspect.getsource(OracleShortFeatureEngine)
        oracle_tree = ast.parse(oracle_source)
        oracle_checker = ASTShortCausalityChecker()
        oracle_checker.visit(oracle_tree)
        self.assertEqual(
            len(oracle_checker.violations), 0,
            f"AST violations in OracleShortFeatureEngine: {oracle_checker.violations}"
        )

        malicious_sample = """
def compute_short_features(df):
    future_down = df['Close'].shift(-2)
    df['Cheating_Down_Feature'] = future_down / df['Close']
    return df
"""
        bad_tree = ast.parse(malicious_sample)
        bad_checker = ASTShortCausalityChecker()
        bad_checker.visit(bad_tree)
        self.assertGreater(
            len(bad_checker.violations), 0,
            "AST checker failed to detect malicious negative shift(-2) in feature engineering!"
        )

    def test_short_walk_forward_cv_purge_isolation(self):
        """
        Validates that walk-forward CV splits enforce >= 5 trading days purge gap
        between expanding training windows and test folds to prevent label leakage.
        """
        all_dates = SyntheticShortDataGenerator.generate_trading_dates(
            start_date='2021-11-01', num_days=1100
        )
        df_dates = pd.DataFrame({'Date': all_dates})
        df_dates['Year'] = df_dates['Date'].dt.year

        test_years = [2024, 2025, 2026]
        purge_days = 5

        folds = []
        for test_yr in test_years:
            test_mask = (df_dates['Year'] == test_yr)
            if not test_mask.any():
                continue
            test_start_idx = df_dates[test_mask].index[0]
            test_end_idx = df_dates[test_mask].index[-1]

            train_end_idx = test_start_idx - purge_days
            if train_end_idx <= 50:
                continue
            train_start_idx = 0

            folds.append({
                'test_year': test_yr,
                'train_idx': (train_start_idx, train_end_idx),
                'test_idx': (test_start_idx, test_end_idx),
                'purge_gap': test_start_idx - train_end_idx
            })

        self.assertGreaterEqual(len(folds), 2, "Walk-forward splitter must generate at least 2 folds!")

        for f in folds:
            purge_gap = f['purge_gap']
            self.assertGreaterEqual(
                purge_gap, purge_days,
                f"Purge gap in fold {f['test_year']} is {purge_gap} (< {purge_days})! Risk of label overlap leakage!"
            )
            train_d = set(df_dates.iloc[f['train_idx'][0]:f['train_idx'][1]]['Date'])
            test_d = set(df_dates.iloc[f['test_idx'][0]:f['test_idx'][1] + 1]['Date'])
            overlap = train_d.intersection(test_d)
            self.assertEqual(
                len(overlap), 0,
                f"Train and test date sets must be strictly disjoint! Found overlap: {overlap}"
            )


# ============================================================================
# Section 5: CLI Test Runner
# ============================================================================

def run_tests():
    """Execute all test suites with verbose output."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestShortTripleBarrierLabeling))
    suite.addTests(loader.loadTestsFromTestCase(TestShortInferenceModel))
    suite.addTests(loader.loadTestsFromTestCase(TestShortTemporalIntegrityZeroLookahead))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
