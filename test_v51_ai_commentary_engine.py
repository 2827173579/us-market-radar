# -*- coding: utf-8 -*-
"""
V5.1 专项离线单元测试：双引擎大模型解盘 + 硬风控铁笼 + Discord 私有白名单 + 零密钥泄露 + 节流干跑隔离
====================================================================================================
100% Mock 网络、使用 tempfile.TemporaryDirectory()、0 真实 API 消耗、0 覆写根目录生产 HTML。
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import ai_commentary_engine as ace
import create_futu_terminal as futu_term
import discord_radar_bot as bot


def _sample_snapshot(vix_ratio: float = 1.16, knife_locked: bool = True, bear_inertia: bool = True):
    return {
        "updated_at_et": "2026-11-23 10:00:00 ET",
        "session_code": "REGULAR",
        "session_name": "常规盘中",
        "weather": "暴雨空仓" if knife_locked else "晴天进攻",
        "market_regime": "BEAR_DEFENSE" if knife_locked else "BULL_TREND",
        "vix_regime": {
            "vix_spot": 35.9 if knife_locked else 15.2,
            "vix3m_spot": 30.9 if knife_locked else 17.5,
            "vix_term_ratio": vix_ratio,
            "vxn_spot": 42.1 if knife_locked else 18.0,
            "vix_state": "BACKWARDATION_BRAKE" if knife_locked else "CALM",
        },
        "futures": {
            "NQ=F": {"last": 25661.0, "pct_chg": -1.2, "vwap_24h": 25780.0, "vwap_diff_pct": -0.46, "status_tag": "24H 期货实时"},
            "ES=F": {"last": 7060.0, "pct_chg": -0.9, "vwap_24h": 7066.0, "vwap_diff_pct": -0.09, "status_tag": "24H 期货实时"},
        },
        "market_temperature": {
            "source_tag": "盘中现货",
            "macro_bubble_score": 0.0,
            "macro_bubble_tier": "冰点无泡沫",
            "intraday_bubble_score": 12.0,
            "intraday_bubble_tier": "日内无追高泡沫",
            "macro_bottom_score": 50.0,
            "macro_bottom_tier": "⛔ 超卖观察坑 · 倒挂未解禁抄底",
            "knife_catch_locked": knife_locked,
            "intraday_bottom_score": 20.0,
            "intraday_bottom_tier": "⛔ 日内阴跌寻底 · 禁左侧接刀",
            "intraday_knife_catch_locked": knife_locked,
        },
        "cross_market_liquidity": {"hyg_ief_ratio": 0.83, "hyg_ief_5d_pct": -1.5, "dxy_spot": 103.2, "dxy_5d_pct": 0.8, "usdjpy_spot": 154.0, "usdjpy_5d_pct": -0.3, "stress_level": "warn"},
        "opex_shield": {"active": False, "next_opex_date": "2026-12-18", "days_to_opex": 25},
        "tickers": {
            "QQQ": {"last": 500.0, "pct_chg": -1.5, "vwap": 505.0, "vwap_diff_pct": -0.99, "rsi_5m": 32.0, "anchor_s1": 495.0, "anchor_s2": 488.0, "anchor_r1": 508.0, "anchor_r2": 515.0, "stop_loss": 492.0, "sr_breach_state": "INTACT", "v46_regime_mode": "BEAR_INERTIA_C5" if bear_inertia else "NORMAL_BASE", "bear_inertia_active": bear_inertia, "p15_t5_pct": -5.2, "ai_long_wr": 41.0, "ai_short_wr": 59.0},
            "SPY": {"last": 590.0, "pct_chg": -1.1, "vwap": 594.0, "vwap_diff_pct": -0.67, "rsi_5m": 35.0, "anchor_s1": 585.0, "anchor_s2": 578.0, "anchor_r1": 596.0, "anchor_r2": 602.0, "stop_loss": 582.0, "sr_breach_state": "INTACT", "v46_regime_mode": "BEAR_INERTIA_C5" if bear_inertia else "NORMAL_BASE", "bear_inertia_active": bear_inertia, "p15_t5_pct": -4.1, "ai_long_wr": 43.0, "ai_short_wr": 57.0},
            "SOXX": {"last": 210.0, "pct_chg": -2.4, "vwap": 214.0, "vwap_diff_pct": -1.87, "rsi_5m": 28.0, "anchor_s1": 205.0, "anchor_s2": 198.0, "anchor_r1": 216.0, "anchor_r2": 222.0, "stop_loss": 202.0, "sr_breach_state": "BROKE_S1", "v46_regime_mode": "BEAR_INERTIA_C5" if bear_inertia else "NORMAL_BASE", "bear_inertia_active": bear_inertia, "p15_t5_pct": -8.4, "ai_long_wr": 38.0, "ai_short_wr": 62.0},
        },
    }


class TestV51AiCommentaryEngine(unittest.TestCase):
    def test_01_dual_engine_gemini_429_timeout_fallback_to_deepseek(self):
        """Mock Gemini 429 与超时 -> 自动无缝切换 DeepSeek (deepseek-flash) 并标记兜底；支持 thinkingConfig 400 自动剥离重试。"""
        calls = []

        def fake_post_429(url, headers, payload, timeout):
            calls.append((url, payload))
            if "generativelanguage.googleapis.com" in url:
                return 429, '{"error": "rate limit"}'
            self.assertEqual(payload.get("model"), "deepseek-flash")
            return 200, json.dumps({"choices": [{"message": {"content": "【一句话定性】测试兜底成功。"}}]})

        env_ov = {
            "GEMINI_API_KEY": "MOCK_GEM_KEY_ONLY_TEST",
            "DEEPSEEK_API_KEY": "MOCK_DS_KEY_ONLY_TEST",
            "GEMINI_MODEL": "gemini-3.8-flash",
            "DEEPSEEK_MODEL": "deepseek-flash",
        }
        res = ace.call_llm_text("sys", "usr", provider="auto", env_override=env_ov, http_post_fn=fake_post_429)
        self.assertEqual(res["status"], "OK")
        self.assertTrue(res["fallback_used"])
        self.assertIn("DeepSeek V4.1 Flash (兜底)", res["provider_used"])
        self.assertEqual(len(calls), 2)

        # 超时同样切换 DeepSeek
        def fake_post_timeout(url, headers, payload, timeout):
            if "generativelanguage.googleapis.com" in url:
                raise TimeoutError("simulated timeout")
            return 200, json.dumps({"choices": [{"message": {"content": "【一句话定性】超时兜底成功。"}}]})

        res2 = ace.call_llm_text("sys", "usr", provider="auto", env_override=env_ov, http_post_fn=fake_post_timeout)
        self.assertEqual(res2["status"], "OK")
        self.assertTrue(res2["fallback_used"])
        self.assertIn("DeepSeek", res2["provider_used"])

        # Gemini 400 thinkingConfig 不兼容 -> 自动剥离 thinkingConfig 重试一次并成功
        gem_payloads = []

        def fake_post_strip_thinking(url, headers, payload, timeout):
            gem_payloads.append(payload)
            if "thinkingConfig" in payload.get("generationConfig", {}):
                return 400, '{"error": "Invalid JSON payload: unknown name thinkingConfig"}'
            return 200, json.dumps({"candidates": [{"content": {"parts": [{"text": "【一句话定性】剥离重试成功。"}]}}]})

        res3 = ace.call_llm_text("sys", "usr", provider="gemini", env_override=env_ov, http_post_fn=fake_post_strip_thinking)
        self.assertEqual(res3["status"], "OK")
        self.assertEqual(len(gem_payloads), 2)
        self.assertNotIn("thinkingConfig", gem_payloads[1]["generationConfig"])

    def test_02_risk_lock_prompt_and_post_override_safety_net(self):
        """VIX 倒挂 / knife_catch_locked=True 下：Prompt 写死禁抄底 + 违规输出大胆抄底时代码层自动追加硬风控红警。"""
        snap = _sample_snapshot(vix_ratio=1.16, knife_locked=True, bear_inertia=True)
        locked, reasons = ace.evaluate_risk_lock(snap, target="QQQ")
        self.assertTrue(locked)
        self.assertGreaterEqual(len(reasons), 2)

        sys_p = ace.build_system_prompt(risk_lock_active=True, batch_mode=False)
        self.assertIn("严禁给出任何“抄底", sys_p)
        self.assertIn("100% 源自输入 JSON", sys_p)

        def fake_rogue_llm(url, headers, payload, timeout):
            return 200, json.dumps({
                "candidates": [{"content": {"parts": [{"text": "【一句话定性】跌很多了，建议大胆抄底！"}]}}]
            })

        with tempfile.TemporaryDirectory() as tmpdir:
            res = ace.generate_single_brief(
                target="QQQ",
                snapshot=snap,
                provider="gemini",
                env_override={"GEMINI_API_KEY": "MOCK_KEY"},
                http_post_fn=fake_rogue_llm,
                cache_path=os.path.join(tmpdir, "ai_cache.json"),
            )
            self.assertEqual(res["status"], "OK")
            self.assertTrue(res["risk_lock_active"])
            self.assertIn(ace.RISK_OVERRIDE_BANNER, res["text"])

        # 若原文已带明确否定（如“严禁大胆抄底”），则不误触发重复叠加
        clean_neg = ace.apply_post_risk_safety_net("当前严禁大胆抄底，耐心等待收回 VWAP。", risk_lock_active=True)
        self.assertNotIn(ace.RISK_OVERRIDE_BANNER, clean_neg)

    def test_03_discord_owner_whitelist_reject_zero_llm_calls_and_allow(self):
        """非白名单 Discord User ID 调用 `/ai` 被强制 ephemeral 拒绝且 0 次调用 LLM；白名单本人放行；注册 6 命令。"""
        llm_calls = {"n": 0}

        def fake_post(url, headers, payload, timeout):
            llm_calls["n"] += 1
            return 200, json.dumps({
                "candidates": [{"content": {"parts": [{"text": "【一句话定性】白名单管理员实时剧本。"}]}}]
            })

        snap = _sample_snapshot(vix_ratio=0.88, knife_locked=False, bear_inertia=False)
        env_ov = {
            "GEMINI_API_KEY": "MOCK_KEY",
            "AI_OWNER_DISCORD_USER_IDS": "111222333444,555666777888",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            cpath = os.path.join(tmpdir, "ai_cache.json")
            # 1) 陌生人拒绝且 0 次调 LLM
            denied = bot.handle_ai_command_request(
                user_id="999999999999",
                target="SPY",
                engine="auto",
                refresh=True,
                snapshot=snap,
                cache_path=cpath,
                env_override=env_ov,
                http_post_fn=fake_post,
            )
            self.assertFalse(denied["allowed"])
            self.assertTrue(denied["ephemeral"])
            self.assertEqual(denied["message"], "🔒 此指令仅限雷达管理员本人使用")
            self.assertEqual(llm_calls["n"], 0)

            # 2) 未配置白名单一律拒绝且 0 次调 LLM
            denied_empty = bot.handle_ai_command_request(
                user_id="111222333444",
                target="OVERVIEW",
                snapshot=snap,
                cache_path=cpath,
                env_override={"GEMINI_API_KEY": "MOCK_KEY", "AI_OWNER_DISCORD_USER_IDS": ""},
                http_post_fn=fake_post,
            )
            self.assertFalse(denied_empty["allowed"])
            self.assertEqual(llm_calls["n"], 0)

            # 3) 白名单本人放行
            allowed = bot.handle_ai_command_request(
                user_id="111222333444",
                target="SPY",
                engine="auto",
                refresh=True,
                snapshot=snap,
                cache_path=cpath,
                env_override=env_ov,
                http_post_fn=fake_post,
            )
            self.assertTrue(allowed["allowed"])
            self.assertEqual(llm_calls["n"], 1)
            self.assertIn("SPY / VOO", allowed["embed"]["title"])

        client = bot.RadarBotClient(guild_id="1569503976477433987")
        cmd_names = {c.name for c in client.tree.get_commands()}
        self.assertEqual(cmd_names, {"check", "vix", "top5", "ledger", "temp", "ai"})

    def test_04_zero_secret_leakage_in_rendered_html(self):
        """扫描生成的 macro_radar AI 区块与 futu_pro_terminal HTML，断言绝不包含任何密钥或白名单 ID。"""
        canary_gem = "CANARY_SECRET_GEMINI_KEY_XYZ987"
        canary_ds = "CANARY_SECRET_DEEPSEEK_KEY_ABC654"
        canary_owner = "987654321012345678"
        raw_cache = {
            "status": "OK",
            "updated_at_et": "2026-11-23 10:15 ET",
            "updated_at_kst": "2026-11-24 00:15 KST",
            "provider_used": "Gemini 3.8 Flash",
            "risk_lock_active": True,
            "GEMINI_API_KEY": canary_gem,
            "DEEPSEEK_API_KEY": canary_ds,
            "AI_OWNER_DISCORD_USER_IDS": canary_owner,
            "items": {
                "OVERVIEW": "【一句话定性】大盘偏弱防守。",
                "QQQ": "【一句话定性】QQQ 观察 505 均价线。",
                "SPY": "【一句话定性】SPY / VOO 同步观察 594。",
                "SOXX": "【一句话定性】SOXX 跌破 S1 严禁接刀。",
            },
        }
        safe = ace.sanitize_ai_commentary_for_web(raw_cache)
        macro_ai_html = ace.render_macro_ai_commentary_html(safe)
        futu_html = futu_term.build_futu_terminal_html(
            stocks_data=[{"ticker": "NVDA", "name": "英伟达", "cat": "半导体", "last": 140.0, "pct_chg": 0.5, "bias": 2.0, "rsi": 55.0}],
            macro_data={
                "QQQ": {"ticker": "QQQ", "name": "纳指100", "cat": "宽基", "last": 500.0, "pct_chg": -1.0, "bias": -2.0, "rsi": 42.0},
                "SPY": {"ticker": "SPY", "name": "标普500", "cat": "宽基", "last": 590.0, "pct_chg": -0.8, "bias": -1.5, "rsi": 44.0},
                "SOXX": {"ticker": "SOXX", "name": "半导体", "cat": "半导体", "last": 210.0, "pct_chg": -1.9, "bias": -4.0, "rsi": 35.0},
            },
            weather_info={"weather": "暴雨空仓", "desc": "防守", "tactics": "轻仓", "ai_commentary": safe},
            update_time_str="2026-11-23 10:15:00",
            sync_side_effects=False,
        )
        for html_doc in (macro_ai_html, futu_html):
            for secret in (canary_gem, canary_ds, canary_owner, "generativelanguage.googleapis.com", "api.deepseek.com"):
                self.assertNotIn(secret, html_doc)
        self.assertIn("v51AiCommentaryCard", macro_ai_html)
        self.assertIn("SPY / VOO", macro_ai_html)
        self.assertIn("v51TerminalAiBrief", futu_html)

    def test_05_throttling_off_hours_reuse_and_dry_run_isolation(self):
        """验证四合一打包刷新、25 分钟节流复用、休市 0 消耗及 RADAR_DISABLE_EXTERNAL_ALERTS=1 干跑 0 外发。"""
        snap = _sample_snapshot(vix_ratio=0.90, knife_locked=False, bear_inertia=False)
        calls = {"n": 0}

        def fake_batch_post(url, headers, payload, timeout):
            calls["n"] += 1
            batch_json = json.dumps(
                {
                    "OVERVIEW": "【一句话定性】大盘震荡偏强。",
                    "QQQ": "【一句话定性】QQQ 站稳均价线。",
                    "SPY": "【一句话定性】SPY / VOO（走势100%同步）稳健运行。",
                    "SOXX": "【一句话定性】SOXX 观察上方压力。",
                },
                ensure_ascii=False,
            )
            return 200, json.dumps({"candidates": [{"content": {"parts": [{"text": batch_json}]}}]})

        with tempfile.TemporaryDirectory() as tmpdir:
            cpath = os.path.join(tmpdir, "ai_commentary_cache.json")
            et = timezone(timedelta(hours=-5))
            t_open = datetime(2026, 11, 23, 10, 0, tzinfo=et)

            # 1) 干跑保护：RADAR_DISABLE_EXTERNAL_ALERTS=1 下 0 外发
            dry_res = ace.maybe_refresh_web_brief(
                snapshot=snap,
                cache_path=cpath,
                env_override={"GEMINI_API_KEY": "MOCK_KEY", "RADAR_DISABLE_EXTERNAL_ALERTS": "1", "AI_WEB_AUTO_BRIEF_ENABLED": "1"},
                now_dt=t_open,
                http_post_fn=fake_batch_post,
            )
            self.assertEqual(calls["n"], 0)
            self.assertTrue(dry_res.get("dry_run_skipped"))

            # 2) 盘中首次四合一打包刷新（仅 1 次请求生成全部 4 份）
            live_env = {"GEMINI_API_KEY": "MOCK_KEY", "RADAR_DISABLE_EXTERNAL_ALERTS": "0", "AI_WEB_AUTO_BRIEF_ENABLED": "1", "AI_WEB_REFRESH_MINUTES": "25"}
            res1 = ace.maybe_refresh_web_brief(
                snapshot=snap,
                cache_path=cpath,
                env_override=live_env,
                now_dt=t_open,
                http_post_fn=fake_batch_post,
            )
            self.assertEqual(res1["status"], "OK")
            self.assertEqual(calls["n"], 1)
            self.assertEqual(set(res1["items"].keys()), {"OVERVIEW", "QQQ", "SPY", "SOXX"})

            # 3) 10 分钟内再次调度 -> 命中节流缓存，0 次 API 消耗
            res2 = ace.maybe_refresh_web_brief(
                snapshot=snap,
                cache_path=cpath,
                env_override=live_env,
                now_dt=t_open + timedelta(minutes=10),
                http_post_fn=fake_batch_post,
            )
            self.assertTrue(res2.get("from_cache"))
            self.assertEqual(calls["n"], 1)

            # 4) 休市/周末 -> 已有缓存直接复用，0 次 API 消耗
            t_weekend = datetime(2026, 11, 28, 12, 0, tzinfo=et)
            res3 = ace.maybe_refresh_web_brief(
                snapshot=snap,
                cache_path=cpath,
                env_override=live_env,
                now_dt=t_weekend,
                http_post_fn=fake_batch_post,
            )
            self.assertTrue(res3.get("from_cache"))
            self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
