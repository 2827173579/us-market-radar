# -*- coding: utf-8 -*-
"""
V5.0 专项单元测试：股指期货先导哨 (NQ=F / ES=F) + 大盘/日内泡沫与抄底四维温度计
================================================================================
100% Mock 离线隔离（使用 tempfile.TemporaryDirectory），严禁写入根目录任何生产 HTML 或账本。
"""

import math
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

import discord_radar_bot as bot
import integrated_market_radar as radar
import market_temperature_engine as mte


def _build_mock_intraday_df(
    base_price: float = 20000.0,
    n_bars: int = 80,
    drift_per_bar: float = 1.5,
    last_bar_reversal: bool = False,
) -> pd.DataFrame:
    start = datetime(2026, 11, 20, 9, 30, tzinfo=timezone.utc)
    idx = [start + timedelta(minutes=5 * i) for i in range(n_bars)]
    closes = []
    opens = []
    highs = []
    lows = []
    vols = []
    px = float(base_price)
    for i in range(n_bars):
        o = px
        c = px + drift_per_bar
        h = max(o, c) + abs(drift_per_bar) * 0.5 + 2.0
        l = min(o, c) - abs(drift_per_bar) * 0.5 - 2.0
        v = 1200.0 + (i % 7) * 80.0
        if last_bar_reversal and i == n_bars - 1:
            o = px - 12.0
            c = px + 35.0
            l = o - 25.0
            h = c + 5.0
            v = 4500.0
        opens.append(o)
        closes.append(c)
        highs.append(h)
        lows.append(l)
        vols.append(v)
        px = c
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=pd.DatetimeIndex(idx),
    )


def _build_mock_daily_df(base_price: float = 20000.0, n_days: int = 40) -> pd.DataFrame:
    start = datetime(2026, 9, 15, tzinfo=timezone.utc)
    idx = [start + timedelta(days=i) for i in range(n_days)]
    closes = [base_price * (1.0 + 0.001 * i) for i in range(n_days)]
    return pd.DataFrame(
        {
            "Open": [c * 0.996 for c in closes],
            "High": [c * 1.009 for c in closes],
            "Low": [c * 0.991 for c in closes],
            "Close": closes,
            "Volume": [100000.0] * n_days,
        },
        index=pd.DatetimeIndex(idx),
    )


def _sample_pure_stocks(n: int = 10, above_ma20_ratio: float = 0.6, rsi_val: float = 55.0):
    tickers = ["NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AVGO", "LLY", "WMT", "V"][:n]
    out = []
    cut = int(round(len(tickers) * above_ma20_ratio))
    for i, tk in enumerate(tickers):
        is_above = i < cut
        out.append(
            {
                "ticker": tk,
                "cat": "科技龙头",
                "last": 110.0 if is_above else 90.0,
                "ma20": 100.0,
                "bias20": 10.0 if is_above else -10.0,
                "bias": 8.0 if is_above else -8.0,
                "rsi": rsi_val,
            }
        )
    return out


class TestV50FuturesAndTemperature(unittest.TestCase):
    def test_01_btc_crypto_inverse_zero_impact(self):
        """BTC/MSTR/COIN/XAUUSD/GLD/TLT/SQQQ/SOXS/VIXY ±50% 极端波动对 4 项温度计分数影响严格为 0.0。"""
        base_macro = {
            "QQQ": {"last": 500.0, "pct_chg": 0.8, "bias": 4.2, "bias20": 2.4, "vwap_diff_pct": 0.45, "rsi_5m": 58.0, "atr": 7.5},
            "SPY": {"last": 590.0, "pct_chg": 0.6, "bias": 3.5, "bias20": 1.9, "vwap_diff_pct": 0.35, "rsi_5m": 56.0, "atr": 6.2},
            "SMH": {"last": 250.0, "pct_chg": 1.4, "bias": 6.0, "bias20": 4.0, "rsi": 63.0},
            "GLD": {"last": 320.0, "pct_chg": 0.2, "bias": 1.0, "rsi": 50.0},
            "TLT": {"last": 90.0, "pct_chg": -0.3, "bias": -2.0, "rsi": 45.0},
            "SQQQ": {"last": 10.0, "pct_chg": -2.4, "bias": -12.0, "rsi": 30.0},
            "SOXS": {"last": 20.0, "pct_chg": -4.2, "bias": -15.0, "rsi": 28.0},
            "VIXY": {"last": 30.0, "pct_chg": -2.0, "bias": -5.0, "rsi": 40.0},
        }
        base_stocks = _sample_pure_stocks(10, above_ma20_ratio=0.7, rsi_val=60.0) + [
            {"ticker": "BTC-USD", "cat": "数字资产", "last": 60000.0, "ma20": 60000.0, "bias": 0.0, "rsi": 50.0},
            {"ticker": "IBIT", "cat": "数字资产", "last": 40.0, "ma20": 40.0, "bias": 0.0, "rsi": 50.0},
            {"ticker": "MSTR", "cat": "加密资产", "last": 100.0, "ma20": 100.0, "bias": 0.0, "rsi": 50.0},
            {"ticker": "COIN", "cat": "数字资产", "last": 120.0, "ma20": 120.0, "bias": 0.0, "rsi": 50.0},
            {"ticker": "MARA", "cat": "数字资产", "last": 15.0, "ma20": 15.0, "bias": 0.0, "rsi": 50.0},
            {"ticker": "XAUUSD", "cat": "大宗商品", "last": 3500.0, "ma20": 3500.0, "bias": 0.0, "rsi": 50.0},
        ]
        vr = {"vix_spot": 17.5, "vix_term_ratio": 0.89, "vix_state": "CALM", "vix_bottom_signal": False, "vix_drawdown_from_peak_pct": 6.0}
        liq = {"hyg_ief_5d_pct": 0.4}

        res_base = mte.compute_market_temperature(
            macro_data=base_macro,
            stocks_data=base_stocks,
            vix_regime=vr,
            cross_market_liquidity=liq,
            session_code="REGULAR",
        )

        # 注入 +50% 与 -50% 极端暴涨暴跌到所有非纯美股标的
        for mult, bias_v, rsi_v in ((1.5, 50.0, 99.0), (0.5, -50.0, 2.0)):
            mut_macro = {k: dict(v) for k, v in base_macro.items()}
            for ex_tk in ("GLD", "TLT", "SQQQ", "SOXS", "VIXY"):
                mut_macro[ex_tk]["last"] *= mult
                mut_macro[ex_tk]["pct_chg"] = (mult - 1.0) * 100.0
                mut_macro[ex_tk]["bias"] = bias_v
                mut_macro[ex_tk]["rsi"] = rsi_v
            mut_stocks = []
            for s in base_stocks:
                d = dict(s)
                if d["ticker"] in {"BTC-USD", "IBIT", "MSTR", "COIN", "MARA", "XAUUSD"}:
                    d["last"] = d["ma20"] * mult
                    d["bias"] = bias_v
                    d["bias20"] = bias_v
                    d["rsi"] = rsi_v
                mut_stocks.append(d)

            res_mut = mte.compute_market_temperature(
                macro_data=mut_macro,
                stocks_data=mut_stocks,
                vix_regime=vr,
                cross_market_liquidity=liq,
                session_code="REGULAR",
            )
            for key in ("macro_bubble_score", "intraday_bubble_score", "macro_bottom_score", "intraday_bottom_score"):
                self.assertEqual(
                    res_mut[key] - res_base[key],
                    0.0,
                    f"Excluded asset shock perturbed {key}: base={res_base[key]} vs mut={res_mut[key]}",
                )
            self.assertEqual(res_mut["pure_us_equity_count"], 10)
            self.assertTrue(res_mut["excluded_btc"])

    def test_02_macro_bottom_knife_catch_lock_and_unlock(self):
        """VIX 倒挂未企稳期严格锁定 macro_bottom_score <= 50 且 knife_catch_locked=True；右侧确认后解锁 >= 65。"""
        oversold_macro = {
            "QQQ": {"last": 440.0, "pct_chg": -2.4, "bias": -11.0, "bias20": -8.5, "vwap_diff_pct": -1.2, "rsi_5m": 25.0, "atr": 9.0},
            "SPY": {"last": 530.0, "pct_chg": -1.9, "bias": -9.0, "bias20": -7.0, "vwap_diff_pct": -0.9, "rsi_5m": 27.0, "atr": 7.0},
            "SMH": {"last": 200.0, "pct_chg": -3.5, "bias": -14.0, "bias20": -10.0, "rsi": 26.0},
        }
        washout_stocks = _sample_pure_stocks(10, above_ma20_ratio=0.1, rsi_val=28.0)
        liq_stress = {"hyg_ief_5d_pct": -2.2}

        # 场景 A：VIX 倒挂刹车期（无右侧企稳）
        vr_locked = {
            "vix_spot": 35.9,
            "vix_term_ratio": 1.16,
            "vix_state": "BACKWARDATION_BRAKE",
            "vix_bottom_signal": False,
            "vix_drawdown_from_peak_pct": 0.0,
        }
        res_locked = mte.compute_market_temperature(
            macro_data=oversold_macro,
            stocks_data=washout_stocks,
            vix_regime=vr_locked,
            cross_market_liquidity=liq_stress,
            session_code="REGULAR",
        )
        self.assertTrue(res_locked["knife_catch_locked"])
        self.assertLessEqual(res_locked["macro_bottom_score"], 50.0)
        self.assertEqual(res_locked["macro_bottom_breakdown"]["layer2_right_side"], 0.0)
        self.assertIn("超卖观察坑", res_locked["macro_bottom_tier"])

        # 场景 B：恐慌出清 + 右侧企稳确认解锁
        rev_macro = {k: dict(v) for k, v in oversold_macro.items()}
        rev_macro["QQQ"]["pct_chg"] = 1.6
        rev_macro["QQQ"]["vwap_diff_pct"] = 0.5
        rev_macro["SPY"]["pct_chg"] = 1.2
        rev_macro["SPY"]["vwap_diff_pct"] = 0.4
        vr_unlocked = {
            "vix_spot": 31.0,
            "vix_term_ratio": 0.93,
            "vix_state": "WASHOUT_BOTTOM_WINDOW",
            "vix_bottom_signal": True,
            "vix_drawdown_from_peak_pct": 14.0,
        }
        res_unlocked = mte.compute_market_temperature(
            macro_data=rev_macro,
            stocks_data=washout_stocks,
            vix_regime=vr_unlocked,
            cross_market_liquidity=liq_stress,
            session_code="REGULAR",
        )
        self.assertFalse(res_unlocked["knife_catch_locked"])
        self.assertGreaterEqual(res_unlocked["macro_bottom_score"], 65.0)
        self.assertIn("右侧恐慌出清", res_unlocked["macro_bottom_tier"])

    def test_03_intraday_bottom_knife_catch_lock_and_unlock(self):
        """日内单边阴跌锁定 <= 50 并叠加倒挂期轻仓提示；5m 右侧企稳后解锁 >= 65。"""
        vr_back = {"vix_spot": 32.0, "vix_term_ratio": 1.08, "vix_state": "BACKWARDATION_BRAKE", "vix_bottom_signal": False}
        macro_selloff = {
            "QQQ": {"last": 460.0, "pct_chg": -1.9, "bias": -5.0, "vwap_diff_pct": -1.35, "rsi_5m": 24.0, "atr": 7.0, "near_intraday_low": True, "intraday_stabilized": False},
            "SPY": {"last": 550.0, "pct_chg": -1.4, "bias": -4.0, "vwap_diff_pct": -1.05, "rsi_5m": 26.0, "atr": 6.0, "near_intraday_low": True, "intraday_stabilized": False},
        }
        res_lock = mte.compute_market_temperature(macro_data=macro_selloff, vix_regime=vr_back, session_code="REGULAR")
        self.assertTrue(res_lock["intraday_knife_catch_locked"])
        self.assertLessEqual(res_lock["intraday_bottom_score"], 50.0)
        self.assertIn("大盘倒挂期仅限轻仓反抽", res_lock["intraday_bottom_tier"])

        macro_stab = {
            "QQQ": {"last": 462.0, "pct_chg": -1.5, "bias": -5.0, "vwap_diff_pct": -1.1, "rsi_5m": 28.0, "atr": 7.0, "near_intraday_low": False, "intraday_stabilized": True},
            "SPY": {"last": 552.0, "pct_chg": -1.2, "bias": -4.0, "vwap_diff_pct": -0.9, "rsi_5m": 29.0, "atr": 6.0, "near_intraday_low": False, "intraday_stabilized": True},
        }
        res_ok = mte.compute_market_temperature(macro_data=macro_stab, vix_regime=vr_back, session_code="REGULAR")
        self.assertFalse(res_ok["intraday_knife_catch_locked"])
        self.assertGreaterEqual(res_ok["intraday_bottom_score"], 65.0)
        self.assertIn("大盘倒挂期仅限轻仓反抽", res_ok["intraday_bottom_tier"])

    def test_04_bounds_finite_and_premarket_futures_fallback(self):
        """验证 4 项分数严格有限且在 [0, 100]，负涨幅收敛 <= 25，盘前/夜盘自动切期货实时数据源。"""
        fut_snap = {
            "degraded": False,
            "symbols": {
                "NQ=F": {"symbol": "NQ=F", "available": True, "weekend_closed": False, "last": 21200.0, "pct_chg": 2.3, "vwap_diff_pct": 1.5, "rsi_5m": 82.0, "atr_20d_pct": 1.5, "status_tag": "24H 期货实时"},
                "ES=F": {"symbol": "ES=F", "available": True, "weekend_closed": False, "last": 6050.0, "pct_chg": 1.8, "vwap_diff_pct": 1.2, "rsi_5m": 79.0, "atr_20d_pct": 1.1, "status_tag": "24H 期货实时"},
            },
        }
        res_pre = mte.compute_market_temperature(
            macro_data={"QQQ": {"last": 500.0, "pct_chg": -0.5, "bias": 15.0, "bias20": 10.0, "vwap_diff_pct": -0.4, "rsi_5m": 40.0}},
            stocks_data=_sample_pure_stocks(10, above_ma20_ratio=1.0, rsi_val=82.0),
            vix_regime={"vix_spot": 11.0, "vix_term_ratio": 0.78, "vix_state": "CALM"},
            futures_snapshot=fut_snap,
            session_code="PRE_MARKET",
        )
        self.assertEqual(res_pre["source_tag"], "期货实时")
        self.assertGreaterEqual(res_pre["intraday_bubble_score"], 75.0)
        self.assertIn("严禁追高", res_pre["intraday_bubble_tier"])
        for k in ("macro_bubble_score", "intraday_bubble_score", "macro_bottom_score", "intraday_bottom_score"):
            self.assertTrue(math.isfinite(res_pre[k]))
            self.assertGreaterEqual(res_pre[k], 0.0)
            self.assertLessEqual(res_pre[k], 100.0)

        # 常规盘中负涨幅时日内泡沫收敛至 <= 25
        res_neg = mte.compute_market_temperature(
            macro_data={"QQQ": {"last": 500.0, "pct_chg": -0.2, "bias": 2.0, "vwap_diff_pct": -0.1, "rsi_5m": 65.0}},
            session_code="REGULAR",
        )
        self.assertEqual(res_neg["source_tag"], "盘中现货")
        self.assertLessEqual(res_neg["intraday_bubble_score"], 25.0)

    def test_05_futures_fetch_mock_and_atomic_cache(self):
        """使用 Mock downloader 与 TemporaryDirectory 验证 NQ=F/ES=F 抓取、24H VWAP、原子缓存命中与单标的降级。"""
        nq_5m = _build_mock_intraday_df(20500.0, n_bars=60, drift_per_bar=2.0, last_bar_reversal=True)
        es_5m = _build_mock_intraday_df(5950.0, n_bars=60, drift_per_bar=0.8)
        nq_1d = _build_mock_daily_df(20300.0, n_days=40)
        es_1d = _build_mock_daily_df(5900.0, n_days=40)

        multi_5m = pd.concat({"NQ=F": nq_5m, "ES=F": es_5m}, axis=1)
        multi_1d = pd.concat({"NQ=F": nq_1d, "ES=F": es_1d}, axis=1)
        call_cnt = {"n": 0}

        def fake_dl(symbols, period="5d", interval="5m", **kwargs):
            call_cnt["n"] += 1
            return multi_5m if interval == "5m" else multi_1d

        with tempfile.TemporaryDirectory() as tmpdir:
            cpath = os.path.join(tmpdir, "futures_temp_cache.json")
            snap1 = mte.fetch_futures_snapshot(
                force_refresh=True,
                cache_path=cpath,
                ttl_seconds=180,
                session_code="PRE_MARKET",
                downloader=fake_dl,
                now_et=datetime(2026, 11, 20, 8, 30, tzinfo=timezone(timedelta(hours=-5))),
            )
            self.assertFalse(snap1["degraded"])
            self.assertTrue(snap1["symbols"]["NQ=F"]["available"])
            self.assertTrue(snap1["symbols"]["ES=F"]["available"])
            self.assertEqual(snap1["symbols"]["NQ=F"]["status_tag"], "24H 期货实时")
            self.assertGreater(len(snap1["symbols"]["NQ=F"]["series_24h"]["prices"]), 5)
            self.assertTrue(os.path.exists(cpath))

            # TTL 命中缓存：不应再次调用 downloader
            prev_calls = call_cnt["n"]
            snap2 = mte.fetch_futures_snapshot(
                force_refresh=False,
                cache_path=cpath,
                ttl_seconds=180,
                session_code="WEEKEND",
                downloader=fake_dl,
                now_et=datetime(2026, 11, 21, 12, 0, tzinfo=timezone(timedelta(hours=-5))),
            )
            self.assertTrue(snap2["from_cache"])
            self.assertEqual(call_cnt["n"], prev_calls)
            self.assertEqual(snap2["symbols"]["NQ=F"]["status_tag"], "周末休市·最近24H参考")

    def test_06_monitored_pool_strictly_79_no_futures(self):
        """验证 79 只可交易标的池 (14 ETF + 65 Stocks) 严格零污染，不含 NQ=F / ES=F。"""
        etfs = radar.MONITORED_TARGETS.get("ETFS", {})
        stocks = radar.MONITORED_TARGETS.get("STOCKS", {})
        self.assertEqual(len(etfs), 14)
        self.assertEqual(len(stocks), 65)
        self.assertEqual(len(etfs) + len(stocks), 79)
        all_tks = set(etfs.keys()) | set(stocks.keys())
        self.assertNotIn("NQ=F", all_tks)
        self.assertNotIn("ES=F", all_tks)

    def test_07_discord_temp_and_vix_embeds_plain_chinese(self):
        """验证 Discord Bot `/temp` 与 `/vix` 通俗化渲染、零禁用术语、零裸横线，且注册 5 个斜杠命令。"""
        snap = {
            "updated_at_et": "2026-11-23 09:40:00 ET",
            "session_code": "REGULAR",
            "session_name": "常规盘中",
            "market_regime": "BEAR_DEFENSE",
            "weather": "暴雨空仓",
            "vix_regime": {
                "vix_spot": 35.9,
                "vxn_spot": 42.1,
                "vxn_vix_spread": 6.2,
                "vix_term_ratio": 1.16,
                "vix_state": "BACKWARDATION_BRAKE",
                "tnx_yield": 4.52,
                "vix_10d_peak": 36.2,
                "vix_drawdown_from_peak_pct": 0.8,
            },
            "cross_market_liquidity": {"hyg_ief_5d_pct": -1.5, "dxy_spot": 103.2, "dxy_5d_pct": 0.9, "usdjpy_spot": 154.0, "usdjpy_5d_pct": -0.4},
            "opex_shield": {"active": False, "next_opex_date": "2026-12-18", "days_to_opex": 25},
            "futures": {
                "NQ=F": {"last": 20880.25, "pct_chg": -1.25, "vwap_diff_pct": -0.62, "status_tag": "24H 期货实时"},
                "ES=F": {"last": 5920.50, "pct_chg": -0.95, "vwap_diff_pct": -0.44, "status_tag": "24H 期货实时"},
            },
            "market_temperature": {
                "source_tag": "盘中现货",
                "macro_bubble_score": 18.5,
                "macro_bubble_tier": "冰点无泡沫",
                "intraday_bubble_score": 12.0,
                "intraday_bubble_tier": "日内无追高泡沫",
                "macro_bottom_score": 48.0,
                "macro_bottom_tier": "⛔ 超卖观察坑 · 倒挂未解禁抄底",
                "knife_catch_locked": True,
                "intraday_bottom_score": 44.0,
                "intraday_bottom_tier": "⛔ 日内阴跌寻底 · 禁左侧接刀（大盘倒挂期仅限轻仓反抽）",
                "intraday_knife_catch_locked": True,
                "headline_cn": "大盘处于恐慌倒挂期，接刀锁已生效，严禁左侧盲目抄底。",
            },
        }
        temp_emb = bot.build_temp_embed_dict(snap)
        vix_emb = bot.build_vix_embed_dict(snap)

        for label, emb in (("/temp", temp_emb), ("/vix", vix_emb)):
            parts = [emb.get("title", ""), emb.get("description", "")]
            for fld in emb.get("fields", []):
                parts.append(fld.get("name", ""))
                parts.append(fld.get("value", ""))
            combined = "\n".join(parts)
            self.assertIn("一句话结论", combined, f"{label} missing 一句话结论")
            for bad in ("BEAR_DEFENSE", "P25-P75", "5m RSI", "MA50 偏离", "期限矩阵", "信用利差"):
                self.assertNotIn(bad, combined, f"{label} leaked forbidden jargon {bad}")
            for line in combined.splitlines():
                self.assertNotEqual(line.strip(), "---", f"{label} has bare ---")

        self.assertIn("不含BTC", temp_emb["title"])
        self.assertIn("NQ=F", temp_emb["fields"][0]["value"])
        self.assertIn("ES=F", temp_emb["fields"][0]["value"])
        self.assertIn("期货先导 (NQ/ES)", vix_emb["fields"][2]["value"])

        client = bot.RadarBotClient(guild_id="1569503976477433987")
        cmd_names = {c.name for c in client.tree.get_commands()}
        self.assertEqual(cmd_names, {"check", "vix", "top5", "ledger", "temp"})


if __name__ == "__main__":
    unittest.main()
