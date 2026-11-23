# -*- coding: utf-8 -*-
"""
V4.3 Discord 双向交互机器人与轻量只读快照全 Mock 专项单元测试 (test_discord_radar_bot.py)

覆盖核心场景：
1. latest_radar_snapshot.json 原子落盘与 79 只监控池 (14 ETF + 65 个股) 完整覆盖；
2. /check 大小写不敏感、中文别名 (黄金/xau/soxl)、Autocomplete、多头挂单 vs 空头 R1/S1 切换、盘前旧 VWAP 标注、走廊偏离、未知代码优雅建议；
3. /vix 恐慌矩阵 + 策略 A/B + 跨市场流动性 + OPEX 护盾，以及 /top5 抗跌领涨真龙头榜；
4. /ledger 空白就绪兜底与多空严格分账回放（全临时路径隔离）；
5. 缺少 DISCORD_BOT_TOKEN 时 status=0 优雅退出、四命令注册验证、零行情 API 导入与零裸 `---` 校验。
"""

import ast
import json
import os
import pathlib
import tempfile
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import discord

import discord_radar_bot as bot
import integrated_market_radar as radar
import signal_ledger


NY_TZ = ZoneInfo("America/New_York")


def _assert_clean_embed(testcase: unittest.TestCase, emb_dict: dict) -> None:
    testcase.assertIsInstance(emb_dict, dict)
    for key in ("title", "description"):
        txt = str(emb_dict.get(key) or "")
        for ln in txt.splitlines():
            testcase.assertNotEqual(ln.strip(), "---", f"Bare '---' found in embed {key}: {txt!r}")
    for fld in emb_dict.get("fields") or []:
        val = str(fld.get("value") or "")
        testcase.assertTrue(len(val) > 0, f"Empty embed field value for {fld.get('name')}")
        testcase.assertLessEqual(len(val), 1024, f"Field value > 1024 for {fld.get('name')}")
        for ln in val.splitlines():
            testcase.assertNotEqual(ln.strip(), "---", f"Bare '---' found in field {fld.get('name')}: {val!r}")
    # 验证可被真实 discord.py Embed.from_dict 无损反序列化
    emb_obj = discord.Embed.from_dict(emb_dict)
    testcase.assertTrue(len(str(emb_obj.title or "")) > 0)


def _build_full_79_pool_fixture(session_code: str = "REGULAR"):
    etf_map = radar.MONITORED_TARGETS["ETFS"]
    stock_map = radar.MONITORED_TARGETS["STOCKS"]
    assert len(etf_map) == 14 and len(stock_map) == 65

    macro_data = {}
    for idx, (tk, meta) in enumerate(etf_map.items()):
        is_inv = tk in ("SQQQ", "SOXS", "VIXY")
        base_px = 50.0 + idx * 10.0
        vwap_px = round(base_px * (0.985 if tk == "SOXL" else 1.015), 2)
        macro_data[tk] = {
            "name": meta["name"],
            "sub": meta["sub"],
            "cat": meta["cat"],
            "last": base_px,
            "active_price": round(base_px * (1.012 if tk == "SOXL" else 0.992), 2),
            "pct_change": 1.25 if tk == "SOXL" else -0.80,
            "ext_price": round(base_px * 1.018, 2) if session_code != "REGULAR" else 0.0,
            "ext_pct": 1.80 if session_code != "REGULAR" else 0.0,
            "effective_pct": 1.80 if session_code != "REGULAR" else (1.25 if tk == "SOXL" else -0.80),
            "session_tag": "盘前" if session_code == "PRE_MARKET" else "盘中",
            "vwap": vwap_px,
            "has_5m_intraday": True,
            "rsi": 44.0 if tk == "SOXL" else 52.0,
            "rsi_5m": 46.5 if tk == "SOXL" else 39.2,
            "rvol_5m": 1.35,
            "rs_20d": 6.4 if tk == "SOXL" else -1.8,
            "bias": 3.2 if tk == "SOXL" else -4.5,
            "fib_low": round(base_px * 0.92, 2),
            "fib_high": round(base_px * 0.98, 2),
            "is_pit": tk == "SOXL",
            "is_stop": tk == "QQQ",
            "is_momentum": tk == "SPY",
            "is_take_profit": False,
            "is_deep_pullback": False,
            "is_crash_halt": False,
            "is_cooling_halt": False,
            "is_inverse_hedge": is_inv,
            "broken_bearish": tk == "QQQ",
            "broken_rebound_ready": False,
            "optimal_limit": round(base_px * 0.995, 2) if tk != "QQQ" else 0.0,
            "entry_stop_loss": round(base_px * 0.945, 2) if tk != "QQQ" else round(base_px * 0.91, 2),
            "broken_r1": round(base_px * 1.05, 2),
            "broken_s1": round(base_px * 0.91, 2),
            "signal": "🟢 黄金坑右侧反转" if tk == "SOXL" else ("🔴 破位止损" if tk == "QQQ" else "🔵 多头加速"),
        }

    stocks_list = []
    for idx, (tk, meta) in enumerate(stock_map.items()):
        base_px = 80.0 + idx * 3.0
        is_bear_stock = tk in ("AMD", "MRVL")
        is_leader = tk in ("NVDA", "TSM", "AVGO", "MU", "GLW", "ASML")
        stocks_list.append({
            "ticker": tk,
            "name": meta["name"],
            "sub": meta["sub"],
            "cat": meta["cat"],
            "last": base_px,
            "active_price": round(base_px * (0.95 if is_bear_stock else 1.02), 2),
            "pct_change": -3.1 if is_bear_stock else (3.4 if is_leader else 0.6),
            "ext_price": round(base_px * 1.025, 2) if session_code != "REGULAR" else 0.0,
            "ext_pct": 2.50 if session_code != "REGULAR" else 0.0,
            "effective_pct": 2.50 if session_code != "REGULAR" else (-3.1 if is_bear_stock else (3.4 if is_leader else 0.6)),
            "session_tag": "盘前" if session_code == "PRE_MARKET" else "盘中",
            "vwap": round(base_px * (1.03 if is_bear_stock else 0.995), 2),
            "has_5m_intraday": True,
            "rsi": 34.0 if is_bear_stock else 56.0,
            "rsi_5m": 31.2 if is_bear_stock else 58.5,
            "rvol_5m": 1.45,
            "rs_20d": -7.5 if is_bear_stock else (12.0 + (idx % 7) if is_leader else 1.5),
            "bias": -11.2 if is_bear_stock else 4.8,
            "fib_low": round(base_px * 0.90, 2),
            "fib_high": round(base_px * 0.96, 2),
            "is_pit": tk == "NVDA",
            "is_stop": is_bear_stock,
            "is_momentum": is_leader and tk != "NVDA",
            "is_take_profit": False,
            "is_deep_pullback": False,
            "is_crash_halt": False,
            "is_cooling_halt": False,
            "is_inverse_hedge": False,
            "broken_bearish": is_bear_stock,
            "broken_rebound_ready": False,
            "optimal_limit": 0.0 if is_bear_stock else round(base_px * 0.99, 2),
            "entry_stop_loss": round(base_px * 0.92, 2),
            "broken_r1": round(base_px * 1.04, 2),
            "broken_s1": round(base_px * 0.88, 2),
            "signal": "🔴 破位止损" if is_bear_stock else ("🟢 黄金坑右侧反转" if tk == "NVDA" else "🔵 多头推进"),
        })

    vix_regime = {
        "as_of_date": "2026-11-20",
        "vix_spot": 31.20,
        "vix_pct": 8.40,
        "vxn_spot": 38.90,
        "vxn_pct": 9.10,
        "vxn_vix_spread": 7.70,
        "tech_fear_label": "🔥 科技/半导体极端去杠杆（VXN 显著高于大盘）",
        "vix3m_spot": 29.10,
        "vix_term_ratio": 1.072,
        "term_structure_state": "BACKWARDATION",
        "term_structure_cn": "期限倒挂 · 机构恐慌踩踏",
        "vvix_spot": 124.50,
        "tnx_yield": 4.42,
        "vix_10d_peak": 32.10,
        "vix_drawdown_from_peak_pct": 2.8,
        "vix_term_ratio_10d_peak": 1.080,
        "vix_state": "BACKWARDATION_BRAKE",
        "vix_hard_brake": True,
        "raw_bottom_signal": False,
        "vix_bottom_signal": False,
        "vix_bottom_premarket_preview": False,
        "pill_text": "🔴 VIX 31.2 · 倒挂预警(1.07)",
        "summary_cn": "机构恐慌倒挂踩踏期，严禁左侧接飞刀",
        "advice_cn": "倒挂期间大盘体制锁死 BEAR_DEFENSE，等待 VIX/VIX3M < 0.97 且回撤 >=5%。",
        "degraded": False,
    }
    liq = {
        "hyg_ief_ratio": 0.8214,
        "hyg_ief_1d_pct": -0.45,
        "hyg_ief_5d_pct": -1.32,
        "dxy_spot": 106.45,
        "dxy_1d_pct": 0.35,
        "dxy_5d_pct": 1.55,
        "usdjpy_spot": 152.30,
        "usdjpy_1d_pct": -0.60,
        "usdjpy_5d_pct": -1.10,
        "stress_level": "danger",
        "stress_flags": ["信用利差走阔", "美元急升收紧"],
        "badge_cn": "🔴 跨市场流动性收紧预警",
        "summary_cn": "信用利差走阔 + 美元急升收紧",
        "degraded": False,
    }
    weather_info = {
        "market_regime": "BEAR_DEFENSE",
        "market_regime_cn": "🔴 空头防守体制",
        "weather": "暴雨空仓",
        "desc": "纳指基准与半导体破位",
        "breadth_ma20_pct": 22.5,
        "vix_regime": vix_regime,
        "cross_market_liquidity": liq,
    }
    ai_predictions = {
        "by_ticker": {
            "SOXL": {
                "calibrated_win_rate_pct": 56.2,
                "model_raw_prob_pct": 54.0,
                "calibrated_short_win_rate_pct": 38.4,
                "model_raw_short_prob_pct": 40.0,
                "action": "🟢 低吸窗口打开",
                "short_action": "观望",
                "r1": 120.5,
                "s1": 96.0,
                "target_profit_price": 122.0,
            },
            "AMD": {
                "calibrated_win_rate_pct": 37.5,
                "model_raw_prob_pct": 49.0,
                "calibrated_short_win_rate_pct": 61.2,
                "model_raw_short_prob_pct": 58.0,
                "action": "🛡️ 暴雨空仓·严禁左侧接刀",
                "short_action": "🩸 空头占优·逢高做空",
                "r1": 92.8,
                "r2": 102.0,
                "s1": 79.0,
                "s2": 75.5,
                "target_profit_price": 92.8,
            },
        }
    }
    intraday_forecasts = {
        "by_ticker": {
            "SOXL": {
                "status": "ok",
                "pmh": 114.2,
                "pml": 107.1,
                "divergence": {
                    "status": "IN_CORRIDOR",
                    "latest_time": "14:30",
                    "slot_p25": 108.5,
                    "slot_p50": 110.8,
                    "slot_p75": 113.0,
                    "pct_vs_p50": 0.45,
                },
            },
            "AMD": {
                "status": "ok",
                "pmh": 112.0,
                "pml": 101.5,
                "divergence": {
                    "status": "BELOW_P25",
                    "latest_time": "14:30",
                    "slot_p25": 104.0,
                    "slot_p50": 106.2,
                    "slot_p75": 108.5,
                    "pct_vs_p50": -3.40,
                },
            },
        }
    }
    opex = {
        "active": True,
        "timing": "TODAY",
        "kind": "MONTHLY_OPEX",
        "badge": "OPEX 护盾",
        "headline": "⚠️ 月度 OPEX 期权到期日 · 警惕尾盘负 Gamma 剧烈波动",
        "detail": "到期换仓集中，防尾盘假突破与插针。",
        "next_opex_date": "2026-11-20",
        "days_to_opex": 0,
    }
    return macro_data, stocks_list, weather_info, vix_regime, liq, intraday_forecasts, ai_predictions, opex


class TestDiscordRadarBot(unittest.TestCase):
    def test_01_snapshot_atomic_write_and_79_ticker_coverage(self):
        macro_data, stocks_list, weather_info, vix_regime, liq, ip_res, ai_res, opex = _build_full_79_pool_fixture("REGULAR")
        with tempfile.TemporaryDirectory() as tmpdir:
            snap_path = os.path.join(tmpdir, "latest_radar_snapshot.json")
            snap = radar.build_latest_radar_snapshot(
                macro_data=macro_data,
                stocks_data=stocks_list,
                weather_info=weather_info,
                vix_regime=vix_regime,
                cross_market_liquidity=liq,
                intraday_forecasts=ip_res,
                ai_predictions=ai_res,
                session_code="REGULAR",
                session_name="常规盘中",
                now_str="2026-11-20 14:30:00",
                now_et=datetime(2026, 11, 20, 14, 30, tzinfo=NY_TZ),
                opex_shield=opex,
                daily_sr_anchors={},
            )
            written = radar.write_latest_radar_snapshot(snap, snapshot_path=snap_path)
            self.assertTrue(os.path.exists(written))
            leftover_tmps = [fn for fn in os.listdir(tmpdir) if ".tmp." in fn]
            self.assertEqual(leftover_tmps, [])

            loaded = bot.load_radar_snapshot(snap_path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["ticker_count"], 79)
            expected_pool = set(radar.MONITORED_TARGETS["ETFS"]) | set(radar.MONITORED_TARGETS["STOCKS"])
            self.assertEqual(set(loaded["tickers"].keys()), expected_pool)
            for idx_sym in ("^VIX", "^VXN", "^VIX3M", "^VVIX", "^TNX"):
                self.assertNotIn(idx_sym, loaded["tickers"])

            self.assertIn("倒挂", loaded["vix_regime"]["strategy_a_status"])
            self.assertIn("倒挂期绝对禁多", loaded["vix_regime"]["strategy_b_status"])
            self.assertEqual(len(loaded["resilient_leaders_top5"]), 5)
            leader_tickers = {x["ticker"] for x in loaded["resilient_leaders_top5"]}
            self.assertTrue(leader_tickers.isdisjoint({"SQQQ", "SOXS", "VIXY", "TLT", "GLD", "XAUUSD", "BTC-USD"}))
            self.assertEqual(loaded["tickers"]["AMD"]["corridor"]["status_cn"], "跌破下沿")
            self.assertEqual(loaded["tickers"]["SOXL"]["corridor"]["status_cn"], "贴合剧本")

    def test_02_check_command_fuzzy_autocomplete_and_stances(self):
        macro_data, stocks_list, weather_info, vix_regime, liq, ip_res, ai_res, opex = _build_full_79_pool_fixture("PRE_MARKET")
        snap = radar.build_latest_radar_snapshot(
            macro_data=macro_data,
            stocks_data=stocks_list,
            weather_info=weather_info,
            vix_regime=vix_regime,
            cross_market_liquidity=liq,
            intraday_forecasts=ip_res,
            ai_predictions=ai_res,
            session_code="PRE_MARKET",
            session_name="盘前交易",
            now_str="2026-11-20 08:15:00",
            now_et=datetime(2026, 11, 20, 8, 15, tzinfo=NY_TZ),
            opex_shield=opex,
            daily_sr_anchors={},
        )

        # Autocomplete 上限、关键词命中、大小写不敏感
        blank_choices = bot.build_autocomplete_choices("", snap)
        self.assertEqual(len(blank_choices), 25)
        for label, val in blank_choices:
            self.assertLessEqual(len(label), 100)
            self.assertTrue(len(val) > 0)

        self.assertEqual(bot.resolve_ticker("soxl", snap), "SOXL")
        self.assertEqual(bot.resolve_ticker("xau", snap), "XAUUSD")
        self.assertEqual(bot.resolve_ticker("黄金", snap), "XAUUSD")
        self.assertEqual(bot.resolve_ticker("比特币", snap), "BTC-USD")
        self.assertIsNone(bot.resolve_ticker("ZZZZZ_UNKNOWN", snap))

        # 多头窗 + 盘前扩展价 + 旧 VWAP 昨日参考标注 + 大白话诊断
        soxl_emb = bot.build_check_embed_dict("soxl", snap)
        _assert_clean_embed(self, soxl_emb)
        self.assertEqual(soxl_emb["color"], 0x10B981)
        soxl_text = json.dumps(soxl_emb, ensure_ascii=False)
        self.assertIn("一句话结论", soxl_text)
        self.assertIn("参考关注价", soxl_text)
        self.assertIn("防守止损线", soxl_text)
        self.assertIn("昨日参考", soxl_text)
        self.assertIn("盘前", soxl_text)
        self.assertIn("基本按正常预期节奏走", soxl_text)
        self.assertNotIn("BEAR_DEFENSE", soxl_text)

        # 空头防守视角：别手痒抄底 + 禁止出现挂单 + 弱于预期
        amd_emb = bot.build_check_embed_dict("AMD", snap)
        _assert_clean_embed(self, amd_emb)
        self.assertEqual(amd_emb["color"], 0xEF4444)
        amd_text = json.dumps(amd_emb, ensure_ascii=False)
        self.assertIn("一句话结论", amd_text)
        self.assertIn("别抄底", amd_text)
        self.assertIn("别手痒抄底", amd_text)
        self.assertIn("比预期弱很多", amd_text)
        self.assertNotIn("参考挂单", amd_text)
        self.assertNotIn("参考关注价", amd_text)
        self.assertNotIn("BEAR_DEFENSE", amd_text)

        # 中文别名 "黄金" 命中 XAUUSD
        xau_emb = bot.build_check_embed_dict("黄金", snap)
        _assert_clean_embed(self, xau_emb)
        self.assertIn("XAUUSD", xau_emb["title"])

        # 未知代码优雅提示
        unk_emb = bot.build_check_embed_dict("NONEXISTENT99", snap)
        _assert_clean_embed(self, unk_emb)
        self.assertEqual(unk_emb["color"], 0xF59E0B)
        self.assertIn("79", unk_emb["description"])

    def test_03_vix_and_top5_embeds_and_no_bare_hr(self):
        macro_data, stocks_list, weather_info, vix_regime, liq, ip_res, ai_res, opex = _build_full_79_pool_fixture("PRE_MARKET")
        vix_regime_preview = dict(vix_regime)
        vix_regime_preview.update({
            "vix_spot": 23.40,
            "vix_pct": -6.80,
            "vix_term_ratio": 0.942,
            "vix_state": "BOTTOM_PREMARKET_PREVIEW",
            "vix_hard_brake": False,
            "raw_bottom_signal": True,
            "vix_bottom_signal": False,
            "vix_bottom_premarket_preview": True,
            "pill_text": "🟡 VIX 23.4 · 盘前预热 (0.94)",
        })
        snap = radar.build_latest_radar_snapshot(
            macro_data=macro_data,
            stocks_data=stocks_list,
            weather_info=weather_info,
            vix_regime=vix_regime_preview,
            cross_market_liquidity=liq,
            intraday_forecasts=ip_res,
            ai_predictions=ai_res,
            session_code="PRE_MARKET",
            session_name="盘前交易",
            now_str="2026-11-20 08:30:00",
            now_et=datetime(2026, 11, 20, 8, 30, tzinfo=NY_TZ),
            opex_shield=opex,
            daily_sr_anchors={},
        )

        vix_emb = bot.build_vix_embed_dict(snap)
        _assert_clean_embed(self, vix_emb)
        self.assertEqual(vix_emb["color"], 0xF59E0B)
        vix_text = json.dumps(vix_emb, ensure_ascii=False)
        for token in (
            "一句话结论",
            "大盘恐慌",
            "科技股恐慌",
            "10年美债利率",
            "波段抄底",
            "日内抢反弹",
            "美东 09:30",
            "高收益债偏好（跌 = 资金在躲风险）",
            "美元指数",
            "日元",
            "期权到期日提醒",
        ):
            self.assertIn(token, vix_text)
        for bad in ("BEAR_DEFENSE", "信用利差", "期限矩阵"):
            self.assertNotIn(bad, vix_text)

        top5_emb = bot.build_top5_embed_dict(snap)
        _assert_clean_embed(self, top5_emb)
        self.assertEqual(top5_emb["color"], 0x10B981)
        self.assertIn("最抗跌的 5 只票", top5_emb["title"])
        self.assertIn("一句话结论", top5_emb["description"])

        empty_top5 = bot.build_top5_embed_dict({"resilient_leaders_top5": [], "updated_at_et": "2026-11-22 10:00:00"})
        _assert_clean_embed(self, empty_top5)
        self.assertIn("一句话结论", empty_top5["description"])

    def test_04_ledger_embed_empty_and_populated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_ledger = os.path.join(tmpdir, "signal_ledger.json")

            # 空白状态不应创建文件，且展示「账本已就绪，从下周第一条实盘提醒开始自动记账复盘」
            empty_emb = bot.build_ledger_embed_dict(ledger_path=temp_ledger, snapshot={"updated_at_et": "2026-11-22 10:00:00"})
            _assert_clean_embed(self, empty_emb)
            self.assertFalse(os.path.exists(temp_ledger))
            self.assertIn("账本已就绪，从下周第一条实盘提醒开始自动记账复盘", empty_emb["description"])

            # 写入 1 笔做多成功 + 1 笔防守有效拦截（仅写入临时路径）
            now_et = datetime(2026, 11, 20, 10, 15, tzinfo=NY_TZ)
            signal_ledger.record_signal(
                "INTRADAY_5M_BUY", "NVDA", 140.0,
                timestamp_et=now_et, ledger_path=temp_ledger, allow_when_disabled=True,
            )
            signal_ledger.record_signal(
                "VIX_BACKWARDATION_BRAKE", "SPY", 600.0,
                timestamp_et=now_et, ledger_path=temp_ledger, allow_when_disabled=True,
            )
            doc = signal_ledger.load_ledger(temp_ledger)
            doc["signals"][0]["eval"].update({"ret_eod_pct": 2.40, "mae_pct": -0.50})
            doc["signals"][0]["status"] = "COMPLETE"
            doc["signals"][1]["eval"].update({"ret_eod_pct": -1.80, "mae_pct": -2.10})
            doc["signals"][1]["status"] = "COMPLETE"
            signal_ledger.save_ledger(doc, temp_ledger)

            pop_emb = bot.build_ledger_embed_dict(ledger_path=temp_ledger, snapshot={"updated_at_et": "2026-11-22 10:00:00"})
            _assert_clean_embed(self, pop_emb)
            pop_text = json.dumps(pop_emb, ensure_ascii=False)
            self.assertIn("一句话结论", pop_text)
            self.assertIn("做多提醒准不准", pop_text)
            self.assertIn("拦截有效率", pop_text)
            self.assertIn("100.0%", pop_text)
            self.assertIn("NVDA", pop_text)
            self.assertIn("SPY", pop_text)
            self.assertNotIn("BEAR_DEFENSE", pop_text)

    def test_05_missing_token_graceful_exit_and_security_hygiene(self):
        # 缺少 Token 时返回 0 且绝不抛异常
        rc = bot.main(env_override={})
        self.assertEqual(rc, 0)

        # 离线构建 Client 确认 5 个斜杠命令注册完毕 (含 V5.0 /temp)
        client = bot.RadarBotClient(guild_id="1569503976477433987")
        cmd_names = {c.name for c in client.tree.get_commands()}
        self.assertEqual(cmd_names, {"check", "vix", "top5", "ledger", "temp"})

        # 静态安全审计：零外部行情 API 导入、零硬编码密钥/Webhook
        bot_src = pathlib.Path(bot.__file__).read_text(encoding="utf-8")
        tree = ast.parse(bot_src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        forbidden_imports = {"yfinance", "finnhub", "jin10_news_sentinel", "integrated_market_radar", "requests", "urllib"}
        self.assertTrue(imported.isdisjoint(forbidden_imports), f"Forbidden network/market imports in bot: {imported & forbidden_imports}")
        self.assertNotIn("discord.com/api/webhooks", bot_src)
        self.assertNotIn("MTU3Mzkz", bot_src)


if __name__ == "__main__":
    unittest.main()
