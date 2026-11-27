# -*- coding: utf-8 -*-
"""
离线回归单测：防御因子 V2 四色变灯 + 防高开低走 + 精简卡片文案 + 预警中心正规盘门禁与跨日自动清理
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import create_futu_terminal as cft
import integrated_market_radar as imr


def _make_live_lookup():
    return {
        "KO": {"effective_pct": -0.58},
        "PEP": {"effective_pct": -0.12},
        "MCD": {"effective_pct": -0.55},
        "LLY": {"effective_pct": 0.23},
        "MRNA": {"effective_pct": 3.28},
        "TLT": {"effective_pct": -0.06},
        "GLD": {"effective_pct": 2.70},
        "VIXY": {"effective_pct": -6.11},
        "SMH": {"effective_pct": 4.61},
        "SOXL": {"effective_pct": 14.07},
        "NVDA": {"effective_pct": 4.17},
        "QQQ": {"effective_pct": 2.55},
    }


class TestDefensiveFlightV2AndAlerts(unittest.TestCase):
    def test_01_static_four_color_tiers(self):
        """验证静态四色分档：绿(-6.5%) / 蓝(0.0%) / 黄(+1.2%) / 红(+2.0% / +2.5%)。"""
        cases = [
            (-6.50, "green", 0x10B981, "🟢"),
            (0.00, "blue", 0x3B82F6, "🔵"),
            (1.20, "yellow", 0xF59E0B, "🟡"),
            (2.00, "red", 0xEF4444, "🔴"),
            (2.50, "red", 0xEF4444, "🔴"),
        ]
        for sc, exp_lvl, exp_clr, exp_emoji in cases:
            ev = imr.evaluate_defensive_flight_v2(
                df_alert={"flight_score": sc, "def_mean_chg": 0.0, "off_mean_chg": -sc},
                live_lookup=_make_live_lookup(),
                session_code="REGULAR",
                weather_info={"weather": "晴朗主升"},
                state={},
                date_et="2026-11-27",
            )
            self.assertEqual(ev["lamp_level"], exp_lvl, f"score={sc}")
            self.assertEqual(ev["color"], exp_clr, f"score={sc}")
            self.assertEqual(ev["lamp_emoji"], exp_emoji, f"score={sc}")

    def test_02_rth_high_open_fade_early_warning_and_premarket_immunity(self):
        """验证正规盘高开低走提前变灯（-6.50% -> -2.80% 亮黄灯；-> -2.20% 亮红灯），且盘前绝不误触发。"""
        # 1) 正规盘初盘 -6.50% -> 锁定 rth_min_score=-6.50, 亮绿灯
        state = {}
        ev1 = imr.evaluate_defensive_flight_v2(
            df_alert={"flight_score": -6.50, "def_mean_chg": -0.15, "off_mean_chg": 6.35},
            live_lookup=_make_live_lookup(),
            session_code="REGULAR",
            weather_info={"weather": "晴朗主升"},
            state=state,
            date_et="2026-11-27",
        )
        self.assertEqual(ev1["lamp_level"], "green")
        self.assertAlmostEqual(ev1["rth_min_score"], -6.50, places=2)
        state["rth_tracker"] = ev1["rth_tracker"]

        # 2) 正规盘回升至 -2.80%（rebound = +3.70% >= +2.5%）：虽总分仍为负，但提前亮黄灯！
        ev2 = imr.evaluate_defensive_flight_v2(
            df_alert={"flight_score": -2.80, "def_mean_chg": 0.50, "off_mean_chg": 3.30},
            live_lookup=_make_live_lookup(),
            session_code="REGULAR",
            weather_info={"weather": "晴朗主升"},
            state=state,
            date_et="2026-11-27",
        )
        self.assertEqual(ev2["lamp_level"], "yellow")
        self.assertEqual(ev2["color"], 0xF59E0B)
        self.assertTrue(ev2["is_fade_yellow"])
        self.assertAlmostEqual(ev2["rebound_from_low"], 3.70, places=2)

        # 3) 正规盘继续抽血回升至 -2.20%（rebound = +4.30% >= +4.0%）：升级为红灯！
        ev3 = imr.evaluate_defensive_flight_v2(
            df_alert={"flight_score": -2.20, "def_mean_chg": 0.80, "off_mean_chg": 3.00},
            live_lookup=_make_live_lookup(),
            session_code="REGULAR",
            weather_info={"weather": "晴朗主升"},
            state=state,
            date_et="2026-11-27",
        )
        self.assertEqual(ev3["lamp_level"], "red")
        self.assertEqual(ev3["color"], 0xEF4444)
        self.assertTrue(ev3["is_fade_red"])

        # 4) 盘前时段绝不触发高开低走偏移
        ev_pre = imr.evaluate_defensive_flight_v2(
            df_alert={"flight_score": -2.20, "def_mean_chg": 0.80, "off_mean_chg": 3.00},
            live_lookup=_make_live_lookup(),
            session_code="PRE_MARKET",
            weather_info={"weather": "暴雨空仓"},
            state=state,
            date_et="2026-11-27",
        )
        self.assertEqual(ev_pre["lamp_level"], "green")
        self.assertFalse(ev_pre["is_fade_yellow"])
        self.assertFalse(ev_pre["is_fade_red"])

    def test_03_compact_card_premarket_suffix_risk_lock_and_top4_defense(self):
        """验证盘前后缀、暴雨空仓期文案防冲突、Top-4 防御篮子精简与变灯刻度尺输出，以及灯色切换无条件触发去重锁。"""
        payload, ev = imr.build_defensive_flight_discord_payload(
            df_alert={"flight_score": -6.50, "def_mean_chg": -0.15, "off_mean_chg": 6.35},
            live_lookup=_make_live_lookup(),
            update_time_str="2026-11-27 06:50:00",
            session_code="PRE_MARKET",
            weather_info={"weather": "暴雨空仓", "market_regime": "BEAR_DEFENSE"},
            state={},
            date_et="2026-11-27",
        )
        desc = payload["embeds"][0]["description"]
        self.assertIn("(盘前参考)", desc)
        self.assertIn("待 09:30 ET 开盘锁定基准", desc)
        self.assertNotIn("多头仓位顺势拿稳", desc)
        self.assertIn("风控防守期", desc)
        self.assertIn("📏 变灯：🟢≤-1.5｜🔵-1.5~+1｜🟡≥+1/回撤+2.5｜🔴≥+1.8/回撤+4.0%", desc)
        # 防御篮子 8 只按波动绝对值精简 Top-4：VIXY(-6.1%) / MRNA(+3.3%) / GLD(+2.7%) / KO(-0.6%)
        def_line = [ln for ln in desc.splitlines() if ln.startswith("🛡️")][0]
        for top4_sym in ("VIXY", "MRNA", "GLD", "KO"):
            self.assertIn(top4_sym, def_line)
        for dropped_sym in ("TLT", "PEP", "LLY"):
            self.assertNotIn(dropped_sym, def_line)

    def test_03b_two_tier_cooldown_anti_spam_and_zero_cooldown_breakthrough(self):
        """验证两档防抖冷却：同色 5 分钟内 VIXY 跳动 2% & Δscore=0.38%p 拦截；满 30m/15m 且 Δ>=0.50%p 放行；变灯或 Δ>=1.0%p 0 冷却穿透。"""
        base_ts = 1795770000.0
        with tempfile.TemporaryDirectory() as td:
            st_file = os.path.join(td, "df_state.json")
            init_state = {
                "day": {
                    "last_score": -5.03,
                    "last_lamp_level": "green",
                    "last_fade_active": False,
                    "last_date_et": "2026-11-27",
                    "last_push_ts": base_ts,
                    "session": "PRE_MARKET",
                    "components": {"VIXY": -3.9, "SOXL": 12.0},
                },
                "rth_tracker": {},
            }
            with open(st_file, "w", encoding="utf-8") as f:
                json.dump(init_state, f)

            with patch.object(imr, "_DEFENSIVE_FLIGHT_PUSH_STATE", st_file):
                # 1) 同为绿灯、盘前 5 分钟内 VIXY 跳动 2.0% 且 Δscore = 0.38%p -> 严格拦截跳过
                live_vixy_jump = _make_live_lookup()
                live_vixy_jump["VIXY"] = {"effective_pct": -5.9}
                self.assertFalse(
                    imr.should_send_defensive_flight_alert(
                        df_alert={"flight_score": -5.41, "def_mean_chg": -0.2, "off_mean_chg": 5.21},
                        live_lookup=live_vixy_jump,
                        session_code="PRE_MARKET",
                        weather_info={"weather": "暴雨空仓"},
                        date_et="2026-11-27",
                        now_ts=base_ts + 5 * 60,
                    )
                )

                # 2) 同为绿灯、盘前满 30 分钟但 Δscore 仅 0.38%p (< 0.50%p) -> 仍拦截
                self.assertFalse(
                    imr.should_send_defensive_flight_alert(
                        df_alert={"flight_score": -5.41, "def_mean_chg": -0.2, "off_mean_chg": 5.21},
                        live_lookup=live_vixy_jump,
                        session_code="PRE_MARKET",
                        weather_info={"weather": "暴雨空仓"},
                        date_et="2026-11-27",
                        now_ts=base_ts + 31 * 60,
                    )
                )

                # 3) 同为绿灯、盘前满 30 分钟且 Δscore = 0.55%p (>= 0.50%p) -> 允许推送
                self.assertTrue(
                    imr.should_send_defensive_flight_alert(
                        df_alert={"flight_score": -5.58, "def_mean_chg": -0.2, "off_mean_chg": 5.38},
                        live_lookup=live_vixy_jump,
                        session_code="PRE_MARKET",
                        weather_info={"weather": "暴雨空仓"},
                        date_et="2026-11-27",
                        now_ts=base_ts + 30 * 60,
                    )
                )

                # 4) 仅隔 2 分钟但单次剧烈异动 |Δscore| = 1.05%p (>= 1.00%p) -> 0 冷却即时穿透
                self.assertTrue(
                    imr.should_send_defensive_flight_alert(
                        df_alert={"flight_score": -6.08, "def_mean_chg": -0.2, "off_mean_chg": 5.88},
                        live_lookup=live_vixy_jump,
                        session_code="PRE_MARKET",
                        weather_info={"weather": "暴雨空仓"},
                        date_et="2026-11-27",
                        now_ts=base_ts + 2 * 60,
                    )
                )

                # 5) 正规盘同色冷却：10 分钟拦截，满 15 分钟且 Δ >= 0.50%p 放行；仅隔 2 分钟但 green -> yellow 0 冷却穿透
                rth_state = {
                    "day": {
                        "last_score": -5.00,
                        "last_lamp_level": "green",
                        "last_fade_active": False,
                        "last_date_et": "2026-11-27",
                        "last_push_ts": base_ts,
                        "session": "REGULAR",
                        "components": {},
                    },
                    "rth_tracker": {"date_et": "2026-11-27", "rth_open_score": -5.00, "rth_min_score": -5.00},
                }
                with open(st_file, "w", encoding="utf-8") as f:
                    json.dump(rth_state, f)
                self.assertFalse(
                    imr.should_send_defensive_flight_alert(
                        df_alert={"flight_score": -5.60, "def_mean_chg": 0.0, "off_mean_chg": 5.60},
                        live_lookup={},
                        session_code="REGULAR",
                        weather_info={"weather": "晴"},
                        date_et="2026-11-27",
                        now_ts=base_ts + 10 * 60,
                    )
                )
                self.assertTrue(
                    imr.should_send_defensive_flight_alert(
                        df_alert={"flight_score": -5.60, "def_mean_chg": 0.0, "off_mean_chg": 5.60},
                        live_lookup={},
                        session_code="REGULAR",
                        weather_info={"weather": "晴"},
                        date_et="2026-11-27",
                        now_ts=base_ts + 15 * 60,
                    )
                )
                # 仅隔 2 分钟但由 green -> yellow（高开低走回升 +2.60%）-> 0 冷却穿透
                self.assertTrue(
                    imr.should_send_defensive_flight_alert(
                        df_alert={"flight_score": -2.40, "def_mean_chg": 0.0, "off_mean_chg": 2.40},
                        live_lookup={},
                        session_code="REGULAR",
                        weather_info={"weather": "晴"},
                        date_et="2026-11-27",
                        now_ts=base_ts + 2 * 60,
                    )
                )

    def test_04_notification_center_rth_gate_and_cross_day_cleanup(self):
        """验证后端与前端预警中心正规盘门禁（非 REGULAR 屏蔽 5m买点与批量破位）及跨日清理旧通知。"""
        with tempfile.TemporaryDirectory() as td:
            nf = os.path.join(td, "notifs.json")
            # 预埋昨日 11-26 残余告警（防御起飞 + MSFT 5m买点 + AAOI 破位）
            old_notifs = [
                {"id": "DEF_FLIGHT_2026-11-26", "time": "11-26 21:12", "category": "defensive_flight", "ticker": "DEFENSE", "title": "旧防御"},
                {"id": "5M_MSFT_2026-11-26", "time": "11-26 21:12", "category": "5m_entry", "ticker": "MSFT", "title": "旧5m"},
                {"id": "STOP_AAOI_2026-11-26", "time": "11-26 15:50", "category": "stop_loss", "ticker": "AAOI", "title": "旧破位"},
            ]
            with open(nf, "w", encoding="utf-8") as f:
                json.dump(old_notifs, f, ensure_ascii=False)

            stocks = [
                {
                    "ticker": "MSFT", "name": "微软", "cat": "软件", "last": 500.0, "active_price": 500.0,
                    "pct_change": 0.5, "intraday_entry_ready": True, "has_5m_intraday": True, "is_stop": False,
                },
                {
                    "ticker": "AAOI", "name": "AOI", "cat": "光通信", "last": 40.0, "active_price": 40.0,
                    "pct_change": -3.5, "intraday_entry_ready": False, "is_stop": True, "fib_low": 70.0, "bias": -48.0,
                },
            ]
            macro = {"SPY": {"name": "标普", "cat": "ETF", "last": 730.0, "pct_change": 1.2}}
            weather = {"weather": "阴天防守", "desc": "测试", "tactics": "防守"}

            with patch.object(imr, "NOTIFICATION_CACHE_FILE", nf), \
                 patch("ml_predictor_5y.compute_defensive_flight_alert", return_value={"is_alert": False, "flight_score": -4.0}):
                # 盘前 PRE_MARKET：必须清理全部 11-26 旧告警，且绝不生成 5m_entry 与 stop_loss
                res_pre = imr.generate_and_sync_notifications(macro, stocks, weather, "2026-11-27 06:50:00", session_code="PRE_MARKET")
                cats_pre = [n["category"] for n in res_pre]
                self.assertNotIn("5m_entry", cats_pre)
                self.assertNotIn("stop_loss", cats_pre)
                self.assertNotIn("defensive_flight", cats_pre)
                for n in res_pre:
                    self.assertEqual(n.get("date_et"), "2026-11-27")

                # 正规盘 REGULAR：允许生成当日 5m_entry 与 stop_loss
                res_rth = imr.generate_and_sync_notifications(macro, stocks, weather, "2026-11-27 10:05:00", session_code="REGULAR")
                cats_rth = [n["category"] for n in res_rth]
                self.assertIn("5m_entry", cats_rth)
                self.assertIn("stop_loss", cats_rth)

        # 验证前端 HTML 包含正规盘门禁、跨日清理与基线静默初始化逻辑
        html = cft.build_futu_terminal_html(
            stocks_data=stocks,
            macro_data=macro,
            weather_info=weather,
            update_time_str="2026-11-27 06:50:00",
            session_code="PRE_MARKET",
            terminal_notifications=old_notifs,
        )
        self.assertIn('const CURRENT_ET_DATE = "2026-11-27";', html)
        self.assertIn("filterNotificationsForSessionAndDay", html)
        self.assertIn("terminalNotifDateEt", html)
        self.assertIn("isNewTradingDayNotifReset", html)


if __name__ == "__main__":
    unittest.main()
