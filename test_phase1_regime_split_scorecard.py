# -*- coding: utf-8 -*-
"""
阶段一核心回归与对抗测试：
1. 三态牛熊体制门禁 (BULL_TREND / NEUTRAL_CHOP / BEAR_DEFENSE) + <=48% 硬封顶与反向/避险豁免
2. Walk-Forward 折内时间衰减样本权重 (Zero Lookahead Bias / 严格隔离)
3. 次新股/短样本 (<756日) 贝叶斯强收缩 (SNDK 445日 假高胜率彻底消除)
4. 拆股除权自动检测与自愈防护 (SNDK 1拆8 自愈 + -87.5%/-45% 虚假止损拦截)
5. 实盘预测成绩单 (7D/30D 滚动命中率 + 历史待核验队列结算 + <45% 黄灯预警)
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

import ml_predictor_5y
import integrated_market_radar


class _DummyModel:
    def __init__(self, n_features: int, p_pos: float = 0.85):
        self.feature_importances_ = np.ones(n_features, dtype=float)
        self.p_pos = p_pos

    def predict_proba(self, X):
        n = len(X)
        return np.column_stack([np.full(n, 1.0 - self.p_pos), np.full(n, self.p_pos)])


class TestPhase1RegimeSplitScorecard(unittest.TestCase):
    def test_01_market_regime_gate_and_bear_cap(self):
        bear_state = ml_predictor_5y.compute_market_regime_state(
            qqq_bias_ma20=-2.5,
            spy_bias_ma20=-1.8,
            qqq_bias_ma50=-3.6,
            smh_bias_ma50=-4.1,
            breadth_ma20_pct=24.0,
            vixy_pos_60=0.82,
            qqq_ret_5d=-4.2,
            qqq_down_days_5d=4,
        )
        self.assertEqual(bear_state["market_regime"], "BEAR_DEFENSE")
        self.assertLess(bear_state["regime_score"], 0.0)

        bull_state = ml_predictor_5y.compute_market_regime_state(
            qqq_bias_ma20=2.1,
            spy_bias_ma20=1.7,
            qqq_bias_ma50=3.4,
            smh_bias_ma50=4.0,
            breadth_ma20_pct=68.0,
            vixy_pos_60=0.25,
            qqq_ret_5d=2.8,
            qqq_down_days_5d=1,
            defensive_flight_alert=False,
        )
        self.assertEqual(bull_state["market_regime"], "BULL_TREND")

        fcols = list(ml_predictor_5y.FEATURE_COLS)
        # 补齐全部 26 维因果特征列（并避开全 1.0 恒定占位符触发路径）
        base_rows = [
            {
                "Ticker": "NVDA", "Sector": "Semiconductors", "Close": 150.0,
                "ATR_Pct": 2.8, "Bias_MA50": -1.5, "Bias_MA20": -2.0, "RSI_14": 44.0,
                "Low_Prev": 148.0, "EMA10_20_Spread": -0.8, "Is_Core_28": True,
                "Prob_1": 0.85, "Prob_Short_1": 0.35, "History_Bars": 1200,
            },
            {
                "Ticker": "SQQQ", "Sector": "Index_Macro_Hedge", "Close": 22.0,
                "ATR_Pct": 4.2, "Bias_MA50": 6.0, "Bias_MA20": 4.0, "RSI_14": 60.0,
                "Low_Prev": 21.0, "EMA10_20_Spread": 1.2, "Is_Core_28": False,
                "Prob_1": 0.85, "Prob_Short_1": 0.25, "History_Bars": 1200,
            },
            {
                "Ticker": "GLD", "Sector": "Index_Macro_Hedge", "Close": 250.0,
                "ATR_Pct": 1.1, "Bias_MA50": 2.0, "Bias_MA20": 1.0, "RSI_14": 54.0,
                "Low_Prev": 248.0, "EMA10_20_Spread": 0.5, "Is_Core_28": False,
                "Prob_1": 0.85, "Prob_Short_1": 0.25, "History_Bars": 1200,
            },
        ]
        for r in base_rows:
            for fc in fcols:
                r.setdefault(fc, 0.0)
        latest_df = pd.DataFrame(base_rows)
        eval_df = pd.DataFrame([
            {"Date": "2026-11-10", "Ticker": "NVDA", "Sector": "Semiconductors", "Prob_1": 0.62, "Calibrated_Prob": 0.62, "Prob_Short_1": 0.40, "Calibrated_Short_Prob": 0.40, "Realized_Return": 2.0, "Label": 1, "Label_Short": 0, "Realized_Return_Short": -2.0},
            {"Date": "2026-11-11", "Ticker": "SQQQ", "Sector": "Index_Macro_Hedge", "Prob_1": 0.65, "Calibrated_Prob": 0.65, "Prob_Short_1": 0.35, "Calibrated_Short_Prob": 0.35, "Realized_Return": 3.0, "Label": 1, "Label_Short": 0, "Realized_Return_Short": -3.0},
        ])
        macro_bear = {
            "status": "🌧️ 暴雨空仓",
            "market_regime": "BEAR_DEFENSE",
            "market_regime_cn": "🔴 空头防守体制",
            "breadth_ma20_pct": 22.0,
            "qqqm_bias": -3.2,
            "smh_bias": -4.1,
            "vixy_pos": 0.85,
            "vixy_pos_60": 0.85,
        }
        with patch.object(ml_predictor_5y, "build_and_save_live_prediction_scorecard", return_value={"win_rate_7d_pct": 55.0, "win_rate_30d_pct": 58.0, "sample_count_7d": 7, "sample_count_30d": 30, "is_degraded": False, "warning_text": "✅ 稳健"}):
            tables = ml_predictor_5y.generate_report_tables(
                oos_df=eval_df,
                model=_DummyModel(len(fcols), p_pos=0.85),
                feature_cols=fcols,
                latest_features_df=latest_df,
                macro_weather=macro_bear,
                is_realtime=True,
                raw_panel=None,
            )
        by_tk = {r["ticker"]: r for r in tables["all_live_predictions"]}
        nvda = by_tk["NVDA"]
        sqqq = by_tk["SQQQ"]
        gld = by_tk["GLD"]

        self.assertTrue(nvda["is_regime_capped"])
        self.assertLessEqual(nvda["calibrated_win_rate_pct"], 48.0)
        for forbidden in ("极高确定性右侧做多", "逢低吸筹", "博弈空头回补"):
            self.assertNotIn(forbidden, nvda["action"])

        self.assertFalse(sqqq["is_regime_capped"])
        self.assertGreater(sqqq["calibrated_win_rate_pct"], 48.0)
        self.assertFalse(gld["is_regime_capped"])
        self.assertGreater(gld["calibrated_win_rate_pct"], 48.0)

    def test_02_time_decay_weights_zero_lookahead(self):
        dates_full = pd.bdate_range("2021-01-04", "2026-11-15")
        cutoff = pd.Timestamp("2025-06-30")
        dates_fold = dates_full[dates_full <= cutoff]

        w_fold = ml_predictor_5y.compute_time_decay_sample_weights(
            pd.Series(dates_fold), fold_max_date=cutoff, half_life_trading_days=210.0, min_weight=0.18
        )
        w_fold_only = ml_predictor_5y.compute_time_decay_sample_weights(
            pd.Series(dates_fold), fold_max_date=None, half_life_trading_days=210.0, min_weight=0.18
        )
        self.assertTrue(np.allclose(w_fold, w_fold_only, atol=1e-9))
        self.assertAlmostEqual(float(np.mean(w_fold)), 1.0, places=4)
        self.assertGreater(float(w_fold[-1]), float(w_fold[0]) * 3.0)

        w_future = ml_predictor_5y.compute_time_decay_sample_weights(
            pd.Series(dates_full), fold_max_date=pd.Timestamp("2026-11-15"), half_life_trading_days=210.0, min_weight=0.18
        )
        self.assertEqual(len(w_fold), len(dates_fold))
        self.assertEqual(len(w_future), len(dates_full))

    def test_03_short_sample_bayesian_shrinkage(self):
        res_sndk = ml_predictor_5y.apply_short_history_bayesian_shrinkage(0.827, 445)
        self.assertTrue(res_sndk["is_short_sample"])
        self.assertLessEqual(res_sndk["shrunk_pct"], 55.0)
        self.assertIn("445日", res_sndk["sample_shrinkage_tag"])
        self.assertLess(res_sndk["ci_low"], res_sndk["shrunk_pct"])
        self.assertGreater(res_sndk["ci_high"], res_sndk["shrunk_pct"])

        res_mid = ml_predictor_5y.apply_short_history_bayesian_shrinkage(0.70, 600)
        self.assertTrue(res_mid["is_short_sample"])
        expected_mid = 50.0 + (70.0 - 50.0) * (600.0 / (600.0 + 500.0))
        self.assertAlmostEqual(res_mid["shrunk_pct"], expected_mid, places=1)

        res_mature = ml_predictor_5y.apply_short_history_bayesian_shrinkage(0.75, 1000)
        self.assertFalse(res_mature["is_short_sample"])
        self.assertAlmostEqual(res_mature["shrunk_pct"], 75.0, places=1)
        self.assertEqual(res_mature["sample_shrinkage_tag"], "")

    def test_04_sndk_split_1_for_8_heal_and_alert_guard(self):
        dates = pd.bdate_range(end="2026-11-19", periods=60)
        closes = np.linspace(710.0, 755.16, 60)
        df_sndk = pd.DataFrame({
            "Open": closes * 0.995,
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Volume": np.full(60, 1_000_000.0),
        }, index=dates)

        # 模拟 1 拆 8 当日实时行情未除权 pc 导致 -87.5% 假暴跌
        rq_raw = {
            "last": 94.40,
            "change": round(94.40 - 755.16, 2),
            "pct_change": -87.50,
            "high": 95.80,
            "low": 93.10,
        }
        df_healed, rq_healed, meta = integrated_market_radar.reconcile_live_quote_with_split_guard(
            "SNDK", df_sndk, rq=rq_raw, allow_network_on_jump=False
        )
        self.assertTrue(meta["split_detected"])
        self.assertEqual(meta["split_factor"], 8.0)
        self.assertFalse(meta["split_suspect_unhealed"])
        self.assertAlmostEqual(float(df_healed["Close"].iloc[-1]), 755.16 / 8.0, places=2)
        self.assertAlmostEqual(float(df_healed["Volume"].iloc[-1]), 8_000_000.0, places=0)
        self.assertLess(abs(rq_healed["pct_change"]), 1.0)
        self.assertFalse(integrated_market_radar.should_suppress_stop_loss_alert({"ticker": "SNDK", **rq_healed, **meta}))

        # 验证 AI S1/R1 也能按 1/8 同步归一化
        s1_n, r1_n, _ = integrated_market_radar._normalize_ai_levels_for_price(
            {"s1": 720.0, "r1": 800.0, "cal_win_rate": 54.0}, 94.40, meta
        )
        self.assertAlmostEqual(s1_n, 90.0, places=2)
        self.assertAlmostEqual(r1_n, 100.0, places=2)

        # 验证 ANT Day-0 IPO 豁免
        _, _, meta_ant = integrated_market_radar.reconcile_live_quote_with_split_guard(
            "ANT", df_sndk, rq={"last": 120.0, "change": 0.0, "pct_change": 0.0}, allow_network_on_jump=False
        )
        self.assertFalse(meta_ant["split_detected"])
        self.assertFalse(meta_ant["split_suspect_unhealed"])

        # 验证无法匹配标准拆股比例的 -45% 异常跳变置 split_suspect_unhealed=True 并拦截止损弹窗
        df_weird = pd.DataFrame({
            "Open": [100.0] * 10, "High": [102.0] * 10, "Low": [98.0] * 10,
            "Close": [100.0] * 9 + [55.0], "Volume": [500_000.0] * 10,
        }, index=pd.bdate_range(end="2026-11-19", periods=10))
        _, _, meta_weird = integrated_market_radar.reconcile_live_quote_with_split_guard(
            "TESTX", df_weird, rq=None, allow_network_on_jump=False
        )
        self.assertTrue(meta_weird["split_suspect_unhealed"])
        self.assertTrue(integrated_market_radar.should_suppress_stop_loss_alert({"ticker": "SNDK", "is_stop": True, "pct_change": -87.5}))
        self.assertTrue(integrated_market_radar.should_suppress_stop_loss_alert({"ticker": "TESTX", "is_stop": True, "pct_change": -45.0, **meta_weird}))

        # 验证 dispatch_wechat_radar_alerts & generate_and_sync_notifications 均严格拦截虚假 STOP_LOSS
        bad_stock = {
            "ticker": "SNDK", "name": "SanDisk", "cat": "存储",
            "active_price": 94.40, "last": 94.40, "pct_change": -87.5, "ext_pct": 0.0,
            "is_stop": True, "is_pit": False, "dist_to_pit": -20.0, "fib_low": 700.0, "fib_high": 760.0,
            "optimal_limit": 94.0, "entry_stop_loss": 88.0, "fractional_shares": 1.0,
            "is_crash_halt": False, "is_cooling_halt": False, "is_take_profit": False,
            "intraday_signal": "WAIT", "bias": -25.0, "rsi": 22.0,
            "split_suspect_unhealed": True,
        }
        with patch.object(integrated_market_radar, "load_alert_history", return_value={}), \
             patch.object(integrated_market_radar, "save_alert_history"), \
             patch.object(integrated_market_radar, "load_target_watchlist", return_value=[]), \
             patch.object(integrated_market_radar, "_SHARED_ALERTS", False), \
             patch.object(integrated_market_radar, "send_pushplus_wechat_alert", return_value=True) as mock_push:
            integrated_market_radar.dispatch_wechat_radar_alerts({}, [bad_stock], {"weather": "🌧️ 暴雨空仓"}, "2026-11-20 09:30:00")
            for call_args in mock_push.call_args_list:
                self.assertNotIn("STOP_LOSS", str(call_args))
                self.assertNotIn("跌破支撑线", str(call_args))
                self.assertNotIn("破位止损", str(call_args))

        with patch.object(integrated_market_radar, "load_terminal_notifications", return_value=[]), \
             patch.object(integrated_market_radar, "save_terminal_notifications") as mock_save_notif:
            notifs = integrated_market_radar.generate_and_sync_notifications(
                {"SPY": {"name": "标普500", "cat": "大盘基准", "last": 580.0, "pct_change": -1.2, "bias": -2.0, "rsi": 42.0}},
                [bad_stock],
                {"weather": "🌧️ 暴雨空仓", "desc": "test", "tactics": "defend"},
                "2026-11-20 09:30:00",
            )
            mock_save_notif.assert_called_once()
            stop_notifs = [n for n in notifs if n.get("category") == "stop_loss" and n.get("ticker") == "SNDK"]
            self.assertEqual(len(stop_notifs), 0)

    def test_05_live_prediction_scorecard_and_warning_flag(self):
        dates = pd.bdate_range(end="2026-11-19", periods=35)
        rows_bad_7d = []
        for idx, dt in enumerate(dates):
            is_last_7 = idx >= 28
            for j in range(10):
                win = (j < 3) if is_last_7 else (j < 7)
                rows_bad_7d.append({
                    "Date": dt,
                    "Ticker": f"TK{j}",
                    "Prob_1": 0.65,
                    "Calibrated_Prob": 0.65,
                    "Prob_Short_1": 0.35,
                    "Calibrated_Short_Prob": 0.35,
                    "Label": 1 if win else 0,
                    "Realized_Return": 2.0 if win else -2.0,
                    "Label_Short": 0 if win else 1,
                    "Realized_Return_Short": -2.0 if win else 2.0,
                })
        oos_df = pd.DataFrame(rows_bad_7d)

        with tempfile.TemporaryDirectory() as td:
            sc_path = os.path.join(td, "live_prediction_scorecard.json")
            seed_data = {
                "pending_predictions": [
                    {
                        "ticker": "NVDA",
                        "pred_date": dates[20].strftime("%Y-%m-%d"),
                        "direction": "LONG",
                        "pred_prob_pct": 66.0,
                        "entry_price": 100.0,
                    }
                ],
                "reconciled_live_records": [],
            }
            with open(sc_path, "w", encoding="utf-8") as f:
                json.dump(seed_data, f)

            raw_panel = pd.DataFrame({
                "Date": list(dates[21:27]),
                "Ticker": ["NVDA"] * 6,
                "Close": [101.0, 102.0, 103.0, 104.0, 106.0, 107.0],
            })
            sc = ml_predictor_5y.build_and_save_live_prediction_scorecard(
                oos_df=oos_df,
                raw_panel=raw_panel,
                all_live_rows=[{"ticker": "AAPL", "is_etf": False, "latest_price": 220.0, "calibrated_win_rate_pct": 58.0, "calibrated_short_win_rate_pct": 40.0}],
                scorecard_path=sc_path,
                save_to_disk=True,
            )
            self.assertAlmostEqual(sc["win_rate_7d_pct"], 30.0, places=1)
            self.assertTrue(sc["is_degraded"])
            self.assertEqual(sc["warning_text"], "⚠️ 模型近期失准·自动降权参考")
            self.assertEqual(sc["reconciled_live_count"], 1)
            self.assertEqual(sc["reconciled_live_records"][0]["is_win"], True)
            self.assertAlmostEqual(sc["reconciled_live_records"][0]["realized_return_pct"], 6.0, places=1)
            self.assertTrue(os.path.exists(sc_path))

            html_path = os.path.join(td, "predict_radar_5y.html")
            ml_predictor_5y.render_html_dashboard(
                {
                    "kpi": {"sync_time": "2026-11-20 09:30:00", "data_mode": "unit-test", "high_conviction_win_rate": 56.0, "profit_factor": 1.4, "top_sector": "半导体", "top_feature": "QQQ_Bias_MA50 (14.0%)"},
                    "macro_weather": {"status": "🌧️ 暴雨空仓", "market_regime": "BEAR_DEFENSE", "market_regime_cn": "🔴 空头防守体制", "breadth_ma20_pct": 22.0},
                    "table1": [{"threshold_label": ">= 60%", "trigger_count": 100, "win_rate_pct": 56.0, "pos_win_rate_pct": 56.0, "touch_win_rate_pct": 52.0, "avg_realized_return_pct": 0.4, "win_loss_ratio": 1.3, "profit_factor": 1.4}],
                    "table2": [{"sector_name_cn": "半导体", "sector_key": "Semiconductors", "tickers_count": 10, "oos_total_signals": 500, "high_conviction_signals": 100, "high_conviction_win_rate_pct": 52.0, "high_conviction_pos_win_rate_pct": 52.0, "high_conviction_touch_win_rate_pct": 50.0, "profit_factor": 1.3, "avg_realized_return_pct": 0.5, "win_loss_ratio": 1.2, "alpha_rating": "⭐⭐⭐☆☆"}],
                    "table3": [],
                    "table4": [],
                    "top_long": [],
                    "top_short": [],
                    "live_scorecard": sc,
                },
                output_path=html_path,
            )
            with open(html_path, "r", encoding="utf-8") as hf:
                html_txt = hf.read()
            self.assertIn("marketRegimeBadge", html_txt)
            self.assertIn("liveScorecardBadge", html_txt)
            self.assertIn("⚠️ 模型近期失准·自动降权参考", html_txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
