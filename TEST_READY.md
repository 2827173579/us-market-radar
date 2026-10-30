# TEST_READY: V3.2 Circuit Breaker & V3.1 Execution Test Suites

**Milestone**: M0 (E2E & Unit Test Suites Creation)  
**Author**: `teamwork_test_writer_m0`  
**Date**: 2026-10-31  
**Target Repository**: `C:\Users\User\.gemini\antigravity-ide\scratch\us-market-radar`

---

## 1. Executive Summary

Milestone M0 establishes comprehensive, automated verification oracles for the V3.2 Crash Circuit Breaker Upgrades and V3.1 Live Market Open Execution Alignment. Two new test suites have been created and validated against the baseline codebase:

1. **`test_v32_circuit_breaker.py`**: A 14-test unit and scenario test suite in Python (`unittest`) verifying all 4 V3.2 crash circuit breaker algorithms, 3-day cooling periods, executioner's blade high-to-low flush detection, ATR dual-track adaptive thresholds, pre/post-market reversal deadlock unlocks, and V3.1 9:30 open order execution sizing.
2. **`test_frontend_stress.js`**: A zero-dependency Node.js stress testing harness that simulates ticker selection (`selectTicker()`) across all 66 tickers in `futu_pro_terminal.html`, verifying 0 uncaught exceptions and asserting zero DOM style remnants across state transitions.

Both test suites serve as **authoritative test oracles** following strict Test-Driven Development (TDD) discipline.

---

## 2. Test Suites Catalog

| Test File | Type | Target Module | Scope | Tests Count | Baseline Status (M0) |
|---|---|---|---|---|---|
| `test_v32_circuit_breaker.py` | Unit & Scenario | `integrated_market_radar.py`, `v31_strategy.py` | V3.2 Scenarios 1–7, Indicators | 14 tests | 3 PASS, 11 FAIL (Awaiting M1 Backend) |
| `test_frontend_stress.js` | Frontend Stress | `futu_pro_terminal.html`, `create_futu_terminal.py` | 66 Tickers, Exception & DOM Remnant Audit | 66 tickers + 432 transitions | 0 Exceptions PASS, 484 Remnants FAIL (Awaiting M2 Frontend) |
| `test_e2e_v31.py` | E2E Regression | `v31_strategy.py` | 4-Tier Opaque Box (Friction, Pure Stock, QQQ Base, Runners) | 55 tests | 55/55 PASS (100%) |
| `test_backtest_engine.py` | Ledger Audit | `backtest_5y_engine.py` | Double-entry accounting, $0.0000 discrepancy | 23 tests | 23/23 PASS (100%) |
| `test_adversarial_v31.py` | Adversarial | `v31_strategy.py` | Gaps, liquidity starvation, regime whipsaws | 13 tests | Blocked on `AXTI` cache key (Fix in M1) |

---

## 3. How to Run the Tests

### 3.1 V3.2 Circuit Breaker & V3.1 Execution Suite
```powershell
cd C:\Users\User\.gemini\antigravity-ide\scratch\us-market-radar
python test_v32_circuit_breaker.py
# Or via standard unittest:
python -m unittest test_v32_circuit_breaker.py
```

### 3.2 Frontend 66-Ticker Stress & DOM Remnant Harness
```powershell
cd C:\Users\User\.gemini\antigravity-ide\scratch\us-market-radar
node test_frontend_stress.js
```

### 3.3 Existing Regression Test Suites
```powershell
cd C:\Users\User\.gemini\antigravity-ide\scratch\us-market-radar
python test_e2e_v31.py
python test_backtest_engine.py
python backtest_5y_engine.py
```

---

## 4. Scenario Coverage & Acceptance Criteria

### 4.1 `test_v32_circuit_breaker.py` Scenarios

1. **Scenario 1 (Dead-cat Bounce Defense)**:
   - *Setup*: Stock experienced crash in past 3 days (`Prev_Crash_3d == True`). Opens flat (+0.2%), 5m RSI turns up with a 5m green candle.
   - *Assertion*: `evaluate_intraday_entry` must freeze entry: `intraday_entry_ready == False`, `is_cooling_halt == True`, `optimal_limit == 0.0`, and status banner contains `'⛔ 暴跌冷却期 (3日内曾大跌·等待日线企稳确认)'`.
2. **Scenario 2 (Intraday False-Green Non-Unlock)**:
   - *Setup*: Stock under 3-day cooling bounces +1.0% intraday with unclosed green candle, but fails to reach +2.5% or >50% body recovery.
   - *Assertion*: Cooling period remains locked: `is_cooling_halt == True`, `intraday_entry_ready == False`.
3. **Scenario 3 (Intraday Valid Recovery Unlock & Condition a)**:
   - *Setup 3A*: Stock under cooling surges >= +2.5% intraday, recovers >50% of crash day's red body, and trades above VWAP and EMA5.
     *Assertion*: `is_cooling_halt == False`.
   - *Setup 3B*: Prior closed day confirmed right-turn rebound candle (`Prev_Is_Right_Turn == True`).
     *Assertion*: Cooling period unlocks even on flat day (+0.4%): `is_cooling_halt == False`.
4. **Scenario 4 (High-to-Low Flush / 断头铡刀)**:
   - *Setup*: `rth_high = 110.0`, current price = `103.0` (flush = -6.36% <= -6.0%) and `pct_change = -1.5%` (<= -1.0%).
   - *Assertion*: `is_crash_halt == True`, reason contains `'断头铡刀防接刀'`, `optimal_limit == 0.0`, `intraday_entry_ready == False`.
5. **Scenario 5 (ATR Dual-Track Adaptive Threshold)**:
   - *Low-volatility (ATR% = 1.5%)*: Threshold = -3.8%. Safe at -3.7% (`is_crash_halt == False`), halts at -3.9% (`is_crash_halt == True`).
   - *Mid-volatility (ATR% = 2.2%)*: Threshold = -4.4% (2.0 * ATR%). Safe at -4.3%, halts at -4.5%.
   - *High-volatility (ATR% = 3.0%)*: Threshold = -5.2% (capped). Safe at -5.1%, halts at -5.3%.
6. **Scenario 6 (Deadlock Unlock on Pre/Post-Market Reversal)**:
   - *Setup*: Regular hours `pct_change = -6.0%` (below crash threshold), but post-market surges +8.0% (`safe_ext = +8.0% >= 3.0%`, net `+2.0% > -2.5%`).
   - *Assertion*: `is_crash_halt == False` (deadlock automatically unlocked).
7. **Scenario 7 (V3.1 9:30 Open Limit & Execution Sizing)**:
   - *7A (Optimal Limit)*: 9:30 open buy limit is `round(rth_close * 1.020, 2)`; RTH limit is `round(active_price * 1.005, 2)`.
   - *7B (Cancel Line)*: Gap-down cancel line is `max(low_price, last * 0.985)`.
   - *7C (Structural Stop)*: Stop loss is `max(min(low_price - 0.5 * atr, last - 1.8 * atr), last * 0.932)`.
   - *7D (Signals)*: Relative strength gate (`rs_60 >= -8.0`) and oversold bounce signal (`min_rsi_3d <= 32.0`, gain >= +1.5%, `vol_ratio >= 1.15`).
8. **Indicator Materialization**:
   - `compute_indicators(df)` produces `Crash_3d`, `Prev_Crash_3d`, `Is_Right_Turn`, `Prev_Is_Right_Turn`, `Prev_Crash_Body`, `Prev_Crash_Close` while preserving `Crash_5d`.

### 4.2 `test_frontend_stress.js` Acceptance Criteria

1. **Zero Uncaught Exceptions**:
   - `selectTicker(ticker)` called across all 66 tickers throws 0 JavaScript exceptions.
2. **Zero DOM Style Remnants**:
   - When switching from any crash-halt ticker (`SMH`, `SOXX`, `SOXL`, etc.) to any normal ticker (`IGV`, `AAPL`, `MSFT`, etc.), `b1El.style.color` must be restored from `#9ca3af` to canonical `#00e676` or default, and `b1ValEl.style.color` must be restored from `#ff5252` to canonical `#6b7280`.
3. **Card Visibility Isolation**:
   - `intcTacticalBox` is displayed (`display: block`) ONLY if `shares > 0`.
   - `optionsAnomalyBox` is displayed ONLY if `has_options == true`.

---

## 5. Escalation Log for Implementing Agents

The test writer identifies the following implementation tasks required to achieve 100% test pass:

### For Milestone M1 (Backend Developer):
1. **Expand `evaluate_intraday_entry` signature and logic in `integrated_market_radar.py`**:
   - Add parameters: `rth_high=None`, `prev_crash_3d=False`, `prev_is_right_turn=False`, `ema5=0.0`, `prev_crash_body=0.0`, `prev_crash_close=0.0`, `session_code="REGULAR"`.
   - Implement ATR dual-track threshold: `crash_thresh = -max(3.8, min(5.2, 2.0 * atr_pct))`.
   - Implement pre/post-market reversal unlock: if `safe_ext >= 3.0 and (pct_change + safe_ext) > -2.5`, override base crash halt.
   - Implement high-to-low flush filter: if `intraday_flush_pct <= -6.0 and pct_change <= -1.0`, trigger `is_crash_halt = True` with `'断头铡刀防接刀'`.
   - Implement 3-day cooling period freeze: if `prev_crash_3d` and neither Condition (a) nor Condition (b) met, set `is_cooling_halt = True` and banner `'⛔ 暴跌冷却期 (3日内曾大跌·等待日线企稳确认)'`.
   - Implement 9:30 open limit: `round(rth_close * 1.020, 2)` when `session_code != "REGULAR"`.
2. **Update `compute_indicators` in `integrated_market_radar.py` and `v31_strategy.py`**:
   - Compute `Crash_3d`, `Prev_Crash_3d`, `Is_Right_Turn`, `Prev_Is_Right_Turn`, `Prev_Crash_Body`, `Prev_Crash_Close`.
3. **Add defensive cache guard**:
   - In `v31_strategy.py:767` and `test_v31_synthesis.py:370`: `if ticker not in indicators_data: continue` to resolve `KeyError: 'AXTI'`.

### For Milestone M2 (Frontend Developer):
1. **Fix DOM Remnants in `create_futu_terminal.py`**:
   - In `selectTicker()` non-crash / non-cooling branch, explicitly reset:
     ```javascript
     if (b1El) {
         b1El.innerText = b1Shares > 0 ? (b1Shares.toFixed(2) + " 股") : "--";
         b1El.style.color = "#00e676";
     }
     if (b1ValEl) {
         b1ValEl.innerText = b1Val > 0 ? ("约 $" + b1Val.toFixed(1) + " · 1.5%风险平价") : "1.5% 风险平价一次性建仓";
         b1ValEl.style.color = "#6b7280";
     }
     ```
2. **Display Cooling Banner in Futu Terminal**:
   - Map `is_cooling_halt` in `renderWatchlist` and recognize in `selectTicker()`.
   - Color `pIntradayBanner` red (`#ff5252`) and freeze position display when in cooling halt.
   - Align gap-down cancel line formula to `Math.max(data.rth_low || 0, curLast * 0.985)`.
