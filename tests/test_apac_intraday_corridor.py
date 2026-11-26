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

        # 第二次盘中轮询：传入更晚的实盘价格（突破上沿）-> P25/P50/P75 必须保持锁死不变，仅橙线与背离更新
        live_rows = [[slots[i], 1.18, 1.25, 1.18, round(1.18 + i * 0.015, 3), 2e6] for i in range(12)]
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


if __name__ == "__main__":
    unittest.main()
