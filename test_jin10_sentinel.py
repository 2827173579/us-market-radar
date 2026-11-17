#!/usr/bin/env python3
"""
test_jin10_sentinel.py - 金十数据宏观快讯与财经日历哨兵离线单元测试套件
全程 Mock 所有 HTTP/Discord 网络请求与临时状态文件，严禁向真实 Discord 发送任何消息。
"""

import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import requests

import jin10_news_sentinel as jn
import shared_alert_state


class TestJin10NewsSentinel(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmpdir.name, ".jin10_pushed_ids.json")
        self.shared_path = os.path.join(self.tmpdir.name, "shared_alert_dedupe.json")
        self._orig_shared_file = shared_alert_state.SHARED_FILE
        shared_alert_state.SHARED_FILE = self.shared_path

        # 固定基准时间：2026-11-17 08:18:00 ET (= 21:18:00 UTC+8)
        self.tz_et = timezone(timedelta(hours=-5))
        self.tz_bj = timezone(timedelta(hours=8))
        self.now_et = datetime(2026, 11, 17, 8, 18, 0, tzinfo=self.tz_et)
        self.now_ts = self.now_et.timestamp()
        self.now_bj_str = self.now_et.astimezone(self.tz_bj).strftime("%Y-%m-%d %H:%M:%S")

    def tearDown(self):
        shared_alert_state.SHARED_FILE = self._orig_shared_file
        self.tmpdir.cleanup()

    def test_01_red_important_and_whitelist_filter(self):
        """仅标红 important==1 且非广告且命中白名单的快讯可通过过滤（含 type=0 与 type=2）。"""
        raw_items = [
            # 非标红，即使含美联储也必须过滤
            {
                "id": "flash_non_red",
                "type": 0,
                "important": 0,
                "time": self.now_bj_str,
                "data": {"content": "美联储官员例行行程预告"},
            },
            # 广告帖，即使标红也必须过滤
            {
                "id": "flash_ad",
                "type": 0,
                "important": 1,
                "extras": {"ad": True},
                "time": self.now_bj_str,
                "data": {"content": "英伟达VIP课程限时优惠"},
            },
            # 标红但无关美股/宏观
            {
                "id": "flash_unrelated",
                "type": 0,
                "important": 1,
                "time": self.now_bj_str,
                "data": {"content": "国内某景区周末客流量创新高"},
            },
            # 标红 type=0 + HTML 标签 + 鲍威尔
            {
                "id": "flash_powell",
                "type": 0,
                "important": 1,
                "time": self.now_bj_str,
                "data": {"content": "<b>金十快讯</b><br/>美联储主席鲍威尔称将依据后续CPI数据评估降息节奏。"},
            },
            # 标红 type=2 + 关键词在 title 中
            {
                "id": "flash_nvda_t2",
                "type": 2,
                "important": 1,
                "time": self.now_bj_str,
                "data": {"title": "英伟达Q3财报前瞻要点", "content": "AI算力需求持续强劲"},
            },
        ]

        res = jn.filter_important_flashes(raw_items, now_ts=self.now_ts, max_age_seconds=7200)
        ids = [r["id"] for r in res]
        self.assertEqual(ids, ["flash_powell", "flash_nvda_t2"])
        self.assertNotIn("<b>", res[0]["content"])
        self.assertIn("美联储", res[0]["keywords"])
        self.assertIn("鲍威尔", res[0]["keywords"])
        self.assertIn("CPI", res[0]["keywords"])
        self.assertIn("降息", res[0]["keywords"])
        self.assertIn("英伟达", res[1]["keywords"])

    def test_02_all_whitelist_keywords_recognized(self):
        """验证全部 21 个核心美股/宏观白名单关键词均可命中，无遗漏。"""
        expected_kws = [
            "美联储", "鲍威尔", "降息", "加息", "非农", "CPI", "PPI", "PCE",
            "关税", "芯片", "半导体", "英伟达", "台积电", "微软", "苹果",
            "特朗普", "中东", "原油", "美债", "纳斯达克", "标普",
        ]
        for kw in expected_kws:
            hits = jn.matches_macro_whitelist(f"快讯测试：{kw}最新动态")
            self.assertIn(kw, hits, f"Whitelist keyword missing: {kw}")
        self.assertEqual(jn.matches_macro_whitelist("本地商场促销公告"), [])

    def test_03_countdown_15min_pre_release_warning(self):
        """美国三星/五星重磅数据发布前 15 分钟内触发防插针倒计时预警，过滤非美/低星/超时事件。"""
        t_in_12m = (self.now_et + timedelta(minutes=12)).astimezone(self.tz_bj).strftime("%Y-%m-%d %H:%M:%S")
        t_in_25m = (self.now_et + timedelta(minutes=25)).astimezone(self.tz_bj).strftime("%Y-%m-%d %H:%M:%S")
        t_past_2m = (self.now_et - timedelta(minutes=2)).astimezone(self.tz_bj).strftime("%Y-%m-%d %H:%M:%S")

        events = [
            # 命中：美国 5星 核心CPI，12分钟后公布
            jn._normalize_calendar_row({
                "id": "ev_cpi", "country": "美国", "name": "核心CPI年率", "time_period": "10月",
                "star": 5, "pub_time": t_in_12m, "previous": "3.3", "consensus": "3.2", "actual": "", "unit": "%",
            }, "jin10_rili"),
            # 不触发：距公布 25 分钟（>15m）
            jn._normalize_calendar_row({
                "id": "ev_far", "country": "美国", "name": "CPI年率", "time_period": "10月",
                "star": 5, "pub_time": t_in_25m, "previous": "2.6", "consensus": "2.7", "actual": "", "unit": "%",
            }, "jin10_rili"),
            # 不触发：2星低重要度
            jn._normalize_calendar_row({
                "id": "ev_low", "country": "美国", "name": "核心PPI月率", "time_period": "10月",
                "star": 2, "pub_time": t_in_12m, "previous": "0.3", "consensus": "0.2", "actual": "", "unit": "%",
            }, "jin10_rili"),
            # 不触发：非美国（欧元区）
            jn._normalize_calendar_row({
                "id": "ev_eu", "country": "欧元区", "name": "CPI年率", "time_period": "10月",
                "star": 5, "pub_time": t_in_12m, "previous": "2.0", "consensus": "2.1", "actual": "", "unit": "%",
            }, "jin10_rili"),
            # 不触发倒计时：已过公布时间
            jn._normalize_calendar_row({
                "id": "ev_past", "country": "美国", "name": "FOMC利率决议",
                "star": 5, "pub_time": t_past_2m, "previous": "4.50", "consensus": "4.25", "actual": "", "unit": "%",
            }, "jin10_rili"),
        ]

        countdowns, actuals = jn.evaluate_calendar_alerts(events, now_ts=self.now_ts)
        self.assertEqual(len(countdowns), 1)
        self.assertEqual(len(actuals), 0)
        embed = jn.build_countdown_embed(countdowns[0])
        self.assertIn(
            "⚠️ 宏观大考倒计时：距离【美国10月核心CPI年率】公布还有 15 分钟，谨防上下剧烈插针，暂停盲目挂单！",
            embed["description"],
        )
        self.assertIn("前值", embed["description"])
        self.assertIn("预期值", embed["description"])

    def test_04_actual_release_parsing_and_cross_source_dedupe(self):
        """解析 type=1 与日历公布值（含 0% 边界值），验证前值/预期值/实际公布值卡片及跨源去重。"""
        pub_bj = (self.now_et - timedelta(minutes=1)).astimezone(self.tz_bj).strftime("%Y-%m-%d %H:%M:%S")
        pub_epoch = (self.now_et - timedelta(minutes=1)).timestamp()

        # 金十 type=1 公布快讯
        t1_item = {
            "id": "20261117213001",
            "type": 1,
            "important": 1,
            "time": pub_bj,
            "data": {
                "country": "美国",
                "name": "PPI年率",
                "time_period": "10月",
                "star": 3,
                "unit": "%",
                "previous": "6.30",
                "consensus": "6.6",
                "actual": 0.0,  # 检验 0.0 不被当作 falsy 跳过
                "pub_time": pub_bj,
                "data_id": 9991,
            },
        }
        ev_t1 = jn.parse_jin10_type1_release(t1_item)
        # 华尔街见闻同名指标（同比 -> 年率规范化对齐）
        ev_wscn = jn._normalize_calendar_row({
            "id": "wscn_9991",
            "country": "美国",
            "title": "10月PPI同比",
            "importance": 3,
            "public_date": pub_epoch,
            "previous": "6.3",
            "forecast": "6.6",
            "actual": "0.0",
            "unit": "%",
        }, "wscn")

        self.assertEqual(
            jn.canonical_event_dedupe_id(ev_t1, "ACTUAL"),
            jn.canonical_event_dedupe_id(ev_wscn, "ACTUAL"),
        )
        countdowns, actuals = jn.evaluate_calendar_alerts([ev_t1], now_ts=self.now_ts)
        self.assertEqual(len(countdowns), 0)
        self.assertEqual(len(actuals), 1)
        embed = jn.build_actual_embed(actuals[0])
        self.assertIn("前值", embed["description"])
        self.assertIn("预期值", embed["description"])
        self.assertIn("实际公布值", embed["description"])
        self.assertIn("0.0%", embed["description"])

    def test_05_dual_dedupe_and_failed_post_does_not_lock(self):
        """验证本地 .jin10_pushed_ids.json + shared_alert_state 双重去重锁及推送失败不写锁机制。"""
        pub_bj = self.now_bj_str
        flash_payload = {
            "status": 200,
            "data": [{
                "id": "flash_dedupe_1",
                "type": 0,
                "important": 1,
                "time": pub_bj,
                "data": {"content": "美联储决议重磅落地，市场美债收益率剧烈波动。"},
            }],
        }

        def fake_get_ok(url, headers=None, timeout=None):
            m = MagicMock()
            if "flash-api" in url:
                m.status_code = 200
                m.json.return_value = flash_payload
            else:
                m.status_code = 404
                m.json.return_value = []
            return m

        with patch.dict(os.environ, {"DISCORD_JIN10_WEBHOOK_URL": "https://mock.invalid/webhook"}, clear=True):
            # 1) Discord 推送失败 (HTTP 500) -> 不写任何锁，可重试
            fail_resp = MagicMock(status_code=500)
            with patch("jin10_news_sentinel.requests.get", side_effect=fake_get_ok), \
                 patch("jin10_news_sentinel.requests.post", return_value=fail_resp) as m_post:
                s1 = jn.run_jin10_sentinel_safe(state_path=self.state_path, now_ts=self.now_ts)
                self.assertEqual(s1["flashes_sent"], 0)
                self.assertEqual(m_post.call_count, 1)
                self.assertFalse(os.path.exists(self.state_path))
                self.assertFalse(shared_alert_state.is_sent("JIN10_FLASH:flash_dedupe_1"))

            # 2) Discord 推送成功 (HTTP 204) -> 双重落盘写锁
            ok_resp = MagicMock(status_code=204)
            with patch("jin10_news_sentinel.requests.get", side_effect=fake_get_ok), \
                 patch("jin10_news_sentinel.requests.post", return_value=ok_resp) as m_post:
                s2 = jn.run_jin10_sentinel_safe(state_path=self.state_path, now_ts=self.now_ts)
                self.assertEqual(s2["flashes_sent"], 1)
                self.assertEqual(m_post.call_count, 1)
                self.assertIn("JIN10_FLASH:flash_dedupe_1", jn.load_pushed_state(self.state_path))
                self.assertTrue(shared_alert_state.is_sent("JIN10_FLASH:flash_dedupe_1"))

            # 3) 模拟备机（本地 .jin10_pushed_ids.json 为空，仅共享账本已记锁）-> 必须跳过，绝不重复推送
            other_device_state = os.path.join(self.tmpdir.name, ".jin10_tablet.json")
            with patch("jin10_news_sentinel.requests.get", side_effect=fake_get_ok), \
                 patch("jin10_news_sentinel.requests.post", return_value=ok_resp) as m_post:
                s3 = jn.run_jin10_sentinel_safe(state_path=other_device_state, now_ts=self.now_ts)
                self.assertEqual(s3["flashes_sent"], 0)
                self.assertEqual(m_post.call_count, 0)

    def test_06_timeout_3s_enforced_and_silent_degradation(self):
        """验证全部 HTTP 请求严格使用 timeout=3 且网络超时/断连时静默降级不抛异常。"""
        seen_timeouts = []

        def timeout_get(url, headers=None, timeout=None):
            seen_timeouts.append(timeout)
            raise requests.exceptions.Timeout("simulated 3s timeout")

        with patch.dict(os.environ, {"DISCORD_JIN10_WEBHOOK_URL": "https://mock.invalid/webhook"}, clear=True), \
             patch("jin10_news_sentinel.requests.get", side_effect=timeout_get), \
             patch("jin10_news_sentinel.requests.post") as m_post:
            summary = jn.run_jin10_sentinel_safe(state_path=self.state_path, now_ts=self.now_ts, timeout=3)
            self.assertEqual(summary, {"flashes_sent": 0, "countdowns_sent": 0, "actuals_sent": 0})
            self.assertEqual(m_post.call_count, 0)
            self.assertTrue(len(seen_timeouts) >= 2)
            self.assertTrue(all(t == 3 for t in seen_timeouts))

    def test_07_webhook_priority_fallback_and_zero_hardcoded_secrets(self):
        """验证 DISCORD_JIN10_WEBHOOK_URL 优先及 DISCORD_CLAW_WEBHOOK_URL 自动回退，全仓零硬编码。"""
        with patch.dict(
            os.environ,
            {
                "DISCORD_JIN10_WEBHOOK_URL": "https://example.invalid/jin10",
                "DISCORD_CLAW_WEBHOOK_URL": "https://example.invalid/claw",
            },
            clear=True,
        ):
            self.assertEqual(jn.get_jin10_webhook_url(), "https://example.invalid/jin10")

        with patch.dict(os.environ, {"DISCORD_CLAW_WEBHOOK_URL": "https://example.invalid/claw"}, clear=True):
            self.assertEqual(jn.get_jin10_webhook_url(), "https://example.invalid/claw")

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(jn.get_jin10_webhook_url(), "")

        # 扫描源码确保无硬编码 Discord Webhook URL
        base_dir = os.path.dirname(os.path.abspath(__file__))
        for fname in (
            "jin10_news_sentinel.py",
            "test_jin10_sentinel.py",
            "integrated_market_radar.py",
            "android_tablet/TradeRadar_Android.py",
            ".env.example",
        ):
            with open(os.path.join(base_dir, fname), encoding="utf-8") as f:
                txt = f.read()
            self.assertIsNone(
                re.search(r"https://discord(?:app)?\.com/api/webhooks/\d{10,}/[A-Za-z0-9_-]{10,}", txt),
                f"Real webhook URL leaked in {fname}!",
            )

    def test_08_public_html_isolation(self):
        """确认金十消息绝不会写入任何公开 HTML 页面。"""
        unique_marker = "UNIQUE_JIN10_HEADLINE_英伟达芯片出口关税_ONLY_DISCORD"
        mock_flash = {
            "status": 200,
            "data": [{
                "id": "flash_iso_1",
                "type": 0,
                "important": 1,
                "time": self.now_bj_str,
                "data": {"content": unique_marker},
            }],
        }

        def fake_get(url, headers=None, timeout=None):
            m = MagicMock()
            if "flash-api" in url:
                m.status_code = 200
                m.json.return_value = mock_flash
            else:
                m.status_code = 404
                m.json.return_value = []
            return m

        with patch.dict(os.environ, {"DISCORD_JIN10_WEBHOOK_URL": "https://mock.invalid/webhook"}, clear=True), \
             patch("jin10_news_sentinel.requests.get", side_effect=fake_get), \
             patch("jin10_news_sentinel.requests.post", return_value=MagicMock(status_code=204)):
            res = jn.run_jin10_sentinel_safe(state_path=self.state_path, now_ts=self.now_ts)
            self.assertEqual(res["flashes_sent"], 1)

        base_dir = os.path.dirname(os.path.abspath(__file__))
        for html_name in ("futu_pro_terminal.html", "index.html", "predict_radar_5y.html", "stocks_radar.html", "macro_radar.html"):
            p = os.path.join(base_dir, html_name)
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    content = f.read()
                self.assertNotIn(unique_marker, content, f"Jin10 flash leaked to public HTML: {html_name}")

    def test_09_radar_integration_async_dispatch_and_isolation(self):
        """验证 integrated_market_radar 异步调用金十哨兵且异常严格隔离不阻断主线程。"""
        import integrated_market_radar as imr
        with patch.object(jn, "run_jin10_sentinel_safe", side_effect=RuntimeError("mock failure")) as m_safe:
            t = jn.run_jin10_sentinel_async(state_path=self.state_path)
            if t is not None:
                t.join(timeout=2)
            self.assertTrue(m_safe.called)

        with patch.object(jn, "run_jin10_sentinel_async") as m_async:
            imr.dispatch_jin10_sentinel_async()
            self.assertEqual(m_async.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
