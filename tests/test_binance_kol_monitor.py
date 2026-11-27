# -*- coding: utf-8 -*-
"""离线单元测试：binance_kol_monitor.py（拆单聚合、动作分类、冷启动防刷屏、增量去重）。"""

import unittest
from unittest.mock import patch

import binance_kol_monitor as bkm


class TestBinanceKolMonitor(unittest.TestCase):
    def test_01_extract_portfolio_id_and_action_classify(self):
        url = "https://www.binance.com/zh-CN/copy-trading/lead-details/5075281354358777856?timeRange=7D"
        self.assertEqual(bkm.extract_portfolio_id(url), "5075281354358777856")
        self.assertEqual(bkm.extract_portfolio_id("5075281354358777856"), "5075281354358777856")

        self.assertEqual(bkm.classify_order_action("BUY", "LONG")["code"], "OPEN_LONG")
        self.assertEqual(bkm.classify_order_action("SELL", "LONG", 100.0)["code"], "CLOSE_LONG")
        self.assertEqual(bkm.classify_order_action("SELL", "SHORT")["code"], "OPEN_SHORT")
        self.assertEqual(bkm.classify_order_action("BUY", "SHORT", -50.0)["code"], "CLOSE_SHORT")

    def test_02_twap_slice_aggregation(self):
        orders = [
            {"symbol": "NVDAUSDT", "baseAsset": "NVDA", "quoteAsset": "USDT", "side": "BUY", "positionSide": "LONG", "type": "MARKET", "executedQty": 1000.0, "avgPrice": 144.0, "totalPnl": 0.0, "orderUpdateTime": 1795626500000},
            {"symbol": "NVDAUSDT", "baseAsset": "NVDA", "quoteAsset": "USDT", "side": "BUY", "positionSide": "LONG", "type": "MARKET", "executedQty": 2000.0, "avgPrice": 144.3, "totalPnl": 0.0, "orderUpdateTime": 1795626530000},
            {"symbol": "牛来USDT", "baseAsset": "牛来", "quoteAsset": "USDT", "side": "SELL", "positionSide": "LONG", "type": "MARKET", "executedQty": 10000.0, "avgPrice": 0.045, "totalPnl": 500.0, "orderUpdateTime": 1795675360000},
        ]
        groups = bkm.aggregate_orders_for_alert(orders, merge_window_ms=120_000)
        self.assertEqual(len(groups), 2)
        nvda = groups[0]
        self.assertEqual(nvda["symbol"], "NVDAUSDT")
        self.assertEqual(nvda["fill_count"], 2)
        self.assertAlmostEqual(nvda["total_qty"], 3000.0, places=4)
        self.assertAlmostEqual(nvda["avg_price"], 144.2, places=4)
        self.assertAlmostEqual(nvda["total_notional"], 432600.0, places=2)

    def test_03_cold_start_and_incremental_dedupe(self):
        detail = {"nickname": "熬鹰资本", "marginBalance": "585950.85", "aumAmount": "8577636.45", "currentCopyCount": 1000, "maxCopyCount": 1000, "sharpRatio": "2.68", "positionShow": False}
        old_orders = [
            {"symbol": "牛来USDT", "baseAsset": "牛来", "quoteAsset": "USDT", "side": "SELL", "positionSide": "LONG", "type": "MARKET", "executedQty": 480192.0, "avgPrice": 0.0457336, "totalPnl": 1901.35, "orderUpdateTime": 1795675360878},
        ]
        new_order = {"symbol": "ETHUSDT", "baseAsset": "ETH", "quoteAsset": "USDT", "side": "SELL", "positionSide": "SHORT", "type": "MARKET", "executedQty": 50.0, "avgPrice": 3200.0, "totalPnl": 0.0, "orderUpdateTime": 1795680000000}

        state = {"schema_version": "binance-kol-v1", "kols": {}}
        kol_cfg = {"name": "熬鹰资本", "portfolio_id": "5075281354358777856", "webhook_url": "https://discord.com/api/webhooks/test/abc", "send_startup_card": True}
        sent_embeds = []

        def fake_post(session, url, embed, **kwargs):
            sent_embeds.append(embed)
            return True

        with patch.object(bkm, "fetch_kol_detail", return_value=detail), \
             patch.object(bkm, "fetch_kol_order_history", return_value=old_orders), \
             patch.object(bkm, "post_discord_embed", side_effect=fake_post):
            # 第1轮冷启动：仅发 1 张上线基线卡，绝不把历史单当新单发
            r1 = bkm.poll_single_kol(None, kol_cfg, state, poll_interval=20)
            self.assertEqual(r1["status"], "initialized")
            self.assertEqual(r1["pushed"], 1)
            self.assertIn("监控已上线", sent_embeds[-1]["title"])

            # 第2轮相同历史数据：0 推送
            r2 = bkm.poll_single_kol(None, kol_cfg, state, poll_interval=20)
            self.assertEqual(r2["pushed"], 0)

        with patch.object(bkm, "fetch_kol_detail", return_value=detail), \
             patch.object(bkm, "fetch_kol_order_history", return_value=[new_order] + old_orders), \
             patch.object(bkm, "post_discord_embed", side_effect=fake_post):
            # 第3轮出现 1 笔 ETHUSDT 开空：精准推送 1 张开空卡片
            r3 = bkm.poll_single_kol(None, kol_cfg, state, poll_interval=20)
            self.assertEqual(r3["pushed"], 1)
            self.assertIn("开空 / 加空", sent_embeds[-1]["title"])
            self.assertIn("ETHUSDT", sent_embeds[-1]["title"])

            # 第4轮重复拉到同一笔 ETHUSDT：严格去重 0 推送
            r4 = bkm.poll_single_kol(None, kol_cfg, state, poll_interval=20)
            self.assertEqual(r4["pushed"], 0)


if __name__ == "__main__":
    unittest.main()
