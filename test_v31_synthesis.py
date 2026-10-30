import os
import sys
import pickle
import numpy as np
import pandas as pd

PROJECT_DIR = r"C:\Users\User\.gemini\antigravity-ide\scratch\us-market-radar"
sys.path.insert(0, PROJECT_DIR)

from backtest_5y_engine import (
    CORE_STOCKS, MACRO_BENCHMARKS, INITIAL_CAPITAL,
    compute_rolling_indicators, independent_metrics_recalculation
)

# Load 5Y cached data
CACHE_FILE = os.path.join(PROJECT_DIR, ".backtest_5y_cache.pkl")
with open(CACHE_FILE, "rb") as f:
    raw_data = pickle.load(f)

indicators_data = {}
for tk, df in raw_data.items():
    if len(df) >= 20:
        indicators_data[tk] = compute_rolling_indicators(df)

trading_dates = sorted(indicators_data["QQQ"].index)
warmup_period = min(60, len(trading_dates) - 1)
trading_dates = trading_dates[warmup_period:]

REMOVED_TICKERS = {'KO', 'PEP', 'MCD', 'NKE', 'V', 'INTC', 'WDC', 'HPE', 'MRNA'}
AGGRESSIVE_STOCKS = [t for t in CORE_STOCKS if t not in REMOVED_TICKERS]


def run_v31_synthesis(
    variant_name="V3.1",
    stocks_pool=AGGRESSIVE_STOCKS,
    mode="PARKING",          # "PARKING" or "FIXED_CORE"
    core_qqq_pct=0.60,
    satellite_stock_pct=0.40,
    max_stock_positions=3,
    stock_pos_pct=0.25,      # 25% single stock cap
    stock_risk_pct=0.015,    # 1.5% account risk per stock
    slippage_rate=0.00015,   # Firstrade 1.5 bps
    stock_regime_gate="MA50",# "NONE", "MA50", "MA50_HALF_RISK"
    qqq_bear_filter="MA200"  # "NONE", "MA200"
):
    cash = INITIAL_CAPITAL
    active_positions = {}
    pending_orders = []
    closed_trades = []
    portfolio_history = []
    total_commission_slippage = 0.0
    qqq_shares = 0.0

    qqq_df = indicators_data["QQQ"]
    smh_df = indicators_data["SMH"]

    for d_idx, date in enumerate(trading_dates):
        qqq_bias = float(qqq_df["Bias_MA50"].loc[date]) if date in qqq_df.index and "Bias_MA50" in qqq_df.columns else 0.0
        smh_bias = float(smh_df["Bias_MA50"].loc[date]) if date in smh_df.index and "Bias_MA50" in smh_df.columns else 0.0
        is_rainstorm = (qqq_bias < -3.0 and smh_bias < -3.0)

        qqq_close_today = float(qqq_df["Close"].loc[date])
        qqq_open_today = float(qqq_df["Open"].loc[date])
        qqq_ma50_today = float(qqq_df["MA50"].loc[date]) if "MA50" in qqq_df.columns else qqq_close_today
        qqq_sma200_today = float(qqq_df["SMA200"].loc[date]) if "SMA200" in qqq_df.columns else qqq_close_today

        prev_date = trading_dates[d_idx - 1] if d_idx > 0 else date
        qqq_close_prev = float(qqq_df["Close"].loc[prev_date])
        qqq_ma50_prev = float(qqq_df["MA50"].loc[prev_date]) if "MA50" in qqq_df.columns else qqq_close_prev
        qqq_sma200_prev = float(qqq_df["SMA200"].loc[prev_date]) if "SMA200" in qqq_df.columns else qqq_close_prev

        # Macro exit condition for QQQ
        qqq_defense_trigger = (qqq_open_today < qqq_sma200_prev) or is_rainstorm
        if mode == "PARKING":
            if qqq_shares > 0 and qqq_defense_trigger:
                fill_qqq_exit = qqq_open_today * (1.0 - slippage_rate)
                proceeds = qqq_shares * fill_qqq_exit
                total_commission_slippage += qqq_shares * qqq_open_today * slippage_rate
                cash += proceeds
                qqq_shares = 0.0
        elif mode == "PURE_STOCK":
            qqq_shares = 0.0

        # Sort pending orders: SELL before BUY
        sell_orders = [o for o in pending_orders if o["action"] in ["SELL_EXIT", "TRIM_50"]]
        buy_orders = [o for o in pending_orders if o["action"] == "BUY"]
        pending_orders = []

        # 1. Execute SELL_EXIT
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
                fill_p = open_p * (1.0 - slippage_rate)
                gross_proceeds = pos["shares"] * fill_p
                total_commission_slippage += pos["shares"] * (open_p - fill_p)
                cash += gross_proceeds

                total_exit_dollars = gross_proceeds + pos["trim_proceeds"]
                orig_cost_dollars = pos["orig_shares"] * pos["entry_price"]
                pnl_dollar = total_exit_dollars - orig_cost_dollars
                pnl_pct = (pnl_dollar / orig_cost_dollars) * 100.0

                closed_trades.append({
                    "ticker": tk,
                    "entry_date": pos["entry_date"],
                    "exit_date": date,
                    "hold_days": (pd.to_datetime(date) - pd.to_datetime(pos["entry_date"])).days,
                    "entry_price": pos["entry_price"],
                    "exit_price": fill_p,
                    "pnl_dollar": pnl_dollar,
                    "pnl_pct": pnl_pct,
                    "exit_reason": order["reason"],
                    "is_win": pnl_dollar > 0
                })
                del active_positions[tk]
            elif order["action"] == "TRIM_50":
                if not pos["trimmed"]:
                    trim_shares = pos["shares"] * 0.5
                    fill_p = open_p * (1.0 - slippage_rate)
                    trim_proceeds = trim_shares * fill_p
                    total_commission_slippage += trim_shares * (open_p - fill_p)
                    cash += trim_proceeds

                    pos["shares"] -= trim_shares
                    pos["trimmed"] = True
                    pos["trim_proceeds"] = trim_proceeds
                    pos["is_be_active"] = True
                    pos["be_stop"] = max(pos.get("be_stop", 0.0), round(pos["entry_price"] * 1.005, 4))

        # 2. Execute BUY
        for order in buy_orders:
            tk = order["ticker"]
            if tk in active_positions or len(active_positions) >= max_stock_positions:
                continue
            df_tk = indicators_data[tk]
            if date not in df_tk.index:
                continue

            open_p = float(df_tk["Open"].loc[date])
            sig_close = order.get("signal_close", open_p)
            sig_low = order.get("signal_low", open_p * 0.98)
            sig_atr = order.get("signal_atr", 1.0)

            if open_p < sig_low or open_p < sig_close * 0.985 or open_p > sig_close * 1.020:
                continue

            fill_p = open_p * (1.0 + slippage_rate)
            struct_stop = min(sig_low - 0.5 * sig_atr, fill_p - 1.8 * sig_atr)
            stop_level = max(struct_stop, fill_p * 0.932)
            stop_dist_pct = max((fill_p - stop_level) / fill_p, 0.035)

            stk_invested = sum(p["shares"] * float(indicators_data[p["ticker"]]["Close"].loc[date]) for p in active_positions.values())
            current_equity = cash + stk_invested + (qqq_shares * qqq_close_today)

            eff_risk = stock_risk_pct
            eff_pos = stock_pos_pct
            if stock_regime_gate == "MA50_HALF_RISK" and qqq_close_today < qqq_ma50_today:
                eff_risk = stock_risk_pct * 0.5
                eff_pos = stock_pos_pct * 0.6

            target_notional = min(current_equity * eff_pos, (current_equity * eff_risk) / stop_dist_pct)

            # In parking mode, liquidate QQQ if needed
            if mode == "PARKING" and cash < target_notional and qqq_shares > 0:
                needed_cash = target_notional - cash
                fill_qqq_sale = qqq_open_today * (1.0 - slippage_rate)
                qqq_shares_to_sell = min(qqq_shares, needed_cash / fill_qqq_sale)
                sale_proceeds = qqq_shares_to_sell * fill_qqq_sale
                total_commission_slippage += qqq_shares_to_sell * qqq_open_today * slippage_rate
                cash += sale_proceeds
                qqq_shares -= qqq_shares_to_sell

            alloc_capital = min(target_notional, cash)
            if alloc_capital >= 200.0:
                shares = alloc_capital / fill_p
                total_commission_slippage += shares * (fill_p - open_p)
                cash -= alloc_capital

                active_positions[tk] = {
                    "ticker": tk, "entry_date": date, "entry_price": fill_p,
                    "shares": shares, "orig_shares": shares, "stop_level": stop_level,
                    "entry_ma50": order.get("entry_ma50", fill_p),
                    "entry_fib_low": order.get("entry_fib_low", fill_p * 0.95),
                    "peak_price": fill_p, "max_pnl_pct": 0.0, "is_be_active": False,
                    "be_stop": 0.0, "trimmed": False, "trim_proceeds": 0.0, "bars_held": 0
                }

        # Parking mode: park idle cash into QQQ if above SMA200 and not rainstorm
        if mode == "PARKING":
            allow_park = (not is_rainstorm) and (qqq_close_prev > qqq_sma200_prev) and (cash >= 1000.0)
            if allow_park:
                park_cash = max(0.0, cash - 500.0)
                fill_qqq_buy = qqq_open_today * (1.0 + slippage_rate)
                add_qqq_shares = park_cash / fill_qqq_buy
                total_commission_slippage += add_qqq_shares * qqq_open_today * slippage_rate
                cash -= park_cash
                qqq_shares += add_qqq_shares

        # Intraday Stops
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

            be_triggered = False
            be_fill_price = 0.0
            be_reason = ""
            if days_held >= 1 and pos["is_be_active"] and pos["be_stop"] > 0:
                be_level = pos["be_stop"]
                is_lock = (be_level > entry_p * 1.01)
                lock_tag = f"阶梯锁利(+{(be_level/entry_p - 1)*100:.1f}%)" if is_lock else "保本线"

                if open_p <= be_level:
                    be_triggered = True
                    be_fill_price = open_p * (1.0 - slippage_rate)
                    be_reason = f"🛡️ 触及{lock_tag}出场(跳空低开)"
                elif low_p <= be_level:
                    be_triggered = True
                    be_fill_price = be_level * (1.0 - slippage_rate)
                    be_reason = f"🛡️ 触及{lock_tag}出场"

            stop_triggered = False
            stop_fill_price = 0.0
            stop_reason = ""
            effective_stop_level = pos.get("stop_level", entry_p * 0.932)

            if open_p <= effective_stop_level:
                stop_triggered = True
                stop_fill_price = open_p * (1.0 - slippage_rate)
                stop_reason = "🔴 结构熔断止损(跳空开盘)"
            elif low_p <= effective_stop_level:
                stop_triggered = True
                stop_fill_price = effective_stop_level * (1.0 - slippage_rate)
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
                total_commission_slippage += pos["shares"] * slippage_rate * (exit_price / (1.0 - slippage_rate))
                cash += gross_proceeds

                total_exit_dollars = gross_proceeds + pos["trim_proceeds"]
                orig_cost_dollars = pos["orig_shares"] * pos["entry_price"]
                pnl_dollar = total_exit_dollars - orig_cost_dollars
                pnl_pct = (pnl_dollar / orig_cost_dollars) * 100.0

                closed_trades.append({
                    "ticker": ticker, "entry_date": pos["entry_date"], "exit_date": date,
                    "hold_days": days_held, "entry_price": pos["entry_price"],
                    "exit_price": exit_price, "pnl_dollar": pnl_dollar,
                    "pnl_pct": pnl_pct, "exit_reason": exit_reason, "is_win": pnl_dollar > 0
                })
                tickers_to_close_intraday.append(ticker)
            else:
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

        # EOD Check (Queue for Next Open)
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
            if (rsi >= 82.0) or ((rsi >= 76.0 and bias_ma50 >= 20.0) and is_stall and cur_pnl >= 10.0):
                signal_exit_reason = "🎉 极度冲顶清仓止盈"
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

        # Candidate Scanning
        allow_stock_entry = not is_rainstorm
        if stock_regime_gate == "MA50" and qqq_close_today < qqq_ma50_today:
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

                cond_bull_trend = (close_p > sma200) and (ma50 >= sma200 * 0.97) and (rs_60 >= -8.0)
                cond_pit_depth = (-0.20 <= fib_pos <= 2.20)
                cond_pit_oversold = (min_rsi_3d <= 45.0) and (rsi <= 50.0)
                cond_pit_atr = (atr_pct <= 5.2)

                cond_golden_pit = cond_bull_trend and cond_pit_depth and cond_pit_oversold and is_right_turn and cond_pit_atr

                if close_p > sma200:
                    cond_oversold = (min_rsi_3d <= 32.0) and (close_p > open_p) and (close_p > prev_close * 1.015) and (vol_ratio >= 1.15)
                else:
                    cond_oversold = (min_rsi_3d <= 28.0) and (close_p > open_p) and (close_p > prev_close * 1.015) and (vol_ratio >= 1.15)

                if cond_golden_pit or cond_oversold:
                    candidates.append({
                        "ticker": ticker, "close": close_p, "low": low_p, "atr": atr,
                        "ma50": ma50, "fib_low": fib_low, "rsi": rsi, "rs_60": rs_60
                    })

            candidates.sort(key=lambda x: (x["rs_60"], -x["rsi"]), reverse=True)
            slots_available = max_stock_positions - len(active_positions) - len(pending_orders)
            for cand in candidates[:max(0, slots_available)]:
                pending_orders.append({
                    "action": "BUY", "ticker": cand["ticker"], "signal_close": cand["close"],
                    "signal_low": cand["low"], "signal_atr": cand["atr"],
                    "entry_ma50": cand["ma50"], "entry_fib_low": cand["fib_low"]
                })

        # Mark-to-market
        stk_mkt_val = sum(p["shares"] * float(indicators_data[p["ticker"]]["Close"].loc[date]) for p in active_positions.values())
        qqq_mkt_val = qqq_shares * qqq_close_today
        portfolio_history.append({
            "date": date, "equity": cash + stk_mkt_val + qqq_mkt_val,
            "cash": cash, "stk_val": stk_mkt_val, "qqq_val": qqq_mkt_val
        })

    df_port = pd.DataFrame(portfolio_history)
    df_trades = pd.DataFrame(closed_trades) if closed_trades else pd.DataFrame()
    final_eq = df_port.iloc[-1]["equity"]
    peak_eq = df_port["equity"].cummax()
    max_dd = ((df_port["equity"] - peak_eq) / peak_eq).min() * 100.0
    cagr = ((final_eq / INITIAL_CAPITAL) ** (1.0 / (len(trading_dates) / 252.0)) - 1.0) * 100.0

    wins = df_trades[df_trades["pnl_dollar"] > 0] if len(df_trades) > 0 else pd.DataFrame()
    losses = df_trades[df_trades["pnl_dollar"] <= 0] if len(df_trades) > 0 else pd.DataFrame()
    gp = wins["pnl_dollar"].sum() if len(wins) > 0 else 0.0
    gl = abs(losses["pnl_dollar"].sum()) if len(losses) > 0 else 1.0
    pf = gp / gl
    wr = len(wins) / len(df_trades) * 100.0 if len(df_trades) > 0 else 0.0
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0.0

    return {
        "name": variant_name,
        "final_equity": final_eq,
        "net_profit": final_eq - INITIAL_CAPITAL,
        "cagr": cagr,
        "max_dd": max_dd,
        "dollar_pf": pf,
        "win_rate": wr,
        "trades": len(df_trades),
        "calmar": calmar,
        "friction": total_commission_slippage,
        "df_trades": df_trades
    }


if __name__ == "__main__":
    syntheses = [
        ("V3.1-Track1 (Pure Stock MA50 Gate, 25% pos, 1.5% risk)", {
            "mode": "PURE_STOCK", "stock_pos_pct": 0.25, "stock_risk_pct": 0.015,
            "stock_regime_gate": "MA50", "qqq_bear_filter": "NONE", "max_stock_positions": 4
        }),
        ("V3.1-Track2-Opt1 (Parking + MA200 QQQ Defense + Stock MA50 Gate)", {
            "mode": "PARKING", "stock_pos_pct": 0.25, "stock_risk_pct": 0.015,
            "stock_regime_gate": "MA50", "qqq_bear_filter": "MA200", "max_stock_positions": 3
        }),
        ("V3.1-Track2-Opt2 (Parking + MA200 QQQ Defense + Stock MA50 HalfRisk)", {
            "mode": "PARKING", "stock_pos_pct": 0.25, "stock_risk_pct": 0.015,
            "stock_regime_gate": "MA50_HALF_RISK", "qqq_bear_filter": "MA200", "max_stock_positions": 3
        }),
    ]

    print("\n" + "=" * 115)
    print(" 🌟 V3.1 Ultimate Edition Dual-Track Synthesis Matrix")
    print("=" * 115)
    saved_res = {}
    for name, params in syntheses:
        res = run_v31_synthesis(variant_name=name, **params)
        saved_res[name] = res
        print(f"{res['name']:<55s} ｜ ${res['final_equity']:<11,.2f} ｜ {res['cagr']:<+7.2f}% ｜ {res['max_dd']:<7.2f}% ｜ {res['dollar_pf']:<7.2f} ｜ {res['win_rate']:<5.1f}% ｜ {res['trades']:<5d} ｜ {res['calmar']:<6.2f} ｜ ${res['friction']:<8,.2f}")
    print("=" * 115)

    print("\n--- V3.1-Track1 Top Winners (Verification of Breakout Gain Preservation) ---")
    df_t1 = saved_res["V3.1-Track1 (Pure Stock MA50 Gate, 25% pos, 1.5% risk)"]["df_trades"]
    for idx, r in df_t1.sort_values(by="pnl_pct", ascending=False).head(10).iterrows():
        print(f"{r['ticker']:<6} | {str(r['entry_date'])[:10]} -> {str(r['exit_date'])[:10]} | {r['pnl_pct']:+6.2f}% (${r['pnl_dollar']:+8.2f}) | {r['exit_reason']}")
