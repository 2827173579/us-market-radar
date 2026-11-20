#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_vix_intraday_discord_alerts.py - Offline Unit & Contract Test Suite

覆盖 VIX/VXN 恐慌中枢与盘中预测走廊背离（SPY/QQQ/SOXL）接入三大 Discord 频道的所有门禁、路由、
附注与跨设备去重锁要求：
- 全程 Mock urllib.request.urlopen / requests.post，零真实外发推送；
- 独立隔离 shared_alert_state.SHARED_FILE 至临时目录，绝不污染实盘锁文件。
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

try:
    from zoneinfo import ZoneInfo
    ET_TZ = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    from datetime import timezone, timedelta
    ET_TZ = timezone(timedelta(hours=-5))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import discord_sentinel as ds
import integrated_market_radar as imr
import shared_alert_state
import test_discord_sentinel as tds


class _DummyHttpResp:
    def __init__(self, status=204):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


def _calc_payload_chars(payload: dict) -> int:
    total = 0
    for embed in payload.get("embeds", []):
        total += len(embed.get("title", ""))
        total += len(embed.get("description", ""))
        total += len((embed.get("footer") or {}).get("text", ""))
        for field in embed.get("fields", []):
            total += len(field.get("name", "")) + len(field.get("value", ""))
    return total


class TestVixIntradayDiscordAlerts(unittest.TestCase):
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

    # 1. 频道一：VIX 倒挂硬刹车路由与同日去重
    def test_vix_backwardation_brake_routes_to_claw_and_dedupes(self):
        captured, fake_open = self._capture_urlopen()
        vix_regime = {
            "vix_spot": 31.4,
            "vxn_spot": 34.2,
            "vxn_vix_spread": 2.8,  # < 5.5，不触发频道二 VXN 去杠杆
            "vix3m_spot": 28.5,
            "vix_term_ratio": 1.10,
            "vvix_spot": 132.4,
            "tnx_yield": 4.92,
            "term_structure_cn": "期限倒挂（短端恐慌爆表）",
            "vix_state": "BACKWARDATION_BRAKE",
            "vix_hard_brake": True,
            "vix_bottom_signal": False,
        }
        with patch("urllib.request.urlopen", side_effect=fake_open):
            res1 = imr.dispatch_vix_regime_discord_alerts(vix_regime, dedupe_day="2026-11-20", now_str="2026-11-20 10:05:00")
            res2 = imr.dispatch_vix_regime_discord_alerts(vix_regime, dedupe_day="2026-11-20", now_str="2026-11-20 10:06:00")

        self.assertTrue(res1["vix_brake_sent"])
        self.assertFalse(res2["vix_brake_sent"])
        self.assertEqual(len(captured), 1, "同交易日同状态只允许推送 1 次")
        self.assertEqual(captured[0]["url"], self.CLAW_URL)
        embed = captured[0]["payload"]["embeds"][0]
        self.assertIn("VIX 期限倒挂硬刹车", embed["title"])
        desc = embed["description"]
        for tok in ("^VIX", "^VXN", "VIX/VIX3M", "^VVIX", "^TNX", "31.40", "34.20", "1.10", "严禁左侧接刀"):
            self.assertIn(tok, desc)
        self.assertTrue(shared_alert_state.is_sent("2026-11-20:VIX_REGIME:BACKWARDATION_BRAKE"))

    # 2. 频道一：恐慌出清右侧抄底观察窗路由与同日去重
    def test_vix_washout_bottom_window_routes_to_claw_and_dedupes(self):
        captured, fake_open = self._capture_urlopen()
        vix_regime = {
            "vix_spot": 23.1,
            "vix_10d_peak": 32.8,
            "vix_drawdown_from_peak_pct": 29.57,
            "vxn_spot": 26.5,
            "vxn_vix_spread": 3.4,
            "vix3m_spot": 25.4,
            "vix_term_ratio": 0.91,
            "vix_term_ratio_drop": 0.16,
            "vvix_spot": 106.2,
            "tnx_yield": 4.48,
            "term_structure_cn": "升水恢复",
            "vix_state": "WASHOUT_BOTTOM_WINDOW",
            "vix_hard_brake": False,
            "vix_bottom_signal": True,
        }
        with patch("urllib.request.urlopen", side_effect=fake_open):
            res1 = imr.dispatch_vix_regime_discord_alerts(vix_regime, dedupe_day="2026-11-20", now_str="2026-11-20 11:00:00")
            res2 = imr.dispatch_vix_regime_discord_alerts(vix_regime, dedupe_day="2026-11-20", now_str="2026-11-20 11:01:00")

        self.assertTrue(res1["vix_bottom_sent"])
        self.assertFalse(res2["vix_bottom_sent"])
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["url"], self.CLAW_URL)
        embed = captured[0]["payload"]["embeds"][0]
        self.assertIn("恐慌出清", embed["title"])
        desc = embed["description"]
        for tok in ("32.80", "29.57%", "0.91", "站稳日内 VWAP", "右侧观察窗"):
            self.assertIn(tok, desc)
        self.assertTrue(shared_alert_state.is_sent("2026-11-20:VIX_REGIME:WASHOUT_BOTTOM_WINDOW"))

    # 3. 频道二：^VXN >= 32 且 VXN - VIX >= 5.5 路由至防御因子背离频道与同日单次去重
    def test_vxn_tech_deleveraging_routes_to_defensive_channel_and_dedupes(self):
        captured, fake_open = self._capture_urlopen()
        vix_extreme = {
            "vix_spot": 28.9,
            "vxn_spot": 36.5,
            "vxn_vix_spread": 7.6,
            "vix_term_ratio": 0.98,
            "vvix_spot": 127.1,
            "tnx_yield": 5.05,
            "vix_state": "ELEVATED_CAUTION",
            "vix_bottom_signal": False,
        }
        def_flight = {"flight_score": 2.45, "is_alert": True}

        with patch("urllib.request.urlopen", side_effect=fake_open):
            ok1 = imr.send_discord_vxn_deleveraging_alert(
                vix_extreme, def_flight=def_flight, update_time_str="2026-11-20 10:20:00", dedupe_day="2026-11-20"
            )
            ok2 = imr.send_discord_vxn_deleveraging_alert(
                vix_extreme, def_flight=def_flight, update_time_str="2026-11-20 10:21:00", dedupe_day="2026-11-20"
            )
            # 阈值未达到不应触发（vxn=31.0 或 spread=4.0）
            ok_low_vxn = imr.send_discord_vxn_deleveraging_alert(
                {"vix_spot": 24.0, "vxn_spot": 31.0, "vxn_vix_spread": 7.0},
                def_flight=def_flight, dedupe_day="2026-11-21",
            )
            ok_low_spread = imr.send_discord_vxn_deleveraging_alert(
                {"vix_spot": 30.0, "vxn_spot": 34.0, "vxn_vix_spread": 4.0},
                def_flight=def_flight, dedupe_day="2026-11-22",
            )

        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertFalse(ok_low_vxn)
        self.assertFalse(ok_low_spread)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["url"], self.DEF_URL)
        desc = captured[0]["payload"]["embeds"][0]["description"]
        for tok in ("^VXN", "36.50", "^VIX", "28.90", "+7.60", "127.10", "+2.45%"):
            self.assertIn(tok, desc)
        self.assertTrue(shared_alert_state.is_sent("2026-11-20:VXN_TECH_DELEVERAGING"))

    # 3b. 验证兼容别名 DISCORD_DEFENSIVE_WEBHOOK_URL 回退
    def test_defensive_webhook_env_alias_fallback(self):
        with patch.dict(
            os.environ,
            {
                "DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL": "",
                "DISCORD_DEFENSIVE_WEBHOOK_URL": self.DEF_URL,
            },
            clear=False,
        ):
            self.assertEqual(imr.get_defensive_webhook_url(), self.DEF_URL)

    # 4. 频道一：盘中走廊背离白名单（仅 SPY/QQQ/SOXL）+ 09:45~15:50 ET 严格时间门禁
    def test_intraday_corridor_whitelist_and_time_gate(self):
        captured, fake_open = self._capture_urlopen()
        forecast_doc = {
            "forecast_date": "2026-11-20",
            "market_regime": "BEAR_DEFENSE",
            "by_ticker": {
                tk: {
                    "ticker": tk,
                    "forecast_date": "2026-11-20",
                    "status": "ok",
                    "divergence": {
                        "status": "BELOW_P25",
                        "latest_slot": 4,
                        "latest_time": "09:50",
                        "actual_price": 100.0,
                        "slot_p25": 102.0,
                        "slot_p50": 103.5,
                        "slot_p75": 105.0,
                        "pct_vs_p50": -3.38,
                    },
                }
                for tk in ("NVDA", "SOXS", "SQQQ", "SPY", "QQQ", "SOXL")
            },
        }
        # Friday 2026-11-20
        dt_0940 = datetime(2026, 11, 20, 9, 40, tzinfo=ET_TZ)
        dt_1555 = datetime(2026, 11, 20, 15, 55, tzinfo=ET_TZ)
        dt_sat = datetime(2026, 11, 21, 11, 0, tzinfo=ET_TZ)
        dt_1015 = datetime(2026, 11, 20, 10, 15, tzinfo=ET_TZ)
        dt_stale = datetime(2026, 11, 23, 10, 15, tzinfo=ET_TZ)

        with patch("urllib.request.urlopen", side_effect=fake_open):
            r_early = imr.dispatch_intraday_corridor_divergence_alerts(forecast_doc, now_et=dt_0940, session_code="REGULAR")
            r_late = imr.dispatch_intraday_corridor_divergence_alerts(forecast_doc, now_et=dt_1555, session_code="REGULAR")
            r_pre = imr.dispatch_intraday_corridor_divergence_alerts(forecast_doc, now_et=dt_1015, session_code="PRE_MARKET")
            r_post = imr.dispatch_intraday_corridor_divergence_alerts(forecast_doc, now_et=dt_1015, session_code="POST_MARKET")
            r_sat = imr.dispatch_intraday_corridor_divergence_alerts(forecast_doc, now_et=dt_sat, session_code="WEEKEND")
            r_stale = imr.dispatch_intraday_corridor_divergence_alerts(forecast_doc, now_et=dt_stale, session_code="REGULAR")
            self.assertEqual(len(captured), 0, "09:40 ET 或非常规盘中或周末或过期快照严禁触发")
            self.assertEqual(r_early["blocked_reason"], "OUTSIDE_0945_1550_ET")
            self.assertEqual(r_late["blocked_reason"], "OUTSIDE_0945_1550_ET")
            self.assertEqual(r_pre["blocked_reason"], "SESSION_PRE_MARKET")
            self.assertEqual(r_post["blocked_reason"], "SESSION_POST_MARKET")
            self.assertEqual(r_sat["blocked_reason"], "WEEKEND")
            self.assertEqual(r_stale["blocked_reason"], "STALE_FORECAST_DATE")

            r_ok = imr.dispatch_intraday_corridor_divergence_alerts(forecast_doc, now_et=dt_1015, session_code="REGULAR")

        self.assertEqual(len(captured), 3)
        sent_tickers = [x["ticker"] for x in r_ok["sent"]]
        self.assertEqual(sent_tickers, ["SPY", "QQQ", "SOXL"])
        for t_forbidden in ("NVDA", "SOXS", "SQQQ"):
            self.assertNotIn(t_forbidden, sent_tickers)
        for req_obj in captured:
            self.assertEqual(req_obj["url"], self.CLAW_URL)

    # 5. 频道一：QQQ 附带 SQQQ 现价、SOXL 附带 SOXS 现价、SPY 附带大盘体制与 VIX/VIX3M + 同日同向去重
    def test_intraday_corridor_inverse_hedge_footnotes_and_dedupe(self):
        captured, fake_open = self._capture_urlopen()
        forecast_doc = {
            "forecast_date": "2026-11-20",
            "market_regime": "BEAR_DEFENSE",
            "by_ticker": {
                "SPY": {
                    "ticker": "SPY",
                    "forecast_date": "2026-11-20",
                    "status": "ok",
                    "divergence": {
                        "status": "BELOW_P25",
                        "latest_slot": 8,
                        "latest_time": "10:10",
                        "actual_price": 581.20,
                        "slot_p25": 584.10,
                        "slot_p50": 586.50,
                        "slot_p75": 588.80,
                        "pct_vs_p50": -0.90,
                    },
                },
                "QQQ": {
                    "ticker": "QQQ",
                    "forecast_date": "2026-11-20",
                    "status": "ok",
                    "divergence": {
                        "status": "BELOW_P25",
                        "latest_slot": 8,
                        "latest_time": "10:10",
                        "actual_price": 495.40,
                        "slot_p25": 498.80,
                        "slot_p50": 501.00,
                        "slot_p75": 503.20,
                        "pct_vs_p50": -1.12,
                    },
                },
                "SOXL": {
                    "ticker": "SOXL",
                    "forecast_date": "2026-11-20",
                    "status": "ok",
                    "divergence": {
                        "status": "ABOVE_P75",
                        "latest_slot": 8,
                        "latest_time": "10:10",
                        "actual_price": 32.85,
                        "slot_p25": 30.10,
                        "slot_p50": 31.05,
                        "slot_p75": 31.95,
                        "pct_vs_p50": +5.80,
                    },
                },
            },
        }
        macro_data = {
            "SQQQ": {"active_price": 48.62, "last": 48.62, "pct_change": 3.45},
            "SOXS": {"active_price": 19.34, "last": 19.34, "pct_change": -5.18},
        }
        weather_info = {
            "market_regime": "BEAR_DEFENSE",
            "market_regime_cn": "🔴 防御风控体制",
            "vix_regime": {"vix_spot": 28.9, "vix_term_ratio": 1.07},
        }
        dt_1030 = datetime(2026, 11, 20, 10, 30, tzinfo=ET_TZ)

        with patch("urllib.request.urlopen", side_effect=fake_open):
            res1 = imr.dispatch_intraday_corridor_divergence_alerts(
                forecast_doc, macro_data=macro_data, weather_info=weather_info, now_et=dt_1030, session_code="REGULAR"
            )
            res2 = imr.dispatch_intraday_corridor_divergence_alerts(
                forecast_doc, macro_data=macro_data, weather_info=weather_info, now_et=dt_1030, session_code="REGULAR"
            )

        self.assertEqual(len(res1["sent"]), 3)
        self.assertEqual(len(res2["sent"]), 0, "同交易日同方向二次触发必须被去重锁拦截")
        self.assertEqual(len(res2["skipped_dedupe"]), 3)
        self.assertEqual(len(captured), 3)

        by_title = {item["payload"]["embeds"][0]["title"]: item["payload"]["embeds"][0]["description"] for item in captured}
        spy_desc = next(v for k, v in by_title.items() if "SPY" in k)
        qqq_desc = next(v for k, v in by_title.items() if "QQQ" in k)
        soxl_desc = next(v for k, v in by_title.items() if "SOXL" in k)

        self.assertIn("防御风控体制", spy_desc)
        self.assertIn("VIX/VIX3M 1.07", spy_desc)
        self.assertIn("SQQQ", qqq_desc)
        self.assertIn("$48.62", qqq_desc)
        self.assertIn("+3.45%", qqq_desc)
        self.assertIn("SOXS", soxl_desc)
        self.assertIn("$19.34", soxl_desc)
        self.assertIn("-5.18%", soxl_desc)

        # 验证方向反转（QQQ ABOVE_P75）使用独立 key 允许发一次
        forecast_doc["by_ticker"]["QQQ"]["divergence"]["status"] = "ABOVE_P75"
        with patch("urllib.request.urlopen", side_effect=fake_open):
            res3 = imr.dispatch_intraday_corridor_divergence_alerts(
                forecast_doc, macro_data=macro_data, weather_info=weather_info, now_et=dt_1030, session_code="REGULAR"
            )
        self.assertEqual(len(res3["sent"]), 1)
        self.assertEqual(res3["sent"][0]["key"], "2026-11-20:INTRADAY_CORRIDOR:QQQ:ABOVE_P75")

    # 6. 频道三：discord_sentinel.py 定时战报总台 Embed 嵌入 VIX/VXN 摘要与字符数安全校验
    def test_discord_sentinel_embed_vix_vxn_summary_and_char_limits(self):
        analysis = tds.create_synthetic_market_analysis(include_resonance=True, include_divergence=True)
        analysis["vix_regime"] = {
            "vix_spot": 28.9,
            "vxn_spot": 36.5,
            "vxn_vix_spread": 7.6,
            "vix_term_ratio": 1.07,
            "vvix_spot": 127.1,
            "tnx_yield": 5.05,
            "vix_bottom_signal": False,
        }
        p1, p2a, p2b = ds.build_discord_payloads(analysis)
        e1 = p1["embeds"][0]
        desc1 = e1["description"]
        self.assertIn("VIX 28.9", desc1)
        self.assertIn("VXN 36.5 (+7.6)", desc1)
        self.assertIn("期限比 1.07 (倒挂)", desc1)
        self.assertIn("VVIX 127.1", desc1)
        self.assertIn("10Y 5.05%", desc1)
        self.assertNotIn("```", desc1)
        self.assertLess(len(desc1), 4096)
        self.assertLessEqual(_calc_payload_chars(p1), 2000)
        self.assertLessEqual(_calc_payload_chars(p2a), 5500)
        self.assertLessEqual(_calc_payload_chars(p2b), 5500)

        # 触发恐慌出清抄底窗高亮标注
        analysis_bottom = tds.create_synthetic_market_analysis(include_resonance=True, include_divergence=True)
        analysis_bottom["vix_regime"] = {
            "vix_spot": 22.8,
            "vxn_spot": 26.1,
            "vxn_vix_spread": 3.3,
            "vix_term_ratio": 0.91,
            "vvix_spot": 105.0,
            "tnx_yield": 4.45,
            "vix_bottom_signal": True,
        }
        p1_b, _, _ = ds.build_discord_payloads(analysis_bottom)
        desc_b = p1_b["embeds"][0]["description"]
        self.assertIn("🟢 恐慌出清抄底窗", desc_b)
        self.assertIn("升水", desc_b)
        self.assertLess(_calc_payload_chars(p1_b), 6000)

    # 7. 验证干跑安全保护开关（RADAR_DISABLE_EXTERNAL_ALERTS=1）绝不发任何真实 HTTP 请求
    def test_dry_run_guard_blocks_all_real_webhook_posts(self):
        captured, fake_open = self._capture_urlopen()
        vix_regime = {
            "vix_spot": 31.4,
            "vxn_spot": 37.5,
            "vxn_vix_spread": 6.1,
            "vix_term_ratio": 1.10,
            "vvix_spot": 130.0,
            "tnx_yield": 5.0,
            "vix_state": "BACKWARDATION_BRAKE",
            "vix_bottom_signal": False,
        }
        forecast_doc = {
            "forecast_date": "2026-11-20",
            "by_ticker": {
                "QQQ": {
                    "ticker": "QQQ",
                    "forecast_date": "2026-11-20",
                    "status": "ok",
                    "divergence": {"status": "BELOW_P25", "latest_slot": 4, "actual_price": 495.0, "slot_p25": 498.0},
                }
            },
        }
        dt_1030 = datetime(2026, 11, 20, 10, 30, tzinfo=ET_TZ)
        with patch.dict(os.environ, {"RADAR_DISABLE_EXTERNAL_ALERTS": "1"}, clear=False):
            with patch("urllib.request.urlopen", side_effect=fake_open), patch("requests.post") as mock_req_post:
                r_vix = imr.dispatch_vix_regime_discord_alerts(vix_regime, dedupe_day="2026-11-20")
                r_ip = imr.dispatch_intraday_corridor_divergence_alerts(forecast_doc, now_et=dt_1030, session_code="REGULAR")
                ok_sent = ds.dispatch_discord_messages(self.CLAW_URL, {"embeds": [{"title": "x", "description": "y"}]})

        self.assertFalse(r_vix["vix_brake_sent"])
        self.assertFalse(r_vix["vxn_deleveraging_sent"])
        self.assertEqual(len(r_ip["sent"]), 0)
        self.assertFalse(ok_sent)
        self.assertEqual(len(captured), 0)
        self.assertEqual(mock_req_post.call_count, 0)

    # 8. 策略 B：⚡ VIX 盘中真退潮 · 右侧博反抽（倒挂期绝对禁多 + 5道硬门禁 + 同日去重）
    def test_strategy_b_vix_intraday_rebound_gates_and_dedupe(self):
        captured, fake_open = self._capture_urlopen()
        macro_ok = {
            "VIXY": {"active_price": 34.10, "vwap": 35.20, "is_5m_green": False},  # VIXY < VWAP
            "SPY": {"active_price": 748.50, "vwap": 746.00, "is_5m_green": True},
            "QQQ": {"active_price": 672.30, "vwap": 670.10, "is_5m_green": True},
            "SOXL": {"active_price": 52.40, "vwap": 51.10, "is_5m_green": True},
        }
        dt_1000 = datetime(2026, 11, 20, 10, 0, tzinfo=ET_TZ)
        dt_0940 = datetime(2026, 11, 20, 9, 40, tzinfo=ET_TZ)
        dt_1505 = datetime(2026, 11, 20, 15, 5, tzinfo=ET_TZ)

        # (a) 倒挂期（ratio=1.05）即使 VIX 大跌、VIXY < VWAP 且 SOXL > VWAP，也必须严格拦截
        vr_backwardation = {
            "vix_spot": 25.6,
            "vix_pct": -9.2,
            "vix_10d_peak": 28.2,
            "vix_term_ratio": 1.05,
            "vix_term_ratio_10d_peak": 1.10,
            "vix_state": "BACKWARDATION_BRAKE",
            "vix_hard_brake": True,
        }
        # (b) 虽解除倒挂（ratio=0.95），但当日 VIX 跌幅不足 -5%（例如 -3.2% 盘中假回落） -> 严格拦截
        vr_weak_drop = {
            "vix_spot": 23.8,
            "vix_pct": -3.2,
            "vix_10d_peak": 28.2,
            "vix_term_ratio": 0.95,
            "vix_term_ratio_10d_peak": 1.10,
            "vix_state": "ELEVATED_CAUTION",
            "vix_hard_brake": False,
        }
        # (c) 5 道门禁全满足：ratio=0.955 < 1.00，10d峰值 28.18 >= 24，当日 VIX 暴跌 -13.8% <= -5%
        vr_true_ebbing = {
            "vix_spot": 19.92,
            "vix_pct": -13.80,
            "vix_10d_peak": 28.18,
            "vix_term_ratio": 0.955,
            "vix_term_ratio_10d_peak": 1.103,
            "vix_state": "WASHOUT_BOTTOM_WINDOW",
            "vix_hard_brake": False,
        }

        with patch("urllib.request.urlopen", side_effect=fake_open):
            r_back = imr.dispatch_vix_intraday_rebound_alerts(
                vr_backwardation, macro_data=macro_ok, now_et=dt_1000, session_code="REGULAR"
            )
            r_weak = imr.dispatch_vix_intraday_rebound_alerts(
                vr_weak_drop, macro_data=macro_ok, now_et=dt_1000, session_code="REGULAR"
            )
            r_early = imr.dispatch_vix_intraday_rebound_alerts(
                vr_true_ebbing, macro_data=macro_ok, now_et=dt_0940, session_code="REGULAR"
            )
            r_late = imr.dispatch_vix_intraday_rebound_alerts(
                vr_true_ebbing, macro_data=macro_ok, now_et=dt_1505, session_code="REGULAR"
            )
            # VIXY 未跌破 VWAP 时也必须拦截
            macro_vixy_high = dict(macro_ok, VIXY={"active_price": 35.80, "vwap": 35.20, "is_5m_green": True})
            r_vixy_hi = imr.dispatch_vix_intraday_rebound_alerts(
                vr_true_ebbing, macro_data=macro_vixy_high, now_et=dt_1000, session_code="REGULAR"
            )

            self.assertEqual(len(captured), 0, "任何一道门禁未过都绝不允许触发推送")
            self.assertIn("BACKWARDATION", r_back["blocked_reason"])
            self.assertEqual(r_weak["blocked_reason"], "VIX_NOT_COLLAPSING_5PCT")
            self.assertEqual(r_early["blocked_reason"], "OUTSIDE_0945_1500_ET")
            self.assertEqual(r_late["blocked_reason"], "OUTSIDE_0945_1500_ET")
            self.assertEqual(r_vixy_hi["blocked_reason"], "VIXY_NOT_BELOW_VWAP")

            # 正式满足 5 道门禁触发：SPY / QQQ / SOXL 各推 1 条
            r_ok1 = imr.dispatch_vix_intraday_rebound_alerts(
                vr_true_ebbing, macro_data=macro_ok, now_et=dt_1000, session_code="REGULAR"
            )
            # 同一交易日二次调用被 shared_alert_state 去重拦截
            r_ok2 = imr.dispatch_vix_intraday_rebound_alerts(
                vr_true_ebbing, macro_data=macro_ok, now_et=dt_1000, session_code="REGULAR"
            )

        self.assertEqual(len(r_ok1["sent"]), 3)
        self.assertEqual(len(r_ok2["sent"]), 0)
        self.assertEqual(len(r_ok2["skipped_dedupe"]), 3)
        self.assertEqual(len(captured), 3)

        for item in captured:
            self.assertEqual(item["url"], self.CLAW_URL)
            emb = item["payload"]["embeds"][0]
            self.assertIn("VIX 盘中真退潮 · 右侧窄止损博反抽", emb["description"])
            self.assertIn("轻仓看R1·严禁隔夜", emb["description"])
            self.assertIn("-13.80%", emb["description"])
            self.assertIn("0.955", emb["description"])

        descs = {item["payload"]["embeds"][0]["title"]: item["payload"]["embeds"][0]["description"] for item in captured}
        spy_d = next(v for k, v in descs.items() if "SPY" in k)
        soxl_d = next(v for k, v in descs.items() if "SOXL" in k)
        self.assertIn("SPY: -1.2%", spy_d)
        self.assertIn("SOXL: -2.5%", soxl_d)
        for tk in ("SPY", "QQQ", "SOXL"):
            self.assertTrue(shared_alert_state.is_sent(f"2026-11-20:VIX_INTRADAY_REBOUND:{tk}"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
