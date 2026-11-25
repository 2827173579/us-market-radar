# -*- coding: utf-8 -*-
"""
Binance 美股实时报价主源与 V5.5.1 延展时段宽点差护栏隔离单元测试 (test_binance_equity_feed.py)
全部采用 Mock HTTP 与 tempfile.TemporaryDirectory()，零外网调用、零生产账本触碰。
"""

import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace

import pandas as pd

import binance_equity_feed as bn
import copy_trade_engine as cte
import copy_trade_kol_engine as kol


def _et(y: int, m: int, d: int, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=cte._NY_TZ)


class _FakeResp:
    def __init__(self, status_code: int, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class TestBinanceEquityFeed(unittest.TestCase):
    def setUp(self):
        bn.reset_rate_limit_cooldown()

    def tearDown(self):
        bn.reset_rate_limit_cooldown()

    def test_01_valid_l1_book_mid_and_spread_bps_and_exclusions(self):
        """验证正常 L1 盘口 mid / spread_bps 精确解析，且非美股/指数严格排除。"""
        self.assertTrue(bn.is_eligible_us_equity("NVDA"))
        self.assertTrue(bn.is_eligible_us_equity("SOXL"))
        for bad in ("BTC-USD", "XAUUSD", "KOSPI", "^VIX", "^VIX3M", "^VXN", "^VVIX", "^TNX", "NQ=F"):
            self.assertFalse(bn.is_eligible_us_equity(bad), f"Should exclude {bad}")

        mock_books = {
            "NVDA": {"symbol": "NVDA", "bidPrice": "149.49", "askPrice": "149.54", "bidSize": "200", "askSize": "180"},
            "SPY": {"symbol": "SPY", "bidPrice": "711.98", "askPrice": "712.06", "bidSize": "500", "askSize": "450"},
        }

        def fake_get(url, params=None, headers=None, timeout=None):
            sym = (params or {}).get("symbol")
            self.assertEqual((headers or {}).get("X-MBX-APIKEY"), "MOCK_KEY_ONLY")
            self.assertLessEqual(timeout, 3.0)
            return _FakeResp(200, mock_books[sym])

        quotes, stats = bn.fetch_binance_equity_quotes(
            ["NVDA", "SPY", "BTC-USD", "XAUUSD", "KOSPI", "^VIX"],
            max_workers=5,
            timeout=3.0,
            api_key="MOCK_KEY_ONLY",
            http_get_fn=fake_get,
        )
        self.assertEqual(stats["hit"], 2)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["fallback_count"], 0)
        self.assertAlmostEqual(quotes["NVDA"]["price"], 149.515, places=3)
        self.assertAlmostEqual(quotes["NVDA"]["spread_bps"], round((0.05 / 149.515) * 10000.0, 2), places=2)
        self.assertAlmostEqual(quotes["SPY"]["price"], 712.02, places=2)
        self.assertAlmostEqual(quotes["SPY"]["spread_bps"], round((0.08 / 712.02) * 10000.0, 2), places=2)
        self.assertNotIn("BTC-USD", quotes)
        self.assertNotIn("^VIX", quotes)

    def test_02_dirty_book_timeout_missing_key_and_429_cooldown_fallback(self):
        """验证未配置 Key、脏盘口 (bid<=0 / ask<bid)、超时与 429 冷却退避全部自动回退。"""
        # 1) 缺失 API Key -> 零请求直接回退
        called = {"n": 0}
        q_nokey, st_nokey = bn.fetch_binance_equity_quotes(
            ["NVDA", "QQQ"], api_key="",
            http_get_fn=lambda *a, **k: called.__setitem__("n", called["n"] + 1),
        )
        self.assertEqual(q_nokey, {})
        self.assertEqual(called["n"], 0)
        self.assertFalse(st_nokey["enabled"])

        # 2) 脏盘口与超时自动剔除，仅保留干净报价
        def fake_mixed(url, params=None, headers=None, timeout=None):
            sym = (params or {}).get("symbol")
            if sym == "NVDA":
                return _FakeResp(200, {"bidPrice": "150.00", "askPrice": "150.10"})
            if sym == "SOXL":
                return _FakeResp(200, {"bidPrice": "0", "askPrice": "30.00"})          # bid <= 0 脏盘口
            if sym == "SOXS":
                return _FakeResp(200, {"bidPrice": "68.00", "askPrice": "67.50"})      # ask < bid 倒挂脏盘口
            if sym == "MU":
                raise TimeoutError("simulated 3s timeout")
            return _FakeResp(500, {"code": -1, "msg": "server error"})

        q_mix, st_mix = bn.fetch_binance_equity_quotes(
            ["NVDA", "SOXL", "SOXS", "MU", "AMD"],
            max_workers=4,
            api_key="MOCK_KEY",
            http_get_fn=fake_mixed,
        )
        self.assertEqual(list(q_mix.keys()), ["NVDA"])
        self.assertEqual(st_mix["fallback_count"], 4)
        self.assertEqual(st_mix["dirty_count"], 2)

        # 3) HTTP 429 触发冷却退避 -> 后续调用零外网请求直接回退
        def fake_429(url, params=None, headers=None, timeout=None):
            return _FakeResp(429, {"code": -1003, "msg": "Too many requests"}, headers={"Retry-After": "120"})

        q_rl, st_rl = bn.fetch_binance_equity_quotes(["NVDA"], api_key="MOCK_KEY", http_get_fn=fake_429)
        self.assertEqual(q_rl, {})
        self.assertTrue(st_rl["rate_limited"])

        after_calls = {"n": 0}
        q_cd, st_cd = bn.fetch_binance_equity_quotes(
            ["NVDA", "QQQ"],
            api_key="MOCK_KEY",
            http_get_fn=lambda *a, **k: after_calls.__setitem__("n", after_calls["n"] + 1),
        )
        self.assertEqual(q_cd, {})
        self.assertEqual(after_calls["n"], 0)
        self.assertEqual(st_cd["skipped_reason"], "rate_limit_cooldown")

    def test_03_build_realtime_quote_preserves_prev_close(self):
        """验证 build_realtime_quote_from_binance 保留历史日线 prev_close 口径。"""
        idx = pd.to_datetime(["2026-11-23", "2026-11-24"])
        df = pd.DataFrame({"Close": [140.0, 145.0]}, index=idx)
        bq = {"price": 150.0, "bid": 149.95, "ask": 150.05, "spread_bps": 6.67}
        rq = bn.build_realtime_quote_from_binance("NVDA", bq, df_hist=df, ny_today=datetime(2026, 11, 25).date())
        self.assertAlmostEqual(rq["last"], 150.0, places=2)
        self.assertAlmostEqual(rq["prev_close"], 145.0, places=2)
        self.assertAlmostEqual(rq["change"], 5.0, places=2)
        self.assertAlmostEqual(rq["spread_bps"], 6.67, places=2)

    def test_04_extended_hours_wide_spread_guard_over_60bps(self):
        """验证 V5.5.1 延展时段 spread_bps > 60.0 时跳过止损判定：不误杀、不递增计数，也不清零已有真实破位计数。"""
        with tempfile.TemporaryDirectory() as td:
            lp = os.path.join(td, "kol_spread.json")
            ai = {"market_regime": "BULL_EXPANSION", "by_ticker": {
                "VIXY": {"calibrated_win_rate_pct": 65.0, "calibrated_short_win_rate_pct": 20.0},
            }}
            q_open = {
                "QQQ": {"active_price": 500.0, "pct_change": -1.0},
                "VIXY": {"active_price": 24.0, "prev_close": 23.8, "pct_change": 0.84, "day_low": 23.4, "vwap": 23.9, "vwap_diff_pct": 0.4, "rsi_5m": 58.0, "rvol_5m": 1.8},
            }
            # 1) 盘中正常开 1 笔 VIXY
            r0 = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_open, daily_sr_anchors={"VIXY": {"s1": 23.4, "r1": 23.95}},
                market_regime="BULL_EXPANSION", market_session="REGULAR",
                now_et=_et(2026, 11, 24, 14, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r0["opened_now"]), 1)
            sl_px = r0["opened_now"][0]["sl_price"]

            # 2) 夜盘出现薄盘口宽点差 (119.6 bps > 60.0) 假击穿 -> 跳过判定，count 保持 0 且不平仓
            q_wide_breach = {"VIXY": {"ext_price": sl_px - 0.50, "spread_bps": 119.6, "has_realtime": True}}
            r_wide1 = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_wide_breach, market_regime="BULL_EXPANSION",
                market_session="OVERNIGHT", now_et=_et(2026, 11, 25, 3, 0, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_wide1["closed_now"]), 0)
            self.assertEqual(r_wide1["ledger"]["open_positions"][0]["ext_sl_breach_count"], 0)
            # 参考价仍正常更新展示
            self.assertAlmostEqual(r_wide1["ledger"]["open_positions"][0]["last_price"], round(sl_px - 0.50, 2), places=2)

            # 3) 盘前正常点差 (15.0 bps <= 60.0) 第 1 轮真实击穿 -> count 计为 1，暂不平仓
            q_tight_breach = {"VIXY": {"ext_price": sl_px - 0.40, "spread_bps": 15.0, "has_realtime": True}}
            r_t1 = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_tight_breach, market_regime="BULL_EXPANSION",
                market_session="PRE_MARKET", now_et=_et(2026, 11, 25, 7, 10, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_t1["closed_now"]), 0)
            self.assertEqual(r_t1["ledger"]["open_positions"][0]["ext_sl_breach_count"], 1)

            # 4) 间隔 70 秒后出现一次宽点差 (85.0 bps > 60.0) 抖动 -> 跳过判定，既不平仓也不清零 count=1！
            q_mid_wide = {"VIXY": {"ext_price": sl_px - 0.60, "spread_bps": 85.0, "has_realtime": True}}
            r_mid = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_mid_wide, market_regime="BULL_EXPANSION",
                market_session="PRE_MARKET", now_et=_et(2026, 11, 25, 7, 11, 10), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_mid["closed_now"]), 0)
            self.assertEqual(r_mid["ledger"]["open_positions"][0]["ext_sl_breach_count"], 1)

            # 5) 正常点差 (12.0 bps <= 60.0) 第 2 轮确认持续击穿 (>= 60s) -> 正式触发 SL_HIT 平仓！
            q_tight2 = {"VIXY": {"ext_price": sl_px - 0.45, "spread_bps": 12.0, "has_realtime": True}}
            r_t2 = kol.run_kol_copy_trade_cycle(
                ai_predictions=ai, live_quotes=q_tight2, market_regime="BULL_EXPANSION",
                market_session="PRE_MARKET", now_et=_et(2026, 11, 25, 7, 12, 0), ledger_path=lp, dry_run=False,
            )
            self.assertEqual(len(r_t2["closed_now"]), 1)
            self.assertEqual(r_t2["closed_now"][0]["outcome"], "SL_HIT")
            self.assertTrue(r_t2["closed_now"][0]["ext_confirmed_close"])


if __name__ == "__main__":
    unittest.main()
