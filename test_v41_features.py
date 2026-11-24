#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_v41_features.py - V4.1 升级专项离线单元测试（全 Mock 网络，零真实外发）

覆盖范围：
1. 策略 A（恐慌出清抄底窗）盘前/夜盘/盘后/周末防误触发锁 + TTL 缓存跨开盘切换 + 09:30 ET 常规盘中正常解锁与去重；
2. 非 REGULAR 时段 VWAP/5m RSI 标注 `(上日参考 · 09:30重置)` 与 空头占优/对冲卡片 `上方压力 (R1)`/`下方支撑 (S1)` 防看反切换；
3. SNDK 1 拆 8 盘前拆股断层对齐（last=320 / ext=37.34 -> -88.33% 修复为 -6.65%）与无法匹配的极端毛刺抑制；
4. 恐慌退潮「抗跌领涨 Top 5」筛选器：严格排除 SQQQ/SOXS/VIXY/TLT/GLD/XAUUSD/BTC-USD 及暴跌股，按 VWAP + 20日RS + 超额SPY 排序；
5. OPEX / 四巫日 (Quad Witching) 自动日历计算（含 2026-11-20 月度 OPEX、2026-12-18 四巫日、T-1 预警及横幅渲染）；
6. 宏观跨市场流动性监测（HYG/IEF · DX-Y.NYB · JPY=X）、断网降级容错、79 标的池纯净性校验及 Discord 模板裸 `---` 清理。
"""

import json
import os
import re
import sys
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import create_futu_terminal as cft
import integrated_market_radar as imr
import jin10_news_sentinel as jn
import shared_alert_state
import vix_regime_engine as vre


class _DummyHttpResp:
    def __init__(self, status=204):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


def _make_series(values, start="2026-11-01"):
    idx = pd.bdate_range(start=start, periods=len(values))
    return pd.Series(list(values), index=idx, dtype=float)


class TestV41Features(unittest.TestCase):
    CLAW_URL = "https://example.invalid/webhook/claw"
    DEF_URL = "https://example.invalid/webhook/defensive"

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._orig_shared_file = shared_alert_state.SHARED_FILE
        shared_alert_state.SHARED_FILE = os.path.join(self._tmp_dir.name, "shared_alert_dedupe.json")
        self.env_patch = patch.dict(
            os.environ,
            {
                "DISCORD_CLAW_WEBHOOK_URL": self.CLAW_URL,
                "DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL": self.DEF_URL,
                "DISCORD_DEFENSIVE_WEBHOOK_URL": "",
                "RADAR_DISABLE_EXTERNAL_ALERTS": "",
            },
            clear=False,
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        shared_alert_state.SHARED_FILE = self._orig_shared_file
        self._tmp_dir.cleanup()

    def _capture_urlopen(self):
        captured = []

        def _fake_urlopen(req, timeout=10):
            body = json.loads(req.data.decode("utf-8"))
            captured.append({"url": req.full_url, "payload": body})
            return _DummyHttpResp(204)

        return captured, _fake_urlopen

    # 1. 策略 A 盘前/夜盘防误触发锁 + REGULAR 解锁
    def test_01_strategy_a_premarket_lock_and_regular_unlock(self):
        # 10日曾达极端恐慌(VIX 32.5 / ratio 1.08), 今日 VIX=29.0, VIX3M=30.94 -> ratio=0.937 (<0.97), dd=10.8% (>=5%), VIX 未反弹
        vix_vals = [23.0, 26.0, 32.5, 31.8, 30.2, 29.0]
        vix3m_vals = [23.5, 25.5, 30.1, 30.5, 30.8, 30.94]
        vvix_vals = [100.0, 114.0, 125.0, 118.0, 112.0, 108.0]
        tnx_vals = [4.35, 4.40, 4.45, 4.42, 4.41, 4.40]

        vix_s = _make_series(vix_vals)
        vix3m_s = _make_series(vix3m_vals)
        vvix_s = _make_series(vvix_vals)
        tnx_s = _make_series(tnx_vals)

        for non_rth in ("PRE_MARKET", "OVERNIGHT", "POST_MARKET", "WEEKEND"):
            snap = vre.compute_vix_regime_from_series(
                vix_s, vix3m_s, vvix_s, tnx_s, session_code=non_rth
            )
            self.assertFalse(snap["vix_bottom_signal"], f"{non_rth} 严禁亮绿灯")
            self.assertTrue(snap["vix_bottom_premarket_preview"], f"{non_rth} 应进入盘前预热态")
            self.assertTrue(snap["raw_bottom_signal"])
            self.assertEqual(snap["vix_state"], "BOTTOM_PREMARKET_PREVIEW")
            self.assertEqual(snap["pill_level"], "warn")
            self.assertIn("盘前预热", snap["pill_text"])
            self.assertIn("待09:30确认", snap["pill_text"])

        snap_pm = vre.compute_vix_regime_from_series(
            vix_s, vix3m_s, vvix_s, tnx_s, session_code="PRE_MARKET"
        )
        captured, fake_open = self._capture_urlopen()
        with patch("urllib.request.urlopen", side_effect=fake_open):
            res_pm = imr.dispatch_vix_regime_discord_alerts(
                snap_pm, session_code="PRE_MARKET", dedupe_day="2026-11-20", now_str="2026-11-20 08:15:00"
            )
            # 即使上游误传 raw bottom_signal=True 且状态标为 WASHOUT_BOTTOM_WINDOW，在 PRE_MARKET 也必须被二道锁拦下
            res_pm_forced = imr.dispatch_vix_regime_discord_alerts(
                {**snap_pm, "vix_bottom_signal": True, "vix_bottom_premarket_preview": False, "vix_state": "WASHOUT_BOTTOM_WINDOW"},
                session_code="PRE_MARKET",
                dedupe_day="2026-11-20",
                now_str="2026-11-20 08:20:00",
            )

        self.assertFalse(res_pm["vix_bottom_sent"])
        self.assertTrue(res_pm["skipped_premarket_bottom"])
        self.assertFalse(res_pm_forced["vix_bottom_sent"])
        self.assertTrue(res_pm_forced["skipped_premarket_bottom"])
        self.assertEqual(len(captured), 0, "PRE_MARKET 严禁向 Discord 推送策略 A 绿灯")
        self.assertFalse(
            shared_alert_state.is_sent("2026-11-20:VIX_REGIME:WASHOUT_BOTTOM_WINDOW"),
            "PRE_MARKET 严禁消耗当日 Discord 去重锁",
        )

        # 缓存跨开盘切换：08:20 PRE_MARKET 写入的 TTL 缓存，到 09:35 REGULAR 读取应无网络无缝激活抄底窗
        cache_p = os.path.join(self._tmp_dir.name, "vix_regime_cache.json")
        vre._write_cached_snapshot(cache_p, snap_pm)
        snap_cached_rth = vre.get_vix_regime_snapshot(
            force_refresh=False, cache_path=cache_p, ttl_seconds=180, allow_network=False, session_code="REGULAR"
        )
        self.assertTrue(snap_cached_rth["vix_bottom_signal"])
        self.assertFalse(snap_cached_rth["vix_bottom_premarket_preview"])
        self.assertEqual(snap_cached_rth["vix_state"], "WASHOUT_BOTTOM_WINDOW")
        self.assertEqual(snap_cached_rth["pill_level"], "bottom")

        # 09:35 REGULAR 常规盘中正常推送并写入当日去重锁
        with patch("urllib.request.urlopen", side_effect=fake_open):
            res_rth_1 = imr.dispatch_vix_regime_discord_alerts(
                snap_cached_rth, session_code="REGULAR", dedupe_day="2026-11-20", now_str="2026-11-20 09:35:00"
            )
            res_rth_2 = imr.dispatch_vix_regime_discord_alerts(
                snap_cached_rth, session_code="REGULAR", dedupe_day="2026-11-20", now_str="2026-11-20 09:38:00"
            )

        self.assertTrue(res_rth_1["vix_bottom_sent"])
        self.assertFalse(res_rth_2["vix_bottom_sent"])
        self.assertEqual(len(captured), 1)
        self.assertTrue(shared_alert_state.is_sent("2026-11-20:VIX_REGIME:WASHOUT_BOTTOM_WINDOW"))

    # 2. 盘前旧 VWAP/5m RSI 标注 + 空头/对冲态卡片防看反文案
    def test_02_premarket_stale_vwap_label_and_bear_card_anti_inversion(self):
        mock_stock = {
            "ticker": "NVDA", "name": "英伟达", "sub": "AI算力绝对霸主", "cat": "芯片与半导体",
            "last": 135.0, "change": -2.5, "pct_change": -1.82, "active_price": 134.0,
            "ext_price": 134.0, "ext_pct": -0.74, "effective_pct": -0.74,
            "ma50": 140.0, "ema20": 138.0, "bias": -4.29, "atr": 4.5, "rsi": 42.0, "vol_ratio": 1.1,
            "fib_low": 130.0, "fib_high": 145.0, "is_realtime": True,
            "signal": "🔴 破位走弱", "session_tag": "盘前", "rs_20d": -2.5, "intraday_entry_ready": False,
            "vwap": 136.5, "has_5m_intraday": True, "rsi_5m": 44.0, "rvol_5m": 1.1,
        }
        html_pre = cft.build_futu_terminal_html(
            stocks_data=[mock_stock],
            macro_data={},
            weather_info={"weather": "⛅ 阴天防守", "tactics": "轻仓防守", "vix_regime": {"vix_state": "ELEVATED_CAUTION", "vix_spot": 22.0}},
            update_time_str="2026-11-20 08:30:00",
            has_realtime=True,
            target_watchlist=["NVDA"],
            terminal_notifications=[],
            session_code="PRE_MARKET",
            sync_side_effects=False,
            sync_dir=self._tmp_dir.name,
        )
        self.assertIn('const SESSION_CODE = "PRE_MARKET";', html_pre)
        self.assertIn("(上日参考 · 09:30重置)", html_pre)
        self.assertIn('id="pVwapLabel"', html_pre)
        self.assertIn('id="pRsi5mLabel"', html_pre)
        self.assertIn("上方压力 (R1)", html_pre)
        self.assertIn("下方支撑 (S1)", html_pre)
        self.assertIn("空头留意支撑区止盈 / 反抽压力位防守", html_pre)
        self.assertIn("建议买入反向ETF对冲", html_pre)

    # 3. SNDK 盘前拆股断层对齐 & 极端毛刺拦截
    def test_03_sndk_extended_quote_split_alignment(self):
        # 场景 A：SNDK 昨收未除权 320.0 vs 1拆8 盘前价 37.34（原本 -88.33% 假暴跌） -> 对齐昨收 40.0，盘前涨跌幅 -6.65%
        adj_last, adj_ep, chg, pct, healed, reason = imr.reconcile_extended_quote_with_split_guard(
            "SNDK", last_price=320.0, ext_price=37.34
        )
        self.assertTrue(healed)
        self.assertAlmostEqual(adj_last, 40.0, places=2)
        self.assertAlmostEqual(adj_ep, 37.34, places=2)
        self.assertAlmostEqual(pct, -6.65, places=1)
        self.assertIn("8x", reason)

        # 场景 B：本地历史已除权 (40.0) 且传入 split_meta，同时 stale intra_info["vwap"] 也被同步缩小 8 倍
        df_adj = pd.DataFrame({"Close": [41.0, 40.0]}, index=pd.bdate_range("2026-11-18", periods=2))
        intra_mock = {"vwap": 322.0, "rth_high": 328.0}
        adj_last_b, adj_ep_b, _, pct_b, healed_b, _ = imr.reconcile_extended_quote_with_split_guard(
            "SNDK", last_price=320.0, ext_price=37.34, df_hist=df_adj, intra_info=intra_mock
        )
        self.assertTrue(healed_b)
        self.assertAlmostEqual(adj_last_b, 40.0, places=2)
        self.assertAlmostEqual(pct_b, -6.65, places=1)
        self.assertAlmostEqual(intra_mock["vwap"], 40.25, places=2)

        # 场景 C：无法匹配拆股因子的极端断层（如 100 -> 4，比值 25x 超出标准比例容差）应直接拦截扩展报价
        _, adj_ep_bad, _, pct_bad, healed_bad, reason_bad = imr.reconcile_extended_quote_with_split_guard(
            "SNDK", last_price=100.0, ext_price=4.0
        )
        self.assertTrue(healed_bad)
        self.assertEqual(adj_ep_bad, 0.0)
        self.assertEqual(pct_bad, 0.0)
        self.assertEqual(reason_bad, "extreme_ext_cliff_suppressed")

        # 场景 D：正常波动（-8%）完全不改动
        adj_l_norm, adj_e_norm, _, pct_norm, healed_norm, _ = imr.reconcile_extended_quote_with_split_guard(
            "NVDA", last_price=100.0, ext_price=92.0
        )
        self.assertFalse(healed_norm)
        self.assertAlmostEqual(adj_l_norm, 100.0, places=2)
        self.assertAlmostEqual(adj_e_norm, 92.0, places=2)
        self.assertAlmostEqual(pct_norm, -8.0, places=2)

    # 4. 恐慌退潮·抗跌领涨 Top 5 筛选器排序与过滤逻辑
    def test_04_resilient_leaders_top5_ranking_and_exclusions(self):
        macro = {
            "SPY": {"ticker": "SPY", "name": "标普500", "cat": "大盘基准", "last": 580.0, "active_price": 580.0, "vwap": 579.0, "pct_change": -0.50, "rs_20d": 0.0},
            "SQQQ": {"ticker": "SQQQ", "name": "3倍做空纳指", "cat": "反向做空", "last": 15.0, "active_price": 15.0, "vwap": 14.0, "pct_change": 6.0, "rs_20d": 20.0, "is_inverse_hedge": True},
            "SOXS": {"ticker": "SOXS", "name": "3倍做空半导", "cat": "反向做空", "last": 25.0, "active_price": 25.0, "vwap": 24.0, "pct_change": 8.0, "rs_20d": 25.0, "is_inverse_hedge": True},
            "VIXY": {"ticker": "VIXY", "name": "恐慌ETF", "cat": "恐慌指数", "last": 40.0, "active_price": 40.0, "vwap": 38.0, "pct_change": 9.0, "rs_20d": 30.0, "is_inverse_hedge": True},
            "TLT": {"ticker": "TLT", "name": "长期美债", "cat": "避险美债", "last": 92.0, "active_price": 92.0, "vwap": 91.0, "pct_change": 1.2, "rs_20d": 6.0},
            "GLD": {"ticker": "GLD", "name": "黄金ETF", "cat": "大宗商品", "last": 240.0, "active_price": 240.0, "vwap": 239.0, "pct_change": 1.5, "rs_20d": 8.0},
        }
        stocks = [
            {"ticker": "XAUUSD", "name": "伦敦金", "cat": "大宗商品", "last": 2650.0, "active_price": 2650.0, "vwap": 2630.0, "pct_change": 2.0, "rs_20d": 15.0},
            {"ticker": "BTC-USD", "name": "比特币", "cat": "数字资产", "last": 90000.0, "active_price": 90000.0, "vwap": 89000.0, "pct_change": 3.0, "rs_20d": 18.0},
            {"ticker": "HALT1", "name": "暴跌股", "cat": "云与软件", "last": 50.0, "active_price": 50.0, "vwap": 49.0, "pct_change": -6.0, "rs_20d": 12.0, "is_crash_halt": True},
            {"ticker": "NVDA", "name": "英伟达", "cat": "芯片与半导体", "last": 145.0, "active_price": 145.0, "vwap": 142.0, "pct_change": 2.2, "rs_20d": 12.0},
            {"ticker": "AVGO", "name": "博通", "cat": "芯片与半导体", "last": 180.0, "active_price": 180.0, "vwap": 178.0, "pct_change": 1.5, "rs_20d": 10.0},
            {"ticker": "TSM", "name": "台积电", "cat": "芯片与半导体", "last": 195.0, "active_price": 195.0, "vwap": 194.0, "pct_change": 1.0, "rs_20d": 7.0},
            {"ticker": "MSFT", "name": "微软", "cat": "云与软件", "last": 420.0, "active_price": 420.0, "vwap": 419.0, "pct_change": 0.4, "rs_20d": 4.0},
            {"ticker": "AAPL", "name": "苹果", "cat": "终端硬件", "last": 228.0, "active_price": 228.0, "vwap": 227.5, "pct_change": 0.1, "rs_20d": 2.0},
            {"ticker": "AMD", "name": "超威", "cat": "芯片与半导体", "last": 135.0, "active_price": 135.0, "vwap": 137.0, "pct_change": -0.2, "rs_20d": 14.0},
        ]
        top5 = cft.compute_resilient_leaders_top5(stocks, macro_data=macro, session_code="REGULAR", top_n=5)
        self.assertEqual(len(top5), 5)
        tks = [x["ticker"] for x in top5]
        for forbidden in ("SQQQ", "SOXS", "VIXY", "TLT", "GLD", "XAUUSD", "BTC-USD", "HALT1"):
            self.assertNotIn(forbidden, tks)
        self.assertEqual(tks[0], "NVDA")
        self.assertNotIn("AMD", tks, "未站上当日 VWAP 的标的优先级低于站稳 VWAP 的前 5 只领涨龙头")
        self.assertTrue(all(x["above_vwap"] for x in top5))

        # 盘前模式回退正涨幅/强于SPY
        stocks_pm = [
            {**s, "ext_price": s["last"], "ext_pct": s["pct_change"], "vwap": s["last"] * 1.10}
            for s in stocks
        ]
        top5_pm = cft.compute_resilient_leaders_top5(stocks_pm, macro_data=macro, session_code="PRE_MARKET", top_n=5)
        self.assertEqual(top5_pm[0]["ticker"], "NVDA")
        self.assertEqual(top5_pm[0]["vwap_tag"], "✓强于SPY")

    # 5. OPEX / 四巫日自动计算与横幅整合
    def test_05_opex_and_quad_witching_auto_calendar(self):
        # 2026-11-20 = 月度 OPEX (11月第3个周五)
        nov_opex = jn.compute_opex_shield_info(date(2026, 11, 20))
        self.assertTrue(nov_opex["active"])
        self.assertEqual(nov_opex["timing"], "TODAY")
        self.assertEqual(nov_opex["kind"], "MONTHLY_OPEX")
        self.assertFalse(nov_opex["is_quad_witching"])
        self.assertEqual(nov_opex["opex_date"], "2026-11-20")
        self.assertIn("月度 OPEX 期权到期日", nov_opex["headline"])

        # 2026-11-19 = 月度 OPEX T-1
        nov_t1 = jn.compute_opex_shield_info(date(2026, 11, 19))
        self.assertTrue(nov_t1["active"])
        self.assertEqual(nov_t1["timing"], "T_MINUS_1")
        self.assertEqual(nov_t1["kind"], "MONTHLY_OPEX")

        # 2026-12-18 = 四巫日 Quad Witching (12月第3个周五)
        dec_qw = jn.compute_opex_shield_info(date(2026, 12, 18))
        self.assertTrue(dec_qw["active"])
        self.assertEqual(dec_qw["timing"], "TODAY")
        self.assertEqual(dec_qw["kind"], "QUAD_WITCHING")
        self.assertTrue(dec_qw["is_quad_witching"])
        self.assertIn("四巫日", dec_qw["headline"])

        # 2026-12-17 = 四巫日 T-1；以及 3/6/9 月第三个周五均为四巫日
        self.assertEqual(jn.compute_opex_shield_info(date(2026, 12, 17))["kind"], "QUAD_WITCHING")
        self.assertEqual(jn.compute_opex_shield_info(date(2026, 12, 17))["timing"], "T_MINUS_1")
        for qw_dt in (date(2026, 3, 20), date(2026, 6, 19), date(2026, 9, 18)):
            info = jn.compute_opex_shield_info(qw_dt)
            self.assertTrue(info["active"])
            self.assertEqual(info["kind"], "QUAD_WITCHING")

        # 2026-11-22 (非 OPEX/T-1 周日) 不激活，指向下一次 2026-12-18
        calm_dt = jn.compute_opex_shield_info(date(2026, 11, 22))
        self.assertFalse(calm_dt["active"])
        self.assertEqual(calm_dt["next_opex_date"], "2026-12-18")
        self.assertTrue(calm_dt["next_is_quad_witching"])

        # 顶部横幅在 OPEX 日自动点亮护盾
        banner = jn.build_calendar_banner_summary([], [], target_date=date(2026, 11, 20))
        self.assertEqual(banner["level"], "opex_shield")
        self.assertIn("月度 OPEX 期权到期日", banner["headline"])
        html_banner = jn.render_macro_calendar_banner_html(
            {"macro_calendar": [], "macro_flashes": []}, variant="terminal", target_date=date(2026, 11, 20)
        )
        self.assertIn("月度 OPEX 期权到期日", html_banner)
        self.assertIn("OPEX 护盾", html_banner)

    # 6. 跨市场流动性监控 (HYG/IEF · DXY · USDJPY)、断网降级、79 池纯净性与裸 --- 审计
    def test_06_cross_market_liquidity_degradation_and_pool_purity(self):
        # 正常温和状态
        calm_liq = vre.compute_cross_market_liquidity_from_series(
            hyg_series=_make_series([79.0, 79.1, 79.2, 79.1, 79.3, 79.4]),
            ief_series=_make_series([94.0, 94.0, 94.1, 94.0, 94.1, 94.2]),
            dxy_series=_make_series([106.0, 106.1, 106.2, 106.1, 106.0, 106.1]),
            usdjpy_series=_make_series([154.0, 154.2, 154.1, 154.3, 154.4, 154.5]),
        )
        self.assertEqual(calm_liq["stress_level"], "calm")
        self.assertFalse(calm_liq["credit_stress"])
        self.assertFalse(calm_liq["dxy_tightening"])
        self.assertFalse(calm_liq["carry_unwind"])

        # 流动性收紧高压组合：信用利差走阔 + 美元急升 + 日元套息逆转
        danger_liq = vre.compute_cross_market_liquidity_from_series(
            hyg_series=_make_series([80.0, 79.5, 79.0, 78.5, 78.0, 77.5]),
            ief_series=_make_series([94.0, 94.2, 94.4, 94.6, 94.8, 95.0]),
            dxy_series=_make_series([105.0, 105.4, 105.8, 106.2, 106.6, 107.0]),
            usdjpy_series=_make_series([156.0, 155.0, 154.0, 153.0, 152.0, 151.0]),
        )
        self.assertEqual(danger_liq["stress_level"], "danger")
        self.assertTrue(danger_liq["credit_stress"])
        self.assertTrue(danger_liq["dxy_tightening"])
        self.assertTrue(danger_liq["carry_unwind"])
        self.assertIn("流动性收紧", danger_liq["summary_cn"])

        # 网络完全异常时优雅降级不抛异常
        liq_cache = os.path.join(self._tmp_dir.name, "macro_liquidity_cache.json")
        with patch.object(vre.yf, "download", side_effect=RuntimeError("offline")):
            with patch.object(vre.yf, "Ticker", side_effect=RuntimeError("offline")):
                deg_liq = vre.get_cross_market_liquidity_snapshot(
                    force_refresh=True, cache_path=liq_cache, allow_network=True
                )
        self.assertEqual(deg_liq["stress_level"], "degraded")
        self.assertTrue(deg_liq["degraded"])
        self.assertIsNone(deg_liq["hyg_ief_ratio"])

        # 严守 79 只可交易自选池不变，绝不混入 HYG / IEF / DX-Y.NYB / JPY=X / ^VIX 等宏观指标
        etfs = imr.MONITORED_TARGETS["ETFS"]
        stocks = imr.MONITORED_TARGETS["STOCKS"]
        self.assertEqual(len(etfs), 15)
        self.assertEqual(len(stocks), 65)
        self.assertEqual(len(etfs) + len(stocks), 80)
        for macro_only in ("HYG", "IEF", "DX-Y.NYB", "JPY=X", "^VIX", "^VXN", "^VIX3M", "^VVIX", "^TNX", "^KS11"):
            self.assertNotIn(macro_only, etfs)
            self.assertNotIn(macro_only, stocks)

        # macro_radar.html 成功渲染跨市场流动性卡片
        macro_out_html = os.path.join(self._tmp_dir.name, "macro_radar.html")
        idx_20 = pd.bdate_range("2026-10-15", periods=25)
        dummy_hist = {
            tk: pd.DataFrame({
                "Open": [100.0 + i for i in range(25)],
                "High": [101.0 + i for i in range(25)],
                "Low": [99.0 + i for i in range(25)],
                "Close": [100.5 + i for i in range(25)],
                "Volume": [1000000 for _ in range(25)],
            }, index=idx_20)
            for tk in ("QQQ", "SPY", "DIA", "SMH", "IWM", "TLT", "GLD", "IGV", "VIXY")
        }
        dummy_macro = {
            tk: {
                "name": tk, "sub": "测试", "cat": "基准",
                "last": 120.0, "pct_change": 0.5, "active_price": 120.0, "ext_price": 0.0,
                "ma50": 115.0, "bias": 4.35, "atr": 2.0, "rsi": 55.0, "status": "强势多头", "badge_color": "#1b5e20",
            }
            for tk in ("QQQ", "SPY", "TLT")
        }
        dummy_weather = {
            "weather": "🟡 震荡分化", "icon": "🟡", "color": "#f59e0b",
            "desc": "测试", "tactics": "控制仓位",
            "qqqm_bias": 2.1, "qqqm_ma20_bias": 1.0, "qqqm_pct": 0.3,
            "smh_bias": 1.5, "smh_ma20_bias": 0.8, "smh_pct": 0.2,
            "vixy_last": 35.0, "tlt_bias": -1.2, "tlt_state": "中性",
            "vix_regime": vre.compute_vix_regime_from_series(_make_series([20.0, 21.0])),
            "cross_market_liquidity": danger_liq,
        }
        with patch.object(imr, "MACRO_HTML_FILE", macro_out_html):
            imr.generate_macro_radar_html(dummy_macro, dummy_weather, dummy_hist, update_time_str="2026-11-20 10:00:00", has_realtime=False)
        with open(macro_out_html, "r", encoding="utf-8") as mf:
            macro_html_txt = mf.read()
        self.assertIn('id="crossMarketLiquidityCard"', macro_html_txt)
        self.assertIn("HYG / IEF", macro_html_txt)
        self.assertIn("DX-Y.NYB", macro_html_txt)
        self.assertIn("JPY=X", macro_html_txt)
        self.assertIn("跨市场流动性收紧预警", macro_html_txt)

        # Discord 推送模板不应包含裸 `---` 分隔线
        with open(os.path.join(PROJECT_DIR, "integrated_market_radar.py"), "r", encoding="utf-8") as rf:
            radar_src = rf.read()
        self.assertIsNone(re.search(r'(?:^|\\n)---(?:\\n|$)', radar_src))
        self.assertNotIn('md.append("---")', radar_src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
