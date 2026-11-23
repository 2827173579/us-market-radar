# -*- coding: utf-8 -*-
"""
V4.4 综合升级全 Mock 专项单元测试 (test_v44_sr_anchor_and_plain_bot.py)

覆盖核心验收项：
1. 当日 S1/S2/R1/R2 静态锚定锁定后，模拟单边暴跌与单边暴涨验证 4 锚点绝不漂移，五态状态机精准演进；
2. ATR_ESTIMATE 取消绿色支撑样式，熔断 / VIX 倒挂期强制降级 4H 底背离绿标；
3. SOXS 高位止盈态绝不输出「参考挂单」，改为「别追高 · 分批止盈 + 利润保护线」；
4. /check 盘后/周末休市严格区分「盘后薄盘」与「正规盘收」；/top5 盘后/休市统一用正规盘收盘计算且严格紧凑 2 行防折行；
5. /vix 使用「高收益债偏好（跌 = 资金在躲风险）」，全部 4 命令均含「一句话结论」、零黑话、零裸 `---`、零写生产账本。
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
import signal_ledger
import sr_anchor_engine as sre


NY_TZ = ZoneInfo("America/New_York")

FORBIDDEN_JARGON = (
    "BEAR_DEFENSE",
    "P25-P75",
    "P25~P75",
    "偏离P50",
    "偏离 P50",
    "20日超额RS",
    "20日RS",
    "5m RSI",
    "14日 RSI",
    "MA50 偏离",
    "MA50偏离",
    "期限矩阵",
    "信用利差",
)


def _make_daily_df(end_date_str: str = "2026-11-19", n_days: int = 65, base_close: float = 50.0, mode: str = "normal") -> pd.DataFrame:
    end_d = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    dates = []
    cur = end_d - timedelta(days=n_days * 2)
    while len(dates) < n_days:
        if cur.weekday() < 5 and cur <= end_d:
            dates.append(cur)
        cur += timedelta(days=1)

    closes, highs, lows, vols = [], [], [], []
    for i in range(n_days):
        if mode == "normal":
            c = base_close + 3.0 * float(np.sin(i / 6.0)) if i < n_days - 1 else base_close
            h, l = c + 1.0, c - 1.0
        elif mode == "breakdown":
            c = 65.0 + 2.0 * float(np.sin(i / 5.0)) if i < n_days - 1 else base_close
            h, l = c + 1.0, (c - 0.8 if i < n_days - 1 else base_close - 0.2)
        else:
            c = 35.0 + 1.8 * float(np.sin(i / 5.0)) if i < n_days - 1 else base_close
            h, l = c + 0.8, c - 0.8
        closes.append(round(c, 2))
        highs.append(round(h, 2))
        lows.append(round(l, 2))
        vols.append(2_000_000.0 + (i % 5) * 150_000.0)
    return pd.DataFrame(
        {"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=pd.to_datetime(dates),
    )


def _assert_plain_clean_embed(tc: unittest.TestCase, emb_dict: dict, label: str = "") -> str:
    tc.assertIsInstance(emb_dict, dict, label)
    full_parts = [str(emb_dict.get("title") or ""), str(emb_dict.get("description") or "")]
    for key in ("title", "description"):
        for ln in str(emb_dict.get(key) or "").splitlines():
            tc.assertNotEqual(ln.strip(), "---", f"Bare '---' in {label} {key}")
    for fld in emb_dict.get("fields") or []:
        val = str(fld.get("value") or "")
        tc.assertTrue(len(val) > 0, f"Empty field value in {label}: {fld.get('name')}")
        tc.assertLessEqual(len(val), 1024, f"Field value > 1024 in {label}: {fld.get('name')}")
        for ln in val.splitlines():
            tc.assertNotEqual(ln.strip(), "---", f"Bare '---' in {label} field {fld.get('name')}")
        full_parts.append(str(fld.get("name") or ""))
        full_parts.append(val)

    # 验证可被 discord.py 无损序列化
    discord.Embed.from_dict(emb_dict)

    combined = "\n".join(full_parts)
    tc.assertIn("一句话结论", str(emb_dict.get("description") or ""), f"Missing 一句话结论 in {label}")
    for bad in FORBIDDEN_JARGON:
        tc.assertNotIn(bad, combined, f"Forbidden jargon {bad!r} found in {label}: {combined}")
    return combined


class TestV44SrAnchorAndPlainBot(unittest.TestCase):
    def test_01_static_anchors_no_drift_and_5_state_flip(self):
        hist_df = _make_daily_df("2026-11-19", n_days=65, base_close=50.0, mode="normal")
        with tempfile.TemporaryDirectory() as tmpdir:
            anc_path = os.path.join(tmpdir, "daily_sr_anchors.json")
            locked = sre.get_or_lock_daily_sr_anchor(
                ticker="NVDA", date_et="2026-11-20", prev_close=50.0, atr_pct=4.0, df_history=hist_df, anchors_path=anc_path
            )
            s2, s1, r1, r2 = locked["anchor_s2"], locked["anchor_s1"], locked["anchor_r1"], locked["anchor_r2"]
            self.assertTrue(0 < s2 < s1 < 50.0 < r1 < r2)

            # 模拟同日先暴跌至 35 又暴涨至 68，再次锁定时 4 个锚点绝不漂移
            for live_px in (35.0, 68.0):
                wild_df = pd.concat([
                    hist_df,
                    pd.DataFrame(
                        {"Open": [50.0], "High": [max(51.0, live_px)], "Low": [min(49.0, live_px)], "Close": [live_px], "Volume": [9e6]},
                        index=pd.to_datetime(["2026-11-20"]),
                    ),
                ])
                relocked = sre.get_or_lock_daily_sr_anchor(
                    ticker="NVDA", date_et="2026-11-20", prev_close=50.0, atr_pct=9.0, df_history=wild_df, anchors_path=anc_path
                )
                for k in ("anchor_s1", "anchor_s2", "anchor_r1", "anchor_r2", "s1_source", "s2_source", "r1_source", "r2_source"):
                    self.assertEqual(relocked[k], locked[k])

            self.assertEqual(sre.evaluate_sr_breach_state(50.0, locked)["sr_breach_state"], sre.STATE_INTACT)

            st_s1 = sre.evaluate_sr_breach_state(round((s1 + s2) / 2.0, 2), locked)
            self.assertEqual(st_s1["sr_breach_state"], sre.STATE_S1_BREACHED)
            self.assertIn("已击穿转压", st_s1["sr_status_badge"])

            st_all_sup = sre.evaluate_sr_breach_state(round(s2 - 2.0, 2), locked, is_halt=True, vwap=46.0)
            self.assertEqual(st_all_sup["sr_breach_state"], sre.STATE_ALL_SUPPORT_BREACHED)
            self.assertIn("今日原定支撑全线跌穿", st_all_sup["sr_status_badge"])
            self.assertIn("下方无真实支撑，别猜底", st_all_sup["sr_status_badge"])
            self.assertIn("今日原定支撑全线跌穿", st_all_sup["scenario_script"])

            st_r1 = sre.evaluate_sr_breach_state(round((r1 + r2) / 2.0, 2), locked, vwap=53.0)
            self.assertEqual(st_r1["sr_breach_state"], sre.STATE_R1_BROKEN)
            self.assertIn("已突破 ➔ 转为支撑", st_r1["sr_status_badge"])

            st_all_res = sre.evaluate_sr_breach_state(round(r2 + 3.0, 2), locked, vwap=58.0)
            self.assertEqual(st_all_res["sr_breach_state"], sre.STATE_ALL_RESISTANCE_BROKEN)
            self.assertIn("突破今日全线阻力", st_all_res["sr_status_badge"])
            self.assertIn("EMA9 移动止盈", st_all_res["scenario_script"])
            self.assertIn("VWAP", st_all_res["scenario_script"])

    def test_02_atr_estimate_styling_and_4h_suppression(self):
        bd_df = _make_daily_df("2026-11-19", n_days=65, base_close=38.0, mode="breakdown")
        bd_anc = sre.compute_static_sr_levels_with_sources("BDSTK", ref_price=38.0, atr_pct=4.5, df_history=bd_df)
        self.assertEqual(bd_anc["s1_source"], sre.SOURCE_ATR_ESTIMATE)
        self.assertEqual(bd_anc["s2_source"], sre.SOURCE_ATR_ESTIMATE)

        html_out = cft.build_futu_terminal_html(
            stocks_data=[{
                "ticker": "NVDA", "name": "NVIDIA", "sub": "芯片", "cat": "芯片算力",
                "last": 38.0, "active_price": 38.0, "pct_change": -7.5, "vwap": 40.0,
                "rsi": 29.0, "bias": -15.0, "atr": 1.9, "fib_low": 41.0, "fib_high": 45.0,
                "signal": "🔴 破位止损", "is_crash_halt": True, "is_stop": True,
            }],
            macro_data={"SPY": {"name": "标普", "cat": "大盘基准", "last": 580.0, "active_price": 580.0, "pct_change": -1.0, "vwap": 582.0}},
            weather_info={"weather": "暴雨空仓", "market_regime": "BEAR_DEFENSE", "vix_regime": {"vix_spot": 35.9, "vix_term_ratio": 1.16, "vix_state": "BACKWARDATION_BRAKE", "vix_hard_brake": True}},
            daily_sr_anchors={"NVDA": bd_anc},
        )
        # 确认 ATR_ESTIMATE 使用中性/琥珀样式并明确标注估算防线 (无真实筹码)
        self.assertIn("估算防线 (无真实筹码)", html_out)
        self.assertIn("今日原定支撑全线跌穿 (原S1:${s1v} / 原S2:${s2v}) · 下方无真实支撑，别猜底", html_out)
        self.assertNotIn("估算防线 (无真实筹码) S1:${s1v} / S2:${s2v}</span>` ? pal.up", html_out)

        # 熔断保护期与 VIX 倒挂期强制压制 4H 绿标
        h_sup = sre.suppress_resonance_badge("🟢 4H 底背离拐点 (见底信号)", "div-bull", is_crash_halt=True, vix_regime={"vix_hard_brake": False})
        self.assertEqual(h_sup["four_h_tag"], "div-suppressed")
        self.assertEqual(h_sup["four_h_div"], "⚠️ 4H 潜在背离 (熔断锁定·未确认)")
        self.assertNotIn("见底信号", h_sup["four_h_div"])

        v_sup = sre.suppress_resonance_badge("🟢 4H 底背离拐点 (见底信号)", "div-bull", is_crash_halt=False, vix_regime={"vix_hard_brake": True, "vix_term_ratio": 1.16})
        self.assertEqual(v_sup["four_h_tag"], "div-suppressed")
        self.assertEqual(v_sup["four_h_div"], "⚠️ 4H 潜在背离 (VIX倒挂压制)")

    def test_03_soxs_no_buy_limit_and_soxl_weekend_separation(self):
        macro = {
            "SOXS": {
                "name": "3倍做空半导体", "sub": "反向对冲", "cat": "反向对冲",
                "last": 83.16, "active_price": 76.50, "pct_change": 31.71,
                "ext_price": 76.50, "ext_pct": -8.01, "effective_pct": -8.01,
                "session_tag": "休市", "vwap": 75.48, "ema20": 69.50,
                "rs_20d": 72.14, "bias": 66.38, "rsi": 67.9,
                "is_inverse_hedge": True, "is_take_profit": True,
                "optimal_limit": 76.50, "entry_stop_loss": 66.89,
                "signal": "🟣 高位止盈",
            },
            "SOXL": {
                "name": "3倍做多半导体", "sub": "正向杠杆", "cat": "正向杠杆",
                "last": 30.88, "active_price": 33.37, "pct_change": -31.83,
                "ext_price": 33.37, "ext_pct": 8.06, "effective_pct": 8.06,
                "session_tag": "休市", "vwap": 35.74, "ema20": 38.00,
                "rs_20d": -49.42, "bias": -67.53, "rsi": 28.5,
                "is_crash_halt": True, "is_stop": True, "broken_bearish": True,
                "optimal_limit": 0.0, "entry_stop_loss": 0.0,
                "signal": "🔴 暴跌熔断",
            },
            "SPY": {
                "name": "标普500", "cat": "大盘基准",
                "last": 580.0, "active_price": 580.0, "pct_change": -1.20,
                "vwap": 581.0, "rs_20d": 0.0,
            },
        }
        anchors = {
            "SOXS": {
                "anchor_s1": 61.21, "anchor_s2": 58.28, "anchor_sl": 57.41,
                "anchor_r1": 66.90, "anchor_r2": 81.45,
                "s1_source": sre.SOURCE_REAL_CHIPS, "s2_source": sre.SOURCE_REAL_CHIPS,
                "r1_source": sre.SOURCE_ATR_ESTIMATE, "r2_source": sre.SOURCE_REAL_CHIPS,
            },
            "SOXL": {
                "anchor_s1": 42.92, "anchor_s2": 41.45, "anchor_sl": 40.83,
                "anchor_r1": 46.26, "anchor_r2": 47.05,
                "s1_source": sre.SOURCE_ATR_ESTIMATE, "s2_source": sre.SOURCE_ATR_ESTIMATE,
                "r1_source": sre.SOURCE_REAL_CHIPS, "r2_source": sre.SOURCE_REAL_CHIPS,
            },
        }
        snap = radar.build_latest_radar_snapshot(
            macro_data=macro,
            stocks_data=[],
            weather_info={"market_regime": "BEAR_DEFENSE", "market_regime_cn": "🔴 空头防守体制", "weather": "暴雨空仓"},
            vix_regime={
                "vix_spot": 35.89, "vix_pct": 26.2, "vxn_spot": 45.25, "vxn_vix_spread": 9.36,
                "vix3m_spot": 30.94, "vix_term_ratio": 1.16, "tnx_yield": 4.91,
                "vix_10d_peak": 35.89, "vix_drawdown_from_peak_pct": 0.0,
                "vix_state": "BACKWARDATION_BRAKE", "vix_hard_brake": True,
                "pill_text": "🔴 VIX 35.9 · 倒挂预警(1.16)",
                "advice_cn": "倒挂说明机构仍在抢买近期保护，大盘体制强制锁死 BEAR_DEFENSE、多头胜率封顶 48%。",
            },
            ai_predictions={"by_ticker": {
                "SOXS": {"calibrated_win_rate_pct": 38.7, "model_raw_prob_pct": 45.0, "calibrated_short_win_rate_pct": 38.9, "target_profit_price": 83.73, "four_h_div": "⚡ 4H 均线多头共振", "four_h_tag": "div-trend"},
                "SOXL": {"calibrated_win_rate_pct": 42.4, "model_raw_prob_pct": 81.4, "calibrated_short_win_rate_pct": 19.4, "target_profit_price": 36.52, "four_h_div": "🟢 4H 底背离拐点 (见底信号)", "four_h_tag": "div-bull"},
            }},
            intraday_forecasts={"by_ticker": {
                "SOXS": {"status": "ok", "pmh": 70.78, "pml": 66.15, "divergence": {"status": "ABOVE_P75"}},
                "SOXL": {"status": "ok", "pmh": 43.16, "pml": 39.81, "divergence": {"status": "BELOW_P25"}},
            }},
            session_code="WEEKEND",
            session_name="周末休市",
            now_str="2026-11-22 15:20:00",
            now_et=datetime(2026, 11, 22, 15, 20, tzinfo=NY_TZ),
            daily_sr_anchors=anchors,
        )

        # 验证 SOXS 高位止盈态绝不出现 "参考挂单" / 开仓止损
        soxs_emb = bot.build_check_embed_dict("SOXS", snap)
        soxs_txt = _assert_plain_clean_embed(self, soxs_emb, "check SOXS")
        self.assertNotIn("参考挂单", soxs_txt)
        self.assertNotIn("参考关注价", soxs_txt)
        self.assertIn("别追高", soxs_txt)
        self.assertIn("分批止盈", soxs_txt)
        self.assertIn("利润保护线", soxs_txt)
        self.assertIn("盘后 **$76.50**（盘后 -8.01%，休市薄盘别当真）｜正规盘收 **$83.16**（+31.71%）", soxs_txt)

        # 验证 SOXL 休市盘后薄盘 vs 正规盘收盘清晰隔离 + 全线跌穿 + 熔断压制 4H + AI 强制降档
        soxl_emb = bot.build_check_embed_dict("SOXL", snap)
        soxl_txt = _assert_plain_clean_embed(self, soxl_emb, "check SOXL")
        self.assertEqual(soxl_emb["color"], 0xEF4444)
        self.assertIn("盘后 **$33.37**（盘后 +8.06%，休市薄盘别当真）｜正规盘收 **$30.88**（-31.83%）", soxl_txt)
        self.assertIn("今日原定支撑已全线跌穿", soxl_txt)
        self.assertIn("下方无真实支撑，别猜底", soxl_txt)
        self.assertIn("（估算参考，无真实筹码）", soxl_txt)
        self.assertIn("昨日参考", soxl_txt)
        self.assertIn("系统已强制降档", soxl_txt)
        self.assertIn("⚠️ 4H 潜在背离 (熔断锁定·未确认)", soxl_txt)
        self.assertNotIn("见底信号", soxl_txt)
        self.assertNotIn("参考挂单", soxl_txt)

    def test_04_top5_post_market_weekend_rth_close_and_compact_2line(self):
        macro = {
            "SPY": {"ticker": "SPY", "name": "标普500", "cat": "大盘基准", "last": 580.0, "active_price": 588.0, "pct_change": -1.10, "ext_price": 588.0, "ext_pct": 1.38, "vwap": 581.0, "rs_20d": 0.0},
            "SOXS": {"ticker": "SOXS", "name": "3倍做空", "cat": "反向做空", "last": 83.0, "pct_change": 30.0, "rs_20d": 70.0, "is_inverse_hedge": True},
        }
        stocks = [
            # RTH 大跌 -4.5%、盘后薄盘虚涨 +9.0% 的诱多陷阱股，休市/盘后绝不能靠盘后虚涨混进前排
            {"ticker": "TRAP", "name": "盘后虚涨股", "cat": "云与软件", "last": 100.0, "active_price": 109.0, "pct_change": -4.50, "ext_price": 109.0, "ext_pct": 9.00, "vwap": 102.0, "rs_20d": 6.0},
            {"ticker": "WMT", "name": "沃尔玛", "cat": "传统消费", "last": 128.13, "active_price": 127.00, "pct_change": 0.14, "ext_price": 127.00, "ext_pct": -0.88, "vwap": 127.8, "rs_20d": 20.34},
            {"ticker": "LLY", "name": "礼来", "cat": "生物医药", "last": 1348.06, "active_price": 1340.00, "pct_change": 0.07, "ext_price": 1340.00, "ext_pct": -0.60, "vwap": 1346.0, "rs_20d": 19.53},
            {"ticker": "AMZN", "name": "亚马逊", "cat": "云与电商", "last": 251.00, "active_price": 250.00, "pct_change": 1.69, "ext_price": 250.00, "ext_pct": -0.40, "vwap": 249.0, "rs_20d": 13.15},
            {"ticker": "MRNA", "name": "Moderna", "cat": "生物医药", "last": 199.30, "active_price": 198.00, "pct_change": 2.21, "ext_price": 198.00, "ext_pct": -0.65, "vwap": 197.0, "rs_20d": 11.07},
            {"ticker": "V", "name": "Visa", "cat": "金融科技", "last": 404.80, "active_price": 402.00, "pct_change": 0.73, "ext_price": 402.00, "ext_pct": -0.69, "vwap": 403.0, "rs_20d": 12.32},
        ]

        for sess in ("POST_MARKET", "WEEKEND"):
            top5 = cft.compute_resilient_leaders_top5(stocks, macro_data=macro, session_code=sess, top_n=5)
            tks = [x["ticker"] for x in top5]
            self.assertEqual(len(tks), 5)
            self.assertNotIn("TRAP", tks, f"{sess} 严禁用盘后虚涨覆盖正规盘跌幅导致伪龙头混入 Top5")
            self.assertNotIn("SOXS", tks)
            wmt = next(x for x in top5 if x["ticker"] == "WMT")
            self.assertAlmostEqual(wmt["pct"], 0.14, places=2)
            # WMT 正规盘 +0.14% 相对 SPY 正规盘 -1.10% 应为 +1.24%（不能被盘后虚弹 SPY +1.38% 扭曲）
            self.assertAlmostEqual(wmt["excess_vs_spy"], 1.24, places=2)

        snap = radar.build_latest_radar_snapshot(
            macro_data=macro,
            stocks_data=stocks,
            weather_info={"market_regime": "BEAR_DEFENSE", "weather": "暴雨空仓"},
            vix_regime={"vix_spot": 35.9, "vix_term_ratio": 1.16, "vix_state": "BACKWARDATION_BRAKE", "vix_hard_brake": True},
            session_code="WEEKEND",
            session_name="周末休市",
            now_str="2026-11-22 15:23:00",
            now_et=datetime(2026, 11, 22, 15, 23, tzinfo=NY_TZ),
            ai_predictions={},
            intraday_forecasts={},
            daily_sr_anchors={},
        )
        top5_emb = bot.build_top5_embed_dict(snap)
        top5_txt = _assert_plain_clean_embed(self, top5_emb, "top5 WEEKEND")
        desc = top5_emb["description"]
        self.assertIn("收 $128.13 (+0.14%)", desc)
        self.assertNotIn("$127.00", desc)
        self.assertNotIn("✓", desc)
        # 检查每只票严格 2 行紧凑排版
        blocks = [b for b in desc.split("\n\n")[1:] if b.strip()]
        self.assertEqual(len(blocks), 5)
        for blk in blocks:
            self.assertEqual(len(blk.splitlines()), 2, f"Expected strictly 2 lines per /top5 entry: {blk!r}")

        # 验证 JS computeResilientLeadersTop5 也包含 POST_MARKET 与 WEEKEND 走正规盘 pct_change
        html_out = cft.build_futu_terminal_html(
            stocks_data=stocks,
            macro_data=macro,
            weather_info={"weather": "暴雨空仓", "market_regime": "BEAR_DEFENSE", "vix_regime": {"vix_state": "BACKWARDATION_BRAKE", "vix_spot": 35.9}},
            session_code="WEEKEND",
        )
        self.assertIn('SESSION_CODE === "REGULAR" || SESSION_CODE === "POST_MARKET" || SESSION_CODE === "WEEKEND"', html_out)

    def test_05_vix_and_all_four_commands_plain_chinese_hygiene(self):
        snap = radar.build_latest_radar_snapshot(
            macro_data={
                "SPY": {"name": "标普500", "cat": "大盘基准", "last": 580.0, "active_price": 580.0, "pct_change": -1.1, "vwap": 581.0, "rs_20d": 0.0},
                "QQQ": {"name": "纳指100ETF", "cat": "大盘基准", "last": 500.0, "active_price": 500.0, "pct_change": -1.5, "vwap": 499.0, "rs_20d": 2.5, "is_pit": True, "optimal_limit": 498.0, "entry_stop_loss": 485.0},
            },
            stocks_data=[
                {"ticker": "WMT", "name": "沃尔玛", "cat": "传统消费", "last": 128.13, "active_price": 128.13, "pct_change": 0.14, "vwap": 127.5, "rs_20d": 20.3},
            ],
            weather_info={"market_regime": "BEAR_DEFENSE", "weather": "暴雨空仓"},
            vix_regime={
                "vix_spot": 35.9, "vix_pct": 26.2, "vxn_spot": 45.3, "vxn_vix_spread": 9.4,
                "vix3m_spot": 30.9, "vix_term_ratio": 1.16, "tnx_yield": 4.91,
                "vix_10d_peak": 35.9, "vix_drawdown_from_peak_pct": 0.0,
                "vix_state": "BACKWARDATION_BRAKE", "vix_hard_brake": True,
                "advice_cn": "倒挂锁死 BEAR_DEFENSE",
            },
            cross_market_liquidity={"hyg_ief_ratio": 0.8328, "hyg_ief_5d_pct": -2.54, "dxy_spot": 103.81, "dxy_5d_pct": 0.39, "usdjpy_spot": 159.71, "usdjpy_5d_pct": -0.11},
            opex_shield={"active": False, "next_opex_date": "2026-12-18", "next_is_quad_witching": True, "days_to_opex": 26},
            ai_predictions={"by_ticker": {"QQQ": {"calibrated_win_rate_pct": 46.0, "model_raw_prob_pct": 62.0, "target_profit_price": 520.0}}},
            intraday_forecasts={"by_ticker": {}},
            session_code="WEEKEND",
            now_str="2026-11-22 15:25:00",
            daily_sr_anchors={},
        )

        # /vix 验证
        vix_emb = bot.build_vix_embed_dict(snap)
        vix_txt = _assert_plain_clean_embed(self, vix_emb, "vix")
        self.assertIn("高收益债偏好（跌 = 资金在躲风险）", vix_txt)
        self.assertIn("期权到期日提醒", vix_txt)
        self.assertIn("26 天", vix_txt)
        self.assertIn("红灯停", vix_txt)

        # /check 正常多头 & 未知代码 & 空快照
        _assert_plain_clean_embed(self, bot.build_check_embed_dict("QQQ", snap), "check QQQ")
        _assert_plain_clean_embed(self, bot.build_check_embed_dict("NOPE", snap), "check unknown")
        _assert_plain_clean_embed(self, bot.build_check_embed_dict("NVDA", None), "check none")
        _assert_plain_clean_embed(self, bot.build_vix_embed_dict(None), "vix none")
        _assert_plain_clean_embed(self, bot.build_top5_embed_dict(snap), "top5 populated")
        _assert_plain_clean_embed(self, bot.build_top5_embed_dict({"resilient_leaders_top5": []}), "top5 empty")

        # /ledger 空态与有数据态（严格临时目录隔离，不接触生产 data_cache/signal_ledger.json）
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_ledger = os.path.join(tmpdir, "signal_ledger.json")
            empty_l = bot.build_ledger_embed_dict(ledger_path=temp_ledger, snapshot=snap)
            empty_l_txt = _assert_plain_clean_embed(self, empty_l, "ledger empty")
            self.assertIn("账本已就绪，从下周第一条实盘提醒开始自动记账复盘", empty_l_txt)
            self.assertFalse(os.path.exists(temp_ledger))

            t_et = datetime(2026, 11, 20, 10, 15, tzinfo=NY_TZ)
            signal_ledger.record_signal("INTRADAY_5M_BUY", "NVDA", 140.0, timestamp_et=t_et, ledger_path=temp_ledger, allow_when_disabled=True)
            signal_ledger.record_signal("VIX_BACKWARDATION_BRAKE", "SPY", 600.0, timestamp_et=t_et, ledger_path=temp_ledger, allow_when_disabled=True)
            doc = signal_ledger.load_ledger(temp_ledger)
            doc["signals"][0]["eval"].update({"ret_eod_pct": 2.5, "ret_t3_pct": 4.2, "mae_pct": -0.4})
            doc["signals"][0]["status"] = "COMPLETE"
            doc["signals"][1]["eval"].update({"ret_eod_pct": -2.0, "ret_t3_pct": -3.1, "mae_pct": -2.5})
            doc["signals"][1]["status"] = "COMPLETE"
            signal_ledger.save_ledger(doc, temp_ledger)

            pop_l = bot.build_ledger_embed_dict(ledger_path=temp_ledger, snapshot=snap)
            pop_l_txt = _assert_plain_clean_embed(self, pop_l, "ledger populated")
            self.assertIn("做多提醒准不准", pop_l_txt)
            self.assertIn("提醒别抄底拦得准不准", pop_l_txt)

    def test_06_gap_up_breakout_tp_sl_ordering_guard(self):
        """回归验证：大幅高开突破原 R1/R2 后，目标止盈严格 > max(active_price, entry_price) 且绝不出现负百分比。"""
        import create_futu_terminal as futu
        # 1) SOXL 实盘复现：现价 $44.82，参考买点 $43.47，静态 R1=$32.72、R2=$40.00 已被全线突破
        res_all_broken = futu.resolve_long_tp_sl_ordering_guard(
            active_price=44.82,
            entry_price=43.47,
            raw_tp=32.72,
            r1=32.72,
            r2=40.00,
            raw_sl=42.77,
            eff_atr_frac=0.06,
        )
        self.assertGreater(res_all_broken["target_profit_price"], 44.82)
        self.assertGreater(res_all_broken["target_profit_pct"], 0.0)
        self.assertEqual(res_all_broken["tp_upgrade_note"], "已破R1 · 上移至动态目标")
        self.assertLess(res_all_broken["stop_loss_price"], 43.47)

        # 2) 仅突破 R1 ($32.72)，R2 ($40.00) 仍有效 -> 自动上移至 R2 ($40.00)
        res_r1_broken = futu.resolve_long_tp_sl_ordering_guard(
            active_price=35.00,
            entry_price=34.20,
            raw_tp=32.72,
            r1=32.72,
            r2=40.00,
            raw_sl=32.00,
            eff_atr_frac=0.05,
        )
        self.assertEqual(res_r1_broken["target_profit_price"], 40.00)
        self.assertGreater(res_r1_broken["target_profit_pct"], 0.0)
        self.assertEqual(res_r1_broken["tp_upgrade_note"], "已破R1 · 上移至R2")

        # 3) 跳空低开跌破原止损 -> 自动下移止损并标注 '已破原止损 · 现价防守'
        res_sl_broken = futu.resolve_long_tp_sl_ordering_guard(
            active_price=28.00,
            entry_price=28.50,
            raw_tp=32.72,
            r1=32.72,
            r2=40.00,
            raw_sl=29.50,
            eff_atr_frac=0.05,
        )
        self.assertLess(res_sl_broken["stop_loss_price"], 28.00)
        self.assertEqual(res_sl_broken["sl_upgrade_note"], "已破原止损 · 现价防守")


if __name__ == "__main__":
    unittest.main()
