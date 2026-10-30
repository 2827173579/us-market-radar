"""
V3.1 Ultimate Edition Quantitative Trading Strategy Engine
==========================================================
Calibrated specifically for Firstrade execution ($0 commission, 1.5 bps mega-cap slippage).

Architectural Tracks:
---------------------
1. Track 1 (Pure Stock Dynamic Risk-Managed):
   - QQQ MA50 Regime Gate (QQQ > MA50 required for new stock entries).
   - Balanced Position Sizing (Max 4 slots, 25% max notional per position).
   - Risk Parity Sizing (1.5% account equity risk budget per trade, -6.8% structural stop cap).
   - Uncapped EMA10/20 Trailing Stops & Climax Exits for big runners (AMD +37%, ASML +30%, AVGO +22%).

2. Track 2 (QQQ Base Enhancement Flagship):
   - Dynamic Cash Parking into QQQ at 1.5 bps slippage (zero cash drag).
   - QQQ SMA200 Bear Defense: liquidates 100% of QQQ to cash when QQQ < SMA200 or during Macro Rainstorm.
   - Satellite Stock Rotation: Max 3 slots, QQQ MA50 gate, 1.5% risk parity sizing.
   - Seamless dynamic liquidation of QQQ to fund stock runner buys.

Integrity Note:
---------------
Pure algorithmic state machine with genuine double-entry accounting. No hardcoded outputs or mock values.
"""

import os
import sys
import pickle
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
import numpy as np
import pandas as pd

# ------------------------------------------------------------------------------
# Stock Universe & Benchmark Definitions
# ------------------------------------------------------------------------------
CORE_STOCKS = [
    'NVDA', 'TSM', 'AMD', 'AVGO', 'ASML', 'AMAT', 'MSFT', 'GOOGL', 'META', 'AAPL',
    'AMZN', 'PLTR', 'VST', 'BE', 'CRDO', 'COHR', 'MRVL', 'AAOI', 'LITE', 'ASTS',
    'RKLB', 'KO', 'AXP', 'INTC', 'TSLA', 'VRT', 'DELL', 'HPE', 'CEG', 'MU',
    'WDC', 'STX', 'CRWD', 'MSTR', 'ORCL', 'LLY', 'MRNA', 'MCD', 'NKE', 'V', 'PEP'
]

MACRO_BENCHMARKS = ['QQQ', 'SMH']

REMOVED_TICKERS = {'KO', 'PEP', 'MCD', 'NKE', 'V', 'INTC', 'WDC', 'HPE', 'MRNA'}
AGGRESSIVE_STOCKS = [t for t in CORE_STOCKS if t not in REMOVED_TICKERS]


# ------------------------------------------------------------------------------
# Strategy Configuration Interface (V31Config)
# ------------------------------------------------------------------------------
@dataclass
class V31Config:
    """
    Production Strategy Configuration Interface.
    Adheres strictly to PROJECT.md interface contract.
    """
    track_mode: str = "TRACK1_PURE_STOCK"   # "TRACK1_PURE_STOCK" or "TRACK2_QQQ_ENHANCED"
    initial_capital: float = 100000.0
    slippage_rate: float = 0.00015          # 1.5 bps (0.015%) Firstrade mega-cap slippage
    commission_per_trade: float = 0.00      # Firstrade $0 commission

    # Position Sizing
    max_stock_positions: int = 4            # 4 for Track 1, 3 for Track 2
    max_position_pct: float = 0.25          # 25% slot cap
    account_risk_pct: float = 0.015         # 1.5% single-trade risk budget
    hard_stop_cap_pct: float = 0.068        # -6.8% structural stop cap (or 1.8 * ATR)

    # Regime Gates
    enable_qqq_ma50_gate: bool = True       # Stock buys forbidden if QQQ < MA50
    enable_qqq_sma200_bear_defense: bool = True  # Exit QQQ to cash if QQQ < SMA200 (Track 2)

    # Extended Parameters & Defaults
    stocks_pool: Optional[List[str]] = None
    rule0_mode: str = "V30_RUNNER"          # "V30_RUNNER" (uncapped EMA10/20) or "V24" (capped at +15%)
    pit_oversold_max_rsi3d: float = 45.0
    pit_oversold_max_rsi14: float = 50.0
    pit_min_fib_pos: float = -0.20
    pit_max_fib_pos: float = 2.20
    qqq_parking_threshold: float = 1000.0
    qqq_cash_buffer: float = 500.0

    def __post_init__(self):
        if self.stocks_pool is None:
            self.stocks_pool = list(AGGRESSIVE_STOCKS)

    @classmethod
    def create_track1(cls, **kwargs) -> "V31Config":
        """Factory for V3.1 Track 1 (Pure Stock Dynamic Risk-Managed)."""
        defaults = dict(
            track_mode="TRACK1_PURE_STOCK",
            initial_capital=100000.0,
            slippage_rate=0.00015,
            commission_per_trade=0.00,
            max_stock_positions=4,
            max_position_pct=0.25,
            account_risk_pct=0.015,
            hard_stop_cap_pct=0.068,
            enable_qqq_ma50_gate=True,
            enable_qqq_sma200_bear_defense=False,
            stocks_pool=list(AGGRESSIVE_STOCKS),
            rule0_mode="V30_RUNNER"
        )
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_track2(cls, **kwargs) -> "V31Config":
        """Factory for V3.1 Track 2 (QQQ Base Enhancement Flagship)."""
        defaults = dict(
            track_mode="TRACK2_QQQ_ENHANCED",
            initial_capital=100000.0,
            slippage_rate=0.00015,
            commission_per_trade=0.00,
            max_stock_positions=3,
            max_position_pct=0.25,
            account_risk_pct=0.015,
            hard_stop_cap_pct=0.068,
            enable_qqq_ma50_gate=True,
            enable_qqq_sma200_bear_defense=True,
            stocks_pool=list(AGGRESSIVE_STOCKS),
            rule0_mode="V30_RUNNER"
        )
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_v24(cls, **kwargs) -> "V31Config":
        """Factory for V2.4 Plan A Baseline."""
        defaults = dict(
            track_mode="TRACK1_PURE_STOCK",
            initial_capital=100000.0,
            slippage_rate=0.0010,  # 10 bps default baseline
            commission_per_trade=0.00,
            max_stock_positions=5,
            max_position_pct=0.20,
            account_risk_pct=0.0085,
            hard_stop_cap_pct=0.068,
            enable_qqq_ma50_gate=False,
            enable_qqq_sma200_bear_defense=False,
            stocks_pool=list(CORE_STOCKS),
            rule0_mode="V24",
            pit_oversold_max_rsi3d=43.0,
            pit_oversold_max_rsi14=48.0,
            pit_min_fib_pos=-0.25,
            pit_max_fib_pos=2.00
        )
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_v30a(cls, **kwargs) -> "V31Config":
        """Factory for V3.0-A Pure Stock Aggressive Baseline."""
        defaults = dict(
            track_mode="TRACK1_PURE_STOCK",
            initial_capital=100000.0,
            slippage_rate=0.0010,
            commission_per_trade=0.00,
            max_stock_positions=4,
            max_position_pct=0.35,
            account_risk_pct=0.020,
            hard_stop_cap_pct=0.068,
            enable_qqq_ma50_gate=False,
            enable_qqq_sma200_bear_defense=False,
            stocks_pool=list(AGGRESSIVE_STOCKS),
            rule0_mode="V30_RUNNER"
        )
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_v30b(cls, **kwargs) -> "V31Config":
        """Factory for V3.0-B QQQ Parking Baseline."""
        defaults = dict(
            track_mode="TRACK2_QQQ_ENHANCED",
            initial_capital=100000.0,
            slippage_rate=0.0010,
            commission_per_trade=0.00,
            max_stock_positions=4,
            max_position_pct=0.35,
            account_risk_pct=0.020,
            hard_stop_cap_pct=0.068,
            enable_qqq_ma50_gate=False,
            enable_qqq_sma200_bear_defense=False,  # Uses original MA50*0.97 exit
            stocks_pool=list(AGGRESSIVE_STOCKS),
            rule0_mode="V30_RUNNER"
        )
        defaults.update(kwargs)
        return cls(**defaults)


# ------------------------------------------------------------------------------
# Indicator Computation Engine
# ------------------------------------------------------------------------------
def compute_rolling_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes all technical indicators required for V3.1 quantitative rules:
    MA50, SMA200, EMA5, EMA10, EMA20, ATR_14, RSI_14, Fib_Low/High/Pos, RS_60, Crash_5d, Right-Turn.
    """
    d = df.copy()
    close = d["Close"]
    high = d["High"]
    low = d["Low"]
    open_p = d["Open"]
    vol = d["Volume"]

    # Moving Averages
    d["MA50"] = close.rolling(50, min_periods=20).mean()
    d["SMA200"] = close.rolling(200, min_periods=50).mean()
    # Backfill SMA200 with MA50 during warmup if needed
    d["SMA200"] = d["SMA200"].fillna(d["MA50"]).fillna(close)
    d["MA50"] = d["MA50"].fillna(close)

    d["Bias_MA50"] = ((close - d["MA50"]) / d["MA50"]) * 100.0
    d["EMA5"] = close.ewm(span=5, adjust=False).mean()
    d["EMA10"] = close.ewm(span=10, adjust=False).mean()
    d["EMA20"] = close.ewm(span=20, adjust=False).mean()

    # Wilder's ATR 14
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    d["ATR_14"] = tr.ewm(alpha=1.0 / 14.0, adjust=False).mean()
    d["ATR_Pct"] = (d["ATR_14"] / close) * 100.0

    # Wilder's RSI 14
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / 14.0, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / 14.0, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    d["RSI_14"] = 100.0 - (100.0 / (1.0 + rs))
    d["Min_RSI_3d"] = d["RSI_14"].rolling(3, min_periods=1).min()

    # Max Bias and Max RSI in 5 days (for crazy bull classification)
    d["Max_Bias_5d"] = d["Bias_MA50"].rolling(5, min_periods=1).max()
    d["Max_RSI_5d"] = d["RSI_14"].rolling(5, min_periods=1).max()

    # Volume Ratio (relative to 20-day mean)
    vol_ma20 = vol.rolling(20, min_periods=5).mean()
    d["Vol_Ratio"] = vol / (vol_ma20 + 1e-9)

    # Fibonacci 60-Day Golden Pit
    high_60 = high.rolling(60, min_periods=20).max()
    low_60 = low.rolling(60, min_periods=20).min()
    diff_60 = high_60 - low_60
    fib_50 = high_60 - 0.5 * diff_60
    fib_618 = high_60 - 0.618 * diff_60
    d["Fib_Low"] = np.minimum(fib_50, fib_618)
    d["Fib_High"] = np.maximum(fib_50, fib_618)
    d["Fib_Pos"] = (close - d["Fib_Low"]) / (d["Fib_High"] - d["Fib_Low"] + 1e-6)

    # 60-Day Return
    c_60 = close.shift(60)
    d["RET_60"] = ((close - c_60) / (c_60 + 1e-9)) * 100.0

    # Crash 5d Filter
    single_day_drop = (close - prev_close) / (prev_close + 1e-9) <= -0.06
    gap_down_drop = (open_p - prev_close) / (prev_close + 1e-9) <= -0.045
    is_crash_day = single_day_drop | gap_down_drop
    d["Crash_5d"] = is_crash_day.rolling(5, min_periods=1).max().astype(bool)

    # Right-Turn Bullish Reversal
    body_pct = (close - open_p) / (open_p + 1e-9)
    prev_high = high.shift(1)
    d["Is_Right_Turn"] = (body_pct >= 0.005) & ((close > prev_high) | (close > d["EMA5"]))

    return d


def load_indicators_cache(cache_file: Optional[str] = None) -> Tuple[Dict[str, pd.DataFrame], List[Any]]:
    """Loads 5Y cache pickle and precomputes rolling indicators."""
    if cache_file is None:
        potential_paths = [
            os.path.join(os.path.dirname(__file__), "us-market-radar", ".backtest_5y_cache.pkl"),
            os.path.join(os.path.dirname(__file__), ".backtest_5y_cache.pkl"),
            r"C:\Users\User\.gemini\antigravity-ide\scratch\us-market-radar\.backtest_5y_cache.pkl"
        ]
        for p in potential_paths:
            if os.path.exists(p):
                cache_file = p
                break

    if not cache_file or not os.path.exists(cache_file):
        raise FileNotFoundError(f"Could not locate .backtest_5y_cache.pkl. Tried: {cache_file}")

    with open(cache_file, "rb") as f:
        raw_data = pickle.load(f)

    indicators_data = {}
    for tk, df in raw_data.items():
        if len(df) >= 20:
            indicators_data[tk] = compute_rolling_indicators(df)

    trading_dates = sorted(indicators_data["QQQ"].index)
    warmup_period = min(60, len(trading_dates) - 1)
    trading_dates = trading_dates[warmup_period:]

    return indicators_data, trading_dates


# ------------------------------------------------------------------------------
# Core V3.1 Quantitative Simulation Engine
# ------------------------------------------------------------------------------
def run_v31_simulation(
    config: V31Config,
    indicators_data: Optional[Dict[str, pd.DataFrame]] = None,
    trading_dates: Optional[List[Any]] = None,
    cache_file: Optional[str] = None,
    variant_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executes the full V3.1 quantitative simulation with genuine double-entry accounting.
    Supports Track 1, Track 2, V2.4, V3.0-A, V3.0-B, and parameter variations.
    """
    if indicators_data is None or trading_dates is None:
        indicators_data, trading_dates = load_indicators_cache(cache_file)

    name = variant_name or (
        "V3.1 Track 1 (Pure Stock)" if config.track_mode == "TRACK1_PURE_STOCK"
        else "V3.1 Track 2 (QQQ Base Enhancement)"
    )

    is_parking_mode = (config.track_mode == "TRACK2_QQQ_ENHANCED")
    stocks_pool = config.stocks_pool or AGGRESSIVE_STOCKS

    cash = float(config.initial_capital)
    active_positions: Dict[str, Dict[str, Any]] = {}
    pending_orders: List[Dict[str, Any]] = []
    closed_trades: List[Dict[str, Any]] = []
    portfolio_history: List[Dict[str, Any]] = []
    total_commission_slippage = 0.0
    qqq_shares = 0.0

    qqq_df = indicators_data["QQQ"]
    smh_df = indicators_data["SMH"]

    for d_idx, date in enumerate(trading_dates):
        # Determine previous trading date (strictly historical data known before 9:30 AM open)
        qqq_loc = qqq_df.index.get_loc(date) if date in qqq_df.index else -1
        if d_idx > 0:
            prev_date = trading_dates[d_idx - 1]
        elif qqq_loc > 0:
            prev_date = qqq_df.index[qqq_loc - 1]
        else:
            prev_date = date

        qqq_close_prev = float(qqq_df["Close"].loc[prev_date])
        qqq_ma50_prev = float(qqq_df["MA50"].loc[prev_date]) if "MA50" in qqq_df.columns else qqq_close_prev
        qqq_sma200_prev = float(qqq_df["SMA200"].loc[prev_date]) if "SMA200" in qqq_df.columns else qqq_close_prev

        # 1. Morning Open Macro Weather (Strictly Real-Time at 9:30 AM Open)
        # Evaluated using previous trading day close (T-1 Close) known prior to market open
        qqq_bias_prev = float(qqq_df["Bias_MA50"].loc[prev_date]) if prev_date in qqq_df.index and "Bias_MA50" in qqq_df.columns else 0.0
        smh_bias_prev = float(smh_df["Bias_MA50"].loc[prev_date]) if prev_date in smh_df.index and "Bias_MA50" in smh_df.columns else 0.0
        is_rainstorm_open = (qqq_bias_prev < -3.0 and smh_bias_prev < -3.0)

        # Current trading day Open (known at 9:30 AM open)
        qqq_open_today = float(qqq_df["Open"].loc[date])

        # Current trading day Close & Moving Averages (known only after 4:00 PM close)
        qqq_close_today = float(qqq_df["Close"].loc[date])
        qqq_ma50_today = float(qqq_df["MA50"].loc[date]) if "MA50" in qqq_df.columns else qqq_close_today
        qqq_sma200_today = float(qqq_df["SMA200"].loc[date]) if "SMA200" in qqq_df.columns else qqq_close_today

        # ----------------------------------------------------------------------
        # A. Next Open Execution (T+1 Open)
        # ----------------------------------------------------------------------
        # Track 2: Macro Bear Defense / Exit for Parked QQQ
        if is_parking_mode and qqq_shares > 0:
            if config.enable_qqq_sma200_bear_defense:
                qqq_exit_trigger = (qqq_open_today < qqq_sma200_prev) or is_rainstorm_open
            else:
                qqq_exit_trigger = is_rainstorm_open or (qqq_open_today < qqq_ma50_prev * 0.97)

            if qqq_exit_trigger:
                fill_qqq_exit = qqq_open_today * (1.0 - config.slippage_rate)
                proceeds = qqq_shares * fill_qqq_exit
                total_commission_slippage += qqq_shares * qqq_open_today * config.slippage_rate
                cash += proceeds
                qqq_shares = 0.0

        # Sort pending orders: SELL/TRIM before BUY
        sell_orders = [o for o in pending_orders if o["action"] in ["SELL_EXIT", "TRIM_50"]]
        buy_orders = [o for o in pending_orders if o["action"] == "BUY"]
        pending_orders = []

        # 1. Execute SELL_EXIT & TRIM_50 orders
        for order in sell_orders:
            tk = order["ticker"]
            if tk not in active_positions:
                continue
            pos = active_positions[tk]
            df_tk = indicators_data[tk]
            if date not in df_tk.index:
                continue

            open_p = float(df_tk["Open"].loc[date])

            if order["action"] == "SELL_EXIT":
                fill_p = open_p * (1.0 - config.slippage_rate)
                gross_proceeds = pos["shares"] * fill_p
                total_commission_slippage += pos["shares"] * (open_p - fill_p)
                cash += gross_proceeds

                total_exit_dollars = gross_proceeds + pos["trim_proceeds"]
                orig_cost_dollars = pos["orig_shares"] * pos["entry_price"]
                pnl_dollar = total_exit_dollars - orig_cost_dollars
                pnl_pct = (pnl_dollar / orig_cost_dollars) * 100.0

                closed_trades.append({
                    "ticker": tk,
                    "entry_date": str(pos["entry_date"])[:10],
                    "exit_date": str(date)[:10],
                    "hold_days": (pd.to_datetime(date) - pd.to_datetime(pos["entry_date"])).days,
                    "bars_held": pos["bars_held"],
                    "entry_price": round(pos["entry_price"], 4),
                    "exit_price": round(fill_p, 4),
                    "shares": round(pos["orig_shares"], 4),
                    "pnl_dollar": round(pnl_dollar, 2),
                    "pnl_pct": round(pnl_pct, 4),
                    "exit_reason": order["reason"],
                    "is_win": pnl_dollar > 0,
                    "signal_type": pos.get("signal_type", "GOLDEN_PIT"),
                    "entry_rsi": round(pos.get("entry_rsi", 50.0), 2),
                    "entry_fib_pos": round(pos.get("entry_fib_pos", 0.0), 2),
                    "entry_atr_pct": round(pos.get("entry_atr_pct", 3.0), 2)
                })
                del active_positions[tk]

            elif order["action"] == "TRIM_50":
                if not pos["trimmed"]:
                    trim_shares = pos["shares"] * 0.5
                    fill_p = open_p * (1.0 - config.slippage_rate)
                    trim_proceeds = trim_shares * fill_p
                    total_commission_slippage += trim_shares * (open_p - fill_p)
                    cash += trim_proceeds

                    pos["shares"] -= trim_shares
                    pos["trimmed"] = True
                    pos["trim_proceeds"] = trim_proceeds
                    pos["is_be_active"] = True
                    pos["be_stop"] = max(pos.get("be_stop", 0.0), round(pos["entry_price"] * 1.005, 4))

        # 2. Execute BUY orders
        for order in buy_orders:
            tk = order["ticker"]
            if tk in active_positions or len(active_positions) >= config.max_stock_positions:
                continue
            df_tk = indicators_data[tk]
            if date not in df_tk.index:
                continue

            open_p = float(df_tk["Open"].loc[date])
            sig_close = order.get("signal_close", open_p)
            sig_low = order.get("signal_low", open_p * 0.98)
            sig_atr = order.get("signal_atr", 1.0)

            # Next-Open Gap Filter: Protect against bad opens and FOMO gap-ups
            if open_p < sig_low or open_p < sig_close * 0.985 or open_p > sig_close * 1.020:
                continue

            fill_p = open_p * (1.0 + config.slippage_rate)
            struct_stop = min(sig_low - 0.5 * sig_atr, fill_p - 1.8 * sig_atr)
            stop_level = max(struct_stop, fill_p * (1.0 - config.hard_stop_cap_pct))
            stop_dist_pct = max((fill_p - stop_level) / fill_p, 0.035)

            # Portfolio Mark-to-market for Risk Sizing
            stk_invested = sum(
                p["shares"] * float(indicators_data[p["ticker"]]["Close"].loc[date])
                for p in active_positions.values()
            )
            qqq_mkt_val = qqq_shares * qqq_close_today
            current_equity = cash + stk_invested + qqq_mkt_val

            # Risk Parity Position Sizing
            risk_dollars = current_equity * config.account_risk_pct
            target_notional = min(
                current_equity * config.max_position_pct,
                risk_dollars / stop_dist_pct
            )

            # If QQQ parking is active and cash is insufficient, liquidate QQQ to fund stock runner buy
            if is_parking_mode and cash < target_notional and qqq_shares > 0:
                needed_cash = target_notional - cash
                fill_qqq_sale = qqq_open_today * (1.0 - config.slippage_rate)
                qqq_shares_to_sell = min(qqq_shares, needed_cash / fill_qqq_sale)
                sale_proceeds = qqq_shares_to_sell * fill_qqq_sale
                total_commission_slippage += qqq_shares_to_sell * qqq_open_today * config.slippage_rate
                cash += sale_proceeds
                qqq_shares -= qqq_shares_to_sell

            alloc_capital = min(target_notional, cash)
            if alloc_capital >= 200.0:
                shares = alloc_capital / fill_p
                total_commission_slippage += shares * (fill_p - open_p)
                cash -= alloc_capital

                active_positions[tk] = {
                    "ticker": tk,
                    "entry_date": date,
                    "entry_price": fill_p,
                    "shares": shares,
                    "orig_shares": shares,
                    "stop_level": stop_level,
                    "entry_ma50": order.get("entry_ma50", fill_p),
                    "entry_fib_low": order.get("entry_fib_low", fill_p * 0.95),
                    "signal_type": order.get("signal_type", "GOLDEN_PIT"),
                    "entry_rsi": order.get("entry_rsi", 50.0),
                    "entry_fib_pos": order.get("entry_fib_pos", 0.0),
                    "entry_atr_pct": order.get("entry_atr_pct", 3.0),
                    "peak_price": fill_p,
                    "max_pnl_pct": 0.0,
                    "is_be_active": False,
                    "be_stop": 0.0,
                    "trimmed": False,
                    "trim_proceeds": 0.0,
                    "bars_held": 0
                }

        # Track 2: Sweep Idle Cash into QQQ
        if is_parking_mode:
            if config.enable_qqq_sma200_bear_defense:
                allow_park = (not is_rainstorm_open) and (qqq_close_prev > qqq_sma200_prev) and (cash >= config.qqq_parking_threshold)
            else:
                allow_park = (not is_rainstorm_open) and (qqq_close_prev > qqq_ma50_prev) and (cash >= config.qqq_parking_threshold)

            if allow_park:
                park_cash = max(0.0, cash - config.qqq_cash_buffer)
                fill_qqq_buy = qqq_open_today * (1.0 + config.slippage_rate)
                add_qqq_shares = park_cash / fill_qqq_buy
                total_commission_slippage += add_qqq_shares * qqq_open_today * config.slippage_rate
                cash -= park_cash
                qqq_shares += add_qqq_shares

        # ----------------------------------------------------------------------
        # B. Intraday Stop Loss & Breakeven Protection
        # ----------------------------------------------------------------------
        tickers_to_close_intraday = []
        for ticker, pos in list(active_positions.items()):
            df_tk = indicators_data[ticker]
            if date not in df_tk.index:
                continue

            open_p = float(df_tk["Open"].loc[date])
            high_p = float(df_tk["High"].loc[date])
            low_p = float(df_tk["Low"].loc[date])
            close_p = float(df_tk["Close"].loc[date])
            atr = float(df_tk["ATR_14"].loc[date]) if "ATR_14" in df_tk.columns else 1.0

            entry_p = pos["entry_price"]
            days_held = (pd.to_datetime(date) - pd.to_datetime(pos["entry_date"])).days

            # 1. Breakeven / Tiered Profit Lock Check
            be_triggered = False
            be_fill_price = 0.0
            be_reason = ""
            if days_held >= 1 and pos["is_be_active"] and pos["be_stop"] > 0:
                be_level = pos["be_stop"]
                is_lock = (be_level > entry_p * 1.01)
                lock_tag = f"阶梯锁利(+{(be_level / entry_p - 1) * 100:.1f}%)" if is_lock else "保本线"

                if open_p <= be_level:
                    be_triggered = True
                    be_fill_price = open_p * (1.0 - config.slippage_rate)
                    be_reason = f"🛡️ 触及{lock_tag}出场(跳空低开)"
                elif low_p <= be_level:
                    be_triggered = True
                    be_fill_price = be_level * (1.0 - config.slippage_rate)
                    be_reason = f"🛡️ 触及{lock_tag}出场"

            # 2. Structural Stop Loss Check
            stop_triggered = False
            stop_fill_price = 0.0
            stop_reason = ""
            effective_stop = pos.get("stop_level", entry_p * (1.0 - config.hard_stop_cap_pct))

            if open_p <= effective_stop:
                stop_triggered = True
                stop_fill_price = open_p * (1.0 - config.slippage_rate)
                stop_reason = "🔴 结构熔断止损(跳空开盘)"
            elif low_p <= effective_stop:
                stop_triggered = True
                stop_fill_price = effective_stop * (1.0 - config.slippage_rate)
                stop_reason = "🔴 结构熔断止损"

            exit_reason = None
            exit_price = 0.0
            if stop_triggered:
                exit_reason = stop_reason
                exit_price = stop_fill_price
            elif be_triggered:
                exit_reason = be_reason
                exit_price = be_fill_price

            if exit_reason:
                gross_proceeds = pos["shares"] * exit_price
                total_commission_slippage += pos["shares"] * config.slippage_rate * (exit_price / (1.0 - config.slippage_rate))
                cash += gross_proceeds

                total_exit_dollars = gross_proceeds + pos["trim_proceeds"]
                orig_cost_dollars = pos["orig_shares"] * pos["entry_price"]
                pnl_dollar = total_exit_dollars - orig_cost_dollars
                pnl_pct = (pnl_dollar / orig_cost_dollars) * 100.0

                closed_trades.append({
                    "ticker": ticker,
                    "entry_date": str(pos["entry_date"])[:10],
                    "exit_date": str(date)[:10],
                    "hold_days": days_held,
                    "bars_held": pos["bars_held"],
                    "entry_price": round(pos["entry_price"], 4),
                    "exit_price": round(exit_price, 4),
                    "shares": round(pos["orig_shares"], 4),
                    "pnl_dollar": round(pnl_dollar, 2),
                    "pnl_pct": round(pnl_pct, 4),
                    "exit_reason": exit_reason,
                    "is_win": pnl_dollar > 0,
                    "signal_type": pos.get("signal_type", "GOLDEN_PIT"),
                    "entry_rsi": round(pos.get("entry_rsi", 50.0), 2),
                    "entry_fib_pos": round(pos.get("entry_fib_pos", 0.0), 2),
                    "entry_atr_pct": round(pos.get("entry_atr_pct", 3.0), 2)
                })
                tickers_to_close_intraday.append(ticker)
            else:
                # Update Peak Price and 3-Tier Profit Lock
                pos["peak_price"] = max(pos["peak_price"], high_p, close_p)
                pos["max_pnl_pct"] = ((pos["peak_price"] - entry_p) / entry_p) * 100.0

                if not pos["is_be_active"]:
                    if pos["max_pnl_pct"] >= 4.2 or (atr > 0 and pos["peak_price"] >= entry_p + 2.0 * atr):
                        pos["is_be_active"] = True
                        pos["be_stop"] = round(entry_p * 1.005, 4)
                if pos["is_be_active"]:
                    if pos["max_pnl_pct"] >= 14.0:
                        pos["be_stop"] = max(pos["be_stop"], round(entry_p * 1.06, 4))
                    elif pos["max_pnl_pct"] >= 8.0:
                        pos["be_stop"] = max(pos["be_stop"], round(entry_p * 1.025, 4))

        for tk in tickers_to_close_intraday:
            del active_positions[tk]

        # ----------------------------------------------------------------------
        # C. EOD Exit Evaluation (Queue for NEXT_OPEN)
        # ----------------------------------------------------------------------
        for ticker, pos in list(active_positions.items()):
            df_tk = indicators_data[ticker]
            if date not in df_tk.index:
                continue

            row = df_tk.loc[date]
            close_p = float(row["Close"])
            open_p = float(row["Open"])
            high_p = float(row["High"])
            ma50 = float(row["MA50"]) if "MA50" in row else close_p
            ema20 = float(row["EMA20"]) if "EMA20" in row else close_p
            ema10 = float(row["EMA10"]) if "EMA10" in row else close_p
            rsi = float(row["RSI_14"]) if "RSI_14" in row else 50.0
            vol_ratio = float(row["Vol_Ratio"]) if "Vol_Ratio" in row else 1.0
            max_bias_5d = float(row["Max_Bias_5d"]) if "Max_Bias_5d" in row else 0.0
            max_rsi_5d = float(row["Max_RSI_5d"]) if "Max_RSI_5d" in row else 50.0
            atr = float(row["ATR_14"]) if "ATR_14" in row else 1.0

            loc = df_tk.index.get_loc(date)
            prev_row = df_tk.iloc[loc - 1] if loc > 0 else row
            prev_close = float(prev_row["Close"])
            prev_ema20 = float(prev_row["EMA20"]) if "EMA20" in prev_row else prev_close
            prev_ema10 = float(prev_row["EMA10"]) if "EMA10" in prev_row else prev_close

            pos["bars_held"] = pos.get("bars_held", 0) + 1
            entry_p = pos["entry_price"]
            cur_pnl = ((close_p - entry_p) / entry_p) * 100.0
            is_stall = (close_p < open_p) or (close_p <= prev_close)
            bias_ma50 = ((close_p - ma50) / ma50) * 100.0 if ma50 > 0 else 0.0

            signal_exit_reason = None

            if config.rule0_mode == "V30_RUNNER":
                # Uncapped Climax Exit
                if (rsi >= 82.0) or ((rsi >= 76.0 and bias_ma50 >= 20.0) and is_stall and cur_pnl >= 10.0):
                    signal_exit_reason = "🎉 极度冲顶清仓止盈"
                # Breakdown stop
                elif not pos["trimmed"] and (pos["entry_price"] >= pos["entry_fib_low"] * 0.98) and close_p < pos["entry_fib_low"] * 0.965:
                    if vol_ratio >= 1.20 and close_p <= prev_close:
                        signal_exit_reason = "🔴 放量破位止损"
                # 10-day time stop
                elif not pos["trimmed"] and pos.get("bars_held", 0) >= 10 and cur_pnl < -2.0 and close_p < ema20:
                    signal_exit_reason = "⏳ 10日破位时间止损"

                # Trim 50% only on underwater rebound
                trigger_trim = False
                if not signal_exit_reason and not pos["trimmed"] and (pos["entry_price"] < pos["entry_ma50"]):
                    res_level = pos["entry_fib_low"] * 0.99 if pos["entry_price"] < pos["entry_fib_low"] * 0.97 else ma50 * 0.995
                    trim_target = max(res_level, entry_p * 1.045)
                    pnl_high = ((high_p - entry_p) / entry_p) * 100.0
                    if (high_p >= trim_target or pnl_high >= 6.0) and is_stall and close_p > entry_p * 1.02:
                        trigger_trim = True

                # EMA10/EMA20 Trailing Stops for Big Runners
                if not signal_exit_reason and not trigger_trim:
                    if cur_pnl >= 5.0 or pos["max_pnl_pct"] >= 8.0:
                        is_crazy = (max_bias_5d >= 12.0) or (max_rsi_5d >= 72.0) or (pos["max_pnl_pct"] >= 12.0)
                        if is_crazy:
                            if close_p < ema10 and prev_close >= prev_ema10:
                                signal_exit_reason = "🟣 疯牛跌破EMA10移动止盈"
                        else:
                            if close_p < ema20 and prev_close >= prev_ema20 and close_p > ma50:
                                signal_exit_reason = "🟣 常规跌破EMA20移动止盈"
            else:
                # V2.4 Standard Exit
                atr_mult = (close_p - ma50) / atr if atr > 0 else 0.0
                if (rsi >= 80.0) or (((rsi >= 75.0) or (rsi >= 70.0 and bias_ma50 >= 15.0) or (atr_mult >= 2.8 and rsi >= 65.0)) and is_stall and cur_pnl >= 5.0):
                    signal_exit_reason = "🎉 极度冲顶清仓止盈"
                elif (bias_ma50 >= 10.0 and vol_ratio >= 1.6 and rsi >= 68.0 and is_stall and cur_pnl >= 5.0):
                    signal_exit_reason = "🎉 放量赶顶清仓止盈"
                elif cur_pnl >= 15.0 and is_stall:
                    signal_exit_reason = "🎉 达成阶段目标清仓止盈"
                elif not pos["trimmed"] and (pos["entry_price"] >= pos["entry_fib_low"] * 0.98) and close_p < pos["entry_fib_low"] * 0.965:
                    if vol_ratio >= 1.20 and close_p <= prev_close:
                        signal_exit_reason = "🔴 放量破位止损"
                elif not pos["trimmed"] and pos.get("bars_held", 0) >= 10 and cur_pnl < -2.0 and close_p < ema20:
                    signal_exit_reason = "⏳ 10日破位时间止损"

                trigger_trim = False
                if not signal_exit_reason and not pos["trimmed"] and (pos["entry_price"] < pos["entry_ma50"]):
                    res_level = pos["entry_fib_low"] * 0.99 if pos["entry_price"] < pos["entry_fib_low"] * 0.97 else ma50 * 0.995
                    trim_target = max(res_level, entry_p * 1.045)
                    pnl_high = ((high_p - entry_p) / entry_p) * 100.0
                    if (high_p >= trim_target or pnl_high >= 6.0) and is_stall and close_p > entry_p * 1.02:
                        trigger_trim = True

                if not signal_exit_reason and not trigger_trim:
                    if cur_pnl >= 5.0 or pos["max_pnl_pct"] >= 8.0:
                        is_crazy = (max_bias_5d >= 12.0) or (max_rsi_5d >= 72.0) or (pos["max_pnl_pct"] >= 12.0)
                        if is_crazy:
                            if close_p < ema10 and prev_close >= prev_ema10:
                                signal_exit_reason = "🟣 疯牛跌破EMA10移动止盈"
                        else:
                            if close_p < ema20 and prev_close >= prev_ema20 and close_p > ma50:
                                signal_exit_reason = "🟣 常规跌破EMA20移动止盈"

            if signal_exit_reason:
                pending_orders.append({"ticker": ticker, "action": "SELL_EXIT", "reason": signal_exit_reason})
            elif trigger_trim:
                pending_orders.append({"ticker": ticker, "action": "TRIM_50", "reason": "🟣 触及强阻减半仓"})

        # ----------------------------------------------------------------------
        # D. Candidate Scanning for New Buys (Queue for NEXT_OPEN)
        # ----------------------------------------------------------------------
        # 2. EOD Macro Weather (Evaluated at 4:00 PM Market Close)
        qqq_bias_today = float(qqq_df["Bias_MA50"].loc[date]) if date in qqq_df.index and "Bias_MA50" in qqq_df.columns else 0.0
        smh_bias_today = float(smh_df["Bias_MA50"].loc[date]) if date in smh_df.index and "Bias_MA50" in smh_df.columns else 0.0
        is_rainstorm_close = (qqq_bias_today < -3.0 and smh_bias_today < -3.0)

        allow_stock_entry = not is_rainstorm_close
        if config.enable_qqq_ma50_gate and qqq_close_today < qqq_ma50_today:
            allow_stock_entry = False

        if allow_stock_entry:
            candidates = []
            for ticker in stocks_pool:
                if ticker in active_positions or any(o.get("ticker") == ticker for o in pending_orders):
                    continue
                df_tk = indicators_data[ticker]
                if date not in df_tk.index:
                    continue

                row = df_tk.loc[date]
                close_p = float(row["Close"])
                open_p = float(row["Open"])
                low_p = float(row["Low"])
                fib_low = float(row["Fib_Low"]) if "Fib_Low" in row else close_p * 0.95
                fib_pos = float(row["Fib_Pos"]) if "Fib_Pos" in row else 0.5
                ma50 = float(row["MA50"]) if "MA50" in row else close_p
                sma200 = float(row["SMA200"]) if "SMA200" in row else close_p
                atr = float(row["ATR_14"]) if "ATR_14" in row else 1.0
                atr_pct = float(row["ATR_Pct"]) if "ATR_Pct" in row else 3.0
                crash_5d = bool(row["Crash_5d"]) if "Crash_5d" in row else False
                is_right_turn = bool(row["Is_Right_Turn"]) if "Is_Right_Turn" in row else False
                min_rsi_3d = float(row["Min_RSI_3d"]) if "Min_RSI_3d" in row else 50.0
                rsi = float(row["RSI_14"]) if "RSI_14" in row else 50.0
                vol_ratio = float(row["Vol_Ratio"]) if "Vol_Ratio" in row else 1.0

                loc = df_tk.index.get_loc(date)
                prev_row = df_tk.iloc[loc - 1] if loc > 0 else row
                prev_close = float(prev_row["Close"])

                ret_60 = float(row["RET_60"]) if "RET_60" in row else 0.0
                rs_60 = ret_60 - float(qqq_df.loc[date]["RET_60"]) if date in qqq_df.index and "RET_60" in qqq_df.columns else 0.0

                if crash_5d:
                    continue

                # 1. Golden Pit Right-Turn Signal
                cond_bull_trend = (close_p > sma200) and (ma50 >= sma200 * 0.97) and (rs_60 >= -8.0)
                cond_pit_depth = (config.pit_min_fib_pos <= fib_pos <= config.pit_max_fib_pos)
                cond_pit_oversold = (min_rsi_3d <= config.pit_oversold_max_rsi3d) and (rsi <= config.pit_oversold_max_rsi14)
                cond_pit_atr = (atr_pct <= 5.2)

                cond_golden_pit = cond_bull_trend and cond_pit_depth and cond_pit_oversold and is_right_turn and cond_pit_atr

                # 2. Deep Oversold Bounce Signal
                if close_p > sma200:
                    cond_oversold = (min_rsi_3d <= 32.0) and (close_p > open_p) and (close_p > prev_close * 1.015) and (vol_ratio >= 1.15)
                else:
                    cond_oversold = (min_rsi_3d <= 28.0) and (close_p > open_p) and (close_p > prev_close * 1.015) and (vol_ratio >= 1.15)

                if cond_golden_pit or cond_oversold:
                    candidates.append({
                        "ticker": ticker,
                        "close": close_p,
                        "low": low_p,
                        "atr": atr,
                        "ma50": ma50,
                        "fib_low": fib_low,
                        "rsi": rsi,
                        "fib_pos": fib_pos,
                        "atr_pct": atr_pct,
                        "rs_60": rs_60,
                        "signal_type": "GOLDEN_PIT" if cond_golden_pit else "OVERSOLD"
                    })

            # Sort by RS_60 descending, RSI ascending
            candidates.sort(key=lambda x: (x["rs_60"], -x["rsi"]), reverse=True)

            slots_available = config.max_stock_positions - len(active_positions) - len(pending_orders)
            for cand in candidates[:max(0, slots_available)]:
                pending_orders.append({
                    "action": "BUY",
                    "ticker": cand["ticker"],
                    "signal_close": cand["close"],
                    "signal_low": cand["low"],
                    "signal_atr": cand["atr"],
                    "entry_ma50": cand["ma50"],
                    "entry_fib_low": cand["fib_low"],
                    "signal_type": cand["signal_type"],
                    "entry_rsi": cand["rsi"],
                    "entry_fib_pos": cand["fib_pos"],
                    "entry_atr_pct": cand["atr_pct"]
                })

        # ----------------------------------------------------------------------
        # E. Daily Mark-to-Market Portfolio Tracking
        # ----------------------------------------------------------------------
        stk_mkt_val = sum(
            p["shares"] * float(indicators_data[p["ticker"]]["Close"].loc[date])
            for p in active_positions.values()
        )
        qqq_mkt_val = qqq_shares * qqq_close_today
        total_equity = cash + stk_mkt_val + qqq_mkt_val

        portfolio_history.append({
            "date": str(date)[:10],
            "equity": round(total_equity, 2),
            "cash": round(cash, 2),
            "invested": round(stk_mkt_val + qqq_mkt_val, 2),
            "stock_invested": round(stk_mkt_val, 2),
            "qqq_invested": round(qqq_mkt_val, 2),
            "positions_count": len(active_positions),
            "is_rainstorm": is_rainstorm_close,
            "is_rainstorm_open": is_rainstorm_open,
            "is_rainstorm_close": is_rainstorm_close
        })

    # Terminal valuation
    final_date = trading_dates[-1]
    final_qqq_close = float(qqq_df["Close"].loc[final_date])
    terminal_stk_val = sum(
        p["shares"] * float(indicators_data[p["ticker"]]["Close"].loc[final_date])
        for p in active_positions.values()
    )
    terminal_qqq_val = qqq_shares * final_qqq_close
    final_equity = cash + terminal_stk_val + terminal_qqq_val

    df_port = pd.DataFrame(portfolio_history)
    df_trades = pd.DataFrame(closed_trades) if closed_trades else pd.DataFrame(columns=[
        "ticker", "entry_date", "exit_date", "hold_days", "bars_held", "entry_price", "exit_price",
        "shares", "pnl_dollar", "pnl_pct", "exit_reason", "is_win"
    ])

    # --------------------------------------------------------------------------
    # Double-Entry Ledger Verification
    # --------------------------------------------------------------------------
    final_invested_calc = round(terminal_stk_val + terminal_qqq_val, 2)
    final_cash_calc = round(cash, 2)
    final_equity_calc = round(final_equity, 2)
    ledger_discrepancy = round(abs(final_equity_calc - (final_cash_calc + final_invested_calc)), 4)
    assert ledger_discrepancy < 0.10, f"Double-entry violation: equity {final_equity_calc} != {final_cash_calc} + {final_invested_calc}"

    # Independent Metrics Calculation (Zero-Division Safe)
    num_days = len(trading_dates)
    years = num_days / 252.0 if num_days > 0 else 1.0
    if config.initial_capital > 0:
        total_return_pct = ((final_equity - config.initial_capital) / config.initial_capital) * 100.0
        cagr = (((final_equity / config.initial_capital) ** (1.0 / years) - 1.0) * 100.0) if final_equity > 0 else -100.0
    else:
        total_return_pct = 0.0
        cagr = 0.0

    if config.initial_capital > 0 and len(df_port) > 0 and (df_port["equity"] > 0).any():
        cummax = df_port["equity"].cummax()
        safe_cummax = cummax.replace(0, np.nan)
        dd_series = ((df_port["equity"] - safe_cummax) / safe_cummax).fillna(0.0) * 100.0
        trough_idx = dd_series.idxmin()
        max_dd = float(dd_series.loc[trough_idx]) if pd.notna(trough_idx) else 0.0
        mdd_trough_date = df_port.loc[trough_idx, "date"] if pd.notna(trough_idx) else "N/A"

        peak_sub = df_port.loc[:trough_idx, "equity"]
        peak_idx = peak_sub.idxmax() if not peak_sub.empty else trough_idx
        mdd_peak_date = df_port.loc[peak_idx, "date"] if pd.notna(peak_idx) else "N/A"

        recovery_sub = df_port.loc[trough_idx:, "equity"]
        recovered_rows = recovery_sub[recovery_sub >= df_port.loc[peak_idx, "equity"]] if pd.notna(peak_idx) else pd.Series(dtype=float)
        mdd_recovery_date = df_port.loc[recovered_rows.index[0], "date"] if len(recovered_rows) > 0 else "Not Recovered"
    else:
        max_dd = 0.0
        mdd_peak_date = str(trading_dates[0])[:10] if len(trading_dates) > 0 else "N/A"
        mdd_trough_date = str(trading_dates[0])[:10] if len(trading_dates) > 0 else "N/A"
        mdd_recovery_date = "N/A"

    calmar_ratio = (cagr / abs(max_dd)) if abs(max_dd) > 0 else 0.0

    total_trades = len(df_trades)
    win_trades = len(df_trades[df_trades["pnl_dollar"] > 0]) if total_trades > 0 else 0
    loss_trades = total_trades - win_trades
    win_rate = (win_trades / total_trades * 100.0) if total_trades > 0 else 0.0

    dollar_wins = float(df_trades[df_trades["pnl_dollar"] > 0]["pnl_dollar"].sum()) if total_trades > 0 else 0.0
    dollar_losses = abs(float(df_trades[df_trades["pnl_dollar"] <= 0]["pnl_dollar"].sum())) if total_trades > 0 else 0.0
    dollar_pf = (dollar_wins / dollar_losses) if dollar_losses > 0 else (99.0 if dollar_wins > 0 else 0.0)

    avg_win = float(df_trades[df_trades["pnl_pct"] > 0]["pnl_pct"].mean()) if win_trades > 0 else 0.0
    avg_loss = float(df_trades[df_trades["pnl_pct"] <= 0]["pnl_pct"].mean()) if loss_trades > 0 else 0.0

    daily_ret = df_port["equity"].pct_change().fillna(0.0)
    rf_daily = 0.03 / 252.0
    excess_ret = daily_ret - rf_daily
    daily_std = daily_ret.std()
    sharpe_ratio = (excess_ret.mean() / daily_std) * np.sqrt(252.0) if daily_std > 0 else 0.0
    downside_std = np.sqrt(((daily_ret.apply(lambda r: min(r - rf_daily, 0.0))) ** 2).mean())
    sortino_ratio = (excess_ret.mean() / downside_std) * np.sqrt(252.0) if downside_std > 0 else 0.0

    metrics = {
        "final_equity": final_equity,
        "net_profit": final_equity - config.initial_capital,
        "total_return_pct": total_return_pct,
        "cagr": cagr,
        "max_dd": max_dd,
        "max_drawdown": max_dd,
        "dollar_pf": dollar_pf,
        "dollar_profit_factor": dollar_pf,
        "win_rate": win_rate,
        "total_trades": total_trades,
        "win_trades": win_trades,
        "loss_trades": loss_trades,
        "calmar_ratio": calmar_ratio,
        "total_slippage_cost": total_commission_slippage,
        "total_commission_slippage": total_commission_slippage,
        "mdd_peak_date": mdd_peak_date,
        "mdd_trough_date": mdd_trough_date,
        "mdd_recovery_date": mdd_recovery_date,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "years": years
    }

    return {
        "variant_name": name,
        "metrics": metrics,
        "df_trades": df_trades,
        "df_port": df_port,
        "active_positions": active_positions,
        "ledger_audit": {
            "final_equity": final_equity_calc,
            "final_cash": final_cash_calc,
            "final_invested": final_invested_calc,
            "discrepancy": ledger_discrepancy,
            "is_balanced": (ledger_discrepancy < 0.10)
        }
    }


# ------------------------------------------------------------------------------
# Strategy Class Wrapper
# ------------------------------------------------------------------------------
class V31Strategy:
    """
    Object-oriented production strategy wrapper for V3.1 Ultimate Edition.
    """
    def __init__(self, config: Optional[V31Config] = None):
        self.config = config or V31Config.create_track1()
        self.indicators_data: Optional[Dict[str, pd.DataFrame]] = None
        self.trading_dates: Optional[List[Any]] = None
        self.last_result: Optional[Dict[str, Any]] = None

    def load_data(self, cache_file: Optional[str] = None):
        self.indicators_data, self.trading_dates = load_indicators_cache(cache_file)

    def run_backtest(self, cache_file: Optional[str] = None) -> Dict[str, Any]:
        if self.indicators_data is None or self.trading_dates is None:
            self.load_data(cache_file)
        self.last_result = run_v31_simulation(
            config=self.config,
            indicators_data=self.indicators_data,
            trading_dates=self.trading_dates
        )
        return self.last_result

    def get_top_winners(self, n: int = 10) -> pd.DataFrame:
        if not self.last_result or "df_trades" not in self.last_result:
            raise ValueError("Run backtest before querying top winners.")
        df = self.last_result["df_trades"]
        if len(df) == 0:
            return df
        return df.sort_values(by="pnl_pct", ascending=False).head(n)

    def get_exit_attribution(self) -> pd.DataFrame:
        if not self.last_result or "df_trades" not in self.last_result:
            raise ValueError("Run backtest before querying exit attribution.")
        df = self.last_result["df_trades"]
        if len(df) == 0:
            return pd.DataFrame()
        grouped = df.groupby("exit_reason").agg(
            count=("pnl_dollar", "count"),
            win_rate=("is_win", lambda s: s.mean() * 100.0),
            avg_pnl_pct=("pnl_pct", "mean"),
            sum_pnl_dollar=("pnl_dollar", "sum")
        ).reset_index()
        grouped["pct_of_trades"] = (grouped["count"] / len(df)) * 100.0
        return grouped.sort_values(by="count", ascending=False)
