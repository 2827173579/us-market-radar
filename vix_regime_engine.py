# -*- coding: utf-8 -*-
"""
全局真 VIX 恐慌中枢 + 期限结构倒挂刹车 + 恐慌出清右侧抄底雷达引擎 (vix_regime_engine.py)

核心职责（严格独立于 79 只可交易自选股池之外，零未来函数穿越 Zero Lookahead Bias）：
1. 通过 yfinance 单次批量免费抓取 ^VIX / ^VIX3M / ^VVIX / ^TNX（独立容错 + 本地 TTL 缓存防频控）；
2. 计算期限结构比值 (^VIX / ^VIX3M) 及近 10 个交易日（含当日）滚动恐慌峰值与回撤；
3. 输出三态状态机：
   - 状态 A（期限倒挂硬刹车）：vix_term_ratio >= 1.00 或 vix_spot >= 30.0 -> 强制 BEAR_DEFENSE 严防接飞刀；
   - 状态 B（恐慌出清 · 右侧抄底观察窗）：近 10 日曾极端恐慌且当前确认见顶回落升水 -> 平滑放宽优质多头上限；
   - 状态 C（常态平稳 / 避险升温）：正常升水或温和警惕，与大盘均线及 79 池宽度共同治理体制门禁；
4. 提供跨页面共享的轻量 UI 胶囊与详情浮层片段（兼容 Futu 深/浅双主题）。
"""

from __future__ import annotations

import html as _html
import json
import math
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
VIX_SYMBOLS = ("^VIX", "^VXN", "^VIX3M", "^VVIX", "^TNX")
LIQUIDITY_SYMBOLS = ("HYG", "IEF", "DX-Y.NYB", "JPY=X")
DEFAULT_CACHE_PATH = os.path.join(WORK_DIR, "data_cache", "vix_regime_cache.json")
DEFAULT_LIQUIDITY_CACHE_PATH = os.path.join(WORK_DIR, "data_cache", "macro_liquidity_cache.json")
DEFAULT_TTL_SECONDS = 180


def _normalize_session_code(session_code: Optional[str]) -> str:
    if session_code is None:
        return "REGULAR"
    cleaned = str(session_code).strip().upper()
    return cleaned or "REGULAR"


def _apply_session_lock_to_snapshot(snap: Dict[str, Any], session_code: Optional[str] = None) -> Dict[str, Any]:
    """
    策略 A 盘前/夜盘防误触发锁：
    仅当美东常规盘中 (REGULAR) 且 raw_bottom_signal=True 时才允许正式亮绿灯并开启抄底观察窗；
    在 PRE_MARKET / OVERNIGHT / POST_MARKET / WEEKEND 等非常规时段，强制保持
    vix_bottom_signal=False 与 vix_bottom_premarket_preview=True（盘前预热·待09:30确认）。
    """
    out = dict(snap or {})
    sess = _normalize_session_code(session_code)
    out["session_code"] = sess

    vix_spot = out.get("vix_spot")
    if vix_spot is None or str(out.get("vix_state") or "").upper() == "UNAVAILABLE":
        out["raw_bottom_signal"] = False
        out["vix_bottom_premarket_preview"] = False
        return out

    raw_bottom = out.get("raw_bottom_signal")
    if raw_bottom is None:
        raw_bottom = bool(out.get("vix_bottom_signal") or out.get("vix_bottom_premarket_preview"))
    raw_bottom = bool(raw_bottom) and (not bool(out.get("vix_hard_brake")))
    out["raw_bottom_signal"] = raw_bottom

    try:
        vix_disp = f"{float(vix_spot):.1f}"
    except Exception:
        vix_disp = "--"
    ratio_v = out.get("vix_term_ratio")
    try:
        ratio_disp = f"{float(ratio_v):.2f}" if ratio_v is not None else None
    except Exception:
        ratio_disp = None
    try:
        peak_f = float(out.get("vix_10d_peak") or vix_spot)
    except Exception:
        peak_f = 0.0
    try:
        dd_f = float(out.get("vix_drawdown_from_peak_pct") or 0.0)
    except Exception:
        dd_f = 0.0

    if bool(out.get("vix_hard_brake")):
        out["vix_bottom_signal"] = False
        out["vix_bottom_premarket_preview"] = False
        return out

    if raw_bottom:
        if sess != "REGULAR":
            out["vix_bottom_signal"] = False
            out["vix_bottom_premarket_preview"] = True
            out["vix_state"] = "BOTTOM_PREMARKET_PREVIEW"
            out["pill_level"] = "warn"
            out["pill_text"] = f"🟡 VIX {vix_disp} · 盘前预热 (待09:30确认)"
            out["summary_cn"] = (
                f"盘前预热 · 待 09:30 确认（非常规盘中 ^VIX3M 冻结在上一收盘价，恐慌回撤 {dd_f:.1f}% 需开盘确认）"
            )
            out["advice_cn"] = (
                f"当前处于非美东常规盘中（{sess}），^VIX3M 远期报价冻结在上一收盘价，"
                f"VIX/VIX3M ({ratio_disp or '--'}) 可能被盘前瞬时波动人为压低。"
                "系统已启用盘前防误触发锁，暂不亮抄底绿灯、不推送 Discord 且不写入当日去重锁，待 09:30 ET 开盘确认后生效。"
            )
        else:
            out["vix_bottom_signal"] = True
            out["vix_bottom_premarket_preview"] = False
            out["vix_state"] = "WASHOUT_BOTTOM_WINDOW"
            out["pill_level"] = "bottom"
            out["pill_text"] = f"🟢 VIX {vix_disp} · 恐慌出清抄底窗"
            out["summary_cn"] = f"恐慌确认见顶回落（高点 {peak_f:.1f} 回撤 {dd_f:.1f}%），右侧优质标的抄底窗已开启"
            out["advice_cn"] = (
                f"近10日极端恐慌已回落 {dd_f:.1f}%（≥5%）、比值回落至 0.97 以下"
                f"（ratio={float(ratio_v):.2f}）且当日 VIX 未反扑（≤+3%），对非暴跌熔断且站稳日内 VWAP 的优质标的放宽多头胜率上限至 58%。"
                if ratio_v is not None
                else "比值回落至 0.97 以下、恐慌自 10 日高点回撤 ≥5% 且当日 VIX 未反扑（≤+3%）时开启右侧抄底窗。"
            )
    else:
        out["vix_bottom_signal"] = False
        out["vix_bottom_premarket_preview"] = False
    return out


def _clean_close_series(raw_series: Any, as_of_date: Optional[Any] = None, is_tnx: bool = False) -> pd.Series:
    """标准化收盘序列为按自然日升序排列的 tz-naive 日频序列，严格截断 <= as_of_date（Zero Lookahead）。"""
    if raw_series is None:
        return pd.Series(dtype=float)
    try:
        s = pd.Series(raw_series).copy() if not isinstance(raw_series, pd.Series) else raw_series.copy()
    except Exception:
        return pd.Series(dtype=float)
    if s.empty:
        return pd.Series(dtype=float)

    try:
        idx = pd.to_datetime(s.index, errors="coerce")
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        idx = idx.normalize()
    except Exception:
        return pd.Series(dtype=float)

    vals = pd.to_numeric(s, errors="coerce")
    df_tmp = pd.DataFrame({"dt": idx, "val": vals.values}).dropna(subset=["dt", "val"])
    if df_tmp.empty:
        return pd.Series(dtype=float)
    df_tmp = df_tmp[np.isfinite(df_tmp["val"]) & (df_tmp["val"] > 0.0)]
    if df_tmp.empty:
        return pd.Series(dtype=float)

    out = df_tmp.groupby("dt", as_index=True)["val"].last().sort_index()
    if is_tnx and not out.empty and float(out.median()) > 20.0:
        # 少数行情源将美债收益率报价放大 10 倍 (如 45.2 代表 4.52%)
        out = out / 10.0

    if as_of_date is not None:
        cutoff = pd.to_datetime(as_of_date, errors="coerce")
        if not pd.isna(cutoff):
            if getattr(cutoff, "tzinfo", None) is not None:
                cutoff = cutoff.tz_localize(None)
            cutoff = cutoff.normalize()
            out = out[out.index <= cutoff]
    return out


def _extract_symbol_close_from_download(df: Any, symbol: str) -> pd.Series:
    """兼容 yfinance MultiIndex ((field, sym) / (sym, field)) 与单标的 DataFrame，提取 Close 序列。"""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.Series(dtype=float)
    cols = df.columns
    if isinstance(cols, pd.MultiIndex):
        try:
            if ("Close", symbol) in cols:
                return df[("Close", symbol)]
            if (symbol, "Close") in cols:
                return df[(symbol, "Close")]
            if ("Adj Close", symbol) in cols:
                return df[("Adj Close", symbol)]
            if (symbol, "Adj Close") in cols:
                return df[(symbol, "Adj Close")]
        except Exception:
            pass
        return pd.Series(dtype=float)

    # 单标的降级返回单层列
    if "Close" in cols:
        return df["Close"]
    if "Adj Close" in cols:
        return df["Adj Close"]
    return pd.Series(dtype=float)


def fetch_vix_raw_series(timeout: float = 10.0) -> Dict[str, pd.Series]:
    """
    单次批量抓取 ^VIX / ^VIX3M / ^VVIX / ^TNX 近 1 个月日线数据。
    任一指数超时或缺失仅对单指数回退重试并允许返回空序列，绝不抛出未捕获异常阻断实盘循环。
    """
    result: Dict[str, pd.Series] = {sym: pd.Series(dtype=float) for sym in VIX_SYMBOLS}
    if yf is None:
        return result

    batch_df = None
    try:
        batch_df = yf.download(
            list(VIX_SYMBOLS),
            period="1mo",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=timeout,
        )
    except Exception:
        batch_df = None

    for sym in VIX_SYMBOLS:
        is_tnx = (sym == "^TNX")
        raw_s = _extract_symbol_close_from_download(batch_df, sym) if batch_df is not None else pd.Series(dtype=float)
        cleaned = _clean_close_series(raw_s, is_tnx=is_tnx)
        if cleaned.empty:
            try:
                hist_df = yf.Ticker(sym).history(period="1mo", interval="1d", auto_adjust=False)
                cleaned = _clean_close_series(_extract_symbol_close_from_download(hist_df, sym), is_tnx=is_tnx)
            except Exception:
                cleaned = pd.Series(dtype=float)
        result[sym] = cleaned

    return result


def compute_vix_regime_from_series(
    vix_series: Any,
    vix3m_series: Any = None,
    vvix_series: Any = None,
    tnx_series: Any = None,
    as_of_date: Optional[Any] = None,
    vxn_series: Any = None,
    session_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    纯因果 VIX/VXN 恐慌中枢状态机（Zero Lookahead Bias）：
    严格只用 <= as_of_date 数据与向后因果前值对齐（ffill，绝不使用 bfill）。
    """
    sess = _normalize_session_code(session_code)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    vix_s = _clean_close_series(vix_series, as_of_date=as_of_date, is_tnx=False)
    vxn_s = _clean_close_series(vxn_series, as_of_date=as_of_date, is_tnx=False)
    vix3m_s = _clean_close_series(vix3m_series, as_of_date=as_of_date, is_tnx=False)
    vvix_s = _clean_close_series(vvix_series, as_of_date=as_of_date, is_tnx=False)
    tnx_s = _clean_close_series(tnx_series, as_of_date=as_of_date, is_tnx=True)

    available_symbols = [
        sym for sym, ser in (("^VIX", vix_s), ("^VXN", vxn_s), ("^VIX3M", vix3m_s), ("^VVIX", vvix_s), ("^TNX", tnx_s))
        if not ser.empty
    ]

    vxn_spot_unavail = round(float(vxn_s.iloc[-1]), 2) if not vxn_s.empty else None
    vxn_peak_unavail = round(float(vxn_s.tail(10).max()), 2) if not vxn_s.empty else None
    vxn_dd_unavail = (
        round(((vxn_peak_unavail - vxn_spot_unavail) / vxn_peak_unavail) * 100.0, 2)
        if (vxn_spot_unavail is not None and vxn_peak_unavail and vxn_peak_unavail > 0)
        else None
    )

    if vix_s.empty:
        return {
            "as_of_date": None,
            "updated_at": now_str,
            "vix_spot": None,
            "vix_prev": None,
            "vix_pct": None,
            "vxn_spot": vxn_spot_unavail,
            "vxn_pct": None,
            "vxn_10d_peak": vxn_peak_unavail,
            "vxn_drawdown_from_peak_pct": vxn_dd_unavail,
            "vxn_vix_spread": None,
            "tech_fear_label": "⚪ 科技波动暂缺",
            "vix3m_spot": round(float(vix3m_s.iloc[-1]), 2) if not vix3m_s.empty else None,
            "vix3m_pct": None,
            "vix_term_ratio": None,
            "vvix_spot": round(float(vvix_s.iloc[-1]), 2) if not vvix_s.empty else None,
            "vvix_pct": None,
            "tnx_yield": round(float(tnx_s.iloc[-1]), 2) if not tnx_s.empty else None,
            "tnx_yield_3d": round(float(tnx_s.iloc[-1]), 3) if not tnx_s.empty else None,
            "tnx_pct": None,
            "vix_10d_peak": None,
            "vix_drawdown_from_peak_pct": None,
            "vix_term_ratio_10d_peak": None,
            "vix_term_ratio_drop": None,
            "vvix_10d_peak": None,
            "vvix_drawdown_from_peak_pct": None,
            "vix_is_down_today": False,
            "term_structure_state": "UNKNOWN",
            "term_structure_cn": "数据暂缺",
            "vix_hard_brake": False,
            "raw_bottom_signal": False,
            "vix_bottom_premarket_preview": False,
            "vix_bottom_signal": False,
            "session_code": sess,
            "vix_state": "UNAVAILABLE",
            "pill_level": "neutral",
            "pill_text": "⚪ VIX 暂缺 · 常规门禁",
            "summary_cn": "全局 VIX 数据暂不可用，沿用基准均线与全池宽度门禁",
            "advice_cn": "当前波动率指数外部源暂未返回数据，系统自动优雅降级，继续按大盘 MA20/MA50 与 79 池宽度执行风控。",
            "available_symbols": available_symbols,
            "degraded": True,
        }

    def _last_prev_pct(ser: pd.Series) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        if ser is None or ser.empty:
            return None, None, None
        cur = float(ser.iloc[-1])
        prev = float(ser.iloc[-2]) if len(ser) >= 2 else None
        pct = round(((cur - prev) / prev) * 100.0, 2) if (prev is not None and prev > 0) else None
        return cur, prev, pct

    vix_spot_f, vix_prev_f, vix_pct = _last_prev_pct(vix_s)
    vxn_spot_f, _, vxn_pct = _last_prev_pct(vxn_s)
    vix3m_spot_f, _, vix3m_pct = _last_prev_pct(vix3m_s)
    vvix_spot_f, _, vvix_pct = _last_prev_pct(vvix_s)
    tnx_spot_f, _, tnx_pct = _last_prev_pct(tnx_s)
    vix_is_down_today = bool(vix_prev_f is not None and vix_spot_f < vix_prev_f)

    # 纳指 100 专属恐慌 (^VXN) 近 10 日滚动峰值/回撤 + 科技恐慌溢价 (VXN - VIX)
    vxn_10d_peak_f: Optional[float] = None
    vxn_dd_pct: Optional[float] = None
    vxn_vix_spread_f: Optional[float] = None
    if not vxn_s.empty and vxn_spot_f is not None:
        vxn_10d = vxn_s.tail(10)
        vxn_10d_peak_f = float(vxn_10d.max())
        if vxn_10d_peak_f > 0:
            vxn_dd_pct = round(((vxn_10d_peak_f - vxn_spot_f) / vxn_10d_peak_f) * 100.0, 2)
        if vix_spot_f is not None:
            vxn_vix_spread_f = round(vxn_spot_f - vix_spot_f, 2)

    if vxn_spot_f is None:
        tech_fear_label = "⚪ 科技波动暂缺"
    elif vxn_10d_peak_f is not None and vxn_10d_peak_f >= 32.0 and vxn_dd_pct is not None and vxn_dd_pct >= 8.0:
        tech_fear_label = "🟢 科技恐慌回落 · 半导体/AI 压力缓解"
    elif vxn_spot_f >= 32.0 and (vxn_vix_spread_f is not None and vxn_vix_spread_f >= 5.5):
        tech_fear_label = "🔥 科技/半导体极端去杠杆（VXN 显著高于大盘）"
    elif vxn_spot_f >= 26.0:
        tech_fear_label = "🟡 科技板块避险升温"
    else:
        tech_fear_label = "⚪ 科技波动平稳"

    # 严格因果前值对齐 (^VIX 盘前先出新 K 线时仅 ffill ^VIX3M 已知前值，绝不 bfill)
    ratio_series = pd.Series(dtype=float)
    if not vix3m_s.empty:
        union_idx = vix_s.index.union(vix3m_s.index).sort_values()
        vix_u = vix_s.reindex(union_idx).ffill()
        vix3m_u = vix3m_s.reindex(union_idx).ffill()
        ratio_series = (vix_u / vix3m_u).replace([np.inf, -np.inf], np.nan).dropna()

    # 近 10 个交易日（含当日）因果滚动高点与回撤
    vix_10d = vix_s.tail(10)
    vix_10d_peak_f = float(vix_10d.max()) if not vix_10d.empty else vix_spot_f
    vix_dd_pct = round(((vix_10d_peak_f - vix_spot_f) / vix_10d_peak_f) * 100.0, 2) if vix_10d_peak_f > 0 else 0.0

    vix_term_ratio_f: Optional[float] = None
    vix_term_ratio_10d_peak_f: Optional[float] = None
    vix_term_ratio_drop: Optional[float] = None
    if not ratio_series.empty:
        vix_term_ratio_f = float(ratio_series.iloc[-1])
        r_10d = ratio_series.tail(10)
        vix_term_ratio_10d_peak_f = float(r_10d.max())
        vix_term_ratio_drop = round(vix_term_ratio_10d_peak_f - vix_term_ratio_f, 3)

    vvix_10d_peak_f: Optional[float] = None
    vvix_dd_pct: Optional[float] = None
    if not vvix_s.empty and vvix_spot_f is not None:
        vvix_10d = vvix_s.tail(10)
        vvix_10d_peak_f = float(vvix_10d.max())
        if vvix_10d_peak_f > 0:
            vvix_dd_pct = round(((vvix_10d_peak_f - vvix_spot_f) / vvix_10d_peak_f) * 100.0, 2)

    vix_term_ratio_out = round(vix_term_ratio_f, 3) if vix_term_ratio_f is not None else None
    vix_term_peak_out = round(vix_term_ratio_10d_peak_f, 3) if vix_term_ratio_10d_peak_f is not None else None

    if vix_term_ratio_out is None:
        term_structure_state = "UNKNOWN"
        term_structure_cn = "远期暂缺"
    elif vix_term_ratio_out >= 1.00:
        term_structure_state = "BACKWARDATION"
        term_structure_cn = "期限倒挂 · 机构恐慌踩踏"
    elif vix_term_ratio_out >= 0.92:
        term_structure_state = "ELEVATED"
        term_structure_cn = "警戒收窄 · 避险升温"
    else:
        term_structure_state = "CONTANGO"
        term_structure_cn = "正常升水 · 健康平稳"

    # 状态 A：期限倒挂硬刹车 (vix_term_ratio >= 1.00 或 vix_spot >= 30.0)
    vix_hard_brake = bool(
        (vix_term_ratio_f is not None and vix_term_ratio_f >= 1.00)
        or (vix_spot_f >= 30.0)
    )

    # 状态 B：恐慌出清 · 右侧抄底观察窗 (组 C* 提纯版 + 二次恐慌抬头熔断锁)
    # 1) 剔除低 VIX 时纯 VVIX>=112 假阳，要求近 10 日真出现过 VIX>=26.0 或 期限比>=1.00 倒挂
    had_extreme_panic_10d = bool(
        (vix_10d_peak_f >= 26.0)
        or (vix_term_ratio_10d_peak_f is not None and vix_term_ratio_10d_peak_f >= 1.00)
    )
    # 2) 二次抬头熔断锁：期限比必须回落至 0.97 以下（脱离 0.995 濒临倒挂区），且当日 VIX 涨幅 <= +3.0%
    ratio_cooled = bool(
        (vix_term_ratio_f is not None and vix_term_ratio_10d_peak_f is not None)
        and (vix_term_ratio_f < 0.97)
        and (
            (vix_term_ratio_f < vix_term_ratio_10d_peak_f)
            or ((vix_term_ratio_10d_peak_f - vix_term_ratio_f) >= 0.06)
        )
    )
    # 3) 回撤确认门槛优化为 >= 5.0%
    vol_cooled = bool(
        (vix_dd_pct >= 5.0)
        or (vvix_dd_pct is not None and vvix_dd_pct >= 5.0 and vix_is_down_today)
    )
    no_vix_rebound_spike = bool(vix_pct is None or float(vix_pct) <= 3.0)
    raw_bottom_signal = bool(
        (not vix_hard_brake)
        and had_extreme_panic_10d
        and ratio_cooled
        and vol_cooled
        and no_vix_rebound_spike
    )
    vix_bottom_premarket_preview = bool(raw_bottom_signal and sess != "REGULAR")
    vix_bottom_signal = bool(raw_bottom_signal and sess == "REGULAR")

    vix_disp = f"{vix_spot_f:.1f}"
    ratio_disp = f"{vix_term_ratio_f:.2f}" if vix_term_ratio_f is not None else None

    if vix_hard_brake:
        vix_state = "BACKWARDATION_BRAKE"
        pill_level = "danger"
        if ratio_disp is not None and vix_term_ratio_f >= 1.00:
            pill_text = f"🔴 VIX {vix_disp} · 倒挂预警({ratio_disp})"
        elif ratio_disp is not None:
            pill_text = f"🔴 VIX {vix_disp} · 极端恐慌({ratio_disp})"
        else:
            pill_text = f"🔴 VIX {vix_disp} · 高恐慌警戒"
        summary_cn = "机构恐慌倒挂踩踏期，严禁左侧接飞刀"
        advice_cn = (
            "倒挂说明机构仍在抢买近期保护，大盘体制强制锁死 BEAR_DEFENSE、多头胜率封顶 48%；"
            "比值回落至 0.97 以下、恐慌自 10 日高点回撤 ≥5% 且当日 VIX 未反扑（≤+3%）时开启右侧抄底窗。"
        )
    elif vix_bottom_signal or vix_bottom_premarket_preview:
        vix_state = "WASHOUT_BOTTOM_WINDOW"
        pill_level = "bottom"
        pill_text = f"🟢 VIX {vix_disp} · 恐慌出清抄底窗"
        summary_cn = f"恐慌确认见顶回落（高点 {vix_10d_peak_f:.1f} 回撤 {vix_dd_pct:.1f}%），右侧优质标的抄底窗已开启"
        advice_cn = (
            f"近10日极端恐慌已回落 {vix_dd_pct:.1f}%（≥5%）、比值回落至 0.97 以下"
            f"（ratio={vix_term_ratio_f:.2f}）且当日 VIX 未反扑（≤+3%），对非暴跌熔断且站稳日内 VWAP 的优质标的放宽多头胜率上限至 58%。"
            if vix_term_ratio_f is not None
            else "比值回落至 0.97 以下、恐慌自 10 日高点回撤 ≥5% 且当日 VIX 未反扑（≤+3%）时开启右侧抄底窗。"
        )
    elif (vix_term_ratio_f is not None and vix_term_ratio_f >= 0.92) or (vix_spot_f >= 20.0):
        vix_state = "ELEVATED_CAUTION"
        pill_level = "warn"
        pill_text = f"🟡 VIX {vix_disp} · 避险升温({ratio_disp})" if ratio_disp else f"🟡 VIX {vix_disp} · 避险升温"
        summary_cn = "波动率抬升或期限收窄，避险升温"
        advice_cn = (
            "避险情绪升温但期限尚未倒挂，控制单笔仓位、优选站稳均线与日内 VWAP 的龙头标的；"
            "比值回落至 0.97 以下、恐慌自 10 日高点回撤 ≥5% 且当日 VIX 未反扑（≤+3%）时开启右侧抄底窗。"
        )
    else:
        vix_state = "CALM_CONTANGO"
        pill_level = "calm"
        pill_text = f"⚪ VIX {vix_disp} · 平稳({ratio_disp})" if ratio_disp else f"⚪ VIX {vix_disp} · 平稳"
        summary_cn = "期限结构正常升水，恐慌水平平稳"
        advice_cn = (
            "远期波动率高于近期（Contango），机构对冲需求温和，按常规趋势与黄金坑纪律执行；"
            "比值回落至 0.97 以下、恐慌自 10 日高点回撤 ≥5% 且当日 VIX 未反扑（≤+3%）时开启右侧抄底窗。"
        )

    degraded = len(available_symbols) < len(VIX_SYMBOLS)
    payload = {
        "as_of_date": vix_s.index[-1].strftime("%Y-%m-%d"),
        "updated_at": now_str,
        "vix_spot": round(vix_spot_f, 2),
        "vix_prev": round(vix_prev_f, 2) if vix_prev_f is not None else None,
        "vix_pct": vix_pct,
        "vxn_spot": round(vxn_spot_f, 2) if vxn_spot_f is not None else None,
        "vxn_pct": vxn_pct,
        "vxn_10d_peak": round(vxn_10d_peak_f, 2) if vxn_10d_peak_f is not None else None,
        "vxn_drawdown_from_peak_pct": vxn_dd_pct,
        "vxn_vix_spread": vxn_vix_spread_f,
        "tech_fear_label": tech_fear_label,
        "vix3m_spot": round(vix3m_spot_f, 2) if vix3m_spot_f is not None else None,
        "vix3m_pct": vix3m_pct,
        "vix_term_ratio": vix_term_ratio_out,
        "vvix_spot": round(vvix_spot_f, 2) if vvix_spot_f is not None else None,
        "vvix_pct": vvix_pct,
        "tnx_yield": round(tnx_spot_f, 2) if tnx_spot_f is not None else None,
        "tnx_yield_3d": round(tnx_spot_f, 3) if tnx_spot_f is not None else None,
        "tnx_pct": tnx_pct,
        "vix_10d_peak": round(vix_10d_peak_f, 2),
        "vix_drawdown_from_peak_pct": vix_dd_pct,
        "vix_term_ratio_10d_peak": vix_term_peak_out,
        "vix_term_ratio_drop": vix_term_ratio_drop,
        "vvix_10d_peak": round(vvix_10d_peak_f, 2) if vvix_10d_peak_f is not None else None,
        "vvix_drawdown_from_peak_pct": vvix_dd_pct,
        "vix_is_down_today": vix_is_down_today,
        "term_structure_state": term_structure_state,
        "term_structure_cn": term_structure_cn,
        "vix_hard_brake": vix_hard_brake,
        "raw_bottom_signal": raw_bottom_signal,
        "vix_bottom_premarket_preview": vix_bottom_premarket_preview,
        "vix_bottom_signal": vix_bottom_signal,
        "session_code": sess,
        "vix_state": vix_state,
        "pill_level": pill_level,
        "pill_text": pill_text,
        "summary_cn": summary_cn,
        "advice_cn": advice_cn,
        "available_symbols": available_symbols,
        "degraded": degraded,
    }
    return _apply_session_lock_to_snapshot(payload, session_code=sess)


def _read_cached_snapshot(cache_path: str) -> Optional[Dict[str, Any]]:
    if not cache_path or not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _write_cached_snapshot(cache_path: str, payload: Dict[str, Any]) -> None:
    if not cache_path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
        tmp_path = f"{cache_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"_cached_epoch": time.time(), **payload}, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, cache_path)
    except Exception:
        pass


def get_vix_regime_snapshot(
    force_refresh: bool = False,
    cache_path: str = DEFAULT_CACHE_PATH,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    allow_network: bool = True,
    session_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    获取全局 VIX 恐慌中枢快照（带本地 TTL 节流与静默降级）。
    优先读取未过期的本地缓存；过期或强制刷新时批量抓取 ^VIX/^VIX3M/^VVIX/^TNX；
    网络失败时优雅回退旧缓存或返回 UNAVAILABLE 结构。
    """
    sess = _normalize_session_code(session_code)
    cached = _read_cached_snapshot(cache_path)
    if cached and not force_refresh:
        cached_epoch = float(cached.get("_cached_epoch", 0.0) or 0.0)
        if cached_epoch > 0 and (time.time() - cached_epoch) < max(5, int(ttl_seconds)):
            out = {k: v for k, v in cached.items() if k != "_cached_epoch"}
            return _apply_session_lock_to_snapshot(out, session_code=sess)

    if not allow_network:
        if cached:
            out = {k: v for k, v in cached.items() if k != "_cached_epoch"}
            return _apply_session_lock_to_snapshot(out, session_code=sess)
        return compute_vix_regime_from_series(None, session_code=sess)

    raw_map = fetch_vix_raw_series()
    snap = compute_vix_regime_from_series(
        vix_series=raw_map.get("^VIX"),
        vix3m_series=raw_map.get("^VIX3M"),
        vvix_series=raw_map.get("^VVIX"),
        tnx_series=raw_map.get("^TNX"),
        vxn_series=raw_map.get("^VXN"),
        session_code=sess,
    )

    if snap.get("vix_spot") is None and cached and cached.get("vix_spot") is not None:
        fallback = {k: v for k, v in cached.items() if k != "_cached_epoch"}
        fallback["degraded"] = True
        return _apply_session_lock_to_snapshot(fallback, session_code=sess)

    if snap.get("vix_spot") is not None:
        _write_cached_snapshot(cache_path, snap)
    return snap


def fetch_cross_market_liquidity_raw(timeout: float = 10.0) -> Dict[str, pd.Series]:
    """
    抓取宏观跨市场流动性指标 (HYG / IEF / DX-Y.NYB / JPY=X 近 1 个月日线)。
    严格归属宏观引擎，严禁混入 79 只可交易标的池；任一指数异常静默降级不阻断主流程。
    """
    result: Dict[str, pd.Series] = {sym: pd.Series(dtype=float) for sym in LIQUIDITY_SYMBOLS}
    if yf is None:
        return result

    batch_df = None
    try:
        batch_df = yf.download(
            list(LIQUIDITY_SYMBOLS),
            period="1mo",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=timeout,
        )
    except Exception:
        batch_df = None

    for sym in LIQUIDITY_SYMBOLS:
        raw_s = _extract_symbol_close_from_download(batch_df, sym) if batch_df is not None else pd.Series(dtype=float)
        cleaned = _clean_close_series(raw_s, is_tnx=False)
        if cleaned.empty:
            try:
                hist_df = yf.Ticker(sym).history(period="1mo", interval="1d", auto_adjust=False)
                cleaned = _clean_close_series(_extract_symbol_close_from_download(hist_df, sym), is_tnx=False)
            except Exception:
                cleaned = pd.Series(dtype=float)
        result[sym] = cleaned
    return result


def compute_cross_market_liquidity_from_series(
    hyg_series: Any = None,
    ief_series: Any = None,
    dxy_series: Any = None,
    usdjpy_series: Any = None,
    as_of_date: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    纯因果计算跨市场流动性压力指标：
    1. HYG / IEF（高收益债信用利差比值：下行代表信用利差走阔/抽血）；
    2. DX-Y.NYB（美元指数 DXY：急升代表美元流动性收紧）；
    3. JPY=X（美元兑日元：急跌/日元急升代表 Carry Trade 套息交易去杠杆）。
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hyg_s = _clean_close_series(hyg_series, as_of_date=as_of_date, is_tnx=False)
    ief_s = _clean_close_series(ief_series, as_of_date=as_of_date, is_tnx=False)
    dxy_s = _clean_close_series(dxy_series, as_of_date=as_of_date, is_tnx=False)
    jpy_s = _clean_close_series(usdjpy_series, as_of_date=as_of_date, is_tnx=False)

    available_symbols = [
        sym for sym, ser in (("HYG", hyg_s), ("IEF", ief_s), ("DX-Y.NYB", dxy_s), ("JPY=X", jpy_s))
        if not ser.empty
    ]

    def _spot_and_5d_pct(ser: pd.Series, digits: int = 2) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        if ser is None or ser.empty:
            return None, None, None
        cur = float(ser.iloc[-1])
        prev1 = float(ser.iloc[-2]) if len(ser) >= 2 else None
        pct1 = round(((cur - prev1) / prev1) * 100.0, 2) if (prev1 and prev1 > 0) else None
        ref5 = float(ser.iloc[-6]) if len(ser) >= 6 else (float(ser.iloc[0]) if len(ser) >= 2 else None)
        pct5 = round(((cur - ref5) / ref5) * 100.0, 2) if (ref5 and ref5 > 0) else None
        return round(cur, digits), pct1, pct5

    hyg_ief_ratio_s = pd.Series(dtype=float)
    if not hyg_s.empty and not ief_s.empty:
        u_idx = hyg_s.index.union(ief_s.index).sort_values()
        hyg_u = hyg_s.reindex(u_idx).ffill()
        ief_u = ief_s.reindex(u_idx).ffill()
        hyg_ief_ratio_s = (hyg_u / ief_u).replace([np.inf, -np.inf], np.nan).dropna()

    hyg_ief_ratio, hyg_ief_1d_pct, hyg_ief_5d_pct = _spot_and_5d_pct(hyg_ief_ratio_s, digits=4)
    dxy_spot, dxy_1d_pct, dxy_5d_pct = _spot_and_5d_pct(dxy_s, digits=2)
    usdjpy_spot, usdjpy_1d_pct, usdjpy_5d_pct = _spot_and_5d_pct(jpy_s, digits=2)

    if hyg_ief_ratio is None and dxy_spot is None and usdjpy_spot is None:
        return {
            "as_of_date": None,
            "updated_at": now_str,
            "hyg_ief_ratio": None,
            "hyg_ief_1d_pct": None,
            "hyg_ief_5d_pct": None,
            "dxy_spot": None,
            "dxy_1d_pct": None,
            "dxy_5d_pct": None,
            "usdjpy_spot": None,
            "usdjpy_1d_pct": None,
            "usdjpy_5d_pct": None,
            "credit_stress": False,
            "dxy_tightening": False,
            "carry_unwind": False,
            "stress_flags": [],
            "stress_level": "degraded",
            "badge_cn": "⚪ 跨市场流动性暂缺",
            "summary_cn": "跨市场指标（HYG/IEF · DXY · USD/JPY）外部源暂不可用，已自动静默降级不阻塞主雷达。",
            "available_symbols": available_symbols,
            "degraded": True,
        }

    credit_stress = bool(hyg_ief_5d_pct is not None and hyg_ief_5d_pct <= -1.0)
    dxy_tightening = bool(dxy_5d_pct is not None and dxy_5d_pct >= 1.2)
    carry_unwind = bool(usdjpy_5d_pct is not None and usdjpy_5d_pct <= -1.5)

    stress_flags = []
    if credit_stress:
        stress_flags.append("信用利差走阔")
    if dxy_tightening:
        stress_flags.append("美元急升收紧")
    if carry_unwind:
        stress_flags.append("日元急升/套息逆转")

    if len(stress_flags) >= 2:
        stress_level = "danger"
        badge_cn = "🔴 跨市场流动性收紧预警"
        summary_cn = (
            f"{' + '.join(stress_flags)} ➔ 警惕全球流动性收紧与机构去杠杆波及风险资产。"
        )
    elif len(stress_flags) == 1:
        stress_level = "warn"
        badge_cn = "🟡 局部跨市场流动性波动"
        summary_cn = (
            f"监测到【{stress_flags[0]}】➔ 留意风险溢价波动，精选强现金流及抗跌标的。"
        )
    else:
        stress_level = "calm"
        badge_cn = "🟢 跨市场流动性平稳"
        summary_cn = "高收益信用利差、美元指数与日元套息线温和运行，未见跨市场流动性抽血共振。"

    latest_dates = [
        ser.index[-1] for ser in (hyg_ief_ratio_s, dxy_s, jpy_s) if not ser.empty
    ]
    as_of_str = max(latest_dates).strftime("%Y-%m-%d") if latest_dates else None

    return {
        "as_of_date": as_of_str,
        "updated_at": now_str,
        "hyg_ief_ratio": hyg_ief_ratio,
        "hyg_ief_1d_pct": hyg_ief_1d_pct,
        "hyg_ief_5d_pct": hyg_ief_5d_pct,
        "dxy_spot": dxy_spot,
        "dxy_1d_pct": dxy_1d_pct,
        "dxy_5d_pct": dxy_5d_pct,
        "usdjpy_spot": usdjpy_spot,
        "usdjpy_1d_pct": usdjpy_1d_pct,
        "usdjpy_5d_pct": usdjpy_5d_pct,
        "credit_stress": credit_stress,
        "dxy_tightening": dxy_tightening,
        "carry_unwind": carry_unwind,
        "stress_flags": stress_flags,
        "stress_level": stress_level,
        "badge_cn": badge_cn,
        "summary_cn": summary_cn,
        "available_symbols": available_symbols,
        "degraded": len(available_symbols) < len(LIQUIDITY_SYMBOLS),
    }


def get_cross_market_liquidity_snapshot(
    force_refresh: bool = False,
    cache_path: str = DEFAULT_LIQUIDITY_CACHE_PATH,
    ttl_seconds: int = 300,
    allow_network: bool = True,
) -> Dict[str, Any]:
    """获取跨市场流动性监控快照（HYG/IEF + DX-Y.NYB + JPY=X，TTL 缓存 + 静默降级）。"""
    cached = _read_cached_snapshot(cache_path)
    if cached and not force_refresh:
        cached_epoch = float(cached.get("_cached_epoch", 0.0) or 0.0)
        if cached_epoch > 0 and (time.time() - cached_epoch) < max(5, int(ttl_seconds)):
            return {k: v for k, v in cached.items() if k != "_cached_epoch"}

    if not allow_network:
        if cached:
            return {k: v for k, v in cached.items() if k != "_cached_epoch"}
        return compute_cross_market_liquidity_from_series()

    raw_map = fetch_cross_market_liquidity_raw()
    snap = compute_cross_market_liquidity_from_series(
        hyg_series=raw_map.get("HYG"),
        ief_series=raw_map.get("IEF"),
        dxy_series=raw_map.get("DX-Y.NYB"),
        usdjpy_series=raw_map.get("JPY=X"),
    )

    if snap.get("stress_level") == "degraded" and cached and cached.get("stress_level") != "degraded":
        fallback = {k: v for k, v in cached.items() if k != "_cached_epoch"}
        fallback["degraded"] = True
        return fallback

    if snap.get("stress_level") != "degraded":
        _write_cached_snapshot(cache_path, snap)
    return snap


def _fmt_val(v: Any, digits: int = 2, suffix: str = "") -> str:
    if v is None:
        return "--"
    try:
        fv = float(v)
        if not math.isfinite(fv):
            return "--"
        return f"{fv:.{digits}f}{suffix}"
    except Exception:
        return "--"


def render_vix_pill_widget_parts(
    vix_regime: Optional[Dict[str, Any]] = None,
    popover_align: str = "right",
) -> Tuple[str, str, str]:
    """
    返回可在 predict_radar_5y.html / macro_radar.html / stocks_radar.html 复用的
    (css_str, pill_and_popover_html, js_str)，共用 futu_theme CSS 变量，严格兼容深/浅双主题与移动端零溢出。
    注意：js_str 内不含 HTML id="..." 属性，确保全页 DOM ID 唯一审计 0 重复。
    """
    vr = vix_regime if isinstance(vix_regime, dict) and vix_regime else compute_vix_regime_from_series(None)
    lvl = str(vr.get("pill_level") or "neutral")
    pill_text = _html.escape(str(vr.get("pill_text") or "⚪ VIX 暂缺 · 常规门禁"))
    vix_str = _fmt_val(vr.get("vix_spot"), 2)
    vxn_str = _fmt_val(vr.get("vxn_spot"), 2)
    spread_v = vr.get("vxn_vix_spread")
    try:
        spread_str = f"{float(spread_v):+.2f}" if spread_v is not None and math.isfinite(float(spread_v)) else "--"
    except Exception:
        spread_str = "--"
    tech_label = _html.escape(str(vr.get("tech_fear_label") or "⚪ 科技波动平稳"))
    vix3m_str = _fmt_val(vr.get("vix3m_spot"), 2)
    ratio_v = vr.get("vix_term_ratio")
    ratio_str = _fmt_val(ratio_v, 3)
    vvix_str = _fmt_val(vr.get("vvix_spot"), 2)
    tnx_str = _fmt_val(vr.get("tnx_yield"), 2, "%")
    peak_str = _fmt_val(vr.get("vix_10d_peak"), 2)
    dd_str = _fmt_val(vr.get("vix_drawdown_from_peak_pct"), 1, "%")
    summary_str = _html.escape(str(vr.get("summary_cn") or "波动率监测中"))
    advice_str = _html.escape(str(vr.get("advice_cn") or "比值回落至 0.97 以下、恐慌自 10 日高点回撤 ≥5% 且当日 VIX 未反扑（≤+3%）时开启右侧抄底窗。"))
    term_cn = _html.escape(str(vr.get("term_structure_cn") or "--"))

    ratio_badge_cls = "vix-m-danger" if (ratio_v is not None and float(ratio_v) >= 1.0) else (
        "vix-m-warn" if (ratio_v is not None and float(ratio_v) >= 0.92) else "vix-m-ok"
    )
    spread_badge_cls = "vix-m-danger" if (spread_v is not None and float(spread_v) >= 5.5) else (
        "vix-m-warn" if (spread_v is not None and float(spread_v) >= 3.5) else ""
    )
    pop_pos_css = "left: 0; right: auto;" if str(popover_align).lower() == "left" else "right: 0; left: auto;"

    css_str = f"""
        .vix-pill-wrap {{
            position: relative;
            display: inline-flex;
            align-items: center;
            flex-shrink: 0;
        }}
        .vix-regime-pill {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 11.5px;
            font-weight: 700;
            border: 1px solid rgba(148, 163, 184, 0.35);
            background: rgba(148, 163, 184, 0.12);
            color: var(--text-main, var(--text-primary, #F3F4F6));
            cursor: pointer;
            white-space: nowrap;
            transition: transform 0.12s ease, border-color 0.15s ease;
            font-family: inherit;
        }}
        .vix-regime-pill:hover {{ border-color: #38bdf8; }}
        .vix-regime-pill.level-danger {{
            color: #ef4444;
            background: rgba(239, 68, 68, 0.14);
            border-color: rgba(239, 68, 68, 0.45);
        }}
        .vix-regime-pill.level-bottom {{
            color: #10b981;
            background: rgba(16, 185, 129, 0.14);
            border-color: rgba(16, 185, 129, 0.45);
        }}
        .vix-regime-pill.level-warn {{
            color: #f59e0b;
            background: rgba(245, 158, 11, 0.14);
            border-color: rgba(245, 158, 11, 0.45);
        }}
        [data-theme="light"] .vix-regime-pill.level-danger {{ color: #DC2626; background: #FEE2E2; border-color: #FCA5A5; }}
        [data-theme="light"] .vix-regime-pill.level-bottom {{ color: #059669; background: #D1FAE5; border-color: #6EE7B7; }}
        [data-theme="light"] .vix-regime-pill.level-warn {{ color: #B45309; background: #FEF3C7; border-color: #FCD34D; }}
        [data-theme="light"] .vix-regime-pill.level-calm,
        [data-theme="light"] .vix-regime-pill.level-neutral {{ color: #334155; background: #F1F5F9; border-color: #CBD5E1; }}

        .vix-detail-popover {{
            display: none;
            position: absolute;
            top: calc(100% + 8px);
            {pop_pos_css}
            width: 340px;
            max-width: calc(100vw - 24px);
            background: var(--bg-card, var(--panel-surface, #121826));
            border: 1px solid var(--border-strong, var(--border-color, #334155));
            border-radius: 12px;
            padding: 12px 14px;
            box-shadow: var(--shadow-card, 0 12px 28px rgba(0, 0, 0, 0.45));
            z-index: 900;
            text-align: left;
            color: var(--text-main, var(--text-primary, #F3F4F6));
        }}
        .vix-detail-popover.is-open,
        .vix-detail-popover.is-hover-open {{ display: block; }}
        .vix-pop-head {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            padding-bottom: 8px;
            margin-bottom: 8px;
            border-bottom: 1px solid var(--border-subtle, var(--border-color, rgba(255,255,255,0.1)));
            font-size: 12.5px;
            font-weight: 800;
        }}
        .vix-pop-grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 6px;
            margin-bottom: 8px;
        }}
        .vix-pop-cell {{
            background: var(--bg-elevated, var(--surface-elevated, rgba(255,255,255,0.04)));
            border: 1px solid var(--border-subtle, var(--border-color, rgba(255,255,255,0.08)));
            border-radius: 8px;
            padding: 6px 8px;
        }}
        .vix-pop-lbl {{
            display: block;
            font-size: 10.5px;
            color: var(--text-muted, #94a3b8);
            margin-bottom: 2px;
        }}
        .vix-pop-val {{
            font-size: 13px;
            font-weight: 800;
            font-variant-numeric: tabular-nums;
            color: var(--text-main, var(--text-primary, #f3f4f6));
        }}
        .vix-m-danger {{ color: #ef4444 !important; }}
        .vix-m-warn {{ color: #f59e0b !important; }}
        .vix-m-ok {{ color: #10b981 !important; }}
        .vix-pop-tech {{
            font-size: 11.5px;
            font-weight: 700;
            line-height: 1.4;
            color: var(--text-main, var(--text-primary, #f3f4f6));
            background: var(--bg-elevated, var(--surface-elevated, rgba(255,255,255,0.05)));
            border: 1px solid var(--border-subtle, var(--border-color, rgba(255,255,255,0.1)));
            border-radius: 8px;
            padding: 6px 9px;
            margin-bottom: 6px;
        }}
        .vix-pop-advice {{
            font-size: 11.5px;
            line-height: 1.5;
            color: var(--text-sub, var(--text-secondary, #cbd5e1));
            background: var(--bg-elevated, var(--surface-elevated, rgba(56,189,248,0.08)));
            border-radius: 8px;
            padding: 7px 9px;
        }}
        @media (max-width: 768px) {{
            .vix-detail-popover {{
                position: fixed;
                left: 10px !important;
                right: 10px !important;
                top: 56px;
                width: auto;
                max-width: calc(100vw - 20px);
            }}
        }}
    """

    pill_html = f"""
        <div class="vix-pill-wrap" id="vixPillWrap">
            <button type="button" id="pVixRegimePill" class="vix-regime-pill level-{lvl}" onclick="toggleVixPopover(event)" title="{summary_str}" aria-expanded="false" aria-controls="vixDetailPopover">{pill_text}</button>
            <div id="vixDetailPopover" class="vix-detail-popover" role="dialog" aria-label="全局真 VIX 恐慌中枢详情">
                <div class="vix-pop-head">
                    <span>⚡ 全局真 VIX 恐慌中枢</span>
                    <span style="font-size: 11px; color: var(--text-muted, #94a3b8);">{term_cn}</span>
                </div>
                <div class="vix-pop-grid">
                    <div class="vix-pop-cell"><span class="vix-pop-lbl">^VIX (标普恐慌)</span><span class="vix-pop-val">{vix_str}</span></div>
                    <div class="vix-pop-cell"><span class="vix-pop-lbl">^VIX3M (3M远期)</span><span class="vix-pop-val">{vix3m_str}</span></div>
                    <div class="vix-pop-cell"><span class="vix-pop-lbl">纳指恐慌 (^VXN)</span><span class="vix-pop-val">{vxn_str}</span></div>
                    <div class="vix-pop-cell"><span class="vix-pop-lbl">科技溢价 (VXN - VIX)</span><span class="vix-pop-val {spread_badge_cls}">{spread_str}</span></div>
                    <div class="vix-pop-cell"><span class="vix-pop-lbl">期限比值 (VIX/VIX3M)</span><span class="vix-pop-val {ratio_badge_cls}">{ratio_str}</span></div>
                    <div class="vix-pop-cell"><span class="vix-pop-lbl">^VVIX (波动的波动)</span><span class="vix-pop-val">{vvix_str}</span></div>
                    <div class="vix-pop-cell"><span class="vix-pop-lbl">10Y 美债 (^TNX)</span><span class="vix-pop-val">{tnx_str}</span></div>
                    <div class="vix-pop-cell"><span class="vix-pop-lbl">10日峰值 / 回撤</span><span class="vix-pop-val">{peak_str} / -{dd_str}</span></div>
                </div>
                <div class="vix-pop-tech">{tech_label}</div>
                <div class="vix-pop-advice">{advice_str}</div>
            </div>
        </div>
    """

    js_str = """
        function toggleVixPopover(ev) {
            if (ev && ev.stopPropagation) ev.stopPropagation();
            var pop = document.getElementById('vixDetailPopover');
            var pill = document.getElementById('pVixRegimePill');
            if (!pop || !pill) return;
            var open = !pop.classList.contains('is-open');
            pop.classList.toggle('is-open', open);
            if (!open) pop.classList.remove('is-hover-open');
            pill.setAttribute('aria-expanded', open ? 'true' : 'false');
        }
        (function initVixPillHover() {
            var wrap = document.getElementById('vixPillWrap');
            var pop = document.getElementById('vixDetailPopover');
            if (!wrap || !pop || !window.matchMedia || !window.matchMedia('(hover: hover)').matches) return;
            wrap.addEventListener('mouseenter', function() { pop.classList.add('is-hover-open'); });
            wrap.addEventListener('mouseleave', function() { pop.classList.remove('is-hover-open'); });
        })();
        document.addEventListener('click', function(e) {
            var wrap = document.getElementById('vixPillWrap');
            var pop = document.getElementById('vixDetailPopover');
            var pill = document.getElementById('pVixRegimePill');
            if (!wrap || !pop) return;
            if (!wrap.contains(e.target) && (pop.classList.contains('is-open') || pop.classList.contains('is-hover-open'))) {
                pop.classList.remove('is-open');
                pop.classList.remove('is-hover-open');
                if (pill) pill.setAttribute('aria-expanded', 'false');
            }
        });
        document.addEventListener('keydown', function(e) {
            if (e && e.key === 'Escape') {
                var pop = document.getElementById('vixDetailPopover');
                var pill = document.getElementById('pVixRegimePill');
                if (pop) { pop.classList.remove('is-open'); pop.classList.remove('is-hover-open'); }
                if (pill) pill.setAttribute('aria-expanded', 'false');
            }
        });
    """
    return css_str, pill_html, js_str
