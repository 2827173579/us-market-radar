# -*- coding: utf-8 -*-
"""
test_v46_c5_dual_regime_predictor.py - V4.6 C5 Dual-Regime Predictor & Risk-Band UI Unit Tests (100% Offline/Mocked)
"""

import json
import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

import numpy as np
import pandas as pd

import discord_radar_bot as drb
import integrated_market_radar as imr
import ml_predictor_5y as mlp


def _make_synthetic_panel(bear_tail: bool = False, n_days: int = 900) -> pd.DataFrame:
    """Build deterministic multi-ticker daily Close panel (900 US business days)."""
    dates = pd.bdate_range(end="2026-11-20", periods=n_days)
    rng = np.random.default_rng(42)

    t = np.arange(n_days, dtype=float)
    base_wave = 100.0 * np.exp(0.0005 * t + 0.04 * np.sin(t / 14.0))
    # Inject a realistic 2022/earlier bear drawdown episode in first third so strict bear analogs exist
    bear_mask = (t >= 180) & (t <= 260)
    base_wave[bear_mask] *= np.linspace(1.0, 0.72, int(np.sum(bear_mask)))

    smh = base_wave * (1.0 + 0.004 * rng.standard_normal(n_days))
    qqq = base_wave * 0.95 * (1.0 + 0.003 * rng.standard_normal(n_days))
    spy = base_wave * 1.10 * (1.0 + 0.0025 * rng.standard_normal(n_days))
    nvda = base_wave * 1.25 * np.exp(0.0003 * t) * (1.0 + 0.006 * rng.standard_normal(n_days))
    soxl = base_wave * 0.65 * (1.0 + 0.012 * rng.standard_normal(n_days))
    soxs = 40.0 * (100.0 / np.maximum(soxl, 1.0))
    vixy = 20.0 + 1.5 * np.sin(t / 20.0)
    vixy[bear_mask] = np.linspace(22.0, 42.0, int(np.sum(bear_mask)))

    # Inject recent regime at tail (last 18 days)
    if bear_tail:
        smh[-18:] *= np.linspace(1.0, 0.86, 18)
        qqq[-18:] *= np.linspace(1.0, 0.87, 18)
        spy[-18:] *= np.linspace(1.0, 0.89, 18)
        nvda[-18:] *= np.linspace(1.0, 0.80, 18)
        soxl[-18:] *= np.linspace(1.0, 0.62, 18)
        soxs[-18:] *= np.linspace(1.0, 1.45, 18)
        vixy[-18:] = np.linspace(20.0, 39.0, 18)
    else:
        smh[-18:] *= np.linspace(1.0, 1.05, 18)
        qqq[-18:] *= np.linspace(1.0, 1.04, 18)
        spy[-18:] *= np.linspace(1.0, 1.03, 18)
        nvda[-18:] *= np.linspace(1.0, 1.06, 18)
        soxl[-18:] *= np.linspace(1.0, 1.09, 18)
        vixy[-18:] = np.linspace(21.0, 18.0, 18)

    rows = []
    for tk, arr in (
        ("SMH", smh),
        ("QQQ", qqq),
        ("SPY", spy),
        ("VIXY", vixy),
        ("NVDA", nvda),
        ("SOXL", soxl),
        ("SOXS", soxs),
    ):
        for dt, c in zip(dates, arr):
            px = round(float(max(1.0, c)), 4)
            rows.append({
                "Date": dt,
                "Ticker": tk,
                "Open": px,
                "High": round(px * 1.01, 4),
                "Low": round(px * 0.99, 4),
                "Close": px,
                "Volume": 1_000_000.0,
            })
    return pd.DataFrame(rows)


def _compute_independent_base_wave(piv: pd.DataFrame, tk: str, window_len: int = 25, forecast_days: int = 15) -> np.ndarray:
    """Independent reference implementation of legacy Base KNN-5 + Zero-Drift FFT."""
    closes = piv[tk].values.astype(float)
    n = len(closes)
    recent_n = min(120, n)
    rec_c = closes[-recent_n:]
    ema20_arr = pd.Series(rec_c).ewm(span=20, adjust=False).mean().values
    det_pct = (rec_c - ema20_arr) / (ema20_arr + 1e-8)
    fft_vals = np.fft.rfft(det_pct)
    freqs = np.fft.rfftfreq(recent_n)
    mags = np.abs(fft_vals)
    mags[0] = 0.0
    valid_f = [i for i in range(1, len(freqs)) if (1.0 / 25.0) <= freqs[i] <= (1.0 / 4.0)]
    top_idx = sorted(valid_f, key=lambda i: mags[i])[-3:] if len(valid_f) >= 3 else list(np.argsort(mags)[-3:])
    fut_x = np.arange(recent_n, recent_n + forecast_days)
    harm_pct = np.zeros(forecast_days, dtype=float)
    for i in top_idx:
        harm_pct += (mags[i] / (recent_n / 2.0)) * np.cos(2.0 * np.pi * freqs[i] * fut_x + np.angle(fft_vals[i]))
    harm_osc = harm_pct - np.linspace(harm_pct[0], harm_pct[-1], forecast_days)

    dates_idx = piv.index
    n_piv = len(piv)
    start_i = piv.index.get_loc(pd.Timestamp("2023-02-01")) if pd.Timestamp("2023-02-01") in piv.index else max(0, n_piv - 920)
    end_i = n_piv - window_len - forecast_days - 60
    t_smh = piv["SMH"].values[-window_len:]
    n_smh = (t_smh - np.mean(t_smh)) / (np.std(t_smh) + 1e-8)
    t_qqq = piv["QQQ"].values[-window_len:]
    n_qqq = (t_qqq - np.mean(t_qqq)) / (np.std(t_qqq) + 1e-8)
    cur_ret5_smh = (t_smh[-1] - t_smh[-6]) / (t_smh[-6] + 1e-8)
    raw_cands = []
    for i in range(start_i, end_i):
        c_smh = piv["SMH"].values[i:i + window_len]
        c_qqq = piv["QQQ"].values[i:i + window_len]
        corr_s = float(np.corrcoef(n_smh, (c_smh - np.mean(c_smh)) / (np.std(c_smh) + 1e-8))[0, 1])
        corr_q = float(np.corrcoef(n_qqq, (c_qqq - np.mean(c_qqq)) / (np.std(c_qqq) + 1e-8))[0, 1])
        j_corr = 0.65 * corr_s + 0.35 * corr_q
        h_ret5 = (c_smh[-1] - c_smh[-6]) / (c_smh[-6] + 1e-8)
        h_b1 = (c_smh[-1] - c_smh[-2]) / (c_smh[-2] + 1e-8)
        if cur_ret5_smh < -0.02 and not (h_ret5 < -0.02 and h_b1 > -0.01):
            continue
        raw_cands.append((j_corr, i))
    raw_cands.sort(key=lambda x: x[0], reverse=True)
    top_k = []
    for c in raw_cands:
        if all(abs(c[1] - p[1]) >= 20 for p in top_k):
            top_k.append(c)
        if len(top_k) == 5:
            break
    w_arr = np.array([max(0.1, c[0]) ** 2 for c in top_k], dtype=float)
    w_arr /= np.sum(w_arr)
    acc = np.zeros(forecast_days, dtype=float)
    col_vals = piv[tk].values
    for w_val, (_, idx_h) in zip(w_arr, top_k):
        base_v = col_vals[idx_h + window_len - 1]
        fut_v = col_vals[idx_h + window_len:idx_h + window_len + forecast_days]
        acc += w_val * np.clip((fut_v - base_v) / base_v, -0.35, 0.35)
    return 0.55 * acc + 0.50 * harm_osc


class TestV46C5DualRegimePredictor(unittest.TestCase):
    def test_c5_switch_isolated_breadth_vs_confirmed_bear_and_inverse_etf(self):
        # 1. Isolated breadth < 35 with healthy SPY/QQQ above MA20 or VIXY <= MA20 must NOT trigger
        res_iso = mlp.compute_v46_c5_switch_flag(
            qqq_bias50=2.5, smh_bias50=3.0, spy_bias50=1.8,
            spy_bias20=1.0, qqq_bias20=1.2, breadth_ma20=30.0,
            vixy_pos60=0.45, vixy_bias20=-2.0, vix_proxy=18.0,
            cur_tk_c=98.0, cur_ema5=99.0, cur_ema10=100.0, cur_ema20=99.5,
            cur_b20=-1.5, cur_b50=-1.0, cur_ret3=-2.0, cur_ret5=-2.5, cur_down4=2.0,
            ticker="NVDA",
        )
        self.assertFalse(res_iso["breadth_confirmed_weak"])
        self.assertFalse(res_iso["macro_bear_defense"])
        self.assertFalse(res_iso["c5_switch_active"])
        self.assertEqual(res_iso["v46_regime_mode"], "NORMAL_BASE")

        # 2. Confirmed weak breadth (MA20 broken + VIXY above MA20 + VIX proxy >= 25) MUST trigger for weak stock
        res_conf = mlp.compute_v46_c5_switch_flag(
            qqq_bias50=-1.5, smh_bias50=-2.0, spy_bias50=-0.5,
            spy_bias20=-1.0, qqq_bias20=-1.5, breadth_ma20=30.0,
            vixy_pos60=0.60, vixy_bias20=3.0, vix_proxy=26.0,
            cur_tk_c=95.0, cur_ema5=98.0, cur_ema10=100.0, cur_ema20=102.0,
            cur_b20=-4.0, cur_b50=-5.0, cur_ret3=-3.5, cur_ret5=-4.5, cur_down4=3.0,
            ticker="NVDA",
        )
        self.assertTrue(res_conf["breadth_confirmed_weak"])
        self.assertTrue(res_conf["macro_bear_defense"])
        self.assertTrue(res_conf["c5_switch_active"])
        self.assertEqual(res_conf["v46_regime_mode"], "BEAR_INERTIA_C5")

        # 3. Dual index MA50 breakdown / VIXY panic triggers C5
        res_idx = mlp.compute_v46_c5_switch_flag(
            qqq_bias50=-4.0, smh_bias50=-4.5, spy_bias50=-2.5,
            spy_bias20=-2.0, qqq_bias20=-3.0, breadth_ma20=42.0,
            vixy_pos60=0.80, vixy_bias20=9.0, vix_proxy=32.0,
            cur_tk_c=40.0, cur_ema5=43.0, cur_ema10=45.0, cur_ema20=48.0,
            cur_b20=-9.0, cur_b50=-12.0, cur_ret3=-6.0, cur_ret5=-9.0, cur_down4=3.0,
            ticker="SOXL",
        )
        self.assertTrue(res_idx["c5_switch_active"])

        # 4. Individual stock deep breakdown triggers even if macro isn't in defense
        res_stk = mlp.compute_v46_c5_switch_flag(
            qqq_bias50=2.0, smh_bias50=2.0, spy_bias50=2.0,
            spy_bias20=1.0, qqq_bias20=1.0, breadth_ma20=58.0,
            vixy_pos60=0.35, vixy_bias20=-3.0, vix_proxy=17.0,
            cur_tk_c=88.0, cur_ema5=91.0, cur_ema10=94.0, cur_ema20=97.0,
            cur_b20=-7.0, cur_b50=-8.5, cur_ret3=-4.0, cur_ret5=-6.5, cur_down4=3.0,
            ticker="AMD",
        )
        self.assertTrue(res_stk["stock_deep_breakdown"])
        self.assertTrue(res_stk["c5_switch_active"])

        # 5. Inverse hedge ETFs stay NORMAL_BASE even in panic
        res_inv = mlp.compute_v46_c5_switch_flag(
            qqq_bias50=-5.0, smh_bias50=-6.0, spy_bias50=-4.0,
            spy_bias20=-3.0, qqq_bias20=-4.0, breadth_ma20=20.0,
            vixy_pos60=0.95, vixy_bias20=15.0, vix_proxy=38.0,
            cur_tk_c=20.0, cur_ema5=21.0, cur_ema10=22.0, cur_ema20=23.0,
            cur_b20=-8.0, cur_b50=-10.0, cur_ret3=-6.0, cur_ret5=-7.0, cur_down4=4.0,
            ticker="SOXS",
        )
        self.assertFalse(res_inv["c5_switch_active"])
        self.assertEqual(res_inv["v46_regime_mode"], "NORMAL_BASE")

    def test_normal_base_exactness_bear_c5_behavior_causality_and_band_invariants(self):
        as_of_sat = datetime(2026, 11, 21, 12, 0)

        # Calm bull panel: must stay NORMAL_BASE with fft_weight=0.50 and bit-exact Base wave_ret
        panel_calm = _make_synthetic_panel(bear_tail=False)
        res_calm = mlp.generate_time_cycle_projections(
            panel_calm,
            target_tickers=["SOXL", "NVDA", "SOXS"],
            forecast_days=15,
            as_of=as_of_sat,
        )
        piv_calm = panel_calm.pivot(index="Date", columns="Ticker", values="Close").sort_index().dropna(subset=["SMH", "QQQ"])
        for tk in ("SOXL", "NVDA"):
            item = res_calm[tk]
            self.assertEqual(item["v46_regime_mode"], "NORMAL_BASE")
            self.assertFalse(item["bear_inertia_active"])
            self.assertAlmostEqual(item["fft_weight"], 0.50, places=6)
            expected_w = _compute_independent_base_wave(piv_calm, tk)
            actual_w = np.array([p["wave_ret"] for p in item["projection"]], dtype=float)
            self.assertTrue(np.allclose(actual_w, np.round(expected_w, 6), atol=1e-6))

        # Bear panic panel: SOXL/NVDA switch to BEAR_INERTIA_C5 (fft_weight=0.0), SOXS stays NORMAL_BASE
        panel_bear = _make_synthetic_panel(bear_tail=True)
        res_bear = mlp.generate_time_cycle_projections(
            panel_bear,
            target_tickers=["SOXL", "NVDA", "SOXS"],
            forecast_days=15,
            as_of=as_of_sat,
        )
        t_last = pd.Timestamp("2026-11-20")
        t_dates = pd.bdate_range(end="2026-11-20", periods=900)
        cutoff_16 = t_dates[-1 - 16]

        for tk in ("SOXL", "NVDA"):
            item = res_bear[tk]
            self.assertEqual(item["v46_regime_mode"], "BEAR_INERTIA_C5")
            self.assertTrue(item["bear_inertia_active"])
            self.assertEqual(item["fft_weight"], 0.0)
            self.assertEqual(len(item["path_p15"]), 15)
            self.assertEqual(len(item["path_p85"]), 15)
            # Causality assertion on matched analog period end date <= t - 16 trading days
            period_str = item["analog_match"]["period"]
            end_dt_s = period_str.split("~")[1].strip()[:10]
            self.assertLessEqual(pd.Timestamp(end_dt_s), cutoff_16)
            self.assertLess(pd.Timestamp(end_dt_s), t_last)

            # Invariant check P15 <= Main <= P85 across T+1..T+15
            for d_i in range(15):
                p_main = item["projection"][d_i]
                p_15 = item["path_p15"][d_i]
                p_85 = item["path_p85"][d_i]
                self.assertLessEqual(p_15["price"], p_main["price"])
                self.assertLessEqual(p_main["price"], p_85["price"])
                self.assertLessEqual(p_15["pct"], p_main["pct"])
                self.assertLessEqual(p_main["pct"], p_85["pct"])

        self.assertEqual(res_bear["SOXS"]["v46_regime_mode"], "NORMAL_BASE")
        self.assertFalse(res_bear["SOXS"]["bear_inertia_active"])

        # Live alignment scaling: -12% and +15% price shifts preserve P15 <= Main <= P85 proportionality
        soxl_tc = {"SOXL": json.loads(json.dumps(res_bear["SOXL"]))}
        orig_anchor = soxl_tc["SOXL"]["anchor_price"]
        for mult in (0.88, 1.15):
            test_dict = json.loads(json.dumps(soxl_tc))
            new_px = round(orig_anchor * mult, 2)
            mlp.align_time_cycle_projections_with_live(
                tc_dict=test_dict,
                live_prices={"SOXL": new_px},
                session_code="REGULAR",
                as_of=datetime(2026, 11, 23, 10, 30),
            )
            aligned = test_dict["SOXL"]
            self.assertAlmostEqual(aligned["anchor_price"], new_px, places=2)
            for d_i in range(15):
                p_main = aligned["projection"][d_i]
                p_15 = aligned["path_p15"][d_i]
                p_85 = aligned["path_p85"][d_i]
                self.assertLessEqual(p_15["price"], p_main["price"])
                self.assertLessEqual(p_main["price"], p_85["price"])
                self.assertAlmostEqual(p_main["pct"], round(((p_main["price"] - new_px) / new_px) * 100.0, 1), delta=0.2)
            self.assertEqual(aligned["p15_t5_pct"], aligned["path_p15"][4]["pct"])
            self.assertEqual(aligned["p15_t15_pct"], aligned["path_p15"][14]["pct"])
            self.assertEqual(aligned["p85_t15_pct"], aligned["path_p85"][14]["pct"])

    def test_snapshot_and_discord_check_embed_bear_inertia_annotation(self):
        snap_mock = {
            "updated_at_et": "2026-11-22 18:00",
            "session_code": "WEEKEND",
            "session_name": "周末休市",
            "market_regime": "BEAR_DEFENSE",
            "weather": "🌧️ 空头防守",
            "vix_regime": {"vix_spot": 30.5, "vix_hard_brake": True},
            "tickers": {
                "SOXL": {
                    "ticker": "SOXL",
                    "name": "3倍做多半导体",
                    "cat": "核心ETF",
                    "price": 30.88,
                    "last": 30.88,
                    "pct_change": -12.0,
                    "effective_pct": -12.0,
                    "vwap": 34.10,
                    "decision_stance": "BEAR",
                    "decision_verdict": "🔴 空头占优 · 严禁左侧接刀",
                    "bear_inertia_active": True,
                    "v46_regime_mode": "BEAR_INERTIA_C5",
                    "p15_t5_pct": -6.8,
                },
                "SOXS": {
                    "ticker": "SOXS",
                    "name": "3倍做空半导体",
                    "cat": "反向对冲",
                    "price": 18.50,
                    "last": 18.50,
                    "pct_change": 11.2,
                    "effective_pct": 11.2,
                    "vwap": 17.80,
                    "is_inverse_hedge": True,
                    "decision_stance": "NEUTRAL",
                    "bear_inertia_active": True,
                    "p15_t5_pct": -3.0,
                },
            },
        }
        emb_soxl = drb.build_check_embed_dict("SOXL", snap_mock)
        exam_val = [f["value"] for f in emb_soxl["fields"] if "通俗体检" in f["name"]][0]
        self.assertIn("15日推演", exam_val)
        self.assertIn("空头惯性延续", exam_val)
        self.assertIn("-6.80%", exam_val)

        # Inverse ETF must never show equity bear-inertia suppression note
        emb_soxs = drb.build_check_embed_dict("SOXS", snap_mock)
        exam_soxs = [f["value"] for f in emb_soxs["fields"] if "通俗体检" in f["name"]][0]
        self.assertNotIn("空头惯性延续", exam_soxs)

    def test_sync_live_prices_to_predict_html_preserves_and_scales_v46_bands(self):
        import tempfile
        panel_bear = _make_synthetic_panel(bear_tail=True)
        tc_proj = mlp.generate_time_cycle_projections(
            panel_bear,
            target_tickers=["SOXL", "NVDA"],
            forecast_days=15,
            as_of=datetime(2026, 11, 21, 12, 0),
        )
        old_anchor = float(tc_proj["SOXL"]["anchor_price"])
        new_px = round(old_anchor * 0.88, 2)
        with tempfile.TemporaryDirectory() as tmpdir:
            dc_dir = os.path.join(tmpdir, "data_cache")
            os.makedirs(dc_dir, exist_ok=True)
            ai_path = os.path.join(dc_dir, "latest_ai_predictions.json")
            html_path = os.path.join(tmpdir, "predict_radar_5y.html")
            ai_payload = {
                "by_ticker": {
                    "SOXL": {
                        "ticker": "SOXL",
                        "latest_price": old_anchor,
                        "current_price": old_anchor,
                        "bear_inertia_active": True,
                        "v46_regime_mode": "BEAR_INERTIA_C5",
                    }
                },
                "all_predictions": [{"ticker": "SOXL", "latest_price": old_anchor}],
                "time_cycle_projections": tc_proj,
            }
            with open(ai_path, "w", encoding="utf-8") as f:
                json.dump(ai_payload, f)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><script>const timeCycleData = " + json.dumps(tc_proj) + ";</script></html>")

            with mock.patch.object(imr, "SCRATCH_DIR", tmpdir):
                ok = imr.sync_live_prices_to_predict_html(
                    macro_data={"SOXL": {"ticker": "SOXL", "active_price": new_px, "last": new_px}},
                    stocks_data=[],
                    session_code="REGULAR",
                )
            self.assertTrue(ok)
            updated_ai = json.load(open(ai_path, "r", encoding="utf-8"))
            soxl_tc = updated_ai["time_cycle_projections"]["SOXL"]
            self.assertEqual(soxl_tc["v46_regime_mode"], "BEAR_INERTIA_C5")
            self.assertTrue(soxl_tc["bear_inertia_active"])
            self.assertAlmostEqual(soxl_tc["anchor_price"], new_px, places=2)
            for d_i in range(15):
                pm = soxl_tc["projection"][d_i]
                p15 = soxl_tc["path_p15"][d_i]
                p85 = soxl_tc["path_p85"][d_i]
                self.assertLessEqual(p15["price"], pm["price"])
                self.assertLessEqual(pm["price"], p85["price"])
            self.assertEqual(updated_ai["by_ticker"]["SOXL"]["p15_t5_pct"], soxl_tc["p15_t5_pct"])
            self.assertEqual(updated_ai["all_predictions"][0]["p15_t5_pct"], soxl_tc["p15_t5_pct"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
