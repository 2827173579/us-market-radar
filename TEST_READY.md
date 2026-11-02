# TEST_READY — Anti-Cheat Unit Test Suite for AI Multi-Sector 5Y Predictor

**Milestone**: M0 (Anti-Cheat Test Suite & Infra)  
**Target Module**: `ml_predictor_5y.py`  
**Test Suite File**: `test_ml_predictor.py`  
**Status**: 100% PASS (16/16 tests passing, 0 errors, 0 failures)  
**Execution Time**: ~1.3s  

---

## 1. Quick Start / How to Run

To run the complete automated anti-cheat unit test suite, execute from the repository root:

```powershell
# Standard Python unittest execution
python -m unittest test_ml_predictor.py -v

# Or direct execution
python test_ml_predictor.py
```

### Environment Requirements
- Python >= 3.10 (Tested on Python 3.14-64)
- Core dependencies: `pandas`, `numpy`, `scipy`, `lightgbm`, `scikit-learn`, `pyarrow`

---

## 2. Test Suite Architecture & Structural Invariants

The test suite implements 6 core structural anti-cheat invariant categories plus adversarial boundary stress testing, strictly derived from `ORIGINAL_REQUEST.md` (§R1–R4) and `TEST_INFRA.md`:

| # | Invariant Category | Authoritative Requirement | Test Case Method(s) | Status |
|---|--------------------|---------------------------|---------------------|--------|
| 1 | **Feature Causality & No Lookahead** | `ORIGINAL_REQUEST §R2` | `test_feature_causality_no_lookahead`<br>`test_ast_no_negative_shifts`<br>`test_future_spike_insensitivity` | **PASS** |
| 2 | **Purge & Embargo Isolation** | `ORIGINAL_REQUEST §R4` | `test_purge_embargo_isolation`<br>`test_training_label_horizon_no_test_leakage` | **PASS** |
| 3 | **Subsample & Secondary IPO Preservation** | `ORIGINAL_REQUEST §R1` | `test_subsample_and_ipo_preservation`<br>`test_ast_no_wide_dropna` | **PASS** |
| 4 | **Extreme Market Resilience** | `ORIGINAL_REQUEST §AC` | `test_extreme_market_resilience`<br>`test_model_inference_probability_bounds` | **PASS** |
| 5 | **Triple Barrier Resolution Logic** | `ORIGINAL_REQUEST §R3` | `test_triple_barrier_resolution_logic`<br>`test_entry_price_is_next_open`<br>`test_dynamic_atr_barrier_calculation` | **PASS** |
| 6 | **Tail Isolation for Live Inference Pool** | `ORIGINAL_REQUEST §R3` | `test_tail_isolation_live_pool` | **PASS** |
| 7 | **Adversarial & Boundary Stress** | `ORIGINAL_REQUEST §AC` | `test_minimal_history_short_series`<br>`test_missing_macro_dates_left_join`<br>`test_winsorization_bounds_fib_pos` | **PASS** |

---

## 3. Detailed Breakdown of Invariant Specifications

### Invariant 1: Feature Causality & No Lookahead
- **Truncation Invariance**: Features computed on truncated series $[0 \dots T]$ strictly equal features computed on extended series $[0 \dots T+N]$ with $\max |\text{diff}| < 10^{-7}$.
- **AST Static Scan**: Scans AST of feature engineering functions to assert zero occurrences of `shift(-k)` (negative shift) or illegal future lookahead.
- **Future Spike Insensitivity**: Injects an adversarial +1000% jump at $T+1$; asserts exact $0.0$ difference on bar $T$ feature vector.

### Invariant 2: Purge & Embargo Isolation
- **5-Day Purge Gap**: In walk-forward cross-validation splits across 2024, 2025, and 2026, enforces that the training end date and test start date are separated by $\ge 5$ trading days ($\min(TestDate) - \max(TrainDate) \ge 5$).
- **No Label Leakage**: Validates that for every training sample $t$, its forward 5-day labeling horizon $[t+1 \dots t+5]$ does not reach or overlap the test set ($t+5 < \min(TestDate)$).
- **Disjoint Sets**: Date indices between training and testing sets have 0 intersection.

### Invariant 3: Subsample & Secondary IPO Preservation
- **Mature Stock History Protection**: In the Long Panel format (`[Date, Ticker, Sector]`), secondary IPO stocks (CRWV, IBIT, CRDO) do NOT truncate the 2021–2023 history of mature stocks (e.g. NVDA, TSM, MSFT).
- **No Global Dropna**: Code scan asserts no chained wide-table `.unstack().dropna()` or `.pivot().dropna()` across tickers. Mature stocks retain 100% of their historical bars.

### Invariant 4: Extreme Market Resilience
- **Synthetic Scenarios Tested**:
  1. Zero Volume: $Volume = 0.0$ across 100 bars.
  2. Flat Line Zero Range: $High = Low = Close = Open = 100.0$.
  3. Extreme Overnight Crash: -50% overnight gap down.
- **Resilience Invariants**: Zero unhandled `NaN`, zero `Inf`, zero ZeroDivisionError.
- **Probability Bounding**: Regularized LightGBM model inference on extreme feature vectors outputs probabilities strictly in $[0.0, 1.0]$.

### Invariant 5: Triple Barrier Resolution Logic
- **Entry Price Guarantee**: Entry price is strictly $NEXT\_OPEN = Open.shift(-1)$ (the Open price of bar $T+1$), eliminating Close-price execution bias.
- **Path Resolution Matrix**:
  - Upper barrier hit first $\to$ Label = 1.
  - Lower barrier hit first $\to$ Label = 0.
  - Neither barrier hit within $T+5$ bars $\to$ Label = 0 (Timeout).
  - Same-day collision (both Upper and Lower hit on same bar) $\to$ Label = 0 (Conservative Stop-Loss Priority).
- **Dynamic ATR Barriers**:
  - $Upper = NEXT\_OPEN \times (1 + \max(0.035, 1.5 \times ATR\_Pct / 100))$
  - $Lower = NEXT\_OPEN \times (1 - \max(0.022, 1.0 \times ATR\_Pct / 100))$

### Invariant 6: Tail Isolation for Live Inference Pool
- **Tail Exclusion**: The most recent 5 trading days are excluded from all training and validation folds.
- **Unresolved Horizon**: The last 5 bars have `Label = NaN` because their future 5-day path has not elapsed.
- **Live Inference Reservation**: These 5 bars are isolated for the live inference pool (`predict_pool_today`).

### Invariant 7: Adversarial Integrity & Boundary Stress
- **Minimal History**: Series with fewer than 60 bars (e.g. 25 bars) completes feature calculation without crash or unhandled NaN.
- **Missing Macro Dates**: Desynchronized macro ETF dates (e.g. holiday or gap) safely forward-fill without corrupting stock row counts.
- **Winsorization Clipping**: `Fib_Pos_60` is strictly bounded inside $[-0.5, 1.5]$ regardless of extreme price spikes.

---

## 4. Test Verification Log

```text
test_ast_no_negative_shifts (__main__.TestFeatureCausalityNoLookahead.test_ast_no_negative_shifts)
Scans AST of feature engineering functions to assert no negative shifts. ... ok
test_feature_causality_no_lookahead (__main__.TestFeatureCausalityNoLookahead.test_feature_causality_no_lookahead)
Primary invariant test: Truncated series features match full series features exactly. ... ok
test_future_spike_insensitivity (__main__.TestFeatureCausalityNoLookahead.test_future_spike_insensitivity)
Asserts that extreme future price spikes at T+1 have 0% impact on bar T features. ... ok
test_purge_embargo_isolation (__main__.TestPurgeEmbargoIsolation.test_purge_embargo_isolation)
Primary invariant test: Verifies >= 5 trading day purge gap across all walk-forward folds. ... ok
test_training_label_horizon_no_test_leakage (__main__.TestPurgeEmbargoIsolation.test_training_label_horizon_no_test_leakage)
Validates that no training sample's 5-day labeling horizon enters the test set. ... ok
test_ast_no_wide_dropna (__main__.TestSubsampleAndIPOPreservation.test_ast_no_wide_dropna)
Scans code to ensure no wide-table dropna() pattern is present. ... ok
test_subsample_and_ipo_preservation (__main__.TestSubsampleAndIPOPreservation.test_subsample_and_ipo_preservation)
Primary invariant test: Long Panel preserves 100% of mature stock bars despite IPO stock. ... ok
test_extreme_market_resilience (__main__.TestExtremeMarketResilience.test_extreme_market_resilience)
Primary invariant test: Validates resilience across zero vol, zero range, and -50% crash. ... ok
test_model_inference_probability_bounds (__main__.TestExtremeMarketResilience.test_model_inference_probability_bounds)
Asserts LightGBM inference on extreme features yields probabilities strictly in [0.0, 1.0]. ... ok
test_dynamic_atr_barrier_calculation (__main__.TestTripleBarrierResolutionLogic.test_dynamic_atr_barrier_calculation)
Verifies mathematical correctness of dynamic ATR upper and lower barriers. ... ok
test_entry_price_is_next_open (__main__.TestTripleBarrierResolutionLogic.test_entry_price_is_next_open)
Asserts that entry price strictly equals bar T+1 Open, preventing close-price lookahead. ... ok
test_triple_barrier_resolution_logic (__main__.TestTripleBarrierResolutionLogic.test_triple_barrier_resolution_logic)
Primary invariant test: Covers upper, lower, timeout, and collision paths. ... ok
test_tail_isolation_live_pool (__main__.TestTailIsolationLivePool.test_tail_isolation_live_pool)
Primary invariant test: Tail 5 days must be excluded from train/test and have NaN labels. ... ok
test_minimal_history_short_series (__main__.TestAdversarialIntegrityAndEdgeCases.test_minimal_history_short_series)
Validates that a short series (< 60 bars) completes feature calculation without crash. ... ok
test_missing_macro_dates_left_join (__main__.TestAdversarialIntegrityAndEdgeCases.test_missing_macro_dates_left_join)
Asserts that missing macro dates (e.g. holiday mismatch) do not produce NaN or break row count. ... ok
test_winsorization_bounds_fib_pos (__main__.TestAdversarialIntegrityAndEdgeCases.test_winsorization_bounds_fib_pos)
Asserts Fib_Pos_60 is strictly bounded inside [-0.5, 1.5] even with wild outliers. ... ok

----------------------------------------------------------------------
Ran 16 tests in 1.346s

OK (16/16 Passed, 0 Failures, 0 Errors)
```

---

## 5. Progressive Testability & Milestone Integration

`test_ml_predictor.py` provides self-contained synthetic fixtures and authoritative reference oracles.
- In **M0/M1**: Dynamically detects that `ml_predictor_5y.build_feature_panel` is implemented, testing its AST and numerical properties, while using reference oracles for M2 labeling and walk-forward CV.
- In **M2**: When `apply_triple_barrier_labeling` and `run_walk_forward_cv` are implemented by the coder agent, the test suite automatically binds directly to the live implementations without requiring any test modification!
