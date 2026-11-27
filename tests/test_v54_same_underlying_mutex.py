# -*- coding: utf-8 -*-
"""
V5.4 同源资产互斥锁 (SAME_UNDERLYING_GROUPS) + V5.8 空账本安全渲染离线单测
全程使用 tmp_path，严禁触碰任何生产账本或发送外部通知。
"""

import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import copy_trade_engine as cte
import copy_trade_kol_engine as kol
import discord_radar_bot as bot

ET_TZ = ZoneInfo("America/New_York")


def _mk_ai(items: dict) -> dict:
    return {
        "market_regime": "BULL_TREND",
        "by_ticker": {
            tk: {
                "ticker": tk,
                "calibrated_win_rate_pct": p_l,
                "calibrated_short_win_rate_pct": p_s,
                "atr_pct": 2.5,
            }
            for tk, (p_l, p_s) in items.items()
        },
    }


def _mk_quotes(prices: dict) -> dict:
    return {
        tk: {"active_price": px, "last": px, "prev_close": px, "pct_change": 0.2}
        for tk, px in prices.items()
    }


def test_v54_gold_mutex_blocks_xauusd_when_holding_gld_and_vice_versa():
    """V5.4 已持 GLD 时严格拦截 XAUUSD，已持 XAUUSD 时严格拦截 GLD，且记录 SAME_UNDERLYING_MUTEX:GOLD。"""
    today = date(2026, 11, 25)
    ai = _mk_ai({"GLD": (66.0, 24.0), "XAUUSD": (68.0, 22.0), "TLT": (62.0, 28.0), "NVDA": (64.0, 26.0)})
    quotes = _mk_quotes({"GLD": 334.0, "XAUUSD": 3645.0, "TLT": 82.0, "NVDA": 140.0})

    # 1) 已持 GLD -> 拦截 XAUUSD，不同组 TLT/NVDA 正常放行
    rej1 = []
    cands1 = cte.screen_copy_trade_candidates(
        ai_predictions=ai,
        live_quotes=quotes,
        open_positions=[{"ticker": "GLD", "direction": "LONG"}],
        today_date=today,
        market_regime="BULL_TREND",
        rejected_log=rej1,
    )
    tks1 = [c["ticker"] for c in cands1]
    assert "XAUUSD" not in tks1
    assert "NVDA" in tks1
    assert "TLT" in tks1
    assert any(r["ticker"] == "XAUUSD" and r["reason"] == "SAME_UNDERLYING_MUTEX:GOLD" for r in rej1)

    # 2) 已持 XAUUSD（含小写归一化校验）-> 拦截 GLD
    rej2 = []
    cands2 = cte.screen_copy_trade_candidates(
        ai_predictions=ai,
        live_quotes=quotes,
        open_positions=[{"ticker": "xauusd", "direction": "LONG"}],
        today_date=today,
        market_regime="BULL_TREND",
        rejected_log=rej2,
    )
    tks2 = [c["ticker"] for c in cands2]
    assert "GLD" not in tks2
    assert "NVDA" in tks2
    assert "TLT" in tks2
    assert any(r["ticker"] == "GLD" and r["reason"] == "SAME_UNDERLYING_MUTEX:GOLD" for r in rej2)


def test_v54_same_underlying_groups_same_cycle_dedupe_and_existing_positions_preserved(tmp_path):
    """同轮候选内同源资产 (GOLD / SP500 / SEMI_INDEX / BTC / NASDAQ100) 最多只开 1 只；既有双黄金持仓不被强平。"""
    today = date(2026, 11, 25)
    ai = _mk_ai({
        "XAUUSD": (72.0, 20.0),
        "GLD": (70.0, 20.0),
        "SMH": (69.0, 21.0),
        "SOXX": (68.0, 22.0),
        "SPY": (67.0, 23.0),
        "VOO": (66.0, 24.0),
        "BTC-USD": (65.0, 25.0),
        "IBIT": (64.0, 26.0),
    })
    quotes = _mk_quotes({
        "XAUUSD": 3645.0, "GLD": 334.0, "SMH": 250.0, "SOXX": 220.0,
        "SPY": 600.0, "VOO": 550.0, "BTC-USD": 95000.0, "IBIT": 54.0,
    })
    rej = []
    cands = cte.screen_copy_trade_candidates(
        ai_predictions=ai,
        live_quotes=quotes,
        open_positions=[],
        today_date=today,
        market_regime="BULL_TREND",
        rejected_log=rej,
    )
    assert [c["ticker"] for c in cands] == ["XAUUSD", "SMH", "SPY", "BTC-USD"]
    rej_map = {r["ticker"]: r["reason"] for r in rej}
    assert rej_map["GLD"] == "SAME_UNDERLYING_MUTEX:GOLD"
    assert rej_map["SOXX"] == "SAME_UNDERLYING_MUTEX:SEMI_INDEX"
    assert rej_map["VOO"] == "SAME_UNDERLYING_MUTEX:SP500"

    # 验证既有持仓中若历史已同时持有 GLD + XAUUSD，运行周期绝不强平任何一笔
    lp = str(tmp_path / "v54_existing_gold.json")
    t0 = datetime(2026, 11, 25, 10, 0, 0, tzinfo=ET_TZ)
    ldg = cte.create_empty_copy_ledger(now_et=t0)
    ldg["cash"] = 500.0
    ldg["open_positions"] = [
        {
            "trade_id": "GLD_LONG_2026-11-24", "ticker": "GLD", "direction": "LONG",
            "slot_weight": 0.25, "allocated_capital": 250.0, "entry_date": "2026-11-24",
            "entry_price": 332.95, "last_price": 334.0, "tp_price": 344.22, "sl_price": 328.88,
            "expiry_date": "2026-12-02",
        },
        {
            "trade_id": "XAUUSD_LONG_2026-11-24", "ticker": "XAUUSD", "direction": "LONG",
            "slot_weight": 0.25, "allocated_capital": 250.0, "entry_date": "2026-11-24",
            "entry_price": 3637.21, "last_price": 3645.0, "tp_price": 3795.59, "sl_price": 3589.26,
            "expiry_date": "2026-12-02",
        },
    ]
    cte.save_copy_ledger(ldg, lp)
    res = cte.run_copy_trade_cycle(
        ai_predictions=_mk_ai({"NVDA": (65.0, 25.0)}),
        live_quotes=_mk_quotes({"GLD": 334.2, "XAUUSD": 3646.5, "NVDA": 140.0}),
        session_code="REGULAR",
        now_et=t0,
        ledger_path=lp,
        dry_run=False,
    )
    assert len(res["closed"]) == 0
    open_tks = {p["ticker"] for p in res["ledger"]["open_positions"]}
    assert {"GLD", "XAUUSD", "NVDA"}.issubset(open_tks)


def test_v58_fresh_empty_kol_ledger_safe_render_zero_division(tmp_path):
    """重置后的 V5.8 空账本 ($1,000, 0持仓, 0成交) 可被 /copy 卡片与前端抽屉零异常解析渲染。"""
    t0 = datetime(2026, 11, 28, 10, 0, 0, tzinfo=ET_TZ)
    kol_path = str(tmp_path / "copy_trade_ledger_kol.json")
    steady_path = str(tmp_path / "copy_trade_ledger.json")

    fresh_kol = kol.create_empty_kol_ledger(now_et=t0)
    fresh_kol["engine_version"] = kol.KOL_ENGINE_VERSION
    kol.save_kol_ledger(fresh_kol, kol_path)
    cte.save_copy_ledger(cte.create_empty_copy_ledger(now_et=t0), steady_path)

    loaded = kol.load_kol_ledger(kol_path, now_et=t0)
    assert loaded["total_equity"] == pytest.approx(1000.0)
    assert loaded["cash"] == pytest.approx(1000.0)
    assert loaded["peak_equity"] == pytest.approx(1000.0)
    assert loaded["max_drawdown_pct"] == pytest.approx(0.0)
    assert loaded["open_positions"] == []
    assert loaded["closed_trades"] == []
    assert loaded["stats"]["closed_count"] == 0
    assert loaded["stats"]["overall_win_rate_pct"] is None
    assert loaded["stats"]["profit_factor"] is None

    # /copy Discord 双轨 Embed 渲染（无 KeyError / ZeroDivisionError）
    emb = bot.build_copy_embed_dict(copy_ledger_path=steady_path, kol_ledger_path=kol_path)
    emb_txt = emb["title"] + " " + " ".join(f["value"] for f in emb["fields"]) + " " + emb["footer"]["text"]
    assert "$1,000.00" in emb_txt
    assert "V5.8" in emb_txt
    assert "0/2" in emb_txt

    # 前端双轨账本抽屉 HTML 渲染（无异常）
    html = kol.render_dual_track_copy_drawer_html(steady_path=steady_path, kol_path=kol_path)
    assert "V5.8 KOL" in html
    assert "$1,000.00" in html
    assert "0/2" in html
