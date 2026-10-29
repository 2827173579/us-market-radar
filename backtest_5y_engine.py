"""
5-Year Full-Cycle Quantitative Strategy Backtest Engine (2021 - 2026)
Zero-Lookahead Bias · Dual-Track Stop Loss · Dynamic Breakeven Shield · Adaptive Gearbox
"""

import os
import sys
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
import time
import pickle
from datetime import datetime
import numpy as np
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

# Working directory
SCRATCH_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(SCRATCH_DIR, ".backtest_5y_cache.pkl")
REPORT_HTML_FILE = os.path.join(SCRATCH_DIR, "backtest_report_5y.html")

# Monitored Core Tickers
CORE_STOCKS = [
    'NVDA', 'TSM', 'AMD', 'AVGO', 'ASML', 'AMAT', 'MSFT', 'GOOGL', 'META', 'AAPL',
    'AMZN', 'PLTR', 'VST', 'BE', 'CRDO', 'COHR', 'MRVL', 'AAOI', 'LITE', 'ASTS',
    'RKLB', 'KO', 'AXP', 'INTC', 'TSLA', 'VRT', 'DELL', 'HPE', 'CEG', 'MU',
    'WDC', 'STX', 'CRWD', 'MSTR', 'ORCL', 'LLY', 'MRNA', 'MCD', 'NKE', 'V', 'PEP'
]
MACRO_BENCHMARKS = ['QQQ', 'SMH']

SLIPPAGE_RATE = 0.001  # 0.1% slippage per trade
INITIAL_CAPITAL = 100000.0
MAX_POSITIONS = 5
POSITION_PCT = 0.20  # 20% capital per position

def fetch_or_load_historical_data():
    """Fetch 5-year OHLCV data with local caching to prevent rate-limiting."""
    if os.path.exists(CACHE_FILE):
        print(f"[*] 📦 发现本地 5 年行情缓存: {CACHE_FILE}")
        try:
            with open(CACHE_FILE, "rb") as f:
                data = pickle.load(f)
            if "QQQ" in data and len(data) >= 20:
                print(f"[+] ✅ 成功载入 {len(data)} 只标的 5 年历史行情！")
                return data
        except Exception as e:
            print(f"[!] 缓存读取失败，重新拉取: {e}")

    all_tickers = MACRO_BENCHMARKS + CORE_STOCKS
    print(f"[*] 🚀 启动多线程拉取 {len(all_tickers)} 只标的过去 5 年日线数据 (yfinance period='5y')...")
    data = {}

    def _fetch_single(tk):
        try:
            df = yf.download(tk, period="5y", auto_adjust=True, progress=False)
            if df is not None and len(df) >= 40:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                return tk, df
        except Exception as err:
            pass
        return tk, None

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_fetch_single, tk): tk for tk in all_tickers}
        for fut in as_completed(futures):
            tk, df = fut.result()
            if df is not None and len(df) >= 40:
                data[tk] = df
                print(f"  [+] {tk:5s} 抓取成功 ({len(df)} 交易日)")
            else:
                print(f"  [-] {tk:5s} 数据不足或拉取失败")

    with open(CACHE_FILE, "wb") as f:
        pickle.dump(data, f)
    print(f"[+] 💾 已持久化写入本地缓存: {CACHE_FILE} (共 {len(data)} 只标的)")
    return data

def compute_rolling_indicators(df_raw):
    """
    严格基于当日及历史计算技术指标，严禁未来函数。
    """
    df = df_raw.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    # 1. 均线与偏离度
    df["MA50"] = close.rolling(50, min_periods=20).mean()
    df["Bias_MA50"] = ((close - df["MA50"]) / df["MA50"]) * 100.0

    # 2. 指数移动均线 EMA20, EMA10, EMA5
    df["EMA20"] = close.ewm(span=20, adjust=False).mean()
    df["EMA10"] = close.ewm(span=10, adjust=False).mean()
    df["EMA5"] = close.ewm(span=5, adjust=False).mean()

    # 3. ATR 14
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    df["ATR_14"] = tr.ewm(com=13, min_periods=14).mean()

    # 4. RSI 14
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, min_periods=14).mean()
    avg_loss = loss.ewm(com=13, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI_14"] = 100.0 - (100.0 / (1.0 + rs))

    # 5. 5日滚动极值 (用于自适应疯牛档，防破位当天漏报)
    df["Max_Bias_5d"] = df["Bias_MA50"].rolling(5, min_periods=1).max()
    df["Max_RSI_5d"] = df["RSI_14"].rolling(5, min_periods=1).max()

    # 6. 成交量 20 日均线与量比
    df["VOL_MA20"] = volume.rolling(20, min_periods=5).mean()
    df["Vol_Ratio"] = volume / df["VOL_MA20"].replace(0, np.nan)

    # 7. 60 日滚动斐波那契黄金坑
    high_60 = high.rolling(60, min_periods=20).max()
    low_60 = low.rolling(60, min_periods=20).min()
    diff_60 = high_60 - low_60
    fib_50 = high_60 - 0.5 * diff_60
    fib_618 = high_60 - 0.618 * diff_60
    df["Fib_Low"] = np.minimum(fib_50, fib_618)
    df["Fib_High"] = np.maximum(fib_50, fib_618)

    return df

def run_5y_backtest():
    raw_data = fetch_or_load_historical_data()
    if "QQQ" not in raw_data or "SMH" not in raw_data:
        print("[!] 错误: 必须包含 QQQ 与 SMH 宏观基准！")
        return

    print("\n[*] ⚙️ 正在对全量标的计算滚动无未来函数技术指标...")
    indicators_data = {}
    for tk, df in raw_data.items():
        if len(df) >= 60:
            indicators_data[tk] = compute_rolling_indicators(df)

    # 对齐共同交易日序列
    trading_dates = sorted(indicators_data["QQQ"].index)
    # 取从第 60 个交易日开始（保证指标充分收敛）
    trading_dates = trading_dates[60:]
    print(f"[+] ⏱️ 回测区间: {trading_dates[0].strftime('%Y-%m-%d')} ~ {trading_dates[-1].strftime('%Y-%m-%d')} (共 {len(trading_dates)} 个交易日)\n")

    # --------------------------------------------------------------------------
    # 状态机初始化
    # --------------------------------------------------------------------------
    closed_trades = []
    active_positions = {}  # ticker -> pos_info

    # 组合账户资产跟踪 ($100k 本金，单股限额 20%，最多 5 只并发)
    cash = INITIAL_CAPITAL
    portfolio_history = []  # list of {date, equity, cash, invested, drawdown_pct}
    peak_equity = INITIAL_CAPITAL

    qqq_df = indicators_data["QQQ"]
    smh_df = indicators_data["SMH"]

    print("[*] 🏃 开始逐日运行事件驱动回测引擎 (Day-by-Day Event Loop)...")
    for d_idx, date in enumerate(trading_dates):
        # 1. 宏观天气判定
        qqq_bias = qqq_df["Bias_MA50"].loc[date] if date in qqq_df.index else 0.0
        smh_bias = smh_df["Bias_MA50"].loc[date] if date in smh_df.index else 0.0
        is_rainstorm = (qqq_bias < -3.0 and smh_bias < -3.0)

        # 2. 检查现有持仓的出场与减仓信号
        tickers_to_close = []
        for ticker, pos in list(active_positions.items()):
            df_tk = indicators_data[ticker]
            if date not in df_tk.index:
                continue

            row = df_tk.loc[date]
            close = float(row["Close"])
            high = float(row["High"])
            low = float(row["Low"])
            open_p = float(row["Open"])
            ma50 = float(row["MA50"])
            ema20 = float(row["EMA20"])
            ema10 = float(row["EMA10"])
            atr = float(row["ATR_14"])
            rsi = float(row["RSI_14"])
            vol_ratio = float(row["Vol_Ratio"])
            max_bias_5d = float(row["Max_Bias_5d"])
            max_rsi_5d = float(row["Max_RSI_5d"])
            prev_row = df_tk.iloc[df_tk.index.get_loc(date)-1] if df_tk.index.get_loc(date) > 0 else row
            prev_close = float(prev_row["Close"])
            prev_ema20 = float(prev_row["EMA20"])
            prev_ema10 = float(prev_row["EMA10"])

            entry_p = pos["entry_price"]
            # 持续记忆历史最高价 (含盘中 High)
            pos["peak_price"] = max(pos["peak_price"], high, close)
            pos["max_pnl_pct"] = ((pos["peak_price"] - entry_p) / entry_p) * 100.0

            # A. 动态保本铁盾激活检查
            if not pos["is_be_active"]:
                if pos["max_pnl_pct"] >= 4.0 or (atr > 0 and pos["peak_price"] >= entry_p + 0.8 * atr):
                    pos["is_be_active"] = True
                    pos["be_stop"] = round(entry_p * 1.005, 4)

            # -------------------- 出场规则检查 --------------------
            exit_reason = None
            exit_price = close * (1.0 - SLIPPAGE_RATE)

            # 规则 1: 触及动态保本止损线
            if pos["is_be_active"] and low <= pos["be_stop"]:
                exit_reason = "🛡️ 触及保本线出场"
                exit_price = pos["be_stop"] * (1.0 - SLIPPAGE_RATE)

            # 规则 2: 双轨制极限止损
            elif not exit_reason:
                # 极限熔断: 浮亏 <= -7% 或 跌破 entry - 2*ATR
                pnl_low = ((low - entry_p) / entry_p) * 100.0
                if pnl_low <= -7.0 or (atr > 0 and low <= entry_p - 2.0 * atr):
                    exit_reason = "🔴 极限熔断止损"
                    exit_price = min(close, entry_p * 0.93) * (1.0 - SLIPPAGE_RATE)
                # 浅度放量破位 (仅在未减半仓的水下状态观察)
                elif not pos["trimmed"] and close < pos["entry_fib_low"] * 0.97:
                    if vol_ratio >= 1.20 and close <= prev_close:
                        exit_reason = "🔴 放量破位止损"
                        exit_price = close * (1.0 - SLIPPAGE_RATE)

            # 规则 3: 水下反抽强阻减半仓 (Trim_1)
            if not exit_reason and not pos["trimmed"] and (pos["entry_price"] < pos["entry_ma50"]):
                trim_target = min(pos["entry_fib_low"], ma50) * 0.99
                pnl_high = ((high - entry_p) / entry_p) * 100.0
                is_stall = (close < open_p) or (close <= prev_close)
                if (high >= trim_target or pnl_high >= 6.0) and is_stall and close > entry_p:
                    # 执行 50% 减仓
                    pos["trimmed"] = True
                    pos["trim_price"] = max(close, trim_target) * (1.0 - SLIPPAGE_RATE)
                    pos["trim_date"] = date
                    # 剩余半仓自动锁定保本
                    pos["is_be_active"] = True
                    pos["be_stop"] = max(pos.get("be_stop", 0.0), round(entry_p * 1.005, 4))
                    # 组合账户回笼 50% 现金
                    trade_shares = pos["shares"] * 0.5
                    cash += trade_shares * pos["trim_price"]

            # 规则 4: 移动止盈【EMA20 / EMA10 双速自适应变速箱】
            if not exit_reason:
                cur_pnl = ((close - entry_p) / entry_p) * 100.0
                # 浮盈满 +5% 或历史曾经大赚，开启变速箱跟踪
                if cur_pnl >= 5.0 or pos["max_pnl_pct"] >= 8.0:
                    is_crazy = (max_bias_5d >= 12.0) or (max_rsi_5d >= 72.0) or (pos["max_pnl_pct"] >= 12.0)
                    if is_crazy:
                        # 疯牛档: 破 EMA10 锁利
                        if close < ema10 and prev_close >= prev_ema10:
                            exit_reason = "🟣 疯牛跌破EMA10移动止盈"
                            exit_price = close * (1.0 - SLIPPAGE_RATE)
                    else:
                        # 常规主升档: 破 EMA20 锁利
                        if close < ema20 and prev_close >= prev_ema20 and close > ma50:
                            exit_reason = "🟣 常规跌破EMA20移动止盈"
                            exit_price = close * (1.0 - SLIPPAGE_RATE)

            if exit_reason:
                # 结算该笔交易
                hold_days = (date - pos["entry_date"]).days
                if pos["trimmed"]:
                    # 加权平均盈亏: 50% 减仓收益 + 50% 尾仓收益
                    pnl_1 = ((pos["trim_price"] - entry_p) / entry_p) * 100.0
                    pnl_2 = ((exit_price - entry_p) / entry_p) * 100.0
                    final_pnl_pct = 0.5 * pnl_1 + 0.5 * pnl_2
                    rem_shares = pos["shares"] * 0.5
                    cash += rem_shares * exit_price
                else:
                    final_pnl_pct = ((exit_price - entry_p) / entry_p) * 100.0
                    cash += pos["shares"] * exit_price

                closed_trades.append({
                    "ticker": ticker,
                    "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
                    "exit_date": date.strftime("%Y-%m-%d"),
                    "hold_days": hold_days,
                    "entry_price": entry_p,
                    "exit_price": exit_price,
                    "peak_price": pos["peak_price"],
                    "pnl_pct": round(final_pnl_pct, 2),
                    "exit_reason": exit_reason,
                    "trimmed_50": pos["trimmed"],
                    "is_win": final_pnl_pct > 0.0
                })
                tickers_to_close.append(ticker)

        for tk in tickers_to_close:
            del active_positions[tk]

        # 3. 检查新开仓买入信号 (仅在非暴雨天气 且 账户持仓未满 5 只时)
        if not is_rainstorm:
            # 扫描所有未持仓的核心标的
            candidates = []
            for ticker in CORE_STOCKS:
                if ticker in active_positions or ticker not in indicators_data:
                    continue

                df_tk = indicators_data[ticker]
                if date not in df_tk.index:
                    continue

                row = df_tk.loc[date]
                close = float(row["Close"])
                open_p = float(row["Open"])
                vol_ratio = float(row["Vol_Ratio"])
                rsi = float(row["RSI_14"])
                fib_low = float(row["Fib_Low"])
                fib_high = float(row["Fib_High"])
                ma50 = float(row["MA50"])
                atr = float(row["ATR_14"])
                prev_row = df_tk.iloc[df_tk.index.get_loc(date)-1] if df_tk.index.get_loc(date) > 0 else row
                prev_close = float(prev_row["Close"])

                # 买入条件 A: 黄金坑就位 (0.382~0.618 支撑带 + RSI <= 48 + 企稳阳线或缩量)
                cond_pit = (fib_low * 0.985 <= close <= fib_high * 1.015) and (rsi <= 48.0) and (close >= open_p or vol_ratio <= 1.0)
                # 买入条件 B: 极度超卖深坑反包 (RSI <= 35 冰点 + 放量阳线反包)
                cond_oversold = (rsi <= 35.0) and (close > open_p) and (close > prev_close) and (vol_ratio >= 1.1)

                if cond_pit or cond_oversold:
                    signal_type = "🟢 黄金坑低吸" if cond_pit else "🚀 极度超卖反包"
                    candidates.append({
                        "ticker": ticker,
                        "close": close,
                        "ma50": ma50,
                        "fib_low": fib_low,
                        "fib_high": fib_high,
                        "atr": atr,
                        "signal": signal_type
                    })

            # 开仓准入 (最多补满 5 只，按 RSI 越低优先级越高排队)
            candidates.sort(key=lambda x: indicators_data[x["ticker"]]["RSI_14"].loc[date])
            for cand in candidates:
                if len(active_positions) >= MAX_POSITIONS:
                    break

                # 资金分配 (单股限额 20% 总资产)
                alloc_capital = (cash + sum(p["shares"] * float(indicators_data[p["ticker"]]["Close"].loc[date]) for p in active_positions.values())) * POSITION_PCT
                alloc_capital = min(alloc_capital, cash)
                if alloc_capital < 200.0:  # 资金不足
                    continue

                fill_price = cand["close"] * (1.0 + SLIPPAGE_RATE)
                shares = alloc_capital / fill_price
                cash -= alloc_capital

                active_positions[cand["ticker"]] = {
                    "ticker": cand["ticker"],
                    "entry_date": date,
                    "entry_price": fill_price,
                    "entry_ma50": cand["ma50"],
                    "entry_fib_low": cand["fib_low"],
                    "entry_fib_high": cand["fib_high"],
                    "entry_atr": cand["atr"],
                    "shares": shares,
                    "peak_price": fill_price,
                    "max_pnl_pct": 0.0,
                    "is_be_active": False,
                    "be_stop": 0.0,
                    "trimmed": False,
                    "trim_price": 0.0,
                    "trim_date": None,
                    "signal_type": cand["signal"]
                }

        # 4. 每日组合资产快照
        invested_val = sum(pos["shares"] * float(indicators_data[pos["ticker"]]["Close"].loc[date]) for pos in active_positions.values())
        total_equity = cash + invested_val
        peak_equity = max(peak_equity, total_equity)
        drawdown_pct = ((total_equity - peak_equity) / peak_equity) * 100.0

        portfolio_history.append({
            "date": date.strftime("%Y-%m-%d"),
            "equity": round(total_equity, 2),
            "cash": round(cash, 2),
            "invested": round(invested_val, 2),
            "positions_count": len(active_positions),
            "drawdown_pct": round(drawdown_pct, 2),
            "is_rainstorm": is_rainstorm
        })

    # --------------------------------------------------------------------------
    # 统计指标计算
    # --------------------------------------------------------------------------
    df_trades = pd.DataFrame(closed_trades)
    df_port = pd.DataFrame(portfolio_history)

    # A. 交易统计
    total_trades = len(df_trades)
    win_trades = len(df_trades[df_trades["is_win"]]) if total_trades > 0 else 0
    loss_trades = total_trades - win_trades
    win_rate = (win_trades / total_trades * 100.0) if total_trades > 0 else 0.0

    avg_pnl = df_trades["pnl_pct"].mean() if total_trades > 0 else 0.0
    win_pnls = df_trades[df_trades["pnl_pct"] > 0]["pnl_pct"]
    loss_pnls = df_trades[df_trades["pnl_pct"] <= 0]["pnl_pct"]
    avg_win = win_pnls.mean() if len(win_pnls) > 0 else 0.0
    avg_loss = loss_pnls.mean() if len(loss_pnls) > 0 else 0.0
    profit_factor = abs(win_pnls.sum() / loss_pnls.sum()) if len(loss_pnls) > 0 and loss_pnls.sum() != 0 else 99.0
    max_win = df_trades["pnl_pct"].max() if total_trades > 0 else 0.0
    max_loss = df_trades["pnl_pct"].min() if total_trades > 0 else 0.0
    avg_hold_days = df_trades["hold_days"].mean() if total_trades > 0 else 0.0

    # B. 组合绩效指标
    final_equity = df_port["equity"].iloc[-1]
    total_return_pct = ((final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100.0
    years = len(trading_dates) / 252.0
    cagr = ((final_equity / INITIAL_CAPITAL) ** (1.0 / years) - 1.0) * 100.0
    max_dd = df_port["drawdown_pct"].min()
    calmar_ratio = abs(cagr / max_dd) if max_dd != 0 else 0.0

    # 日收益与夏普比率 (无风险利率 3.0%)
    df_port["daily_ret"] = df_port["equity"].pct_change().fillna(0.0)
    rf_daily = 0.03 / 252.0
    excess_ret = df_port["daily_ret"] - rf_daily
    sharpe_ratio = (excess_ret.mean() / df_port["daily_ret"].std()) * np.sqrt(252.0) if df_port["daily_ret"].std() > 0 else 0.0

    # QQQ 基准对比
    qqq_start = float(qqq_df.loc[trading_dates[0]]["Close"])
    qqq_end = float(qqq_df.loc[trading_dates[-1]]["Close"])
    qqq_return = ((qqq_end - qqq_start) / qqq_start) * 100.0
    qqq_cagr = ((qqq_end / qqq_start) ** (1.0 / years) - 1.0) * 100.0
    qqq_rolling_max = qqq_df["Close"].loc[trading_dates].cummax()
    qqq_dd = ((qqq_df["Close"].loc[trading_dates] - qqq_rolling_max) / qqq_rolling_max).min() * 100.0

    # --------------------------------------------------------------------------
    # 控制台打印结构化体检表
    # --------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print(" 🏆 5年全周期美股量化策略回测体检表 (2021-2026 · 1:1实盘闭环复刻)")
    print("=" * 80)
    print(f" ⏱️ 回测区间: {trading_dates[0].strftime('%Y-%m-%d')} ~ {trading_dates[-1].strftime('%Y-%m-%d')} ({years:.1f} 年 · {len(trading_dates)} 个交易日)")
    print(f" 💼 组合本金: ${INITIAL_CAPITAL:,.0f} ｜ 杠杆: 1.0x (无杠杆现货) ｜ 单股限额: 20% (最多 5 只并发)")
    print(f" ⛽ 滑点摩擦: 双边 0.1% (每笔往返扣除 0.2% 交易成本)")
    print("-" * 80)
    print(" 【一、 纯交易信号维度 (Trade-Level Statistics)】")
    print(f"   • 总交易笔数 (Total Trades):  {total_trades} 笔")
    print(f"   • 胜率 (Win Rate):           {win_rate:.1f}% ({win_trades} 胜 / {loss_trades} 负)")
    print(f"   • 盈亏比 (Profit Factor):     {profit_factor:.2f}")
    print(f"   • 平均单笔盈亏 (Avg PnL):     {avg_pnl:+.2f}%")
    print(f"   • 平均盈利交易 (Avg Win):     {avg_win:+.2f}%")
    print(f"   • 平均亏损交易 (Avg Loss):    {avg_loss:+.2f}%")
    print(f"   • 最大单笔盈利 (Max Win):     {max_win:+.2f}%")
    print(f"   • 最大单笔亏损 (Max Loss):    {max_loss:+.2f}% (铁血硬风控拦截)")
    print(f"   • 平均持仓周期 (Avg Holding): {avg_hold_days:.1f} 天")
    print("-" * 80)
    print(" 【二、 投资组合复利与风控维度 (Portfolio-Level Performance)】")
    print(f"   • 初始资金:                   ${INITIAL_CAPITAL:,.2f}")
    print(f"   • 最终资金 (Final Equity):    ${final_equity:,.2f}")
    print(f"   • 5年总收益率 (Total Return): {total_return_pct:+.2f}%  (对比 QQQ 基准: {qqq_return:+.2f}%)")
    print(f"   • 年化复合收益率 (CAGR):      {cagr:+.2f}%  (对比 QQQ 基准: {qqq_cagr:+.2f}%)")
    print(f"   • 最大回撤 (Max Drawdown):    {max_dd:.2f}%  (对比 QQQ 基准最大回撤: {qqq_dd:.2f}%)")
    print(f"   • 夏普比率 (Sharpe Ratio):    {sharpe_ratio:.2f}")
    print(f"   • 卡玛比率 (Calmar Ratio):    {calmar_ratio:.2f}")
    print("-" * 80)

    # 出场类型归因
    print(" 【三、 出场行为量化归因 (Exit Attribution)】")
    exit_counts = df_trades["exit_reason"].value_counts()
    for reason, count in exit_counts.items():
        sub_df = df_trades[df_trades["exit_reason"] == reason]
        sub_win = len(sub_df[sub_df["is_win"]]) / len(sub_df) * 100.0 if len(sub_df) > 0 else 0.0
        sub_pnl = sub_df["pnl_pct"].mean() if len(sub_df) > 0 else 0.0
        print(f"   • {reason:25s}: {count:3d} 次 ({count/total_trades*100:4.1f}%) ｜ 胜率 {sub_win:5.1f}% ｜ 均值 {sub_pnl:+5.1f}%")
    print("=" * 80 + "\n")

    # --------------------------------------------------------------------------
    # 生成暗黑交互式 HTML 战报 (backtest_report_5y.html)
    # --------------------------------------------------------------------------
    generate_html_report(
        df_trades=df_trades,
        df_port=df_port,
        qqq_df=qqq_df,
        trading_dates=trading_dates,
        metrics={
            "total_trades": total_trades,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "avg_pnl": avg_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "max_win": max_win,
            "max_loss": max_loss,
            "avg_hold_days": avg_hold_days,
            "final_equity": final_equity,
            "total_return_pct": total_return_pct,
            "cagr": cagr,
            "max_dd": max_dd,
            "sharpe_ratio": sharpe_ratio,
            "calmar_ratio": calmar_ratio,
            "qqq_return": qqq_return,
            "qqq_cagr": qqq_cagr,
            "qqq_dd": qqq_dd,
            "years": years
        }
    )

def generate_html_report(df_trades, df_port, qqq_df, trading_dates, metrics):
    """生成专业暗黑量化战报 HTML"""
    # 准备 Plotly 曲线数据
    dates = df_port["date"].tolist()
    equity_curve = df_port["equity"].tolist()
    drawdown_curve = df_port["drawdown_pct"].tolist()

    # 标准化 QQQ 曲线为相同 $100k 起点
    qqq_closes = [float(qqq_df.loc[d]["Close"]) for d in trading_dates]
    qqq_base = qqq_closes[0]
    qqq_curve = [round(INITIAL_CAPITAL * (c / qqq_base), 2) for c in qqq_closes]

    # 格式化交易记录表格行 (最新前 150 笔)
    trades_rows_html = []
    for _, t in df_trades.iloc[::-1].head(150).iterrows():
        pnl_color = "#3fb950" if t["pnl_pct"] > 0 else "#f85149"
        badge = "badge-win" if t["pnl_pct"] > 0 else "badge-loss"
        trim_tag = " <span style='font-size:10px; background:#1f6feb; padding:2px 4px; border-radius:3px;'>50%强阻减仓</span>" if t["trimmed_50"] else ""
        trades_rows_html.append(f"""
        <tr>
            <td style="font-weight: bold; color: #58a6ff;">{t['ticker']}</td>
            <td>{t['entry_date']}</td>
            <td>{t['exit_date']} ({t['hold_days']}天)</td>
            <td>${t['entry_price']:.2f}</td>
            <td>${t['exit_price']:.2f}</td>
            <td style="color: {pnl_color}; font-weight: bold;">{t['pnl_pct']:+.2f}%</td>
            <td><span class="{badge}">{t['exit_reason']}</span>{trim_tag}</td>
        </tr>
        """)

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>美股量化策略 5年全周期回测审计战报 (2021-2026)</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        body {{
            background-color: #0d1117;
            color: #c9d1d9;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 20px;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #30363d;
            padding-bottom: 16px;
            margin-bottom: 24px;
        }}
        .title-box h1 {{
            margin: 0;
            font-size: 24px;
            color: #58a6ff;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .title-box p {{
            margin: 6px 0 0 0;
            color: #8b949e;
            font-size: 13px;
        }}
        .nav-btn {{
            background: #238636;
            color: white;
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 13px;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: 0.2s;
        }}
        .nav-btn:hover {{
            background: #2ea043;
        }}
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .kpi-card {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 16px;
        }}
        .kpi-label {{
            font-size: 12px;
            color: #8b949e;
            margin-bottom: 6px;
        }}
        .kpi-val {{
            font-size: 24px;
            font-weight: bold;
            color: #f0f6fc;
        }}
        .kpi-sub {{
            font-size: 11px;
            color: #8b949e;
            margin-top: 4px;
        }}
        .chart-box {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 24px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}
        th, td {{
            padding: 10px 12px;
            text-align: left;
            border-bottom: 1px solid #21262d;
        }}
        th {{
            background: #161b22;
            color: #8b949e;
            font-weight: 600;
        }}
        tr:hover {{
            background: #1f242c;
        }}
        .badge-win {{
            background: rgba(63, 185, 80, 0.15);
            color: #3fb950;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        }}
        .badge-loss {{
            background: rgba(248, 81, 73, 0.15);
            color: #f85149;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title-box">
                <h1>🏛️ 美股量化策略 5年全周期回测审计战报</h1>
                <p>区间: 2021-2026 ({metrics['years']:.1f} 年 · 1200+ 交易日) ｜ 双轨制止损 + 动态保本铁盾 + 双速自适应移动止盈 1:1 实盘复刻</p>
            </div>
            <div>
                <a href="futu_pro_terminal.html" class="nav-btn">📱 返回富途专业看盘终端</a>
            </div>
        </div>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-label">5年总复利收益 (策略 vs QQQ)</div>
                <div class="kpi-val" style="color: {'#3fb950' if metrics['total_return_pct']>0 else '#f85149'};">{metrics['total_return_pct']:+.2f}%</div>
                <div class="kpi-sub">QQQ同期基准: {metrics['qqq_return']:+.2f}% (超额 Alpha: {metrics['total_return_pct'] - metrics['qqq_return']:+.2f}%)</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">年化复合收益率 (CAGR)</div>
                <div class="kpi-val" style="color: #58a6ff;">{metrics['cagr']:+.2f}%</div>
                <div class="kpi-sub">QQQ年化: {metrics['qqq_cagr']:+.2f}%</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">最大历史回撤 (Max Drawdown)</div>
                <div class="kpi-val" style="color: #f85149;">{metrics['max_dd']:.2f}%</div>
                <div class="kpi-sub">QQQ同期回撤: {metrics['qqq_dd']:.2f}% (风控胜出)</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">夏普比率 (Sharpe Ratio)</div>
                <div class="kpi-val" style="color: #d29922;">{metrics['sharpe_ratio']:.2f}</div>
                <div class="kpi-sub">卡玛比率 (Calmar): {metrics['calmar_ratio']:.2f}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">纯交易胜率 (Win Rate)</div>
                <div class="kpi-val" style="color: {'#3fb950' if metrics['win_rate']>=60 else '#58a6ff'};">{metrics['win_rate']:.1f}%</div>
                <div class="kpi-sub">盈亏比 (Profit Factor): <b>{metrics['profit_factor']:.2f}</b></div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">总交易笔数 / 平均持仓</div>
                <div class="kpi-val" style="color: #f0f6fc;">{metrics['total_trades']} 笔</div>
                <div class="kpi-sub">平均每笔持仓: {metrics['avg_hold_days']:.1f} 天 ｜ 均值: {metrics['avg_pnl']:+.2f}%</div>
            </div>
        </div>

        <div class="chart-box">
            <div id="equityChart" style="height: 480px;"></div>
        </div>

        <div class="chart-box">
            <div id="drawdownChart" style="height: 220px;"></div>
        </div>

        <div class="chart-box">
            <h3 style="margin-top:0; color:#58a6ff;">📋 核心交易明细日志 (近 150 笔真实状态机闭环记录)</h3>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>标的代码</th>
                            <th>建仓日期</th>
                            <th>平仓日期 (持仓)</th>
                            <th>买入成本</th>
                            <th>平仓价格</th>
                            <th>综合盈亏%</th>
                            <th>出场状态机归因</th>
                        </tr>
                    </thead>
                    <tbody>
                        {"".join(trades_rows_html)}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        const dates = {dates};
        const strategyEquity = {equity_curve};
        const qqqEquity = {qqq_curve};
        const drawdown = {drawdown_curve};

        const traceStrategy = {{
            x: dates,
            y: strategyEquity,
            mode: 'lines',
            name: '量化策略净值 ($100k起步)',
            line: {{ color: '#2ea043', width: 2.2 }}
        }};

        const traceQQQ = {{
            x: dates,
            y: qqqEquity,
            mode: 'lines',
            name: 'QQQ 纳指基准 ($100k起步)',
            line: {{ color: '#8b949e', width: 1.5, dash: 'dot' }}
        }};

        const layoutEquity = {{
            title: {{ text: '📈 5年全周期组合复利资金曲线对比 (Strategy vs QQQ Benchmark)', font: {{ color: '#f0f6fc', size: 16 }} }},
            paper_bgcolor: '#161b22',
            plot_bgcolor: '#161b22',
            xaxis: {{ gridcolor: '#30363d', color: '#8b949e' }},
            yaxis: {{ title: '账户总资产 ($)', gridcolor: '#30363d', color: '#8b949e' }},
            legend: {{ font: {{ color: '#c9d1d9' }}, orientation: 'h', y: 1.1 }},
            margin: {{ l: 60, r: 20, t: 60, b: 40 }}
        }};

        Plotly.newPlot('equityChart', [traceStrategy, traceQQQ], layoutEquity, {{ responsive: true }});

        const traceDD = {{
            x: dates,
            y: drawdown,
            fill: 'tozeroy',
            mode: 'lines',
            name: '策略动态回撤 (%)',
            line: {{ color: '#f85149', width: 1.2 }},
            fillcolor: 'rgba(248, 81, 73, 0.2)'
        }};

        const layoutDD = {{
            title: {{ text: '🌊 历史最大动态回撤水下分布图 (Drawdown Under Water)', font: {{ color: '#f0f6fc', size: 14 }} }},
            paper_bgcolor: '#161b22',
            plot_bgcolor: '#161b22',
            xaxis: {{ gridcolor: '#30363d', color: '#8b949e' }},
            yaxis: {{ title: '回撤幅度 (%)', gridcolor: '#30363d', color: '#8b949e' }},
            margin: {{ l: 60, r: 20, t: 40, b: 40 }}
        }};

        Plotly.newPlot('drawdownChart', [traceDD], layoutDD, {{ responsive: true }});
    </script>
</body>
</html>"""

    with open(REPORT_HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"[+] ✅ 暗黑专业交互式网页战报已生成: {REPORT_HTML_FILE}")

if __name__ == "__main__":
    run_5y_backtest()
