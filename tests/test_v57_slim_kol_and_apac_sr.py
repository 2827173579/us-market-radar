# -*- coding: utf-8 -*-
"""
V5.7 精简版 KOL 狙击引擎 (Slim_2S_BE26_ETF52) + 温度计未截断审计 + 亚太/A股前复权撑压与 /check 4项修复隔离单测
全程使用 tmp_path，严禁触碰任何生产账本或外发消息。
"""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import apac_ashare_feed as aaf
import copy_trade_engine as cte
import copy_trade_kol_engine as kol
import discord_radar_bot as bot
import market_temperature_engine as mte

ET_TZ = ZoneInfo("America/New_York")


def _mk_ai(tk: str, p_l: float, p_s: float) -> dict:
    return {
        "by_ticker": {
            tk: {
                "ticker": tk,
                "calibrated_win_rate_pct": p_l,
                "calibrated_short_win_rate_pct": p_s,
                "is_crash_halt": False,
                "is_cooling_halt": False,
            }
        }
    }


def _mk_quote(px: float, vwap: float, rvol: float = 1.8, rsi_5m: float = 55.0, **extra) -> dict:
    q = {
        "last": px,
        "active_price": px,
        "ext_price": px,
        "prev_close": px,
        "pct_change": 0.5,
        "vwap": vwap,
        "vwap_diff_pct": round((px - vwap) / vwap * 100.0, 2) if vwap > 0 else 0.0,
        "rvol_5m": rvol,
        "rsi_5m": rsi_5m,
        "day_low": px * 0.992,
        "day_high": px * 1.008,
        "s1": px * 0.995,
        "r1": px * 0.999,
        "has_realtime": True,
    }
    q.update(extra)
    return q


def test_v57_etf_independent_threshold_and_no_short_inverse():
    cur_et = datetime(2026, 11, 25, 10, 30, 0, tzinfo=ET_TZ)
    # 1) 7 只核心 ETF (SOXX/KSTR) 在 53% vs 46% (净差 7%) 过关，个股 NVDA 同分被拦
    ai = {
        "by_ticker": {
            "SOXX": {"ticker": "SOXX", "calibrated_win_rate_pct": 53.0, "calibrated_short_win_rate_pct": 46.0},
            "KSTR": {"ticker": "KSTR", "calibrated_win_rate_pct": 52.5, "calibrated_short_win_rate_pct": 46.0},
            "NVDA": {"ticker": "NVDA", "calibrated_win_rate_pct": 53.0, "calibrated_short_win_rate_pct": 46.0},
        }
    }
    quotes = {
        "QQQ": _mk_quote(500.0, 499.0),
        "SOXX": _mk_quote(220.0, 219.0, rvol=1.8, rsi_5m=58.0, r1=219.5),
        "KSTR": _mk_quote(15.0, 14.9, rvol=1.8, rsi_5m=58.0, r1=14.95),
        "NVDA": _mk_quote(140.0, 139.0, rvol=1.8, rsi_5m=58.0, r1=139.5),
    }
    cands = kol.screen_kol_sniper_candidates(
        ai_predictions=ai,
        live_quotes=quotes,
        open_positions=[],
        cur_et=cur_et,
        market_regime="BULL_EXPANSION",
    )
    tks = {c["ticker"]: c for c in cands}
    assert "SOXX" in tks and tks["SOXX"]["entry_tag"] == "MAIN_AI_ETF"
    assert "KSTR" in tks and tks["KSTR"]["entry_tag"] == "MAIN_AI_ETF"
    assert "NVDA" not in tks  # 个股仍需 >= 55% / 10%

    # 2) 严禁做空反向/波动率 ETF (SOXS / SQQQ / VIXY)，即使空头胜率 75% 也不得开空
    ai_inv = {
        "by_ticker": {
            "SOXS": {"ticker": "SOXS", "calibrated_win_rate_pct": 25.0, "calibrated_short_win_rate_pct": 75.0},
            "SQQQ": {"ticker": "SQQQ", "calibrated_win_rate_pct": 25.0, "calibrated_short_win_rate_pct": 75.0},
            "VIXY": {"ticker": "VIXY", "calibrated_win_rate_pct": 25.0, "calibrated_short_win_rate_pct": 75.0},
        }
    }
    q_inv = {
        "QQQ": _mk_quote(500.0, 501.0),
        "SOXS": _mk_quote(20.0, 20.1, rvol=1.8, rsi_5m=45.0, pct_change=-2.0, is_broken_bearish=True),
        "SQQQ": _mk_quote(18.0, 18.1, rvol=1.8, rsi_5m=45.0, pct_change=-2.0, is_broken_bearish=True),
        "VIXY": _mk_quote(30.0, 30.1, rvol=1.8, rsi_5m=45.0, pct_change=-2.0, is_broken_bearish=True),
    }
    cands_inv = kol.screen_kol_sniper_candidates(
        ai_predictions=ai_inv,
        live_quotes=q_inv,
        open_positions=[],
        cur_et=cur_et,
        market_regime="BULL_EXPANSION",
    )
    assert cands_inv == []

    # 3) 反向 ETF 顺势做多 (SOXS LONG) 允许过关
    ai_soxs_l = _mk_ai("SOXS", 54.0, 45.0)
    q_soxs_l = {
        "QQQ": _mk_quote(500.0, 502.0, pct_change=-1.5),
        "SOXS": _mk_quote(22.0, 21.8, rvol=1.9, rsi_5m=60.0, r1=21.9),
    }
    cands_l = kol.screen_kol_sniper_candidates(
        ai_predictions=ai_soxs_l,
        live_quotes=q_soxs_l,
        open_positions=[],
        cur_et=cur_et,
        market_regime="BEAR_DEFENSE",
    )
    assert len(cands_l) == 1 and cands_l[0]["ticker"] == "SOXS" and cands_l[0]["direction"] == "LONG"


def test_v57_be26_ladder_and_50pct_runner(tmp_path):
    ldg_path = str(tmp_path / "kol_v57_be26.json")
    t0 = datetime(2026, 11, 25, 10, 0, 0, tzinfo=ET_TZ)
    ldg = kol.create_empty_kol_ledger(now_et=t0)
    # 放入一笔已加满 50% ($500) 的 1x 多单 NVDA @ 100.00，初始止损 96.50
    ldg["cash"] = 500.0
    ldg["open_positions"].append({
        "trade_id": "KOL_NVDA_LONG_2026-11-25_1",
        "ticker": "NVDA",
        "direction": "LONG",
        "setup": "SWEEP_RECLAIM_LONG",
        "entry_tag": "NORMAL",
        "stage": 2,
        "can_add": False,
        "slot_weight": 0.50,
        "peak_slot_weight": 0.50,
        "allocated_capital": 500.0,
        "entry_date": "2026-11-25",
        "entry_time_et": "2026-11-25 09:50:00",
        "orig_entry_price": 100.0,
        "entry_price": 100.0,
        "last_price": 100.0,
        "best_price": 100.0,
        "sl_price": 96.5,
        "fast_be_trimmed": False,
        "partial_tp_done": False,
        "be_stop_active": False,
        "partial_closed_capital": 0.0,
        "partial_realized_pnl_usd": 0.0,
        "max_favorable_return_pct": 0.0,
        "expiry_date": "2026-12-01",
    })
    kol.save_kol_ledger(ldg, ldg_path)

    # 涨至 +2.7% ($102.70) -> 触发 BE26：先平 25% ($125)，余仓 $375 (75%)，SL 推至 100.30
    res1 = kol.run_kol_copy_trade_cycle(
        ai_predictions={},
        live_quotes={"NVDA": _mk_quote(102.70, 101.5)},
        session_code="REGULAR",
        now_et=t0 + timedelta(minutes=15),
        ledger_path=ldg_path,
        dry_run=False,
    )
    assert len(res1["fast_be_now"]) == 1
    assert len(res1["partials_now"]) == 0
    pos1 = res1["ledger"]["open_positions"][0]
    assert pos1["fast_be_trimmed"] is True
    assert pos1["be_stop_active"] is True
    assert pos1["sl_price"] == 100.30
    assert abs(pos1["allocated_capital"] - 375.0) <= 0.1

    # 再涨至 +4.2% ($104.20) -> 触发一阶落袋：仅再平余仓 1/3 ($125)，保留精确 50% ($250) 尾仓追踪！
    res2 = kol.run_kol_copy_trade_cycle(
        ai_predictions={},
        live_quotes={"NVDA": _mk_quote(104.20, 102.0)},
        session_code="REGULAR",
        now_et=t0 + timedelta(minutes=30),
        ledger_path=ldg_path,
        dry_run=False,
    )
    assert len(res2["partials_now"]) == 1
    pos2 = res2["ledger"]["open_positions"][0]
    assert pos2["partial_tp_done"] is True
    assert abs(pos2["allocated_capital"] - 250.0) <= 0.5
    assert abs(pos2["partial_closed_capital"] - 250.0) <= 0.5


def test_v57_overnight_apac_pilot_independent_of_main_slots(tmp_path):
    ldg_path = str(tmp_path / "kol_v57_ov.json")
    t1 = datetime(2026, 11, 25, 22, 0, 0, tzinfo=ET_TZ)
    ai = _mk_ai("SOXX", 56.0, 42.0)
    apac_bull = {"chip_group": {"bias": "BULLISH", "score": 78.0, "weighted_pct": 1.4}, "soxx_lead_bias": "BULLISH"}

    # 宽点差 (45 bps > 35 bps) 拦截
    res_wide = kol.run_kol_copy_trade_cycle(
        ai_predictions=ai,
        live_quotes={"SOXX": _mk_quote(220.0, 219.0, spread_bps=45.0)},
        apac_chip_sentiment=apac_bull,
        session_code="OVERNIGHT",
        now_et=t1,
        ledger_path=ldg_path,
        dry_run=False,
    )
    assert len(res_wide["opened_now"]) == 0

    # 第 1 轮合格点差 (20 bps) -> 记录待确认，不开仓
    res_p1 = kol.run_kol_copy_trade_cycle(
        ai_predictions=ai,
        live_quotes={"SOXX": _mk_quote(220.0, 219.0, spread_bps=20.0)},
        apac_chip_sentiment=apac_bull,
        session_code="OVERNIGHT",
        now_et=t1 + timedelta(minutes=1),
        ledger_path=ldg_path,
        dry_run=False,
    )
    assert len(res_p1["opened_now"]) == 0
    assert res_p1["ledger"]["overnight_pending_confirm"]["ticker"] == "SOXX"

    # 第 2 轮 (>= 60s 后) 确认 -> 正式开 10% 独立轻仓
    res_p2 = kol.run_kol_copy_trade_cycle(
        ai_predictions=ai,
        live_quotes={"SOXX": _mk_quote(220.5, 219.0, spread_bps=20.0)},
        apac_chip_sentiment=apac_bull,
        session_code="OVERNIGHT",
        now_et=t1 + timedelta(minutes=3),
        ledger_path=ldg_path,
        dry_run=False,
    )
    assert len(res_p2["opened_now"]) == 1
    ov_pos = res_p2["opened_now"][0]
    assert ov_pos["entry_tag"] == "OVERNIGHT_APAC_PILOT"
    assert ov_pos["slot_weight"] == pytest.approx(0.10)
    assert ov_pos["can_add"] is False

    # 验证夜盘特批仓不挤占正盘 2 个主槽位（free_slots 仍为 2！）
    ai_rth = {
        "by_ticker": {
            "NVDA": {"ticker": "NVDA", "calibrated_win_rate_pct": 62.0, "calibrated_short_win_rate_pct": 35.0},
            "AVGO": {"ticker": "AVGO", "calibrated_win_rate_pct": 61.0, "calibrated_short_win_rate_pct": 36.0},
        }
    }
    q_rth = {
        "QQQ": _mk_quote(500.0, 499.0),
        "NVDA": _mk_quote(140.0, 139.0, rvol=1.8, rsi_5m=58.0, r1=139.5),
        "AVGO": _mk_quote(180.0, 179.0, rvol=1.8, rsi_5m=58.0, r1=179.5),
    }
    rth_cands = kol.screen_kol_sniper_candidates(
        ai_predictions=ai_rth,
        live_quotes=q_rth,
        open_positions=res_p2["ledger"]["open_positions"],
        cur_et=datetime(2026, 11, 26, 10, 0, 0, tzinfo=ET_TZ),
        market_regime="BULL_EXPANSION",
    )
    assert len(rth_cands) == 2


def test_v57_early_close_black_friday(tmp_path):
    ldg_path = str(tmp_path / "kol_v57_early.json")
    t_bf = datetime(2026, 11, 27, 12, 50, 0, tzinfo=ET_TZ)
    ldg = kol.create_empty_kol_ledger(now_et=t_bf)
    ldg["cash"] = 700.0
    ldg["open_positions"].append({
        "trade_id": "KOL_NVDA_LONG_2026-11-23_1",
        "ticker": "NVDA",
        "direction": "LONG",
        "entry_tag": "NORMAL",
        "stage": 1,
        "slot_weight": 0.30,
        "allocated_capital": 300.0,
        "entry_date": "2026-11-23",
        "entry_time_et": "2026-11-23 10:00:00",
        "orig_entry_price": 140.0,
        "entry_price": 140.0,
        "last_price": 141.0,
        "best_price": 141.0,
        "sl_price": 135.5,
        "expiry_date": "2026-11-27",
    })
    kol.save_kol_ledger(ldg, ldg_path)
    res = kol.run_kol_copy_trade_cycle(
        ai_predictions={},
        live_quotes={"NVDA": _mk_quote(141.5, 141.0)},
        session_code="REGULAR",
        now_et=t_bf,
        ledger_path=ldg_path,
        dry_run=False,
    )
    assert len(res["closed_now"]) == 1
    assert res["closed_now"][0]["exit_reason"] == "T3_EXPIRE"


def test_market_temperature_exposes_raw_macro_bottom_score():
    temp = mte.compute_market_temperature(
        macro_data={
            "QQQ": {"last": 450.0, "pct_chg": 0.8, "vwap_diff_pct": 0.2, "bias": -10.0, "bias20": -7.0, "rsi": 30.0},
            "SPY": {"last": 520.0, "pct_chg": 0.7, "vwap_diff_pct": 0.2, "bias": -8.0, "bias20": -6.0, "rsi": 32.0},
        },
        stocks_data=[{"ticker": f"STK{i}", "last": 90.0, "ma20": 100.0, "rsi": 28.0} for i in range(10)],
        vix_regime={"vix_spot": 34.0, "vix_term_ratio": 1.08, "vix_state": "BACKWARDATION_BRAKE", "vix_bottom_signal": False, "vix_drawdown_from_peak_pct": 5.0},
        cross_market_liquidity={"hyg_ief_5d_pct": -2.0},
        session_code="REGULAR",
    )
    assert temp["knife_catch_locked"] is True
    assert temp["macro_bottom_score"] <= 50.0
    assert "raw_macro_bottom_score" in temp
    assert temp["raw_macro_bottom_score"] > temp["macro_bottom_score"]


def test_apac_split_sanitizer_and_realistic_tiered_sr():
    # 构造模拟未复权 2:1 拆分 + 下跌趋势数据（旧高 0.728，现价 0.383）
    rows = []
    # 前 30 天未复权价 ~0.76 -> 第 30 天 2:1 拆分成 ~0.38，之后在 0.37~0.42 区间震荡
    for i in range(60):
        dt = f"2026-09-{(i % 28) + 1:02d}"
        if i < 30:
            c = 0.82 - i * 0.002
            rows.append([dt, c, c + 0.012, c - 0.012, c, 1000000.0])
        else:
            # 2:1 拆分后真实区间 0.37 ~ 0.415
            wave = [0.405, 0.412, 0.398, 0.390, 0.395, 0.402, 0.388, 0.380, 0.385, 0.383][i % 10]
            rows.append([dt, wave, wave + 0.006, wave - 0.006, wave, 1200000.0])
    sanitized = aaf._sanitize_unadjusted_splits(rows, decimals=3)
    tech = aaf._compute_technicals_from_daily(sanitized, spot_price=0.383, decimals=3)
    # 拆分平滑后 MA50 绝不会飙到 0.525，应在 ~0.39 附近
    assert tech["ma50"] < 0.45
    assert tech["bias_ma20"] is not None
    # R1 应在合理可操作区间 (+1.2% ~ +12%)，绝无山顶僵尸压力位
    r1_pct = (tech["r1"] - 0.383) / 0.383 * 100.0
    r2_pct = (tech["r2"] - 0.383) / 0.383 * 100.0
    assert 1.0 <= r1_pct <= 12.5
    assert r1_pct < r2_pct <= 24.0
    assert tech["s2"] < tech["s1"] < 0.383


def test_discord_check_apac_4_bug_fixes():
    snap = {
        "updated_at_et": "2026-11-26 05:00:00 ET",
        "apac_ashare_meta": {"updated_at_cst": "2026-11-26 16:05:00 CST"},
        "apac_ashare_items": {
            "515880": {
                "ticker": "515880",
                "name": "通信ETF国泰",
                "cat": "A股通信",
                "sub": "A股光模块/CPO核心",
                "kind": "ASHARE_ETF",
                "price_unit": "cny",
                "last": 0.383,
                "pct_change": 1.05,
                "ma20": 0.398,
                "ma50": 0.415,
                "rsi": 36.2,
                "s1": 0.368,
                "s2": 0.354,
                "r1": 0.398,
                "r2": 0.415,
                "turnover": 5.2e8,
            }
        },
        "tickers": {"NVDA": {"ticker": "NVDA", "name": "英伟达", "last": 140.0}},
    }
    emb = bot.build_check_embed_dict("515880", snap)
    full_txt = " ".join(f["value"] for f in emb["fields"]) + " " + emb["footer"]["text"]
    assert "$" not in full_txt
    assert "偏离 --" not in full_txt
    assert "偏离 -3.77%" in full_txt
    assert "偏弱/临近超卖" in full_txt
    assert "中性区间" not in full_txt
    assert "CST CST" not in emb["footer"]["text"]
    assert "16:05:00 CST" in emb["footer"]["text"]
    assert "不计入纯美股温度计" not in full_txt
