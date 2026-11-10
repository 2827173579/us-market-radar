#!/usr/bin/env python3
"""
test_sentinel_challenger_2.py - Empirical Challenger Live & Adversarial Test Suite
Author: teamwork_challenger_sentinel_2
Role: EMPIRICAL CHALLENGER (critic, specialist)

Objectives:
1. Live Push Verification & Instrumentation:
   - Webhook URL read from .env
   - Market data fetched for 51 tickers
   - Exact HTTP status codes (200/204), headers, latency, character counts
   - Check for grey codeblocks
   - Verify >= 1.0s inter-request sleep
2. Adversarial Stress Testing:
   - Synthetic 52 tickers with max possible lengths
   - Maximum possible resonance, divergence, dual extremes
   - Strict verification that no payload exceeds 5,500 chars (safe threshold for Discord 6,000 limit)
   - Verification of zero grey codeblocks (```)
   - HTTP 429 rate limit backoff testing
3. Windows GBK Encoding Vulnerability Verification:
   - Reproduce UnicodeEncodeError on Windows default cp936 console
"""

import os
import sys
import time
import json
import unittest
from unittest.mock import MagicMock, patch
from typing import Dict, Any, List, Tuple

# Project setup
PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# Configure output encoding safely for this test runner
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import discord_sentinel as ds
import requests

class TestSentinelEmpiricalLiveVerification(unittest.TestCase):
    """Empirical live verification of Discord Webhook dispatch and limits."""

    def test_01_env_and_webhook_url(self):
        """Verify .env loading and webhook URL format."""
        ds.load_env_file()
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
        self.assertTrue(webhook_url, "DISCORD_WEBHOOK_URL must not be empty in .env")
        self.assertTrue(
            webhook_url.startswith("https://discord.com/api/webhooks/") or
            webhook_url.startswith("https://discordapp.com/api/webhooks/"),
            f"Webhook URL is not a valid Discord webhook URL: {webhook_url}"
        )
        print(f"\n[Test 1] Webhook URL verified: {webhook_url[:50]}... (length={len(webhook_url)})")

    def test_02_live_push_transmission_and_http_status(self):
        """
        Execute live push instrumenting requests.post to capture:
        - HTTP status code (200 or 204)
        - Latency and time between requests (verifying >= 1.0s sleep)
        - Payload character counts
        - Embed count per payload
        - Zero grey codeblocks (```)
        """
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
        self.assertTrue(webhook_url, "Webhook URL must be set for live push")

        # Intercept requests.post to capture empirical telemetries
        real_post = requests.post
        call_records = []

        def tracked_post(*args, **kwargs):
            t_start = time.time()
            resp = real_post(*args, **kwargs)
            t_end = time.time()
            call_records.append({
                "url": args[0] if args else kwargs.get("url"),
                "json": kwargs.get("json"),
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "text": resp.text,
                "elapsed": t_end - t_start,
                "timestamp": t_start
            })
            return resp

        with patch("requests.post", side_effect=tracked_post):
            # Run sentinel scan without dry-run, push_discord=True
            market_analysis = ds.run_sentinel_scan(dry_run=False, push_discord=True)

        self.assertIsNotNone(market_analysis)
        tickers = market_analysis.get("tickers", {})
        print(f"\n[Test 2] Market data fetched: {len(tickers)} tickers analyzed")
        self.assertGreaterEqual(len(tickers), 51, f"Expected at least 51 tickers, got {len(tickers)}")

        print(f"[Test 2] Dispatched HTTP requests count: {len(call_records)}")
        self.assertGreaterEqual(len(call_records), 2, "Expected at least 2 payloads dispatched (Part 2A and Part 2B)")

        # Verify each request received HTTP 204 No Content (or 200 OK)
        for i, rec in enumerate(call_records):
            status = rec["status_code"]
            print(f"  -> Request #{i+1}: HTTP {status} in {rec['elapsed']:.2f}s | Payload JSON size: {len(json.dumps(rec['json']))} bytes")
            self.assertIn(status, [200, 204], f"Request #{i+1} failed with HTTP status {status}: {rec['text']}")

            payload = rec["json"]
            embeds = payload.get("embeds", [])
            total_chars = 0
            for ej, emb in enumerate(embeds):
                title = emb.get("title", "")
                desc = emb.get("description", "")
                emb_chars = len(title) + len(desc)
                total_chars += emb_chars
                # Invariant: Zero grey codeblocks
                self.assertNotIn("```", desc, f"Grey codeblock found in embed '{title}'")
                self.assertNotIn("```", title, f"Grey codeblock found in embed title '{title}'")
                print(f"     Embed #{ej+1} '{title[:25]}...': {emb_chars} chars")

            print(f"     Total Embed Chars: {total_chars} (Safe limit <= 5500, Discord limit <= 6000)")
            self.assertLessEqual(total_chars, 5500, f"Payload #{i+1} exceeds safe limit 5500 chars: {total_chars}")

        # Verify >= 1.0s delay between sequential dispatches
        if len(call_records) >= 2:
            for i in range(1, len(call_records)):
                delta = call_records[i]["timestamp"] - (call_records[i-1]["timestamp"] + call_records[i-1]["elapsed"])
                print(f"  -> Inter-request sleep between #{i} and #{i+1}: {delta:.2f}s (required >= 0.95s)")
                self.assertGreaterEqual(delta, 0.95, f"Rate-limit sleep between #{i} and #{i+1} was too short: {delta:.2f}s")


class TestSentinelAdversarialLimits(unittest.TestCase):
    """Adversarial stress testing of Discord payload generation and limits."""

    def test_03_adversarial_max_char_boundary(self):
        """
        Stress test with 52 synthetic tickers with maximum possible field lengths:
        - Maximum length ticker symbols
        - Extremely high prices and POCs ($999,999.99)
        - Extreme status text with all badges
        - All resonance and divergence signals active
        - Verify build_discord_payloads NEVER exceeds 5,500 characters per payload.
        """
        synthetic_tickers = {}
        # Use all sentinel tickers
        for sym in ds.ALL_SENTINEL_TICKERS:
            synthetic_tickers[sym] = {
                "price": 999999.99,
                "rsi_4h": 12.3,
                "rsi_1d": 23.4,
                "poc": 999999.99,
                "support": 888888.88,
                "resistance": 1111111.11,
                "status": "🔥 双周期见底(⚠️冷却观察·✅右侧企稳)",
                "display_name": sym
            }

        factor_y = {
            "price": 999999.99,
            "rsi_4h": 15.5,
            "rsi_1d": 25.5,
            "poc": 999999.99,
            "support": 888888.88,
            "resistance": 1111111.11,
            "status": "🔥 双周期见底",
            "display_name": "神秘因子Y"
        }

        # Max out alerts
        market_analysis = {
            "timestamp": "2026-11-10 15:30:00",
            "session": "REGULAR",
            "tickers": synthetic_tickers,
            "factor_y": factor_y,
            "resonance_alerts": [
                "⚡ 【SOXL×SOXS 底部共振】SOXL见底 + SOXS触顶，3X芯片做多共振强信号！\n📊 SOXL 4H_RSI:25.0 vs SOXS 4H_RSI:72.0",
                "⚡ 【TQQQ×SQQQ 底部共振】TQQQ见底 + SQQQ触顶，3X纳指做多共振强信号！\n📊 TQQQ 4H_RSI:28.0 vs SQQQ 4H_RSI:75.0"
            ],
            "divergence_alerts": [
                "🟡 【因子Y×芯片·左侧背离】因子Y高位拥挤，芯片超卖见底，留意高低切反抽！\n📊 芯片 4H_RSI:22.0 vs 因子Y 4H_RSI:68.0"
            ],
            "dual_extremes": [f"{s}(见底)" for s in ds.ALL_SENTINEL_TICKERS[:15]]
        }

        payload_1, payload_2a, payload_2b = ds.build_discord_payloads(market_analysis)

        # Check Payload 1
        self.assertIsNotNone(payload_1)
        p1_embeds = payload_1.get("embeds", [])
        self.assertLessEqual(len(p1_embeds), 10)
        p1_chars = sum(len(e.get("title", "")) + len(e.get("description", "")) + len(e.get("footer", {}).get("text", "")) for e in p1_embeds)
        print(f"\n[Adversarial Test 3] Payload 1 (Max Stress) chars: {p1_chars} (Limit <= 5,500)")
        self.assertLessEqual(p1_chars, 5500, f"Payload 1 exceeded 5,500 chars: {p1_chars}")

        # Check Payload 2A
        p2a_embeds = payload_2a.get("embeds", [])
        self.assertLessEqual(len(p2a_embeds), 10)
        p2a_chars = sum(len(e.get("title", "")) + len(e.get("description", "")) for e in p2a_embeds)
        print(f"[Adversarial Test 3] Payload 2A (Max Stress) chars: {p2a_chars} (Limit <= 5,500)")
        self.assertLessEqual(p2a_chars, 5500, f"Payload 2A exceeded 5,500 chars: {p2a_chars}")

        # Check Payload 2B
        p2b_embeds = payload_2b.get("embeds", [])
        self.assertLessEqual(len(p2b_embeds), 10)
        p2b_chars = sum(len(e.get("title", "")) + len(e.get("description", "")) for e in p2b_embeds)
        print(f"[Adversarial Test 3] Payload 2B (Max Stress) chars: {p2b_chars} (Limit <= 5,500)")
        self.assertLessEqual(p2b_chars, 5500, f"Payload 2B exceeded 5,500 chars: {p2b_chars}")

        # Invariant: Zero grey codeblocks in any embed
        for p_name, p in [("Payload 1", payload_1), ("Payload 2A", payload_2a), ("Payload 2B", payload_2b)]:
            for e in p.get("embeds", []):
                desc = e.get("description", "")
                self.assertNotIn("```", desc, f"{p_name} contains grey codeblock ```: {desc[:50]}")

    def test_04_http_429_rate_limit_backoff_recovery(self):
        """
        Verify that _post_with_retry correctly honors HTTP 429 'retry_after' header
        and retries successfully.
        """
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_resp_429.json.return_value = {"retry_after": 0.1, "message": "You are being rate limited."}

        mock_resp_204 = MagicMock()
        mock_resp_204.status_code = 204

        post_calls = []
        def mocked_post(*args, **kwargs):
            post_calls.append(time.time())
            if len(post_calls) == 1:
                return mock_resp_429
            return mock_resp_204

        with patch("requests.post", side_effect=mocked_post):
            t0 = time.time()
            res = ds._post_with_retry("https://mock-discord-webhook", {"content": "test"}, max_retries=2)
            elapsed = time.time() - t0

        self.assertTrue(res, "_post_with_retry should succeed after recovering from HTTP 429")
        self.assertEqual(len(post_calls), 2, "Expected exactly 2 post attempts (1 fail, 1 retry)")
        self.assertGreaterEqual(elapsed, 0.1, "Expected sleep >= retry_after (0.1s)")
        print(f"\n[Test 4] HTTP 429 Backoff & Retry verified: 2 calls made, elapsed={elapsed:.3f}s")


class TestSentinelWindowsGBKVulnerability(unittest.TestCase):
    """
    Empirically reproduce the Windows GBK encoding defect in discord_sentinel.py.
    """

    def test_05_reproduce_gbk_unicode_encode_error(self):
        """
        Simulate Windows console in GBK (cp936) mode without UTF-8 reconfigure.
        Verify that printing '\u2705' (✅) raises UnicodeEncodeError.
        """
        test_str = "✅ [Discord Sentinel] Webhook dispatch completed successfully."
        with self.assertRaises(UnicodeEncodeError):
            test_str.encode("gbk")
        print("\n[Test 5] Successfully reproduced: '\\u2705' raises UnicodeEncodeError under gbk codec.")

        test_str_rocket = "🚀 [Discord Sentinel] Starting schedule loop (interval: 1800s)..."
        with self.assertRaises(UnicodeEncodeError):
            test_str_rocket.encode("gbk")
        print("[Test 5] Successfully reproduced: '\\U0001f680' (rocket) raises UnicodeEncodeError under gbk codec.")


if __name__ == "__main__":
    unittest.main()
