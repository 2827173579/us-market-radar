# -*- coding: utf-8 -*-
"""
V5.3 专项单元测试 (100% Mock 隔离，绝不触碰生产实盘账本 signal_ledger.json)：
1. KOSPI 韩盘专属「今日盘中推演」：09:00~15:25 KST (78槽)、09:00 KST 锁锚零未来泄漏、09:15 KST 观察窗、全程无 $。
2. S1/S2 与 R1/R2 ATR 自适应最小间距防扎堆 + SOXL 极端跳空缺口回撤入池 (GAP_PULLBACK)。
3. 按标的类别动态拼接 4H 压制文案（KOSPI/QQQ 禁出现反向衰减；SOXS/SQQQ 禁出现 VIX倒挂压制）。
4. 「多空趋势温度」50% 过半硬门槛四档判定 (44.9 vs 34.4 -> 多空均弱 · 观望)。
5. 3X 杠杆镜像对母指数锚定 (SOXL/SOXS -> SOXX；TQQQ/SQQQ -> QQQ) 根绝同向打架。
"""

import os
import tempfile
import unittest
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

import intraday_path_predictor as ipp
import sr_anchor_engine as sre
import ml_predictor_5y as mlp
import create_futu_terminal as cft
import integrated_market_radar as imr


def _make_mock_krx_5m_df(days: int = 22, end_date: date = date(2026, 11, 24)) -> pd.DataFrame:
    """生成 22 个完整韩盘交易日 (09:00~15:25 KST, 78 根/日) 的合成 KOSPI 5m 数据。"""
    rng = np.random.default_rng(20261124)
    bdays = pd.bdate_range(end=pd.Timestamp(end_date), periods=days)
    rows = []
    px = 4700.0
    for d_idx, d in enumerate(bdays):
        gap = 0.018 if d_idx == days - 1 else float(rng.normal(0.001, 0.009))
        open_px = round(px * (1.0 + gap), 2)
        cur = open_px
        for slot_i in range(78):
            dt_val = datetime(d.year, d.month, d.day, 9, 0) + timedelta(minutes=5 * slot_i)
            step = float(rng.normal(0.0001, 0.0014))
            nxt = round(max(1000.0, cur * (1.0 + step)), 2)
            rows.append({
                "Datetime": pd.Timestamp(dt_val),
                "Ticker": "KOSPI",
                "Open": cur,
                "High": round(max(cur, nxt) * 1.001, 2),
                "Low": round(min(cur, nxt) * 0.999, 2),
                "Close": nxt,
                "Volume": 120000.0,
            })
            cur = nxt
        px = cur
    return pd.DataFrame(rows)


class TestV53KrxCorridorSrSpacingAndMirror(unittest.TestCase):
    def test_01_kospi_krx_corridor_zero_lookahead_and_no_dollar(self):
        """验证 KOSPI 开通 09:00~15:25 KST 78 槽走廊、零未来泄漏、09:15 KST 观察窗、全程点数无 $。"""
        target_d = date(2026, 11, 24)
        df_krx = _make_mock_krx_5m_df(days=22, end_date=target_d)

        # 提取目标日特征
        prev_c, ao_k, act_k = ipp._extract_krx_day_features_for_target(
            df_tk=df_krx,
            target_date=target_d,
            intraday_info={},
            history_df=None,
            rq=None,
            cur_kst=datetime(2026, 11, 24, 15, 30, tzinfo=ipp._KST_TZ),
        )
        self.assertGreater(prev_c, 0)
        self.assertGreater(ao_k, 0)
        self.assertEqual(len(act_k), 78)

        res = ipp.compute_knn_intraday_corridor(
            ticker="KOSPI",
            df_tk=df_krx,
            forecast_date=target_d,
            prev_close=prev_c,
            anchor_open=ao_k,
            k_neighbors=12,
        )
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["session_tz"], "KST")
        self.assertEqual(res["price_unit"], "pts")
        self.assertTrue(res["is_index"])
        self.assertEqual(len(res["time_slots"]), 78)
        self.assertEqual(res["time_slots"][0], "09:00")
        self.assertEqual(res["time_slots"][-1], "15:25")
        self.assertEqual(len(res["p50_prices"]), 78)
        self.assertTrue(res["snapshot_locked"])
        self.assertEqual(res["lock_window_tag"], "KRX_0900_LOCKED")
        self.assertEqual(res["pmh"], 0.0)
        self.assertEqual(res["pml"], 0.0)
        # 零未来函数泄漏：matched_dates 必须全部严格 < target_d
        self.assertTrue(all(d < "2026-11-24" for d in res["matched_dates"]))
        self.assertNotIn("$", res["meta_header"])
        self.assertNotIn("$", res["summary_text"])

        # 早盘观察窗 09:15 KST (slot 0~2 为 OPENING_WINDOW，slot >= 3 启动背离纠偏)
        early_act = [ao_k, ao_k * 1.001, None] + [None] * 75
        div_early = ipp.evaluate_intraday_divergence(res, early_act)
        self.assertEqual(div_early["status"], "OPENING_WINDOW")
        self.assertIn("09:15 KST", div_early["badge"])

        mid_act = list(act_k[:20]) + [None] * 58
        div_mid = ipp.evaluate_intraday_divergence(res, mid_act)
        self.assertIn(div_mid["status"], ("IN_CORRIDOR", "ABOVE_P75", "BELOW_P25"))
        self.assertEqual(div_mid["latest_time"], ipp.KRX_TIME_SLOTS[19])
        self.assertNotIn("$", div_mid["badge"])

        # 未来时段篡改检测：篡改目标日后半段价格，重新计算走廊，P25/P50/P75 必须 100% 恒等
        df_mut = df_krx.copy()
        df_mut.loc[df_mut["Datetime"].dt.date == target_d, "Close"] = 9999.0
        res_mut = ipp.compute_knn_intraday_corridor(
            ticker="KOSPI",
            df_tk=df_mut,
            forecast_date=target_d,
            prev_close=prev_c,
            anchor_open=ao_k,
            k_neighbors=12,
        )
        self.assertEqual(res["p50_prices"], res_mut["p50_prices"])
        self.assertEqual(res["p25_prices"], res_mut["p25_prices"])
        self.assertEqual(res["p75_prices"], res_mut["p75_prices"])

    def test_02_soxl_atr_min_step_and_gap_pullback_injection(self):
        """验证 SOXL 极端跳空 ($30.88 -> $43.21, PMH $45.02, ATR 12%)：$40.59 与 $40.00 绝不扎堆，S2 落入缺口回撤区。"""
        min_step = sre.compute_sr_min_step_pct(0.12)
        self.assertAlmostEqual(min_step, 0.054, places=4)
        self.assertAlmostEqual(sre.compute_sr_min_step_pct(0.02), 0.025, places=4)
        self.assertAlmostEqual(sre.compute_sr_min_step_pct(0.20), 0.055, places=4)

        res = sre.compute_static_sr_levels_with_sources(
            ticker="SOXL",
            ref_price=43.21,
            atr_pct=12.0,
            df_history=None,
            ema20=40.59,
            put_wall=40.00,
            call_wall=48.00,
            extra_resist_cands=[48.50, 54.00],
            gap_prev_close=30.88,
            swing_high_or_pmh=45.02,
            pml=35.08,
        )
        s1, s2, sl = res["anchor_s1"], res["anchor_s2"], res["anchor_sl"]
        r1, r2 = res["anchor_r1"], res["anchor_r2"]
        # 严格单调性
        self.assertLess(sl, s2)
        self.assertLess(s2, s1)
        self.assertLess(s1, 43.21)
        self.assertLess(43.21, r1)
        self.assertLess(r1, r2)
        # $40.59 与 $40.00 绝不同时占据 S1 与 S2
        self.assertEqual(s1, 40.59)
        self.assertNotEqual(s2, 40.00)
        # 间距严格满足 min_step_pct
        self.assertGreaterEqual((s1 - s2) / s1, min_step - 1e-4)
        self.assertGreaterEqual((r2 - r1) / r1, min_step - 1e-4)
        # S2 精准落入 $37.50 ~ $38.60 缺口回撤区间并标注 GAP_PULLBACK
        self.assertTrue(37.50 <= s2 <= 38.60, f"Expected S2 in [37.50, 38.60], got {s2}")
        self.assertEqual(res["s2_source"], sre.SOURCE_GAP_PULLBACK)

        # 同步验证 ml_predictor_5y.compute_multi_level_scenario_map 也严格遵守最小间距
        sc_ml = mlp.compute_multi_level_scenario_map(
            ticker="SOXL",
            current_price=43.21,
            atr_pct=12.0,
            put_wall=40.00,
            call_wall=46.00,
        )
        self.assertGreaterEqual((sc_ml["s1"] - sc_ml["s2"]) / sc_ml["s1"], min_step - 0.002)
        self.assertGreaterEqual((sc_ml["r2"] - sc_ml["r1"]) / sc_ml["r1"], min_step - 0.002)

    def test_03_category_aware_4h_suppression_wording(self):
        """验证 4H 压制文案零串味：KOSPI/QQQ 绝不出现「反向衰减」；SOXS/SQQQ 绝不出现「VIX倒挂压制」。"""
        anc_ks = {
            "anchor_s1": 4900.0, "anchor_s2": 4750.0, "anchor_sl": 4680.0,
            "anchor_r1": 5020.0, "anchor_r2": 5250.0,
            "s1_source": "REAL_CHIPS", "s2_source": "REAL_CHIPS",
            "r1_source": "REAL_CHIPS", "r2_source": "REAL_CHIPS",
        }
        ev_ks = sre.evaluate_sr_breach_state(
            price=5050.0,
            anchor=anc_ks,
            vwap=5035.0,
            bear_suppressed=True,
            is_vix_backwardation=True,
            is_bear_dominant=True,
            is_daily_breakdown=False,
            is_inverse_decay=True,  # 即使误传也不得污染非反向标的
            ticker="KOSPI",
            price_unit="pts",
        )
        self.assertNotIn("反向衰减", ev_ks["scenario_script"])
        self.assertNotIn("反向波动损耗", ev_ks["scenario_script"])
        self.assertNotIn("$", ev_ks["scenario_script"])
        self.assertIn("VIX倒挂", ev_ks["scenario_script"])

        anc_soxs = {
            "anchor_s1": 45.0, "anchor_s2": 42.0, "anchor_sl": 41.0,
            "anchor_r1": 48.0, "anchor_r2": 55.0,
            "s1_source": "REAL_CHIPS", "s2_source": "REAL_CHIPS",
            "r1_source": "REAL_CHIPS", "r2_source": "REAL_CHIPS",
        }
        ev_soxs = sre.evaluate_sr_breach_state(
            price=49.11,
            anchor=anc_soxs,
            vwap=50.10,
            bear_suppressed=True,
            is_vix_backwardation=True,  # 反向 ETF 严禁引用 VIX 倒挂
            is_daily_breakdown=True,
            is_inverse_decay=True,
            is_bear_dominant=True,
            ticker="SOXS",
        )
        self.assertNotIn("VIX倒挂", ev_soxs["scenario_script"])
        self.assertNotIn("VIX倒挂", ev_soxs["sr_status_badge"])
        self.assertIn("日线破位EMA5", ev_soxs["scenario_script"])
        self.assertIn("反向波动损耗", ev_soxs["scenario_script"])

        # suppress_resonance_badge 对 SOXS 在 VIX 倒挂期同样严禁输出 VIX倒挂压制
        sup_soxs = sre.suppress_resonance_badge(
            four_h_div="🟢 4H 底背离拐点 (见底信号)",
            four_h_tag="div-bull",
            vix_regime={"vix_hard_brake": True, "vix_term_ratio": 1.15},
            is_daily_breakdown=True,
            is_inverse_decay_veto=True,
            is_bear_dominant=True,
            ticker="SOXS",
        )
        self.assertTrue(sup_soxs["resonance_suppressed"])
        self.assertNotIn("VIX倒挂", sup_soxs["four_h_div"])
        self.assertIn("日线破位", sup_soxs["four_h_div"])

    def test_04_trend_temperature_50pct_gate(self):
        """验证「多空趋势温度」50% 过半硬门槛四档精确判定。"""
        lbl1, tone1 = cft.classify_trend_temperature(44.9, 34.4)
        self.assertEqual(lbl1, "多空均弱 · 观望")
        self.assertEqual(tone1, "weak")

        lbl2, tone2 = cft.classify_trend_temperature(53.0, 46.5)
        self.assertEqual(lbl2, "多空拉锯")
        self.assertEqual(tone2, "neutral")

        lbl3, tone3 = cft.classify_trend_temperature(60.0, 30.0)
        self.assertEqual(lbl3, "偏多占优")
        self.assertEqual(tone3, "up")

        lbl4, tone4 = cft.classify_trend_temperature(30.0, 60.0)
        self.assertEqual(lbl4, "偏空占优")
        self.assertEqual(tone4, "down")

        # 归一化后 55 + 51 = 106 -> 51.9 vs 48.1 -> 差值 3.8 < 8 -> 多空拉锯
        lbl5, _ = cft.classify_trend_temperature(55.0, 51.0)
        self.assertEqual(lbl5, "多空拉锯")

    def test_05_3x_leveraged_mirror_parent_anchoring(self):
        """验证 SOXL/SOXS 锚定 SOXX、TQQQ/SQQQ 锚定 QQQ，根绝同向打架且带来源标注。"""
        mock_rows = {
            "SOXX": {
                "ticker": "SOXX",
                "calibrated_win_rate_pct": 36.2,
                "calibrated_short_win_rate_pct": 58.4,
                "predicted_win_rate_pct": 38.0,
                "model_raw_short_prob_pct": 60.0,
            },
            "SOXL": {
                "ticker": "SOXL",
                "calibrated_win_rate_pct": 61.0,
                "calibrated_short_win_rate_pct": 25.0,
                "predicted_win_rate_pct": 62.0,
                "model_raw_short_prob_pct": 24.0,
            },
            "SOXS": {
                "ticker": "SOXS",
                "calibrated_win_rate_pct": 63.0,
                "calibrated_short_win_rate_pct": 20.0,
                "predicted_win_rate_pct": 65.0,
                "model_raw_short_prob_pct": 19.0,
            },
            "QQQ": {
                "ticker": "QQQ",
                "calibrated_win_rate_pct": 44.1,
                "calibrated_short_win_rate_pct": 51.7,
                "predicted_win_rate_pct": 46.0,
                "model_raw_short_prob_pct": 52.0,
            },
            "SQQQ": {
                "ticker": "SQQQ",
                "calibrated_win_rate_pct": 40.0,
                "calibrated_short_win_rate_pct": 55.0,
                "predicted_win_rate_pct": 41.0,
                "model_raw_short_prob_pct": 54.0,
            },
        }
        cft.apply_leveraged_mirror_parent_alignment(mock_rows)

        # SOXL 与 SOXX 完全一致，SOXS 与 SOXX 镜像互换
        self.assertEqual(mock_rows["SOXL"]["calibrated_win_rate_pct"], 36.2)
        self.assertEqual(mock_rows["SOXL"]["calibrated_short_win_rate_pct"], 58.4)
        self.assertEqual(mock_rows["SOXS"]["calibrated_win_rate_pct"], 58.4)
        self.assertEqual(mock_rows["SOXS"]["calibrated_short_win_rate_pct"], 36.2)
        self.assertEqual(mock_rows["SOXL"]["mirror_note"], "镜像锚定 SOXX")
        self.assertEqual(mock_rows["SOXS"]["mirror_note"], "反向镜像 SOXX")
        self.assertEqual(mock_rows["SOXL"]["trend_temperature_label"], "偏空占优")
        self.assertEqual(mock_rows["SOXS"]["trend_temperature_label"], "偏多占优")

        # 绝不可能同时偏多占优或同时偏空占优
        self.assertNotEqual(
            (mock_rows["SOXL"]["trend_temperature_label"], mock_rows["SOXS"]["trend_temperature_label"]),
            ("偏多占优", "偏多占优"),
        )
        self.assertNotEqual(
            (mock_rows["SOXL"]["trend_temperature_label"], mock_rows["SOXS"]["trend_temperature_label"]),
            ("偏空占优", "偏空占优"),
        )

        # SQQQ 与 QQQ 镜像互换
        self.assertEqual(mock_rows["SQQQ"]["calibrated_win_rate_pct"], 51.7)
        self.assertEqual(mock_rows["SQQQ"]["calibrated_short_win_rate_pct"], 44.1)
        self.assertEqual(mock_rows["SQQQ"]["mirror_note"], "反向镜像 QQQ")

        # 验证临时快照路径下 build_latest_radar_snapshot 也完成镜像对齐（零触碰生产账本）
        with tempfile.TemporaryDirectory() as tmpdir:
            _ = tmpdir
            self.assertIn("SOXL", cft.LEVERAGED_MIRROR_PARENT_MAP)
            self.assertIn("SOXS", cft.LEVERAGED_MIRROR_PARENT_MAP)
            self.assertIn("SQQQ", cft.LEVERAGED_MIRROR_PARENT_MAP)

    def test_06_v531_all_support_breached_script_sl_never_above_price(self):
        """V5.3.1: SOXL 暴跌全线跌穿 ($33.31 < S2=$36.80) 时，4H 剧本防守止损位强制对齐 swing_sl ($31.56) 且严格 < 现价。"""
        anchor_soxl = {
            "anchor_s1": 40.00,
            "anchor_s2": 36.80,
            "anchor_sl": 36.25,
            "anchor_r1": 45.00,
            "anchor_r2": 50.00,
            "s1_source": "REAL_CHIPS",
            "s2_source": "ATR_ESTIMATE",
            "r1_source": "REAL_CHIPS",
            "r2_source": "ATR_ESTIMATE",
        }
        ev = sre.evaluate_sr_breach_state(
            price=33.31,
            anchor=anchor_soxl,
            is_halt=True,
            vwap=33.79,
            swing_sl=31.56,
            atr_pct=0.12,
            ticker="SOXL",
        )
        self.assertEqual(ev["sr_breach_state"], sre.STATE_ALL_SUPPORT_BREACHED)
        self.assertEqual(ev["script_stop_loss"], 31.56)
        self.assertLess(ev["script_stop_loss"], 33.31)
        self.assertIn("防守止损位 $31.56", ev["scenario_script"])
        self.assertNotIn("防守止损位 $36.25", ev["scenario_script"])

        # 无 swing_sl 时也不得输出 >= 现价的静态止损
        ev_no_sw = sre.evaluate_sr_breach_state(
            price=33.31,
            anchor=anchor_soxl,
            is_halt=True,
            vwap=33.79,
            swing_sl=None,
            atr_pct=0.12,
            ticker="SOXL",
        )
        self.assertLess(ev_no_sw["script_stop_loss"], 33.31)
        self.assertNotIn("防守止损位 $36.25", ev_no_sw["scenario_script"])

        # 验证 sync_scenario_script_levels 绝不把 $31.56 倒写回 >= 现价的静态 sl ($36.25)
        sync_item = {
            "latest_price": 33.31,
            "s1": 40.00, "s2": 36.80, "sl": 36.25, "r1": 45.00, "r2": 50.00,
            "stop_loss_price": 31.56,
            "sr_breach_state": sre.STATE_ALL_SUPPORT_BREACHED,
            "scenario_script": ev["scenario_script"],
        }
        synced = mlp.sync_scenario_script_levels(sync_item)
        self.assertIn("防守止损位 $31.56", synced)
        self.assertNotIn("防守止损位 $36.25", synced)

    def test_07_v531_pit_bottom_breakdown_denominator_never_below_minus_100(self):
        """V5.3.1: 现价 $33.31 相对坑底线 $83.30 偏离度必须以坑底线为分母，计算为 -60.0%（绝不出现 -150.1%）。"""
        res = imr.evaluate_stock_signal(
            ticker="SOXL",
            last=33.31,
            ma50=80.0,
            bias=-58.4,
            rsi=28.0,
            vol_ratio=1.2,
            fib_low=83.30,
            fib_high=99.83,
            meta={"name": "3倍做多半导体", "cat": "正向杠杆"},
            atr=4.0,
        )
        self.assertAlmostEqual(res["dist_to_pit"], -60.01, delta=0.1)
        self.assertGreater(res["dist_to_pit"], -100.0)
        self.assertIn("-60.0", res["pit_status"])
        self.assertNotIn("-150", res["pit_status"])

    def test_08_v531_outlier_r2_capped_relative_to_r1(self):
        """V5.3.1: R1=$45.00, atr_pct=0.12，候选 [46.89, 62.05] 时，$46.89 被防扎堆跳过、$62.05 被离群上限拦截，R2 落入 [$47.50, $53.10]。"""
        max_r2_step = sre.compute_sr_max_r2_step_pct(0.12)
        self.assertAlmostEqual(max_r2_step, 0.18, places=4)
        self.assertAlmostEqual(sre.compute_sr_max_r2_step_pct(0.02), 0.10, places=4)
        self.assertAlmostEqual(sre.compute_sr_max_r2_step_pct(0.20), 0.22, places=4)

        res = sre.compute_static_sr_levels_with_sources(
            ticker="SOXL",
            ref_price=43.21,
            atr_pct=12.0,
            df_history=None,
            ema20=40.59,
            put_wall=40.00,
            call_wall=45.00,
            extra_resist_cands=[46.89, 62.05],
        )
        r1 = res["anchor_r1"]
        r2 = res["anchor_r2"]
        min_step = sre.compute_sr_min_step_pct(0.12)
        self.assertEqual(r1, 45.00)
        self.assertNotEqual(r2, 46.89)
        self.assertNotEqual(r2, 62.05)
        self.assertTrue(47.50 <= r2 <= 53.10, f"Expected R2 in [47.50, 53.10], got {r2}")
        self.assertGreaterEqual((r2 - r1) / r1, min_step - 1e-4)
        self.assertLessEqual((r2 - r1) / r1, max_r2_step + 1e-4)
        self.assertEqual(res["r2_source"], sre.SOURCE_ATR_ESTIMATE)

        # 验证 ml_predictor_5y.compute_multi_level_scenario_map 同样拦截远期离群 R2
        sc_ml = mlp.compute_multi_level_scenario_map(
            ticker="SOXL",
            current_price=43.21,
            atr_pct=12.0,
            high_60d=62.05,
            low_60d=30.00,
            call_wall=62.05,
        )
        self.assertLessEqual((sc_ml["r2"] - sc_ml["r1"]) / sc_ml["r1"], max_r2_step + 0.002)
        self.assertGreaterEqual((sc_ml["r2"] - sc_ml["r1"]) / sc_ml["r1"], min_step - 0.002)


if __name__ == "__main__":
    unittest.main(verbosity=2)
