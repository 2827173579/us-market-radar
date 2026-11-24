# -*- coding: utf-8 -*-
"""
V5.2 单元测试套件：
1. Part A 六项盘中极端行情不变量（概率归一化 <=100%、4H 硬门禁、离群 R2 收口、回踩支撑不低于止损线、贴近 R1 文案与反向 ETF 话术）
2. Part B 第 80 只正式标的 KOSPI (^KS11) 全链路不变量（80 标的池、0 Finnhub 消耗、跨时区缓存防误触、非美股时段优雅降级、全链路零 $ 点数格式）
全程使用 TemporaryDirectory 隔离，100% 零实盘账本读写。
"""
import json
import os
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import patch

import numpy as np
import pandas as pd

import ai_commentary_engine as ace
import create_futu_terminal as cft
import discord_radar_bot as bot
import integrated_market_radar as imr
import intraday_path_predictor as ipp
import market_temperature_engine as mte
import ml_predictor_5y as mlp
import sr_anchor_engine as sre


class TestV52SoxsPolishAndKospi(unittest.TestCase):
    def test_01_probability_normalization_never_exceeds_100(self):
        # SOXS 实盘案例：48.4% + 61.2% = 109.6% -> 等比缩放后 <= 100.0% 且空头仍 > 多头
        pl, ps = mlp.normalize_dual_probabilities(48.4, 61.2)
        self.assertLessEqual(round(pl + ps, 1), 100.0)
        self.assertGreater(ps, pl)
        self.assertAlmostEqual(ps / pl, 61.2 / 48.4, delta=0.03)

        # 原始多空分：59.0% + 60.0% = 119.0% -> <= 100.0% 且空头仍 >= 多头
        rl, rs = cft.normalize_dual_probabilities(59.0, 60.0)
        self.assertLessEqual(round(rl + rs, 1), 100.0)
        self.assertGreaterEqual(rs, rl)

        # 未超 100% 时保持原值不变
        nl, ns = mlp.normalize_dual_probabilities(44.1, 51.7)
        self.assertEqual((nl, ns), (44.1, 51.7))

    def test_02_four_h_hard_gate_blocks_bull_resonance_and_bull_scripts(self):
        # 1) SOXS 暴跌 -40.95% / 跌破 EMA5 / 反向衰减：绝不允许显示 "⚡ 4H 均线多头共振"
        sup = sre.suppress_resonance_badge(
            "⚡ 4H 均线多头共振",
            "div-trend",
            is_crash_halt=False,
            is_cooling_halt=False,
            vix_regime={"vix_hard_brake": False, "vix_term_ratio": 0.95},
            is_daily_breakdown=True,
            is_inverse_decay_veto=True,
            is_bear_dominant=True,
        )
        self.assertTrue(sup["resonance_suppressed"])
        self.assertEqual(sup["four_h_tag"], "div-suppressed")
        self.assertNotIn("多头共振", sup["resonance_badge_text"])

        # 2) SOXS 处于 INTACT 区间但 bias_ma50 > 4.0 且受破位/反向衰减压制：绝不喊【右侧主升剧本】
        anchor_soxs = {
            "anchor_s1": 45.30,
            "anchor_s2": 44.94,
            "anchor_sl": 44.27,
            "anchor_r1": 50.00,
            "anchor_r2": 61.21,
        }
        ev_soxs = sre.evaluate_sr_breach_state(
            price=49.11,
            anchor=anchor_soxs,
            bias_ma50=8.5,
            vwap=49.80,
            base_script="【右侧主升剧本】依托波段支撑 S1 ($45.30) 逢低做多，上看阻力 R1 ($50.00) 与突破扩展目标 R2 ($61.21)；关键防守位 $44.27",
            bear_suppressed=True,
            swing_tp=54.70,
            swing_sl=47.36,
            atr_pct=12.0,
            is_daily_breakdown=True,
            is_inverse_decay=True,
            ticker="SOXS",
        )
        self.assertNotIn("右侧主升剧本", ev_soxs["scenario_script"])
        self.assertNotIn("做多", ev_soxs["scenario_script"])
        self.assertIn("压制", ev_soxs["scenario_script"])
        self.assertIn("47.36", ev_soxs["scenario_script"])
        self.assertIn("不可跌破上方止损线硬扛", ev_soxs["scenario_script"])

        # 3) QQQ 跳空越过 R1 ($625) 但 VIX 倒挂压制 + R2 ($762.86) 异常偏远：禁止喊【R1突破转支撑 · 看高R2】
        ev_qqq = sre.evaluate_sr_breach_state(
            price=650.85,
            anchor={"anchor_s1": 600.0, "anchor_s2": 581.07, "anchor_sl": 572.0, "anchor_r1": 625.0, "anchor_r2": 762.86},
            bias_ma50=2.0,
            vwap=650.53,
            bear_suppressed=True,
            swing_tp=679.89,
            swing_sl=630.73,
            atr_pct=1.8,
            ticker="QQQ",
        )
        self.assertEqual(ev_qqq["sr_breach_state"], "R1_BROKEN")
        self.assertTrue(ev_qqq["r2_is_far_historical"])
        self.assertNotIn("看高R2", ev_qqq["scenario_script"])
        self.assertIn("远期前高参考", ev_qqq["scenario_script"])

    def test_03_support_pullback_never_below_swing_sl_in_bull_regime(self):
        # 正常多头行情下若旧静态 S1 ($45.30) 或原 R1 ($625.00) 低于波段止损 swing_sl，绝不推荐跌破止损去接回踩
        ev_intact = sre.evaluate_sr_breach_state(
            price=49.50,
            anchor={"anchor_s1": 45.30, "anchor_s2": 44.00, "anchor_sl": 43.20, "anchor_r1": 52.00, "anchor_r2": 55.00},
            bias_ma50=5.5,
            vwap=48.80,
            bear_suppressed=False,
            swing_tp=53.00,
            swing_sl=47.36,
            atr_pct=4.0,
            ticker="NVDA",
        )
        self.assertIn("47.36", ev_intact["scenario_script"])
        self.assertIn("仅为破位后次级参考", ev_intact["scenario_script"])
        self.assertNotIn("关键防守位 $43.20", ev_intact["scenario_script"])

        ev_r1_bull = sre.evaluate_sr_breach_state(
            price=651.0,
            anchor={"anchor_s1": 600.0, "anchor_s2": 581.0, "anchor_sl": 572.0, "anchor_r1": 625.0, "anchor_r2": 690.0},
            bias_ma50=3.5,
            vwap=650.5,
            bear_suppressed=False,
            swing_tp=680.0,
            swing_sl=630.73,
            atr_pct=2.2,
            ticker="QQQ",
        )
        self.assertNotIn("等回踩原 R1 ($625.00)", ev_r1_bull["scenario_script"])
        self.assertIn("630.73", ev_r1_bull["scenario_script"])

    def test_04_near_r1_vs_broken_r1_and_inverse_etf_wording(self):
        # SOXS $49.98 vs R1 $50.00 (未真突破，仅因 < 1.005 贴近切换动态目标) -> 必须写 "贴近R1"，禁止抢跑写 "已破R1"
        near_res = cft.resolve_long_tp_sl_ordering_guard(
            active_price=49.98,
            entry_price=0.0,
            raw_tp=50.00,
            r1=50.00,
            r2=0.0,
            raw_sl=46.98,
            eff_atr_frac=0.06,
        )
        self.assertGreater(near_res["target_profit_price"], 49.98 * 1.005)
        self.assertIn("贴近R1", near_res["tp_upgrade_note"])
        self.assertNotIn("已破R1", near_res["tp_upgrade_note"])

        # 真正突破 R1 ($50.50 > $50.00) -> 写 "已破R1"
        brk_res = cft.resolve_long_tp_sl_ordering_guard(
            active_price=50.50,
            entry_price=0.0,
            raw_tp=50.00,
            r1=50.00,
            r2=56.00,
            raw_sl=47.00,
            eff_atr_frac=0.04,
        )
        self.assertEqual(brk_res["target_profit_price"], 56.00)
        self.assertIn("已破R1", brk_res["tp_upgrade_note"])

        # 反向 ETF (SOXS / SQQQ / VIXY) 严禁出现 "逢高反抽沽空/买Put" 或自我推荐 "建议买入SOXS对冲"
        for inv_tk in ("SOXS", "SQQQ", "VIXY"):
            for wr in (58.0, 68.5):
                act_ml, _ = mlp.get_short_action_directive(wr, is_crash_halt=False, rsi_14=48.0, ticker=inv_tk, is_etf=True)
                act_imr, _ = imr.get_short_action_directive(wr, is_crash_halt=False, rsi_14=48.0, ticker=inv_tk, is_etf=True)
                self.assertEqual(act_ml, "⚡ 逢高分批减仓/锁利（反向工具防损耗）")
                self.assertEqual(act_imr, "⚡ 逢高分批减仓/锁利（反向工具防损耗）")

    def test_05_kospi_80th_target_zero_finnhub_and_cross_tz_cache_guard(self):
        etfs = imr.MONITORED_TARGETS["ETFS"]
        stocks = imr.MONITORED_TARGETS["STOCKS"]
        self.assertEqual(len(etfs), 15)
        self.assertEqual(len(stocks), 65)
        self.assertEqual(len(etfs) + len(stocks), 80)
        self.assertIn("KOSPI", etfs)
        self.assertNotIn("^KS11", etfs)
        self.assertEqual(etfs["KOSPI"]["yf_symbol"], "^KS11")
        self.assertTrue(etfs["KOSPI"]["is_index"])
        self.assertEqual(etfs["KOSPI"]["price_unit"], "pts")

        # 纯美股温度计排除 KOSPI
        self.assertFalse(mte.is_pure_us_equity("KOSPI", etfs["KOSPI"]))
        self.assertFalse(mte.is_pure_us_equity("^KS11"))

        # 非美股时段盘中推演优雅降级
        ip_ks = ipp.compute_knn_intraday_corridor(
            ticker="KOSPI",
            df_tk=None,
            forecast_date=date(2026, 11, 24),
            prev_close=4920.0,
            anchor_open=5013.52,
        )
        self.assertEqual(ip_ks["status"], "non_us_session")
        self.assertIn("韩国交易时段 (09:00–15:30 KST)", ip_ks["status_message"])

        # 核心防坑：KOSPI 提前跨日（如美股在 2026-11-23，韩国已在 2026-11-24）绝不触发美股扩展池重下
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_pq = os.path.join(tmpdir, "ml_extended_panel.parquet")
            us_day = date(2026, 11, 23)
            kr_day = date(2026, 11, 24)

            core_syms = list(mlp.SECTOR_MAP.keys())
            macro_syms = list(mlp.MACRO_ETFS)
            raw_rows = []
            dts_us = pd.bdate_range("2026-09-01", "2026-11-23")
            for sym in dict.fromkeys(core_syms + macro_syms):
                for dt in dts_us:
                    raw_rows.append({
                        "Date": dt, "Ticker": sym, "Sector": mlp.SECTOR_MAP.get(sym, "Semiconductors"),
                        "Open": 100.0, "High": 102.0, "Low": 99.0, "Close": 101.0, "Volume": 1000000.0,
                    })
            raw_panel = pd.DataFrame(raw_rows)

            ext_rows = []
            for sym, (sec_k, _) in mlp.EXTENDED_RADAR_SECTOR_MAP.items():
                if sym in set(raw_panel["Ticker"].unique()):
                    continue
                sym_dts = list(dts_us) + ([pd.Timestamp(kr_day)] if sym == "KOSPI" else [])
                base_px = 5010.0 if sym == "KOSPI" else 80.0
                for dt in sym_dts:
                    ext_rows.append({
                        "Date": dt, "Ticker": sym, "Sector": sec_k,
                        "Open": base_px, "High": base_px * 1.01, "Low": base_px * 0.99, "Close": base_px, "Volume": 500000.0,
                    })
            pd.DataFrame(ext_rows).to_parquet(ext_pq, index=False)

            feat_core = mlp.build_feature_panel(raw_panel)
            latest_core = feat_core[feat_core["Ticker"].isin(mlp.TARGET_STOCKS)].sort_values("Date").groupby("Ticker").last().reset_index()

            with patch.object(mlp, "_get_latest_expected_trading_day", return_value=us_day), \
                 patch.object(mlp, "_get_latest_expected_krx_day", return_value=kr_day), \
                 patch.object(mlp, "_fetch_single_ticker") as mock_dl:
                full_df = mlp.build_extended_live_features_df(
                    raw_panel=raw_panel,
                    core_latest_stocks=latest_core,
                    ext_cache_file=ext_pq,
                    force_refresh=False,
                )
                self.assertEqual(mock_dl.call_count, 0, f"Cross-TZ KOSPI date must NOT trigger US ticker re-download! Calls: {mock_dl.call_args_list}")
                self.assertIn("KOSPI", set(full_df["Ticker"].values))

    def test_06_kospi_zero_dollar_sign_across_sr_discord_and_ai(self):
        # 1) sr_anchor_engine 点数无 $
        ev_ks = sre.evaluate_sr_breach_state(
            price=5013.52,
            anchor={"anchor_s1": 4910.00, "anchor_s2": 4820.00, "anchor_sl": 4760.00, "anchor_r1": 5080.00, "anchor_r2": 5160.00, "price_unit": "pts", "is_index": True},
            bias_ma50=2.5,
            vwap=5002.10,
            ticker="KOSPI",
            price_unit="pts",
        )
        self.assertNotIn("$", ev_ks["sr_status_badge"])
        self.assertNotIn("$", ev_ks["scenario_script"])
        self.assertIn("4,910.00", ev_ks["scenario_script"])
        self.assertIn("5,080.00", ev_ks["scenario_script"])

        ev_ks_r1 = sre.evaluate_sr_breach_state(
            price=5095.00,
            anchor={"anchor_s1": 4910.00, "anchor_s2": 4820.00, "anchor_sl": 4760.00, "anchor_r1": 5080.00, "anchor_r2": 5160.00, "price_unit": "pts", "is_index": True},
            bias_ma50=2.5,
            vwap=5002.10,
            ticker="KOSPI",
            price_unit="pts",
        )
        self.assertNotIn("$", ev_ks_r1["sr_status_badge"])
        self.assertNotIn("$", ev_ks_r1["scenario_script"])
        self.assertIn("5,002.10", ev_ks_r1["scenario_script"])

        # 2) Discord /check KOSPI 零 $ 且含韩国时段说明
        mock_snap = {
            "updated_at_et": "2026-11-23 22:30:00",
            "session_code": "OVERNIGHT",
            "session_name": "美股夜盘",
            "weather": "多云防守",
            "market_regime": "NEUTRAL_CHOP",
            "tickers": {
                "KOSPI": {
                    "ticker": "KOSPI",
                    "name": "韩国综合指数",
                    "cat": "亚太指数",
                    "is_index": True,
                    "price_unit": "pts",
                    "krx_session_label": "韩盘交易中 (09:00–15:30 KST)",
                    "price": 5013.52,
                    "last": 5013.52,
                    "pct_change": 2.73,
                    "effective_pct": 2.73,
                    "vwap": 5001.20,
                    "vwap_diff_pct": 0.25,
                    "rs_20d": 3.4,
                    "ai_long_win_rate_pct": 54.0,
                    "decision_stance": "BULL",
                    "optimal_limit": 4965.00,
                    "entry_stop_loss": 4880.00,
                    "target_profit_price": 5120.00,
                    "anchor_s1": 4920.00,
                    "anchor_s2": 4840.00,
                    "anchor_r1": 5080.00,
                    "anchor_r2": 5160.00,
                    "sr_breach_state": "INTACT",
                }
            },
        }
        emb = bot.build_check_embed_dict("韩国综指", mock_snap)
        emb_json = json.dumps(emb, ensure_ascii=False)
        self.assertIn("KOSPI", emb["title"])
        self.assertIn("韩盘交易中", emb_json)
        self.assertNotIn("$", emb_json, f"Discord /check KOSPI leaked '$' sign: {emb_json}")

        # 3) AI 解盘 /ai target=KOSPI 净化任何残留 $ 符号
        with tempfile.TemporaryDirectory() as td:
            cpath = os.path.join(td, "ai_cache.json")
            def fake_post(url, headers, payload, timeout):
                return 200, json.dumps({
                    "choices": [{"message": {"content": "【一句话定性】KOSPI 现报 $5,013.52，站上 VWAP $5,001.20。\n【关键多空分水岭】下方支撑 S1 $4,920.00，上方阻力 R1 $5,080.00。\n【开盘/盘中条件剧本】如果守住 5,001.20 就看 5,080.00；否则回踩 4,920.00 观察。"}}]
                })
            res = ace.generate_single_brief(
                target="KOSPI",
                snapshot=mock_snap,
                provider="deepseek",
                env_override={"DEEPSEEK_API_KEY": "test_dummy_key_not_real"},
                http_post_fn=fake_post,
                cache_path=cpath,
            )
            self.assertEqual(res["status"], "OK")
            self.assertNotIn("$", res["text"])


if __name__ == "__main__":
    unittest.main()
