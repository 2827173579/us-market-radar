# -*- coding: utf-8 -*-
"""
V4.5.1 专项全 Mock 离线单元测试 (test_v451_predict_ladder_and_24h_polish.py)

覆盖要求：
1. predict_radar_5y (ml_predictor_5y):
   - SOXL 全线跌穿原始支撑 (ALL_SUPPORT_BREACHED) 时，点位天梯严格按价格降序排列，
     当前现价落于 原S1/原S2/原SL 下方；已击穿支撑取消绿色样式并标注 (已击穿转压) / 原SL (已触及)；
     ATR_ESTIMATE 标注 (估算·无真实筹码)；底部附 ⚠️ 今日原定支撑全线击穿 · 下方真空寻底（严禁左侧接刀）；
     副标题更新为 当日静态锚定 · 支撑击穿转压；主图参考线同步标 原S1(已破)/原S2(已破)/原SL(已破)。
   - 对称验证 ALL_RESISTANCE_BROKEN 场景下 原R1/原R2 落于现价下方，呈青/绿支撑态并标注 已突破转撑，
     顶部附 🚀 突破今日全线阻力 · 沿 VWAP/EMA9 移动止盈，主图参考线标 原R1(转撑)/原R2(转撑)。
   - align_time_cycle_projections_with_live 完整透传 anchor_* 与 *_source 并随 live_prices 更新 sr_breach_state。
2. create_futu_terminal:
   - BTC-USD 右下微结构卡片副标显示 (24H 实时滚动)，严禁出现 (上日参考 · 09:30重置)；
   - XAUUSD 周末休市显示 (最近24H参考 · 休市)，平日显示 (24H 实时滚动)；
   - 普通美股/ETF 非 RTH 时段仍保留 (上日参考 · 09:30重置)；
   - renderIntradayPathChart 包含 24H 水平参考线 < 16px 相邻防重叠与左右交替放置逻辑。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone

import create_futu_terminal as cft
import ml_predictor_5y as mlp

GREEN_COLORS = {"#3fb950", "#059669", "#10b981", "#2dd4bf", "#0d9488", "#34d399"}


def _make_sample_tables(tc_proj: dict, all_live: list) -> dict:
    return {
        "kpi": {
            "high_conviction_win_rate": 61.5,
            "profit_factor": 2.1,
            "top_sector": "半导体与算力 (⭐⭐⭐⭐⭐)",
            "top_feature": "QQQ_Bias_MA50 (14.3%)",
            "sync_time": "2026-11-23 02:00:00",
            "data_mode": "Offline Mock",
            "is_realtime": False,
        },
        "macro_weather": {
            "status": "🌧️ 暴雨空仓",
            "desc": "Mock",
            "qqqm_bias": -4.2,
            "smh_bias": -4.8,
            "vixy_pos": 0.85,
            "market_regime": "BEAR_DEFENSE",
            "market_regime_cn": "🔴 空头防守体制",
            "breadth_ma20_pct": 24.0,
        },
        "vix_regime": {"vix_spot": 30.0, "vix_term_ratio": 1.05, "vix_state": "BACKWARDATION_BRAKE", "vix_hard_brake": True},
        "live_scorecard": {"win_rate_7d_pct": 60.0, "win_rate_30d_pct": 62.0, "is_degraded": False},
        "table1": [
            {
                "threshold_label": "P >= 0.55",
                "trigger_count": 100,
                "pos_win_rate_pct": 62.0,
                "touch_win_rate_pct": 65.0,
                "win_rate_pct": 62.0,
                "avg_realized_return_pct": 1.25,
                "win_loss_ratio": 1.6,
                "profit_factor": 2.1,
            },
            {
                "threshold_label": "P >= 0.50",
                "trigger_count": 150,
                "pos_win_rate_pct": 55.0,
                "touch_win_rate_pct": 58.0,
                "win_rate_pct": 55.0,
                "avg_realized_return_pct": 0.65,
                "win_loss_ratio": 1.4,
                "profit_factor": 1.5,
            },
        ],
        "table2": [
            {
                "sector_name_cn": "半导体与算力",
                "sector_key": "SEMI",
                "tickers_count": 10,
                "oos_total_signals": 200,
                "high_conviction_signals": 80,
                "high_conviction_pos_win_rate_pct": 62.0,
                "high_conviction_touch_win_rate_pct": 65.0,
                "avg_realized_return_pct": 1.4,
                "win_loss_ratio": 1.7,
                "profit_factor": 1.8,
                "alpha_rating": "⭐⭐⭐⭐⭐",
            }
        ],
        "table3": [
            {
                "rank": 1,
                "feature": "QQQ_Bias_MA50",
                "category": "大盘趋势",
                "category_tag": "macro",
                "total_gain": 1200.0,
                "gain_share_pct": 14.3,
                "rationale": "Mock",
            }
        ],
        "table4": all_live,
        "top_long": all_live,
        "top_short": all_live,
        "all_live_predictions": all_live,
        "time_cycle_projections": tc_proj,
    }


class TestV451PredictLadderAnd24hPolish(unittest.TestCase):
    def test_01_soxl_all_support_breached_descending_and_no_green(self):
        soxl_data = {
            "ticker": "SOXL",
            "current_price": 30.88,
            "anchor_s1": 42.92,
            "anchor_s2": 41.45,
            "anchor_sl": 40.83,
            "anchor_r1": 46.26,
            "anchor_r2": 47.05,
            "s1": 42.92,
            "s2": 41.45,
            "sl": 40.83,
            "r1": 46.26,
            "r2": 47.05,
            "s1_source": "ATR_ESTIMATE",
            "s2_source": "ATR_ESTIMATE",
            "r1_source": "REAL_CHIPS",
            "r2_source": "REAL_CHIPS",
        }
        for is_light in (False, True):
            spec = mlp.build_cycle_sr_ladder_spec(soxl_data, is_light_theme=is_light)
            self.assertEqual(spec["sr_state"], "ALL_SUPPORT_BREACHED")
            self.assertIn("当日静态锚定", spec["subtitle"])
            self.assertIn("支撑击穿转压", spec["subtitle"])
            self.assertEqual(
                spec["bottom_banner"],
                "⚠️ 今日原定支撑全线击穿 · 下方真空寻底（严禁左侧接刀）",
            )
            self.assertEqual(spec["top_banner"], "")

            prices = [it["val"] for it in spec["items"]]
            self.assertEqual(prices, sorted(prices, reverse=True))
            self.assertEqual([it["id"] for it in spec["items"]], ["R2", "R1", "S1", "S2", "SL", "CUR"])

            by_id = {it["id"]: it for it in spec["items"]}
            self.assertTrue(by_id["S1"]["is_breached"])
            self.assertTrue(by_id["S2"]["is_breached"])
            self.assertTrue(by_id["SL"]["is_breached"])
            self.assertIn("原S1 (已击穿转压)", by_id["S1"]["key"])
            self.assertIn("(估算·无真实筹码)", by_id["S1"]["key"])
            self.assertIn("原S2 (已击穿转压)", by_id["S2"]["key"])
            self.assertIn("(估算·无真实筹码)", by_id["S2"]["key"])
            self.assertEqual(by_id["SL"]["key"], "原SL (已触及)")

            for lvl_id in ("S1", "S2", "SL"):
                self.assertNotIn(by_id[lvl_id]["color"].lower(), GREEN_COLORS)
                self.assertEqual(by_id[lvl_id]["border_style"], "dashed")

            cl_by_id = {cl["id"]: cl for cl in spec["canvas_lines"]}
            self.assertEqual(cl_by_id["S1"]["key"], "原S1(已破)")
            self.assertEqual(cl_by_id["S2"]["key"], "原S2(已破)")
            self.assertEqual(cl_by_id["SL"]["key"], "原SL(已破)")
            for lvl_id in ("S1", "S2", "SL"):
                self.assertNotIn(cl_by_id[lvl_id]["color"].lower(), GREEN_COLORS)
                self.assertTrue(cl_by_id[lvl_id]["isBreached"])

    def test_02_all_resistance_broken_symmetric_flip_and_render_html(self):
        qqq_data = {
            "ticker": "QQQ",
            "current_price": 525.0,
            "anchor_s1": 490.0,
            "anchor_s2": 482.0,
            "anchor_sl": 474.0,
            "anchor_r1": 498.0,
            "anchor_r2": 510.0,
            "s1": 490.0,
            "s2": 482.0,
            "sl": 474.0,
            "r1": 498.0,
            "r2": 510.0,
            "s1_source": "ATR_ESTIMATE",
            "s2_source": "REAL_CHIPS",
            "r1_source": "ATR_ESTIMATE",
            "r2_source": "REAL_CHIPS",
        }
        spec = mlp.build_cycle_sr_ladder_spec(qqq_data, is_light_theme=False)
        self.assertEqual(spec["sr_state"], "ALL_RESISTANCE_BROKEN")
        self.assertIn("当日静态锚定", spec["subtitle"])
        self.assertIn("全线突破转撑", spec["subtitle"])
        self.assertEqual(spec["top_banner"], "🚀 突破今日全线阻力 · 沿 VWAP/EMA9 移动止盈")
        self.assertEqual(spec["bottom_banner"], "")

        prices = [it["val"] for it in spec["items"]]
        self.assertEqual(prices, sorted(prices, reverse=True))
        self.assertEqual([it["id"] for it in spec["items"]], ["CUR", "R2", "R1", "S1", "S2", "SL"])

        by_id = {it["id"]: it for it in spec["items"]}
        self.assertTrue(by_id["R1"]["is_broken"])
        self.assertTrue(by_id["R2"]["is_broken"])
        self.assertIn("原R1 (已突破转撑)", by_id["R1"]["key"])
        self.assertIn("(估算·无真实筹码)", by_id["R1"]["key"])
        self.assertIn("原R2 (已突破转撑)", by_id["R2"]["key"])
        self.assertIn(by_id["R1"]["color"].lower(), GREEN_COLORS)
        self.assertIn(by_id["R2"]["color"].lower(), GREEN_COLORS)
        # Intact ATR_ESTIMATE S1 must NOT be green and must carry estimation tag
        self.assertNotIn(by_id["S1"]["color"].lower(), GREEN_COLORS)
        self.assertIn("(估算·无真实筹码)", by_id["S1"]["key"])

        cl_by_id = {cl["id"]: cl for cl in spec["canvas_lines"]}
        self.assertEqual(cl_by_id["R1"]["key"], "原R1(转撑)")
        self.assertEqual(cl_by_id["R2"]["key"], "原R2(转撑)")
        self.assertEqual(cl_by_id["S1"]["key"], "估算S1")

        # Verify render_html_dashboard pre-renders SOXL ladder and removes legacy subtitle
        soxl_tc = {
            "ticker": "SOXL",
            "current_price": 30.88,
            "anchor_price": 30.88,
            "s1": 42.92, "s2": 41.45, "sl": 40.83, "r1": 46.26, "r2": 47.05,
            "anchor_s1": 42.92, "anchor_s2": 41.45, "anchor_sl": 40.83, "anchor_r1": 46.26, "anchor_r2": 47.05,
            "s1_source": "ATR_ESTIMATE", "s2_source": "ATR_ESTIMATE", "r1_source": "REAL_CHIPS", "r2_source": "REAL_CHIPS",
            "history": [{"date": "2026-11-20", "price": 30.88}],
            "projection": [{"day": "T+1", "date": "2026-11-23", "price": 31.2, "pct": 1.0, "wave_ret": 0.01}],
            "cycle_windows": [],
            "analog_match": {"period": "2025-03-17 ~ 2025-04-21", "similarity_pct": 90.0},
            "today_label": "最新收盘 (11-20)",
        }
        live_row = {
            "rank": 1, "short_rank": 1, "ticker": "SOXL", "sector_cn": "半导体与算力",
            "sector_rating": "⭐⭐⭐⭐⭐", "sector_pf": 1.8, "latest_price": 30.88, "current_price": 30.88,
            "atr_pct": 7.0, "bias_ma50": -18.0, "calibrated_win_rate_pct": 40.0, "predicted_win_rate_pct": 42.0,
            "calibrated_short_win_rate_pct": 62.0, "target_profit_price": 35.0, "target_profit_pct": 13.3,
            "stop_loss_price": 29.0, "stop_loss_pct": 6.1, "macro_temp": "🌧️ 暴雨空仓", "action": "⛔ 熔断观望",
            "s1": 42.92, "s2": 41.45, "sl": 40.83, "r1": 46.26, "r2": 47.05,
            "s1_source": "ATR_ESTIMATE", "s2_source": "ATR_ESTIMATE", "r1_source": "REAL_CHIPS", "r2_source": "REAL_CHIPS",
            "sr_breach_state": "ALL_SUPPORT_BREACHED", "scenario_script": "Mock script",
        }
        with tempfile.TemporaryDirectory() as td:
            out_html = os.path.join(td, "predict_test.html")
            mlp.render_html_dashboard(_make_sample_tables({"SOXL": soxl_tc}, [live_row]), output_path=out_html)
            with open(out_html, "r", encoding="utf-8") as hf:
                html_txt = hf.read()
            self.assertNotIn("软ATR+POC动态锚定", html_txt)
            self.assertIn("当日静态锚定", html_txt)
            self.assertIn("原S1 (已击穿转压)", html_txt)
            self.assertIn("原S2 (已击穿转压)", html_txt)
            self.assertIn("原SL (已触及)", html_txt)
            self.assertIn("⚠️ 今日原定支撑全线击穿 · 下方真空寻底（严禁左侧接刀）", html_txt)
            self.assertIn("buildCycleSrLadderSpec", html_txt)
            # Ensure no duplicate DOM IDs in pre-rendered HTML
            body_only = html_txt.split("<body>")[1].split("const timeCycleData")[0]
            ids = re.findall(r'\bid="([^"]+)"', body_only)
            self.assertEqual(len(ids), len(set(ids)), f"Duplicate IDs: {[x for x in ids if ids.count(x) > 1]}")

    def test_03_align_time_cycle_propagates_anchor_sources_and_state(self):
        tc_dict = {
            "SOXL": {
                "ticker": "SOXL",
                "current_price": 43.5,
                "anchor_price": 43.5,
                "s1": 42.92, "s2": 41.45, "sl": 40.83, "r1": 46.26, "r2": 47.05,
                "history": [{"date": "2026-11-20", "price": 43.5}],
                "projection": [{"day": "T+1", "date": "2026-11-23", "price": 44.0, "pct": 1.1, "wave_ret": 0.011}],
            }
        }
        by_tk = {
            "SOXL": {
                "ticker": "SOXL",
                "latest_price": 30.88,
                "current_price": 30.88,
                "s1": 42.92, "s2": 41.45, "sl": 40.83, "r1": 46.26, "r2": 47.05,
                "anchor_s1": 42.92, "anchor_s2": 41.45, "anchor_sl": 40.83, "anchor_r1": 46.26, "anchor_r2": 47.05,
                "s1_source": "ATR_ESTIMATE", "s2_source": "ATR_ESTIMATE", "r1_source": "REAL_CHIPS", "r2_source": "REAL_CHIPS",
                "sr_anchor_locked": True,
                "sr_breach_state": "ALL_SUPPORT_BREACHED",
                "scenario_script": "Mock",
            }
        }
        mlp.align_time_cycle_projections_with_live(
            tc_dict=tc_dict,
            live_prices={"SOXL": 30.88},
            by_ticker=by_tk,
            session_code="REGULAR",
            as_of=datetime(2026, 11, 20, 14, 30, tzinfo=timezone.utc),
        )
        soxl = tc_dict["SOXL"]
        self.assertEqual(soxl["current_price"], 30.88)
        self.assertEqual(soxl["s1_source"], "ATR_ESTIMATE")
        self.assertEqual(soxl["r1_source"], "REAL_CHIPS")
        self.assertEqual(soxl["anchor_s1"], 42.92)
        self.assertEqual(soxl["sr_breach_state"], "ALL_SUPPORT_BREACHED")
        self.assertTrue(soxl["sr_anchor_locked"])

    def test_04_futu_terminal_24h_micro_labels_and_ref_anti_overlap(self):
        html_out = cft.build_futu_terminal_html(
            stocks_data=[
                {
                    "ticker": "BTC-USD", "name": "比特币现货", "sub": "数字资产", "cat": "数字资产",
                    "last": 60856.92, "active_price": 60856.92, "pct_change": 1.27, "vwap": 59992.55,
                    "rsi": 48.0, "bias": -12.0, "atr": 1200.0, "fib_low": 58000.0, "fib_high": 63000.0,
                    "signal": "🟢 超跌右侧反包",
                },
                {
                    "ticker": "XAUUSD", "name": "伦敦金现货", "sub": "避险大宗", "cat": "避险资产",
                    "last": 3530.0, "active_price": 3530.0, "pct_change": 0.4, "vwap": 3510.0,
                    "rsi": 55.0, "bias": 1.5, "atr": 45.0, "fib_low": 3450.0, "fib_high": 3600.0,
                    "signal": "🔵 多头趋势",
                },
                {
                    "ticker": "NVDA", "name": "英伟达", "sub": "AI算力", "cat": "AI算力",
                    "last": 180.0, "active_price": 180.0, "pct_change": -1.2, "vwap": 181.5,
                    "rsi": 42.0, "bias": -3.0, "atr": 4.5, "fib_low": 170.0, "fib_high": 190.0,
                    "signal": "👀 观望",
                },
            ],
            macro_data={"SPY": {"name": "标普500", "cat": "大盘基准", "last": 580.0, "active_price": 580.0, "pct_change": -0.5, "vwap": 581.0}},
            weather_info={"weather": "暴雨空仓", "market_regime": "BEAR_DEFENSE"},
            session_code="WEEKEND",
        )
        self.assertIn("getMicroLabelSuffixHtml", html_out)
        self.assertIn("(24H 实时滚动)", html_out)
        self.assertIn("(最近24H参考 · 休市)", html_out)
        self.assertIn("(上日参考 · 09:30重置)", html_out)
        self.assertIn("const minGapPx = 16;", html_out)
        self.assertIn('class="ip-ref-label"', html_out)
        self.assertIn("alignRight", html_out)


if __name__ == "__main__":
    unittest.main()
