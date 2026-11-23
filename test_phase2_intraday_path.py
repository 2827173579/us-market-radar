# -*- coding: utf-8 -*-
"""
阶段二核心回归与对抗测试：
1. 历史相似开盘日 KNN 匹配零未来函数穿越 (Zero Lookahead Bias)
2. 美东 09:25 ET 盘前预测走廊快照不可篡改锁定机制
3. 美东 09:45 ET 起跌破 P25 / 突破 P75 / 走廊内实时背离纠偏判定
4. ANT (<10日次新股) 与 BTC-USD/XAUUSD (7x24h连续品种) 优雅降级
5. 60 日 5m Parquet 缓存命中时 60 秒轮询 0 次全量重下保护
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
if WORK_DIR not in sys.path:
    sys.path.insert(0, WORK_DIR)

import intraday_path_predictor as ipp

try:
    import zoneinfo
    NY_TZ = zoneinfo.ZoneInfo("America/New_York")
except Exception:
    from datetime import timezone
    NY_TZ = timezone(timedelta(hours=-5))


def _make_synthetic_5m_panel(tickers, trading_days, base_price=100.0, inject_spike_on_date=None):
    rows = []
    rng = np.random.default_rng(42)
    for tk_idx, tk in enumerate(tickers):
        px = base_price + tk_idx * 20.0
        for d_idx, d in enumerate(trading_days):
            d_str = d.strftime("%Y-%m-%d")
            # 盘前 08:00~09:25 采样若干 bar
            for pm in (0, 30, 55, 80):
                dt_pm = pd.Timestamp(f"{d_str} 08:00:00") + pd.Timedelta(minutes=pm)
                rows.append({
                    "Datetime": dt_pm,
                    "Ticker": tk,
                    "Open": px,
                    "High": px * 1.008,
                    "Low": px * 0.994,
                    "Close": px * (1.003 if d_idx % 2 == 0 else 0.997),
                    "Volume": 10000.0,
                })
            # 常规 RTH 09:30 ~ 15:55 共 78 根 5m bar
            day_open = px * (1.006 if d_idx % 2 == 0 else 0.995)
            cur = day_open
            for slot_i in range(78):
                dt_bar = pd.Timestamp(f"{d_str} 09:30:00") + pd.Timedelta(minutes=slot_i * 5)
                drift = -0.00025 if (d_idx % 2 == 0 and slot_i >= 18) else 0.00018
                cur = cur * (1.0 + drift + float(rng.normal(0.0, 0.0006)))
                bar_c = cur * 1.50 if (inject_spike_on_date == d_str and slot_i >= 2) else cur
                rows.append({
                    "Datetime": dt_bar,
                    "Ticker": tk,
                    "Open": bar_c * 0.999,
                    "High": bar_c * 1.002,
                    "Low": bar_c * 0.998,
                    "Close": bar_c,
                    "Volume": 50000.0,
                })
            px = cur
    return pd.DataFrame(rows)


class TestPhase2IntradayPathCorridor(unittest.TestCase):
    def setUp(self):
        self.all_days = list(pd.bdate_range("2026-09-01", periods=30))
        self.hist_days = self.all_days[:-1]
        self.forecast_day = self.all_days[-1].date()
        self.forecast_str = self.forecast_day.strftime("%Y-%m-%d")

    def test_01_knn_zero_lookahead_and_strict_date_bound(self):
        df_with_spike = _make_synthetic_5m_panel(["QQQ", "NVDA"], self.all_days, inject_spike_on_date=self.forecast_str)
        df_clean = df_with_spike[df_with_spike["Datetime"].dt.date < self.forecast_day].copy()

        qqq_df_c = df_clean[df_clean["Ticker"] == "QQQ"]
        qqq_df_s = df_with_spike[df_with_spike["Ticker"] == "QQQ"]
        gap_c, reg_c = ipp._build_qqq_context_maps(qqq_df_c, self.forecast_day)
        gap_s, reg_s = ipp._build_qqq_context_maps(qqq_df_s, self.forecast_day)
        self.assertEqual(gap_c, gap_s)
        self.assertEqual(reg_c, reg_s)

        nvda_c = df_clean[df_clean["Ticker"] == "NVDA"]
        nvda_s = df_with_spike[df_with_spike["Ticker"] == "NVDA"]

        res_clean = ipp.compute_knn_intraday_corridor(
            ticker="NVDA", df_tk=nvda_c, forecast_date=self.forecast_day,
            prev_close=120.0, anchor_open=121.2, pmh=121.8, pml=120.4,
            today_qqq_gap_pct=0.5, today_regime_score=0.0,
            qqq_gap_by_date=gap_c, qqq_regime_by_date=reg_c, k_neighbors=12,
        )
        res_spike = ipp.compute_knn_intraday_corridor(
            ticker="NVDA", df_tk=nvda_s, forecast_date=self.forecast_day,
            prev_close=120.0, anchor_open=121.2, pmh=121.8, pml=120.4,
            today_qqq_gap_pct=0.5, today_regime_score=0.0,
            qqq_gap_by_date=gap_s, qqq_regime_by_date=reg_s, k_neighbors=12,
        )

        self.assertEqual(res_clean["status"], "ok")
        self.assertEqual(len(res_clean["p50_prices"]), 78)
        self.assertEqual(res_clean["p25_prices"], res_spike["p25_prices"])
        self.assertEqual(res_clean["p50_prices"], res_spike["p50_prices"])
        self.assertEqual(res_clean["p75_prices"], res_spike["p75_prices"])
        self.assertEqual(res_clean["matched_dates"], res_spike["matched_dates"])
        self.assertEqual(res_clean["summary_text"], res_spike["summary_text"])
        self.assertTrue(all(d < self.forecast_str for d in res_spike["matched_dates"]))

    def test_02_0925_et_snapshot_lock_immutability(self):
        df_panel = _make_synthetic_5m_panel(["QQQ", "SPY", "NVDA"], self.hist_days)
        with tempfile.TemporaryDirectory() as td:
            pq_path = os.path.join(td, "intraday_5m_60d.parquet")
            meta_path = os.path.join(td, ".intraday_5m_60d_meta.json")
            out_path = os.path.join(td, "intraday_path_forecasts.json")
            df_panel.to_parquet(pq_path, index=False)
            t_lock = datetime(self.forecast_day.year, self.forecast_day.month, self.forecast_day.day, 9, 26, tzinfo=NY_TZ)
            with open(meta_path, "w", encoding="utf-8") as mf:
                json.dump({"last_refresh_date": self.forecast_str, "last_refresh_slot": "premarket"}, mf)

            # 1) 09:26 ET 锁定盘前快照
            doc1 = ipp.update_intraday_path_forecasts(
                tickers=["QQQ", "SPY", "NVDA"],
                intraday_map={"NVDA": {"ext_price": 122.0, "pmh": 122.5, "pml": 120.8}},
                realtime_quotes={"NVDA": {"last": 122.0}},
                market_regime="NEUTRAL_CHOP",
                now_et=t_lock,
                cache_path=pq_path,
                meta_path=meta_path,
                output_path=out_path,
            )
            nvda1 = doc1["by_ticker"]["NVDA"]
            self.assertTrue(nvda1["snapshot_locked"])
            self.assertEqual(nvda1["lock_window_tag"], "PREOPEN_0925_LOCK")

            # 2) 10:30 ET 传入剧烈变化的盘中报价与截然不同的 regime，检验盘前走廊绝不被修改，仅更新实盘线与背离状态
            t_live = datetime(self.forecast_day.year, self.forecast_day.month, self.forecast_day.day, 10, 30, tzinfo=NY_TZ)
            doc2 = ipp.update_intraday_path_forecasts(
                tickers=["QQQ", "SPY", "NVDA"],
                intraday_map={"NVDA": {
                    "rth_date": self.forecast_str,
                    "rth_open": 135.0,
                    "pmh": 140.0,
                    "rth_5m_series": [[0, 122.0], [1, 121.0], [2, 118.0], [3, 114.0], [12, 110.0]],
                }},
                realtime_quotes={"NVDA": {"last": 110.0}},
                market_regime="BEAR_DEFENSE",
                now_et=t_live,
                cache_path=pq_path,
                meta_path=meta_path,
                output_path=out_path,
            )
            nvda2 = doc2["by_ticker"]["NVDA"]
            self.assertEqual(nvda2["p25_prices"], nvda1["p25_prices"])
            self.assertEqual(nvda2["p50_prices"], nvda1["p50_prices"])
            self.assertEqual(nvda2["p75_prices"], nvda1["p75_prices"])
            self.assertEqual(nvda2["anchor_open"], nvda1["anchor_open"])
            self.assertEqual(nvda2["pmh"], nvda1["pmh"])
            self.assertEqual(nvda2["summary_text"], nvda1["summary_text"])
            self.assertEqual(nvda2["matched_dates"], nvda1["matched_dates"])
            self.assertAlmostEqual(nvda2["actual_prices"][12], 110.0, places=2)
            self.assertEqual(nvda2["divergence"]["status"], "BELOW_P25")

    def test_03_0945_et_divergence_detection(self):
        forecast_item = {
            "status": "ok",
            "anchor_open": 100.0,
            "p25_prices": [99.5] * 78,
            "p50_prices": [100.0] * 78,
            "p75_prices": [100.5] * 78,
        }
        # 1) 09:40 ET (slot=2 < 3) 即使低于 P25 也应处于前15分钟观察窗
        act_pre15 = [None] * 78
        act_pre15[0], act_pre15[1], act_pre15[2] = 100.0, 99.0, 96.0
        div_open = ipp.evaluate_intraday_divergence(forecast_item, act_pre15)
        self.assertEqual(div_open["status"], "OPENING_WINDOW")
        self.assertIn("09:45 ET", div_open["badge"])

        # 2) 09:45 ET (slot=3) 跌破 P25 下沿 -> BELOW_P25
        act_below = list(act_pre15)
        act_below[3] = 98.0
        div_below = ipp.evaluate_intraday_divergence(forecast_item, act_below)
        self.assertEqual(div_below["status"], "BELOW_P25")
        self.assertIn("跌破盘前剧本下沿", div_below["badge"])

        # 3) 突破 P75 上沿 -> ABOVE_P75
        act_above = list(act_pre15)
        act_above[3] = 102.0
        div_above = ipp.evaluate_intraday_divergence(forecast_item, act_above)
        self.assertEqual(div_above["status"], "ABOVE_P75")
        self.assertIn("突破盘前剧本上沿", div_above["badge"])

        # 4) 走廊内部 -> IN_CORRIDOR
        act_in = list(act_pre15)
        act_in[3] = 100.1
        div_in = ipp.evaluate_intraday_divergence(forecast_item, act_in)
        self.assertEqual(div_in["status"], "IN_CORRIDOR")
        self.assertIn("走势贴合盘前推演走廊", div_in["badge"])

    def test_04_short_history_and_24h_graceful_degradation(self):
        # ANT 只有 3 天 5m 数据 (<10 天)
        df_ant = _make_synthetic_5m_panel(["ANT"], self.hist_days[-3:], base_price=83.0)
        res_ant = ipp.compute_knn_intraday_corridor(
            ticker="ANT", df_tk=df_ant, forecast_date=self.forecast_day,
            prev_close=83.0, anchor_open=83.0, pmh=84.0, pml=82.5,
        )
        self.assertEqual(res_ant["status"], "insufficient_history")
        self.assertIn("积累 10 个交易日", res_ant["status_message"])

        # BTC-USD & XAUUSD 24h 连续交易品种
        for tk24 in ("BTC-USD", "XAUUSD"):
            res_24 = ipp.compute_knn_intraday_corridor(
                ticker=tk24, df_tk=None, forecast_date=self.forecast_day,
                prev_close=100.0, anchor_open=100.5,
            )
            self.assertEqual(res_24["status"], "continuous_24h")
            self.assertIn("VWAP", res_24["status_message"])

    def test_05_60d_cache_hit_zero_full_redownloads(self):
        df_panel = _make_synthetic_5m_panel(["QQQ", "SPY", "NVDA"], self.hist_days)
        with tempfile.TemporaryDirectory() as td:
            pq_path = os.path.join(td, "intraday_5m_60d.parquet")
            meta_path = os.path.join(td, ".intraday_5m_60d_meta.json")
            out_path = os.path.join(td, "intraday_path_forecasts.json")
            df_panel.to_parquet(pq_path, index=False)
            with open(meta_path, "w", encoding="utf-8") as mf:
                json.dump({"last_refresh_date": self.forecast_str, "last_refresh_slot": "premarket"}, mf)

            mock_dl = MagicMock(side_effect=AssertionError("盘中缓存命中时绝不能重新调用 yf.download"))
            for hr, mn in ((9, 45), (10, 15), (11, 0), (15, 50)):
                dt_rth = datetime(self.forecast_day.year, self.forecast_day.month, self.forecast_day.day, hr, mn, tzinfo=NY_TZ)
                self.assertFalse(ipp.should_refresh_60d_cache(pq_path, meta_path, now_et=dt_rth))
                ipp.update_intraday_path_forecasts(
                    tickers=["QQQ", "SPY", "NVDA"],
                    now_et=dt_rth,
                    cache_path=pq_path,
                    meta_path=meta_path,
                    output_path=out_path,
                    downloader=mock_dl,
                )
            self.assertEqual(mock_dl.call_count, 0)

            # 收盘后 (>=16:15 ET) 应允许今日仅 1 次盘后增量刷新，刷新完同一日不再重复刷新
            dt_post = datetime(self.forecast_day.year, self.forecast_day.month, self.forecast_day.day, 16, 20, tzinfo=NY_TZ)
            self.assertTrue(ipp.should_refresh_60d_cache(pq_path, meta_path, now_et=dt_post))

    def test_05_rth_0930_open_anchor_translation_and_zero_lookahead(self):
        """验证 09:30 真实开盘价偏离盘前锁定锚 >=1.5% 时纯平移走廊，保持 P25%/P50%/P75% 相对形态 100% 不变。"""
        base_item = {
            "status": "ok",
            "anchor_open": 45.00,
            "prev_close": 30.88,
            "p25_pcts": [0.0, -1.5, -2.0],
            "p50_pcts": [0.0, 0.2, 0.5],
            "p75_pcts": [0.0, 1.8, 2.4],
            "p25_prices": [45.00, 44.32, 44.10],
            "p50_prices": [45.00, 45.09, 45.22],
            "p75_prices": [45.00, 45.81, 46.08],
        }
        orig_p25_pcts = list(base_item["p25_pcts"])
        orig_p50_pcts = list(base_item["p50_pcts"])
        orig_p75_pcts = list(base_item["p75_pcts"])

        # 09:30 真实开盘价 $42.00（较盘前锚 $45.00 偏离 -6.67% >= 1.5%）
        actual_arr = [42.00, 42.30, None]
        ipp.apply_rth_open_anchor_translation(base_item, actual_arr, threshold_pct=1.5)

        self.assertTrue(base_item.get("rth_open_aligned"))
        self.assertEqual(base_item["premarket_anchor_open"], 45.00)
        self.assertEqual(base_item["anchor_open"], 42.00)
        self.assertEqual(base_item["rth_open_price"], 42.00)
        # Zero Lookahead：相对百分比形态严格零修改
        self.assertEqual(base_item["p25_pcts"], orig_p25_pcts)
        self.assertEqual(base_item["p50_pcts"], orig_p50_pcts)
        self.assertEqual(base_item["p75_pcts"], orig_p75_pcts)
        # 价格走廊按 $42.00 纯平移
        self.assertEqual(base_item["p50_prices"][0], 42.00)
        self.assertEqual(base_item["p25_prices"][2], round(42.00 * (1.0 - 0.02), 2))
        self.assertEqual(base_item["p75_prices"][2], round(42.00 * (1.0 + 0.024), 2))

    def test_06_1x_etf_adaptive_0930_threshold_and_corridor_relative_squash_guard(self):
        """验证 1x ETF (QQQ) 1% 盘前跳动即触发自适应 09:30 开盘锚平移，且前端源码含 0.55*coreSpan 防压扁与博反抽徽标。"""
        # 1) QQQ 日内走廊半宽约 0.75% -> 自适应阈值 max(0.5%, 0.5*0.75%) = 0.5%，跳动 0.9% 即触发平移
        qqq_item = {
            "status": "ok",
            "anchor_open": 654.00,
            "prev_close": 610.04,
            "p25_pcts": [0.0, -0.7, -0.8],
            "p50_pcts": [0.0, 0.0, 0.1],
            "p75_pcts": [0.0, 0.8, 0.7],
            "p25_prices": [654.00, 649.42, 648.77],
            "p50_prices": [654.00, 654.00, 654.65],
            "p75_prices": [654.00, 659.23, 658.58],
        }
        ipp.apply_rth_open_anchor_translation(qqq_item, [648.10, 649.00, None])
        self.assertTrue(qqq_item.get("rth_open_aligned"))
        self.assertEqual(qqq_item["anchor_open"], 648.10)
        self.assertEqual(qqq_item["premarket_anchor_open"], 654.00)

        # 2) 验证前端源码具备相对走廊跨度 0.55*coreSpanRth 收口、⚡ 博反抽徽标与已破 R1 动态目标替换
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "create_futu_terminal.py"), encoding="utf-8").read()
        self.assertIn("0.55 * coreSpanRth", src)
        self.assertIn("⚡ 博反抽", src)
        self.assertIn("上方目标(${_upLbl})", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
