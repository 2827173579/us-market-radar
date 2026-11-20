# -*- coding: utf-8 -*-
"""
全局真 VIX 恐慌中枢 + 期限结构倒挂刹车 + 恐慌出清右侧抄底窗 + VIXY 文案纠偏 离线单元测试 (Zero Network)
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
if WORK_DIR not in sys.path:
    sys.path.insert(0, WORK_DIR)

import vix_regime_engine as vre
import ml_predictor_5y
import integrated_market_radar


class _DummyModel:
    def __init__(self, n_features: int, p_pos: float = 0.85):
        self.feature_importances_ = np.ones(n_features, dtype=float)
        self.p_pos = p_pos

    def predict_proba(self, X):
        n = len(X)
        return np.column_stack([np.full(n, 1.0 - self.p_pos), np.full(n, self.p_pos)])


def _make_series(values, start="2026-11-05"):
    idx = pd.bdate_range(start=start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


class TestVixRegimeEngine(unittest.TestCase):
    def test_01_normal_contango_and_pool_unchanged(self):
        # 79 只可交易标的池数量绝不能被 ^VIX / ^VIX3M / ^VVIX / ^TNX 污染
        etfs = set(integrated_market_radar.MONITORED_TARGETS["ETFS"].keys())
        stocks = set(integrated_market_radar.MONITORED_TARGETS["STOCKS"].keys())
        self.assertEqual(len(etfs) + len(stocks), 79)
        for vix_sym in ("^VIX", "^VIX3M", "^VVIX", "^TNX"):
            self.assertNotIn(vix_sym, etfs)
            self.assertNotIn(vix_sym, stocks)

        vix_s = _make_series([16.5, 16.2, 16.0, 15.8, 15.8])
        vxn_s = _make_series([19.8, 19.5, 19.2, 19.0, 18.8])
        vix3m_s = _make_series([19.0, 18.8, 18.6, 18.5, 18.4])
        vvix_s = _make_series([92.0, 91.0, 90.0, 89.0, 88.0])
        tnx_s = _make_series([4.25, 4.26, 4.24, 4.22, 4.21])

        snap = vre.compute_vix_regime_from_series(vix_s, vix3m_s, vvix_s, tnx_s, vxn_series=vxn_s)
        self.assertEqual(snap["term_structure_state"], "CONTANGO")
        self.assertLess(snap["vix_term_ratio"], 0.92)
        self.assertFalse(snap["vix_hard_brake"])
        self.assertFalse(snap["vix_bottom_signal"])
        self.assertEqual(snap["vix_state"], "CALM_CONTANGO")
        self.assertEqual(snap["pill_level"], "calm")
        self.assertIn("平稳", snap["pill_text"])
        self.assertFalse(snap["degraded"])

        reg = ml_predictor_5y.compute_market_regime_state(
            qqq_bias_ma20=2.0,
            spy_bias_ma20=1.5,
            qqq_bias_ma50=3.0,
            smh_bias_ma50=3.5,
            breadth_ma20_pct=65.0,
            vixy_pos_60=0.30,
            vix_regime=snap,
        )
        self.assertEqual(reg["market_regime"], "BULL_TREND")
        self.assertFalse(reg["vix_hard_brake"])

    def test_02_backwardation_forces_bear_defense_hard_brake(self):
        vix_s = _make_series([21.0, 23.0, 25.0, 26.8, 28.4])
        vix3m_s = _make_series([22.5, 23.2, 24.5, 25.4, 26.2])
        vvix_s = _make_series([105.0, 112.0, 118.0, 122.0, 126.0])
        tnx_s = _make_series([4.40, 4.42, 4.45, 4.46, 4.48])

        snap = vre.compute_vix_regime_from_series(vix_s, vix3m_s, vvix_s, tnx_s)
        self.assertGreaterEqual(snap["vix_term_ratio"], 1.00)
        self.assertEqual(snap["term_structure_state"], "BACKWARDATION")
        self.assertTrue(snap["vix_hard_brake"])
        self.assertFalse(snap["vix_bottom_signal"])
        self.assertEqual(snap["vix_state"], "BACKWARDATION_BRAKE")
        self.assertEqual(snap["pill_level"], "danger")
        self.assertIn("倒挂预警", snap["pill_text"])

        # 即便指数短线微正/宽度尚可，期限结构倒挂也必须强制锁定 BEAR_DEFENSE 硬刹车
        reg = ml_predictor_5y.compute_market_regime_state(
            qqq_bias_ma20=0.5,
            spy_bias_ma20=0.4,
            qqq_bias_ma50=1.0,
            smh_bias_ma50=1.2,
            breadth_ma20_pct=55.0,
            vixy_pos_60=0.45,
            vix_regime=snap,
        )
        self.assertEqual(reg["market_regime"], "BEAR_DEFENSE")
        self.assertTrue(reg["vix_hard_brake"])

        # 全池常规多头必须封顶 <= 48.0%，反向/避险标的豁免
        nvda_gate = ml_predictor_5y.apply_market_regime_winrate_gate(
            cal_prob_pct=78.0,
            ticker="NVDA",
            market_regime=reg["market_regime"],
            vix_regime=snap,
            price=155.0,
            vwap=150.0,
        )
        self.assertTrue(nvda_gate["is_regime_capped"])
        self.assertFalse(nvda_gate["vix_bottom_relaxed"])
        self.assertLessEqual(nvda_gate["gated_prob_pct"], 48.0)

        sqqq_gate = ml_predictor_5y.apply_market_regime_winrate_gate(
            cal_prob_pct=78.0,
            ticker="SQQQ",
            market_regime=reg["market_regime"],
            vix_regime=snap,
        )
        self.assertFalse(sqqq_gate["is_regime_capped"])
        self.assertGreater(sqqq_gate["gated_prob_pct"], 48.0)

    def test_03_panic_washout_bottom_window_causal_and_relaxed_cap(self):
        # 模拟：先冲高至 VIX=32.0 / ratio=1.08，随后回落至 VIX=25.0 / ratio=0.96
        vix_vals = [19.0, 22.0, 26.5, 32.0, 29.0, 26.8, 25.0]
        vix3m_vals = [21.0, 22.8, 25.8, round(32.0 / 1.08, 3), 28.8, 27.1, round(25.0 / 0.96, 3)]
        vvix_vals = [98.0, 108.0, 118.0, 128.0, 116.0, 108.0, 102.0]
        tnx_vals = [4.50, 4.48, 4.46, 4.42, 4.40, 4.38, 4.35]

        dates = pd.bdate_range("2026-11-10", periods=len(vix_vals))
        vix_s = pd.Series(vix_vals, index=dates)
        vix3m_s = pd.Series(vix3m_vals, index=dates)
        vvix_s = pd.Series(vvix_vals, index=dates)
        tnx_s = pd.Series(tnx_vals, index=dates)

        # 1) 零穿越核验：截至峰值日 T-3 时，绝对不能预知未来回落
        peak_day = dates[3]
        snap_peak = vre.compute_vix_regime_from_series(vix_s, vix3m_s, vvix_s, tnx_s, as_of_date=peak_day)
        self.assertTrue(snap_peak["vix_hard_brake"])
        self.assertFalse(snap_peak["vix_bottom_signal"])
        self.assertAlmostEqual(snap_peak["vix_spot"], 32.0, places=1)
        self.assertAlmostEqual(snap_peak["vix_term_ratio"], 1.08, places=2)

        # 2) 回落日 T：触发 vix_bottom_signal = True（恐慌出清抄底窗）
        snap_washout = vre.compute_vix_regime_from_series(vix_s, vix3m_s, vvix_s, tnx_s)
        self.assertFalse(snap_washout["vix_hard_brake"])
        self.assertTrue(snap_washout["vix_bottom_signal"])
        self.assertEqual(snap_washout["vix_state"], "WASHOUT_BOTTOM_WINDOW")
        self.assertEqual(snap_washout["pill_level"], "bottom")
        self.assertIn("恐慌出清抄底窗", snap_washout["pill_text"])
        self.assertAlmostEqual(snap_washout["vix_10d_peak"], 32.0, places=2)
        self.assertGreaterEqual(snap_washout["vix_drawdown_from_peak_pct"], 8.0)

        # 3) 对站在 VWAP 之上的非熔断优质标的，平滑放宽做多上限至 58.0%
        fcols = list(ml_predictor_5y.FEATURE_COLS)
        rows = [
            {
                "Ticker": "NVDA", "Sector": "Semiconductors", "Close": 152.0, "VWAP": 150.0,
                "ATR_Pct": 2.8, "Bias_MA50": -1.5, "Bias_MA20": -1.0, "RSI_14": 46.0,
                "Low_Prev": 148.0, "EMA10_20_Spread": -0.5, "Is_Core_28": True,
                "Prob_1": 0.85, "Prob_Short_1": 0.30, "History_Bars": 1200,
                "is_crash_halt": False, "is_cooling_halt": False,
            },
            {
                "Ticker": "META", "Sector": "Neo_Cloud", "Close": 595.0, "VWAP": 605.0,
                "ATR_Pct": 2.4, "Bias_MA50": -2.0, "Bias_MA20": -2.0, "RSI_14": 44.0,
                "Low_Prev": 590.0, "EMA10_20_Spread": -0.6, "Is_Core_28": True,
                "Prob_1": 0.85, "Prob_Short_1": 0.35, "History_Bars": 1200,
                "is_crash_halt": False, "is_cooling_halt": False,
            },
            {
                "Ticker": "LUNR", "Sector": "Neo_Cloud", "Close": 18.0, "VWAP": 17.5,
                "ATR_Pct": 4.5, "Bias_MA50": -3.0, "Bias_MA20": -2.5, "RSI_14": 40.0,
                "Low_Prev": 17.0, "EMA10_20_Spread": -1.0, "Is_Core_28": False,
                "Prob_1": 0.85, "Prob_Short_1": 0.45, "History_Bars": 1200,
                "is_crash_halt": True, "is_cooling_halt": False,
            },
        ]
        for r in rows:
            for fc in fcols:
                r.setdefault(fc, 0.0)
        latest_df = pd.DataFrame(rows)
        eval_df = pd.DataFrame([
            {"Date": "2026-11-18", "Ticker": "NVDA", "Sector": "Semiconductors", "Prob_1": 0.65, "Calibrated_Prob": 0.65, "Prob_Short_1": 0.35, "Calibrated_Short_Prob": 0.35, "Realized_Return": 2.5, "Label": 1, "Label_Short": 0, "Realized_Return_Short": -2.5},
        ])
        macro_washout = {
            "status": "⛅ 阴天防守",
            "market_regime": "BEAR_DEFENSE",
            "market_regime_cn": "🔴 空头防守体制",
            "breadth_ma20_pct": 32.0,
            "qqqm_bias": -2.0,
            "smh_bias": -1.5,
            "vixy_pos": 0.68,
            "vixy_pos_60": 0.68,
            "vix_regime": snap_washout,
        }
        with patch.object(ml_predictor_5y, "build_and_save_live_prediction_scorecard", return_value={"win_rate_7d_pct": 55.0, "win_rate_30d_pct": 58.0, "sample_count_7d": 7, "sample_count_30d": 30, "is_degraded": False}):
            tables = ml_predictor_5y.generate_report_tables(
                oos_df=eval_df,
                model=_DummyModel(len(fcols), p_pos=0.85),
                feature_cols=fcols,
                latest_features_df=latest_df,
                macro_weather=macro_washout,
                is_realtime=True,
                raw_panel=None,
            )
        by_tk = {r["ticker"]: r for r in tables["all_live_predictions"]}
        self.assertGreater(by_tk["NVDA"]["calibrated_win_rate_pct"], 48.0)
        self.assertLessEqual(by_tk["NVDA"]["calibrated_win_rate_pct"], 58.0)
        # 未站上 VWAP 的 META 和 暴跌熔断的 LUNR 依然死锁 <= 48.0%
        self.assertLessEqual(by_tk["META"]["calibrated_win_rate_pct"], 48.0)
        self.assertLessEqual(by_tk["LUNR"]["calibrated_win_rate_pct"], 48.0)
        self.assertEqual(tables["vix_regime"]["vix_state"], "WASHOUT_BOTTOM_WINDOW")

    def test_04_premarket_causal_ffill_and_graceful_degradation(self):
        # 1) 盘前时段 ^VIX 比 ^VIX3M 多出当日 K 线，因果前向对齐不得报错
        d_vix = pd.bdate_range("2026-11-16", periods=5)
        d_vix3m = d_vix[:-1]
        vix_s = pd.Series([22.0, 24.0, 26.0, 28.44, 29.25], index=d_vix)
        vix3m_s = pd.Series([22.5, 23.5, 24.2, 27.03], index=d_vix3m)
        snap_pm = vre.compute_vix_regime_from_series(vix_s, vix3m_s, None, None)
        self.assertAlmostEqual(snap_pm["vix_term_ratio"], round(29.25 / 27.03, 3), places=3)
        self.assertTrue(snap_pm["vix_hard_brake"])
        self.assertTrue(snap_pm["degraded"])  # VVIX/TNX 缺失

        # 2) ^VIX3M / ^VVIX / ^TNX 全缺失时优雅降级不触发错误抄底信号
        snap_only_vix = vre.compute_vix_regime_from_series(
            _make_series([32.0, 25.0]), None, None, None
        )
        self.assertIsNone(snap_only_vix["vix_term_ratio"])
        self.assertFalse(snap_only_vix["vix_bottom_signal"])
        self.assertEqual(snap_only_vix["term_structure_state"], "UNKNOWN")

        # 3) 全网抓取异常 + 缓存文件验证：不崩溃并安全降级
        with tempfile.TemporaryDirectory() as tmpd:
            cache_p = os.path.join(tmpd, "vix_regime_cache.json")
            with patch.object(vre.yf, "download", side_effect=RuntimeError("network down")):
                with patch.object(vre.yf, "Ticker", side_effect=RuntimeError("ticker down")):
                    snap_empty = vre.get_vix_regime_snapshot(
                        force_refresh=True, cache_path=cache_p, allow_network=True
                    )
            self.assertEqual(snap_empty["vix_state"], "UNAVAILABLE")
            self.assertIsNone(snap_empty["vix_spot"])

            # 写入有效缓存后开启 TTL 命中校验 (allow_network=False)
            vre._write_cached_snapshot(cache_p, snap_pm)
            snap_cached = vre.get_vix_regime_snapshot(
                force_refresh=False, cache_path=cache_p, ttl_seconds=180, allow_network=False
            )
            self.assertAlmostEqual(snap_cached["vix_spot"], 29.25, places=2)
            self.assertEqual(snap_cached["vix_state"], "BACKWARDATION_BRAKE")

    def test_05_vixy_card_labels_and_widget_html_integrity(self):
        with open(os.path.join(WORK_DIR, "create_futu_terminal.py"), "r", encoding="utf-8") as f:
            futu_src = f.read()

        self.assertIn("📉 波动率期货工具（含展期损耗·无财报）", futu_src)
        self.assertIn("20日对冲脉冲", futu_src)
        self.assertIn("⚡避险飙升", futu_src)
        self.assertIn('id="pVixySpotHint"', futu_src)
        self.assertIn('id="pVixRegimePill"', futu_src)
        self.assertIn('id="vixDetailPopover"', futu_src)

        # 验证 VIXY 分支执行在 else 常规 👑抗跌 / 🟢 近期无财报 之前，避免混入违和文案
        idx_vixy_rs = futu_src.index("isVixySym")
        idx_rs_tag_block = futu_src.index("if (rs20dEl)")
        self.assertGreater(futu_src.index("⚡避险飙升"), idx_rs_tag_block)
        self.assertLess(futu_src.index("⚡避险飙升"), futu_src.index('rsTag = "👑抗跌"'))

        idx_earn = futu_src.index("const earnEl = document.getElementById(\"pEarningsDate\")")
        idx_vixy_earn = futu_src.index("📉 波动率期货工具（含展期损耗·无财报）", idx_earn)
        idx_normal_earn = futu_src.index("🟢 近期无财报", idx_earn)
        self.assertLess(idx_vixy_earn, idx_normal_earn)

        # 验证跨页共享渲染器无重复 ID 或漏闭合
        css_s, html_s, js_s = vre.render_vix_pill_widget_parts(popover_align="left")
        self.assertIn("pVixRegimePill", html_s)
        self.assertIn("vixDetailPopover", html_s)
        self.assertIn("^VXN", html_s)
        self.assertIn("VXN - VIX", html_s)
        self.assertNotIn('id="', js_s)
        self.assertTrue(len(css_s) > 100)

    def test_06_vxn_tech_fear_spread_and_degradation(self):
        # 1) 科技/半导体极端去杠杆：VXN >= 32.0 且 VXN - VIX >= 5.5
        vix_s = _make_series([24.0, 26.0, 28.0, 28.78])
        vxn_s = _make_series([30.0, 32.5, 35.0, 36.48])
        vix3m_s = _make_series([24.5, 25.5, 26.2, 27.03])
        snap_tech_stress = vre.compute_vix_regime_from_series(vix_s, vix3m_s, None, None, vxn_series=vxn_s)
        self.assertIn("^VXN", snap_tech_stress["available_symbols"])
        self.assertAlmostEqual(snap_tech_stress["vxn_spot"], 36.48, places=2)
        self.assertAlmostEqual(snap_tech_stress["vxn_vix_spread"], 7.70, places=2)
        self.assertEqual(snap_tech_stress["tech_fear_label"], "🔥 科技/半导体极端去杠杆（VXN 显著高于大盘）")

        # 2) 零穿越截断 + 科技恐慌回落缓解：VXN 峰值 >= 32.0 且自高点回撤 >= 8.0%
        vix_cool = _make_series([26.0, 29.5, 27.0, 24.5])
        vxn_cool = _make_series([31.0, 38.0, 34.5, 32.5])  # (38-32.5)/38 = 14.47% >= 8%
        vix3m_cool = _make_series([26.5, 27.5, 27.2, 26.5])
        snap_cool = vre.compute_vix_regime_from_series(vix_cool, vix3m_cool, None, None, vxn_series=vxn_cool)
        self.assertAlmostEqual(snap_cool["vxn_10d_peak"], 38.0, places=2)
        self.assertGreaterEqual(snap_cool["vxn_drawdown_from_peak_pct"], 8.0)
        self.assertEqual(snap_cool["tech_fear_label"], "🟢 科技恐慌回落 · 半导体/AI 压力缓解")

        # 3) ^VXN 缺失或拉取失败时优雅降级不崩
        snap_no_vxn = vre.compute_vix_regime_from_series(vix_s, vix3m_s, None, None, vxn_series=None)
        self.assertIsNone(snap_no_vxn["vxn_spot"])
        self.assertIsNone(snap_no_vxn["vxn_vix_spread"])
        self.assertEqual(snap_no_vxn["tech_fear_label"], "⚪ 科技波动暂缺")
        self.assertAlmostEqual(snap_no_vxn["vix_spot"], 28.78, places=2)

    def test_07_table4a_bear_script_pill_and_table4b_spy_no_sqqq(self):
        # 1) get_short_action_directive: SPY/IWM/DIA 禁止推荐 SQQQ，仅 QQQ/纳指科技类推荐 SQQQ
        for broad_etf in ("SPY", "IWM", "DIA"):
            act, cls_ = ml_predictor_5y.get_short_action_directive(
                calibrated_short_win_rate_pct=72.0, rsi_14=50.0, is_crash_halt=False,
                ticker=broad_etf, sector="大盘与宏观ETF", is_etf=True,
            )
            self.assertNotIn("SQQQ", act, f"{broad_etf} must not suggest SQQQ: {act}")
            self.assertEqual(act, "🛡️ 建议买入反向ETF对冲")
            self.assertEqual(cls_, "short-action-etf")

        qqq_act, _ = ml_predictor_5y.get_short_action_directive(
            calibrated_short_win_rate_pct=72.0, rsi_14=50.0, is_crash_halt=False,
            ticker="QQQ", sector="大盘与宏观ETF", is_etf=True,
        )
        self.assertEqual(qqq_act, "🛡️ 建议买入SQQQ对冲")

        # 2) render_html_dashboard 验证表 4-A 空头期中性按钮文案与表 4-B SPY 无 SQQQ 穿帮
        import re as _re
        base_rows = [
            {
                "rank": 1, "ticker": "NVDA", "sector_cn": "半导体芯片", "sector_rating": "⭐⭐⭐⭐⭐",
                "latest_price": 150.0, "atr_pct": 3.0, "bias_ma50": -8.0, "calibrated_win_rate_pct": 44.0,
                "predicted_win_rate_pct": 52.0, "sector_pf": 1.5, "action": "🛡️ 空头体制封顶·暂停常规做多",
                "target_profit_price": 160.0, "target_profit_pct": 6.7, "stop_loss_price": 144.0, "stop_loss_pct": 4.0,
                "macro_temp": "⛅ 阴天防守", "is_etf": False, "is_broken": False, "is_short_covering": False,
                "calibrated_short_win_rate_pct": 55.0, "model_raw_short_prob_pct": 52.0,
                "short_target_profit_price": 142.0, "short_target_profit_pct": 5.3,
                "short_stop_loss_price": 155.0, "short_stop_loss_pct": 3.3,
                "dollar_pcr": 1.1, "pcr_sentiment": "⛅ 中性", "has_options": True,
                "short_action": "⚡ 逢高反抽沽空/买Put", "short_rank": 2,
                "scenario_script": "【黄金坑做多剧本】仅作参考推演",
            },
            {
                "rank": 2, "ticker": "SPY", "sector_cn": "大盘与宏观ETF", "sector_rating": "⭐⭐⭐☆☆",
                "latest_price": 580.0, "atr_pct": 1.2, "bias_ma50": -2.0, "calibrated_win_rate_pct": 42.0,
                "predicted_win_rate_pct": 48.0, "sector_pf": 1.2, "action": "🛡️ 暴雨空仓·严禁左侧接刀",
                "target_profit_price": 595.0, "target_profit_pct": 2.6, "stop_loss_price": 570.0, "stop_loss_pct": 1.7,
                "macro_temp": "🌧️ 暴雨空仓", "is_etf": True, "is_broken": False, "is_short_covering": False,
                "calibrated_short_win_rate_pct": 70.0, "model_raw_short_prob_pct": 66.0,
                "short_target_profit_price": 565.0, "short_target_profit_pct": 2.6,
                "short_stop_loss_price": 590.0, "short_stop_loss_pct": 1.7,
                "dollar_pcr": 1.4, "pcr_sentiment": "🐻 偏空", "has_options": True,
                "short_action": "🛡️ 建议买入SQQQ对冲",  # 模拟旧缓存脏字段也要被渲染层纠偏
                "short_rank": 1,
                "scenario_script": "【黄金坑做多剧本】兜底防御检验",
            },
        ]
        tables_mock = {
            "kpi": {"high_conviction_win_rate": 60.0, "profit_factor": 1.5, "top_sector": "半导体芯片", "top_feature": "Alpha", "sync_time": "2026-11-20", "data_mode": "Test"},
            "macro_weather": {"status": "⛅ 阴天防守", "desc": "test", "market_regime": "BEAR_DEFENSE", "market_regime_cn": "🔴 空头防守体制", "breadth_ma20_pct": 15.0, "qqqm_bias": -3.0, "smh_bias": -4.0, "vixy_pos": 0.7, "smh_igv": -1.0, "vix_regime": {"vix_spot": 28.8, "vxn_spot": 36.5, "vxn_vix_spread": 7.7, "tech_fear_label": "🔥 科技/半导体极端去杠杆（VXN 显著高于大盘）"}},
            "table1": [{"threshold_label": ">= 60%", "trigger_count": 100, "pos_win_rate_pct": 60.0, "touch_win_rate_pct": 55.0, "avg_realized_return_pct": 2.1, "win_loss_ratio": 1.5, "profit_factor": 1.5}],
            "table2": [{"sector_name_cn": "半导体芯片", "sector_key": "Semiconductors", "tickers_count": 10, "oos_total_signals": 200, "high_conviction_signals": 50, "high_conviction_pos_win_rate_pct": 60.0, "high_conviction_touch_win_rate_pct": 55.0, "avg_realized_return_pct": 2.5, "win_loss_ratio": 1.6, "profit_factor": 1.6, "alpha_rating": "⭐⭐⭐⭐⭐"}],
            "table3": [{"rank": 1, "feature": "Alpha", "category": "个股", "category_tag": "stock", "total_gain": 100.0, "gain_share_pct": 10.0, "rationale": "test"}],
            "table4": [base_rows[0]],
            "top_short": [base_rows[1], base_rows[0]],
            "all_live_predictions": base_rows,
            "time_cycle_projections": {},
        }
        with tempfile.TemporaryDirectory() as tmpd:
            out_html = os.path.join(tmpd, "test_predict.html")
            ml_predictor_5y.render_html_dashboard(tables_mock, output_path=out_html)
            with open(out_html, "r", encoding="utf-8") as hf:
                htxt = hf.read()

        t4_long = htxt.split('id="t4-tbody"')[1].split("</tbody>")[0]
        self.assertIn("📋 查看备选剧本 ▾", t4_long)
        self.assertNotIn("📋 黄金坑低吸 ▾", t4_long)
        self.assertIn("t4-script-detail-row", t4_long)

        spy_row_m = _re.search(r'<tr class="t4-short-row"[^>]*data-ticker="SPY".*?</tr>', htxt, _re.DOTALL)
        self.assertIsNotNone(spy_row_m)
        self.assertIn("🛡️ 建议买入反向ETF对冲", spy_row_m.group(0))
        self.assertNotIn("SQQQ", spy_row_m.group(0))


if __name__ == "__main__":
    unittest.main()
