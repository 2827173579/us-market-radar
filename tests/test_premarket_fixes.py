# -*- coding: utf-8 -*-
"""
盘前收口定向修复回归测试 (tests/test_premarket_fixes.py)
覆盖：
1. 美股 PMH/PML 严格按当日美东日期隔离，杜绝混入 T-1/T-2（如感恩节前 11-25）旧极值；
2. AI 首席策略师跨日/节假日切盘前/VIX 倒挂翻转缓存自动失效 + 零代码变量名泄漏清洗；
3. HSTECH 零新增外部 API 从本地 5m parquet 回填真实 5m RSI 与日内均价 VWAP；
4. 3 处轻量 UI 瑕疵：顶栏温度计胶囊无原生 title 遮挡、亚太 X 轴防重叠间距、predict_radar_5y 下拉计数与 79 标的对齐。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

import ai_commentary_engine as ace
import apac_ashare_feed as aaf
import create_futu_terminal as cft
import intraday_path_predictor as ipp
import ml_predictor_5y as mp

ET = ZoneInfo("America/New_York")


class TestPremarketFixes(unittest.TestCase):
    def test_01_us_pmh_pml_strictly_isolates_today_et_date(self):
        """验证盘前提取 PMH/PML 严格过滤当日美东 04:00~09:30，绝不混入 11-25 旧盘前极值。"""
        # 11-25 旧盘前高点 71.77 + 11-27 今日盘前真实区间 53.10~54.20
        df_5m = pd.DataFrame(
            {
                "Datetime": [
                    datetime(2026, 11, 25, 6, 0),
                    datetime(2026, 11, 25, 10, 0),
                    datetime(2026, 11, 27, 4, 30),
                    datetime(2026, 11, 27, 5, 30),
                ],
                "Open": [70.0, 62.0, 54.0, 53.4],
                "High": [71.77, 63.0, 54.20, 53.8],
                "Low": [68.0, 61.5, 53.50, 53.10],
                "Close": [69.0, 62.2, 53.60, 53.12],
                "Volume": [1000, 5000, 1200, 1500],
            }
        )
        # 故意注入带旧日期 pm_date=2026-11-25 的脏 intraday_info，必须被拦截丢弃
        dirty_info = {
            "pmh": 71.77,
            "pml": 53.55,
            "pm_date": "2026-11-25",
            "rth_date": "2026-11-25",
            "ext_price": 53.12,
            "price_kind": "premarket",
        }
        prev_c, anchor_o, pmh, pml, _ = ipp._extract_day_features_for_target(
            "SOXS",
            df_5m,
            date(2026, 11, 27),
            dirty_info,
            None,
            {"last": 53.12},
            datetime(2026, 11, 27, 5, 52, tzinfo=ET),
        )
        self.assertAlmostEqual(prev_c, 62.2, places=2)
        self.assertAlmostEqual(pmh, 54.20, places=2)
        self.assertAlmostEqual(pml, 53.10, places=2)

        # 若今日尚无盘前 5m 且无今日盘前实时价，PMH/PML 必须为 None，绝不回退 11-25 的 71.77
        df_old_only = df_5m.iloc[:2].copy()
        _, _, pmh_none, pml_none, _ = ipp._extract_day_features_for_target(
            "SOXS",
            df_old_only,
            date(2026, 11, 27),
            {"pmh": 71.77, "pml": 68.0, "pm_date": "2026-11-25", "rth_date": "2026-11-25"},
            None,
            None,
            datetime(2026, 11, 27, 4, 5, tzinfo=ET),
        )
        self.assertIsNone(pmh_none)
        self.assertIsNone(pml_none)

    def test_02_ai_commentary_staleness_and_plain_chinese_sanitization(self):
        """验证 AI 首席策略师跨日/节假日切盘前/VIX 倒挂解除时自动失效刷新，且零代码变量名泄漏。"""
        now_et = datetime(2026, 11, 27, 5, 52, tzinfo=ET)
        stale_cache = {
            "schema_version": "v5.1-ai-commentary",
            "status": "OK",
            "updated_at_ts": (now_et.timestamp() - 19 * 3600),
            "updated_at_et": "2026-11-26 10:46 ET",
            "session_label": "🏖️ 节假日休市",
            "risk_lock_active": True,
            "vix_spot": 26.81,
            "vix_term_ratio": 1.02,
            "nq_pct_chg": 0.44,
            "items": {
                "OVERVIEW": "weather=暴雨空仓、risk_lock_active=true，regime=BEAR_INERTIA_C5，p15_t5=-4.3%，RSI_5m 71.3。"
            },
        }
        live_snap = {
            "session_code": "PRE_MARKET",
            "session_name": "🌅 美股盘前",
            "weather": "阴天防守",
            "market_regime": "BEAR_DEFENSE",
            "vix_regime": {"vix_spot": 24.60, "vix3m_spot": 26.29, "vix_term_ratio": 0.94},
            "futures": {"NQ=F": {"last": 26781.75, "pct_chg": 2.62}},
            "market_temperature": {"knife_catch_locked": False},
        }
        reason = ace._detect_cache_staleness_reason(stale_cache, live_snap, now_et)
        self.assertIsNotNone(reason)
        self.assertIn("cross_day", reason)

        # 验证代码层变量名清洗
        cleaned = ace.sanitize_plain_chinese_text(stale_cache["items"]["OVERVIEW"])
        for forbidden in ("weather=", "risk_lock_active", "regime=", "BEAR_INERTIA_C5", "p15_t5", "RSI_5m"):
            self.assertNotIn(forbidden, cleaned)
        self.assertIn("暴雨空仓", cleaned)
        self.assertIn("硬风控锁", cleaned)
        self.assertIn("5分钟RSI", cleaned)

        # 验证 05:52 ET 盘前时（即使未到 08:30 ET），过期的昨日休市缓存会触发重刷（mock HTTP）
        mock_called = {"n": 0}
        def fake_http(url, headers, payload, timeout=30):
            mock_called["n"] += 1
            body = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "OVERVIEW": "【一句话定性】今日美股盘前转暖，NQ 涨 2.62%，VIX 回落至 24.60，倒挂解除。",
                            "QQQ": "【一句话定性】QQQ 盘前偏强。",
                            "SPY": "【一句话定性】SPY / VOO（走势100%同步）盘前偏强。",
                            "SOXX": "【一句话定性】SOXX 盘前走强。",
                        }, ensure_ascii=False)
                    }
                }]
            }
            return 200, json.dumps(body, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as td:
            cp = os.path.join(td, "ai_cache.json")
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(stale_cache, f, ensure_ascii=False)
            res = ace.maybe_refresh_web_brief(
                snapshot=live_snap,
                cache_path=cp,
                env_override={"DEEPSEEK_API_KEY": "sk-mock", "GEMINI_API_KEY": "", "AI_WEB_AUTO_BRIEF_ENABLED": "1"},
                now_dt=now_et,
                http_post_fn=fake_http,
            )
            self.assertEqual(mock_called["n"], 1)
            self.assertFalse(res.get("from_cache"))
            self.assertEqual(res.get("cache_date_et"), "2026-11-27")
            self.assertEqual(res.get("session_code"), "PRE_MARKET")

    def test_03_hstech_local_parquet_microstructure_hydration(self):
        """验证 HSTECH 零新增外部 API 从本地 5m parquet 回填真实 5m RSI 与非零偏离 VWAP。"""
        rows = []
        for i in range(66):
            hh = 9 + (30 + i * 5) // 60 if i < 30 else 13 + ((i - 30) * 5) // 60
            mm = (30 + i * 5) % 60 if i < 30 else ((i - 30) * 5) % 60
            px = round(3433.0 + i * 3.2 - (4.5 if i % 4 == 2 else 0.0), 2)
            rows.append({
                "ticker": "HSTECH",
                "date": "2026-11-27",
                "time_slot": f"{hh:02d}:{mm:02d}",
                "open": px - 1.0,
                "high": px + 4.0,
                "low": px - 3.0,
                "close": px,
                "volume": 0.0,
            })
        with tempfile.TemporaryDirectory() as td:
            pq_path = os.path.join(td, "apac_5m.parquet")
            pd.DataFrame(rows).to_parquet(pq_path, index=False)
            doc = {
                "items": {
                    "HSTECH": {
                        "ticker": "HSTECH",
                        "market": "HK",
                        "decimals": 2,
                        "last": 3645.02,
                        "price": 3645.02,
                        "vwap": None,
                        "vwap_diff_pct": None,
                        "rsi_5m": None,
                        "rvol_5m": None,
                        "has_5m_intraday": False,
                        "intraday_5m": [],
                    }
                }
            }
            cnt = aaf.hydrate_missing_5m_from_local_parquet(doc, parquet_path=pq_path)
            self.assertEqual(cnt, 1)
            hs = doc["items"]["HSTECH"]
            self.assertTrue(hs["has_5m_intraday"])
            self.assertIsNotNone(hs["rsi_5m"])
            self.assertGreater(hs["rsi_5m"], 50.0)
            self.assertIsNotNone(hs["vwap"])
            self.assertNotAlmostEqual(hs["vwap"], 3645.02, places=1)
            self.assertGreater(hs["vwap_diff_pct"], 0.5)

    def test_04_ui_no_native_temp_title_and_xaxis_gap_and_cycle_dropdown_79(self):
        """验证 3 处轻量 UI 修复：温度计胶囊无原生 title 遮挡、亚太 X 轴防重叠间距、79 标的下拉对齐。"""
        stocks = [{"ticker": "NVDA", "name": "英伟达", "price": 155.7, "change_pct": 4.3, "sector": "AI", "volume": 100}]
        macro = {"NQ=F": {"price": 26781.0, "change_pct": 2.6}}
        html = cft.build_futu_terminal_html(
            stocks_data=stocks,
            macro_data=macro,
            weather_info={"weather": "阴天防守"},
            session_code="PRE_MARKET",
        )
        self.assertNotIn('id="pBubbleTempPill" class="temp-pill-btn tabular" onclick="toggleTempPopover(event)" title=', html)
        self.assertNotIn('id="pBottomTempPill" class="temp-pill-btn tabular" onclick="toggleTempPopover(event)" title=', html)
        self.assertNotIn("bubblePill.title =", html)
        self.assertNotIn("bottomPill.title =", html)
        self.assertIn("minTickGapPx = 44", html)

        # 验证 export_latest_ai_predictions_json 同步裁剪多余的 time_cycle_projections 键（如 ANT/AVGO）
        with tempfile.TemporaryDirectory() as td:
            out_json = os.path.join(td, "ai.json")
            tables_data = {
                "all_live_predictions": [{"ticker": "NVDA"}, {"ticker": "QQQ"}],
                "top_long": [{"ticker": "NVDA"}],
                "top_short": [{"ticker": "QQQ"}],
                "time_cycle_projections": {
                    "NVDA": {"current_price": 155.7},
                    "QQQ": {"current_price": 650.0},
                    "ANT": {"current_price": 10.0},
                    "AVGO": {"current_price": 200.0},
                },
            }
            mp.export_latest_ai_predictions_json(tables_data, json_path=out_json)
            doc = json.load(open(out_json, encoding="utf-8"))
            self.assertEqual(sorted(doc["time_cycle_projections"].keys()), ["NVDA", "QQQ"])


if __name__ == "__main__":
    unittest.main()
