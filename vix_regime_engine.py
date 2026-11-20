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
VIX_SYMBOLS = ("^VIX", "^VIX3M", "^VVIX", "^TNX")
DEFAULT_CACHE_PATH = os.path.join(WORK_DIR, "data_cache", "vix_regime_cache.json")
DEFAULT_TTL_SECONDS = 180


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
) -> Dict[str, Any]:
    """
    纯因果 VIX 恐慌中枢状态机（Zero Lookahead Bias）：
    严格只用 <= as_of_date 数据与向后因果前值对齐（ffill，绝不使用 bfill）。
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    vix_s = _clean_close_series(vix_series, as_of_date=as_of_date, is_tnx=False)
    vix3m_s = _clean_close_series(vix3m_series, as_of_date=as_of_date, is_tnx=False)
    vvix_s = _clean_close_series(vvix_series, as_of_date=as_of_date, is_tnx=False)
    tnx_s = _clean_close_series(tnx_series, as_of_date=as_of_date, is_tnx=True)

    available_symbols = [
        sym for sym, ser in (("^VIX", vix_s), ("^VIX3M", vix3m_s), ("^VVIX", vvix_s), ("^TNX", tnx_s))
        if not ser.empty
    ]

    if vix_s.empty:
        return {
            "as_of_date": None,
            "updated_at": now_str,
            "vix_spot": None,
            "vix_prev": None,
            "vix_pct": None,
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
            "vix_bottom_signal": False,
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
    vix3m_spot_f, _, vix3m_pct = _last_prev_pct(vix3m_s)
    vvix_spot_f, _, vvix_pct = _last_prev_pct(vvix_s)
    tnx_spot_f, _, tnx_pct = _last_prev_pct(tnx_s)
    vix_is_down_today = bool(vix_prev_f is not None and vix_spot_f < vix_prev_f)

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

    # 状态 B：恐慌出清 · 右侧抄底观察窗 (需同时满足曾极端恐慌 + 见顶确认回落且当前已非硬刹车)
    had_extreme_panic_10d = bool(
        (vix_10d_peak_f >= 26.0)
        or (vix_term_ratio_10d_peak_f is not None and vix_term_ratio_10d_peak_f >= 1.03)
        or (vvix_10d_peak_f is not None and vvix_10d_peak_f >= 112.0)
    )
    ratio_cooled = bool(
        (vix_term_ratio_f is not None and vix_term_ratio_10d_peak_f is not None)
        and (
            (vix_term_ratio_f < 1.00 and vix_term_ratio_f < vix_term_ratio_10d_peak_f)
            or ((vix_term_ratio_10d_peak_f - vix_term_ratio_f) >= 0.06)
        )
    )
    vol_cooled = bool(
        (vix_dd_pct >= 8.0)
        or (vvix_dd_pct is not None and vvix_dd_pct >= 8.0 and vix_is_down_today)
    )
    vix_bottom_signal = bool((not vix_hard_brake) and had_extreme_panic_10d and ratio_cooled and vol_cooled)

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
            "待比值回落跌破 1.00 且恐慌回撤 ≥8% 时自动开启右侧抄底窗口。"
        )
    elif vix_bottom_signal:
        vix_state = "WASHOUT_BOTTOM_WINDOW"
        pill_level = "bottom"
        pill_text = f"🟢 VIX {vix_disp} · 恐慌出清抄底窗"
        summary_cn = f"恐慌确认见顶回落（高点 {vix_10d_peak_f:.1f} 回撤 {vix_dd_pct:.1f}%），右侧优质标的抄底窗已开启"
        advice_cn = (
            f"近10日极端恐慌已回落 {vix_dd_pct:.1f}% 且期限结构恢复升水"
            f"（ratio={vix_term_ratio_f:.2f}），对非暴跌熔断且站稳日内 VWAP 的优质标的放宽多头胜率上限至 58%。"
            if vix_term_ratio_f is not None
            else "近10日极端恐慌已回落且情绪修复，可右侧精选站稳日内 VWAP 的优质龙头。"
        )
    elif (vix_term_ratio_f is not None and vix_term_ratio_f >= 0.92) or (vix_spot_f >= 20.0):
        vix_state = "ELEVATED_CAUTION"
        pill_level = "warn"
        pill_text = f"🟡 VIX {vix_disp} · 避险升温({ratio_disp})" if ratio_disp else f"🟡 VIX {vix_disp} · 避险升温"
        summary_cn = "波动率抬升或期限收窄，避险升温"
        advice_cn = "避险情绪升温但期限尚未倒挂，控制单笔仓位、优选站稳均线与日内 VWAP 的龙头标的。"
    else:
        vix_state = "CALM_CONTANGO"
        pill_level = "calm"
        pill_text = f"⚪ VIX {vix_disp} · 平稳({ratio_disp})" if ratio_disp else f"⚪ VIX {vix_disp} · 平稳"
        summary_cn = "期限结构正常升水，恐慌水平平稳"
        advice_cn = "远期波动率高于近期（Contango），机构对冲需求温和，按常规趋势与黄金坑纪律执行。"

    degraded = len(available_symbols) < len(VIX_SYMBOLS)
    return {
        "as_of_date": vix_s.index[-1].strftime("%Y-%m-%d"),
        "updated_at": now_str,
        "vix_spot": round(vix_spot_f, 2),
        "vix_prev": round(vix_prev_f, 2) if vix_prev_f is not None else None,
        "vix_pct": vix_pct,
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
        "vix_bottom_signal": vix_bottom_signal,
        "vix_state": vix_state,
        "pill_level": pill_level,
        "pill_text": pill_text,
        "summary_cn": summary_cn,
        "advice_cn": advice_cn,
        "available_symbols": available_symbols,
        "degraded": degraded,
    }


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
) -> Dict[str, Any]:
    """
    获取全局 VIX 恐慌中枢快照（带本地 TTL 节流与静默降级）。
    优先读取未过期的本地缓存；过期或强制刷新时批量抓取 ^VIX/^VIX3M/^VVIX/^TNX；
    网络失败时优雅回退旧缓存或返回 UNAVAILABLE 结构。
    """
    cached = _read_cached_snapshot(cache_path)
    if cached and not force_refresh:
        cached_epoch = float(cached.get("_cached_epoch", 0.0) or 0.0)
        if cached_epoch > 0 and (time.time() - cached_epoch) < max(5, int(ttl_seconds)):
            out = {k: v for k, v in cached.items() if k != "_cached_epoch"}
            return out

    if not allow_network:
        if cached:
            return {k: v for k, v in cached.items() if k != "_cached_epoch"}
        return compute_vix_regime_from_series(None)

    raw_map = fetch_vix_raw_series()
    snap = compute_vix_regime_from_series(
        vix_series=raw_map.get("^VIX"),
        vix3m_series=raw_map.get("^VIX3M"),
        vvix_series=raw_map.get("^VVIX"),
        tnx_series=raw_map.get("^TNX"),
    )

    if snap.get("vix_spot") is None and cached and cached.get("vix_spot") is not None:
        fallback = {k: v for k, v in cached.items() if k != "_cached_epoch"}
        fallback["degraded"] = True
        return fallback

    if snap.get("vix_spot") is not None:
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
    vix3m_str = _fmt_val(vr.get("vix3m_spot"), 2)
    ratio_v = vr.get("vix_term_ratio")
    ratio_str = _fmt_val(ratio_v, 3)
    vvix_str = _fmt_val(vr.get("vvix_spot"), 2)
    tnx_str = _fmt_val(vr.get("tnx_yield"), 2, "%")
    peak_str = _fmt_val(vr.get("vix_10d_peak"), 2)
    dd_str = _fmt_val(vr.get("vix_drawdown_from_peak_pct"), 1, "%")
    summary_str = _html.escape(str(vr.get("summary_cn") or "波动率监测中"))
    advice_str = _html.escape(str(vr.get("advice_cn") or "关注期限结构比值与恐慌回撤信号。"))
    term_cn = _html.escape(str(vr.get("term_structure_cn") or "--"))

    ratio_badge_cls = "vix-m-danger" if (ratio_v is not None and float(ratio_v) >= 1.0) else (
        "vix-m-warn" if (ratio_v is not None and float(ratio_v) >= 0.92) else "vix-m-ok"
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
                    <div class="vix-pop-cell"><span class="vix-pop-lbl">^VIX (现货恐慌)</span><span class="vix-pop-val">{vix_str}</span></div>
                    <div class="vix-pop-cell"><span class="vix-pop-lbl">^VIX3M (3M远期)</span><span class="vix-pop-val">{vix3m_str}</span></div>
                    <div class="vix-pop-cell"><span class="vix-pop-lbl">期限比值 (VIX/VIX3M)</span><span class="vix-pop-val {ratio_badge_cls}">{ratio_str}</span></div>
                    <div class="vix-pop-cell"><span class="vix-pop-lbl">^VVIX (波动的波动)</span><span class="vix-pop-val">{vvix_str}</span></div>
                    <div class="vix-pop-cell"><span class="vix-pop-lbl">10Y 美债 (^TNX)</span><span class="vix-pop-val">{tnx_str}</span></div>
                    <div class="vix-pop-cell"><span class="vix-pop-lbl">10日峰值 / 回撤</span><span class="vix-pop-val">{peak_str} / -{dd_str}</span></div>
                </div>
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
