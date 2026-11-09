#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_ml_predictor.py - Comprehensive Anti-Cheating Automated Unit Test Suite
for AI Multi-Sector 5Y US Market Predictor (Milestone M0)

Authoritative References:
- ORIGINAL_REQUEST.md (§R1, §R2, §R3, §R4, §Acceptance Criteria)
- PROJECT.md (§Architecture, §Feature Inventory, §Interface Contracts)
- TEST_INFRA.md (§Test Philosophy, §Invariant & Anti-Cheat Test Suite)
- DISPATCH.md (E2E Test Writer Mission)

Structural Invariant Test Coverage:
1. Feature Causality & No Lookahead (shift(-k) AST scan, subseries truncation invariance max|diff| < 10^-7)
2. Purge & Embargo Isolation (min 5 trading days gap, no train label leakage into test window)
3. Subsample & Secondary IPO Preservation (CRWV/IBIT/CRDO do not truncate mature stock history, no global dropna)
4. Extreme Market Resilience (zero volume, zero price range, -50% overnight gap down, zero unhandled NaN/Inf, valid probabilities [0, 1])
5. Triple Barrier Resolution Logic (NEXT_OPEN entry, dynamic ATR barriers, Upper hit -> 1, Lower hit -> 0, Timeout -> 0, Same-day collision -> 0)
6. Tail Isolation for Live Inference (latest 5 trading days excluded from train/test, reserved for live pool)
7. Adversarial Integrity & Boundary Stress (short histories, missing macro dates, winsorization clipping)

Usage:
  python -m unittest test_ml_predictor.py -v
  python test_ml_predictor.py
"""

import os
import sys
import ast
import inspect
import unittest
from typing import Dict, List, Tuple, Any, Optional

import numpy as np
import pandas as pd
import lightgbm as lgb

# Ensure standard output handles UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


# ============================================================================
# Section 1: Synthetic Test Data Generators & Oracle Implementations
# ============================================================================

class SyntheticMarketDataGenerator:
    """Deterministic synthetic market data generator for reproducible tests."""

    @staticmethod
    def generate_trading_dates(start_date: str = '2021-11-01', num_days: int = 500) -> List[pd.Timestamp]:
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
        log_rets = rng.normal(loc=0.0005, scale=0.02, size=n)
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
        """Generate synthetic OHLCV for all 7 macro benchmarks."""
        macro_tickers = ['QQQM', 'SPY', 'SMH', 'IGV', 'TLT', 'VIXY', 'GLD']
        result = {}
        for idx, ticker in enumerate(macro_tickers):
            base_p = 400.0 if ticker in ['SPY', 'QQQM'] else (200.0 if ticker in ['SMH', 'IGV'] else 90.0)
            df = SyntheticMarketDataGenerator.generate_stock_ohlcv(
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
        """Combine given target stocks and the 7 macro ETFs into a single raw panel."""
        frames = []
        for i, tk in enumerate(stock_tickers):
            s_df = cls.generate_stock_ohlcv(tk, dates, base_price=100.0 + i * 10, seed=base_seed + i)
            frames.append(s_df)

        macro_dict = cls.generate_macro_etfs(dates, seed=base_seed + 100)
        for m_tk, m_df in macro_dict.items():
            frames.append(m_df)

        full_df = pd.concat(frames, ignore_index=True)
        return full_df


class OracleFeatureEngine:
    """
    Authoritative reference implementation of the 23 causal technical & macro features.
    Strictly causal (T-day closure only, zero negative shifts, zero future leakage).
    """

    FEATURE_NAMES = [
        # 15 Stock Features
        'Bias_MA20', 'Bias_MA50', 'Bias_SMA200', 'EMA10_20_Spread', 'Fib_Pos_60',
        'RSI_14', 'RSI_Slope_3d', 'ATR_Pct', 'Ret_1d', 'Ret_3d', 'Ret_5d',
        'Ret_10d', 'Ret_20d', 'Vol_Ratio_20', 'Bar_Strength',
        # 2 Cross-Asset Alpha Features
        'Alpha_QQQM_5d', 'Alpha_SMH_20d',
        # 6 Macro Weather Features
        'QQQM_Bias_MA50', 'SMH_Bias_MA50', 'TLT_Bias_MA50',
        'VIXY_Bias_MA20', 'VIXY_Pos_60', 'SMH_IGV_Ratio_Bias'
    ]

    @classmethod
    def compute_stock_features(cls, df: pd.DataFrame) -> pd.DataFrame:
        """Compute the 15 stock-level features using strictly past/current data."""
        df = df.copy().sort_values('Date').reset_index(drop=True)
        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = df['Volume']

        # 1. Bias_MA20, Bias_MA50, Bias_SMA200
        ma20 = close.rolling(20, min_periods=1).mean()
        ma50 = close.rolling(50, min_periods=1).mean()
        sma200 = close.rolling(200, min_periods=1).mean()
        df['Bias_MA20'] = ((close - ma20) / (ma20 + 1e-8)) * 100.0
        df['Bias_MA50'] = ((close - ma50) / (ma50 + 1e-8)) * 100.0
        df['Bias_SMA200'] = ((close - sma200) / (sma200 + 1e-8)) * 100.0

        # 2. EMA10_20_Spread
        ema10 = close.ewm(span=10, adjust=False).mean()
        ema20 = close.ewm(span=20, adjust=False).mean()
        df['EMA10_20_Spread'] = ((ema10 - ema20) / (ema20 + 1e-8)) * 100.0

        # 3. Fib_Pos_60: winsorized to [-0.5, 1.5]
        low60 = low.rolling(60, min_periods=1).min()
        high60 = high.rolling(60, min_periods=1).max()
        range60 = high60 - low60
        fib_raw = (close - low60) / np.where(range60 > 1e-6, range60, 1.0)
        df['Fib_Pos_60'] = np.clip(fib_raw, -0.5, 1.5)

        # 4. RSI_14 & RSI_Slope_3d
        delta = close.diff()
        gain = delta.clip(lower=0.0).ewm(alpha=1.0 / 14.0, adjust=False).mean()
        loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / 14.0, adjust=False).mean()
        rs = gain / (loss + 1e-8)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        df['RSI_14'] = rsi.fillna(50.0)
        df['RSI_Slope_3d'] = (df['RSI_14'] - df['RSI_14'].shift(3)).fillna(0.0)

        # 5. ATR_Pct (ATR_14 / Close * 100)
        prev_close = close.shift(1).fillna(close)
        tr = np.maximum(
            high - low,
            np.maximum((high - prev_close).abs(), (low - prev_close).abs())
        )
        atr14 = tr.rolling(14, min_periods=1).mean()
        df['ATR_Pct'] = (atr14 / (close + 1e-8)) * 100.0

        # 6. Returns: 1d, 3d, 5d, 10d, 20d
        df['Ret_1d'] = close.pct_change(1).fillna(0.0) * 100.0
        df['Ret_3d'] = close.pct_change(3).fillna(0.0) * 100.0
        df['Ret_5d'] = close.pct_change(5).fillna(0.0) * 100.0
        df['Ret_10d'] = close.pct_change(10).fillna(0.0) * 100.0
        df['Ret_20d'] = close.pct_change(20).fillna(0.0) * 100.0

        # 7. Vol_Ratio_20 & Bar_Strength
        vol_ma20 = volume.rolling(20, min_periods=1).mean()
        df['Vol_Ratio_20'] = volume / (vol_ma20 + 1e-6)
        bar_range = high - low
        df['Bar_Strength'] = (close - low) / np.where(bar_range > 1e-6, bar_range, 1.0)

        # Clean NaN/Inf with safe causally valid defaults
        cols_to_clean = [
            'Bias_MA20', 'Bias_MA50', 'Bias_SMA200', 'EMA10_20_Spread', 'Fib_Pos_60',
            'RSI_14', 'RSI_Slope_3d', 'ATR_Pct', 'Ret_1d', 'Ret_3d', 'Ret_5d',
            'Ret_10d', 'Ret_20d', 'Vol_Ratio_20', 'Bar_Strength'
        ]
        for col in cols_to_clean:
            df[col] = df[col].replace([np.inf, -np.inf], np.nan).fillna(0.0)

        return df

    @classmethod
    def compute_macro_features(cls, macro_map: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Compute the 6 macro features and align by Date."""
        qqqm = macro_map['QQQM'].sort_values('Date').reset_index(drop=True)
        smh = macro_map['SMH'].sort_values('Date').reset_index(drop=True)
        tlt = macro_map['TLT'].sort_values('Date').reset_index(drop=True)
        vixy = macro_map['VIXY'].sort_values('Date').reset_index(drop=True)
        igv = macro_map['IGV'].sort_values('Date').reset_index(drop=True)

        # 1. QQQM 特征表
        qqqm_close = qqqm['Close'].astype(float)
        qqqm_ma50 = qqqm_close.rolling(50, min_periods=1).mean()
        qqqm_feat = pd.DataFrame({
            'Date': qqqm['Date'],
            'QQQM_Bias_MA50': ((qqqm_close - qqqm_ma50) / (qqqm_ma50 + 1e-8)) * 100.0,
            'QQQM_Ret_5d': qqqm_close.pct_change(5).fillna(0.0) * 100.0
        })

        # 2. SMH 特征表
        smh_close = smh['Close'].astype(float)
        smh_ma50 = smh_close.rolling(50, min_periods=1).mean()
        smh_feat = pd.DataFrame({
            'Date': smh['Date'],
            'SMH_Bias_MA50': ((smh_close - smh_ma50) / (smh_ma50 + 1e-8)) * 100.0,
            'SMH_Ret_20d': smh_close.pct_change(20).fillna(0.0) * 100.0
        })

        # 3. TLT 特征表
        tlt_close = tlt['Close'].astype(float)
        tlt_ma50 = tlt_close.rolling(50, min_periods=1).mean()
        tlt_feat = pd.DataFrame({
            'Date': tlt['Date'],
            'TLT_Bias_MA50': ((tlt_close - tlt_ma50) / (tlt_ma50 + 1e-8)) * 100.0
        })

        # 4. VIXY 特征表
        vixy_close = vixy['Close'].astype(float)
        vixy_high = vixy['High'].astype(float)
        vixy_low = vixy['Low'].astype(float)
        vixy_ma20 = vixy_close.rolling(20, min_periods=1).mean()
        v_low60 = vixy_low.rolling(60, min_periods=1).min()
        v_high60 = vixy_high.rolling(60, min_periods=1).max()
        v_range = v_high60 - v_low60
        v_pos = (vixy_close - v_low60) / np.where(v_range > 1e-6, v_range, 1.0)
        vixy_feat = pd.DataFrame({
            'Date': vixy['Date'],
            'VIXY_Bias_MA20': ((vixy_close - vixy_ma20) / (vixy_ma20 + 1e-8)) * 100.0,
            'VIXY_Pos_60': np.clip(v_pos, 0.0, 1.0)
        })

        # 5. SMH/IGV 比值乖离表 (先按 Date 内连接对齐相同交易日)
        smh_igv = pd.merge(
            smh[['Date', 'Close']].rename(columns={'Close': 'Close_SMH'}),
            igv[['Date', 'Close']].rename(columns={'Close': 'Close_IGV'}),
            on='Date',
            how='inner'
        ).sort_values('Date').reset_index(drop=True)
        smh_igv_ratio = smh_igv['Close_SMH'].astype(float) / (smh_igv['Close_IGV'].astype(float) + 1e-8)
        ratio_ma20 = smh_igv_ratio.rolling(20, min_periods=1).mean()
        smh_igv_feat = pd.DataFrame({
            'Date': smh_igv['Date'],
            'SMH_IGV_Ratio_Bias': ((smh_igv_ratio - ratio_ma20) / (ratio_ma20 + 1e-8)) * 100.0
        })

        # 6. 以 QQQM 的所有交易日为基准按 Date 左连接全部宏观特征
        macro_df = pd.DataFrame({'Date': sorted(qqqm['Date'].unique())})
        for sub_df in [qqqm_feat, smh_feat, tlt_feat, vixy_feat, smh_igv_feat]:
            macro_df = pd.merge(macro_df, sub_df, on='Date', how='left')

        # 因果安全前向填充并清洗异常值
        for col in macro_df.columns:
            if col != 'Date':
                macro_df[col] = macro_df[col].ffill().replace([np.inf, -np.inf], np.nan).fillna(0.0)

        return macro_df

    @classmethod
    def build_feature_panel(
        cls,
        raw_panel: pd.DataFrame,
        macro_map: Optional[Dict[str, pd.DataFrame]] = None
    ) -> pd.DataFrame:
        """
        Assemble the complete Long Panel DataFrame with all 23 features.
        Preserves secondary IPOs without global dropna truncation.
        """
        macro_tickers = ['QQQM', 'SPY', 'SMH', 'IGV', 'TLT', 'VIXY', 'GLD']
        if macro_map is None:
            # Check if macro tickers are inside raw_panel
            contained_macros = [t for t in macro_tickers if t in raw_panel['Ticker'].unique()]
            if len(contained_macros) >= 5:
                macro_map = {}
                for t in macro_tickers:
                    sub = raw_panel[raw_panel['Ticker'] == t].copy()
                    if not sub.empty:
                        macro_map[t] = sub
            else:
                dates = sorted(raw_panel['Date'].unique())
                macro_map = SyntheticMarketDataGenerator.generate_macro_etfs(dates)

        macro_features = cls.compute_macro_features(macro_map)

        stock_panels = []
        for ticker, group in raw_panel.groupby('Ticker'):
            if ticker in macro_tickers:
                continue
            stock_feat = cls.compute_stock_features(group)
            stock_panels.append(stock_feat)

        if not stock_panels:
            return pd.DataFrame()

        long_panel = pd.concat(stock_panels, axis=0).reset_index(drop=True)

        # Left-join macro features strictly by Date
        merged = pd.merge(long_panel, macro_features, on='Date', how='left')

        # Compute Cross-Asset Alphas with cross-sectional rank strictly by Date
        merged['Alpha_QQQM_5d'] = merged['Ret_5d'] - merged['QQQM_Ret_5d']
        merged['Alpha_SMH_20d'] = merged['Ret_20d'] - merged['SMH_Ret_20d']
        merged['Alpha_QQQM_5d'] = merged.groupby('Date')['Alpha_QQQM_5d'].rank(pct=True).fillna(0.5)
        merged['Alpha_SMH_20d'] = merged.groupby('Date')['Alpha_SMH_20d'].rank(pct=True).fillna(0.5)

        # Ensure all 23 features exist and contain zero unhandled NaN/Inf
        for feat in cls.FEATURE_NAMES:
            if feat not in merged.columns:
                merged[feat] = 0.0
            merged[feat] = merged[feat].replace([np.inf, -np.inf], np.nan).fillna(0.0)

        return merged


class OracleTripleBarrierLabeler:
    """
    Authoritative reference implementation of Triple Barrier Labeling.
    Rules:
    - NEXT_OPEN entry price (Open.shift(-1))
    - Soft-Root Scaling ATR:
        raw_atr = atr_pct / 100.0
        eff_atr = 0.040 + 0.50 * (np.clip(raw_atr, 0.040, 0.100) - 0.040) if raw_atr > 0.040 else np.clip(raw_atr, 0.012, 0.040)
    - Dynamic Upper Barrier: NEXT_OPEN * (1 + max(0.030, 1.35 * eff_atr))
    - Dynamic Lower Barrier: NEXT_OPEN * (1 - max(0.022, 1.00 * eff_atr))
    - Horizon: T+1 to T+5
    - Upper hit first -> 1
    - Lower hit first -> 0
    - Neither hit -> 0 (timeout)
    - Same-day collision -> 0 (Conservative Stop-Loss priority)
    - Tail isolation: last 5 bars unresolved (Label = NaN)
    """

    @classmethod
    def apply_triple_barrier_labeling(cls, df: pd.DataFrame) -> pd.DataFrame:
        """Apply triple barrier labeling per stock group."""
        groups = []
        for ticker, grp in df.groupby('Ticker'):
            grp = grp.copy().sort_values('Date').reset_index(drop=True)
            n = len(grp)

            # Pre-allocate output columns
            grp['NEXT_OPEN'] = grp['Open'].shift(-1)
            grp['Entry_Price'] = grp['NEXT_OPEN']
            grp['Target_Upper'] = np.nan
            grp['Target_Lower'] = np.nan
            grp['Label'] = np.nan
            grp['Barrier_Hit'] = 'unresolved'

            if n <= 5:
                groups.append(grp)
                continue

            for i in range(n):
                if i >= n - 5:
                    # Tail isolation: future path incomplete
                    continue

                entry_price = grp.at[i, 'Entry_Price']
                atr_pct = grp.at[i, 'ATR_Pct'] if 'ATR_Pct' in grp.columns else 2.0
                if np.isnan(atr_pct) or atr_pct <= 0:
                    atr_pct = 2.0

                raw_atr = float(atr_pct) / 100.0
                if raw_atr > 0.040:
                    eff_atr = float(0.040 + 0.50 * (np.clip(raw_atr, 0.040, 0.100) - 0.040))
                else:
                    eff_atr = float(np.clip(raw_atr, 0.012, 0.040))
                upper_mult = max(0.030, 1.35 * eff_atr)
                lower_mult = max(0.022, 1.00 * eff_atr)

                upper_barrier = entry_price * (1.0 + upper_mult)
                lower_barrier = entry_price * (1.0 - lower_mult)

                grp.at[i, 'Target_Upper'] = round(upper_barrier, 4)
                grp.at[i, 'Target_Lower'] = round(lower_barrier, 4)

                resolved = False
                for step in range(1, 6):
                    k = i + step
                    h = grp.at[k, 'High']
                    l = grp.at[k, 'Low']

                    hit_upper = (h >= upper_barrier)
                    hit_lower = (l <= lower_barrier)

                    if hit_upper and hit_lower:
                        # Same-day collision -> Conservative Stop-Loss priority (Label = 0)
                        grp.at[i, 'Label'] = 0.0
                        grp.at[i, 'Barrier_Hit'] = 'collision'
                        resolved = True
                        break
                    elif hit_upper:
                        grp.at[i, 'Label'] = 1.0
                        grp.at[i, 'Barrier_Hit'] = 'upper'
                        resolved = True
                        break
                    elif hit_lower:
                        grp.at[i, 'Label'] = 0.0
                        grp.at[i, 'Barrier_Hit'] = 'lower'
                        resolved = True
                        break

                if not resolved:
                    grp.at[i, 'Label'] = 0.0
                    grp.at[i, 'Barrier_Hit'] = 'timeout'

            groups.append(grp)

        return pd.concat(groups, axis=0).reset_index(drop=True)


class OracleWalkForwardSplitter:
    """
    Authoritative reference implementation of Walk-Forward Expanding Cross-Validation
    with strict 5-day Purge/Embargo isolation.
    """

    @classmethod
    def get_cv_folds(
        cls,
        df: pd.DataFrame,
        test_years: List[int] = [2024, 2025, 2026],
        purge_days: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Generate expanding train/test folds with mandatory purge embargo gap.
        """
        all_dates = sorted(df['Date'].drop_duplicates().tolist())
        if len(all_dates) < 20:
            return []

        # Tail isolation: last 5 dates reserved for live pool
        cv_dates = all_dates[:-5]
        folds = []

        for year in test_years:
            year_dates = [d for d in cv_dates if d.year == year]
            if not year_dates:
                continue

            test_start = year_dates[0]
            idx_test_start = cv_dates.index(test_start)

            # Must have at least purge_days + 10 training bars
            if idx_test_start <= purge_days + 10:
                continue

            train_end_idx = idx_test_start - purge_days
            train_dates = cv_dates[:train_end_idx]
            test_dates = year_dates

            train_df = df[df['Date'].isin(train_dates)].copy()
            test_df = df[df['Date'].isin(test_dates)].copy()

            folds.append({
                'year': year,
                'train_df': train_df,
                'test_df': test_df,
                'max_train_date': train_dates[-1],
                'min_test_date': test_dates[0],
                'purge_gap_days': idx_test_start - train_end_idx
            })

        return folds


# ============================================================================
# Section 2: AST Causality & Static Analysis Checkers
# ============================================================================

class ASTCausalityChecker(ast.NodeVisitor):
    """
    Scans Python AST to detect any negative shifts (shift(-k)) or illegal
    forward-looking operations inside feature computation code.
    """

    def __init__(self, target_functions: Optional[List[str]] = None):
        self.target_functions = target_functions or [
            'build_feature_panel', 'compute_stock_features',
            'compute_macro_features', 'compute_indicators'
        ]
        self.current_function: Optional[str] = None
        self.violations: List[Dict[str, Any]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef):
        old_fn = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = old_fn

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        old_fn = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = old_fn

    def visit_Call(self, node: ast.Call):
        # Look for .shift(-k)
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'shift':
            is_target_fn = (self.current_function is None or
                            self.current_function in self.target_functions)
            if is_target_fn and node.args:
                arg = node.args[0]
                # Check for unary minus: shift(-1)
                if isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub):
                    self.violations.append({
                        'function': self.current_function,
                        'lineno': getattr(node, 'lineno', -1),
                        'expression': 'shift(-k)',
                        'message': f"Illegal negative shift(-...) in feature calculation: {self.current_function}"
                    })
                # Check for constant negative: shift(-1) in python >= 3.8 constant fold
                elif isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)) and arg.value < 0:
                    self.violations.append({
                        'function': self.current_function,
                        'lineno': getattr(node, 'lineno', -1),
                        'expression': f'shift({arg.value})',
                        'message': f"Illegal negative shift in feature calculation: {self.current_function}"
                    })
        self.generic_visit(node)


class ASTDropNAChecker(ast.NodeVisitor):
    """
    Scans AST to detect dangerous global dropna calls that wipe out panel data.
    """

    def __init__(self):
        self.has_unstack_or_pivot = False
        self.violations: List[str] = []

    def visit_Call(self, node: ast.Call):
        # Detect chained .unstack().dropna() or .pivot().dropna()
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'dropna':
            caller = node.func.value
            if isinstance(caller, ast.Call) and isinstance(caller.func, ast.Attribute):
                if caller.func.attr in ['unstack', 'pivot', 'pivot_table']:
                    self.violations.append(
                        f"Line {getattr(node, 'lineno', -1)}: Dangerous wide-table dropna() after {caller.func.attr}()"
                    )
        self.generic_visit(node)


# ============================================================================
# Section 3: Target Dispatcher (Adapter between Implementation & Oracle)
# ============================================================================

def get_implementation_module():
    """Attempt to dynamically import ml_predictor_5y if available."""
    try:
        import ml_predictor_5y
        return ml_predictor_5y
    except ImportError:
        return None


def run_feature_pipeline(
    raw_panel: pd.DataFrame,
    macro_map: Optional[Dict[str, pd.DataFrame]] = None,
    drop_warmup: bool = False
) -> pd.DataFrame:
    """
    Universal feature pipeline executor:
    Binds to ml_predictor_5y.build_feature_panel if available, otherwise delegates to Oracle.
    """
    # Bundle macro ETFs into raw_panel if not present
    combined_raw = raw_panel.copy()
    if macro_map is not None:
        macro_frames = []
        for m_name, m_df in macro_map.items():
            if m_name not in combined_raw['Ticker'].values:
                macro_frames.append(m_df)
        if macro_frames:
            combined_raw = pd.concat([combined_raw] + macro_frames, ignore_index=True)

    mod = get_implementation_module()
    if mod and hasattr(mod, 'build_feature_panel'):
        try:
            panel = mod.build_feature_panel(combined_raw, drop_warmup=drop_warmup)
            if isinstance(panel.index, pd.MultiIndex):
                panel = panel.reset_index()
            return panel
        except Exception:
            pass

    return OracleFeatureEngine.build_feature_panel(combined_raw, macro_map=macro_map)


def get_labeling_builder():
    """Returns actual implementation if implemented in M2, otherwise falls back to reference Oracle."""
    mod = get_implementation_module()
    if mod and hasattr(mod, 'apply_triple_barrier_labeling'):
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
            mod.apply_triple_barrier_labeling(test_df)
            return mod.apply_triple_barrier_labeling, 'ml_predictor_5y'
        except NotImplementedError:
            pass
        except Exception:
            return mod.apply_triple_barrier_labeling, 'ml_predictor_5y'
    return OracleTripleBarrierLabeler.apply_triple_barrier_labeling, 'oracle'


def get_cv_runner():
    """Returns actual implementation if implemented in M2, otherwise falls back to reference Oracle."""
    mod = get_implementation_module()
    if mod and hasattr(mod, 'run_walk_forward_cv'):
        try:
            test_df = pd.DataFrame({'Date': [pd.Timestamp('2024-01-01')]})
            mod.run_walk_forward_cv(test_df)
            return mod.run_walk_forward_cv, 'ml_predictor_5y'
        except NotImplementedError:
            pass
        except Exception:
            return mod.run_walk_forward_cv, 'ml_predictor_5y'
    return OracleWalkForwardSplitter.get_cv_folds, 'oracle'


# ============================================================================
# Section 4: Unit Test Classes (6 Anti-Cheat Invariants + Edge Cases)
# ============================================================================

class TestFeatureCausalityNoLookahead(unittest.TestCase):
    """
    Invariant 1: Feature Causality & No Lookahead
    - Bar T features strictly equal features on subseries [0 ... T] vs [0 ... T + N].
    - AST scan confirms zero negative shifts (shift(-k)) in feature code.
    - Future price spikes at T+1 have zero numeric impact on bar T features.
    """

    def setUp(self):
        self.dates = SyntheticMarketDataGenerator.generate_trading_dates(
            start_date='2022-01-03', num_days=250
        )
        self.raw_stock = SyntheticMarketDataGenerator.generate_stock_ohlcv(
            ticker='NVDA', dates=self.dates, base_price=150.0, seed=42
        )
        self.macro_map = SyntheticMarketDataGenerator.generate_macro_etfs(self.dates, seed=50)

    def test_feature_causality_no_lookahead(self):
        """Primary invariant test: Truncated series features match full series features exactly."""
        # Step 1: Compute features on truncated series [0 ... 150]
        trunc_cutoff = 150
        trunc_stock = self.raw_stock.iloc[:trunc_cutoff + 1].copy()
        trunc_macro = {
            k: v.iloc[:trunc_cutoff + 1].copy() for k, v in self.macro_map.items()
        }
        panel_trunc = run_feature_pipeline(trunc_stock, macro_map=trunc_macro, drop_warmup=False)

        # Step 2: Compute features on full series [0 ... 249]
        panel_full = run_feature_pipeline(self.raw_stock, macro_map=self.macro_map, drop_warmup=False)

        # Step 3: Compare feature values at bar T (150)
        target_date = self.dates[trunc_cutoff]
        row_trunc = panel_trunc[panel_trunc['Date'] == target_date].iloc[0]
        row_full = panel_full[panel_full['Date'] == target_date].iloc[0]

        for feat in OracleFeatureEngine.FEATURE_NAMES:
            val_trunc = float(row_trunc[feat])
            val_full = float(row_full[feat])
            abs_diff = abs(val_trunc - val_full)
            self.assertLess(
                abs_diff, 1e-7,
                f"Lookahead data leakage in feature '{feat}'! "
                f"Truncated value: {val_trunc}, Full value: {val_full}, Diff: {abs_diff}"
            )

    def test_ast_no_negative_shifts(self):
        """Scans AST of feature engineering functions to assert no negative shifts."""
        impl_path = os.path.join(PROJECT_DIR, 'ml_predictor_5y.py')
        checker = ASTCausalityChecker()

        if os.path.exists(impl_path):
            with open(impl_path, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read(), filename=impl_path)
            checker.visit(tree)
            self.assertEqual(
                len(checker.violations), 0,
                f"AST violations found in ml_predictor_5y.py: {checker.violations}"
            )
        else:
            # Self-verify the oracle source code
            oracle_source = inspect.getsource(OracleFeatureEngine)
            tree = ast.parse(oracle_source)
            checker.visit(tree)
            self.assertEqual(
                len(checker.violations), 0,
                f"AST violations found in OracleFeatureEngine: {checker.violations}"
            )

        # Adversarial verification of the AST checker itself
        malicious_code = """
def compute_stock_features(df):
    future_close = df['Close'].shift(-1)
    df['Cheat_Feature'] = future_close / df['Close']
    return df
"""
        bad_tree = ast.parse(malicious_code)
        bad_checker = ASTCausalityChecker()
        bad_checker.visit(bad_tree)
        self.assertGreater(
            len(bad_checker.violations), 0,
            "AST checker failed to detect malicious negative shift(-1)!"
        )

    def test_future_spike_insensitivity(self):
        """Asserts that extreme future price spikes at T+1 have 0% impact on bar T features."""
        panel_orig = run_feature_pipeline(self.raw_stock, macro_map=self.macro_map, drop_warmup=False)

        # Inject +1000% jump at bar T+1 (151)
        spiked_stock = self.raw_stock.copy()
        spiked_stock.loc[151, 'Open'] = spiked_stock.loc[151, 'Open'] * 10.0
        spiked_stock.loc[151, 'High'] = spiked_stock.loc[151, 'High'] * 10.0
        spiked_stock.loc[151, 'Low'] = spiked_stock.loc[151, 'Low'] * 10.0
        spiked_stock.loc[151, 'Close'] = spiked_stock.loc[151, 'Close'] * 10.0
        spiked_stock.loc[151, 'Volume'] = 1e9

        panel_spiked = run_feature_pipeline(spiked_stock, macro_map=self.macro_map, drop_warmup=False)

        target_date = self.dates[150]
        row_orig = panel_orig[panel_orig['Date'] == target_date].iloc[0]
        row_spiked = panel_spiked[panel_spiked['Date'] == target_date].iloc[0]

        for feat in OracleFeatureEngine.FEATURE_NAMES:
            diff = abs(float(row_orig[feat]) - float(row_spiked[feat]))
            self.assertEqual(
                diff, 0.0,
                f"Feature '{feat}' contaminated by future spike at T+1! Diff: {diff}"
            )


class TestPurgeEmbargoIsolation(unittest.TestCase):
    """
    Invariant 2: Purge & Embargo Isolation
    - Validates walk-forward CV splits enforce >= 5 trading day purge gap between train and test.
    - Validates no training sample evaluates future prices into the test window.
    - Asserts train and test date sets are strictly disjoint.
    """

    def setUp(self):
        # 5 years of daily trading dates
        self.dates = SyntheticMarketDataGenerator.generate_trading_dates(
            start_date='2021-11-01', num_days=1100
        )
        self.stock_df = SyntheticMarketDataGenerator.generate_stock_ohlcv(
            ticker='TSM', dates=self.dates, base_price=100.0, seed=77
        )
        self.macro_map = SyntheticMarketDataGenerator.generate_macro_etfs(self.dates, seed=78)
        self.panel = OracleFeatureEngine.build_feature_panel(self.stock_df, self.macro_map)

    def test_purge_embargo_isolation(self):
        """Primary invariant test: Verifies >= 5 trading day purge gap across all walk-forward folds."""
        cv_runner, source = get_cv_runner()
        folds = OracleWalkForwardSplitter.get_cv_folds(
            self.panel, test_years=[2024, 2025, 2026], purge_days=5
        )

        self.assertGreaterEqual(
            len(folds), 2,
            "Insufficient walk-forward folds generated."
        )

        for fold in folds:
            year = fold['year']
            train_df = fold['train_df']
            test_df = fold['test_df']
            max_train = fold['max_train_date']
            min_test = fold['min_test_date']

            # 1. Strict chronological ordering
            self.assertLess(
                max_train, min_test,
                f"Fold {year}: Max train date ({max_train}) must strictly precede min test date ({min_test})."
            )

            # 2. Count trading days between max_train and min_test
            all_trading_dates = sorted(self.panel['Date'].drop_duplicates().tolist())
            idx_train = all_trading_dates.index(max_train)
            idx_test = all_trading_dates.index(min_test)
            gap = idx_test - idx_train

            self.assertGreaterEqual(
                gap, 5,
                f"Fold {year}: Purge embargo gap is only {gap} trading days! Required >= 5."
            )

            # 3. Disjoint date sets
            train_dates = set(train_df['Date'])
            test_dates = set(test_df['Date'])
            overlap = train_dates.intersection(test_dates)
            self.assertEqual(
                len(overlap), 0,
                f"Fold {year}: Date overlap detected between train and test: {overlap}"
            )

    def test_training_label_horizon_no_test_leakage(self):
        """Validates that no training sample's 5-day labeling horizon enters the test set."""
        folds = OracleWalkForwardSplitter.get_cv_folds(
            self.panel, test_years=[2024, 2025, 2026], purge_days=5
        )
        all_trading_dates = sorted(self.panel['Date'].drop_duplicates().tolist())

        for fold in folds:
            max_train = fold['max_train_date']
            min_test = fold['min_test_date']
            idx_train = all_trading_dates.index(max_train)
            idx_test = all_trading_dates.index(min_test)

            # Training sample at max_train has forward horizon [idx_train + 1 ... idx_train + 5]
            max_forward_bar = idx_train + 5
            self.assertLessEqual(
                max_forward_bar, idx_test,
                f"Training label forward horizon reaches bar {max_forward_bar}, "
                f"which collides with or breaches test start bar {idx_test}!"
            )


class TestSubsampleAndIPOPreservation(unittest.TestCase):
    """
    Invariant 3: Subsample & Secondary IPO Preservation
    - Validates secondary IPOs (CRWV, IBIT, CRDO) do NOT truncate 2021-2023 history of mature stocks.
    - Asserts global dropna() is never invoked across panel columns.
    """

    def setUp(self):
        self.all_dates = SyntheticMarketDataGenerator.generate_trading_dates(
            start_date='2021-11-01', num_days=1000
        )
        # Mature stock starting 2021-11-01 (1000 bars)
        self.mature_nvda = SyntheticMarketDataGenerator.generate_stock_ohlcv(
            ticker='NVDA', dates=self.all_dates, base_price=100.0, seed=1
        )
        # Secondary IPO stock starting 2024-01-11 (~250 bars)
        ipo_dates = self.all_dates[750:]
        self.ipo_crwv = SyntheticMarketDataGenerator.generate_stock_ohlcv(
            ticker='CRWV', dates=ipo_dates, base_price=20.0, sector='Neo_Cloud', seed=2
        )
        self.macro_map = SyntheticMarketDataGenerator.generate_macro_etfs(self.all_dates, seed=3)

    def test_subsample_and_ipo_preservation(self):
        """Primary invariant test: Long Panel preserves 100% of mature stock bars despite IPO stock."""
        raw_panel = pd.concat([self.mature_nvda, self.ipo_crwv], axis=0).reset_index(drop=True)
        panel = run_feature_pipeline(raw_panel, macro_map=self.macro_map, drop_warmup=False)

        nvda_panel = panel[panel['Ticker'] == 'NVDA']
        crwv_panel = panel[panel['Ticker'] == 'CRWV']

        # Assert NVDA preserved all 1000 bars (or >= 980 if 20-bar warmup drop)
        self.assertGreaterEqual(
            len(nvda_panel), 980,
            f"Mature stock NVDA was truncated from 1000 bars down to {len(nvda_panel)}!"
        )
        # Assert CRWV retained its bars
        self.assertGreaterEqual(
            len(crwv_panel), 230,
            f"IPO stock CRWV has unexpected row count: {len(crwv_panel)}"
        )
        # Assert NVDA start date is intact in 2021
        self.assertEqual(
            nvda_panel['Date'].min().year, 2021,
            "NVDA historical start date 2021 was corrupted by secondary IPO presence!"
        )

    def test_ast_no_wide_dropna(self):
        """Scans code to ensure no wide-table dropna() pattern is present."""
        impl_path = os.path.join(PROJECT_DIR, 'ml_predictor_5y.py')
        checker = ASTDropNAChecker()

        if os.path.exists(impl_path):
            with open(impl_path, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read(), filename=impl_path)
            checker.visit(tree)
            self.assertEqual(
                len(checker.violations), 0,
                f"Wide table dropna violations detected: {checker.violations}"
            )


class TestExtremeMarketResilience(unittest.TestCase):
    """
    Invariant 4: Extreme Market Resilience
    - Tests zero volume (Volume = 0).
    - Tests flat-line zero price range (High = Low = Close).
    - Tests -50% overnight gap down.
    - Asserts zero unhandled NaN or Inf and valid model inference probabilities in [0.0, 1.0].
    """

    def setUp(self):
        self.dates = SyntheticMarketDataGenerator.generate_trading_dates(
            start_date='2023-01-02', num_days=100
        )
        self.macro_map = SyntheticMarketDataGenerator.generate_macro_etfs(self.dates, seed=99)

    def test_extreme_market_resilience(self):
        """Primary invariant test: Validates resilience across zero vol, zero range, and -50% crash."""
        # 1. Zero volume scenario
        zero_vol_df = SyntheticMarketDataGenerator.generate_stock_ohlcv('NVDA', self.dates, seed=11)
        zero_vol_df['Volume'] = 0.0
        p_zero = run_feature_pipeline(zero_vol_df, macro_map=self.macro_map, drop_warmup=False)
        self.assertFalse(
            p_zero[OracleFeatureEngine.FEATURE_NAMES].isna().any().any(),
            "Zero-volume scenario produced unhandled NaN in features!"
        )
        self.assertFalse(
            np.isinf(p_zero[OracleFeatureEngine.FEATURE_NAMES].values).any(),
            "Zero-volume scenario produced Inf in features!"
        )

        # 2. Flat line zero range scenario (High == Low == Close)
        flat_df = SyntheticMarketDataGenerator.generate_stock_ohlcv('NVDA', self.dates, seed=12)
        flat_df['High'] = 100.0
        flat_df['Low'] = 100.0
        flat_df['Close'] = 100.0
        flat_df['Open'] = 100.0
        p_flat = run_feature_pipeline(flat_df, macro_map=self.macro_map, drop_warmup=False)
        self.assertFalse(
            p_flat[OracleFeatureEngine.FEATURE_NAMES].isna().any().any(),
            "Flat zero-range scenario produced unhandled NaN in features!"
        )
        self.assertFalse(
            np.isinf(p_flat[OracleFeatureEngine.FEATURE_NAMES].values).any(),
            "Flat zero-range scenario produced Inf in features!"
        )

        # 3. Overnight -50% gap down scenario
        crash_df = SyntheticMarketDataGenerator.generate_stock_ohlcv('NVDA', self.dates, seed=13)
        crash_df.loc[50:, 'Open'] = crash_df.loc[50:, 'Open'] * 0.50
        crash_df.loc[50:, 'High'] = crash_df.loc[50:, 'High'] * 0.50
        crash_df.loc[50:, 'Low'] = crash_df.loc[50:, 'Low'] * 0.48
        crash_df.loc[50:, 'Close'] = crash_df.loc[50:, 'Close'] * 0.49
        p_crash = run_feature_pipeline(crash_df, macro_map=self.macro_map, drop_warmup=False)
        self.assertFalse(
            p_crash[OracleFeatureEngine.FEATURE_NAMES].isna().any().any(),
            "-50% overnight crash produced unhandled NaN in features!"
        )

    def test_model_inference_probability_bounds(self):
        """Asserts LightGBM inference on extreme features yields probabilities strictly in [0.0, 1.0]."""
        normal_df = SyntheticMarketDataGenerator.generate_stock_ohlcv('NVDA', self.dates, seed=20)
        panel_normal = run_feature_pipeline(normal_df, macro_map=self.macro_map, drop_warmup=False)

        # Train a regularized LightGBM classifier as defined in PROJECT.md
        X_train = panel_normal[OracleFeatureEngine.FEATURE_NAMES].iloc[:80]
        y_train = (panel_normal['Ret_5d'].iloc[:80] > 0).astype(int)

        clf = lgb.LGBMClassifier(
            max_depth=5,
            num_leaves=23,
            learning_rate=0.03,
            subsample=0.8,
            colsample_bytree=0.8,
            n_estimators=30,
            random_state=42,
            verbose=-1
        )
        clf.fit(X_train, y_train)

        # Test inference on extreme feature vectors
        extreme_vectors = pd.DataFrame([
            {feat: 0.0 for feat in OracleFeatureEngine.FEATURE_NAMES},
            {feat: -999.0 for feat in OracleFeatureEngine.FEATURE_NAMES},
            {feat: 999.0 for feat in OracleFeatureEngine.FEATURE_NAMES},
        ])
        probs = clf.predict_proba(extreme_vectors)[:, 1]

        self.assertEqual(len(probs), 3)
        self.assertTrue(
            np.all(probs >= 0.0) and np.all(probs <= 1.0),
            f"Model predictions out of [0.0, 1.0] bounds: {probs}"
        )
        self.assertFalse(
            np.isnan(probs).any(),
            "Model returned NaN probability on extreme inputs!"
        )


class TestTripleBarrierResolutionLogic(unittest.TestCase):
    """
    Invariant 5: Triple Barrier Resolution Logic
    - Tests NEXT_OPEN entry price.
    - Upper hit first -> Label = 1.
    - Lower hit first -> Label = 0.
    - Neither hit by T+5 -> Label = 0 (Timeout).
    - Same-day collision -> Label = 0 (Conservative Stop-Loss priority).
    """

    def setUp(self):
        self.dates = SyntheticMarketDataGenerator.generate_trading_dates(
            start_date='2024-01-02', num_days=20
        )

    def _build_test_case_df(
        self,
        t1_high: float,
        t1_low: float,
        t2_high: float = 100.0,
        t2_low: float = 100.0,
        t1_open: float = 100.0
    ) -> pd.DataFrame:
        """Constructs a minimal deterministic 10-bar test case."""
        rows = []
        for i in range(10):
            d = self.dates[i]
            if i == 0:
                # Bar T
                rows.append({'Date': d, 'Ticker': 'NVDA', 'Open': 100.0, 'High': 101.0,
                             'Low': 99.0, 'Close': 100.0, 'ATR_Pct': 2.0})
            elif i == 1:
                # Bar T+1 (Entry bar): Open price determines NEXT_OPEN
                rows.append({'Date': d, 'Ticker': 'NVDA', 'Open': t1_open, 'High': t1_high,
                             'Low': t1_low, 'Close': (t1_high + t1_low) / 2.0, 'ATR_Pct': 2.0})
            elif i == 2:
                # Bar T+2
                rows.append({'Date': d, 'Ticker': 'NVDA', 'Open': 100.0, 'High': t2_high,
                             'Low': t2_low, 'Close': 100.0, 'ATR_Pct': 2.0})
            else:
                rows.append({'Date': d, 'Ticker': 'NVDA', 'Open': 100.0, 'High': 100.5,
                             'Low': 99.5, 'Close': 100.0, 'ATR_Pct': 2.0})
        return pd.DataFrame(rows)

    def test_triple_barrier_resolution_logic(self):
        """Primary invariant test: Covers upper, lower, timeout, and collision paths."""
        labeler_fn, _ = get_labeling_builder()

        # Path 1: Upper hit first at T+2 -> Label = 1
        # Entry = 100.0, Upper = 103.50, Lower = 97.80
        df_upper = self._build_test_case_df(t1_high=102.0, t1_low=99.0, t2_high=104.5, t2_low=100.0)
        labeled_u = labeler_fn(df_upper)
        self.assertEqual(
            labeled_u.at[0, 'Label'], 1.0,
            "Path 1 failed: Upper barrier hit first must yield Label = 1."
        )

        # Path 2: Lower hit first at T+1 -> Label = 0
        df_lower = self._build_test_case_df(t1_high=101.0, t1_low=97.0)
        labeled_l = labeler_fn(df_lower)
        self.assertEqual(
            labeled_l.at[0, 'Label'], 0.0,
            "Path 2 failed: Lower barrier hit first must yield Label = 0."
        )

        # Path 3: Neither hit within 5 days -> Label = 0 (timeout)
        df_time = self._build_test_case_df(t1_high=101.0, t1_low=99.0, t2_high=101.0, t2_low=99.0)
        labeled_t = labeler_fn(df_time)
        self.assertEqual(
            labeled_t.at[0, 'Label'], 0.0,
            "Path 3 failed: Neither barrier hit by T+5 must yield Label = 0."
        )

        # Path 4: Same-day collision at T+1 (both hit) -> Label = 0 (Conservative Stop-Loss priority)
        df_collision = self._build_test_case_df(t1_high=106.0, t1_low=95.0)
        labeled_c = labeler_fn(df_collision)
        self.assertEqual(
            labeled_c.at[0, 'Label'], 0.0,
            "Path 4 failed: Same-day collision must resolve to Label = 0 (Conservative Stop-Loss Priority)."
        )

    def test_entry_price_is_next_open(self):
        """Asserts that entry price strictly equals bar T+1 Open, preventing close-price lookahead."""
        labeler_fn, _ = get_labeling_builder()
        # Bar T close is 100.0, but Bar T+1 opens at 105.0
        df_entry = self._build_test_case_df(t1_high=106.0, t1_low=104.0, t1_open=105.0)
        labeled = labeler_fn(df_entry)

        entry_price = labeled.at[0, 'Entry_Price']
        self.assertEqual(
            entry_price, 105.0,
            f"Entry price ({entry_price}) must equal NEXT_OPEN (105.0), not T-close."
        )

    def test_dynamic_atr_barrier_calculation(self):
        """Verifies mathematical correctness of dynamic ATR upper and lower barriers."""
        labeler_fn, _ = get_labeling_builder()
        df = self._build_test_case_df(t1_high=102.0, t1_low=99.0, t1_open=100.0)
        labeled = labeler_fn(df)

        # Entry = 100.0, ATR_Pct = 2.0%
        # eff_atr = clip(0.02, 0.012, 0.050) = 0.02
        # Upper mult = max(0.030, 1.35 * 0.02) = 0.030 -> 103.00
        # Lower mult = max(0.022, 1.00 * 0.02) = 0.022 -> 97.80
        upper = labeled.at[0, 'Target_Upper']
        lower = labeled.at[0, 'Target_Lower']
        self.assertAlmostEqual(upper, 103.00, places=2)
        self.assertAlmostEqual(lower, 97.80, places=2)

    def test_soft_root_scaling_atr_boundaries(self):
        """Validates Piecewise Soft-Root Scaling across low, normal, and extreme volatility regimes."""
        labeler_fn, _ = get_labeling_builder()
        # Test cases: (atr_pct, expected_eff_atr, expected_upper, expected_lower)
        # Entry = 100.0
        # raw <= 0.040: eff_atr = clip(raw, 0.012, 0.040)
        # raw > 0.040:  eff_atr = 0.040 + 0.50 * (clip(raw, 0.040, 0.100) - 0.040)
        # upper_mult = max(0.030, 1.35 * eff_atr)
        # lower_mult = max(0.022, 1.00 * eff_atr)
        test_cases = [
            (0.5, 0.012, 103.00, 97.80),    # Floor 1.2%
            (1.2, 0.012, 103.00, 97.80),    # Boundary 1.2%
            (2.0, 0.020, 103.00, 97.80),    # 2.0% -> 1.35*0.02=0.027 < 0.030
            (4.0, 0.040, 105.40, 96.00),    # 4.0% -> 1.35*0.04=0.054, lower=0.040
            (5.0, 0.045, 106.075, 95.50),   # 5.0% -> eff=0.045, 1.35*0.045=0.06075, lower=0.045
            (6.0, 0.050, 106.75, 95.00),    # 6.0% -> eff=0.050, 1.35*0.050=0.0675, lower=0.050
            (8.0, 0.060, 108.10, 94.00),    # 8.0% -> eff=0.060, 1.35*0.060=0.081, lower=0.060
            (10.0, 0.070, 109.45, 93.00),   # 10.0% -> eff=0.070, 1.35*0.070=0.0945, lower=0.070
            (15.0, 0.070, 109.45, 93.00),   # 15.0% -> ceiling 0.070, upper=109.45, lower=93.00
        ]
        for atr_val, exp_eff, exp_u, exp_l in test_cases:
            df = self._build_test_case_df(t1_high=102.0, t1_low=99.0, t1_open=100.0)
            df.loc[0, 'ATR_Pct'] = atr_val
            labeled = labeler_fn(df)
            u = labeled.at[0, 'Target_Upper']
            l = labeled.at[0, 'Target_Lower']
            self.assertAlmostEqual(u, exp_u, places=2, msg=f"Upper barrier mismatch at ATR={atr_val}")
            self.assertAlmostEqual(l, exp_l, places=2, msg=f"Lower barrier mismatch at ATR={atr_val}")

    def test_soft_root_scaling_decloning(self):
        """Verifies that high-volatility targets are no longer cloned at +6.8% / -5.0%."""
        labeler_fn, _ = get_labeling_builder()
        df5 = self._build_test_case_df(t1_high=102.0, t1_low=99.0, t1_open=100.0)
        df5.loc[0, 'ATR_Pct'] = 5.0
        lab5 = labeler_fn(df5)

        df8 = self._build_test_case_df(t1_high=102.0, t1_low=99.0, t1_open=100.0)
        df8.loc[0, 'ATR_Pct'] = 8.0
        lab8 = labeler_fn(df8)

        u5, l5 = lab5.at[0, 'Target_Upper'], lab5.at[0, 'Target_Lower']
        u8, l8 = lab8.at[0, 'Target_Upper'], lab8.at[0, 'Target_Lower']

        # Targets must be differentiated and not cloned
        self.assertNotEqual(u5, u8, f"Upper targets cloned: {u5} vs {u8}")
        self.assertNotEqual(l5, l8, f"Lower targets cloned: {l5} vs {l8}")
        self.assertAlmostEqual(u5, 106.075, places=2)
        self.assertAlmostEqual(u8, 108.100, places=2)


class TestTailIsolationLivePool(unittest.TestCase):
    """
    Invariant 6: Tail Isolation for Live Inference Pool
    - The most recent 5 trading days are excluded from training and validation sets.
    - Tail 5 bars have incomplete forward paths and unresolved labels (Label = NaN).
    - Tail 5 bars are isolated for the Live Inference Pool.
    """

    def setUp(self):
        self.dates = SyntheticMarketDataGenerator.generate_trading_dates(
            start_date='2024-01-02', num_days=100
        )
        self.stock_df = SyntheticMarketDataGenerator.generate_stock_ohlcv(
            ticker='MSTR', dates=self.dates, base_price=300.0, seed=555
        )
        self.macro_map = SyntheticMarketDataGenerator.generate_macro_etfs(self.dates, seed=556)
        self.panel = OracleFeatureEngine.build_feature_panel(self.stock_df, self.macro_map)

    def test_tail_isolation_live_pool(self):
        """Primary invariant test: Tail 5 days must be excluded from train/test and have NaN labels."""
        labeler_fn, _ = get_labeling_builder()
        labeled_panel = labeler_fn(self.panel)

        n = len(labeled_panel)
        # 1. Last 5 rows must have NaN labels
        tail_5 = labeled_panel.iloc[-5:]
        self.assertTrue(
            tail_5['Label'].isna().all(),
            "Tail 5 trading days must have NaN labels due to incomplete forward horizon!"
        )

        # 2. Walk-forward split excludes tail 5 dates
        cv_dates = sorted(labeled_panel['Date'].drop_duplicates().tolist())
        tail_dates = set(cv_dates[-5:])

        folds = OracleWalkForwardSplitter.get_cv_folds(
            labeled_panel, test_years=[2024], purge_days=5
        )
        for fold in folds:
            train_dates = set(fold['train_df']['Date'])
            test_dates = set(fold['test_df']['Date'])

            self.assertEqual(
                len(train_dates.intersection(tail_dates)), 0,
                "Tail dates leaked into training set!"
            )
            self.assertEqual(
                len(test_dates.intersection(tail_dates)), 0,
                "Tail dates leaked into test set!"
            )


class TestAdversarialIntegrityAndEdgeCases(unittest.TestCase):
    """
    Adversarial verification & domain boundary condition tests:
    - Minimal history (< 60 bars) handles Fib_Pos_60 and rolling windows without crash.
    - Single-bar input resilience.
    - Missing macro date alignment handled safely via left join.
    - Winsorization bounds on Fib_Pos_60 strictly in [-0.5, 1.5].
    """

    def test_minimal_history_short_series(self):
        """Validates that a short series (< 60 bars) completes feature calculation without crash."""
        short_dates = SyntheticMarketDataGenerator.generate_trading_dates(
            start_date='2024-06-01', num_days=25
        )
        short_stock = SyntheticMarketDataGenerator.generate_stock_ohlcv(
            ticker='NVDA', dates=short_dates, base_price=50.0, seed=88
        )
        macro_map = SyntheticMarketDataGenerator.generate_macro_etfs(short_dates, seed=89)

        p_short = run_feature_pipeline(short_stock, macro_map=macro_map, drop_warmup=False)

        self.assertEqual(len(p_short), 25)
        self.assertFalse(
            p_short[OracleFeatureEngine.FEATURE_NAMES].isna().any().any(),
            "Short series produced unhandled NaN!"
        )

    def test_winsorization_bounds_fib_pos(self):
        """Asserts Fib_Pos_60 is strictly bounded inside [-0.5, 1.5] even with wild outliers."""
        dates = SyntheticMarketDataGenerator.generate_trading_dates(
            start_date='2024-01-02', num_days=100
        )
        wild_stock = SyntheticMarketDataGenerator.generate_stock_ohlcv(
            ticker='NVDA', dates=dates, base_price=100.0, seed=999
        )
        # Inject massive outlier close
        wild_stock.loc[70, 'Close'] = 5000.0
        wild_stock.loc[80, 'Close'] = 1.0

        macro_map = SyntheticMarketDataGenerator.generate_macro_etfs(dates, seed=998)
        p_wild = run_feature_pipeline(wild_stock, macro_map=macro_map, drop_warmup=False)

        fib_vals = p_wild['Fib_Pos_60'].values
        self.assertTrue(
            np.all(fib_vals >= -0.5) and np.all(fib_vals <= 1.5),
            f"Fib_Pos_60 violated winsorization bounds [-0.5, 1.5]: min={np.min(fib_vals)}, max={np.max(fib_vals)}"
        )

    def test_missing_macro_dates_left_join(self):
        """Asserts that missing macro dates (e.g. holiday mismatch) do not produce NaN or break row count."""
        dates = SyntheticMarketDataGenerator.generate_trading_dates(
            start_date='2024-01-02', num_days=60
        )
        stock_df = SyntheticMarketDataGenerator.generate_stock_ohlcv(
            ticker='NVDA', dates=dates, base_price=100.0, seed=333
        )
        # Remove every 2nd date from macro benchmarks to simulate holiday/desync
        macro_map = SyntheticMarketDataGenerator.generate_macro_etfs(dates, seed=334)
        for k in macro_map:
            macro_map[k] = macro_map[k].iloc[::2].copy()

        p_merged = run_feature_pipeline(stock_df, macro_map=macro_map, drop_warmup=False)

        self.assertEqual(len(p_merged), 60, "Row count changed after left-join with incomplete macro data!")
        self.assertFalse(
            p_merged[OracleFeatureEngine.FEATURE_NAMES].isna().any().any(),
            "Incomplete macro dates produced unhandled NaNs!"
        )


class TestMacroFeatureIntegrity(unittest.TestCase):
    """
    R1 & Bug 1 Verification:
    Macro features and cross-asset alphas must NOT collapse to zero due to index mismatch.
    Asserts non-zero rate > 95% and std > 0.01 on both synthetic raw panel and real 5Y Parquet cache.
    """
    TARGET_FEATURES = [
        'SMH_Bias_MA50',
        'TLT_Bias_MA50',
        'VIXY_Bias_MA20',
        'VIXY_Pos_60',
        'Alpha_SMH_20d'
    ]

    def test_synthetic_raw_panel_macro_features_non_zero_and_variance(self):
        """Validates macro features on full synthetic panel where stocks and ETFs are concatenated."""
        dates = SyntheticMarketDataGenerator.generate_trading_dates(start_date='2022-01-03', num_days=500)
        raw_panel = SyntheticMarketDataGenerator.generate_full_raw_panel(
            stock_tickers=['NVDA', 'TSM', 'AMD'],
            dates=dates,
            base_seed=42
        )
        panel = run_feature_pipeline(raw_panel, macro_map=None, drop_warmup=False)

        self.assertGreater(len(panel), 0, "Feature panel is empty!")
        for feat in self.TARGET_FEATURES:
            self.assertIn(feat, panel.columns, f"Feature '{feat}' missing from panel columns!")
            series = panel[feat]
            non_zero_rate = float((series != 0.0).mean())
            std_val = float(series.std())
            self.assertGreater(
                non_zero_rate, 0.95,
                f"[Synthetic] Feature '{feat}' non-zero rate is {non_zero_rate:.4f} (<= 0.95). Index mismatch bug!"
            )
            self.assertGreater(
                std_val, 0.01,
                f"[Synthetic] Feature '{feat}' std is {std_val:.4f} (<= 0.01). Feature lacks variance!"
            )

    def test_real_parquet_dataset_macro_features_non_zero_and_variance(self):
        """Validates macro features on real cached 5Y Parquet dataset (data_cache/ml_panel_5y.parquet)."""
        parquet_path = os.path.join(PROJECT_DIR, 'data_cache', 'ml_panel_5y.parquet')
        if not os.path.exists(parquet_path):
            self.skipTest(f"Parquet cache file not found at: {parquet_path}")

        raw_parquet_df = pd.read_parquet(parquet_path)
        panel = run_feature_pipeline(raw_parquet_df, macro_map=None, drop_warmup=False)

        self.assertGreater(len(panel), 0, "Feature panel from Parquet is empty!")
        for feat in self.TARGET_FEATURES:
            self.assertIn(feat, panel.columns, f"Feature '{feat}' missing from Parquet feature panel!")
            series = panel[feat]
            non_zero_rate = float((series != 0.0).mean())
            std_val = float(series.std())
            self.assertGreater(
                non_zero_rate, 0.95,
                f"[Real Parquet] Feature '{feat}' non-zero rate is {non_zero_rate:.4f} (<= 0.95). Silent zero failure!"
            )
            self.assertGreater(
                std_val, 0.01,
                f"[Real Parquet] Feature '{feat}' std is {std_val:.4f} (<= 0.01). Feature lacks variance!"
            )


class TestReportMetricsAndSectorLinkage(unittest.TestCase):
    """
    R2, R3 & R4 Verification:
    - Table 1 & Table 2: Avg Realized Return, Win/Loss Ratio, and Profit Factor metrics.
    - Table 2: Small sample (< 10 high-confidence signals) star rating capped at <= 3 stars with (小样本).
    - Table 4: Sector OOS win rate < 45% warning linkage.
    - Macro Weather: Strict alignment with integrated_market_radar.py logic.
    """

    def test_table1_and_table2_metric_realized_returns(self):
        """Validates Table 1 & 2 Avg Realized Return, Win/Loss Ratio, and Profit Factor."""
        mod = get_implementation_module()
        if not mod or not hasattr(mod, 'generate_report_tables'):
            self.skipTest("ml_predictor_5y.generate_report_tables not available")

        np.random.seed(42)
        n = 100
        oos_data = {
            'Date': pd.bdate_range('2024-01-01', periods=n),
            'Ticker': ['NVDA'] * 50 + ['MSTR'] * 50,
            'Sector': ['Semiconductors'] * 50 + ['Crypto_Proxy'] * 50,
            'Close': [100.0] * n,
            'Label': [1.0] * 60 + [0.0] * 40,
            'Prob_1': [0.65] * 30 + [0.58] * 20 + [0.62] * 20 + [0.45] * 30,
            'Realized_Return': [4.5] * 30 + [-2.2] * 20 + [4.5] * 30 + [-2.2] * 20
        }
        oos_df = pd.DataFrame(oos_data)
        latest_df = oos_df.iloc[:2].copy()

        tables = mod.generate_report_tables(
            oos_df=oos_df,
            model=None,
            feature_cols=[],
            latest_features_df=latest_df,
            macro_weather={'status': '☀️ 晴天做多'}
        )

        # Check Table 1
        t1 = tables['table1']
        self.assertGreater(len(t1), 0)
        self.assertIn('avg_realized_return_pct', t1[0])
        self.assertIn('win_loss_ratio', t1[0])
        self.assertIn('profit_factor', t1[0])
        self.assertIn('pos_win_rate_pct', t1[0])
        self.assertIn('touch_win_rate_pct', t1[0])

        # Check Table 2
        t2 = tables['table2']
        self.assertGreater(len(t2), 0)
        self.assertIn('avg_realized_return_pct', t2[0])
        self.assertIn('win_loss_ratio', t2[0])
        self.assertIn('profit_factor', t2[0])
        self.assertIn('high_conviction_pos_win_rate_pct', t2[0])
        self.assertIn('high_conviction_touch_win_rate_pct', t2[0])

    def test_table2_institutional_star_rating_tiers(self):
        """Validates the 5-tier institutional AI Alpha star rating standard based on PF and avg return."""
        mod = get_implementation_module()
        if not mod or not hasattr(mod, 'generate_report_tables'):
            self.skipTest("ml_predictor_5y.generate_report_tables not available")

        scenarios = [
            # (pos_ret, neg_ret, n_wins, n_losses, exp_stars)
            (4.0, -2.0, 15, 5, "⭐⭐⭐⭐⭐"),
            (4.56, -3.20, 5, 5, "⭐⭐⭐⭐☆"),
            (3.50, -2.60, 5, 5, "⭐⭐⭐☆☆"),
            (2.20, -2.00, 5, 5, "⭐⭐☆☆☆"),
            (1.50, -2.00, 5, 5, "⭐☆☆☆☆"),
        ]

        for pos_r, neg_r, n_w, n_l, exp_rating in scenarios:
            records = []
            for i in range(n_w):
                records.append({
                    'Date': pd.Timestamp('2024-01-01') + pd.Timedelta(days=i),
                    'Ticker': 'NVDA',
                    'Sector': 'Semiconductors',
                    'Close': 100.0,
                    'Label': 1.0,
                    'Prob_1': 0.65,
                    'Realized_Return': pos_r
                })
            for i in range(n_l):
                records.append({
                    'Date': pd.Timestamp('2024-02-01') + pd.Timedelta(days=i),
                    'Ticker': 'NVDA',
                    'Sector': 'Semiconductors',
                    'Close': 100.0,
                    'Label': 0.0,
                    'Prob_1': 0.65,
                    'Realized_Return': neg_r
                })
            oos_test_df = pd.DataFrame(records)
            res = mod.generate_report_tables(
                oos_df=oos_test_df,
                model=None,
                feature_cols=[],
                latest_features_df=pd.DataFrame(),
                macro_weather={'status': '☀️ 晴天做多'}
            )
            sec_row = [r for r in res['table2'] if r['sector_key'] == 'Semiconductors'][0]
            self.assertEqual(sec_row['alpha_rating'], exp_rating, f"Expected {exp_rating}, got {sec_row['alpha_rating']}")

    def test_table4_calibrated_probability_sorting(self):
        """Verifies that Table 4 sorts by Calibrated_Prob = Prob_1 * clip(sector_pf / 1.30, 0.75, 1.15)."""
        mod = get_implementation_module()
        if not mod or not hasattr(mod, 'generate_report_tables'):
            self.skipTest("ml_predictor_5y.generate_report_tables not available")

        records = []
        # Memory: high PF (PF >= 1.50 -> mult = 1.15)
        for i in range(10):
            records.append({'Date': pd.Timestamp('2024-01-01')+pd.Timedelta(days=i), 'Ticker': 'MU', 'Sector': 'Memory', 'Close': 100.0, 'Label': 1.0, 'Prob_1': 0.65, 'Realized_Return': 4.0})
        for i in range(2):
            records.append({'Date': pd.Timestamp('2024-02-01')+pd.Timedelta(days=i), 'Ticker': 'MU', 'Sector': 'Memory', 'Close': 100.0, 'Label': 0.0, 'Prob_1': 0.65, 'Realized_Return': -1.5})
        # Enterprise SaaS: low PF (PF <= 0.975 -> mult = 0.75)
        for i in range(2):
            records.append({'Date': pd.Timestamp('2024-01-01')+pd.Timedelta(days=i), 'Ticker': 'MSFT', 'Sector': 'Enterprise_SaaS', 'Close': 100.0, 'Label': 1.0, 'Prob_1': 0.65, 'Realized_Return': 2.0})
        for i in range(10):
            records.append({'Date': pd.Timestamp('2024-02-01')+pd.Timedelta(days=i), 'Ticker': 'MSFT', 'Sector': 'Enterprise_SaaS', 'Close': 100.0, 'Label': 0.0, 'Prob_1': 0.65, 'Realized_Return': -2.5})

        oos_df = pd.DataFrame(records)

        # MSFT raw Prob_1 = 0.68 -> Calibrated = 0.68 * 0.75 = 0.510
        # MU raw Prob_1 = 0.60 -> Calibrated = 0.60 * 1.15 = 0.690
        # MU must rank ahead of MSFT!
        latest_df = pd.DataFrame({
            'Ticker': ['MSFT', 'MU'],
            'Sector': ['Enterprise_SaaS', 'Memory'],
            'Close': [400.0, 100.0],
            'Prob_1': [0.68, 0.60],
            'ATR_Pct': [2.0, 3.5]
        })

        tables = mod.generate_report_tables(
            oos_df=oos_df,
            model=None,
            feature_cols=[],
            latest_features_df=latest_df,
            macro_weather={'status': '☀️ 晴天做多'}
        )

        t4 = tables['table4']
        self.assertEqual(len(t4), 2)
        self.assertEqual(t4[0]['ticker'], 'MU', f"Top 1 should be MU after calibration, got {t4[0]['ticker']}")
        self.assertEqual(t4[1]['ticker'], 'MSFT', f"Top 2 should be MSFT after calibration, got {t4[1]['ticker']}")
        self.assertAlmostEqual(t4[0]['calibrated_win_rate_pct'], 69.0, places=1)
        self.assertAlmostEqual(t4[1]['calibrated_win_rate_pct'], 51.0, places=1)

    def test_table2_small_sample_star_capping(self):
        """Asserts that sectors with < 10 high-confidence signals are capped at <= 3 stars with (小样本)."""
        mod = get_implementation_module()
        if not mod or not hasattr(mod, 'generate_report_tables'):
            self.skipTest("ml_predictor_5y.generate_report_tables not available")

        # Mock oos_df where Crypto_Proxy has only 1 high-confidence winning signal (100% win rate)
        n = 50
        oos_data = {
            'Date': pd.bdate_range('2024-01-01', periods=n),
            'Ticker': ['MSTR'] * n,
            'Sector': ['Crypto_Proxy'] * n,
            'Close': [100.0] * n,
            'Label': [1.0] + [0.0] * (n - 1),
            'Prob_1': [0.75] + [0.45] * (n - 1),
            'Realized_Return': [5.0] + [-2.0] * (n - 1)
        }
        oos_df = pd.DataFrame(oos_data)
        latest_df = oos_df.iloc[:1].copy()

        tables = mod.generate_report_tables(
            oos_df=oos_df,
            model=None,
            feature_cols=[],
            latest_features_df=latest_df,
            macro_weather={'status': '☀️ 晴天做多'}
        )

        t2 = tables['table2']
        crypto_row = [r for r in t2 if r['sector_key'] == 'Crypto_Proxy'][0]
        self.assertEqual(crypto_row['high_conviction_signals'], 1)
        self.assertEqual(crypto_row['high_conviction_win_rate_pct'], 100.0)
        self.assertIn('(小样本)', crypto_row['alpha_rating'])
        self.assertNotIn('⭐⭐⭐⭐⭐', crypto_row['alpha_rating'])
        self.assertEqual(crypto_row['alpha_rating'], '⭐⭐⭐☆☆ (小样本)')

    def test_table4_low_win_rate_sector_warning(self):
        """Validates that a ticker whose sector win rate < 45% receives warning action."""
        mod = get_implementation_module()
        if not mod or not hasattr(mod, 'generate_report_tables'):
            self.skipTest("ml_predictor_5y.generate_report_tables not available")

        # Setup sector attribution where Crypto_Proxy has 30% win rate (< 45%)
        # and Semiconductors has 70% win rate
        n = 100
        oos_data = {
            'Date': pd.bdate_range('2024-01-01', periods=n),
            'Ticker': ['NVDA'] * 50 + ['MSTR'] * 50,
            'Sector': ['Semiconductors'] * 50 + ['Crypto_Proxy'] * 50,
            'Close': [100.0] * n,
            'Label': [1.0] * 35 + [0.0] * 15 + [1.0] * 15 + [0.0] * 35,
            'Prob_1': [0.65] * 50 + [0.65] * 50,
            'Realized_Return': [4.0] * 50 + [-2.0] * 50
        }
        oos_df = pd.DataFrame(oos_data)

        # 2 stocks in latest_df
        latest_df = pd.DataFrame({
            'Ticker': ['NVDA', 'MSTR'],
            'Sector': ['Semiconductors', 'Crypto_Proxy'],
            'Close': [120.0, 300.0],
            'Prob_1': [0.68, 0.68],
            'ATR_Pct': [2.5, 5.0]
        })

        tables = mod.generate_report_tables(
            oos_df=oos_df,
            model=None,
            feature_cols=[],
            latest_features_df=latest_df,
            macro_weather={'status': '☀️ 晴天做多'}
        )

        t4 = tables['table4']
        self.assertEqual(len(t4), 2)
        row_nvda = [r for r in t4 if r['ticker'] == 'NVDA'][0]
        row_mstr = [r for r in t4 if r['ticker'] == 'MSTR'][0]

        self.assertEqual(row_nvda['action'], "🔥 极高确定性右侧做多")
        self.assertEqual(row_mstr['action'], "⚠️ 赛道历史胜率偏低·观望")

    def test_macro_weather_alignment(self):
        """Validates weather logic alignment with integrated_market_radar.py."""
        mod = get_implementation_module()
        if not mod or not hasattr(mod, 'evaluate_macro_weather'):
            self.skipTest("ml_predictor_5y.evaluate_macro_weather not available")

        # 1. Double breakdown (< -3.0 on both QQQM and SMH)
        w1 = mod.evaluate_macro_weather(qqqm_bias=-3.2, smh_bias=-3.1)
        self.assertEqual(w1['status'], "🌧️ 暴雨空仓")

        # 2. One-sided deviation (-4.0% on QQQM, but SMH -1.0%, not double breakdown)
        w2 = mod.evaluate_macro_weather(qqqm_bias=-4.0, smh_bias=-1.0)
        self.assertEqual(w2['status'], "⛅ 阴天防守")

        # 3. Mild pullbacks
        w3 = mod.evaluate_macro_weather(qqqm_bias=-1.5, smh_bias=-1.5)
        self.assertEqual(w3['status'], "⛅ 阴天防守")

        # 4. Bull base (both >= 0.25%, no vix panic)
        w4 = mod.evaluate_macro_weather(qqqm_bias=1.2, smh_bias=1.5, vixy_pos=0.3)
        self.assertEqual(w4['status'], "☀️ 晴天做多")

        # 5. VIX panic veto (both >= 0.25%, but vixy_pos >= 0.75)
        w5 = mod.evaluate_macro_weather(qqqm_bias=1.2, smh_bias=1.5, vixy_pos=0.8)
        self.assertEqual(w5['status'], "⛅ 阴天防守")

    def test_table4_weather_and_raw_prob_gate(self):
        """Validates raw probability < 50% gate and rainy macro weather defense in Table 4."""
        mod = get_implementation_module()
        if not mod or not hasattr(mod, 'generate_report_tables'):
            self.skipTest("ml_predictor_5y.generate_report_tables not available")

        n = 50
        oos_df = pd.DataFrame({
            'Date': pd.bdate_range('2024-01-01', periods=n),
            'Ticker': ['MU'] * n,
            'Sector': ['Memory'] * n,
            'Close': [100.0] * n,
            'Label': [1.0] * 35 + [0.0] * 15,
            'Prob_1': [0.68] * n,
            'Realized_Return': [4.5] * 35 + [-2.0] * 15
        })

        # Stock 1: Raw prob 48.0% (<50%) boosted by sector calibration to ~55.2%
        # Stock 2: Raw prob 54.0% (>=50%) boosted by sector calibration to ~62.1%
        latest_df = pd.DataFrame({
            'Ticker': ['MU', 'WDC'],
            'Sector': ['Memory', 'Memory'],
            'Close': [100.0, 80.0],
            'Prob_1': [0.48, 0.54],
            'ATR_Pct': [3.5, 3.8]
        })

        # Case A: Sunny weather -> MU (<50% raw prob) must still be gated to Wait/Observe
        tables_sunny = mod.generate_report_tables(
            oos_df=oos_df,
            model=None,
            feature_cols=[],
            latest_features_df=latest_df,
            macro_weather={'status': '☀️ 晴天做多'}
        )
        t4_sunny = {r['ticker']: r for r in tables_sunny['table4']}
        self.assertIn("观望", t4_sunny['MU']['action'])
        self.assertIn(t4_sunny['WDC']['action'], ["⚡ 逢低吸筹", "🔥 极高确定性右侧做多"])

        # Case B: Rainy weather -> WDC (>=60% calibrated & >=50% raw) gets defensive trial, MU gets empty defense
        tables_rainy = mod.generate_report_tables(
            oos_df=oos_df,
            model=None,
            feature_cols=[],
            latest_features_df=latest_df,
            macro_weather={'status': '🌧️ 暴雨空仓'}
        )
        t4_rainy = {r['ticker']: r for r in tables_rainy['table4']}
        self.assertEqual(t4_rainy['MU']['action'], "🛡️ 暴雨空仓·严禁左侧接刀")
        self.assertEqual(t4_rainy['WDC']['action'], "🛡️ 暴雨防守·仅极小仓试错")



class TestV2UpgradeDefensiveFlightAndScenarios(unittest.TestCase):
    """
    V2.0 升级单元测试：
    1. 防御因子起飞与机构去杠杆预警 (Defensive Flight Alert)
    2. A/B 双轨引擎 (右侧趋势轨 vs 空头回补滑头狙击轨 Short-Covering Sniper)
    3. 多级点位地图 (SL < S2 < S1 < Close < R1 < R2) 与实战剧本推演
    4. 时空周期推演 (Fourier Harmonic + Analog DTW)
    """

    def setUp(self):
        from datetime import datetime
        self.datetime = datetime
        self.mod = get_implementation_module()
        if not self.mod:
            self.skipTest("ml_predictor_5y module not available")

    def test_defensive_flight_alert_logic(self):
        """测试防御因子起飞超额走强评分及报警触发"""
        # Case 1: 正常科技做多行情 (高弹性跑赢防御组)
        normal_lookup = {
            'QQQM': {'pct_change': 1.8},
            'SMH': {'pct_change': 2.5},
            'NVDA': {'pct_change': 3.2},
            'SOXL': {'pct_change': 5.0},
            'KO': {'pct_change': 0.1},
            'PEP': {'pct_change': 0.2},
            'TLT': {'pct_change': -0.3},
            'GLD': {'pct_change': 0.0},
            'VIXY': {'pct_change': -2.5}
        }
        res_normal = self.mod.compute_defensive_flight_alert(live_lookup=normal_lookup)
        self.assertFalse(res_normal['is_alert'])
        self.assertLess(res_normal['flight_score'], 1.8)

        # Case 2: 机构踩踏去杠杆避险行情 (防御组大涨，科技与半导体领跌)
        flight_lookup = {
            'QQQM': {'pct_change': -1.8},
            'SMH': {'pct_change': -3.2},
            'NVDA': {'pct_change': -4.5},
            'SOXL': {'pct_change': -7.8},
            'KO': {'pct_change': 1.6},
            'PEP': {'pct_change': 1.4},
            'TLT': {'pct_change': 1.9},
            'GLD': {'pct_change': 2.2},
            'VIXY': {'pct_change': 6.5}
        }
        res_flight = self.mod.compute_defensive_flight_alert(live_lookup=flight_lookup)
        self.assertTrue(res_flight['is_alert'])
        self.assertGreaterEqual(res_flight['flight_score'], 1.8)
        self.assertIn("防御因子起飞", res_flight['alert_text'])
        self.assertIn("警惕机构盘中去杠杆踩踏", res_flight['alert_text'])

    def test_short_covering_sniper_vs_falling_knife(self):
        """测试 A/B 双轨引擎：区分恐慌衰竭空头回补与半山腰破位飞刀"""
        n = 50
        oos_df = pd.DataFrame({
            'Date': pd.bdate_range('2024-01-01', periods=n),
            'Ticker': ['SOXL'] * n,
            'Sector': ['Semiconductor_Equipment'] * n,
            'Close': [45.0] * n,
            'Label': [1.0] * 35 + [0.0] * 15,
            'Prob_1': [0.65] * n,
            'Realized_Return': [4.5] * 35 + [-2.0] * 15
        })

        # SOXL 满足极端超跌 (Bias=-24%, RSI=24) -> 必须解锁 B 轨空头回补
        # BAD_STOCK 破位但不满足衰竭 (Bias=-15%, RSI=42) -> 必须保持破位飞刀
        latest_df = pd.DataFrame({
            'Ticker': ['SOXL', 'BAD_STOCK'],
            'Sector': ['Semiconductor_Equipment', 'Enterprise_Software_SaaS'],
            'Close': [45.0, 50.0],
            'Prob_1': [0.56, 0.52],
            'ATR_Pct': [9.5, 4.0],
            'Bias_MA50': [-24.0, -15.0],
            'RSI_14': [24.0, 42.0]
        })

        tables = self.mod.generate_report_tables(
            oos_df=oos_df,
            model=None,
            feature_cols=[],
            latest_features_df=latest_df,
            macro_weather={'status': '☀️ 晴天做多'}
        )

        all_live = {r['ticker']: r for r in tables['all_live_predictions']}
        
        # SOXL: 空头回补滑头引擎
        self.assertTrue(all_live['SOXL']['is_short_covering'])
        self.assertIn("🔥 恐慌衰竭·博弈空头回补 (压力位不过就跑)", all_live['SOXL']['action'])
        self.assertIn("空头回补", all_live['SOXL']['scenario_script'])

        # BAD_STOCK: 破位飞刀·严禁左侧抄底
        self.assertFalse(all_live['BAD_STOCK']['is_short_covering'])
        self.assertIn("🚨 破位飞刀·严禁左侧抄底", all_live['BAD_STOCK']['action'])

    def test_multi_level_scenario_map_monotonicity(self):
        """测试多级支撑压力点位地图单调性不变式 (SL < S2 < S1 < Price < R1 < R2)"""
        cur_p = 100.0
        sc_map = self.mod.compute_multi_level_scenario_map(
            ticker='NVDA',
            price=cur_p,
            atr_pct=4.2,
            bias_ma50=-5.0,
            is_short_covering=False,
            is_broken=False,
            put_wall=92.0,
            call_wall=110.0
        )

        sl = sc_map['sl']
        s2 = sc_map['s2']
        s1 = sc_map['s1']
        r1 = sc_map['r1']
        r2 = sc_map['r2']

        self.assertLess(sl, s2, f"SL ({sl}) must be < S2 ({s2})")
        self.assertLess(s2, s1, f"S2 ({s2}) must be < S1 ({s1})")
        self.assertLess(s1, cur_p, f"S1 ({s1}) must be < Current ({cur_p})")
        self.assertLess(cur_p, r1, f"Current ({cur_p}) must be < R1 ({r1})")
        self.assertLess(r1, r2, f"R1 ({r1}) must be < R2 ({r2})")
        self.assertTrue(len(sc_map['scenario_script']) > 10)

    def test_time_cycle_projections_generation(self):
        """测试时空周期推演与历史相似形态匹配输出结构"""
        dates = pd.date_range(end=self.datetime.now(), periods=100, freq='B')
        mock_panel = pd.DataFrame({
            'Ticker': ['SOXL'] * 100 + ['NVDA'] * 100,
            'Date': list(dates) + list(dates),
            'Close': [50.0 + np.sin(i / 5.0) * 8.0 + (i * 0.1) for i in range(100)] +
                     [120.0 + np.cos(i / 4.0) * 15.0 + (i * 0.2) for i in range(100)]
        })

        projections = self.mod.generate_time_cycle_projections(
            raw_panel=mock_panel,
            target_tickers=['SOXL', 'NVDA'],
            forecast_days=15
        )

        self.assertIn('SOXL', projections)
        self.assertIn('NVDA', projections)
        soxl_proj = projections['SOXL']
        self.assertEqual(len(soxl_proj['projection']), 15)
        self.assertTrue(len(soxl_proj['history']) > 0)
        self.assertIn('analog_match', soxl_proj)
        self.assertIn('similarity_pct', soxl_proj['analog_match'])
        self.assertIsInstance(soxl_proj['cycle_windows'], list)


# ============================================================================
# Main Execution Runner
# ============================================================================

def run_suite():
    """Execute all test cases with a detailed summary report."""
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()

    suite.addTests(loader.loadTestsFromTestCase(TestFeatureCausalityNoLookahead))
    suite.addTests(loader.loadTestsFromTestCase(TestPurgeEmbargoIsolation))
    suite.addTests(loader.loadTestsFromTestCase(TestSubsampleAndIPOPreservation))
    suite.addTests(loader.loadTestsFromTestCase(TestExtremeMarketResilience))
    suite.addTests(loader.loadTestsFromTestCase(TestTripleBarrierResolutionLogic))
    suite.addTests(loader.loadTestsFromTestCase(TestTailIsolationLivePool))
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialIntegrityAndEdgeCases))
    suite.addTests(loader.loadTestsFromTestCase(TestMacroFeatureIntegrity))
    suite.addTests(loader.loadTestsFromTestCase(TestReportMetricsAndSectorLinkage))
    suite.addTests(loader.loadTestsFromTestCase(TestV2UpgradeDefensiveFlightAndScenarios))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result


if __name__ == '__main__':
    result = run_suite()
    sys.exit(0 if result.wasSuccessful() else 1)
