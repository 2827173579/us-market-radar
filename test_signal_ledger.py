#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_signal_ledger.py - V4.2 实盘信号自动记账本 & 周六复盘周报离线单元测试（全 Mock，零真实外发）

覆盖范围：
1. LONG 与 BEAR_DEFENSE 信号记录、同日同键去重、benchmark 基准联记与原子落盘；
2. 零未来函数自动回填（+1h 5m序列/实时价、16:05 ET 后才结算 EOD、T+3/T+5 COMPLETE、MAE/MFE 追踪）；
3. 严格区分多头胜率与防守拦截有效率（下跌日风控刹车计为有效拦截，绝不混入多头亏损）；
4. 周六复盘周报 Embed 渲染：常规活跃周（分组战绩 + 红黑榜 Top 2）与全周 0 做多防守周（无裸 --- 横线）；
5. 周报触发时间窗（周五 18:30 ET ~ 周六 12:00 ET）+ shared_alert_state 同周跨设备去重 + 失败不锁；
6. RADAR_DISABLE_EXTERNAL_ALERTS=1 与 --no-alerts 严格禁写生产账本、禁发 Discord，以及钩子端到端落盘验证；
7. predict_radar_5y.html 轻量记账本抽屉渲染（空账本就绪态 + 有记录 30日 KPI & 明细表）。
"""

import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

try:
    from zoneinfo import ZoneInfo
    ET_TZ = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    from datetime import timezone
    ET_TZ = timezone(timedelta(hours=-5))

import integrated_market_radar as imr
import ml_predictor_5y as mlp
import shared_alert_state
import signal_ledger as sl


class _DummyHttpResp:
    def __init__(self, status=204):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class TestSignalLedger(unittest.TestCase):
    SENTINEL_URL = "https://example.invalid/webhook/sentinel"
    CLAW_URL = "https://example.invalid/webhook/claw"
    DEF_URL = "https://example.invalid/webhook/defensive"

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = os.path.join(self._tmp_dir.name, "signal_ledger.json")
        self._orig_shared_file = shared_alert_state.SHARED_FILE
        shared_alert_state.SHARED_FILE = os.path.join(self._tmp_dir.name, "shared_alert_dedupe.json")
        self.env_patch = patch.dict(
            os.environ,
            {
                "DISCORD_SENTINEL_WEBHOOK_URL": self.SENTINEL_URL,
                "DISCORD_WEBHOOK_URL": "",
                "DISCORD_CLAW_WEBHOOK_URL": self.CLAW_URL,
                "DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL": self.DEF_URL,
                "RADAR_DISABLE_EXTERNAL_ALERTS": "",
                "SIGNAL_LEDGER_PATH": "",
            },
            clear=False,
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        shared_alert_state.SHARED_FILE = self._orig_shared_file
        self._tmp_dir.cleanup()

    def _capture_urlopen(self, status=204):
        captured = []

        def _fake_urlopen(req, timeout=10):
            body = json.loads(req.data.decode("utf-8"))
            captured.append({"url": req.full_url, "payload": body})
            return _DummyHttpResp(status)

        return captured, _fake_urlopen

    # 1. 多空信号记录、同日同键去重与原子落盘
    def test_01_record_long_and_defense_with_dedupe(self):
        dt = datetime(2026, 11, 16, 10, 15, tzinfo=ET_TZ)
        rec1 = sl.record_signal(
            "INTRADAY_5M_BUY", "NVDA", 140.0, timestamp_et=dt,
            vix_at_entry=24.5, vix_ratio_at_entry=0.95, ledger_path=self.ledger_path
        )
        rec_dup = sl.record_signal(
            "INTRADAY_5M_BUY", "NVDA", 141.0, timestamp_et=dt + timedelta(minutes=20),
            ledger_path=self.ledger_path
        )
        self.assertIsNotNone(rec1)
        self.assertIsNone(rec_dup, "同交易日同信号类型同标的只允许记账 1 次")
        self.assertEqual(rec1["direction"], "LONG")
        self.assertEqual(rec1["dedupe_key"], "2026-11-16:INTRADAY_5M_BUY:NVDA")

        ql = {
            "SPY": {"active_price": 585.0},
            "QQQ": {"last": 500.0},
            "SOXL": {"rth_close": 28.5},
        }
        recs_brk = sl.record_benchmark_signals(
            "VIX_BACKWARDATION_BRAKE",
            quote_lookup=ql,
            vix_regime={"vix_spot": 32.1, "vix_term_ratio": 1.08},
            timestamp_et=dt,
            ledger_path=self.ledger_path,
        )
        self.assertEqual(len(recs_brk), 3)
        self.assertTrue(all(r["direction"] == "BEAR_DEFENSE" for r in recs_brk))

        loaded = sl.load_ledger(self.ledger_path)
        self.assertEqual(len(loaded["signals"]), 4)

    # 2. +1h / EOD / T+3 / T+5 与 MAE/MFE 严格零未来函数回填
    def test_02_zero_lookahead_backfill_1h_eod_t3_t5(self):
        t0 = datetime(2026, 11, 16, 10, 0, tzinfo=ET_TZ)  # slot idx 6 (10:00 ET)
        sl.record_signal(
            "CORRIDOR_BREAK_ABOVE", "QQQ", 500.0, timestamp_et=t0, ledger_path=self.ledger_path
        )
        idx_days = pd.to_datetime(["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23"])
        df_daily = pd.DataFrame(
            {
                "Open": [500.0, 504.0, 506.0, 512.0, 514.0, 520.0],
                "High": [506.0, 508.0, 515.0, 518.0, 520.0, 525.0],
                "Low": [492.0, 495.0, 502.0, 509.0, 511.0, 518.0],
                "Close": [505.0, 506.0, 510.0, 515.0, 516.0, 522.0],
            },
            index=idx_days,
        )
        hdict = {"QQQ": df_daily}
        # slot 6=10:00 (500), slot 12=10:30 (490 -> -2%), slot 18=11:00 (+1h = 508 -> +1.6%), slot 30=12:00 (550 -> 未来槽位)
        imap = {
            "QQQ": {
                "rth_date": "2026-11-16",
                "rth_5m_series": [[6, 500.0], [12, 490.0], [18, 508.0], [30, 550.0]],
            }
        }

        # Step A: 当日 11:05 ET（未收盘！即使 df_daily 提前放了后续日期也不得穿越）
        sl.backfill_open_signals(
            live_quotes={"QQQ": {"active_price": 508.5}},
            history_dict=hdict,
            intraday_map=imap,
            now_et=datetime(2026, 11, 16, 11, 5, tzinfo=ET_TZ),
            ledger_path=self.ledger_path,
        )
        r_a = sl.load_ledger(self.ledger_path)["signals"][0]
        self.assertAlmostEqual(r_a["eval"]["ret_1h_pct"], 1.60, places=2)
        self.assertAlmostEqual(r_a["eval"]["mae_pct"], -2.00, places=2)
        self.assertAlmostEqual(r_a["eval"]["mfe_pct"], 1.70, places=2)  # slot 30 的 550 (+10%) 严禁被未来读取
        self.assertIsNone(r_a["eval"]["ret_eod_pct"], "16:05 ET 收盘前严禁结算 EOD")
        self.assertIsNone(r_a["eval"]["ret_t3_pct"])
        self.assertEqual(r_a["status"], "OPEN")

        # Step B: 当日 16:10 ET 收盘后 -> 回填 EOD (+1.00%), 但不得提前偷看 11-17~11-23
        sl.backfill_open_signals(
            live_quotes={},
            history_dict=hdict,
            intraday_map={},
            now_et=datetime(2026, 11, 16, 16, 10, tzinfo=ET_TZ),
            ledger_path=self.ledger_path,
        )
        r_b = sl.load_ledger(self.ledger_path)["signals"][0]
        self.assertAlmostEqual(r_b["eval"]["ret_eod_pct"], 1.00, places=2)
        self.assertIsNone(r_b["eval"]["ret_t3_pct"])

        # Step C: T+3 (2026-11-19 16:15 ET) -> 回填 T+3 (515 vs 500 = +3.00%)
        sl.backfill_open_signals(
            live_quotes={},
            history_dict=hdict,
            intraday_map={},
            now_et=datetime(2026, 11, 19, 16, 15, tzinfo=ET_TZ),
            ledger_path=self.ledger_path,
        )
        r_c = sl.load_ledger(self.ledger_path)["signals"][0]
        self.assertAlmostEqual(r_c["eval"]["ret_t3_pct"], 3.00, places=2)
        self.assertIsNone(r_c["eval"]["ret_t5_pct"])
        self.assertEqual(r_c["status"], "OPEN")

        # Step D: T+5 (2026-11-23 16:15 ET) -> 回填 T+5 (522 vs 500 = +4.40%) 并结算 COMPLETE
        bf_d = sl.backfill_open_signals(
            live_quotes={},
            history_dict=hdict,
            intraday_map={},
            now_et=datetime(2026, 11, 23, 16, 15, tzinfo=ET_TZ),
            ledger_path=self.ledger_path,
        )
        r_d = sl.load_ledger(self.ledger_path)["signals"][0]
        self.assertAlmostEqual(r_d["eval"]["ret_t5_pct"], 4.40, places=2)
        self.assertEqual(r_d["status"], "COMPLETE")
        self.assertEqual(bf_d["completed"], 1)

    # 3. 做多胜率与防守拦截有效率严格分离
    def test_03_separate_long_winrate_and_defense_effectiveness(self):
        # 1笔多头亏损(-2%), 1笔多头盈利(+4%) => 多头胜率 50%
        # 2笔防守刹车发出后大盘跌(-3%, -1.5%) => 防守有效率 100%, 不得混入多头胜率
        ledger_mock = {
            "version": 1,
            "signals": [
                {
                    "dedupe_key": "2026-11-18:VIX_STRATEGY_B_REBOUND:QQQ",
                    "timestamp_et": "2026-11-18T11:00:00-05:00",
                    "date_et": "2026-11-18",
                    "signal_type": "VIX_STRATEGY_B_REBOUND",
                    "signal_label_cn": "策略 B",
                    "direction": "LONG",
                    "ticker": "QQQ",
                    "entry_price": 500.0,
                    "eval": {"ret_eod_pct": 4.0, "ret_t3_pct": 4.2, "mae_pct": -0.3},
                    "status": "OPEN",
                },
                {
                    "dedupe_key": "2026-11-18:INTRADAY_5M_BUY:NVDA",
                    "timestamp_et": "2026-11-18T10:30:00-05:00",
                    "date_et": "2026-11-18",
                    "signal_type": "INTRADAY_5M_BUY",
                    "signal_label_cn": "5m 日内买点",
                    "direction": "LONG",
                    "ticker": "NVDA",
                    "entry_price": 140.0,
                    "eval": {"ret_eod_pct": -2.0, "ret_t3_pct": -2.5, "mae_pct": -3.1},
                    "status": "OPEN",
                },
                {
                    "dedupe_key": "2026-11-19:VIX_BACKWARDATION_BRAKE:SPY",
                    "timestamp_et": "2026-11-19T09:45:00-05:00",
                    "date_et": "2026-11-19",
                    "signal_type": "VIX_BACKWARDATION_BRAKE",
                    "signal_label_cn": "VIX 倒挂硬刹车",
                    "direction": "BEAR_DEFENSE",
                    "ticker": "SPY",
                    "entry_price": 580.0,
                    "eval": {"ret_eod_pct": -3.0, "ret_t3_pct": None, "mae_pct": -3.4},
                    "status": "OPEN",
                },
                {
                    "dedupe_key": "2026-11-19:CORRIDOR_BREAK_BELOW:SOXL",
                    "timestamp_et": "2026-11-19T10:15:00-05:00",
                    "date_et": "2026-11-19",
                    "signal_type": "CORRIDOR_BREAK_BELOW",
                    "signal_label_cn": "盘中走廊下破",
                    "direction": "BEAR_DEFENSE",
                    "ticker": "SOXL",
                    "entry_price": 25.0,
                    "eval": {"ret_eod_pct": 0.4, "ret_t3_pct": None, "mae_pct": -1.5},
                    "status": "OPEN",
                },
            ],
        }
        sl.save_ledger(ledger_mock, self.ledger_path)
        summary = sl.get_recent_summary(
            days=30, max_recent=10, now_et=datetime(2026, 11, 20, 18, 0, tzinfo=ET_TZ), ledger_path=self.ledger_path
        )
        self.assertEqual(summary["long_total"], 2)
        self.assertEqual(summary["long_wins"], 1)
        self.assertAlmostEqual(summary["long_win_rate_pct"], 50.0, places=1)
        self.assertEqual(summary["defense_total"], 2)
        self.assertEqual(summary["defense_hits"], 2)
        self.assertAlmostEqual(summary["defense_eff_rate_pct"], 100.0, places=1)

    # 4. 周报卡片渲染：常规周（含红黑榜 Top 2）与 全周 0 做多防守周（均无裸 --- 横线）
    def test_04_weekly_recap_payload_active_and_zero_long_weeks(self):
        sat_dt = datetime(2026, 11, 21, 9, 0, tzinfo=ET_TZ)
        active_ledger = {
            "version": 1,
            "signals": [
                {
                    "dedupe_key": "2026-11-17:VIX_STRATEGY_A_BOTTOM:SOXL",
                    "date_et": "2026-11-17",
                    "signal_type": "VIX_STRATEGY_A_BOTTOM",
                    "signal_label_cn": "策略 A · 恐慌出清抄底",
                    "direction": "LONG",
                    "ticker": "SOXL",
                    "entry_price": 24.0,
                    "eval": {"ret_eod_pct": 5.5, "ret_t3_pct": 8.2, "mae_pct": -0.8},
                },
                {
                    "dedupe_key": "2026-11-18:INTRADAY_5M_BUY:AMD",
                    "date_et": "2026-11-18",
                    "signal_type": "INTRADAY_5M_BUY",
                    "signal_label_cn": "5m 日内买点",
                    "direction": "LONG",
                    "ticker": "AMD",
                    "entry_price": 135.0,
                    "eval": {"ret_eod_pct": -1.8, "ret_t3_pct": -3.4, "mae_pct": -4.1},
                },
                {
                    "dedupe_key": "2026-11-19:VIX_BACKWARDATION_BRAKE:SPY",
                    "date_et": "2026-11-19",
                    "signal_type": "VIX_BACKWARDATION_BRAKE",
                    "signal_label_cn": "VIX 倒挂硬刹车",
                    "direction": "BEAR_DEFENSE",
                    "ticker": "SPY",
                    "entry_price": 582.0,
                    "eval": {"ret_eod_pct": -2.1, "ret_t3_pct": None, "mae_pct": -2.6},
                },
            ],
        }
        payload_active = sl.build_weekly_recap_payload(ledger_data=active_ledger, now_et=sat_dt)
        emb = payload_active["embeds"][0]
        self.assertIn("11/16~11/20", emb["title"])
        desc = emb["description"]
        for tok in ("分组战绩汇总", "策略 A", "5m 日内买点", "风控刹车", "本周最佳信号 Top 2", "最差踩坑复盘 Top 2", "SOXL", "AMD"):
            self.assertIn(tok, desc)
        self.assertIsNone(re.search(r"(?:^|\n)\s*---\s*(?:\n|$)", desc))

        # 极端空周：0 条做多信号，仅有 2 次风控刹车
        def_only_ledger = {
            "version": 1,
            "signals": [
                {
                    "dedupe_key": "2026-11-20:VIX_BACKWARDATION_BRAKE:SPY",
                    "date_et": "2026-11-20",
                    "signal_type": "VIX_BACKWARDATION_BRAKE",
                    "signal_label_cn": "VIX 倒挂硬刹车",
                    "direction": "BEAR_DEFENSE",
                    "ticker": "SPY",
                    "entry_price": 575.0,
                    "eval": {"ret_eod_pct": -1.5, "ret_t3_pct": None, "mae_pct": -1.9},
                },
                {
                    "dedupe_key": "2026-11-20:TECH_DELEVERAGE_ALERT:QQQ",
                    "date_et": "2026-11-20",
                    "signal_type": "TECH_DELEVERAGE_ALERT",
                    "signal_label_cn": "科技/VXN 去杠杆预警",
                    "direction": "BEAR_DEFENSE",
                    "ticker": "QQQ",
                    "entry_price": 495.0,
                    "eval": {"ret_eod_pct": -2.2, "ret_t3_pct": None, "mae_pct": -2.4},
                },
            ],
        }
        payload_def = sl.build_weekly_recap_payload(ledger_data=def_only_ledger, now_et=sat_dt)
        desc_def = payload_def["embeds"][0]["description"]
        self.assertIn("本周全周风控防守零做多误触", desc_def)
        self.assertIn("2", desc_def)
        self.assertIsNone(re.search(r"(?:^|\n)\s*---\s*(?:\n|$)", desc_def))

    # 5. 周六触发窗口 + shared_alert_state 同周去重 + 推送失败不加锁
    def test_05_saturday_window_and_weekly_dedupe(self):
        # 时间窗边界：周五 18:29 (否), 周五 18:30 (是), 周六 11:59 (是), 周六 12:01 (否)
        self.assertFalse(sl.is_weekly_recap_window(datetime(2026, 11, 20, 18, 29, tzinfo=ET_TZ))[0])
        self.assertTrue(sl.is_weekly_recap_window(datetime(2026, 11, 20, 18, 30, tzinfo=ET_TZ))[0])
        in_sat, wk_key, mon_d, fri_d = sl.is_weekly_recap_window(datetime(2026, 11, 21, 9, 0, tzinfo=ET_TZ))
        self.assertTrue(in_sat)
        self.assertEqual(wk_key, "WEEKLY_LEDGER_RECAP:2026-W47")
        self.assertEqual((mon_d.isoformat(), fri_d.isoformat()), ("2026-11-16", "2026-11-20"))
        self.assertFalse(sl.is_weekly_recap_window(datetime(2026, 11, 21, 12, 1, tzinfo=ET_TZ))[0])

        # 失败 HTTP 500 -> 不写入 shared_alert_state 锁
        cap_fail, open_fail = self._capture_urlopen(status=500)
        with patch("urllib.request.urlopen", side_effect=open_fail):
            res_fail = sl.dispatch_weekly_recap_discord(
                now_et=datetime(2026, 11, 21, 9, 0, tzinfo=ET_TZ), ledger_path=self.ledger_path
            )
        self.assertFalse(res_fail["sent"])
        self.assertFalse(shared_alert_state.is_sent(wk_key))

        # 成功 HTTP 204 -> 发送 1 条并锁住本周；第二次同周自动跳过
        cap_ok, open_ok = self._capture_urlopen(status=204)
        with patch("urllib.request.urlopen", side_effect=open_ok):
            res1 = sl.dispatch_weekly_recap_discord(
                now_et=datetime(2026, 11, 21, 9, 5, tzinfo=ET_TZ), ledger_path=self.ledger_path, device_tag="PC-TEST"
            )
            res2 = sl.dispatch_weekly_recap_discord(
                now_et=datetime(2026, 11, 21, 9, 10, tzinfo=ET_TZ), ledger_path=self.ledger_path, device_tag="TABLET-TEST"
            )
        self.assertTrue(res1["sent"])
        self.assertFalse(res2["sent"])
        self.assertEqual(res2["reason"], "ALREADY_SENT_WEEKLY")
        self.assertEqual(len(cap_ok), 1)
        self.assertEqual(cap_ok[0]["url"], self.SENTINEL_URL)
        self.assertTrue(shared_alert_state.is_sent(wk_key))

    # 6. RADAR_DISABLE_EXTERNAL_ALERTS=1 / --no-alerts 严格不写账本，以及真实回调端到端入账
    def test_06_dry_run_guard_and_end_to_end_dispatcher_hooks(self):
        # Dry-Run 开启时：direct record / backfill / weekly dispatch 全部不得写文件
        dry_path = os.path.join(self._tmp_dir.name, "should_not_exist_ledger.json")
        with patch.dict(os.environ, {"RADAR_DISABLE_EXTERNAL_ALERTS": "1"}):
            self.assertIsNone(sl.record_signal("INTRADAY_5M_BUY", "NVDA", 140.0, ledger_path=dry_path))
            bf = sl.backfill_open_signals(ledger_path=dry_path)
            self.assertEqual(bf["skipped_disabled"], 1)
            wk = sl.dispatch_weekly_recap_discord(
                now_et=datetime(2026, 11, 21, 9, 0, tzinfo=ET_TZ), ledger_path=dry_path
            )
            self.assertEqual(wk["reason"], "DISABLED")
        self.assertFalse(os.path.exists(dry_path), "RADAR_DISABLE_EXTERNAL_ALERTS=1 下严禁创建/写入账本文件")

        # 正常开启推送（Mock urlopen）+ 显式临时 ledger_path -> 三大分发器确认后均自动记账
        cap, open_ok = self._capture_urlopen(status=204)
        vix_reg = {
            "vix_spot": 31.2, "vix_term_ratio": 1.07, "vxn_spot": 36.8, "vxn_vix_spread": 5.6,
            "vix3m_spot": 29.1, "vvix_spot": 128.0, "tnx_yield": 4.85,
            "term_structure_cn": "期限倒挂", "vix_state": "BACKWARDATION_BRAKE",
            "vix_hard_brake": True, "vix_bottom_signal": False, "session_code": "REGULAR",
        }
        macro_d = {"SPY": {"last": 580.0}, "QQQ": {"last": 495.0}, "SOXL": {"last": 26.0}}
        with patch("urllib.request.urlopen", side_effect=open_ok):
            imr.dispatch_vix_regime_discord_alerts(
                vix_regime=vix_reg,
                macro_data=macro_d,
                now_str="2026-11-20 10:10:00",
                dedupe_day="2026-11-20",
                session_code="REGULAR",
                ledger_path=self.ledger_path,
            )
            imr.dispatch_intraday_corridor_divergence_alerts(
                intraday_forecasts={
                    "forecast_date": "2026-11-20",
                    "market_regime": "BEAR_DEFENSE",
                    "by_ticker": {
                        "SPY": {"status": "ok", "forecast_date": "2026-11-20", "divergence": {"status": "BELOW_P25", "latest_slot": 8, "actual_price": 579.2, "slot_p25": 580.0, "slot_p50": 582.0, "slot_p75": 584.0, "pct_vs_p50": -0.48}}
                    },
                },
                macro_data=macro_d,
                weather_info={"vix_regime": vix_reg, "market_regime": "BEAR_DEFENSE"},
                now_et=datetime(2026, 11, 20, 10, 15, tzinfo=ET_TZ),
                session_code="REGULAR",
                ledger_path=self.ledger_path,
            )
            imr.dispatch_vix_intraday_rebound_alerts(
                vix_regime={"vix_spot": 24.0, "vix_prev": 26.0, "vix_pct": -7.7, "vix_10d_peak": 28.0, "vix_term_ratio": 0.93, "vix_term_ratio_10d_peak": 1.02, "vix_state": "WASHOUT_BOTTOM_WINDOW"},
                macro_data={**macro_d, "VIXY": {"active_price": 42.0, "vwap": 44.0}, "QQQ": {"active_price": 498.0, "vwap": 495.0, "is_5m_green": True}},
                now_et=datetime(2026, 11, 20, 11, 0, tzinfo=ET_TZ),
                session_code="REGULAR",
                ledger_path=self.ledger_path,
            )

        recs = sl.load_ledger(self.ledger_path)["signals"]
        types_saved = {(r["signal_type"], r["ticker"], r["direction"]) for r in recs}
        self.assertIn(("VIX_BACKWARDATION_BRAKE", "SPY", "BEAR_DEFENSE"), types_saved)
        self.assertIn(("TECH_DELEVERAGE_ALERT", "QQQ", "BEAR_DEFENSE"), types_saved)
        self.assertIn(("CORRIDOR_BREAK_BELOW", "SPY", "BEAR_DEFENSE"), types_saved)
        self.assertIn(("VIX_STRATEGY_B_REBOUND", "QQQ", "LONG"), types_saved)
        self.assertGreaterEqual(len(cap), 4)

    # 7. predict_radar_5y.html 实盘记账本抽屉渲染（空账本就绪态 + 有记录表渲染）
    def test_07_predict_html_signal_ledger_drawer_rendering(self):
        out_empty = os.path.join(self._tmp_dir.name, "predict_empty.html")
        base_tables = {
            "kpi": {"high_conviction_win_rate": 60.0, "profit_factor": 1.5, "top_sector": "半导体芯片", "top_feature": "Alpha", "sync_time": "2026-11-22 10:00 ET", "data_mode": "在线"},
            "macro_weather": {"status": "🔴 防守", "desc": "test", "market_regime": "BEAR_DEFENSE", "market_regime_cn": "🔴 空头防守体制", "breadth_ma20_pct": 12.0, "qqqm_bias": -3.0, "smh_bias": -4.0, "vixy_pos": 0.7, "smh_igv": -1.0},
            "vix_regime": {},
            "table1": [{"threshold_label": ">= 60%", "trigger_count": 100, "pos_win_rate_pct": 60.0, "touch_win_rate_pct": 55.0, "avg_realized_return_pct": 2.1, "win_loss_ratio": 1.5, "profit_factor": 1.5}],
            "table2": [{"sector_name_cn": "半导体芯片", "sector_key": "Semiconductors", "tickers_count": 10, "oos_total_signals": 200, "high_conviction_signals": 50, "high_conviction_pos_win_rate_pct": 60.0, "high_conviction_touch_win_rate_pct": 55.0, "avg_realized_return_pct": 2.5, "win_loss_ratio": 1.6, "profit_factor": 1.6, "alpha_rating": "⭐⭐⭐⭐⭐"}],
            "table3": [],
            "table4": [],
            "all_live_predictions": [],
            "signal_ledger_summary": sl.get_recent_summary(ledger_path=os.path.join(self._tmp_dir.name, "missing_ledger.json")),
        }
        mlp.render_html_dashboard(base_tables, output_path=out_empty)
        with open(out_empty, "r", encoding="utf-8") as f:
            h_empty = f.read()
        self.assertIn('id="btnSignalLedgerDrawer"', h_empty)
        self.assertIn('id="signalLedgerDrawer"', h_empty)
        self.assertIn("账本已就绪 · 自下周首条实盘推送起自动记账复盘", h_empty)

        sl.record_signal("INTRADAY_5M_BUY", "NVDA", 142.5, timestamp_et=datetime(2026, 11, 20, 10, 30, tzinfo=ET_TZ), ledger_path=self.ledger_path)
        out_full = os.path.join(self._tmp_dir.name, "predict_full.html")
        base_tables["signal_ledger_summary"] = sl.get_recent_summary(
            now_et=datetime(2026, 11, 21, 9, 0, tzinfo=ET_TZ), ledger_path=self.ledger_path
        )
        mlp.render_html_dashboard(base_tables, output_path=out_full)
        with open(out_full, "r", encoding="utf-8") as f:
            h_full = f.read()
        self.assertIn("NVDA", h_full)
        self.assertIn("$142.50", h_full)
        self.assertIn("多头实盘胜率", h_full)
        self.assertIn("防守拦截有效率", h_full)


if __name__ == "__main__":
    unittest.main(verbosity=2)
