# V3.1 Ultimate Edition Production Deployment & Execution Plan
**Institutional Quantitative Trading Plan for Firstrade Brokerage Account**

---

## 1. Executive Summary & Strategy Architecture

The **V3.1 Ultimate Edition** quantitative trading system is an institutional-grade, risk-managed trend-following and swing-trading engine upgraded from V2.4 / V3.0. Calibrated specifically for live execution on **Firstrade Securities** ($0 commission, 1~2 bps mega-cap execution slippage), V3.1 eliminates the structural vulnerabilities of earlier iterations while unlocking multi-year runner profits.

### Dual-Track Strategy Architecture

V3.1 provides two distinct, empirically validated implementation tracks:

| Architectural Dimension | **Track 1: Pure Stock Dynamic Risk-Managed** | **Track 2: QQQ Base Enhancement Flagship (Honest Real-Time)** |
| :--- | :--- | :--- |
| **Primary Objective** | Pure stock alpha generation with defensive drawdown compression | Market-beating risk-adjusted returns combining tech beta and breakout alpha |
| **Target Audience** | Traders seeking 100% individual stock momentum without ETF core | Investors seeking maximum capital efficiency, top Calmar ratio, and low drawdowns |
| **Capital Allocation** | 0% to 100% dynamic cash / equities (0% QQQ) | Dynamic QQQ cash sweep (up to 100%) + up to 3 satellite stock positions |
| **Max Stock Slots** | **4 concurrent stock positions** (25% max notional each) | **3 concurrent stock positions** (25% max notional each) |
| **5-Year Final Equity** | **$130,441.41** (Net Profit: +$30,441.41) | **$187,910.80** (Net Profit: +$87,910.80) |
| **5-Year CAGR** | **+5.77%** | **+14.24%** (vs V3.0-B +13.48%) |
| **Max Drawdown (MDD)** | **-11.31%** (compressed by 54.7% vs V3.0-A's -24.98%) | **-19.03%** (compressed vs QQQ B&H -29.56% and V3.0-B -19.48%) |
| **Dollar Profit Factor** | **1.68** (vs V3.0-A's 1.06) | **1.38** (vs V3.0-B's 1.08) |
| **Win Rate** | **58.3%** (vs V3.0-A's 52.8%) | **55.4%** (vs V3.0-B's 52.8%) |
| **Calmar Ratio** | **0.51** (vs V3.0-A's 0.04 — 12.8x improvement) | **0.75** (vs QQQ B&H 0.60, V3.0-B 0.69) |
| **Total 5Y Slippage** | **$658.22** | **$2,190.24** (88.4% cost savings vs V3.0-B's $18,846.62) |

### Comprehensive 5-Year Full-Cycle Comparative Metrics Matrix (2021-10-29 ~ 2026-10-28, 1,194 Trading Days)

| Performance & Risk Metric | V2.4 Baseline (Plan A) | V3.0-A Aggressive | V3.0-B QQQ Parking (Honest)* | **V3.1 Track 1 (Pure Stock)** | **V3.1 Track 2 (Flagship Honest)** | QQQ Buy & Hold Benchmark |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Initial Capital** | $100,000.00 | $100,000.00 | $100,000.00 | **$100,000.00** | **$100,000.00** | $100,000.00 |
| **Final Equity** | $111,827.57 | $104,856.66 | $166,451.33 | **$130,441.41** | **$187,910.80** | $216,696.43 |
| **Net Profit ($)** | +$11,827.57 | +$4,856.66 | +$66,451.33 | **+$30,441.41** | **+$87,910.80** | +$116,696.43 |
| **CAGR (%)** | +2.39% | +1.01% | +11.35% | **+5.77%** | **+14.24%** | +17.73% |
| **Max Drawdown (MDD)** | -6.09% | -24.99% | -21.56% | **-11.31%** | **-19.03%** | -29.56% |
| **Dollar Profit Factor** | 1.24 | 1.06 | 1.08 | **1.68** | **1.38** | N/A |
| **Win Rate (%)** | 55.0% | 52.8% | 52.8% | **58.3%** | **55.4%** | N/A |
| **Total Closed Trades** | 120 | 127 | 127 | **84** | **74** | 1 |
| **Calmar Ratio** | 0.39 | 0.04 | 0.53 | **0.51** | **0.75** | 0.60 |
| **Cumulative Slippage Drag** | $4,105.17 | $7,884.26 | $17,904.95 | **$658.22** | **$2,190.24** | $0.00 |
| **Double-Entry Balance** | ✅ $0.01 | ✅ $0.01 | ✅ $0.00 | ✅ **$0.00** | ✅ **$0.00** | ✅ $0.00 |

*\*Note on V3.0-B: Under legacy timing with same-day close leak, V3.0-B was reported as $182,075.73 (+13.48% CAGR, -19.48% MDD). Under strict real-time execution, V3.0-B achieves $166,451.33 (+11.35% CAGR, -21.56% MDD). Track 2 outperforms both baselines (+$21,459 higher net wealth) while slashing slippage friction by $15,714 (87.8% cost savings).*

---

## 2. Firstrade Brokerage Friction & Execution Modeling

### 2.1 Fee Structure & Zero Commission Verification
- **Equity & ETF Commissions**: **$0.00** per trade across all online US equity transactions.
- **Regulatory Pass-Through Surcharges (Sell Orders Only)**:
  - *SEC Section 31 Transaction Fee*: $0.0000278 per dollar of gross sales proceeds ($0.56 on a $20,000 position, or 0.28 bps).
  - *FINRA Trading Activity Fee (TAF)*: $0.000166 per share (capped at $8.30 per transaction).
  - *Total Sell Regulatory Friction*: Combined < 0.4 bps, effectively negligible for daily swing horizons.
- **Contractual Model**: In the V3.1 engine, commission is modeled at `$0.00`, and trading friction is calibrated to **1.5 basis points (`0.00015` or 0.015%)** per trade leg on mega-caps and ETFs.

### 2.2 Liquidity & Bid-Ask Spread Calibration
- The strategy universe is concentrated in Tier 1 US Mega-Caps (`NVDA`, `TSLA`, `AAPL`, `MSFT`, `AMZN`, `AMD`, `META`, `GOOGL`, `AVGO`, `TSM`) and `QQQ`.
- Daily trading volume on these names ranges from **$3 Billion to $24 Billion per day**.
- Typical retail order sizes ($15,000 to $30,000) represent less than **0.0001%** of daily dollar volume.
- The average half-spread on mega-caps is $0.005 to $0.01 (0.12 to 0.42 bps). At market open, effective auction slippage averages **1.0 to 2.0 bps**, establishing **1.5 bps** as the conservative institutional calibration standard.

### 2.3 Cash Sweep & Yield Management
- Uninvested cash in Firstrade accounts is automatically swept into FDIC-insured partner banks or can be deployed into ultra-short US Treasury ETFs (e.g. `SGOV` or `BIL`).
- In Track 1 (pure stock), idle cash is held risk-free during market corrections.
- In Track 2 (flagship), idle cash is swept into `QQQ` whenever QQQ trades above its SMA200, achieving near-zero cash drag during bull cycles.

---

## 3. Portfolio Allocation & Position Sizing Engine

### 3.1 Position Sizing Formulas (Risk Parity with Slot Cap)
Every new stock allocation is sized dynamically according to **Account Risk Parity**:

$$\text{Structural Stop Distance (\%)} = \frac{\text{Fill Price} - \text{Stop Level}}{\text{Fill Price}}$$

$$\text{Effective Stop Distance} = \max(\text{Structural Stop Distance}, 0.035)$$

$$\text{Risk Dollars} = \text{Total Portfolio Equity} \times \text{Account Risk Pct (1.5\%)}$$

$$\text{Target Notional} = \min\left(\text{Total Portfolio Equity} \times \text{Max Position Pct (25\%)}, \frac{\text{Risk Dollars}}{\text{Effective Stop Distance}}\right)$$

$$\text{Allocated Capital} = \min(\text{Target Notional}, \text{Available Cash})$$

### 3.2 Allocation Parameters Summary

| Parameter | Track 1 (Pure Stock) | Track 2 (Flagship QQQ Base) | Rationale |
| :--- | :---: | :---: | :--- |
| **Max Concurrent Stock Slots** | **4** | **3** | Diversification without dilution |
| **Max Position Size** | **25.0%** | **25.0%** | Caps downside of any single gap-down |
| **Account Risk Budget / Trade** | **1.50%** | **1.50%** | Prevents drawdowns from loss clusters |
| **Structural Stop Cap** | **-6.80%** | **-6.80%** | Hard circuit breaker on volatility |
| **Minimum Stop Distance** | **3.50%** | **3.50%** | Avoids over-sizing on tight ATR |
| **Minimum Trade Size** | **$200.00** | **$200.00** | Filters out trivial residual orders |
| **QQQ Parking Cash Threshold** | N/A | **$1,000.00** | Minimizes unnecessary micro-sweeps |
| **QQQ Parking Cash Buffer** | N/A | **$500.00** | Retains buffer for settlement fees |

---

## 4. Entry Gates & Signal Selection Protocols

### 4.1 Macro Weather & Regime Gates (Strict Timing Horizon Partitioning)
To ensure complete live execution causality and zero look-ahead bias, macro indicators are strictly bifurcated across two distinct trading horizons:
1. **Morning Open Macro Weather Gate (`is_rainstorm_open` at 9:30 AM ET)**:
   - Evaluated strictly using historical data known prior to market open ($T-1$ Close):
     $$\text{Bias\_MA50}_{t-1}(QQQ) = \frac{Close_{t-1} - MA50_{t-1}}{MA50_{t-1}} \times 100$$
     $$\text{is\_rainstorm\_open} = (\text{Bias\_MA50}_{t-1}(QQQ) < -3.0\% \text{ AND } \text{Bias\_MA50}_{t-1}(SMH) < -3.0\%)$$
   - *Action at 9:30 AM*:
     - In Track 2, if `is_rainstorm_open == True` OR $Open_t(QQQ) < SMA200_{t-1}(QQQ)$, **liquidate 100% of parked QQQ shares immediately to cash**.
     - Idle cash parking sweep into QQQ is strictly prohibited while `is_rainstorm_open == True`.
2. **End-of-Day Macro Weather Gate (`is_rainstorm_close` at 4:00 PM ET)**:
   - Evaluated using today's confirmed closing prices ($T$ Close):
     $$\text{Bias\_MA50}_{t}(QQQ) = \frac{Close_t - MA50_t}{MA50_t} \times 100$$
     $$\text{is\_rainstorm\_close} = (\text{Bias\_MA50}_{t}(QQQ) < -3.0\% \text{ AND } \text{Bias\_MA50}_{t}(SMH) < -3.0\%)$$
   - *Action at 4:05 PM*:
     - If `is_rainstorm_close == True`, **all new stock candidate scanning is halted** (no buy orders queued for $T+1$ open).
3. **QQQ MA50 Regime Gate (The Core Drawdown Compressor)**:
   - Condition: $Close_t(QQQ) \ge MA50_t(QQQ)$.
   - *Action*: If $Close_t(QQQ) < MA50_t(QQQ)$, **all new stock buy orders are suspended**. This single filter eliminates 43 low-probability counter-trend trades during market corrections, compressing drawdowns from -24.98% to -11.31%.

### 4.2 Candidate Screening Signals
Candidates are selected from the 32-stock high-momentum universe (`AGGRESSIVE_STOCKS`):
- **Crash Exclusion Filter**: Any stock experiencing a 1-day drop $\le -6.0\%$ or overnight gap-down $\le -4.5\%$ within the past 5 sessions (`Crash_5d == True`) is immediately disqualified.
- **Signal 1: Golden Pit Right-Turn (`GOLDEN_PIT`)**:
  - *Long-Term Trend*: $Close > SMA200$ AND $MA50 \ge SMA200 \times 0.97$ AND $RS_{60} \ge -8.0\%$.
  - *Fibonacci Retracement*: Depth within 50%–61.8% sweet spot ($-0.20 \le Fib\_Pos \le 2.20$).
  - *Oversold Filter*: $Min\_RSI_{3d} \le 45.0$ AND $RSI_{14} \le 50.0$.
  - *Volatility Constraint*: $ATR\_Pct \le 5.2\%$.
  - *Right-Turn Candle Confirmation*: Real body $\ge +0.5\%$ AND ($Close > High_{t-1}$ OR $Close > EMA5$).
- **Signal 2: Deep Oversold Bounce (`OVERSOLD`)**:
  - If $Close > SMA200$: $Min\_RSI_{3d} \le 32.0$ AND $Close > Open$ AND $Close > Close_{t-1} \times 1.015$ AND $Volume\_Ratio \ge 1.15$.
  - If $Close \le SMA200$: $Min\_RSI_{3d} \le 28.0$ AND $Close > Open$ AND $Close > Close_{t-1} \times 1.015$ AND $Volume\_Ratio \ge 1.15$.

### 4.3 Candidate Priority Ranking
When multiple signals trigger on the same day and available portfolio slots are limited:
$$\text{Rank Priority} = (\text{Relative Strength } RS_{60} \downarrow, \text{RSI}_{14} \uparrow)$$
*Rationale*: Strongest multi-month leaders ($RS_{60}$) experiencing the cleanest short-term pullback (lowest RSI) receive priority capital allocation.

---

## 5. Order Execution Mechanics & Firstrade Order Types

### 5.1 T+1 NEXT_OPEN Execution Discipline
All trading decisions are computed at EOD on confirmed daily closes and executed at the **Next Market Open (9:30:00 AM ET)**:
1. **Execution Priority**:
   - `SELL_EXIT` (Stop-loss, profit-taking, trailing exit) orders execute **first**.
   - `TRIM_50` (Resistance reduction) orders execute **second**.
   - `BUY` orders execute **third**, utilizing released capital.
2. **Next-Open Gap Filter**:
   Before executing a staged Buy order at 9:30 AM:
   - Cancel if $Open < Signal\_Low$ (support broken before open).
   - Cancel if $Open < Signal\_Close \times 0.985$ (overnight gap-down > 1.5%).
   - Cancel if $Open > Signal\_Close \times 1.020$ (overnight gap-up > 2.0%, avoiding FOMO chasing).

### 5.2 Firstrade Order Type Specifications
- **Opening Orders**:
  - **Market-On-Open (MOO)**: Use for primary entry and exit orders.
  - **Important Firstrade Rule**: On Firstrade, MOO orders must be entered prior to **9:28 AM ET**. Orders submitted after 9:28 AM ET become regular market orders and may experience opening-auction dispersion.
  - **Limit-On-Open (LOO)**: For volatile mid-caps, enter LOO with limit set to $Signal\_Close \times 1.020$ to enforce the gap-up filter automatically.
- **Intraday Stop-Loss Orders**:
  - Once filled, submit a **Good-'Til-Canceled (GTC) Stop Order** on Firstrade at the designated `stop_level`.
  - For Tier 2 and Tier 3 profit locks, update the GTC Stop Order to the new `be_stop` price.

---

## 6. Risk Management, Dynamic Stops & Profit-Locking

### 6.1 Structural Stop-Loss Protection
- Initial structural stop level:
  $$\text{struct\_stop} = \min(Signal\_Low - 0.5 \times ATR, Fill\_Price - 1.8 \times ATR)$$
  $$\text{stop\_level} = \max(\text{struct\_stop}, Fill\_Price \times 0.932)$$
- Intraday execution rules:
  - If $Open \le stop\_level$: Filled at $Open \times (1 - \text{slippage})$ (`🔴 结构熔断止损(跳空开盘)`).
  - Else if $Low \le stop\_level$: Filled at $stop\_level \times (1 - \text{slippage})$ (`🔴 结构熔断止损`).

### 6.2 Three-Tier Profit-Locking Engine
Starting on holding day 1 ($days\_held \ge 1$), the high-water mark peak price is tracked daily:

| Tier | Activation Threshold | Locked Stop Level (`be_stop`) | Protection Purpose |
| :---: | :--- | :--- | :--- |
| **Tier 1** | $Max\_PnL \ge +4.2\%$ OR $Peak \ge Entry + 2.0 \times ATR$ | $Entry \times 1.005$ (+0.5%) | Guarantees trade cannot turn into a loss |
| **Tier 2** | $Max\_PnL \ge +8.0\%$ | $Entry \times 1.025$ (+2.5%) | Locks in base operational gain |
| **Tier 3** | $Max\_PnL \ge +14.0\%$ | $Entry \times 1.060$ (+6.0%) | Secures meaningful profit while riding trend |

---

## 7. Trailing Stops & Runner Upside Harvesting

V3.1 permanently retains V3.0's uncapped runner architecture, enabling full capture of multi-week parabolic momentum waves:

```
               ▲ Price
               │                             🎉 极度冲顶清仓 (RSI >= 82)
               │                                      /\
               │                                     /  \
               │                                    /    \
               │                                   /      \
               │                 🟣 疯牛跌破EMA10 /        ▼
               │                   (Bias >= 12%) /
               │                                /
               │              🟣 常规跌破EMA20 /
               │                 (Close < EMA20)
               │                               /
               │               🛡️ 阶梯锁利 (+6%)
               │                             /
               │             🛡️ 阶梯锁利 (+2.5%)
               │                           /
               │           🛡️ 保本线 (+0.5%)
               │                         /
               │   ─── 结构止损 (-6.8%)
               └────────────────────────────────────────────────► Time
```

### 7.1 Uncapped Climax Exit (`🎉 极度冲顶清仓止盈`)
- Trigger: $RSI_{14} \ge 82.0$ OR ($RSI_{14} \ge 76.0$ AND $Bias\_MA50 \ge 20.0\%$ AND $is\_stall$ AND $cur\_pnl \ge 10.0\%$).
- *Action*: Liquidate 100% position at next market open. Harvested AMD at +37.86% and AVGO at +22.60%.

### 7.2 EMA10 / EMA20 Trailing Stops (The Big Winner Engine)
When $cur\_pnl \ge +5.0\%$ or $max\_pnl \ge +8.0\%$:
- **Crazy Bull Mode (`is_crazy == True`)**:
  Triggered if $Max\_Bias_{5d} \ge 12.0\%$ OR $Max\_RSI_{5d} \ge 72.0$ OR $max\_pnl \ge 12.0\%$.
  *Exit Trigger*: First daily close below **EMA10** ($Close < EMA10 \text{ and } Close_{t-1} \ge EMA10_{t-1}$) $\rightarrow$ `🟣 疯牛跌破EMA10移动止盈`. Harvested ASML at +30.90%.
- **Regular Trend Mode (`is_crazy == False`)**:
  *Exit Trigger*: First daily close below **EMA20** ($Close < EMA20 \text{ and } Close_{t-1} \ge EMA20_{t-1} \text{ and } Close > MA50$) $\rightarrow$ `🟣 常规跌破EMA20移动止盈`.

### 7.3 Breakdown & Time Stops
- **Volume Breakdown Stop**: If not trimmed, $Entry \ge Fib\_Low \times 0.98$, $Close < Fib\_Low \times 0.965$, $Vol\_Ratio \ge 1.20$, and $Close \le Close_{t-1}$ $\rightarrow$ `🔴 放量破位止损`.
- **10-Day Time Stop**: If not trimmed, $bars\_held \ge 10$, $cur\_pnl < -2.0\%$, and $Close < EMA20$ $\rightarrow$ `⏳ 10日破位时间止损`.
- **Underwater Rebound Trim 50%**: If $Entry < MA50$, on reaching resistance ($Fib\_Low \times 0.99$ or $MA50 \times 0.995$ or $+6.0\%$) with stall $\rightarrow$ `🟣 触及强阻减半仓`.

---

## 8. Daily Operational SOP Checklist for Firstrade

### Phase 1: Post-Market Routine (4:05 PM – 5:00 PM ET)
- [ ] **Run Quantitative Daily Engine**: Execute scanner across all 32 tickers to update rolling indicators (MA50, SMA200, EMA10/20, RSI, Fib).
- [ ] **Audit Macro Regime (EOD Close Horizon)**:
  - Check QQQ vs MA50: Is $Close(QQQ) \ge MA50$? (If NO $\rightarrow$ Stock buy gate is **LOCKED**).
  - Check EOD Macro Rainstorm (`is_rainstorm_close`): Are QQQ and SMH both $Bias\_MA50 < -3.0\%$? (If YES $\rightarrow$ Freeze new buy candidate queuing).
  - Check QQQ vs SMA200: Is $Close(QQQ) < SMA200$? (If YES $\rightarrow$ Stage QQQ bear defense liquidation for morning open).
- [ ] **Audit Active Positions**:
  - Check for Climax Exits ($RSI \ge 82$).
  - Check for Trailing Stop breaks ($Close < EMA10$ for crazy bull, $Close < EMA20$ for regular trend).
  - Check for Time Stops (10 bars held, $PnL < -2\%$).
  - Update `be_stop` levels for Tier 1 (+0.5%), Tier 2 (+2.5%), or Tier 3 (+6.0%) locks.
- [ ] **Queue Pending Orders**: Generate exact share counts and ticker lists for next morning's orders.

### Phase 2: Pre-Market Routine (8:30 AM – 9:25 AM ET)
- [ ] **Check Overnight Developments**: Review US index futures (NQ, ES) and pre-market earnings releases.
- [ ] **Audit Morning Open Macro Weather (`is_rainstorm_open`)**:
  - Verify $T-1$ Close Macro Weather: If $Bias\_MA50_{t-1}(QQQ) < -3\%$ and $Bias\_MA50_{t-1}(SMH) < -3\%$, flag `is_rainstorm_open = True`.
- [ ] **Verify Next-Open Gap Filter**:
  - Check pre-market quote for buy candidates: Is $Price \le Signal\_Close \times 1.020$? (If gapping up > 2%, cancel buy order).
  - Is $Price \ge Signal\_Close \times 0.985$ and $Price \ge Signal\_Low$? (If gapping down > 1.5%, cancel buy order).
- [ ] **Submit Orders on Firstrade (Deadline: 9:28 AM ET)**:
  - Submit `SELL_EXIT` orders via **Market-On-Open (MOO)**.
  - Submit `BUY` orders via **Market-On-Open (MOO)** or **Limit-On-Open (LOO)** with cap at $+2.0\%$.
  - (Track 2) If QQQ Open < SMA200 or `is_rainstorm_open == True`, submit QQQ liquidation MOO order.

### Phase 3: Market Open Execution (9:30 AM – 9:45 AM ET)
- [ ] **Verify Opening Fills**: Confirm executions on Firstrade platform; record executed fill prices.
- [ ] **Establish GTC Stop Orders**:
  - For each newly filled position, submit a **Good-'Til-Canceled (GTC) Stop Order** at `stop_level`.
- [ ] **Execute Track 2 Cash Parking**:
  - If Track 2 is active, market is above SMA200, and idle cash exceeds $1,000:
  - Buy QQQ shares using `Cash - $500` at current market price.
- [ ] **Update Trading Journal & Ledger**: Verify double-entry ledger balance:
  $$\text{Cash} + \text{Stock Market Value} + \text{QQQ Market Value} = \text{Total Portfolio Equity}$$

### Phase 4: Intraday & Market Close (9:45 AM – 4:00 PM ET)
- [ ] **Intraday Alerts**: Monitor automated price alerts for stop-loss or tiered lock triggers.
- [ ] **3:55 PM ET Spot-Check**: Review candidates nearing potential EMA10/EMA20 trailing stop breaks to anticipate post-market orders.

---

## 9. Independent Audit & Invariant Verification Standards

To guarantee mathematical integrity in production:
1. **Double-Entry Invariant**: $\Delta \text{Discrepancy} = |\text{Equity} - (\text{Cash} + \text{Invested})| \equiv \$0.0000$.
2. **Firstrade Cost Invariant**: Total round-trip commissions must equal **$0.00**. Cumulative slippage must strictly track $1.5\text{ bps} \times \text{Traded Volume}$.
3. **Guardrail Standard**: Under Track 1, maximum historical drawdown must not exceed **-15.0%** (achieved **-11.31%**). Under Track 2, Calmar ratio must exceed **0.70** (achieved **0.75**, outperforming QQQ Buy & Hold at 0.60 and V3.0-B at 0.69) with maximum drawdown compressed to **-19.03%** (vs QQQ's -29.56%).
