# TEST_READY — Automated Test Suite & Invariant Verification for Discord Sentinel

**Milestone**: M0 (Automated Test Suite & Infra)  
**Target Module**: `discord_sentinel.py` & `integrated_market_radar.py` linkage  
**Test Suite File**: `test_discord_sentinel.py`  
**Execution Time**: ~0.74s  
**Test Framework**: Python built-in `unittest`  
**Network Dependency**: 100% Offline (Deterministic synthetic fixtures)  

---

## 1. Quick Start / How to Run

To run the complete automated Discord Sentinel test suite from the repository root:

```powershell
# Direct test runner execution with summary report
python test_discord_sentinel.py

# Or via Python standard unittest module
python -m unittest test_discord_sentinel.py -v
```

### Environment Requirements
- Python >= 3.10 (Tested on Python 3.14-64)
- Dependencies: `pandas`, `numpy`, `requests`, `yfinance`

---

## 2. Test Suite Architecture & Coverage Tiers

The test suite enforces 14 rigorous invariant tests across 4 functional tiers and configuration foundations, strictly derived from `ORIGINAL_REQUEST.md`, `PROJECT.md`, `TEST_INFRA.md`, and Explorer handoffs:

| Tier | Category Header | Authoritative Invariant | Test Method | M0 Status | Target Milestone |
|:---|:---|:---|:---|:---:|:---:|
| **Tier 1** | 5 Core Engineering Corrections | 4H aggregation grouping strictly by Date without cross-day stitching | `test_4h_aggregation_no_cross_day_stitching` | ⏳ Pending M1 | M1 |
| **Tier 1** | 5 Core Engineering Corrections | Half-day early close and intraday in-progress graceful handling | `test_4h_aggregation_early_close_and_intraday` | ⏳ Pending M1 | M1 |
| **Tier 1** | 5 Core Engineering Corrections | Pre/post-market snapshot appends temporary bar without mutating historical 15:30 bar | `test_prepost_market_snapshot_does_not_mutate_historical_bars` | ⏳ Pending M1 | M1 |
| **Tier 1** | 5 Core Engineering Corrections | 1H S/R strict boundary invariant: `1H撑 < active_price < 1H压` under breakdown/breakout/stress | `test_1h_support_resistance_strict_boundary_protection` | ⏳ Pending M1 | M1 |
| **Tier 1** | 5 Core Engineering Corrections | BTC-USD bypasses Finnhub quote; 51 tickers batch multi-threaded fetch without 429 risk | `test_batch_fetch_and_btc_usd_bypass` | ⏳ Pending M1 | M1 |
| **Tier 2** | Quantitative Engine & States | 140-bar 25-bin Volume Profile POC using Typical Price $(H+L+C)/3$, flat-line & zero-vol guards | `test_volume_profile_poc_calculation` | ⏳ Pending M2 | M2 |
| **Tier 2** | Quantitative Engine & States | 5-state RSI state machine with V3.2 3-day crash cooling `(⚠️冷却观察)` & VWAP `(✅右侧企稳)` | `test_rsi_state_machine_transitions` | ⏳ Pending M2 | M2 |
| **Tier 2** | Quantitative Engine & States | Defense Factor Y = mean(SCHD, KO, GLD) with partial fallback & Divergence Alert (diff $\ge 25$) | `test_defense_factor_y_and_divergence` | ⏳ Pending M2 | M2 |
| **Tier 2** | Quantitative Engine & States | 3X mirror resonance: SOXL bottom + SOXS top, TQQQ bottom + SQQQ top | `test_3x_mirror_resonance` | ⏳ Pending M2 | M2 |
| **Tier 3** | Discord Formatting & Limits | Native Discord Markdown: zero occurrences of \`\`\` or \`\`\`text grey codeblocks | `test_embed_native_markdown_no_codeblocks` | ⏳ Pending M3 | M3 |
| **Tier 3** | Discord Formatting & Limits | Discord payload character limit: Msg 1 $\le 2000$, Msg 2A $\le 5500$, Msg 2B $\le 5500$, $\le 10$ embeds | `test_discord_payload_character_limits` | ⏳ Pending M3 | M3 |
| **Tier 4** | Radar Daemon Linkage & Safety | Non-blocking async daemon thread linkage (`isDaemon() == True`) with exception isolation | `test_radar_linkage_async_and_isolated` | ⏳ Pending M3 | M3 |
| **Config** | Universe Definition | Universe contains exactly 51 unique tickers distributed across 5 tiers (4, 12, 13, 13, 9) | `test_universe_structure_and_completeness` | ✅ **PASS** | M0 |
| **Config** | Environment Configuration | `.env` contains `DISCORD_WEBHOOK_URL` pointing to discordapp.com webhook | `test_env_webhook_configuration` | ⏳ Pending M1 | M1 |

---

## 3. Detailed Specification of Test Tiers

### Tier 1: 5 Core Engineering Corrections Invariants
1. **`test_4h_aggregation_no_cross_day_stitching`**:
   - Generates 14 1H bars across 2 full trading days.
   - Enforces grouping strictly by `df.index.date`.
   - Day 1 yields Bar 1 (09:30..12:30) and Bar 2 (13:30..15:30).
   - Invariant: Zero bars span between Day 1 and Day 2.
   - Verifies crypto mode (`BTC-USD`): 24 1H bars per day aggregated into 6 4-hour UTC chunks strictly bounded within date.
2. **`test_4h_aggregation_early_close_and_intraday`**:
   - Half-day session (13:00 close): 4 1H bars produce exactly 1 4H bar; Bar 2 is safely omitted without raising errors or outputting NaN.
   - Active session at 11:00 AM: dynamically forms Bar 1 with available bars.
   - Empty DataFrame: returns empty DataFrame without unhandled exceptions.
3. **`test_prepost_market_snapshot_does_not_mutate_historical_bars`**:
   - In `PRE_MARKET`, `POST_MARKET`, `OVERNIGHT`, and `WEEKEND`, yesterday's closed 15:30 bar is verified immutable.
   - Returned calculation DataFrame has length `len + 1` with temporary snapshot bar: `Close = active_price`, `Open = yesterday_close`, `Volume = 0.0`.
   - In `REGULAR` market session, no synthetic bar is appended.
4. **`test_1h_support_resistance_strict_boundary_protection`**:
   - Monotonicity invariant: `1H撑 < active_price < 1H压` must hold 100% strictly.
   - Breakdown condition (`low_20h >= active_price`): enforces `1H撑 = min(POC, active_price - 1.5 * ATR_1H) < active_price`.
   - Breakout condition (`high_20h <= active_price`): enforces `1H压 = active_price + 1.5 * ATR_1H > active_price`.
   - Stress-tested under extreme market conditions (flash crash to $5.0, massive surge to $2,000.0, degenerate empty inputs).
5. **`test_batch_fetch_and_btc_usd_bypass`**:
   - Mocks yfinance and requests; verifies 51 tickers are fetched in batch mode.
   - Asserts zero calls to Finnhub quote endpoint for `BTC-USD` (avoiding Finnhub null/0 quote failure).

### Tier 2: Quantitative Engine & State Machine Invariants
6. **`test_volume_profile_poc_calculation`**:
   - Creates 140 1H bars with known institutional volume concentration at $100.0.
   - Asserts POC recovers the winning bin midpoint ($100.0 \pm 2.0$).
   - Guards against zero volume (time-at-price fallback), flat lines ($High=Low=Close$), and short history.
7. **`test_rsi_state_machine_transitions`**:
   - Evaluates all 5 states: `[🔥 双周期见底]`, `[🛑 双周期触顶]`, `[⚡ 4H超卖]`, `[安全持股]`.
   - Verifies V3.2 3-day crash cooling: unresolved cooling displays `[🔥 双周期见底·⚠️冷却观察]`; intraday VWAP recovery unlocks `[🔥 双周期见底·✅右侧企稳]`.
8. **`test_defense_factor_y_and_divergence`**:
   - Arithmetic mean of `SCHD`, `KO`, and `GLD` 4H RSI.
   - Graceful fallback when 1 or 2 components are missing.
   - Divergence Alert triggers when `Factor Y 4H_RSI - SOXX 4H_RSI >= 25.0` and `SOXX 4H_RSI <= 32.0`.
9. **`test_3x_mirror_resonance`**:
   - Asserts `SOXL` bottom + `SOXS` top triggers `⚡【SOXL×SOXS 底部共振】`.
   - Asserts `TQQQ` bottom + `SQQQ` top triggers `⚡【TQQQ×SQQQ 底部共振】`.
   - Asserts top resonance when bullish 3X is overbought and inverse 3X is oversold.

### Tier 3: Discord Embed Formatting & Character Limits
10. **`test_embed_native_markdown_no_codeblocks`**:
    - Scans all titles, descriptions, and fields across all generated embeds.
    - Zero occurrences of \`\`\` or \`\`\`text.
    - Native bold formatting `**{sym}**` and emojis verified.
11. **`test_discord_payload_character_limits`**:
    - Message 1 character count $\le 2,000$ (Discord limit: 6,000).
    - Message 2 Part 2A (Tiers 1-3, 5 embeds) total chars $\le 5,500$ (Discord limit: 6,000).
    - Message 2 Part 2B (Tiers 4-5, 4 embeds) total chars $\le 5,500$ (Discord limit: 6,000).
    - Total embeds per message $\le 10$, total embeds across Part 2A and 2B == 9.
    - Each embed description $\le 4,096$ characters.

### Tier 4: Radar Daemon Linkage & Safety Invariants
12. **`test_radar_linkage_async_and_isolated`**:
    - Verifies async background thread launch (`daemon=True`).
    - Verifies network exceptions (timeouts, HTTP 500, HTTP 429) are caught and logged inside `run_sentinel_cycle_safe` without impacting caller.

---

## 4. Test Execution Output (M0 Baseline)

```text
================================================================================
 🚀 RUNNING DISCORD SENTINEL & ENGINEERING INVARIANTS TEST SUITE
================================================================================
test_1h_support_resistance_strict_boundary_protection (__main__.TestDiscordSentinelTier1.test_1h_support_resistance_strict_boundary_protection)
[Engineering Correction 3] Invariant: 1H撑 < active_price < 1H压 must hold 100% strictly. ... ERROR
test_4h_aggregation_early_close_and_intraday (__main__.TestDiscordSentinelTier1.test_4h_aggregation_early_close_and_intraday)
[Engineering Correction 1 Edge Cases] ... ERROR
test_4h_aggregation_no_cross_day_stitching (__main__.TestDiscordSentinelTier1.test_4h_aggregation_no_cross_day_stitching)
[Engineering Correction 1] 4H K-line aggregation must group strictly by Date. ... ERROR
test_batch_fetch_and_btc_usd_bypass (__main__.TestDiscordSentinelTier1.test_batch_fetch_and_btc_usd_bypass)
[Engineering Correction 4] Finnhub 429 Protection & BTC-USD Bypass: ... ERROR
test_prepost_market_snapshot_does_not_mutate_historical_bars (__main__.TestDiscordSentinelTier1.test_prepost_market_snapshot_does_not_mutate_historical_bars)
[Engineering Correction 2] Outside RTH (<09:30 or >16:00 ET), yesterday's ... ERROR
test_3x_mirror_resonance (__main__.TestDiscordSentinelTier2.test_3x_mirror_resonance)
3X Leveraged Mirror Resonance: ... ERROR
test_defense_factor_y_and_divergence (__main__.TestDiscordSentinelTier2.test_defense_factor_y_and_divergence)
Defense Factor Y & Macro Divergence: ... ERROR
test_rsi_state_machine_transitions (__main__.TestDiscordSentinelTier2.test_rsi_state_machine_transitions)
RSI State Machine & V3.2 Cooldown / VWAP Integration: ... ERROR
test_volume_profile_poc_calculation (__main__.TestDiscordSentinelTier2.test_volume_profile_poc_calculation)
Volume Profile POC: 140 1H bars, 25 bins, Typical Price (H+L+C)/3, volume accumulation. ... ERROR
test_discord_payload_character_limits (__main__.TestDiscordSentinelTier3.test_discord_payload_character_limits)
[Payload Limit Invariant] ... ERROR
test_embed_native_markdown_no_codeblocks (__main__.TestDiscordSentinelTier3.test_embed_native_markdown_no_codeblocks)
[Anti-Grayblock Invariant] ... ERROR
test_radar_linkage_async_and_isolated (__main__.TestDiscordSentinelTier4.test_radar_linkage_async_and_isolated)
[Radar Daemon Invariant] ... FAIL
test_env_webhook_configuration (__main__.TestDiscordSentinelUniverseAndEnv.test_env_webhook_configuration)
Verify DISCORD_WEBHOOK_URL is configured in .env. ... FAIL
test_universe_structure_and_completeness (__main__.TestDiscordSentinelUniverseAndEnv.test_universe_structure_and_completeness)
Verify the 5 Tiers universe contains exactly 51 unique tickers. ... ok

================================================================================
 📊 TEST EXECUTION SUMMARY (Elapsed: 0.736s)
================================================================================
 Total Tests Run: 14
 Passed:          1 (Universe completeness verified)
 Failures:        2 (.env pending M1 setup, linkage stub)
 Errors:          11 (NotImplementedError on interface stubs)
================================================================================
```

---

## 5. Escalation & Implementation Guidance for Milestone Agents

1. **For Worker M1 (Engineering Corrections & Data Pipeline)**:
   - Configure `DISCORD_WEBHOOK_URL` in `.env`.
   - Implement `aggregate_1h_to_4h_df`, `append_realtime_snapshot_bar`, `compute_and_protect_1h_sr`, `fetch_sentinel_market_data` in `discord_sentinel.py`.
   - All 5 tests in `TestDiscordSentinelTier1` + `test_env_webhook_configuration` must turn green (PASS).
2. **For Worker M2 (Quantitative Engine & Resonance)**:
   - Implement `compute_volume_profile_poc`, `evaluate_rsi_state`, `compute_factor_y`, `evaluate_cross_asset_resonance` in `discord_sentinel.py`.
   - All 4 tests in `TestDiscordSentinelTier2` must turn green (PASS).
3. **For Worker M3 (Discord Formatting, CLI & Linkage)**:
   - Implement `build_discord_payloads`, CLI flags (`--once`, `--schedule`, `--dry-run`), and `run_sentinel_cycle_safe`.
   - Integrate `dispatch_discord_sentinel_async` into `integrated_market_radar.py` after line 4385.
   - All tests in `TestDiscordSentinelTier3` and `TestDiscordSentinelTier4` must turn green (PASS).
4. **For M4 Final Integration & Auditors**:
   - Run `python test_discord_sentinel.py` to confirm 14/14 (100% PASS).
