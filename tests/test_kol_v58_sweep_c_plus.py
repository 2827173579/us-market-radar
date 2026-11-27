# -*- coding: utf-8 -*-
"""
V5.8 KOL 狙击引擎 (C_PLUS_PURE_BE) 专项离线隔离单测
全程 Mock + tmp_path，严禁访问网络、外发告警或触碰任何生产账本。
"""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import copy_trade_kol_engine as kol

ET_TZ = ZoneInfo("America/New_York")


def _mk_ai(items: dict) -> dict:
    out = {}
    for tk, (p_l, p_s) in items.items():
        out[tk] = {
            "ticker": tk,
            "calibrated_win_rate_pct": p_l,
            "calibrated_short_win_rate_pct": p_s,
            "is_crash_halt": False,
            "is_cooling_halt": False,
        }
    return {"by_ticker": out}


def _mk_quote(px: float, vwap: float, **extra) -> dict:
    q = {
        "last": px,
        "active_price": px,
        "ext_price": px,
        "prev_close": px,
        "pct_change": 0.2,
        "vwap": vwap,
        "vwap_diff_pct": round((px - vwap) / vwap * 100.0, 2) if vwap > 0 else 0.0,
        "rvol_5m": 1.25,
        "rsi_5m": 46.0,
        "prev_rsi_5m": 42.0,
        "open_5m": round(px * 0.996, 2),
        "low_5m": round(px * 0.992, 2),
        "prev_low_5m": round(px * 0.991, 2),
        "prev_close_5m": round(px * 0.995, 2),
        "is_5m_green": True,
        "day_open": round(px * 1.005, 2),
        "day_low": round(px * 0.991, 2),
        "day_high": round(px * 1.01, 2),
        "s1": round(px * 0.997, 2),
        "r1": round(px * 1.04, 2),
        "has_realtime": True,
    }
    q.update(extra)
    return q


def test_v58_underwater_sweep_reclaim_entry_and_tight_sl(tmp_path):
    """水下 SWEEP_RECLAIM_LONG 无 VWAP 硬锁正常 30% 入场，且止损紧贴 sweep_low (~1% 窄损)。"""
    ldg_path = str(tmp_path / "kol_v58_uw.json")
    t0 = datetime(2026, 11, 25, 10, 0, 0, tzinfo=ET_TZ)

    # PLTR 现价 173.38，VWAP 174.47 (-0.62% 水下)，扫穿 S1=172.80 针尖 172.00 后 5m 收阳收回 S1
    ai = _mk_ai({"PLTR": (62.0, 35.0)})
    quotes = {
        "QQQ": _mk_quote(500.0, 499.5, pct_change=0.3),
        "PLTR": _mk_quote(
            173.38,
            174.47,
            open_5m=172.40,
            low_5m=172.00,
            prev_low_5m=172.10,
            prev_close_5m=172.50,
            s1=172.80,
            r1=180.00,
            day_low=172.00,
            rvol_5m=1.2,
            rsi_5m=45.0,
            prev_rsi_5m=40.0,
        ),
    }

    res = kol.run_kol_copy_trade_cycle(
        ai_predictions=ai,
        live_quotes=quotes,
        market_regime="NEUTRAL_CHOP",
        session_code="REGULAR",
        now_et=t0,
        ledger_path=ldg_path,
        dry_run=False,
    )
    assert len(res["opened_now"]) == 1
    pos = res["opened_now"][0]
    assert pos["ticker"] == "PLTR"
    assert pos["setup"] == "SWEEP_RECLAIM_LONG"
    assert pos["slot_weight"] == pytest.approx(0.30)
    assert pos["entered_below_vwap"] is True
    assert pos["vwap_be_pushed"] is False
    assert pos["sweep_low_anchor"] == pytest.approx(172.00)

    # 1x 针尖窄损 = 172.00 * (1 - 0.0025) = 171.57 (~1.04% 风险宽，远小于旧 V5.7 的 2.0%~3.5%)
    expected_sl = round(172.00 * (1.0 - kol.SWEEP_SL_BUF_1X), 2)
    assert pos["sl_price"] == pytest.approx(expected_sl)
    risk_pct = (pos["entry_price"] - pos["sl_price"]) / pos["entry_price"] * 100.0
    assert 0.8 <= risk_pct <= 1.3


def test_v58_qqq_flush_all_blocks_longs_and_exempts_inverse_etfs():
    """QQQ_FLUSH_ALL (-1.3%, vwap_diff=-0.6%) 同时拦截水下与水上普通多头 SWEEP，但豁免 SOXS/SQQQ；缺失 QQQ 时优雅降级。"""
    cur_et = datetime(2026, 11, 25, 13, 30, 0, tzinfo=ET_TZ)
    ai = _mk_ai({
        "NVDA": (64.0, 32.0),
        "MRVL": (63.0, 33.0),
        "SOXS": (56.0, 40.0),
        "SQQQ": (55.0, 41.0),
    })

    # NVDA 在水下 (-1.2%)，MRVL 略高于 VWAP (+0.25%)，两者都满足扫损收回 S1，但 QQQ 处于单边急杀踩踏期
    quotes_flush = {
        "QQQ": _mk_quote(490.0, 493.0, pct_change=-1.30, vwap_diff_pct=-0.61),
        "NVDA": _mk_quote(138.0, 139.68, open_5m=137.0, low_5m=136.5, prev_low_5m=136.8, s1=137.5, r1=145.0, rvol_5m=1.3),
        "MRVL": _mk_quote(90.0, 89.78, open_5m=89.4, low_5m=89.1, prev_low_5m=89.2, s1=89.5, r1=95.0, rvol_5m=1.3),
        "SOXS": _mk_quote(53.39, 56.32, open_5m=52.6, low_5m=52.25, prev_low_5m=52.30, s1=52.80, r1=60.0, rvol_5m=1.25),
        "SQQQ": _mk_quote(22.50, 22.80, open_5m=22.2, low_5m=22.05, prev_low_5m=22.10, s1=22.25, r1=25.0, rvol_5m=1.25),
    }

    cands = kol.screen_kol_sniper_candidates(
        ai_predictions=ai,
        live_quotes=quotes_flush,
        open_positions=[],
        cur_et=cur_et,
        market_regime="NEUTRAL_CHOP",
    )
    tks = {c["ticker"] for c in cands}
    assert "NVDA" not in tks
    assert "MRVL" not in tks
    assert "SOXS" in tks
    assert "SQQQ" in tks
    for c in cands:
        assert c["setup"] == "SWEEP_RECLAIM_LONG"

    # 缺失 QQQ 行情字段时优雅降级不报错，正常放行 NVDA
    quotes_no_qqq = {"NVDA": quotes_flush["NVDA"]}
    cands_no_qqq = kol.screen_kol_sniper_candidates(
        ai_predictions=_mk_ai({"NVDA": (64.0, 32.0)}),
        live_quotes=quotes_no_qqq,
        open_positions=[],
        cur_et=cur_et,
        market_regime="NEUTRAL_CHOP",
    )
    assert len(cands_no_qqq) == 1 and cands_no_qqq[0]["ticker"] == "NVDA"


def test_v58_vwap_pure_be_zero_trim_idempotent_and_allows_addon(tmp_path):
    """水下 SWEEP 反抽至 VWAP (-0.15%) 且毛浮盈 >= +0.8% 时纯推保本、0% 减仓、不重复触发，且后续站稳 VWAP 照常加仓。"""
    ldg_path = str(tmp_path / "kol_v58_vbe.json")
    t0 = datetime(2026, 11, 25, 9, 50, 0, tzinfo=ET_TZ)
    ldg = kol.create_empty_kol_ledger(now_et=t0)
    ldg["cash"] = 700.0
    ldg["open_positions"].append({
        "trade_id": "KOL_META_LONG_2026-11-25_1",
        "ticker": "META",
        "direction": "LONG",
        "setup": "SWEEP_RECLAIM_LONG",
        "entry_tag": "NORMAL",
        "stage": 1,
        "can_add": True,
        "slot_weight": 0.30,
        "peak_slot_weight": 0.30,
        "allocated_capital": 300.0,
        "qty": 3.0,
        "entry_date": "2026-11-25",
        "entry_time_et": "2026-11-25 09:45:00",
        "orig_entry_price": 100.0,
        "entry_price": 100.0,
        "last_price": 100.0,
        "best_price": 100.0,
        "sl_price": 98.80,
        "entered_below_vwap": True,
        "vwap_be_pushed": False,
        "sweep_low_anchor": 99.05,
        "fast_be_trimmed": False,
        "partial_tp_done": False,
        "be_stop_active": False,
        "partial_closed_capital": 0.0,
        "partial_realized_pnl_usd": 0.0,
        "max_favorable_return_pct": 0.0,
        "expiry_date": "2026-12-01",
    })
    kol.save_kol_ledger(ldg, ldg_path)

    sent = []

    def _mock_sender(title, body, dkey, clr):
        sent.append((title, body, dkey, clr))

    # 第 1 轮：反抽到 $101.00（+1.0% >= +0.8%），VWAP=$101.10（vwap_diff=-0.10% >= -0.15%）-> 触发 VWAP 纯推 BE
    res1 = kol.run_kol_copy_trade_cycle(
        ai_predictions={},
        live_quotes={"META": _mk_quote(101.00, 101.10)},
        session_code="REGULAR",
        now_et=t0 + timedelta(minutes=10),
        ledger_path=ldg_path,
        dry_run=False,
        alert_sender=_mock_sender,
    )
    assert len(res1["vwap_be_now"]) == 1
    assert len(res1["fast_be_now"]) == 0
    assert len(res1["partials_now"]) == 0
    pos1 = res1["ledger"]["open_positions"][0]
    assert pos1["vwap_be_pushed"] is True
    assert pos1["be_stop_active"] is True
    assert pos1["sl_price"] == pytest.approx(100.30)
    # 严格 0% 提前减仓：金额、仓位权重、股数、现金均 100% 不变
    assert pos1["allocated_capital"] == pytest.approx(300.0)
    assert pos1["slot_weight"] == pytest.approx(0.30)
    assert pos1["qty"] == pytest.approx(3.0)
    assert pos1["partial_closed_capital"] == pytest.approx(0.0)
    assert res1["ledger"]["cash"] == pytest.approx(700.0)
    assert any("VWAP纯推保本" in a["title"] for a in res1["alerts_sent"])

    # 第 2 轮：价格小幅盘整在 $101.20 -> 不会重复触发 VWAP_BE
    res2 = kol.run_kol_copy_trade_cycle(
        ai_predictions={},
        live_quotes={"META": _mk_quote(101.20, 101.10)},
        session_code="REGULAR",
        now_et=t0 + timedelta(minutes=15),
        ledger_path=ldg_path,
        dry_run=False,
        alert_sender=_mock_sender,
    )
    assert len(res2["vwap_be_now"]) == 0

    # 第 3 轮：放量站稳 VWAP 且涨至 +2.0% ($102.00) -> 照常触发原有 +20% 顺势加仓，且保本损不倒退
    res3 = kol.run_kol_copy_trade_cycle(
        ai_predictions={},
        live_quotes={"META": _mk_quote(102.00, 101.20)},
        session_code="REGULAR",
        now_et=t0 + timedelta(minutes=25),
        ledger_path=ldg_path,
        dry_run=False,
        alert_sender=_mock_sender,
    )
    assert len(res3["addons_now"]) == 1
    pos3 = res3["ledger"]["open_positions"][0]
    assert pos3["stage"] == 2
    assert pos3["slot_weight"] == pytest.approx(0.50)
    assert pos3["sl_price"] >= 100.30


def test_v58_breakout_short_unchanged_and_legacy_v57_ledger_compat(tmp_path):
    """验证 BREAKOUT_MOMENTUM_LONG、空头逻辑未破坏，且缺少新字段的 V5.7 旧账本无缝兼容零 KeyError。"""
    cur_et = datetime(2026, 11, 25, 11, 0, 0, tzinfo=ET_TZ)

    # 1) BREAKOUT_MOMENTUM_LONG 保持原有条件（站稳 VWAP + 突破 R1 + rvol>=1.5 + RSI 50~76）
    cands_brk = kol.screen_kol_sniper_candidates(
        ai_predictions=_mk_ai({"NVDA": (60.0, 38.0)}),
        live_quotes={
            "QQQ": _mk_quote(500.0, 499.0, pct_change=0.4),
            "NVDA": _mk_quote(140.0, 139.0, open_5m=140.2, is_5m_green=False, s1=130.0, r1=139.5, rvol_5m=1.6, rsi_5m=60.0),
        },
        open_positions=[],
        cur_et=cur_et,
        market_regime="BULL_EXPANSION",
    )
    assert len(cands_brk) == 1
    assert cands_brk[0]["setup"] == "BREAKOUT_MOMENTUM_LONG"
    assert cands_brk[0]["entered_below_vwap"] is False

    # 2) REBOUND_REJECT_SHORT 保持原有逻辑
    cands_sh = kol.screen_kol_sniper_candidates(
        ai_predictions=_mk_ai({"AMD": (30.0, 65.0)}),
        live_quotes={
            "QQQ": _mk_quote(490.0, 493.0, pct_change=-1.5),
            "AMD": _mk_quote(150.0, 150.4, pct_change=-1.8, is_broken_bearish=True, rsi_5m=48.0, rvol_5m=1.2),
        },
        open_positions=[],
        cur_et=cur_et,
        market_regime="BEAR_DEFENSE",
    )
    assert len(cands_sh) == 1
    assert cands_sh[0]["direction"] == "SHORT"
    assert cands_sh[0]["setup"] == "REBOUND_REJECT_SHORT"

    # 3) 旧 V5.7 账本持仓（完全缺失 entered_below_vwap / vwap_be_pushed / sweep_low_anchor 字段）无 KeyError
    ldg_path = str(tmp_path / "kol_legacy_v57.json")
    legacy_doc = kol.create_empty_kol_ledger(now_et=cur_et)
    legacy_doc["schema_version"] = "v5.7-kol-sniper-ledger"
    legacy_doc["cash"] = 700.0
    legacy_doc["open_positions"].append({
        "trade_id": "KOL_TSLA_LONG_2026-11-24_1",
        "ticker": "TSLA",
        "direction": "LONG",
        "setup": "SWEEP_RECLAIM_LONG",
        "entry_tag": "NORMAL",
        "stage": 1,
        "can_add": True,
        "slot_weight": 0.30,
        "allocated_capital": 300.0,
        "entry_date": "2026-11-24",
        "entry_time_et": "2026-11-24 10:00:00",
        "orig_entry_price": 300.0,
        "entry_price": 300.0,
        "last_price": 301.0,
        "best_price": 301.0,
        "sl_price": 292.0,
        "expiry_date": "2026-11-28",
    })
    kol.save_kol_ledger(legacy_doc, ldg_path)

    res_leg = kol.run_kol_copy_trade_cycle(
        ai_predictions={},
        live_quotes={"TSLA": _mk_quote(303.0, 303.1)},
        session_code="REGULAR",
        now_et=cur_et,
        ledger_path=ldg_path,
        dry_run=False,
    )
    assert len(res_leg["vwap_be_now"]) == 0  # 旧账本默认 entered_below_vwap=False，不会误触发
    assert len(res_leg["ledger"]["open_positions"]) == 1

    # 4) V5.8 展示标识
    assert kol.KOL_ENGINE_VERSION == "V5.8"
    emb = kol.build_dual_track_copy_embed_dict(kol_ledger=res_leg["ledger"], steady_ledger=None, now_et=cur_et)
    assert "V5.8" in emb["footer"]["text"]
