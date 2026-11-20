# -*- coding: utf-8 -*-
"""
阶段三专项离线回归测试 (test_phase3_hygiene_consistency.py)
覆盖全部 5 项扫雷与口径统一修复：
1. Git 瘦身：.gitignore 忽略 intraday_5m_60d.parquet，Git 索引不再追踪该 14.6MB 二进制文件；
2. 5m 缓存合并防拆股尾巴：单票拉取成功时整票覆盖旧缓存 + 合并层 ±40% 拆股跳变自愈；
3. 点位与止损口径统一：剧本正文 S1/S2/R1/R2 与表头 100% 精确一致，止损无异常倒挂/极端背离；
4. 清除盘前假占位与 0.0% 强 Bug：无 5m 数据不输出 50.0/1.00x 假值，RS=0.0% 输出中性而非强；
5. Windows 缺字方框清理：全仓源码与产出 HTML 彻底清零 🪙 (U+1FA99)。
"""
import json
import os
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import integrated_market_radar as radar
import intraday_path_predictor as ipp
import ml_predictor_5y as mlp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class TestPhase3HygieneConsistency(unittest.TestCase):
    # ------------------------------------------------------------------
    # 1. Git 仓库瘦身校验
    # ------------------------------------------------------------------
    def test_01_gitignore_and_git_index_slimming(self):
        gi_path = os.path.join(BASE_DIR, ".gitignore")
        self.assertTrue(os.path.exists(gi_path))
        gi_text = open(gi_path, "r", encoding="utf-8").read()
        self.assertIn("data_cache/intraday_5m_60d.parquet", gi_text)
        self.assertIn("data_cache/.intraday_5m_60d_meta.json", gi_text)

        out = subprocess.check_output(
            ["git", "ls-files", "data_cache/intraday_5m_60d.parquet", "data_cache/.intraday_5m_60d_meta.json"],
            cwd=BASE_DIR,
            text=True,
        ).strip()
        self.assertEqual(out, "", f"Large 5m binary cache must NOT be tracked in git: {out}")

        tracked_json = subprocess.check_output(
            ["git", "ls-files", "data_cache/intraday_path_forecasts.json"],
            cwd=BASE_DIR,
            text=True,
        ).strip()
        self.assertEqual(tracked_json, "data_cache/intraday_path_forecasts.json")

    # ------------------------------------------------------------------
    # 2. intraday_path_predictor 整票覆盖 + 拆股跳变保护
    # ------------------------------------------------------------------
    def test_02_per_ticker_full_replace_eliminates_split_tail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_p = os.path.join(tmpdir, "intraday_5m_60d.parquet")
            meta_p = os.path.join(tmpdir, ".intraday_5m_60d_meta.json")

            # 模拟旧缓存：SNDK 第 65 天残留未除权 $800 高价，NVDA 为未拉取到的保留标的
            old_dt_sndk = pd.date_range("2026-09-10 09:30", periods=5, freq="5min")
            old_dt_nvda = pd.date_range("2026-11-18 09:30", periods=5, freq="5min")
            old_df = pd.concat([
                pd.DataFrame({
                    "Datetime": old_dt_sndk,
                    "Ticker": "SNDK",
                    "Open": 800.0, "High": 808.0, "Low": 795.0, "Close": 800.0, "Volume": 1000.0,
                }),
                pd.DataFrame({
                    "Datetime": old_dt_nvda,
                    "Ticker": "NVDA",
                    "Open": 150.0, "High": 152.0, "Low": 149.0, "Close": 151.0, "Volume": 5000.0,
                }),
            ], ignore_index=True)
            old_df.to_parquet(cache_p, index=False)

            # 模拟本次下载：SNDK 成功拉回 1拆8 复权后的 $100 序列，NVDA 拉取失败
            new_dt_sndk = pd.date_range("2026-11-19 09:30", periods=6, freq="5min", tz="America/New_York")

            def _mock_dl(tickers_arg, **kwargs):
                cols = pd.MultiIndex.from_product([["SNDK"], ["Open", "High", "Low", "Close", "Volume"]])
                data = np.tile([100.0, 101.0, 99.5, 100.5, 8000.0], (len(new_dt_sndk), 1))
                return pd.DataFrame(data, index=new_dt_sndk, columns=cols)

            merged = ipp.refresh_intraday_5m_60d_cache(
                tickers=["SNDK", "NVDA"],
                cache_path=cache_p,
                meta_path=meta_p,
                force_refresh=True,
                downloader=_mock_dl,
            )

            sndk_rows = merged[merged["Ticker"] == "SNDK"]
            nvda_rows = merged[merged["Ticker"] == "NVDA"]
            self.assertEqual(len(sndk_rows), 6, "SNDK 应整票覆盖为本次新序列，不得保留旧的 5 条 $800 尾巴")
            self.assertLess(float(sndk_rows["Close"].max()), 150.0, "SNDK 不得残留未除权 $800 假高价")
            self.assertEqual(len(nvda_rows), 5, "本次拉取失败的 NVDA 应安全保留其旧缓存")

    def test_03_sanitize_5m_split_jumps_heals_8x_cliff(self):
        dts = pd.date_range("2026-11-18 09:30", periods=6, freq="5min")
        df = pd.DataFrame({
            "Datetime": dts,
            "Ticker": ["SNDK"] * 6,
            "Open": [800.0, 804.0, 796.0, 100.0, 101.0, 102.0],
            "High": [808.0, 812.0, 800.0, 101.5, 102.0, 103.0],
            "Low": [792.0, 796.0, 790.0, 99.0, 100.0, 101.0],
            "Close": [800.0, 804.0, 800.0, 100.0, 101.0, 102.0],
            "Volume": [1000.0, 1000.0, 1000.0, 8000.0, 8000.0, 8000.0],
        })
        healed = ipp.sanitize_5m_split_jumps(df)
        self.assertAlmostEqual(float(healed["Close"].iloc[0]), 100.0, places=2)
        self.assertAlmostEqual(float(healed["Volume"].iloc[0]), 8000.0, places=1)
        self.assertAlmostEqual(float(healed["Close"].iloc[-1]), 102.0, places=2)

    # ------------------------------------------------------------------
    # 3. 点位与止损口径统一 (剧本 vs 表头 100% 一致 + 止损收敛)
    # ------------------------------------------------------------------
    def test_04_scenario_script_synced_after_r2_calibration(self):
        sc = mlp.compute_multi_level_scenario_map(
            ticker="SNDK",
            current_price=100.0,
            atr_pct=6.0,
            bias_ma50=6.0,
            rsi_14=55.0,
            action="🔥 强烈做多",
            is_short_covering=False,
            is_broken=False,
            is_halt=False,
        )
        self.assertLess(sc["sl"], sc["s2"])
        self.assertLess(sc["s2"], sc["s1"])
        self.assertLess(sc["s1"], 100.0)
        self.assertLess(100.0, sc["r1"])
        self.assertLess(sc["r1"], sc["r2"])
        # S2 最大深度收敛至 <= 8.5%（避免 -11%~-15% 与日内止损极端打架）
        s2_dist_pct = (100.0 - sc["s2"]) / 100.0 * 100.0
        self.assertLessEqual(s2_dist_pct, 8.6)

        # 模拟 15 日推演动态拔高 R2 至 $128.88，校验 align_time_cycle_projections_with_live 自动同步剧本正文
        by_tk = {"SNDK": dict(sc, latest_price=100.0, current_price=100.0)}
        tc_dict = {
            "SNDK": {
                "current_price": 100.0,
                "anchor_price": 100.0,
                "history": [{"date": "2026-11-19", "price": 100.0}],
                "projection": [{"date": "2026-11-20", "price": 127.0, "pct": 27.0, "wave_ret": 0.27}],
            }
        }
        mlp.align_time_cycle_projections_with_live(tc_dict, {"SNDK": 100.0}, by_ticker=by_tk, session_code="REGULAR")
        final_r2 = by_tk["SNDK"]["r2"]
        script = by_tk["SNDK"]["scenario_script"]
        self.assertGreater(final_r2, sc["r2"])
        m_r2 = re.search(r"R2\s*\(\$([0-9]+(?:\.[0-9]+)?)\)", script)
        self.assertIsNotNone(m_r2, f"剧本中应包含 R2 数值: {script}")
        self.assertAlmostEqual(float(m_r2.group(1)), float(final_r2), places=2)

    def test_05_live_ai_predictions_script_header_parity(self):
        pred_path = os.path.join(BASE_DIR, "data_cache", "latest_ai_predictions.json")
        if not os.path.exists(pred_path):
            self.skipTest("latest_ai_predictions.json not present")
        payload = json.load(open(pred_path, "r", encoding="utf-8"))
        by_tk = payload.get("by_ticker", {})
        self.assertGreater(len(by_tk), 50)
        mismatches = []
        for tk, item in by_tk.items():
            if not isinstance(item, dict):
                continue
            script = str(item.get("scenario_script") or "")
            if not script:
                continue
            for key, lbl in (("s1", "S1"), ("s2", "S2"), ("r1", "R1"), ("r2", "R2")):
                if item.get(key) is None:
                    continue
                expected = float(item[key])
                for m in re.finditer(rf"{lbl}\s*\(\$([0-9]+(?:\.[0-9]+)?)", script):
                    got = float(m.group(1))
                    if abs(got - expected) > 0.02:
                        mismatches.append((tk, lbl, expected, got, script))
            # 校验单调性 SL < S2 < S1 < R1 < R2
            if all(item.get(k) is not None for k in ("sl", "s2", "s1", "r1", "r2")):
                self.assertLess(float(item["sl"]), float(item["s2"]), f"{tk} SL < S2 violated")
                self.assertLess(float(item["s2"]), float(item["s1"]), f"{tk} S2 < S1 violated")
                self.assertLess(float(item["s1"]), float(item["r1"]), f"{tk} S1 < R1 violated")
                self.assertLess(float(item["r1"]), float(item["r2"]), f"{tk} R1 < R2 violated")
        self.assertEqual(mismatches, [], f"剧本与表头点位不一致: {mismatches[:5]}")

    # ------------------------------------------------------------------
    # 4. 清除盘前假占位数字与 0.0% 强 Bug
    # ------------------------------------------------------------------
    def test_06_no_fake_5m_placeholder_when_missing(self):
        res_empty = radar.evaluate_intraday_entry(
            active_price=100.0,
            fib_low=95.0,
            fib_high=102.0,
            ma50=98.0,
            is_pit=True,
            is_stop=False,
            dist_to_pit=0.0,
            intra_info={},
            rs_20d=0.0,
            atr=2.5,
            daily_rsi=45.0,
            ticker="NVDA",
        )
        self.assertFalse(res_empty["has_5m_intraday"])
        self.assertIsNone(res_empty["rsi_5m"], "无 5m 数据时禁止返回 50.0 假值")
        self.assertIsNone(res_empty["rvol_5m"], "无 5m 数据时禁止返回 1.00x 假值")

        res_valid = radar.evaluate_intraday_entry(
            active_price=100.0,
            fib_low=95.0,
            fib_high=102.0,
            ma50=98.0,
            is_pit=True,
            is_stop=False,
            dist_to_pit=0.0,
            intra_info={"has_5m_intraday": True, "rsi_5m": 34.2, "prev_rsi_5m": 31.0, "min_rsi_5m_30m": 29.5, "rvol_5m": 1.35, "vwap": 99.8},
            rs_20d=1.2,
            atr=2.5,
            daily_rsi=45.0,
            ticker="NVDA",
        )
        self.assertTrue(res_valid["has_5m_intraday"])
        self.assertEqual(res_valid["rsi_5m"], 34.2)
        self.assertEqual(res_valid["rvol_5m"], 1.35)

    def test_07_rs20d_boundary_and_frontend_placeholder_rules(self):
        src = open(os.path.join(BASE_DIR, "create_futu_terminal.py"), "r", encoding="utf-8").read()
        self.assertIn("待开盘 / --", src)
        self.assertIn("rsVal >= 0.5", src)
        self.assertIn("rsVal <= -0.5", src)
        self.assertIn("➖中性", src)
        self.assertNotIn('rsVal >= 0 ? "💪强"', src)

    # ------------------------------------------------------------------
    # 5. Windows 缺字方框 🪙 (U+1FA99) 全面清零
    # ------------------------------------------------------------------
    def test_08_no_coin_missing_glyph_in_source_and_html(self):
        for fn in (
            "create_futu_terminal.py",
            "ml_predictor_5y.py",
            "integrated_market_radar.py",
            "intraday_path_predictor.py",
        ):
            txt = open(os.path.join(BASE_DIR, fn), "r", encoding="utf-8").read()
            self.assertNotIn("\U0001fa99", txt, f"发现 Windows 缺字字符 🪙 于 {fn}")

        for html_fn in ("futu_pro_terminal.html", "stocks_radar.html", "predict_radar_5y.html", "macro_radar.html"):
            hp = os.path.join(BASE_DIR, html_fn)
            if os.path.exists(hp):
                htxt = open(hp, "r", encoding="utf-8").read()
                self.assertNotIn("\U0001fa99", htxt, f"渲染 HTML 中仍含缺字字符 🪙: {html_fn}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
