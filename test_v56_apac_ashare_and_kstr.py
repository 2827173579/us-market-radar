# -*- coding: utf-8 -*-
"""
V5.6 隔离单元测试：亚太纯指数宏观联动 (6 只) + A 股场内 ETF (10 只) + KSTR 入池 (80 -> 81)
=========================================================================================
严格验证：
1. A 股 ETF `¥` 3 位小数格式化（严格 ¥0.000，全链路零 `$`）
2. 亚太纯指数 `pt` 格式化（全链路零 `$`）
3. 美股时段时区智能休眠（断言 `0` 次外部 HTTP 请求）
4. `apac_chip_sentiment` 亚盘芯片权重组 + 大中华风险情绪组聚合 & `macro_radar.html` 卡片渲染
5. 纯美股四维温度计 (`market_temperature_engine.py`) 100% 排除亚太指数与 A 股 ETF（扰动严格为 0.0）
6. `KSTR` 正式入池（80 -> 81 只美股标的，同步覆盖主雷达 / ML / 终端 / 安卓 / Discord Bot）
7. Discord Bot `/check` 支持代码与中文名（`512690` / `酒ETF` / `科创50` / `恒生科技` / `KSTR`）纯本地只读查询
"""

import json
import os
import re
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import apac_ashare_feed as aaf
import create_futu_terminal as cft
import discord_radar_bot as bot
import integrated_market_radar as radar
import market_temperature_engine as mte
import ml_predictor_5y as ml5


def _build_mock_apac_items():
    items = {}
    for meta in aaf.APAC_PURE_INDICES:
        tk = meta["ticker"]
        items[tk] = {
            **meta,
            "last": 39262.39 if tk == "^TWII" else (57440.36 if tk == "^N225" else 22314.95 if tk == "^HSI" else 3440.30 if tk == "HSTECH" else 1169.99 if tk == "000688" else 2189.82),
            "price": 39262.39 if tk == "^TWII" else (57440.36 if tk == "^N225" else 22314.95 if tk == "^HSI" else 3440.30 if tk == "HSTECH" else 1169.99 if tk == "000688" else 2189.82),
            "pct_change": 2.50 if tk in ("^TWII", "^N225", "^HSI", "HSTECH") else -0.85,
            "ma20": 38000.0 if tk == "^TWII" else 2100.0,
            "ma50": 37000.0 if tk == "^TWII" else 2050.0,
            "bias_ma20": 1.25,
            "rsi": 52.3,
            "s1": 38100.0 if tk == "^TWII" else 1120.50,
            "s2": 37200.0 if tk == "^TWII" else 1080.25,
            "r1": 40500.0 if tk == "^TWII" else 1230.80,
            "r2": 41800.0 if tk == "^TWII" else 1280.40,
            "turnover": 2.5e10,
            "daily_ohlc": [
                ["2026-11-24", 1150.0, 1180.0, 1140.0, 1170.0, 100000],
                ["2026-11-25", 1170.0, 1190.0, 1160.0, 1180.0, 110000],
                ["2026-11-26", 1180.0, 1195.0, 1165.0, 1169.99, 120000],
            ],
            "intraday_5m": [
                ["09:35", 1175.0, 1180.0, 1172.0, 1178.0, 5000],
                ["09:40", 1178.0, 1182.0, 1168.0, 1169.99, 6000],
            ],
            "source": "mock",
        }
    for idx, meta in enumerate(aaf.ASHARE_ETFS):
        tk = meta["ticker"]
        base_px = 1.766 if tk == "512530" else (0.428 if tk == "512690" else round(0.750 + idx * 0.055, 3))
        items[tk] = {
            **meta,
            "last": base_px,
            "price": base_px,
            "pct_change": 1.80 if tk in ("588200", "588170", "159995") else -0.62,
            "ma20": round(base_px * 0.99, 3),
            "ma50": round(base_px * 0.97, 3),
            "bias_ma20": 1.01,
            "rsi": 48.5,
            "s1": round(base_px * 0.95, 3),
            "s2": round(base_px * 0.91, 3),
            "r1": round(base_px * 1.05, 3),
            "r2": round(base_px * 1.09, 3),
            "turnover": 8.45e8,
            "daily_ohlc": [
                ["2026-11-24", round(base_px * 0.99, 3), round(base_px * 1.01, 3), round(base_px * 0.98, 3), round(base_px * 0.995, 3), 200000],
                ["2026-11-25", round(base_px * 0.995, 3), round(base_px * 1.02, 3), round(base_px * 0.99, 3), round(base_px * 1.005, 3), 220000],
                ["2026-11-26", round(base_px * 1.005, 3), round(base_px * 1.015, 3), round(base_px * 0.992, 3), base_px, 240000],
            ],
            "intraday_5m": [
                ["09:35", round(base_px * 0.998, 3), round(base_px * 1.006, 3), round(base_px * 0.996, 3), round(base_px * 1.002, 3), 15000],
                ["09:40", round(base_px * 1.002, 3), round(base_px * 1.008, 3), round(base_px * 0.997, 3), base_px, 18000],
            ],
            "source": "mock",
        }
    return items


class TestV56ApacAshareAndKstr(unittest.TestCase):
    def test_price_formatting_cny_3_decimals_and_pts_zero_dollar(self):
        self.assertEqual(aaf.format_apac_price(1.766, "cny"), "¥1.766")
        self.assertEqual(aaf.format_apac_price(0.4, "cny"), "¥0.400")
        self.assertEqual(aaf.format_apac_price(1.2346, "cny"), "¥1.235")
        self.assertNotIn("$", aaf.format_apac_price(1.766, "cny"))

        pts_s = aaf.format_apac_price(39262.39, "pts")
        self.assertEqual(pts_s, "39,262.39 pt")
        self.assertNotIn("$", pts_s)
        self.assertEqual(aaf.format_apac_price(1169.99, "pts"), "1,169.99 pt")

    def test_timezone_smart_sleep_zero_http_during_us_session(self):
        # 亚盘活跃窗 (10:30 KST / 09:30 CST 周四) -> True
        kst_open = datetime(2026, 11, 26, 10, 30, tzinfo=ZoneInfo("Asia/Seoul"))
        self.assertTrue(aaf.is_apac_active_window(kst_open))
        # 亚盘收盘后不久 (17:14 KST / 16:14 CST) -> True
        kst_tail = datetime(2026, 11, 26, 17, 14, tzinfo=ZoneInfo("Asia/Seoul"))
        self.assertTrue(aaf.is_apac_active_window(kst_tail))
        # 亚盘休眠边界外 (17:20 KST) -> False
        kst_sleep = datetime(2026, 11, 26, 17, 20, tzinfo=ZoneInfo("Asia/Seoul"))
        self.assertFalse(aaf.is_apac_active_window(kst_sleep))
        # 美股常规盘中 (10:30 ET 周四 == 00:30+1 KST) -> False
        us_rth = datetime(2026, 11, 26, 10, 30, tzinfo=ZoneInfo("America/New_York"))
        self.assertFalse(aaf.is_apac_active_window(us_rth))
        # 周末 -> False
        kst_sun = datetime(2026, 11, 29, 10, 30, tzinfo=ZoneInfo("Asia/Seoul"))
        self.assertFalse(aaf.is_apac_active_window(kst_sun))

        # 断言美股时段调用 fetch_apac_ashare_snapshot 严格 0 次外部抓取
        with tempfile.TemporaryDirectory() as td:
            cache_p = os.path.join(td, "apac_ashare_cache.json")
            seed_items = _build_mock_apac_items()
            seed_doc = {
                "schema_version": "v5.6-apac-ashare-feed",
                "active_window": True,
                "fetch_mode": "LIVE_FETCH",
                "http_calls": 10,
                "updated_at_cst": "2026-11-26 15:05:00",
                "fetched_epoch": 1795590300.0,
                "count": len(seed_items),
                "items": seed_items,
                "apac_chip_sentiment": aaf.compute_apac_chip_sentiment(seed_items),
            }
            with open(cache_p, "w", encoding="utf-8") as fp:
                json.dump(seed_doc, fp, ensure_ascii=False)

            called = {"n": 0}

            def _forbid_http():
                called["n"] += 1
                raise AssertionError("美股休眠时段严禁发起任何外部 HTTP 请求！")

            res = aaf.fetch_apac_ashare_snapshot(
                now_dt=us_rth,
                cache_path=cache_p,
                kospi_item={"last": 2550.0, "pct_change": 1.2},
                fetcher_override=_forbid_http,
            )
            self.assertEqual(called["n"], 0)
            self.assertEqual(res.get("http_calls"), 0)
            self.assertEqual(res.get("fetch_mode"), "SLEEP_CACHE")
            self.assertFalse(res.get("active_window"))
            self.assertEqual(len(res.get("items") or {}), 16)

    def test_apac_chip_sentiment_and_macro_card_html(self):
        items = _build_mock_apac_items()
        sent = aaf.compute_apac_chip_sentiment(
            items,
            kospi_item={"last": 2610.50, "pct_change": 2.10},
        )
        self.assertEqual(sent.get("schema_version"), "v5.6-apac-chip-sentiment")
        cg = sent["chip_group"]
        gc = sent["greater_china_group"]
        self.assertEqual(cg["total_valid"], 6)
        self.assertEqual(gc["total_valid"], 4)
        self.assertGreater(cg["weighted_pct"], 1.5)
        self.assertEqual(sent["soxx_lead_bias"], "BULLISH")

        html_card = aaf.render_macro_apac_section_html({
            "active_window": False,
            "fetch_mode": "SLEEP_CACHE",
            "updated_at_cst": "2026-11-26 15:05:00",
            "items": items,
            "apac_chip_sentiment": sent,
        })
        self.assertIn("apacChipSentimentCard", html_card)
        self.assertIn("亚太指数与半导体风向", html_card)
        self.assertIn("¥1.135", html_card)
        self.assertIn("¥1.245", html_card)
        self.assertIn("¥1.025", html_card)
        self.assertIn("pt", html_card)
        self.assertNotIn("$", html_card)

    def test_pure_us_temperature_excludes_all_apac_and_ashare_tickers(self):
        base_macro = {
            "QQQ": {"last": 500.0, "pct_change": 0.4, "bias": 2.0, "ma50": 490.0, "rsi": 55.0},
            "SPY": {"last": 590.0, "pct_change": 0.3, "bias": 1.5, "ma50": 581.0, "rsi": 54.0},
            "SMH": {"last": 240.0, "pct_change": 0.6, "bias": 3.0, "ma50": 233.0, "rsi": 57.0},
            "KSTR": {"last": 16.87, "pct_change": -0.5, "bias": -1.2, "ma50": 17.07, "rsi": 46.0},
        }
        base_stocks = [
            {"ticker": "NVDA", "last": 140.0, "pct_change": 1.1, "bias": 4.0, "ma50": 134.6, "rsi": 60.0, "ema20": 137.0, "vwap": 139.0},
            {"ticker": "AAPL", "last": 230.0, "pct_change": 0.2, "bias": 1.0, "ma50": 227.7, "rsi": 52.0, "ema20": 229.0, "vwap": 229.5},
        ]
        vix_reg = {"vix_spot": 16.5, "vix3m_spot": 19.0, "vix_term_ratio": 0.868, "vix_pct": -1.2}
        fut_snap = {
            "NQ=F": {"last": 21000.0, "pct_chg": 0.35, "vwap_diff_pct": 0.15, "rsi_5m": 54.0},
            "ES=F": {"last": 6000.0, "pct_chg": 0.25, "vwap_diff_pct": 0.10, "rsi_5m": 52.0},
        }

        t_clean = mte.compute_market_temperature(
            macro_data=base_macro,
            stocks_data=base_stocks,
            vix_regime=vix_reg,
            futures_snapshot=fut_snap,
            session_code="REGULAR",
        )

        # 注入全部 16 只亚太/A 股极端暴涨暴跌扰动
        polluted_macro = dict(base_macro)
        polluted_stocks = list(base_stocks)
        for tk in aaf.APAC_ASHARE_TICKER_SET:
            self.assertIn(tk, mte.EXCLUDED_TEMPERATURE_TICKERS)
            extreme_row = {
                "ticker": tk, "last": 9999.0, "pct_change": 48.0, "bias": 85.0,
                "ma50": 5000.0, "rsi": 99.0, "ema20": 5500.0, "vwap": 6000.0,
            }
            polluted_macro[tk] = extreme_row
            polluted_stocks.append(extreme_row)

        t_polluted = mte.compute_market_temperature(
            macro_data=polluted_macro,
            stocks_data=polluted_stocks,
            vix_regime=vix_reg,
            futures_snapshot=fut_snap,
            session_code="REGULAR",
        )
        for key in ("macro_bubble_score", "intraday_bubble_score", "macro_bottom_score", "intraday_bottom_score"):
            self.assertEqual(t_clean[key], t_polluted[key], f"{key} perturbed by APAC/A-share tickers!")

    def test_kstr_registered_across_81_target_us_pool(self):
        etfs = radar.MONITORED_TARGETS["ETFS"]
        stocks = radar.MONITORED_TARGETS["STOCKS"]
        self.assertIn("KSTR", etfs)
        self.assertEqual(len(etfs) + len(stocks), 81)
        self.assertIn("KSTR", ml5.EXTENDED_RADAR_SECTOR_MAP)
        for ap_tk in aaf.APAC_ASHARE_TICKER_SET:
            self.assertNotIn(ap_tk, etfs)
            self.assertNotIn(ap_tk, stocks)

        with open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "android_tablet", "TradeRadar_Android.py"),
            "r",
            encoding="utf-8",
        ) as _af:
            android_src = _af.read()
        self.assertIn('"KSTR"', android_src)

    def test_discord_bot_check_apac_ashare_and_kstr(self):
        mock_apac = _build_mock_apac_items()
        snapshot = {
            "updated_at_et": "2026-11-26 10:30:00",
            "session_code": "REGULAR",
            "session_name": "常规盘中",
            "market_regime": "NEUTRAL_CHOP",
            "weather": "阴天防守",
            "ticker_count": 2,
            "tickers": {
                "NVDA": {"ticker": "NVDA", "name": "英伟达", "cat": "芯片算力", "last": 140.5, "price": 140.5, "pct_change": 1.2, "decision_stance": "BULL"},
                "KSTR": {"ticker": "KSTR", "name": "中国科技半导体ETF", "cat": "半导体", "last": 16.87, "price": 16.87, "pct_change": -1.5, "decision_stance": "NEUTRAL", "s1": 16.10, "r1": 17.65},
            },
            "apac_ashare_meta": {"active_window": False, "fetch_mode": "SLEEP_CACHE", "updated_at_cst": "2026-11-26 15:05:00", "count": 16},
            "apac_ashare_items": {k: {kk: vv for kk, vv in v.items() if kk not in ("daily_ohlc", "intraday_5m")} for k, v in mock_apac.items()},
        }

        # 1. A 股 ETF 代码与中文名 (/check 512690 & /check 酒ETF) -> ¥0.428 三位小数，零 $
        self.assertEqual(bot.resolve_ticker("512690", snapshot), "512690")
        self.assertEqual(bot.resolve_ticker("酒ETF", snapshot), "512690")
        emb_512690 = bot.build_check_embed_dict("酒ETF", snapshot)
        txt_512690 = json.dumps(emb_512690, ensure_ascii=False)
        self.assertIn("512690", emb_512690["title"])
        self.assertIn("¥0.428", txt_512690)
        self.assertNotIn("$", txt_512690)

        # 2. 亚太指数中文名 (/check 科创50 & /check 恒生科技) -> pt 单位，零 $
        self.assertEqual(bot.resolve_ticker("科创50", snapshot), "000688")
        self.assertEqual(bot.resolve_ticker("恒生科技", snapshot), "HSTECH")
        for q in ("科创50", "恒生科技"):
            emb_idx = bot.build_check_embed_dict(q, snapshot)
            txt_idx = json.dumps(emb_idx, ensure_ascii=False)
            self.assertIn("pt", txt_idx)
            self.assertNotIn("$", txt_idx)

        # 3. 美股新增 KSTR -> USD $ 单位
        self.assertEqual(bot.resolve_ticker("KSTR", snapshot), "KSTR")
        emb_kstr = bot.build_check_embed_dict("KSTR", snapshot)
        txt_kstr = json.dumps(emb_kstr, ensure_ascii=False)
        self.assertIn("$16.87", txt_kstr)

    def test_terminal_html_apac_partition_and_isolation(self):
        mock_apac = _build_mock_apac_items()
        apac_doc = {
            "items": mock_apac,
            "apac_chip_sentiment": aaf.compute_apac_chip_sentiment(mock_apac),
        }
        macro_sample = {
            "QQQ": {"name": "纳指100", "cat": "指数", "last": 500.0, "pct_change": 0.5, "ma50": 490.0, "bias": 2.0, "atr": 5.0, "rsi": 55.0, "status": "多头", "sub": "核心"},
            "KSTR": {"name": "中国科技半导体ETF", "cat": "半导体", "last": 16.87, "pct_change": -1.2, "ma50": 17.0, "bias": -0.7, "atr": 0.6, "rsi": 45.0, "status": "观望", "sub": "科创50美股"},
        }
        stocks_sample = [
            {"ticker": "NVDA", "name": "英伟达", "cat": "芯片算力", "last": 140.0, "pct_change": 1.0, "ma50": 135.0, "bias": 3.7, "atr": 4.2, "rsi": 58.0, "signal": "多头", "sub": "GPU"},
        ]
        html = cft.build_futu_terminal_html(
            stocks_sample,
            macro_sample,
            {"weather": "阴天防守", "tactics": "轻仓"},
            sync_side_effects=False,
            apac_doc=apac_doc,
        )
        self.assertIn('id="btnApacAshare"', html)
        self.assertIn("🇨🇳 A股/亚太 (16)", html)
        self.assertIn("const APAC_ASHARE_DATA =", html)
        self.assertIn("renderNativeAssetChart", html)
        self.assertIn('"512690"', html)
        self.assertIn('"KSTR"', html)
        # 首屏默认美股总数严格只计 US stocks + macro (1 + 2 = 3)，绝不混入 16 只亚太/A 股
        self.assertIn("全部 (3)", html)


if __name__ == "__main__":
    unittest.main()
