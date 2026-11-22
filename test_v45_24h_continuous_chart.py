# -*- coding: utf-8 -*-
"""
V4.5 专项全 Mock 单元测试 (test_v45_24h_continuous_chart.py)

覆盖核心场景：
1. BTC-USD 与 XAUUSD 生成真实的近 24H 滚动价格序列、累计 VWAP 曲线与 P25~P75 波动带（非空白占位）；
2. XAUUSD 周末休市优雅回退至最近完整 24H 有效交易时段并携带休市说明；现货金基差校准缩放 continuous_24h 曲线；
3. Y 轴防压扁分流逻辑：远端 R1/R2 (+15%~+22%) 转入边缘提示而不拉垮主图 Y 轴，且 ATR_ESTIMATE 保持非绿估算防线标注；
4. 渲染产物头部清理 09:25盘前走廊 美股穿帮文案，包含 UTC 00:00 日锚 / 24H 连续走势 / #ipVwapLine，且微结构网格具备防截断规则。
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

import create_futu_terminal as cft
import integrated_market_radar as radar
import intraday_path_predictor as ipp

try:
    import zoneinfo
    NY_TZ = zoneinfo.ZoneInfo("America/New_York")
except Exception:
    NY_TZ = timezone(timedelta(hours=-5))


def _make_5m_continuous_df(start_utc: datetime, n_bars: int = 288, start_px: float = 60500.0, vol_val: float = 1200.0) -> pd.DataFrame:
    idx = pd.date_range(start=start_utc, periods=n_bars, freq="5min", tz="UTC")
    closes = []
    highs = []
    lows = []
    opens = []
    vols = []
    cur = start_px
    for i in range(n_bars):
        op = cur
        step = np.sin(i / 14.0) * (start_px * 0.0018) + (start_px * 0.00012)
        cl = max(1.0, op + step)
        hi = max(op, cl) * 1.0012
        lo = min(op, cl) * 0.9988
        opens.append(round(op, 2))
        closes.append(round(cl, 2))
        highs.append(round(hi, 2))
        lows.append(round(lo, 2))
        vols.append(vol_val if i % 3 != 0 else vol_val * 1.8)
        cur = cl
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=idx,
    )


def _make_daily_history(end_date_str: str = "2026-11-21", n_days: int = 90, base_px: float = 60000.0) -> pd.DataFrame:
    idx = pd.date_range(end=end_date_str, periods=n_days, freq="D")
    opens, highs, lows, closes, vols = [], [], [], [], []
    for i in range(n_days):
        op = base_px * (1.0 + 0.015 * np.sin(i / 8.0))
        ret = 0.018 * np.sin(i / 4.0) + (0.004 if i % 2 == 0 else -0.004)
        cl = op * (1.0 + ret)
        opens.append(round(op, 2))
        closes.append(round(cl, 2))
        highs.append(round(max(op, cl) * 1.008, 2))
        lows.append(round(min(op, cl) * 0.992, 2))
        vols.append(1_000_000.0)
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=idx,
    )


class TestV45Continuous24hChart(unittest.TestCase):
    def test_01_btc_and_xau_generate_real_24h_series_vwap_and_corridor(self):
        now_utc = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        btc_5m = _make_5m_continuous_df(now_utc - timedelta(hours=24), n_bars=288, start_px=60500.0, vol_val=1500.0)
        xau_5m = _make_5m_continuous_df(now_utc - timedelta(hours=24), n_bars=288, start_px=3530.0, vol_val=800.0)
        btc_daily = _make_daily_history("2026-11-21", n_days=90, base_px=60500.0)
        xau_daily = _make_daily_history("2026-11-21", n_days=90, base_px=3530.0)

        with tempfile.TemporaryDirectory() as td:
            pq_path = os.path.join(td, "intraday_5m_60d.parquet")
            meta_path = os.path.join(td, ".intraday_5m_60d_meta.json")
            out_path = os.path.join(td, "intraday_path_forecasts.json")
            pd.DataFrame({
                "Datetime": [pd.Timestamp("2026-11-20 09:30:00")],
                "Ticker": ["SPY"], "Open": [580.0], "High": [581.0], "Low": [579.0], "Close": [580.5], "Volume": [1000.0],
            }).to_parquet(pq_path, index=False)
            with open(meta_path, "w", encoding="utf-8") as mf:
                mf.write('{"last_refresh_date": "2026-11-20", "last_refresh_slot": "postclose"}')

            mock_dl = MagicMock(side_effect=AssertionError("命中缓存时绝不应重新下载 60d 数据"))
            btc_c24 = ipp._build_24h_series_from_df(btc_5m, "BTC-USD")
            xau_c24 = ipp._build_24h_series_from_df(xau_5m, "XAUUSD")
            self.assertIsNotNone(btc_c24)
            self.assertIsNotNone(xau_c24)

            doc = ipp.update_intraday_path_forecasts(
                tickers=["BTC-USD", "XAUUSD"],
                intraday_map={
                    "BTC-USD": {"continuous_24h": btc_c24},
                    "XAUUSD": {"continuous_24h": xau_c24},
                },
                history_dict={"BTC-USD": btc_daily, "XAUUSD": xau_daily},
                realtime_quotes={"BTC-USD": {"last": btc_c24["actual_prices"][-1]}, "XAUUSD": {"last": xau_c24["actual_prices"][-1]}},
                now_et=datetime(2026, 11, 22, 6, 30, tzinfo=NY_TZ),
                cache_path=pq_path,
                meta_path=meta_path,
                output_path=out_path,
                downloader=mock_dl,
            )
            self.assertEqual(mock_dl.call_count, 0)

            for tk in ("BTC-USD", "XAUUSD"):
                item = doc["by_ticker"][tk]
                self.assertEqual(item["status"], "continuous_24h")
                self.assertTrue(item["has_24h_series"])
                self.assertFalse(item["snapshot_locked"])
                self.assertGreaterEqual(len(item["actual_prices"]), 12)
                self.assertEqual(len(item["vwap_prices"]), len(item["actual_prices"]))
                self.assertEqual(len(item["p25_prices"]), len(item["actual_prices"]))
                self.assertEqual(len(item["p50_prices"]), len(item["actual_prices"]))
                self.assertEqual(len(item["p75_prices"]), len(item["actual_prices"]))
                self.assertLess(item["p25_prices"][0], item["p50_prices"][0])
                self.assertLess(item["p50_prices"][0], item["p75_prices"][0])
                self.assertGreaterEqual(item["high_24h"], item["low_24h"])
                self.assertIn("VWAP", item["summary_text"])
                for field_txt in (item["meta_header"], item["summary_text"], item["divergence"]["badge"]):
                    self.assertNotIn("09:25盘前走廊", field_txt)
                    self.assertNotIn("目标日", field_txt)

            self.assertIn("UTC 00:00 日锚", doc["by_ticker"]["BTC-USD"]["meta_header"])
            self.assertIn("24H 连续走势", doc["by_ticker"]["XAUUSD"]["meta_header"])

    def test_02_xauusd_weekend_closed_fallback_and_spot_basis_calibration(self):
        # 模拟周日查看周五已收盘超过 40 小时的黄金 5m 历史（周末休市兜底）
        friday_end = datetime.now(timezone.utc) - timedelta(hours=40)
        xau_fri = _make_5m_continuous_df(friday_end - timedelta(hours=20), n_bars=180, start_px=3500.0, vol_val=600.0)
        xau_daily = _make_daily_history("2026-11-20", n_days=60, base_px=3500.0)

        item = ipp.compute_continuous_24h_corridor(
            ticker="XAUUSD",
            df_tk=xau_fri,
            history_df=xau_daily,
            now_et=datetime(2026, 11, 22, 6, 30, tzinfo=NY_TZ),
        )
        self.assertEqual(item["status"], "continuous_24h")
        self.assertTrue(item["has_24h_series"])
        self.assertTrue(item["is_weekend_closed"])
        self.assertIn("周末休市", item["divergence"]["badge"])
        self.assertIn("最近 24H", item["divergence"]["badge"])
        self.assertIn("周末展示最近24H", item["meta_header"])
        self.assertGreaterEqual(len(item["actual_prices"]), 12)
        self.assertNotIn("09:25盘前走廊", item["meta_header"])

        # 若周末当轮临时没有抓到 5m 序列，必须优雅沿用 prev_item 保留的最近完整 24H 序列
        reused = ipp.compute_continuous_24h_corridor(
            ticker="XAUUSD",
            intraday_info={},
            df_tk=None,
            history_df=xau_daily,
            prev_item=item,
            now_et=datetime(2026, 11, 22, 7, 0, tzinfo=NY_TZ),
        )
        self.assertTrue(reused["has_24h_series"])
        self.assertTrue(reused["is_weekend_closed"])
        self.assertEqual(reused["actual_prices"], item["actual_prices"])

        # 验证 _apply_xauusd_spot_basis 按 spot/gc 比例同步缩放 continuous_24h 曲线与 VWAP
        hist_d = {"XAUUSD": xau_daily.copy()}
        intra_m = {
            "XAUUSD": {
                "rth_close": 3500.0,
                "vwap": 3490.0,
                "continuous_24h": {
                    "utc_open": 3480.0,
                    "high_24h": 3520.0,
                    "low_24h": 3470.0,
                    "actual_prices": [3480.0, 3500.0],
                    "vwap_prices": [3485.0, 3490.0],
                },
            }
        }
        rt_q = {"XAUUSD": {"last": 3570.0, "has_pct": True}}
        radar._apply_xauusd_spot_basis(hist_d, intra_m, rt_q)
        scaled = intra_m["XAUUSD"]["continuous_24h"]
        ratio = 3570.0 / 3500.0
        self.assertAlmostEqual(scaled["actual_prices"][-1], 3570.0, places=1)
        self.assertAlmostEqual(scaled["utc_open"], round(3480.0 * ratio, 2), places=2)
        self.assertAlmostEqual(intra_m["XAUUSD"]["vwap"], round(3490.0 * ratio, 2), places=2)

    def test_03_y_axis_anti_squashing_and_atr_estimate_non_green(self):
        # 现价 60856.92, 近24H区间 [59500, 62200]
        # 远端 R1=70482.33 (+15.8%), R2=74659.70 (+22.7%) 绝不能拉爆 Y 轴
        # 近端 S1=59824.64, S2=58079.70 (ATR_ESTIMATE) 进入视窗且保持非绿估算标记
        part = ipp.partition_24h_sr_for_viewport(
            spot=60856.92,
            core_min=59500.0,
            core_max=62200.0,
            anchor_sr={
                "anchor_s1": 59824.64,
                "anchor_s2": 58079.70,
                "anchor_r1": 70482.33,
                "anchor_r2": 74659.70,
                "s1_source": "ATR_ESTIMATE",
                "s2_source": "ATR_ESTIMATE",
                "r1_source": "REAL_CHIPS",
                "r2_source": "REAL_CHIPS",
            },
        )
        in_keys = [x["key"] for x in part["in_window"]]
        far_up_keys = [x["key"] for x in part["far_upper"]]
        self.assertIn("S1", in_keys)
        self.assertEqual(far_up_keys, ["R1", "R2"])
        self.assertLess(part["y_max"], 64000.0, "远端 R1/R2 (+15%~+22%) 绝不能把 24H 主图 Y 轴压扁")
        s1_obj = next(x for x in part["in_window"] if x["key"] == "S1")
        self.assertTrue(s1_obj["is_estimate"])

    def test_04_rendered_html_24h_header_no_0925_and_micro_grid_css(self):
        html_out = cft.build_futu_terminal_html(
            stocks_data=[{
                "ticker": "BTC-USD", "name": "比特币现货", "sub": "数字资产", "cat": "数字资产",
                "last": 60856.92, "active_price": 60856.92, "pct_change": 1.27, "vwap": 59992.55,
                "rsi": 48.0, "bias": -12.0, "atr": 1200.0, "fib_low": 58000.0, "fib_high": 63000.0,
                "signal": "🟢 超跌右侧反包",
            }],
            macro_data={
                "SPY": {"name": "标普500", "cat": "大盘基准", "last": 580.0, "active_price": 580.0, "pct_change": -0.5, "vwap": 581.0},
            },
            weather_info={"weather": "暴雨空仓", "market_regime": "BEAR_DEFENSE"},
        )
        # 24H 图表绘制管线（含实时 VWAP 线、视窗外远端 S/R 标签、ATR_ESTIMATE 非绿估算防线与 clean header）
        self.assertIn("id=\"ipVwapLine\"", html_out)
        self.assertIn("id=\"ipFarSrUpper\"", html_out)
        self.assertIn("估算防线 ", html_out)
        self.assertIn("UTC 00:00 日锚", html_out)
        self.assertIn("24H 连续走势", html_out)
        self.assertIn("if (is24h)", html_out)
        # 微结构网格防右边界截断样式
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", html_out)
        self.assertIn("flex-wrap: wrap;", html_out)
        self.assertIn("display: block;\n            font-size: 9.5px;", html_out)


if __name__ == "__main__":
    unittest.main()
