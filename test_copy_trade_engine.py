# -*- coding: utf-8 -*-
"""
V5.4 独立单元测试：$1,000 多空双轨 KOL 模拟跟单账本引擎 (test_copy_trade_engine.py)
全部使用 tempfile.TemporaryDirectory() 隔离，绝不触碰生产 signal_ledger.json 或 copy_trade_ledger.json，绝不外发真实 Discord 消息。
"""

import os
import tempfile
import unittest
from datetime import date, datetime
from unittest import mock

import copy_trade_engine as cte
import discord_radar_bot as bot


def _et(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=cte._NY_TZ)


class TestCopyTradeEngineV54(unittest.TestCase):
    def test_01_long_entry_tp_hit_sl_hit_and_equity_math(self):
        """验证做多正常开仓、TP_HIT 与 SL_HIT 精确滑点与 $1,000 净值/最大回撤/Profit Factor 计算。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            lp = os.path.join(tmpdir, "copy_trade_ledger.json")
            ai_preds = {
                "market_regime": "BULL_TREND",
                "by_ticker": {
                    "AAPL": {
                        "ticker": "AAPL",
                        "calibrated_win_rate_pct": 66.0,
                        "calibrated_short_win_rate_pct": 24.0,
                        "target_profit_price": 210.00,
                        "stop_loss_price": 192.00,
                        "atr_pct": 3.0,
                    },
                    "MSFT": {
                        "ticker": "MSFT",
                        "calibrated_win_rate_pct": 62.0,
                        "calibrated_short_win_rate_pct": 28.0,
                        "target_profit_price": 420.00,
                        "stop_loss_price": 384.00,
                        "atr_pct": 3.0,
                    },
                },
            }
            live_open = {
                "AAPL": {"active_price": 200.00, "prev_close": 199.00, "pct_change": 0.5},
                "MSFT": {"active_price": 400.00, "prev_close": 398.00, "pct_change": 0.5},
            }
            sent = []
            r1 = cte.run_copy_trade_cycle(
                ai_predictions=ai_preds,
                live_quotes=live_open,
                session_code="REGULAR",
                now_et=_et(2026, 11, 16, 9, 40),
                ledger_path=lp,
                dry_run=False,
                alert_sender=lambda t, b, k, c: sent.append((t, b, k, c)),
            )
            self.assertTrue(r1["persisted"])
            self.assertEqual(len(r1["opened"]), 2)
            self.assertEqual(len(sent), 2)
            ldg = r1["ledger"]
            self.assertEqual(ldg["cash"], 500.00)  # 2 * $250
            self.assertEqual(len(ldg["open_positions"]), 2)

            # 盘中 AAPL 触及止盈 $210.00 (+5% - 0.03% = +4.97% -> +$12.43)，MSFT 触及止损 $384.00 (-4% - 0.03% = -4.03% -> -$10.08)
            live_close = {
                "AAPL": {"active_price": 210.00, "prev_close": 199.00},
                "MSFT": {"active_price": 384.00, "prev_close": 398.00},
            }
            r2 = cte.run_copy_trade_cycle(
                ai_predictions=ai_preds,
                live_quotes=live_close,
                session_code="REGULAR",
                now_et=_et(2026, 11, 16, 11, 20),
                ledger_path=lp,
                dry_run=False,
                alert_sender=lambda t, b, k, c: sent.append((t, b, k, c)),
            )
            self.assertEqual(len(r2["closed"]), 2)
            ldg2 = r2["ledger"]
            self.assertEqual(len(ldg2["open_positions"]), 0)
            by_tk = {t["ticker"]: t for t in ldg2["closed_trades"]}
            self.assertEqual(by_tk["AAPL"]["outcome"], "TP_HIT")
            self.assertAlmostEqual(by_tk["AAPL"]["net_return_pct"], 4.97, places=2)
            self.assertAlmostEqual(by_tk["AAPL"]["pnl_usd"], 12.43, places=2)
            self.assertEqual(by_tk["MSFT"]["outcome"], "SL_HIT")
            self.assertAlmostEqual(by_tk["MSFT"]["net_return_pct"], -4.03, places=2)
            self.assertAlmostEqual(by_tk["MSFT"]["pnl_usd"], -10.08, places=2)
            self.assertAlmostEqual(ldg2["total_equity"], 1002.35, places=2)
            self.assertEqual(ldg2["stats"]["tp_hit_count"], 1)
            self.assertEqual(ldg2["stats"]["sl_hit_count"], 1)
            self.assertEqual(ldg2["stats"]["long_win_rate_pct"], 50.0)
            self.assertAlmostEqual(ldg2["stats"]["profit_factor"], round(12.43 / 10.08, 2), places=2)

    def test_02_short_entry_profit_on_drop_and_sl_on_rally(self):
        """验证做空 (SHORT) 下跌盈利 TP_HIT 与上涨亏损 SL_HIT 的逆向盈亏与滑点精确计算。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            lp = os.path.join(tmpdir, "copy_trade_ledger.json")
            ai_preds = {
                "market_regime": "BEAR_DEFENSE",
                "by_ticker": {
                    "NVDA": {
                        "ticker": "NVDA",
                        "calibrated_win_rate_pct": 25.0,
                        "calibrated_short_win_rate_pct": 68.0,
                        "short_target_profit_price": 130.00,
                        "short_stop_loss_price": 147.00,
                        "atr_pct": 4.5,
                    },
                    "TSLA": {
                        "ticker": "TSLA",
                        "calibrated_win_rate_pct": 28.0,
                        "calibrated_short_win_rate_pct": 61.0,
                        "short_target_profit_price": 188.00,
                        "short_stop_loss_price": 210.00,
                        "atr_pct": 5.0,
                    },
                },
            }
            live_open = {
                "NVDA": {"active_price": 140.00, "prev_close": 142.00, "pct_change": -1.41},
                "TSLA": {"active_price": 200.00, "prev_close": 202.00, "pct_change": -0.99},
            }
            r1 = cte.run_copy_trade_cycle(
                ai_predictions=ai_preds,
                live_quotes=live_open,
                session_code="REGULAR",
                now_et=_et(2026, 11, 16, 9, 40),
                ledger_path=lp,
                dry_run=False,
            )
            self.assertEqual(len(r1["opened"]), 2)
            self.assertTrue(all(p["direction"] == "SHORT" for p in r1["opened"]))

            # NVDA 下跌至 $130.00 (+7.1429% - 0.03% = +7.11% -> +$17.78) 止盈；TSLA 上涨至 $210.00 (-5.0% - 0.03% = -5.03% -> -$12.58) 止损
            r2 = cte.run_copy_trade_cycle(
                ai_predictions=ai_preds,
                live_quotes={"NVDA": {"active_price": 130.00}, "TSLA": {"active_price": 210.00}},
                session_code="REGULAR",
                now_et=_et(2026, 11, 16, 14, 0),
                ledger_path=lp,
                dry_run=False,
            )
            by_tk = {t["ticker"]: t for t in r2["closed"]}
            self.assertEqual(by_tk["NVDA"]["outcome"], "TP_HIT")
            self.assertAlmostEqual(by_tk["NVDA"]["net_return_pct"], 7.11, places=2)
            self.assertAlmostEqual(by_tk["NVDA"]["pnl_usd"], 17.78, places=2)
            self.assertEqual(by_tk["TSLA"]["outcome"], "SL_HIT")
            self.assertAlmostEqual(by_tk["TSLA"]["net_return_pct"], -5.03, places=2)
            self.assertAlmostEqual(by_tk["TSLA"]["pnl_usd"], -12.58, places=2)
            self.assertEqual(r2["ledger"]["stats"]["short_total"], 2)
            self.assertEqual(r2["ledger"]["stats"]["short_wins"], 1)
            self.assertEqual(r2["ledger"]["stats"]["short_win_rate_pct"], 50.0)
            self.assertAlmostEqual(r2["ledger"]["total_equity"], 1005.20, places=2)

    def test_03_t5_us_trading_day_expiry_skips_weekends_and_holidays(self):
        """验证第 5 个美股交易日跳过周末与感恩节休市日，且 15:50 ET 触发 T5_EXPIRE 强平并拆分 T5_WIN / T5_LOSS。"""
        # 2026-11-23 (周一) 起第 5 个交易日：24(二), 25(三), 26(感恩节休市跳过!), 27(五), 30(一), 12-01(二)
        exp_thanksgiving = cte.add_us_trading_days(date(2026, 11, 23), 5)
        self.assertEqual(exp_thanksgiving, date(2026, 12, 1))
        # 普通周一 2026-11-09 起第 5 个交易日：10, 11, 12, 13, 16(下周一，跳过周末 14/15)
        exp_normal = cte.add_us_trading_days(date(2026, 11, 9), 5)
        self.assertEqual(exp_normal, date(2026, 11, 16))

        with tempfile.TemporaryDirectory() as tmpdir:
            lp = os.path.join(tmpdir, "copy_trade_ledger.json")
            ai_preds = {
                "market_regime": "BULL_TREND",
                "by_ticker": {
                    "AMD": {
                        "ticker": "AMD",
                        "calibrated_win_rate_pct": 64.0,
                        "calibrated_short_win_rate_pct": 25.0,
                        "target_profit_price": 180.00,
                        "stop_loss_price": 135.00,
                    },
                    "META": {
                        "ticker": "META",
                        "calibrated_win_rate_pct": 62.0,
                        "calibrated_short_win_rate_pct": 28.0,
                        "target_profit_price": 680.00,
                        "stop_loss_price": 540.00,
                    },
                },
            }
            # 2026-11-09 开仓 -> expiry_date = 2026-11-16
            r1 = cte.run_copy_trade_cycle(
                ai_predictions=ai_preds,
                live_quotes={"AMD": {"active_price": 150.00, "prev_close": 149.0}, "META": {"active_price": 600.00, "prev_close": 598.0}},
                session_code="REGULAR",
                now_et=_et(2026, 11, 9, 9, 40),
                ledger_path=lp,
                dry_run=False,
            )
            self.assertEqual(len(r1["opened"]), 2)
            self.assertEqual(r1["opened"][0]["expiry_date"], "2026-11-16")

            # 到期日 15:45 ET 尚未到 15:50 强平点，继续持有
            r_pre = cte.run_copy_trade_cycle(
                ai_predictions={"by_ticker": {}},
                live_quotes={"AMD": {"active_price": 153.00}, "META": {"active_price": 592.00}},
                session_code="REGULAR",
                now_et=_et(2026, 11, 16, 15, 45),
                ledger_path=lp,
                dry_run=False,
            )
            self.assertEqual(len(r_pre["closed"]), 0)
            self.assertEqual(len(r_pre["ledger"]["open_positions"]), 2)

            # 到期日 15:50 ET 触发 T5_EXPIRE：AMD 小赚 (T5_WIN)，META 小亏 (T5_LOSS)
            r_exp = cte.run_copy_trade_cycle(
                ai_predictions={"by_ticker": {}},
                live_quotes={"AMD": {"active_price": 153.00}, "META": {"active_price": 592.00}},
                session_code="REGULAR",
                now_et=_et(2026, 11, 16, 15, 50),
                ledger_path=lp,
                dry_run=False,
            )
            self.assertEqual(len(r_exp["closed"]), 2)
            by_tk = {t["ticker"]: t for t in r_exp["closed"]}
            self.assertEqual(by_tk["AMD"]["exit_reason"], "T5_EXPIRE")
            self.assertEqual(by_tk["AMD"]["outcome"], "T5_WIN")
            self.assertEqual(by_tk["META"]["exit_reason"], "T5_EXPIRE")
            self.assertEqual(by_tk["META"]["outcome"], "T5_LOSS")
            self.assertEqual(r_exp["ledger"]["stats"]["t5_win_count"], 1)
            self.assertEqual(r_exp["ledger"]["stats"]["t5_loss_count"], 1)

    def test_04_max_4_slots_mirror_exclusion_dup_gap_and_earnings_guard(self):
        """验证最多 4 仓上限、同标的防重、{SOXX,SOXL,SOXS} 镜像组互斥去重、>4% 同向跳空过滤与 7 日财报屏蔽。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            lp = os.path.join(tmpdir, "copy_trade_ledger.json")
            ai_preds = {
                "market_regime": "BULL_TREND",
                "by_ticker": {
                    # 镜像组 SEMI_MIRROR 三只同时高胜率 -> 仅允许胜率最高的 SOXX 入选 1 笔
                    "SOXX": {"ticker": "SOXX", "calibrated_win_rate_pct": 72.0, "calibrated_short_win_rate_pct": 20.0},
                    "SOXL": {"ticker": "SOXL", "calibrated_win_rate_pct": 71.0, "calibrated_short_win_rate_pct": 21.0},
                    "SOXS": {"ticker": "SOXS", "calibrated_win_rate_pct": 20.0, "calibrated_short_win_rate_pct": 70.0},
                    # 财报 3 天内 -> 跳过
                    "EARN1": {"ticker": "EARN1", "calibrated_win_rate_pct": 69.0, "calibrated_short_win_rate_pct": 22.0},
                    # 同向高开 +5.5% (>4%) -> 跳过防追单
                    "GAP1": {"ticker": "GAP1", "calibrated_win_rate_pct": 68.0, "calibrated_short_win_rate_pct": 22.0},
                    # 其余 4 只正常候选 -> 连同 SOXX 共 5 只，受 4 槽上限截断为前 4 只
                    "AAPL": {"ticker": "AAPL", "calibrated_win_rate_pct": 65.0, "calibrated_short_win_rate_pct": 25.0},
                    "MSFT": {"ticker": "MSFT", "calibrated_win_rate_pct": 64.0, "calibrated_short_win_rate_pct": 26.0},
                    "AMZN": {"ticker": "AMZN", "calibrated_win_rate_pct": 63.0, "calibrated_short_win_rate_pct": 27.0},
                    "GOOGL": {"ticker": "GOOGL", "calibrated_win_rate_pct": 60.0, "calibrated_short_win_rate_pct": 30.0},
                },
            }
            live_q = {
                "SOXX": {"active_price": 200.0, "prev_close": 198.0},
                "SOXL": {"active_price": 40.0, "prev_close": 39.5},
                "SOXS": {"active_price": 50.0, "prev_close": 50.5},
                "EARN1": {"active_price": 100.0, "prev_close": 99.0},
                "GAP1": {"active_price": 105.5, "prev_close": 100.0},  # +5.5% 跳空
                "AAPL": {"active_price": 220.0, "prev_close": 219.0},
                "MSFT": {"active_price": 410.0, "prev_close": 408.0},
                "AMZN": {"active_price": 210.0, "prev_close": 208.0},
                "GOOGL": {"active_price": 180.0, "prev_close": 179.0},
            }
            earn_map = {"EARN1": {"days_to_earnings": 3, "next_earnings_date": "2026-11-19"}}
            r1 = cte.run_copy_trade_cycle(
                ai_predictions=ai_preds,
                live_quotes=live_q,
                earnings_map=earn_map,
                session_code="REGULAR",
                now_et=_et(2026, 11, 16, 9, 40),
                ledger_path=lp,
                dry_run=False,
            )
            opened_tks = [p["ticker"] for p in r1["opened"]]
            self.assertEqual(len(opened_tks), 4)
            self.assertEqual(opened_tks, ["SOXX", "AAPL", "MSFT", "AMZN"])
            self.assertNotIn("SOXL", opened_tks)
            self.assertNotIn("SOXS", opened_tks)
            self.assertNotIn("EARN1", opened_tks)
            self.assertNotIn("GAP1", opened_tks)
            self.assertNotIn("GOOGL", opened_tks)

            # 次日满仓 4 槽时，同标的与额外标的全部不再重复开仓
            r2 = cte.run_copy_trade_cycle(
                ai_predictions=ai_preds,
                live_quotes=live_q,
                earnings_map=earn_map,
                session_code="REGULAR",
                now_et=_et(2026, 11, 17, 9, 40),
                ledger_path=lp,
                dry_run=False,
            )
            self.assertEqual(len(r2["opened"]), 0)
            self.assertEqual(len(r2["ledger"]["open_positions"]), 4)

    def test_05_dry_run_guard_zero_disk_write_and_zero_alerts(self):
        """验证 RADAR_DISABLE_EXTERNAL_ALERTS=1 下绝不写入账本文件、绝不触发任何外部提醒。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            lp = os.path.join(tmpdir, "copy_trade_ledger.json")
            alerts_called = []
            ai_preds = {
                "market_regime": "BULL_TREND",
                "by_ticker": {
                    "AAPL": {"ticker": "AAPL", "calibrated_win_rate_pct": 68.0, "calibrated_short_win_rate_pct": 20.0},
                },
            }
            live_q = {"AAPL": {"active_price": 220.0, "prev_close": 219.0}}
            with mock.patch.dict(os.environ, {"RADAR_DISABLE_EXTERNAL_ALERTS": "1"}):
                res = cte.run_copy_trade_cycle(
                    ai_predictions=ai_preds,
                    live_quotes=live_q,
                    session_code="REGULAR",
                    now_et=_et(2026, 11, 16, 9, 40),
                    ledger_path=lp,
                    alert_sender=lambda t, b, k, c: alerts_called.append(t),
                )
            self.assertTrue(res["dry_run"])
            self.assertFalse(res["persisted"])
            self.assertFalse(os.path.exists(lp))
            self.assertEqual(len(alerts_called), 0)

    def test_06_kol_alerts_discord_copy_embed_and_drawer_zero_share_count_wording(self):
        """验证所有 KOL 推送、Discord /copy 卡片与网页抽屉绝不出现“建议股数”，空账本优雅展示起跑线。"""
        pos = {
            "ticker": "NVDA",
            "direction": "SHORT",
            "slot_weight": 0.25,
            "entry_price": 140.20,
            "tp_price": 131.50,
            "sl_price": 146.10,
            "expiry_date": "2026-12-02",
            "calibrated_prob_pct": 64.5,
        }
        t_open, b_open = cte.format_kol_open_alert(pos)
        self.assertIn("25%仓位", b_open)
        self.assertIn("做空 NVDA", b_open)
        self.assertNotIn("建议股数", t_open + b_open)

        empty_ldg = cte.create_empty_copy_ledger(now_et=_et(2026, 11, 24, 10, 0))
        tr = {
            "ticker": "NVDA",
            "direction": "SHORT",
            "outcome": "TP_HIT",
            "entry_price": 140.20,
            "exit_price": 131.50,
            "net_return_pct": 6.18,
            "pnl_usd": 15.45,
        }
        empty_ldg["total_equity"] = 1015.45
        empty_ldg["total_return_pct"] = 1.55
        t_close, b_close = cte.format_kol_close_alert(tr, empty_ldg)
        self.assertIn("止盈平仓", b_close)
        self.assertIn("$1,015.45", b_close)
        self.assertNotIn("建议股数", t_close + b_close)

        # Discord /copy embed (空账本隔离测试)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_lp = os.path.join(tmpdir, "copy_trade_ledger.json")
            emb = bot.build_copy_embed_dict(copy_ledger_path=tmp_lp)
            self.assertFalse(os.path.exists(tmp_lp))
            emb_txt = emb["title"] + emb["description"] + " ".join(f["value"] for f in emb["fields"])
            self.assertIn("$1,000.00", emb_txt)
            self.assertNotIn("建议股数", emb_txt)

            # 网页抽屉 HTML（空账本展示 $1,000.00 起跑线，无建议股数，无币 emoji）
            drawer_html = cte.render_copy_trade_drawer_html(ledger_path=tmp_lp)
            self.assertIn("$1,000.00 起跑线", drawer_html)
            self.assertNotIn("建议股数", drawer_html)
            self.assertNotIn("\U0001fa99", drawer_html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
