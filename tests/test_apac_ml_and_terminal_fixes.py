# -*- coding: utf-8 -*-
"""
亚太/A 股独立 AI 胜率引擎 + 右侧指标拉齐 + KOSPI 视图交叉归队 + 4 处网页显示 Bug 隔离单测
"""

from __future__ import annotations

import json
import re
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import apac_ashare_feed as aaf
import apac_ml_predictor as amp
import create_futu_terminal as cft
import discord_radar_bot as drb
import integrated_market_radar as imr
import us_market_calendar as umc

ET = ZoneInfo("America/New_York")
CST = ZoneInfo("Asia/Shanghai")


def _make_synth_daily(n_days: int, start: str = "2025-01-02", drift: float = 0.0005, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=n_days)
    rets = rng.normal(drift, 0.015, size=n_days)
    close = 1.2 * np.cumprod(1.0 + rets)
    high = close * (1.0 + rng.uniform(0.003, 0.018, size=n_days))
    low = close * (1.0 - rng.uniform(0.003, 0.018, size=n_days))
    op = close * (1.0 + rng.normal(0.0, 0.005, size=n_days))
    vol = rng.uniform(5e6, 2e7, size=n_days)
    return pd.DataFrame(
        {"Open": op, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=dates,
    )


class TestApacMlAndTerminalFixes(unittest.TestCase):
    def test_01_strict_causal_us_lag_zero_lookahead(self):
        """严格验证 us_date < apac_date，且免疫 Pandas datetime64[us] vs datetime64[ns] 精度差异。"""
        us_idx = pd.to_datetime(["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26"]).as_unit("ns")
        us_df = pd.DataFrame({
            "us_soxx_1d": [0.01, -0.02, 0.03, 0.09],
            "us_qqq_1d": [0.005, -0.01, 0.015, 0.08],
            "us_vix_1d": [-0.02, 0.05, -0.04, -0.20],
            "us_vix_lvl": [0.1, 0.2, 0.0, -0.5],
        }, index=us_idx)

        for unit in ("ns", "us"):
            apac_idx = pd.to_datetime(["2026-11-24", "2026-11-25", "2026-11-26"]).as_unit(unit)
            aligned, matched = amp.align_us_macro_strict_lag(apac_idx, us_df)
            self.assertEqual(matched, ["2026-11-23", "2026-11-24", "2026-11-25"])
            for d_ap, d_us in zip(["2026-11-24", "2026-11-25", "2026-11-26"], matched):
                self.assertLess(d_us, d_ap, "严禁出现 us_date >= apac_date 未来函数")
            # 2026-11-26 的 0.09 未来数据绝不可出现在任何行
            self.assertAlmostEqual(float(aligned.iloc[-1]["us_soxx_1d"]), 0.03, places=6)

    def test_02_short_history_etf_policy_and_capped_us_macro(self):
        """验证次新样本策略：>=60日可推理、>=120日入训、<250日打『次新样本』，且外盘权重硬封顶。"""
        us_idx = pd.bdate_range("2024-06-01", periods=500)
        rng = np.random.default_rng(42)
        us_df = pd.DataFrame({
            "us_soxx_1d": rng.normal(0.001, 0.02, size=len(us_idx)),
            "us_qqq_1d": rng.normal(0.001, 0.012, size=len(us_idx)),
            "us_vix_1d": rng.normal(0.0, 0.06, size=len(us_idx)),
            "us_vix_lvl": rng.normal(0.0, 0.4, size=len(us_idx)),
        }, index=us_idx)

        hist_map = {
            "512530": _make_synth_daily(360, start="2025-01-02", drift=0.0008, seed=1),  # 成熟样本
            "588200": _make_synth_daily(360, start="2025-01-02", drift=-0.0006, seed=2), # 成熟样本
            "000688": _make_synth_daily(360, start="2025-01-02", drift=0.0002, seed=3),  # 基准
            "399006": _make_synth_daily(360, start="2025-01-02", drift=0.0001, seed=4),  # 基准
            "588170": _make_synth_daily(180, start="2025-09-10", drift=0.0012, seed=5),  # >=120 入训且 <250 次新
            "159530": _make_synth_daily(80, start="2026-02-01", drift=-0.0010, seed=6),  # >=60 推理但 <120 不入训且次新
            "159562": _make_synth_daily(40, start="2026-04-01", drift=0.0010, seed=7),   # <60 不推理
        }

        res = amp.train_and_predict_apac_ml(hist_map, us_df)
        by_tk = res.get("by_ticker") or {}
        self.assertIn("512530", by_tk)
        self.assertIn("588170", by_tk)
        self.assertIn("159530", by_tk)
        self.assertNotIn("159562", by_tk)

        self.assertTrue(by_tk["512530"]["included_in_train"])
        self.assertFalse(by_tk["512530"]["is_short_sample"])
        self.assertEqual(by_tk["512530"]["sample_shrinkage_tag"], "")

        self.assertTrue(by_tk["588170"]["included_in_train"])
        self.assertTrue(by_tk["588170"]["is_short_sample"])
        self.assertEqual(by_tk["588170"]["sample_shrinkage_tag"], "次新样本")

        self.assertFalse(by_tk["159530"]["included_in_train"])
        self.assertTrue(by_tk["159530"]["is_short_sample"])
        self.assertEqual(by_tk["159530"]["sample_shrinkage_tag"], "次新样本")

        for tk, r in by_tk.items():
            self.assertLess(r["us_lead_date"], r["as_of_date"])
            self.assertLessEqual(abs(r["us_lead_adj_pct"]), amp.MAX_US_MACRO_ADJ_PCT + 1e-6)
            self.assertLessEqual(r["calibrated_win_rate_pct"] + r["calibrated_short_win_rate_pct"], 100.1)

    def test_03_cold_start_and_sleep_schedule(self):
        """验证冷启动立即触发计算 + 收盘后每日仅重算一次 + 休眠期 0 HTTP。"""
        need_cold, reason_cold = amp.should_recompute_apac_ml({}, now_dt=datetime(2026, 11, 26, 22, 0, tzinfo=CST))
        self.assertTrue(need_cold)
        self.assertEqual(reason_cold, "COLD_START_MISSING")

        full_fake = {
            "fetched_epoch": datetime(2026, 11, 26, 16, 20, tzinfo=CST).timestamp(),
            "computed_after_close_date": "2026-11-26",
            "by_ticker": {m["ticker"]: {"calibrated_win_rate_pct": 55.0} for m in aaf.ALL_APAC_ASHARE_TARGETS},
        }
        need_sleep, reason_sleep = amp.should_recompute_apac_ml(full_fake, now_dt=datetime(2026, 11, 26, 22, 30, tzinfo=CST))
        self.assertFalse(need_sleep)
        self.assertEqual(reason_sleep, "SLEEP_CACHE")

        # 次日收盘后 16:20 CST -> 触发今日一次增量重算
        need_next, reason_next = amp.should_recompute_apac_ml(full_fake, now_dt=datetime(2026, 11, 27, 16, 20, tzinfo=CST))
        self.assertTrue(need_next)
        self.assertEqual(reason_next, "DAILY_POST_CLOSE_REFRESH")

    def test_04_5m_lunch_gap_and_outlier_volume_sanitizer_and_vwap(self):
        """验证 Bug 2 & 3：剔除 11:35~12:55 午休平盘线 + 清洗 13:00/14:55 累计巨量异常柱 + 真实 VWAP/RSI5m/RVOL5m。"""
        bars = [
            ["09:30", 1.170, 1.172, 1.154, 1.155, 45000000.0],
            ["09:35", 1.153, 1.155, 1.140, 1.145, 22000000.0],
            ["09:40", 1.145, 1.148, 1.138, 1.140, 18000000.0],
            ["11:20", 1.136, 1.137, 1.130, 1.131, 15000000.0],
            ["11:25", 1.130, 1.131, 1.128, 1.130, 14000000.0],
            ["11:30", 1.130, 1.130, 1.130, 1.130, 0.0],
            ["11:35", 1.130, 1.130, 1.130, 1.130, 0.0],
            ["12:00", 1.130, 1.130, 1.130, 1.130, 0.0],
            ["12:55", 1.130, 1.130, 1.130, 1.130, 0.0],
            ["13:00", 1.131, 1.145, 1.131, 1.133, 655743704.0],  # 累计异常巨量 > 40x
            ["13:05", 1.133, 1.139, 1.131, 1.134, 16000000.0],
            ["14:45", 1.119, 1.123, 1.118, 1.119, 17000000.0],
            ["14:50", 1.120, 1.123, 1.119, 1.122, 18000000.0],
            ["14:55", 1.123, 1.123, 1.122, 1.123, 1057049404.0], # 尾盘累计异常巨量 > 60x
        ]
        cleaned, capped = aaf.sanitize_apac_5m_bars(bars, market="CN", decimals=3)
        self.assertTrue(capped)
        times = [r[0] for r in cleaned]
        self.assertNotIn("11:35", times)
        self.assertNotIn("12:00", times)
        self.assertNotIn("12:55", times)
        # 13:00 与 14:55 的异常亿级累计巨量必须已被平滑，不再畸变柱图与 VWAP
        self.assertLess(max(r[5] for r in cleaned), 1e8)

        micro = aaf.compute_apac_5m_microstructure(cleaned, spot_price=1.123, decimals=3)
        self.assertTrue(micro["has_5m_intraday"])
        self.assertIsNotNone(micro["vwap"])
        self.assertNotAlmostEqual(micro["vwap"], 1.123, places=3)
        self.assertLess(micro["vwap_diff_pct"], 0.0)
        self.assertIsNotNone(micro["rsi_5m"])
        self.assertIsNotNone(micro["rvol_5m"])

    def test_05_ema20_distinct_from_ma50_and_s1_anchored_stop(self):
        """验证 Bug 1 & 2：EMA20 与 MA50 独立区分，且日内防守止损锚定 S1 附近而非 -17% S2。"""
        # 构造先横盘后缓跌序列，使 EMA20 明显低于 MA50
        rows = []
        px = 1.30
        for i in range(65):
            if i >= 45:
                px *= 0.992
            rows.append([f"2026-09-{i+1:02d}", px, px * 1.01, px * 0.99, px, 1e7 + i * 1e5])
        tech = aaf._compute_technicals_from_daily(rows, spot_price=px, decimals=3)
        self.assertNotEqual(tech["ema20"], tech["ma50"])
        self.assertLess(tech["ema20"], tech["ma50"])
        self.assertGreater(tech["poc"], 0)
        self.assertGreater(tech["fib_high"], tech["fib_low"])
        # 防守止损必须锚定在现价下方 1.5% ~ 8.5% 合理短线范围内
        sl_dist_pct = (tech["entry_stop_loss"] - tech["last"]) / tech["last"] * 100.0
        self.assertLessEqual(sl_dist_pct, -1.4)
        self.assertGreaterEqual(sl_dist_pct, -8.5)

    def test_06_us_holiday_classification_thanksgiving_2026_11_26(self):
        """验证 Bug 4：感恩节 2026-11-26 全天返回 HOLIDAY (🏖️ 节假日休市)，不再误判 PRE_MARKET。"""
        for hh, mm in ((4, 30), (6, 0), (10, 0), (15, 0)):
            dt = datetime(2026, 11, 26, hh, mm, tzinfo=ET)
            code, label = umc.classify_us_market_session(dt)
            self.assertEqual(code, "HOLIDAY")
            self.assertIn("节假日休市", label)
            r_code, r_label = imr.get_us_market_session(dt)
            self.assertEqual(r_code, "HOLIDAY")
            self.assertIn("节假日休市", r_label)

    def test_07_kospi_frontend_cross_filter_without_cloning_and_holiday_badge(self):
        """验证 KOSPI 仅视图交叉归队（APAC_ASHARE_DATA 绝不克隆 KOSPI），总数显示 17，且顶部显示节假日休市。"""
        macro = {
            "QQQ": {"name": "纳指100", "last": 500.0, "pct_change": 0.5, "ma50": 490.0},
            "KOSPI": {"name": "韩国综指", "last": 2550.0, "pct_change": 0.8, "ma50": 2500.0, "price_unit": "pts", "is_index": True},
        }
        stocks = [{"ticker": "NVDA", "name": "英伟达", "last": 135.0, "pct_change": 1.2, "ma50": 130.0}]
        apac_items = {
            m["ticker"]: {
                "ticker": m["ticker"], "name": m["name"], "kind": m["kind"], "price_unit": m["price_unit"],
                "last": 1.124 if m["price_unit"] == "cny" else 3500.0, "pct_change": -0.5,
                "ema20": 1.226, "ma20": 1.255, "ma50": 1.251, "s1": 1.038, "s2": 0.927, "r1": 1.177, "r2": 1.251,
                "entry_stop_loss": 1.034, "poc": 1.316, "rs_20d": -7.89, "vwap": 1.133, "vwap_diff_pct": -0.83,
            }
            for m in aaf.ALL_APAC_ASHARE_TARGETS
        }
        apac_ml = {
            "by_ticker": {
                "159530": {"calibrated_win_rate_pct": 41.2, "calibrated_short_win_rate_pct": 58.8, "direction_score": -17.6, "signal_cn": "🔴 偏空承压"}
            }
        }
        html = cft.build_futu_terminal_html(
            stocks_data=stocks,
            macro_data=macro,
            weather_info={"weather": "阴天防守"},
            session_code="HOLIDAY",
            session_name="节假日休市",
            apac_doc={"items": apac_items},
            apac_ml_doc=apac_ml,
        )
        self.assertIn("🏖️ 节假日休市", html)
        self.assertIn("🇨🇳 A股/亚太 (17)", html)
        self.assertIn("全部 (3)", html)  # 1 stock + 2 macro，绝不因 16 只亚太膨胀
        # 确认 APAC_ASHARE_DATA JSON 中不含克隆的 KOSPI 节点
        m_json = re.search(r"const APAC_ASHARE_DATA = (\{.*?\});", html)
        self.assertIsNotNone(m_json)
        parsed_apac = json.loads(m_json.group(1))
        self.assertEqual(len(parsed_apac), 16)
        self.assertNotIn("KOSPI", parsed_apac)
        self.assertIn("const APAC_ML_PREDICTIONS =", html)

    def test_08_discord_check_apac_card_includes_ai_rs_poc_zero_dollar(self):
        """验证 Discord /check 亚太卡片包含独立 AI 5日胜率、20日超额RS、POC，且全卡零 `$`。"""
        snap = {
            "updated_at_et": "2026-11-26 06:00:00 ET",
            "session_code": "HOLIDAY",
            "session_name": "节假日休市",
            "apac_ashare_meta": {"updated_at_cst": "2026-11-26 15:10:00 CST"},
            "apac_ashare_items": {
                "159530": {
                    "ticker": "159530", "name": "机器人ETF易方达", "cat": "A股科技", "sub": "人形机器人核心",
                    "kind": "ASHARE_ETF", "price_unit": "cny", "last": 1.124, "pct_change": -1.2,
                    "ema20": 1.226, "ma20": 1.255, "ma50": 1.251, "bias_ma20": -10.44, "rsi": 35.2,
                    "s1": 1.038, "s2": 0.927, "r1": 1.177, "r2": 1.251, "entry_stop_loss": 1.034,
                    "poc": 1.316, "rs_20d": -7.89, "vwap": 1.133, "vwap_diff_pct": -0.83,
                    "ai_long_win_rate_pct": 38.5, "ai_short_win_rate_pct": 61.5, "ai_direction_score": -23.0,
                    "ai_signal_cn": "🔴 偏空承压", "ai_sample_badge": "", "ai_us_lead_date": "2026-11-25",
                }
            },
            "tickers": {"NVDA": {"ticker": "NVDA", "name": "英伟达", "last": 135.0}},
        }
        emb = drb.build_check_embed_dict("159530", snap)
        blob = json.dumps(emb, ensure_ascii=False)
        self.assertIn("亚太独立 AI 胜率", blob)
        self.assertIn("38.5%", blob)
        self.assertIn("20日超额RS", blob)
        self.assertIn("POC", blob)
        self.assertIn("EMA20", blob)
        self.assertIn("¥1.124", blob)
        self.assertNotIn("$", blob)


if __name__ == "__main__":
    unittest.main()
