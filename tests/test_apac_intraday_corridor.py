# -*- coding: utf-8 -*-
"""
亚太/A 股 16 标的「物理隔离今日盘中推演走廊」隔离单测 (tests/test_apac_intraday_corridor.py)
覆盖：
1. 严格零未来函数 (hist_date < forecast_date) 与锁定走廊不变性；
2. 分市场时段与午休 Bar 剔除 (CN 48 / HK 66 / JP 66 / TW 54) + 异常巨量清洗；
3. 短历史 5m 自适应 Top-K (>=20 -> 12 / 8~19 -> 5~10 / <8 -> insufficient_history)；
4. 休眠期 SLEEP_CACHE (http=0) 与冷启动/收盘后增量调度；
5. 前端主终端 HTML 注入 APAC_INTRADAY_PATH_FORECASTS、KOSPI 通道零污染、全链路零 `$`、无 09:25 ET 误导提示。
"""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

import apac_ashare_feed as aaf
import apac_intraday_path_predictor as aip
import create_futu_terminal as cft

CST = ZoneInfo("Asia/Shanghai")


def _make_day_slot_map(slots, base_px=1.200, trend_pct=0.5, decimals=3):
    n = len(slots)
    out = {}
    for i, sl in enumerate(slots):
        frac = i / max(1, n - 1)
        px = round(base_px * (1.0 + (trend_pct / 100.0) * frac), decimals)
        out[sl] = [px, round(px * 1.003, decimals), round(px * 0.997, decimals), px, 1000000.0]
    return out


class TestApacIntradayCorridor(unittest.TestCase):
    def test_01_market_slot_counts_and_lunch_removal(self):
        """验证四大市场规范交易槽数量及严格午休剔除。"""
        cn = aip.get_market_spec("CN")["slots"]
        hk = aip.get_market_spec("HK")["slots"]
        jp = aip.get_market_spec("JP")["slots"]
        tw = aip.get_market_spec("TW")["slots"]
        self.assertEqual(len(cn), 48)
        self.assertEqual(len(hk), 66)
        self.assertEqual(len(jp), 66)
        self.assertEqual(len(tw), 54)
        # CN 无 11:30~12:55 午休槽
        self.assertNotIn("11:30", cn)
        self.assertNotIn("12:00", cn)
        self.assertNotIn("12:55", cn)
        self.assertIn("11:25", cn)
        self.assertIn("13:00", cn)
        # HK 无 12:00~12:55 午休槽
        self.assertNotIn("12:00", hk)
        self.assertNotIn("12:30", hk)
        # JP 无 11:30~12:25 午休槽
        self.assertNotIn("11:30", jp)
        self.assertNotIn("12:00", jp)
        self.assertIn("12:30", jp)

    def test_02_strict_zero_lookahead_and_locked_corridor_immutability(self):
        """严格验证 hist_date < forecast_date，且锁定后仅增量追加橙色实盘线与背离徽章。"""
        slots = aip.get_market_spec("CN")["slots"]
        meta = aaf.APAC_ASHARE_BY_TICKER["159530"]
        dmaps = {}
        # 25 个历史日 (2026-10-01 .. 2026-10-25) + 目标日 2026-10-26 + 未来日 2026-10-27
        for i in range(1, 28):
            d_str = f"2026-10-{i:02d}"
            trend = 8.0 if i == 27 else (0.4 if i % 2 == 0 else -0.3)
            dmaps[d_str] = _make_day_slot_map(slots, base_px=1.150 + i * 0.001, trend_pct=trend, decimals=3)

        # 目标日先仅给前 5 根 5m 实盘
        partial_today = {k: dmaps["2026-10-26"][k] for k in slots[:5]}
        dmaps_run1 = dict(dmaps)
        dmaps_run1["2026-10-26"] = partial_today

        res1 = aip.build_single_ticker_corridor(
            ticker="159530",
            day_slot_maps=dmaps_run1,
            meta=meta,
            forecast_date="2026-10-26",
        )
        self.assertEqual(res1["status"], "ok")
        self.assertTrue(res1["snapshot_locked"])
        self.assertEqual(res1["matched_count"], 12)
        self.assertEqual(len(res1["p50_prices"]), 48)
        for md in res1["matched_dates"]:
            self.assertLess(md, "2026-10-26", "严禁匹配 >= forecast_date 的未来交易日")
        self.assertNotIn("2026-10-26", res1["matched_dates"])
        self.assertNotIn("2026-10-27", res1["matched_dates"])

        # 第二次盘中轮询：保留同一真实 09:30 Open，传入更晚的实盘价格（突破上沿）-> P25/P50/P75 必须保持锁死不变，仅橙线与背离更新
        live_rows = [
            [slots[i], res1["anchor_open"] if i == 0 else 1.18, 1.25, res1["anchor_open"] if i == 0 else 1.18, round(1.18 + i * 0.015, 3), 2e6]
            for i in range(12)
        ]
        res2 = aip.build_single_ticker_corridor(
            ticker="159530",
            day_slot_maps=dmaps_run1,
            meta=meta,
            forecast_date="2026-10-26",
            live_5m_rows=live_rows,
            prev_locked_item=res1,
        )
        self.assertEqual(res2["p25_prices"], res1["p25_prices"])
        self.assertEqual(res2["p50_prices"], res1["p50_prices"])
        self.assertEqual(res2["p75_prices"], res1["p75_prices"])
        self.assertEqual(res2["latest_slot"], 11)
        self.assertEqual(res2["divergence"]["status"], "ABOVE_P75")
        self.assertIn("突破推演上沿", res2["divergence"]["badge"])

    def test_03_adaptive_top_k_for_short_history(self):
        """验证短历史 5m 自适应 Top-K (>=20 -> 12; 8~19 -> 5~10; <8 -> insufficient_history)。"""
        self.assertEqual(aip.choose_adaptive_top_k(59), 12)
        self.assertEqual(aip.choose_adaptive_top_k(20), 12)
        self.assertEqual(aip.choose_adaptive_top_k(16), 8)
        self.assertEqual(aip.choose_adaptive_top_k(10), 5)
        self.assertEqual(aip.choose_adaptive_top_k(8), 5)
        self.assertEqual(aip.choose_adaptive_top_k(7), 0)

        slots = aip.get_market_spec("CN")["slots"]
        meta = aaf.APAC_ASHARE_BY_TICKER["588170"]

        # 12 个历史日 -> K = 6
        dmaps_12 = {f"2026-10-{i:02d}": _make_day_slot_map(slots, base_px=1.30, trend_pct=0.2) for i in range(1, 14)}
        r12 = aip.build_single_ticker_corridor("588170", dmaps_12, meta, forecast_date="2026-10-13")
        self.assertEqual(r12["status"], "ok")
        self.assertEqual(r12["history_days_count"], 12)
        self.assertEqual(r12["matched_count"], 6)
        self.assertIn("历史匹配 6 个相似A股开盘日", r12["summary_text"])

        # 5 个历史日 -> 优雅降级 insufficient_history
        dmaps_5 = {f"2026-10-{i:02d}": _make_day_slot_map(slots, base_px=1.30, trend_pct=0.2) for i in range(1, 7)}
        r5 = aip.build_single_ticker_corridor("588170", dmaps_5, meta, forecast_date="2026-10-06")
        self.assertEqual(r5["status"], "insufficient_history")
        self.assertIn("正在每日累积 5m 分时样本", r5["status_message"])

    def test_04_sleep_cache_zero_http_and_cold_start_schedule(self):
        """验证美股夜间休眠期严格命中 SLEEP_CACHE (http=0)，冷启动缺失立即全量初算。"""
        mode_cold, need_cold = aip.should_refresh_apac_intraday({}, parquet_exists=False, now_dt=datetime(2026, 11, 26, 23, 0, tzinfo=CST))
        self.assertEqual(mode_cold, "COLD_START_FULL")
        self.assertTrue(need_cold)

        fake_doc = {
            "last_parquet_refresh_date": "2026-11-26",
            "by_ticker": {m["ticker"]: {"status": "ok"} for m in aaf.ALL_APAC_ASHARE_TARGETS},
        }
        mode_sleep, need_sleep = aip.should_refresh_apac_intraday(fake_doc, parquet_exists=True, now_dt=datetime(2026, 11, 26, 23, 30, tzinfo=CST))
        self.assertEqual(mode_sleep, "SLEEP_CACHE")
        self.assertFalse(need_sleep)

        # 次日亚盘收盘后 16:20 CST -> 触发每日一次增量追加
        mode_post, need_post = aip.should_refresh_apac_intraday(fake_doc, parquet_exists=True, now_dt=datetime(2026, 11, 27, 16, 20, tzinfo=CST))
        self.assertEqual(mode_post, "POST_CLOSE_APPEND")
        self.assertTrue(need_post)

    def test_05_terminal_html_injects_apac_corridor_zero_dollar_and_preserves_kospi(self):
        """验证终端 HTML 独立注入 APAC_INTRADAY_PATH_FORECASTS，不污染 KOSPI，且推演文案零 `$`。"""
        macro = {"KOSPI": {"name": "韩国综指", "last": 4740.2, "pct_change": 0.91, "price_unit": "pts", "is_index": True}}
        stocks = [{"ticker": "NVDA", "name": "英伟达", "last": 135.0, "pct_change": 1.0}]
        apac_items = {
            "159530": {
                "ticker": "159530", "name": "机器人ETF易方达", "kind": "ASHARE_ETF", "market": "CN",
                "price_unit": "cny", "last": 1.124, "pct_change": -2.18,
            }
        }
        slots = aip.get_market_spec("CN")["slots"]
        apac_ip = {
            "forecast_date": "2026-11-26",
            "by_ticker": {
                "159530": {
                    "ticker": "159530", "market": "CN", "market_cn": "A股", "price_unit": "cny",
                    "session_tz": "CST", "forecast_date": "2026-11-26", "status": "ok",
                    "summary_text": "历史匹配 12 个相似A股开盘日 (开盘跳空 +1.74%)：多呈冲高回落",
                    "anchor_label": "09:30 CST 开盘已锁定", "anchor_open": 1.170, "prev_close": 1.150,
                    "time_slots": slots, "early_window_slot": 3, "early_window_label": "09:45 CST 观察窗",
                    "lunch_seam_idx": 24, "p25_prices": [1.16] * 48, "p50_prices": [1.17] * 48, "p75_prices": [1.18] * 48,
                    "actual_prices": [1.17] * 48, "divergence": {"status": "INSIDE_BAND", "badge": "✅ 走势贴合推演走廊", "color": "#38bdf8"},
                }
            },
        }
        html = cft.build_futu_terminal_html(
            stocks_data=stocks,
            macro_data=macro,
            weather_info={"weather": "阴天防守"},
            session_code="HOLIDAY",
            apac_doc={"items": apac_items},
            apac_intraday_doc=apac_ip,
        )
        self.assertIn("const APAC_INTRADAY_PATH_FORECASTS =", html)
        m = re.search(r"const APAC_INTRADAY_PATH_FORECASTS = (\{.*?\});", html)
        self.assertIsNotNone(m)
        parsed = json.loads(m.group(1))
        self.assertIn("159530", parsed["by_ticker"])
        self.assertNotIn("KOSPI", parsed["by_ticker"])
        self.assertNotIn("$", json.dumps(parsed["by_ticker"]["159530"], ensure_ascii=False))
        self.assertIn("亚太/A股独立推演底模同步中", html)

    def test_06_hstech_tencent_1m_to_5m_aggregation_and_lunch_strip(self):
        """验证 HSTECH 腾讯实时 1m→5m 无损聚合、港股 12:00~13:00 午休剔除及当日实盘进度截取。"""
        raw_lines = [
            "0930 3433.380 74258 742577333.000",
            "0931 3457.990 155549 1555487409.000",
            "0932 3452.100 200000 2000000000.000",
            "0933 3460.000 250000 2500000000.000",
            "0934 3455.500 300000 3000000000.000",
            "0935 3458.000 340000 3400000000.000",
            "0936 3470.200 390000 3900000000.000",
            "1205 3470.200 390000 3900000000.000",  # 港股午休噪声必须被剔除
            "1230 3470.200 390000 3900000000.000",  # 港股午休噪声必须被剔除
        ]
        agg = aip._aggregate_tencent_1m_to_5m(raw_lines, decimals=2)
        self.assertEqual(agg[0][0], "09:30")
        self.assertAlmostEqual(agg[0][1], 3433.38, places=2)  # open
        self.assertAlmostEqual(agg[0][2], 3460.00, places=2)  # high
        self.assertAlmostEqual(agg[0][3], 3433.38, places=2)  # low
        self.assertAlmostEqual(agg[0][4], 3455.50, places=2)  # close
        self.assertEqual(agg[1][0], "09:35")
        self.assertAlmostEqual(agg[1][4], 3470.20, places=2)

        smap = aip._normalize_day_bars_to_canonical_slots(agg, market="HK", decimals=2)
        self.assertIn("09:30", smap)
        self.assertIn("09:35", smap)
        self.assertNotIn("12:05", smap)
        self.assertNotIn("12:30", smap)

        # 用 HSTECH 历史底模 + 仅 09:30~09:35 两根当日柱，验证切到 2026-11-27 且橙线仅画到 slot 1
        hk_slots = aip.get_market_spec("HK")["slots"]
        meta = aaf.APAC_ASHARE_BY_TICKER["HSTECH"]
        dmaps = {f"2026-10-{i:02d}": _make_day_slot_map(hk_slots, base_px=3400.0 + i, trend_pct=0.3, decimals=2) for i in range(1, 25)}
        res = aip.build_single_ticker_corridor(
            ticker="HSTECH",
            day_slot_maps=dmaps,
            meta=meta,
            forecast_date="2026-11-27",
            live_5m_rows=agg,
        )
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["forecast_date"], "2026-11-27")
        self.assertAlmostEqual(res["anchor_open"], 3433.38, places=2)
        self.assertEqual(res["latest_slot"], 1)
        self.assertEqual(sum(1 for x in res["actual_prices"] if x is not None), 2)
        self.assertIsNone(res["actual_prices"][2])

    def test_07_ashare_midday_lock_strictly_uses_0930_open_and_autoheals_drifted_anchor(self):
        """验证盘中 11:00 锁锚严格取 09:30 首根 5m Open、偏离 >0.3% 存量歪锚自动自愈、对齐后严守锁定不变性。"""
        cn_slots = aip.get_market_spec("CN")["slots"]
        meta = aaf.APAC_ASHARE_BY_TICKER["159530"]
        dmaps = {
            f"2026-10-{i:02d}": _make_day_slot_map(cn_slots, base_px=1.120 + i * 0.002, trend_pct=0.4, decimals=3)
            for i in range(1, 25)
        }
        # 构造 2026-11-27 截至 11:00 的 5m 实盘：09:30 Open=1.131，11:00 已拉升到 1.170
        live_rows = []
        for idx in range(19):  # 09:30 .. 11:00
            sl = cn_slots[idx]
            px = round(1.131 + (1.170 - 1.131) * (idx / 18.0), 3)
            op = 1.131 if idx == 0 else px
            live_rows.append([sl, op, round(px + 0.003, 3), round(px - 0.002, 3), px, 500000.0])

        # 1) 盘中 11:00 首次计算：必须严锁 09:30 Open=1.131，绝非 11:00 的 1.170
        fresh = aip.build_single_ticker_corridor(
            ticker="159530",
            day_slot_maps=dmaps,
            meta=meta,
            forecast_date="2026-11-27",
            live_5m_rows=live_rows,
            spot_open=1.131,
            spot_prev_close=1.123,
        )
        self.assertEqual(fresh["status"], "ok")
        self.assertAlmostEqual(fresh["anchor_open"], 1.131, places=3)
        self.assertEqual(fresh["open_anchor_source"], "first_5m_open")
        self.assertTrue(fresh["snapshot_locked"])
        self.assertAlmostEqual(fresh["gap_pct"], round((1.131 / fresh["prev_close"] - 1.0) * 100.0, 2), places=2)

        # 2) 存量歪锚（旧锁 1.170，与真实 09:30 Open=1.131 偏离 3.45% > 0.3%）：触发一次自愈重算
        drifted_prev = dict(fresh)
        drifted_prev["anchor_open"] = 1.170
        drifted_prev["gap_pct"] = 4.19
        drifted_prev["open_anchor_source"] = "legacy"
        drifted_prev["p50_prices"] = [9.999] * 48
        healed = aip.build_single_ticker_corridor(
            ticker="159530",
            day_slot_maps=dmaps,
            meta=meta,
            forecast_date="2026-11-27",
            live_5m_rows=live_rows,
            prev_locked_item=drifted_prev,
            spot_open=1.131,
            spot_prev_close=1.123,
        )
        self.assertAlmostEqual(healed["anchor_open"], 1.131, places=3)
        self.assertEqual(healed["open_anchor_source"], "first_5m_open")
        self.assertTrue(healed["snapshot_locked"])
        self.assertNotEqual(healed["p50_prices"][0], 9.999)

        # 3) 已对齐快照（open_anchor_source="first_5m_open" 且偏离 <= 0.3%）：严格保持锁定，禁止重绘走廊
        aligned_prev = dict(healed)
        sentinel_p50 = [1.234] * 48
        aligned_prev["p50_prices"] = sentinel_p50
        kept = aip.build_single_ticker_corridor(
            ticker="159530",
            day_slot_maps=dmaps,
            meta=meta,
            forecast_date="2026-11-27",
            live_5m_rows=live_rows,
            prev_locked_item=aligned_prev,
            spot_open=1.131,
            spot_prev_close=1.123,
        )
        self.assertTrue(kept["snapshot_locked"])
        self.assertEqual(kept["p50_prices"], sentinel_p50)

    def test_08_kospi_slot0_anchor_without_fabricating_missing_slots(self):
        """验证 KOSPI 仅在 09:00 槽缺失且后续有实盘时锚定 slot 0，绝不伪造 09:05/09:10/09:15 中间缺失槽。"""
        from datetime import date
        import pandas as pd
        import intraday_path_predictor as ipp

        kst = ZoneInfo("Asia/Seoul")
        df_5m = pd.DataFrame(
            {
                "Datetime": [
                    datetime(2026, 11, 27, 9, 20, tzinfo=kst),
                    datetime(2026, 11, 27, 9, 25, tzinfo=kst),
                ],
                "Open": [4750.0, 4731.0],
                "High": [4755.0, 4735.0],
                "Low": [4745.0, 4725.0],
                "Close": [4750.17, 4731.06],
                "Volume": [3000.0, 2800.0],
            }
        )
        prev_close, anchor_open, ap = ipp._extract_krx_day_features_for_target(
            df_tk=df_5m,
            target_date=date(2026, 11, 27),
            intraday_info=None,
            history_df=None,
            rq={"open": 4728.80, "prev_close": 4740.20, "price": 4731.06},
            cur_kst=datetime(2026, 11, 27, 9, 30, tzinfo=kst),
        )
        self.assertAlmostEqual(anchor_open, 4728.80, places=2)
        self.assertAlmostEqual(prev_close, 4740.20, places=2)
        self.assertAlmostEqual(ap[0], 4728.80, places=2)
        self.assertIsNone(ap[1])
        self.assertIsNone(ap[2])
        self.assertIsNone(ap[3])
        self.assertAlmostEqual(ap[4], 4750.17, places=2)
        self.assertAlmostEqual(ap[5], 4731.06, places=2)

    def test_09_frontend_tooltip_distinguishes_missing_source_vs_future_slot(self):
        """验证前端推演图悬停浮窗逻辑按 lastActualIdx 区分「数据源缺失」与「未到时段」。"""
        stocks = [{"ticker": "NVDA", "name": "英伟达", "price": 180.0, "change_pct": 1.0, "sector": "AI", "volume": 100}]
        macro = {"NQ=F": {"price": 25000.0, "change_pct": 0.5}}
        html = cft.build_futu_terminal_html(
            stocks_data=stocks,
            macro_data=macro,
            weather_info={"weather": "晴"},
            session_code="HOLIDAY",
        )
        self.assertIn("lastActualIdx", html)
        self.assertIn("—（数据源缺失）", html)
        self.assertIn("—（未到时段）", html)

    def test_10_ml_predictor_deduplicates_duplicate_tickers_in_json_and_html(self):
        """验证 ml_predictor_5y 导出 JSON 时严格按 ticker 去重，杜绝 MSFT/KOSPI 重复行。"""
        import os
        import tempfile
        import ml_predictor_5y as mp

        sample_rows = [
            {"ticker": "BTC-USD", "short_rank": 1, "rank": 10, "calibrated_win_rate_pct": 40.0, "short_calibrated_win_rate_pct": 56.3, "latest_price": 61678.63, "sector_cn": "加密货币代理"},
            {"ticker": "MSFT", "short_rank": 2, "rank": 11, "calibrated_win_rate_pct": 42.0, "short_calibrated_win_rate_pct": 53.5, "latest_price": 499.92, "sector_cn": "企业软件"},
            {"ticker": "MSFT", "short_rank": 3, "rank": 12, "calibrated_win_rate_pct": 42.0, "short_calibrated_win_rate_pct": 53.5, "latest_price": 499.92, "sector_cn": "企业软件"},
            {"ticker": "KOSPI", "short_rank": 4, "rank": 13, "calibrated_win_rate_pct": 41.0, "short_calibrated_win_rate_pct": 53.1, "latest_price": 4910.15, "sector_cn": "亚太指数"},
            {"ticker": "KOSPI", "short_rank": 5, "rank": 14, "calibrated_win_rate_pct": 41.0, "short_calibrated_win_rate_pct": 53.1, "latest_price": 4910.15, "sector_cn": "亚太指数"},
            {"ticker": "QQQ", "short_rank": 6, "rank": 15, "calibrated_win_rate_pct": 44.0, "short_calibrated_win_rate_pct": 51.0, "latest_price": 505.0, "sector_cn": "宽基ETF"},
            {"ticker": "CAT", "short_rank": 7, "rank": 16, "calibrated_win_rate_pct": 45.0, "short_calibrated_win_rate_pct": 50.2, "latest_price": 380.0, "sector_cn": "工业基建"},
        ]
        tables_data = {
            "kpi": {},
            "macro_weather": {},
            "all_live_predictions": sample_rows,
            "top_long": sample_rows,
            "top_short": sample_rows,
            "table4": sample_rows,
        }
        with tempfile.TemporaryDirectory() as td:
            out_json = os.path.join(td, "latest_ai_predictions.json")
            mp.export_latest_ai_predictions_json(tables_data, json_path=out_json)
            with open(out_json, "r", encoding="utf-8") as f:
                doc = json.load(f)
            all_tks = [r["ticker"] for r in doc["all_predictions"]]
            short_tks = [r["ticker"] for r in doc["top_short"]]
            self.assertEqual(all_tks, ["BTC-USD", "MSFT", "KOSPI", "QQQ", "CAT"])
            self.assertEqual(short_tks, ["BTC-USD", "MSFT", "KOSPI", "QQQ", "CAT"])
            self.assertEqual([r["short_rank"] for r in doc["top_short"]], [1, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
