# -*- coding: utf-8 -*-
"""
V4.4 精准风控补丁全 Mock 专项单元测试 (test_v44_sr_anchor_guard.py)

覆盖核心场景：
1. 当日 S1/S2/R1/R2 静态锚定防漂移（同日盘中暴跌/暴涨绝不修改 anchor_s1/s2/r1/r2）与对称五态顶底互换状态机；
2. 真实历史筹码 (REAL_CHIPS) vs ATR 估算兜底 (ATR_ESTIMATE) 精准识别与前端/Bot 取消误导绿标；
3. 暴跌熔断保护期 / VIX 倒挂期强制降级压制「🟢 4H 底背离拐点 (见底信号)」绿标；
4. latest_radar_snapshot.json 与 Discord Bot /check 卡片全字段同步及零裸 `---` 横线校验。
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
import numpy as np
import pandas as pd

import create_futu_terminal as cft
import discord_radar_bot as bot
import integrated_market_radar as radar
import ml_predictor_5y as mlp
import sr_anchor_engine as sre


NY_TZ = ZoneInfo("America/New_York")


def _make_hist_df(end_date_str: str = "2026-11-19", n_days: int = 65, base_close: float = 42.50, mode: str = "normal") -> pd.DataFrame:
    end_d = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    dates = []
    cur = end_d - timedelta(days=n_days * 2)
    while len(dates) < n_days:
        if cur.weekday() < 5 and cur <= end_d:
            dates.append(cur)
        cur += timedelta(days=1)

    idx = pd.to_datetime(dates)
    closes = []
    highs = []
    lows = []
    vols = []
    for i in range(n_days):
        if mode == "normal":
            # 在 39.5 ~ 45.5 区间震荡且最后一天收在 base_close，保证上下方都有真实历史筹码
            osc = 2.6 * math_sin(i / 6.0)
            c = base_close + osc if i < n_days - 1 else base_close
            h = c + 0.9
            l = c - 0.9
        elif mode == "fresh_breakdown":
            # 前 60 日一直在 50~58 高位密集，最后一日直接跌穿全部历史低点至 base_close=38.0
            c = 54.0 + 2.0 * math_sin(i / 5.0) if i < n_days - 1 else base_close
            h = c + 1.0
            l = c - 0.8 if i < n_days - 1 else base_close - 0.2
        else:
            # fresh_breakout：前 60 日一直在 30~36 震荡，最后一日大涨至 base_close=48.0 创新高
            c = 33.0 + 1.8 * math_sin(i / 5.0) if i < n_days - 1 else base_close
            h = c + 0.6
            l = c - 0.7
        closes.append(round(c, 2))
        highs.append(round(h, 2))
        lows.append(round(l, 2))
        vols.append(1_500_000.0 + (i % 7) * 200_000.0)
    return pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)


def math_sin(x: float) -> float:
    return float(np.sin(x))


def _assert_no_bare_hr(tc: unittest.TestCase, emb_dict: dict) -> None:
    for key in ("title", "description"):
        for ln in str(emb_dict.get(key) or "").splitlines():
            tc.assertNotEqual(ln.strip(), "---")
    for fld in emb_dict.get("fields") or []:
        val = str(fld.get("value") or "")
        tc.assertLessEqual(len(val), 1024)
        for ln in val.splitlines():
            tc.assertNotEqual(ln.strip(), "---")
    discord.Embed.from_dict(emb_dict)


class TestV44SrAnchorGuard(unittest.TestCase):
    def test_01_daily_static_anchor_no_drift_and_5_state_flip(self):
        hist_df = _make_hist_df("2026-11-19", n_days=65, base_close=42.50, mode="normal")
        with tempfile.TemporaryDirectory() as tmpdir:
            anchors_file = os.path.join(tmpdir, "daily_sr_anchors.json")
            locked = sre.get_or_lock_daily_sr_anchor(
                ticker="TESTA",
                date_et="2026-11-20",
                prev_close=42.50,
                atr_pct=4.5,
                df_history=hist_df,
                anchors_path=anchors_file,
            )
            s2 = locked["anchor_s2"]
            s1 = locked["anchor_s1"]
            r1 = locked["anchor_r1"]
            r2 = locked["anchor_r2"]
            self.assertTrue(0 < s2 < s1 < 42.50 < r1 < r2)

            # 同日盘中即使 appending 今日大跌 bar / 修改实时价，再次 lock 或 batch 刷新绝不漂移 4 个锚点
            crash_df = pd.concat([
                hist_df,
                pd.DataFrame(
                    {"Open": [38.0], "High": [38.5], "Low": [30.5], "Close": [30.88], "Volume": [9_000_000.0]},
                    index=pd.to_datetime(["2026-11-20"]),
                ),
            ])
            relocked = sre.get_or_lock_daily_sr_anchor(
                ticker="TESTA",
                date_et="2026-11-20",
                prev_close=42.50,
                atr_pct=8.5,
                df_history=crash_df,
                anchors_path=anchors_file,
            )
            for k in ("anchor_s1", "anchor_s2", "anchor_r1", "anchor_r2", "s1_source", "s2_source", "r1_source", "r2_source"):
                self.assertEqual(relocked[k], locked[k])

            # 模拟单边暴跌 (42.50 -> 击穿S1 -> 跌破S2至30.88) 与 单边暴涨 (42.50 -> 突破R1 -> 突破R2)
            st_intact = sre.evaluate_sr_breach_state(42.50, locked)
            self.assertEqual(st_intact["sr_breach_state"], sre.STATE_INTACT)

            mid_breach_px = round((s1 + s2) / 2.0, 2)
            st_s1 = sre.evaluate_sr_breach_state(mid_breach_px, locked, is_halt=True)
            self.assertEqual(st_s1["sr_breach_state"], sre.STATE_S1_BREACHED)
            self.assertIn("已击穿转压", st_s1["sr_status_badge"])
            self.assertIn(f"S2:${s2:.2f}", st_s1["sr_status_badge"])

            all_sup_px = round(min(30.88, s2 - 1.50), 2)
            st_all_sup = sre.evaluate_sr_breach_state(all_sup_px, locked, is_halt=True, vwap=36.20)
            self.assertEqual(st_all_sup["sr_breach_state"], sre.STATE_ALL_SUPPORT_BREACHED)
            self.assertIn("今日原定支撑全线跌穿", st_all_sup["sr_status_badge"])
            self.assertIn("下方无真实支撑，别猜底", st_all_sup["sr_status_badge"])
            self.assertIn("【暴跌熔断风控剧本】", st_all_sup["scenario_script"])
            # 确保锚点在剧本中也完全未漂移
            self.assertEqual(st_all_sup["anchor_s1"], s1)
            self.assertEqual(st_all_sup["anchor_s2"], s2)

            mid_r1_px = round((r1 + r2) / 2.0, 2)
            st_r1 = sre.evaluate_sr_breach_state(mid_r1_px, locked, vwap=44.10)
            self.assertEqual(st_r1["sr_breach_state"], sre.STATE_R1_BROKEN)
            self.assertIn("已突破 ➔ 转为支撑", st_r1["sr_status_badge"])

            all_res_px = round(max(52.00, r2 + 2.50), 2)
            st_all_res = sre.evaluate_sr_breach_state(all_res_px, locked, vwap=47.30)
            self.assertEqual(st_all_res["sr_breach_state"], sre.STATE_ALL_RESISTANCE_BROKEN)
            self.assertIn("突破今日全线阻力", st_all_res["sr_status_badge"])
            self.assertIn("EMA9 移动止盈", st_all_res["scenario_script"])
            self.assertIn("VWAP", st_all_res["scenario_script"])

            # 跨日 (2026-11-23) 或拆股对齐时允许重建新锚
            next_day = sre.get_or_lock_daily_sr_anchor(
                ticker="TESTA",
                date_et="2026-11-23",
                prev_close=30.88,
                atr_pct=5.0,
                df_history=crash_df,
                anchors_path=anchors_file,
            )
            self.assertEqual(next_day["date_et"], "2026-11-23")
            self.assertEqual(next_day["prev_close"], 30.88)
            self.assertLess(next_day["anchor_s1"], s1)

    def test_02_real_chips_vs_atr_estimate_source_and_visual_separation(self):
        # 1) 全新跌破 60 日所有真实支撑 -> 下方支撑必须标注 ATR_ESTIMATE，而上方存在真实历史筹码阻力标注 REAL_CHIPS
        bd_df = _make_hist_df("2026-11-19", n_days=65, base_close=38.00, mode="fresh_breakdown")
        bd_res = sre.compute_static_sr_levels_with_sources("BDSTK", ref_price=38.00, atr_pct=4.5, df_history=bd_df)
        self.assertEqual(bd_res["s1_source"], sre.SOURCE_ATR_ESTIMATE)
        self.assertEqual(bd_res["s2_source"], sre.SOURCE_ATR_ESTIMATE)
        self.assertIn(sre.SOURCE_REAL_CHIPS, (bd_res["r1_source"], bd_res["r2_source"]))

        # 2) 全新突破 60 日所有真实阻力 -> R1/R2 标注 ATR_ESTIMATE
        bo_df = _make_hist_df("2026-11-19", n_days=65, base_close=48.00, mode="fresh_breakout")
        bo_res = sre.compute_static_sr_levels_with_sources("BOSTK", ref_price=48.00, atr_pct=3.5, df_history=bo_df)
        self.assertEqual(bo_res["r1_source"], sre.SOURCE_ATR_ESTIMATE)
        self.assertEqual(bo_res["r2_source"], sre.SOURCE_ATR_ESTIMATE)

        # 3) 无历史数据时 ml_predictor_5y.compute_multi_level_scenario_map 也标注 ATR_ESTIMATE
        no_hist_sc = mlp.compute_multi_level_scenario_map("NOHIST", current_price=50.0, atr_pct=4.0, is_halt=True)
        self.assertEqual(no_hist_sc["s1_source"], sre.SOURCE_ATR_ESTIMATE)
        self.assertEqual(no_hist_sc["s2_source"], sre.SOURCE_ATR_ESTIMATE)
        self.assertEqual(no_hist_sc["r1_source"], sre.SOURCE_ATR_ESTIMATE)
        self.assertEqual(no_hist_sc["r2_source"], sre.SOURCE_ATR_ESTIMATE)

        # 4) 验证 create_futu_terminal HTML 渲染逻辑包含估算防线(无真实筹码)与五态视觉分支
        html_out = cft.build_futu_terminal_html(
            stocks_data=[{
                "ticker": "NVDA", "name": "NVIDIA", "sub": "算力", "cat": "芯片算力",
                "last": 38.0, "active_price": 38.0, "pct_change": -8.2, "vwap": 40.5,
                "rsi": 28.0, "bias": -14.0, "atr": 1.8, "fib_low": 41.0, "fib_high": 45.0,
                "dist_to_pit": -7.3, "pit_status": "破坑", "signal": "🔴 破位止损",
                "is_crash_halt": True, "is_cooling_halt": False, "is_stop": True,
            }],
            macro_data={
                "QQQ": {"name": "纳指100ETF", "sub": "基准", "cat": "大盘基准", "last": 500.0, "active_price": 500.0, "pct_change": -2.0, "vwap": 505.0, "rsi": 35.0, "bias": -5.0, "atr": 8.0, "fib_low": 490.0, "fib_high": 520.0, "dist_to_pit": 0.0, "pit_status": "正常", "signal": "观望"}
            },
            weather_info={
                "weather": "暴雨空仓", "icon": "🌧️", "market_regime": "BEAR_DEFENSE",
                "vix_regime": {"vix_spot": 32.0, "vix_term_ratio": 1.06, "vix_state": "BACKWARDATION_BRAKE", "vix_hard_brake": True},
            },
            daily_sr_anchors={"NVDA": {**bd_res, "anchor_s1": 36.20, "anchor_s2": 34.40, "anchor_sl": 33.80, "anchor_r1": 45.00, "anchor_r2": 52.00}},
        )
        self.assertIn("估算防线 (无真实筹码)", html_out)
        self.assertIn("今日原定支撑全线跌穿", html_out)
        self.assertIn("突破今日全线阻力", html_out)

    def test_03_suppress_4h_bottom_badge_on_halt_and_vix_backwardation(self):
        # A) 熔断锁定压制
        s_halt = sre.suppress_resonance_badge(
            "🟢 4H 底背离拐点 (见底信号)", "div-bull",
            is_crash_halt=True, is_cooling_halt=False,
            vix_regime={"vix_hard_brake": False, "vix_term_ratio": 0.89},
        )
        self.assertTrue(s_halt["resonance_suppressed"])
        self.assertEqual(s_halt["four_h_tag"], "div-suppressed")
        self.assertEqual(s_halt["four_h_div"], "⚠️ 4H 潜在背离 (熔断锁定·未确认)")
        self.assertNotIn("见底信号", s_halt["four_h_div"])

        # 二次重入幂等校验：已经降级的文案再次经过 suppress_resonance_badge 仍保持 suppressed 状态
        s_recheck = sre.suppress_resonance_badge(
            s_halt["four_h_div"], s_halt["four_h_tag"],
            is_crash_halt=False, is_cooling_halt=False, vix_regime={},
        )
        self.assertTrue(s_recheck["resonance_suppressed"])
        self.assertEqual(s_recheck["four_h_tag"], "div-suppressed")

        # B) 仅 VIX 倒挂压制
        s_vix = sre.suppress_resonance_badge(
            "🟢 4H 底背离拐点 (见底信号)", "div-bull",
            is_crash_halt=False, is_cooling_halt=False,
            vix_regime={"vix_hard_brake": True, "vix_state": "BACKWARDATION_BRAKE", "vix_term_ratio": 1.05},
        )
        self.assertTrue(s_vix["resonance_suppressed"])
        self.assertEqual(s_vix["four_h_tag"], "div-suppressed")
        self.assertEqual(s_vix["four_h_div"], "⚠️ 4H 潜在背离 (VIX倒挂压制)")

        # C) 既非熔断也非倒挂 -> 保留绿色见底信号
        s_ok = sre.suppress_resonance_badge(
            "🟢 4H 底背离拐点 (见底信号)", "div-bull",
            is_crash_halt=False, is_cooling_halt=False,
            vix_regime={"vix_hard_brake": False, "vix_state": "CALM", "vix_term_ratio": 0.88},
        )
        self.assertFalse(s_ok["resonance_suppressed"])
        self.assertEqual(s_ok["four_h_tag"], "div-bull")
        self.assertEqual(s_ok["four_h_div"], "🟢 4H 底背离拐点 (见底信号)")

    def test_04_snapshot_and_discord_bot_check_sync_zero_bare_hr(self):
        hist_df = _make_hist_df("2026-11-19", n_days=65, base_close=42.50, mode="fresh_breakdown")
        anchors = {
            "SOXL": sre.compute_static_sr_levels_with_sources("SOXL", ref_price=42.50, atr_pct=6.0, df_history=hist_df)
        }
        # 确保当前跌破 S2 触发 ALL_SUPPORT_BREACHED + ATR_ESTIMATE + 熔断压制 4H 绿标
        s2_anchor = anchors["SOXL"]["anchor_s2"]
        crash_px = round(s2_anchor - 3.50, 2)
        macro_data = {
            "SOXL": {
                "name": "3倍做多半导体", "sub": "费半3x", "cat": "正向杠杆",
                "last": crash_px, "active_price": crash_px, "pct_change": -9.5,
                "vwap": round(crash_px + 2.0, 2), "rsi": 24.0, "bias": -18.5, "atr": 2.4,
                "fib_low": 39.0, "fib_high": 46.0, "is_crash_halt": True, "is_stop": True,
                "broken_bearish": True, "signal": "🔴 破位止损", "rs_20d": -12.0,
            },
            "QQQ": {
                "name": "纳指100ETF", "sub": "大盘", "cat": "大盘基准",
                "last": 500.0, "active_price": 500.0, "pct_change": 1.2,
                "vwap": 495.0, "rsi": 58.0, "bias": 5.5, "atr": 6.0,
                "fib_low": 480.0, "fib_high": 505.0, "is_momentum": True, "signal": "🔵 多头加速", "rs_20d": 4.5,
            },
        }
        r2_qqq = 510.0
        anchors["QQQ"] = {
            "anchor_s1": 490.0, "anchor_s2": 482.0, "anchor_sl": 474.0,
            "anchor_r1": 498.0, "anchor_r2": r2_qqq,
            "s1_source": sre.SOURCE_REAL_CHIPS, "s2_source": sre.SOURCE_REAL_CHIPS,
            "r1_source": sre.SOURCE_ATR_ESTIMATE, "r2_source": sre.SOURCE_ATR_ESTIMATE,
        }
        macro_data["QQQ"]["active_price"] = 525.0
        macro_data["QQQ"]["last"] = 525.0

        snap = radar.build_latest_radar_snapshot(
            macro_data=macro_data,
            stocks_data=[],
            weather_info={"market_regime": "BEAR_DEFENSE", "market_regime_cn": "🔴 空头防守体制", "weather": "暴雨空仓"},
            vix_regime={"vix_spot": 34.0, "vix_term_ratio": 1.09, "vix_state": "BACKWARDATION_BRAKE", "vix_hard_brake": True},
            cross_market_liquidity={"badge_cn": "🟢 平稳", "stress_level": "calm"},
            ai_predictions={"by_ticker": {
                "SOXL": {"four_h_div": "🟢 4H 底背离拐点 (见底信号)", "four_h_tag": "div-bull", "calibrated_win_rate_pct": 35.0, "calibrated_short_win_rate_pct": 62.0},
                "QQQ": {"four_h_div": "⚡ 4H 均线多头共振", "four_h_tag": "div-trend", "calibrated_win_rate_pct": 60.0, "calibrated_short_win_rate_pct": 35.0},
            }},
            intraday_forecasts={"by_ticker": {}},
            session_code="REGULAR",
            now_str="2026-11-20 14:30:00",
            now_et=datetime(2026, 11, 20, 14, 30, tzinfo=NY_TZ),
            daily_sr_anchors=anchors,
        )

        soxl_item = snap["tickers"]["SOXL"]
        self.assertEqual(soxl_item["sr_breach_state"], sre.STATE_ALL_SUPPORT_BREACHED)
        self.assertEqual(soxl_item["s1_source"], sre.SOURCE_ATR_ESTIMATE)
        self.assertEqual(soxl_item["resonance_badge_text"], "⚠️ 4H 潜在背离 (熔断锁定·未确认)")

        soxl_emb = bot.build_check_embed_dict("soxl", snap)
        _assert_no_bare_hr(self, soxl_emb)
        soxl_txt = json.dumps(soxl_emb, ensure_ascii=False)
        self.assertIn("一句话结论", soxl_txt)
        self.assertIn("今日原定支撑已全线跌穿", soxl_txt)
        self.assertIn("下方无真实支撑，别猜底", soxl_txt)
        self.assertIn("（估算参考，无真实筹码）", soxl_txt)
        self.assertIn("⚠️ 4H 潜在背离 (熔断锁定·未确认)", soxl_txt)
        self.assertNotIn("见底信号", soxl_txt)

        qqq_item = snap["tickers"]["QQQ"]
        self.assertEqual(qqq_item["sr_breach_state"], sre.STATE_ALL_RESISTANCE_BROKEN)
        qqq_emb = bot.build_check_embed_dict("qqq", snap)
        _assert_no_bare_hr(self, qqq_emb)
        qqq_txt = json.dumps(qqq_emb, ensure_ascii=False)
        self.assertIn("已突破今日全部原定阻力", qqq_txt)
        self.assertIn("移动止盈", qqq_txt)


if __name__ == "__main__":
    unittest.main()
