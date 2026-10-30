# V3.1 Ultimate Edition Test Infrastructure Specification (TEST_INFRA.md)

## 1. Overview & Testing Philosophy

The V3.1 Ultimate Edition quantitative trading system upgrades from V2.4 / V3.0 to provide a production-ready, empirically verified algorithmic trading architecture calibrated specifically for execution on Firstrade ($0 commission, 1~2 bps mega-cap slippage).

This document establishes the official test architecture, verification methodology, and acceptance criteria for the entire V3.1 system.

### Core Testing Principles
1. **Opaque-Box Requirement-Driven Testing**: Tests are derived strictly from specifications in `ORIGINAL_REQUEST.md` and `PROJECT.md`. They treat trading logic as an opaque-box system validated through inputs, observable behaviors, trade logs, and portfolio equity states.
2. **Authoritative Ground Truth & Verification Oracles**:
   - Historical market data: 5-year cached daily dataset (`us-market-radar/.backtest_5y_cache.pkl`, 1,194 trading days from 2021 to 2026).
   - Friction model: Firstrade live execution specifications ($0 commission, 1~2 bps slippage on mega-cap equities and ETFs).
   - Reference baseline: V2.4, V3.0-A ($143,450 equity, -24.98% MDD, 0.54 Calmar), V3.0-B ($182,075 equity, -19.48% MDD, 0.69 Calmar), and QQQ Buy & Hold.
3. **Strict Double-Entry Accounting Invariants**: Every backtest run must maintain mathematically closed balance sheets with zero tolerance for phantom capital, unmodeled slippage, or negative cash borrowing.
4. **Progressive Testability & Isolation**: Tests are structured into independent, self-contained units that can be executed in isolation or as a unified comprehensive suite.

---

## 2. Quantitative Accounting Invariants & Guardrails

The test harness enforces five non-negotiable quantitative invariants across all simulated trading bars:

### Invariant 1: Daily Balance Sheet Solvency
$$\text{Equity}_t = \text{Cash}_t + \sum_{i} \text{Shares}_{i,t} \times \text{Close}_{i,t} + \text{QQQ\_Shares}_t \times \text{QQQ\_Close}_t$$
Tolerance: $|\Delta| \le \$0.01$ (to allow for IEEE 754 floating point rounding).

### Invariant 2: No-Margin Solvency (Cash Non-Negativity)
$$\text{Cash}_t \ge 0.00 \quad \forall t$$
Firstrade cash accounts do not permit negative cash balances. Any buy order requiring more than available cash must be scaled down or rejected. In Track 2 parking mode, QQQ liquidation proceeds must settle prior to stock allocation.

### Invariant 3: Firstrade Friction Invariance
$$\text{Total Friction} = \sum_{\text{all trades}} \left| \text{Fill Price} - \text{Base Price} \right| \times \text{Shares}$$
$$\text{Total Commission} = \$0.00$$
All mega-cap stock and QQQ ETF trades must incur zero dollar commissions and exactly the calibrated slippage rate (default $1.5\text{ bps} = 0.00015$).

### Invariant 4: Capital Conservation & PnL Consistency
$$\text{Final Equity} - \text{Initial Capital} = \sum_{\text{closed trades}} \text{PnL}_{\text{dollar}} + \text{Unrealized PnL}_{\text{open}} - \text{Total Friction}$$

### Invariant 5: Realistic Execution & Gap-Down Bypass
- Standard Buy Fill: $\text{Price}_{\text{fill}} = \text{Open}_t \times (1 + \text{slippage})$
- Standard Sell Fill: $\text{Price}_{\text{fill}} = \text{Open}_t \times (1 - \text{slippage})$
- Stop Trigger Fill: If $\text{Open}_t < \text{Stop Level}$, order fills at $\text{Open}_t \times (1 - \text{slippage})$ (modeling overnight gap-down reality), NOT at the theoretical stop price.

---

## 3. Four-Tier Test Architecture

The test suite is structured across 4 rigorous tiers:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        4-TIER TEST ARCHITECTURE                        │
├────────────────────────────────────────────────────────────────────────┤
│ Tier 1: Feature Coverage (>= 5 tests per feature)                      │
│   • F1: Firstrade Friction Calibration                                 │
│   • F2: Pure Stock Dynamic Risk Track (Track 1)                        │
│   • F3: QQQ Base Enhancement Track (Track 2)                           │
│   • F4: Runner Upside Preservation & Trailing Stops                    │
├────────────────────────────────────────────────────────────────────────┤
│ Tier 2: Boundary & Corner Cases (>= 5 tests per category)              │
│   • C1: Empty Signals & Missing Data                                   │
│   • C2: Extreme Slippage & Severe Price Gaps                           │
│   • C3: Capital Limits, Zero Cash & Allocation Saturation              │
│   • C4: Macro Storm & Severe Regime Transitions                        │
├────────────────────────────────────────────────────────────────────────┤
│ Tier 3: Cross-Feature Combinations                                     │
│   • Multi-feature interactions: Sell-priority sweep, regime flips      │
├────────────────────────────────────────────────────────────────────────┤
│ Tier 4: Real-World 5-Year Historical Scenarios                         │
│   • 2022 Bear protection, 2023 consolidation, 2024-2026 AI bull wave    │
│   • Verification of all acceptance criteria & Calmar ratio targets     │
└────────────────────────────────────────────────────────────────────────┘
```

---

### Tier 1: Feature Coverage Specification (>= 5 Tests Per Feature)

#### Feature 1: Firstrade Friction Calibration
- `test_f1_1_zero_commission_buy`: Verifies buy transactions execute with exactly $0.00 commission.
- `test_f1_2_zero_commission_sell`: Verifies sell transactions execute with exactly $0.00 commission.
- `test_f1_3_megacap_buy_slippage_15bps`: Verifies buy fill price is exactly $P_{\text{open}} \times (1 + 0.00015)$.
- `test_f1_4_megacap_sell_slippage_15bps`: Verifies sell fill price is exactly $P_{\text{open}} \times (1 - 0.00015)$.
- `test_f1_5_slippage_cost_accumulation`: Verifies total recorded transaction slippage matches sum of trade slippages.
- `test_f1_6_configurable_slippage_range`: Verifies engine correctly accepts slippage rates within the Firstrade mega-cap range ($1.0 \sim 2.0\text{ bps}$).

#### Feature 2: Pure Stock Dynamic Risk Track (Track 1)
- `test_f2_1_qqq_ma50_regime_gate_blocks_buys`: When QQQ close < MA50, candidate stock buys are strictly blocked.
- `test_f2_2_qqq_ma50_regime_gate_allows_buys`: When QQQ close >= MA50 and market weather is clear, qualifying stock buys are admitted.
- `test_f2_3_slot_cap_max_4_positions`: Maximum concurrent stock holdings cannot exceed 4.
- `test_f2_4_position_notional_cap_25pct`: No single stock position allocation can exceed 25% of current equity.
- `test_f2_5_risk_budget_sizing_15pct`: Position sizing calculates target notional via $1.5\%$ account risk divided by stop distance.
- `test_f2_6_structural_stop_cap_68pct`: Stop level is anchored by structural ATR support and capped at a maximum of $-6.8\%$ from entry.

#### Feature 3: QQQ Base Enhancement Track (Track 2)
- `test_f3_1_core_qqq_parking_allocation`: When cash is idle and QQQ > SMA200, idle funds (above $500 buffer) are automatically parked in QQQ.
- `test_f3_2_qqq_bear_defense_liquidation`: When QQQ drops below SMA200 or macro rainstorm hits, parked QQQ is liquidated to cash at next open.
- `test_f3_3_satellite_slot_cap_max_3`: Stock satellite allocation strictly limits concurrent stock positions to 3.
- `test_f3_4_dynamic_sweep_liquidity_unlock`: When qualifying stock buy signals emerge and cash is insufficient, QQQ shares are sold at market open to fund stock allocation.
- `test_f3_5_no_duplicate_ticker_allocation`: Satellite stock purchase is rejected if ticker is already active or queued.
- `test_f3_6_cash_buffer_preservation`: QQQ parking sweeps always preserve the specified cash reserve ($500) to prevent account freeze.

#### Feature 4: Big Runner Preservation & Trailing Stops
- `test_f4_1_breakeven_stop_activation`: When position profit reaches $\ge +4.2\%$ or $\ge 2.0 \times \text{ATR}$, break-even stop is raised to entry $\times 1.005$.
- `test_f4_2_tier1_profit_lock_8pct`: When position profit reaches $\ge +8.0\%$, trailing floor is elevated to entry $\times 1.025$ ($+2.5\%$ profit lock).
- `test_f4_3_tier2_profit_lock_14pct`: When position profit reaches $\ge +14.0\%$, trailing floor is elevated to entry $\times 1.060$ ($+6.0\%$ profit lock).
- `test_f4_4_crazy_bull_ema10_runner_exit`: When stock exhibits climax momentum ($\ge 12\%$ bias, $\ge 72$ RSI, or $\ge 12\%$ gain), position trails on EMA10 and exits when close drops below EMA10.
- `test_f4_5_standard_bull_ema20_runner_exit`: In normal bull trends ($\ge 5\%$ gain), position trails on EMA20 and exits when close drops below EMA20 (while above MA50).
- `test_f4_6_extreme_climax_rsi82_full_exit`: When RSI $\ge 82$ or extreme stall divergence occurs, full position is liquidated immediately at next open.

---

### Tier 2: Boundary & Corner Cases (>= 5 Tests Per Category)

#### Category 1: Empty Signals & Missing Data
- `test_c1_1_zero_signals_idle_cash`: When 0 buy candidates trigger, Track 1 maintains 100% cash and Track 2 holds parked QQQ without error.
- `test_c1_2_missing_ticker_in_indicators`: Tickers missing from daily indicator cache are gracefully bypassed without KeyError.
- `test_c1_3_zero_volatility_flat_bars`: Zero price movement bars (Open=High=Low=Close) compute indicators without ZeroDivisionError.
- `test_c1_4_empty_portfolio_on_first_bar`: Initial startup conditions handle zero positions and zero pending orders cleanly.
- `test_c1_5_unknown_ticker_order_queue`: Order queue handles unlisted or delisted ticker requests with graceful rejection.

#### Category 2: Extreme Slippage & Severe Price Gaps
- `test_c2_1_severe_gap_down_below_stop`: Overnight gap-down below stop fills at $\text{Open} \times (1 - \text{slippage})$, accurately absorbing slippage gap loss.
- `test_c2_2_severe_gap_up_buy_rejection`: Overnight gap-up $> +2.0\%$ above signal close aborts buy order to prevent emotional chasing.
- `test_c2_3_severe_gap_down_buy_rejection`: Overnight gap-down $< -1.5\%$ below signal close or below signal low aborts buy order.
- `test_c2_4_be_stop_gap_down_fill`: Break-even stop trigger fills at $\text{Open} \times (1 - \text{slippage})$ when opening below break-even level.
- `test_c2_5_zero_slippage_boundary`: Engine operates stably with slippage rate set to exactly 0.00000.

#### Category 3: Capital Limits, Zero Cash & Allocation Saturation
- `test_c3_1_insufficient_cash_buy_rejection`: Buy orders requiring capital when cash $< \$200$ are rejected; no fractional micro-lot created.
- `test_c3_2_full_slots_saturation`: When all 4 slots (Track 1) or 3 slots (Track 2) are filled, subsequent buy signals are safely ignored.
- `test_c3_3_exact_qqq_shares_sweep`: In Track 2, QQQ shares sold to fund a stock purchase match the needed dollars without excess liquidation.
- `test_c3_4_capital_scaling_invariance`: Running simulations with \$10k, \$100k, and \$1M yields identical percentage returns and drawdowns.
- `test_c3_5_fractional_share_precision`: Float share quantities maintain double-entry balance sheet accuracy without drift over 1,000+ bars.

#### Category 4: Macro Storm & Severe Regime Transitions
- `test_c4_1_macro_rainstorm_all_buys_blocked`: Rainstorm condition (QQQ Bias_MA50 < -3.0 and SMH Bias_MA50 < -3.0) halts all new equity entries.
- `test_c4_2_macro_rainstorm_triggers_qqq_exit`: Rainstorm immediately triggers liquidation of parked QQQ at next open.
- `test_c4_3_stock_crash_5d_exclusion`: Stocks exhibiting 5-day flash crash flag are excluded from candidate scanner.
- `test_c4_4_regime_whipsaw_oscillation`: QQQ price oscillating across SMA200 does not cause duplicate simultaneous orders on the same day.
- `test_c4_5_simultaneous_stop_and_macro_exit`: Stock stop-loss and macro exit triggering on the same day execute cleanly without double-sell error.

---

### Tier 3: Cross-Feature Combinations

- `test_x1_track1_macro_storm_position_isolation`: Track 1 holding 2 stocks enters a macro rainstorm; existing positions continue to run trailing stops while new buys are completely halted.
- `test_x2_track2_execution_priority_sweep`: On T+1 open, pending SELL_EXIT orders, TRIM_50 orders, QQQ sweep sales, and BUY orders execute in strict sequence (Sells -> Sweeps -> Buys), ensuring cash availability.
- `test_x3_runner_exit_during_regime_flip`: Stock runner triggers EMA10 trailing stop on the exact bar QQQ drops below MA50; sell order executes at next open without conflict.
- `test_x4_quadruple_simultaneous_stop_out`: 4 active positions hit stop-loss simultaneously; all 4 orders execute at next open, returning account to 100% cash with exact balance sheet balance.
- `test_x5_multistage_runner_lifecycle`: Single trade transitions across full lifecycle (Entry -> Break-even -> Tier 1 Lock (+8%) -> Tier 2 Lock (+14%) -> EMA10 Climax Exit) over 30+ simulated trading bars.

---

### Tier 4: Real-World 5-Year Historical Scenarios (`.backtest_5y_cache.pkl`)

- `test_h1_full_5y_execution_clean`: Unified backtest across 1,194 trading days completes with zero runtime exceptions.
- `test_h2_2022_bear_market_drawdown_reduction`: Track 1 Max DD $\le -15.0\%$ (mitigating V3.0-A's $-24.98\%$), Track 2 Max DD $\le -15.0\%$ (mitigating V3.0-B's $-19.48\%$). Both drastically outperform QQQ 2022 Bear Market ($-33\%$).
- `test_h3_2023_tech_consolidation_capital_preservation`: Regime gates suppress false breakout whipsaws during 2023 choppy market.
- `test_h4_2024_2026_ai_bull_wave_runner_harvesting`: Breakout gains are fully preserved on mega-cap AI leaders:
  - AMD: Closed trade gain $\ge +30.0\%$
  - ASML: Closed trade gain $\ge +25.0\%$
  - AVGO: Closed trade gain $\ge +20.0\%$
- `test_h5_calmar_ratio_outperformance`:
  - Track 1 Calmar Ratio $\ge 0.50$ (higher than V3.0-A with reduced MDD).
  - Track 2 Calmar Ratio $\ge 2.00$ (far superior to QQQ Buy & Hold and V3.0-B).
- `test_h6_5y_double_entry_accounting_audit`: Daily solvency, cash non-negativity, friction tracking, and zero commission invariants hold 100% across all 1,194 historical days.

---

## 4. Test Execution & Reporting Harness

The automated test runner is encapsulated in `test_e2e_v31.py`:
- Command-line execution: `python test_e2e_v31.py`
- Modular test selection: `python test_e2e_v31.py --tier 1`, `python test_e2e_v31.py --tier 4`, etc.
- Detailed visual terminal output with ANSI colors, progress status, and pass/fail summary tables.
- Machine-readable test ledger export to `test_e2e_report.json`.

---

## 5. Pass / Fail Acceptance Criteria

| Criteria ID | Target Metric / Requirement | Threshold / Pass Condition | Authoritative Source |
|---|---|---|---|
| **AC-1** | 5Y Backtest Execution | 100% error-free execution across 1,194 days | `ORIGINAL_REQUEST.md` § Acceptance Criteria |
| **AC-2** | Firstrade Friction Calibration | $0 commission, 1.5 bps slippage on all orders | `ORIGINAL_REQUEST.md` § R2 |
| **AC-3** | Track 1 Max Drawdown Mitigation | Max Drawdown $\le -15.0\%$ (vs V3.0-A $-24.98\%$) | `PROJECT.md` § Milestone M3 |
| **AC-4** | Track 2 Calmar Ratio Outperformance | Calmar Ratio $\ge 2.00$ and beats QQQ B&H | `PROJECT.md` § Milestone M3 |
| **AC-5** | Big Runner Breakout Preservation | AMD $> 30\%$, ASML $> 25\%$, AVGO $> 20\%$ captured | `ORIGINAL_REQUEST.md` § R1 |
| **AC-6** | Accounting Balance Invariance | Solvency, cash $\ge 0$, zero commission | `TEST_INFRA.md` § Invariants |
| **AC-7** | Multi-Tier Test Suite Pass Rate | 100% Pass across all active test cases | `DISPATCH.md` |
