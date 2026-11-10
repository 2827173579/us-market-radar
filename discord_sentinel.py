#!/usr/bin/env python3
"""
discord_sentinel.py - US Market Multi-Tier Quantitative Sentinel & Sector Inspection
Standalone CLI & Daemon Service with Discord Webhook Rich-Text Integration.

M0 Stub Interface Contracts defined per PROJECT.md and TEST_INFRA.md.
Detailed implementations to be completed in:
- M1: 5 Core Engineering Corrections & Data Pipeline
- M2: 5 Tiers Quantitative Engine & Resonance
- M3: Discord Embed Formatting, CLI & Radar Linkage
"""

import os
import sys
import time
import math
import json
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Union
import pandas as pd
import numpy as np

# Ensure UTF-8 output on Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

def load_env_file(env_path: Optional[str] = None) -> None:
    """安全加载本地 .env 文件至 os.environ"""
    if env_path is None:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        from dotenv import load_dotenv
        if os.path.exists(env_path):
            load_dotenv(env_path, override=False)
            return
    except ImportError:
        pass
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass

load_env_file()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

# Universe Definition: 51 unique tickers across 5 tiers
SENTINEL_UNIVERSE_TIERS: Dict[str, List[str]] = {
    "TIER_1_3X": ["SOXL", "SOXS", "TQQQ", "SQQQ"],
    "TIER_2_CORE": ["SPY", "MRVL", "LITE", "BE", "PLTR", "MSFT", "NOW", "COHR", "CRDO", "NBIS", "APLD", "MRNA"],
    "TIER_3_CHIPS": ["SOXX", "SMH", "NVDA", "AVGO", "TSM", "ASML", "AMD", "ARM", "INTC", "MU", "STX", "WDC", "SNDK"],
    "TIER_4_TECH": ["QQQ", "BTC-USD", "MSTR", "COIN", "AAPL", "GOOG", "AMZN", "META", "TSLA", "ORCL", "DELL", "CRWV", "ALAB"],
    "TIER_5_DEFENSE": ["SCHD", "KO", "GLD", "GDX", "VST", "CEG", "NLR", "TLT", "VIXY"],
}

ALL_SENTINEL_TICKERS: List[str] = [
    sym for tier_syms in SENTINEL_UNIVERSE_TIERS.values() for sym in tier_syms
]

TIER_METADATA = {
    "TIER_1_3X": {"title": "👑 【置顶】3X多空杠杆决战台", "color": 15844367},
    "TIER_2_CORE": {"title": "⚡ 【第二梯队】核心高弹性与重点池", "color": 10181046},
    "TIER_3_CHIPS": {"title": "🖥️ 【第三梯队】芯片半导体与存储硬件", "color": 3447003},
    "TIER_4_TECH": {"title": "🌐 【第四梯队】科技巨头、AI云与加密", "color": 5793266},
    "TIER_5_DEFENSE": {"title": "🛡️ 【第五梯队】神秘因子Y与宏观防御", "color": 3066993},
}


# ==============================================================================
# Tier 1: 5 Core Engineering Corrections (Implemented in M1)
# ==============================================================================

# In-memory market data cache with TTL
_SENTINEL_MARKET_CACHE: Dict[str, Any] = {
    "timestamp": 0.0,
    "data": None,
    "tickers": []
}

def clear_sentinel_market_cache() -> None:
    """清空本地内存行情缓存"""
    global _SENTINEL_MARKET_CACHE
    _SENTINEL_MARKET_CACHE = {
        "timestamp": 0.0,
        "data": None,
        "tickers": []
    }


def aggregate_1h_to_4h_df(df_1h: pd.DataFrame, ticker: str = "") -> pd.DataFrame:
    """
    4H K线聚合引擎 (严禁跨日拼接):
    - 美股 RTH (09:30~16:00 ET): 每日固定 2 根 Bar (Bar 1: 09:30..12:30; Bar 2: 13:30..15:30)
    - 加密货币 (BTC-USD 24/7): 每日固定按 4 小时分片，严禁跨 UTC 日期拼接
    """
    if df_1h is None or df_1h.empty:
        return pd.DataFrame()

    df = df_1h.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    ticker_upper = (ticker or "").upper()
    is_crypto = ticker_upper in ("BTC", "BTC-USD") or "BTC" in ticker_upper

    if is_crypto:
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        records = []
        for dt, day_df in df.groupby(df.index.date, sort=False):
            day_sorted = day_df.sort_index()
            for i in range(0, len(day_sorted), 4):
                chunk = day_sorted.iloc[i : i + 4]
                if chunk.empty:
                    continue
                records.append({
                    "Datetime": chunk.index[0],
                    "Open": float(chunk["Open"].iloc[0]),
                    "High": float(chunk["High"].max()),
                    "Low": float(chunk["Low"].min()),
                    "Close": float(chunk["Close"].iloc[-1]),
                    "Volume": float(chunk["Volume"].sum()) if "Volume" in chunk else 0.0,
                })
        if not records:
            return pd.DataFrame()
        res_df = pd.DataFrame(records).set_index("Datetime")
        return res_df
    else:
        # 美股标的：统一转换至 America/New_York 时区
        if df.index.tz is None:
            df.index = df.index.tz_localize("America/New_York")
        else:
            df.index = df.index.tz_convert("America/New_York")

        records = []
        for dt, day_df in df.groupby(df.index.date, sort=False):
            day_sorted = day_df.sort_index()
            # 过滤仅保留 RTH 时段 (09:30 ~ 16:00 ET)
            ny_hr = day_sorted.index.hour + day_sorted.index.minute / 60.0
            rth_df = day_sorted[(ny_hr >= 9.5) & (ny_hr < 16.0)]
            if rth_df.empty:
                continue

            bar1_mask = (rth_df.index.hour < 13) | ((rth_df.index.hour == 13) & (rth_df.index.minute < 30))
            bar1_df = rth_df[bar1_mask]
            bar2_df = rth_df[~bar1_mask]

            for chunk in [bar1_df, bar2_df]:
                if chunk.empty:
                    continue
                records.append({
                    "Datetime": chunk.index[0],
                    "Open": float(chunk["Open"].iloc[0]),
                    "High": float(chunk["High"].max()),
                    "Low": float(chunk["Low"].min()),
                    "Close": float(chunk["Close"].iloc[-1]),
                    "Volume": float(chunk["Volume"].sum()) if "Volume" in chunk else 0.0,
                })

        if not records:
            return pd.DataFrame()
        res_df = pd.DataFrame(records).set_index("Datetime")
        return res_df


def append_realtime_snapshot_bar(df_bars: pd.DataFrame, realtime_price: float, current_session: str) -> pd.DataFrame:
    """
    盘前/盘后实时价防篡改历史 K 线:
    在盘前(<09:30 ET)或盘后(>16:00 ET)时，严禁覆盖历史收盘 Bar;
    在末尾临时 append 快照 Bar 用于参与实时 RSI 计算，保持历史 K 线纯净。
    """
    if df_bars is None or df_bars.empty:
        return df_bars
    if realtime_price is None or realtime_price <= 0:
        return df_bars

    df_calc = df_bars.copy()
    last_close = float(df_calc["Close"].iloc[-1])

    ext_sessions = ("PRE_MARKET", "POST_MARKET", "OVERNIGHT", "WEEKEND")
    if current_session in ext_sessions:
        if abs(realtime_price - last_close) > 1e-4:
            if df_calc.index.tz is not None:
                now_idx = pd.Timestamp.now(tz=df_calc.index.tz)
            else:
                now_idx = pd.Timestamp.now()

            if len(df_calc) > 0 and now_idx <= df_calc.index[-1]:
                now_idx = df_calc.index[-1] + pd.Timedelta(minutes=5)

            snapshot_bar = pd.DataFrame([{
                "Open": last_close,
                "High": max(last_close, float(realtime_price)),
                "Low": min(last_close, float(realtime_price)),
                "Close": float(realtime_price),
                "Volume": 0.0
            }], index=[now_idx])
            snapshot_bar.index.name = df_calc.index.name
            df_calc = pd.concat([df_calc, snapshot_bar])

    return df_calc


def compute_and_protect_1h_sr(df_1h: pd.DataFrame, active_price: float, poc: Optional[float] = None) -> Tuple[float, float, float]:
    """
    计算近 20 根 1H K 线支撑与阻力，并强制执行边界保护 invariant:
    1H撑 < active_price < 1H压
    破位: min(POC, active_price - 1.5 * ATR_1H)
    突破: active_price + 1.5 * ATR_1H
    """
    active_price = float(active_price)
    if df_1h is None or df_1h.empty or len(df_1h) < 2:
        eff_atr = max(round(active_price * 0.02, 2), 0.01)
        s = round(active_price - 1.5 * eff_atr, 2)
        r = round(active_price + 1.5 * eff_atr, 2)
        if s >= active_price:
            s = round(active_price * 0.98, 2)
        if r <= active_price:
            r = round(active_price * 1.02, 2)
        return s, r, eff_atr

    sub20 = df_1h.tail(20)
    low_20h = float(sub20["Low"].min())
    high_20h = float(sub20["High"].max())

    prev_close = sub20["Close"].shift(1)
    tr = pd.concat([
        sub20["High"] - sub20["Low"],
        (sub20["High"] - prev_close).abs(),
        (sub20["Low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    atr_1h = float(tr.ewm(alpha=1/14, adjust=False).mean().iloc[-1])
    eff_atr = max(atr_1h, active_price * 0.01, 0.01)

    s1 = low_20h
    r1 = high_20h

    # 破位: low_20h >= active_price
    if s1 >= active_price:
        if poc is not None and not (isinstance(poc, float) and math.isnan(poc)) and poc < active_price:
            s1 = min(poc, active_price - 1.5 * eff_atr)
        else:
            s1 = active_price - 1.5 * eff_atr

    # 突破: high_20h <= active_price
    if r1 <= active_price:
        r1 = active_price + 1.5 * eff_atr

    s1 = round(float(s1), 2)
    r1 = round(float(r1), 2)
    eff_atr = round(float(eff_atr), 2)

    # 严格不变式终极防线 (1H撑 < active_price < 1H压)
    if s1 >= active_price:
        s1 = round(active_price - max(eff_atr, 0.01), 2)
        if s1 >= active_price:
            s1 = round(active_price - 0.01, 2)
    if r1 <= active_price:
        r1 = round(active_price + max(eff_atr, 0.01), 2)
        if r1 <= active_price:
            r1 = round(active_price + 0.01, 2)

    return s1, r1, eff_atr


def fetch_sentinel_market_data(tickers: List[str], use_cache: bool = True, cache_ttl: float = 60.0) -> Dict[str, Any]:
    """
    单次批量并发抓取 51 只标的 1H 与 1D 数据，BTC-USD 原生直取，0 Finnhub 429 风险。
    支持内存缓存 (TTL 默认 60s)。
    """
    global _SENTINEL_MARKET_CACHE
    import yfinance as yf

    now = time.time()
    # Check cache if valid
    if (use_cache and _SENTINEL_MARKET_CACHE["data"] is not None and 
            (now - _SENTINEL_MARKET_CACHE["timestamp"] < cache_ttl) and
            set(tickers).issubset(set(_SENTINEL_MARKET_CACHE["tickers"]))):
        return _SENTINEL_MARKET_CACHE["data"]

    tickers_clean = [t.strip().upper() for t in tickers if t and t.strip()]
    if not tickers_clean:
        return {"dl_1h": pd.DataFrame(), "dl_1d": pd.DataFrame(), "tickers": []}

    ticker_str = " ".join(tickers_clean)

    # Batch download 1H and 1D data via yfinance (multi-threaded)
    dl_1h = yf.download(
        tickers=ticker_str,
        period="30d",
        interval="1h",
        group_by="ticker",
        threads=True,
        progress=False
    )
    dl_1d = yf.download(
        tickers=ticker_str,
        period="60d",
        interval="1d",
        group_by="ticker",
        threads=True,
        progress=False
    )

    result = {
        "dl_1h": dl_1h,
        "dl_1d": dl_1d,
        "tickers": tickers_clean,
        "timestamp": now
    }

    _SENTINEL_MARKET_CACHE = {
        "timestamp": now,
        "data": result,
        "tickers": tickers_clean
    }
    return result


# ==============================================================================
# Tier 2: Quantitative Engine & State Machine (Implemented in M2)
# ==============================================================================

def compute_series_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    计算 Wilder 平滑 RSI (14 周期)。
    """
    if series is None or len(series) < period:
        return pd.Series(index=series.index if series is not None else None, dtype=float)
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    # When avg_loss is 0.0 (all positive bars), replace by 1e-10 so rsi evaluates to 100.0 instead of NaN
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # If both avg_gain and avg_loss are 0 (completely flat series), RSI is neutrally 50.0
    rsi.loc[(avg_gain == 0) & (avg_loss == 0)] = 50.0
    return rsi


def compute_volume_profile_poc(df_1h: pd.DataFrame) -> float:
    """
    Volume Profile POC:
    - 窗口: 最近 140 根 1H K线 (~20 个交易日)
    - 箱体: 25 个等宽价格区间 (min(Low) ~ max(High))
    - Typical Price: (High + Low + Close) / 3，归入对应箱体累加 Volume
    - 取累积成交量最大箱体的中心值作为 POC
    - 边界保护: 零成交量回退均匀时间分布 (time-at-price)；价格无波动/扁平回退最新价
    """
    if df_1h is None or df_1h.empty:
        return 0.0

    df = df_1h.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    sub = df.tail(140)
    if sub.empty:
        return 0.0

    high_arr = pd.to_numeric(sub["High"], errors="coerce").values
    low_arr = pd.to_numeric(sub["Low"], errors="coerce").values
    close_arr = pd.to_numeric(sub["Close"], errors="coerce").values
    vol_arr = pd.to_numeric(sub["Volume"], errors="coerce").fillna(0.0).values

    valid_mask = (~np.isnan(high_arr)) & (~np.isnan(low_arr)) & (~np.isnan(close_arr))
    if not np.any(valid_mask):
        return 0.0

    high_arr = high_arr[valid_mask]
    low_arr = low_arr[valid_mask]
    close_arr = close_arr[valid_mask]
    vol_arr = vol_arr[valid_mask]

    min_p = float(np.min(low_arr))
    max_p = float(np.max(high_arr))
    current_close = float(close_arr[-1])

    # 扁平区间防除以零
    if max_p <= min_p:
        return round(current_close, 2)

    num_bins = 25
    bw = (max_p - min_p) / float(num_bins)
    if bw <= 0:
        return round(current_close, 2)

    # Typical Price
    tp_arr = (high_arr + low_arr + close_arr) / 3.0

    # 零成交量回退 (均匀分布)
    if np.sum(vol_arr) <= 0.0:
        vol_arr = np.ones_like(tp_arr)

    bin_volumes = np.zeros(num_bins, dtype=float)
    for p_val, v_val in zip(tp_arr, vol_arr):
        idx = int((p_val - min_p) / bw)
        idx = min(num_bins - 1, max(0, idx))
        bin_volumes[idx] += v_val

    max_v = np.max(bin_volumes)
    winning_indices = np.where(bin_volumes == max_v)[0]
    if len(winning_indices) == 1:
        best_idx = winning_indices[0]
    else:
        # 平局时取中点距离当前价最近的箱体
        midpoints = min_p + (winning_indices + 0.5) * bw
        diffs = np.abs(midpoints - current_close)
        best_idx = winning_indices[np.argmin(diffs)]

    poc = min_p + (best_idx + 0.5) * bw
    return round(float(poc), 2)


def evaluate_rsi_state(
    rsi_4h: float,
    rsi_1d: float,
    price: float,
    poc: float,
    is_cooling: bool = False,
    price_gt_vwap: bool = False,
    prev_is_right_turn: bool = False
) -> str:
    """
    RSI 状态机与 V3.2 暴跌冷却/VWAP 企稳融合判定:
    - [🔥 双周期见底]: (rsi_4h <= 32.0 and rsi_1d <= 40.0) 或 (|price - poc|/poc <= 0.015 and rsi_4h <= 35.0)
      * 若处于 3 日暴跌冷却期 (is_cooling):
        - 若已收复 VWAP (price_gt_vwap) 或前日已确认右侧反转 (prev_is_right_turn): [🔥 双周期见底·✅右侧企稳]
        - 否则: [🔥 双周期见底·⚠️冷却观察]
      * 否则: [🔥 双周期见底]
    - [🛑 双周期触顶]: rsi_4h >= 68.0 and rsi_1d >= 65.0
    - [⚡ 4H超卖]: rsi_4h <= 32.0 and rsi_1d > 40.0
    - 默认: [安全持股]
    """
    try:
        rsi_4h = float(rsi_4h)
        rsi_1d = float(rsi_1d)
        price = float(price)
        poc = float(poc) if poc is not None else 0.0
    except (TypeError, ValueError):
        return "[安全持股]"

    if math.isnan(rsi_4h) or math.isnan(rsi_1d):
        return "[安全持股]"

    # 密集区重合判断
    poc_close = False
    if poc > 0 and price > 0:
        poc_close = (abs(price - poc) / poc) <= 0.015

    is_bottom = (rsi_4h <= 32.0 and rsi_1d <= 40.0) or (poc_close and rsi_4h <= 35.0)

    if is_bottom:
        if is_cooling:
            if price_gt_vwap or prev_is_right_turn:
                return "[🔥 双周期见底·✅右侧企稳]"
            else:
                return "[🔥 双周期见底·⚠️冷却观察]"
        else:
            return "[🔥 双周期见底]"

    if rsi_4h >= 68.0 and rsi_1d >= 65.0:
        return "[🛑 双周期触顶]"

    if rsi_4h <= 32.0 and rsi_1d > 40.0:
        return "[⚡ 4H超卖]"

    return "[安全持股]"


def compute_factor_y(components_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    宏观防御因子 Y:
    - 由 SCHD, KO, GLD 三大防御支柱均值合成
    - 缺数容错: 若 1 或 2 只缺失，取可用标的均值；若全缺，回退中性基准 50.0
    - 合成价格、POC、1H撑压及状态判定
    - 严格满足 1H撑 < 现价 < 1H压 边界保护
    """
    res: Dict[str, Any] = {
        "price": 0.0,
        "rsi_4h": 50.0,
        "rsi_1d": 50.0,
        "poc": 0.0,
        "support": 0.0,
        "resistance": 0.0,
        "status": "[安全持股]",
        "display_name": "Y"
    }
    if not components_data or not isinstance(components_data, dict):
        return res

    targets = ("SCHD", "KO", "GLD")
    metrics = ["rsi_4h", "rsi_1d", "price", "poc", "support", "resistance"]

    for m in metrics:
        vals = []
        for sym in targets:
            if sym in components_data and isinstance(components_data[sym], dict):
                v = components_data[sym].get(m)
                if v is not None and not (isinstance(v, float) and math.isnan(v)):
                    try:
                        vals.append(float(v))
                    except (ValueError, TypeError):
                        pass
        if vals:
            res[m] = round(float(np.mean(vals)), 2)

    # 边界保护: support < price < resistance
    p = res["price"]
    s = res["support"]
    r = res["resistance"]
    if p > 0:
        if s >= p:
            s = round(p * 0.985, 2)
        if r <= p:
            r = round(p * 1.015, 2)
        res["support"] = s
        res["resistance"] = r

    # 状态判定
    res["status"] = evaluate_rsi_state(
        rsi_4h=res.get("rsi_4h", 50.0),
        rsi_1d=res.get("rsi_1d", 50.0),
        price=res.get("price", 0.0),
        poc=res.get("poc", 0.0)
    )

    return res


def evaluate_cross_asset_resonance(market_analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    跨资产共振与高低切判定:
    1. 3X 镜像多空共振:
       - 半导体对: SOXL (见底/超卖) + SOXS (触顶/超买) -> ⚡【SOXL×SOXS 底部共振】
       - 半导体对: SOXL (触顶/超买) + SOXS (见底/超卖) -> 🛑【SOXL×SOXS 顶部共振】
       - 纳指对: TQQQ (见底/超卖) + SQQQ (触顶/超买) -> ⚡【TQQQ×SQQQ 底部共振】
       - 纳指对: TQQQ (触顶/超买) + SQQQ (见底/超卖) -> 🛑【TQQQ×SQQQ 顶部共振】
    2. 宏观防御因子 Y x 芯片 (SOXX) 背离 (左侧抄底警报):
       - Factor Y 4H_RSI - SOXX 4H_RSI >= 25.0 且 SOXX 4H_RSI <= 32.0
       - -> 🎯【因子Y×芯片·左侧背离】
    3. 1D+4H 双周期极值标的归集:
       - 收集所有处于 [🔥 双周期见底] 或 [🛑 双周期触顶] 的标的
    """
    tickers = market_analysis.get("tickers", {})
    factor_y = market_analysis.get("factor_y")
    if factor_y is None or not isinstance(factor_y, dict):
        factor_y = compute_factor_y(tickers)
        market_analysis["factor_y"] = factor_y

    resonance_alerts: List[str] = []
    divergence_alerts: List[str] = []
    dual_extremes: List[str] = []

    # Helper to check bottom/oversold
    def is_oversold_or_bottom(t_data: dict) -> bool:
        st = t_data.get("status", "")
        r4 = float(t_data.get("rsi_4h", 50.0))
        return ("见底" in st) or ("超卖" in st) or (r4 <= 35.0)

    # Helper to check top/overbought
    def is_overbought_or_top(t_data: dict) -> bool:
        st = t_data.get("status", "")
        r4 = float(t_data.get("rsi_4h", 50.0))
        return ("触顶" in st) or (r4 >= 65.0)

    # 1. 3X 镜像: SOXL x SOXS
    soxl = tickers.get("SOXL")
    soxs = tickers.get("SOXS")
    if soxl and soxs:
        if is_oversold_or_bottom(soxl) and is_overbought_or_top(soxs):
            poc_val = soxl.get("poc")
            poc_text = f"(技术性成交密集区:${poc_val:.2f})" if poc_val else ""
            resonance_alerts.append(f"🔥 【SOXL×SOXS 底部共振】SOXL超卖{poc_text} + SOXS触顶！")
        elif is_overbought_or_top(soxl) and is_oversold_or_bottom(soxs):
            resonance_alerts.append("🛑 【SOXL×SOXS 顶部共振】SOXL触顶超买 + SOXS见底超卖，防范半导体高位回撤！")

    # 1. 3X 镜像: TQQQ x SQQQ
    tqqq = tickers.get("TQQQ")
    sqqq = tickers.get("SQQQ")
    if tqqq and sqqq:
        if is_oversold_or_bottom(tqqq) and is_overbought_or_top(sqqq):
            poc_val = tqqq.get("poc")
            poc_text = f"(技术性成交密集区:${poc_val:.2f})" if poc_val else ""
            resonance_alerts.append(f"🔥 【TQQQ×SQQQ 底部共振】TQQQ超卖{poc_text} + SQQQ触顶！")
        elif is_overbought_or_top(tqqq) and is_oversold_or_bottom(sqqq):
            resonance_alerts.append("🛑 【TQQQ×SQQQ 顶部共振】TQQQ触顶超买 + SQQQ见底超卖，防范纳指高位回撤！")

    # 2. 宏观因子 Y x 芯片 (SOXX) 背离
    soxx = tickers.get("SOXX")
    if soxx:
        soxx_rsi = float(soxx.get("rsi_4h", 50.0))
        fy_rsi = float(factor_y.get("rsi_4h", 50.0))
        if (fy_rsi - soxx_rsi >= 25.0) and (soxx_rsi <= 32.0):
            divergence_alerts.append(
                f"🟡 【因子Y×芯片·左侧背离】因子Y高位拥挤，芯片超卖见底，留意高低切反抽！\n"
                f"📊 芯片 4H_RSI:{soxx_rsi:.1f} vs 因子Y 4H_RSI:{fy_rsi:.1f}"
            )

    # 3. 1D+4H 双周期极值标的归集
    for sym, t_data in tickers.items():
        st = t_data.get("status", "")
        disp = t_data.get("display_name", "BTC" if sym == "BTC-USD" else sym)
        if "见底" in st:
            dual_extremes.append(f"{disp}(见底)")
        elif "触顶" in st:
            dual_extremes.append(f"{disp}(触顶)")

    market_analysis["resonance_alerts"] = resonance_alerts
    market_analysis["divergence_alerts"] = divergence_alerts
    market_analysis["dual_extremes"] = dual_extremes

    return market_analysis


# ==============================================================================
# Tier 3: Discord Embed Formatting, Dispatcher & Linkage (Implemented in M3)
# ==============================================================================

def format_ticker_block(ticker: str, t_data: Dict[str, Any]) -> str:
    """
    Format single ticker entry with native Discord markdown in accordance with reference frames:
    - Zero ``` or ```text codeblocks
    - Native bold tags and clean spacing
    - BTC-USD formatted as BTC with integer thousands comma separator (e.g. $75,984)
    """
    sym = t_data.get("display_name")
    if not sym:
        sym = "BTC" if ticker in ("BTC", "BTC-USD") else ticker

    price = float(t_data.get("price", 0.0) or 0.0)
    poc = float(t_data.get("poc", 0.0) or 0.0)
    sup = float(t_data.get("support", 0.0) or 0.0)
    res = float(t_data.get("resistance", 0.0) or 0.0)
    status_tag = str(t_data.get("status", "[安全持股]") or "[安全持股]").strip()
    status_fmt = status_tag if (status_tag.startswith("[") and status_tag.endswith("]")) else f"[{status_tag}]"

    if ticker in ("BTC", "BTC-USD") or sym == "BTC":
        price_str = f"${int(round(price)):,}"
        poc_str = f"${int(round(poc)):,}"
        sup_str = f"${int(round(sup)):,}"
        res_str = f"${int(round(res)):,}"
    else:
        price_str = f"${price:.2f}"
        poc_str = f"${poc:.2f}"
        sup_str = f"${sup:.2f}"
        res_str = f"${res:.2f}"

    return (
        f"**{sym}**    现: **{price_str}** {status_fmt}\n"
        f"技术性成交密集区: {poc_str}\n"
        f"1H撑:{sup_str}  |  1H压:{res_str}\n"
        f"-------------------------\n"
    )


def build_message_1_embed(market_analysis: Dict[str, Any]) -> Optional[dict]:
    """
    Message 1: Quant Sentinel Resonance Strong Alert (量化哨兵 共振强警报)
    - Left border color: Vivid Green (#2ECC71 / decimal 3066993)
    - Subtitle / Title: 收线与隔夜结构
    - 3X Leverage Core Monitor Block (👑 【置顶 3X 多空核心监控】)
    - Resonance and Divergence Alert Block
    - 1D+4H Dual-Cycle Extremes
    - Footer: 菲欧尼之心 (Heart of Fioni) · 盘前/盘中/盘尾定时引擎
    """
    tickers = market_analysis.get("tickers", {})
    resonance_alerts = market_analysis.get("resonance_alerts", [])
    divergence_alerts = market_analysis.get("divergence_alerts", [])
    dual_extremes = market_analysis.get("dual_extremes", [])

    has_alerts = bool(resonance_alerts or divergence_alerts or dual_extremes)
    # Check 3X tickers for active signals
    for sym in ("SOXL", "SOXS", "TQQQ", "SQQQ"):
        st = tickers.get(sym, {}).get("status", "")
        if "见底" in st or "触顶" in st or "超卖" in st:
            has_alerts = True
            break

    if not has_alerts:
        return None

    lines = []
    lines.append("-------------------------")
    lines.append("👑 【置顶 3X 多空核心监控】")
    for sym in ("SOXL", "SOXS", "TQQQ", "SQQQ"):
        t_data = tickers.get(sym, {})
        p = float(t_data.get("price", 0.0) or 0.0)
        poc = float(t_data.get("poc", 0.0) or 0.0)
        s = float(t_data.get("support", 0.0) or 0.0)
        r = float(t_data.get("resistance", 0.0) or 0.0)
        st = str(t_data.get("status", "[安全持股]") or "[安全持股]").strip()
        status_fmt = st if (st.startswith("[") and st.endswith("]")) else f"[{st}]"
        lines.append(f"· **{sym}**: 现 **${p:.2f}** | 技术性成交密集区 ${poc:.2f} | 1H ${s:.2f}~${r:.2f} {status_fmt}")

    if divergence_alerts or resonance_alerts:
        lines.append("-------------------------")
        for da in divergence_alerts:
            lines.append(da)
        for ra in resonance_alerts:
            lines.append(ra)

    if dual_extremes:
        lines.append("-------------------------")
        lines.append(f"🔥 1D+4H双周期极值: {', '.join(dual_extremes)}")

    description = "\n".join(lines)

    slot_title = market_analysis.get("slot_title") or "收线与隔夜结构"
    embed = {
        "title": slot_title,
        "color": 3066993,  # Vivid Green #2ECC71
        "description": description,
        "footer": {
            "text": "菲欧尼之心 (Heart of Fioni) · 盘前/盘中/盘尾定时引擎"
        }
    }
    return embed


def build_discord_payloads(market_analysis: Dict[str, Any]) -> Tuple[Optional[dict], dict, dict]:
    """
    构建符合 Discord 原生 Markdown 富文本卡片 (严格拒绝灰色代码块):
    - 返回 (payload_1, payload_2a, payload_2b)
    - 严格满足 6,000 字符限制 (各 <= 5,500) 与 10 embeds 限制:
      * payload_1: 强警报 (1 embed, < 2,000 字符，可为 None)
      * payload_2a: 梯队 1~3 (5 embeds, ~3,200 字符)
      * payload_2b: 梯队 4~5 (4 embeds, ~2,800 字符)
    """
    tickers = market_analysis.get("tickers", {})
    factor_y = market_analysis.get("factor_y")
    if not factor_y or not isinstance(factor_y, dict):
        factor_y = compute_factor_y(tickers)

    # 1. Message 1 (Strong Alert)
    embed_msg1 = build_message_1_embed(market_analysis)
    payload_1 = {
        "username": "菲欧尼之心·量化哨兵",
        "embeds": [embed_msg1]
    } if embed_msg1 else None

    # 2. Message 2 (5 Tiers Sector Inspection - 9 Paginated Embeds)
    # Tier 1 (1 embed): Gold (#F1C40F / 15844367)
    t1_syms = SENTINEL_UNIVERSE_TIERS["TIER_1_3X"]
    desc_t1 = "".join(format_ticker_block(s, tickers.get(s, {})) for s in t1_syms)
    embed_t1 = {
        "title": "👑 【置顶】3X多空杠杆决战台",
        "color": 15844367,
        "description": desc_t1
    }

    # Tier 2 (2 embeds): Purple (#9B59B6 / 10181046)
    t2_syms = SENTINEL_UNIVERSE_TIERS["TIER_2_CORE"]
    desc_t2_1 = "".join(format_ticker_block(s, tickers.get(s, {})) for s in t2_syms[:6])
    embed_t2_1 = {
        "title": "⚡ 【第二梯队】核心高弹性与重点池 (1/2)",
        "color": 10181046,
        "description": desc_t2_1
    }
    desc_t2_2 = "".join(format_ticker_block(s, tickers.get(s, {})) for s in t2_syms[6:])
    embed_t2_2 = {
        "title": "⚡ 【第二梯队】核心高弹性与重点池 (2/2)",
        "color": 10181046,
        "description": desc_t2_2
    }

    # Tier 3 (2 embeds): Cyan Blue (#3498DB / 3447003)
    t3_syms = SENTINEL_UNIVERSE_TIERS["TIER_3_CHIPS"]
    desc_t3_1 = "".join(format_ticker_block(s, tickers.get(s, {})) for s in t3_syms[:7])
    embed_t3_1 = {
        "title": "🖥️ 【第三梯队】芯片半导体与存储硬件 (1/2)",
        "color": 3447003,
        "description": desc_t3_1
    }
    desc_t3_2 = "".join(format_ticker_block(s, tickers.get(s, {})) for s in t3_syms[7:])
    embed_t3_2 = {
        "title": "🖥️ 【第三梯队】芯片半导体与存储硬件 (2/2)",
        "color": 3447003,
        "description": desc_t3_2
    }

    # Tier 4 (2 embeds): Blurple (#5865F2 / 5793266)
    t4_syms = SENTINEL_UNIVERSE_TIERS["TIER_4_TECH"]
    desc_t4_1 = "".join(format_ticker_block(s, tickers.get(s, {})) for s in t4_syms[:7])
    embed_t4_1 = {
        "title": "🌐 【第四梯队】科技巨头、AI云与加密 (1/2)",
        "color": 5793266,
        "description": desc_t4_1
    }
    desc_t4_2 = "".join(format_ticker_block(s, tickers.get(s, {})) for s in t4_syms[7:])
    embed_t4_2 = {
        "title": "🌐 【第四梯队】科技巨头、AI云与加密 (2/2)",
        "color": 5793266,
        "description": desc_t4_2
    }

    # Tier 5 (2 embeds): Emerald (#2ECC71 / 3066993) with synthetic Y at top
    t5_items = ["Y"] + SENTINEL_UNIVERSE_TIERS["TIER_5_DEFENSE"]
    def _get_t5_data(item: str) -> Dict[str, Any]:
        if item == "Y":
            return factor_y if factor_y else compute_factor_y(tickers)
        return tickers.get(item, {})

    desc_t5_1 = "".join(format_ticker_block(s, _get_t5_data(s)) for s in t5_items[:5])
    embed_t5_1 = {
        "title": "🛡️ 【第五梯队】神秘因子Y与宏观防御 (1/2)",
        "color": 3066993,
        "description": desc_t5_1
    }
    desc_t5_2 = "".join(format_ticker_block(s, _get_t5_data(s)) for s in t5_items[5:])
    embed_t5_2 = {
        "title": "🛡️ 【第五梯队】神秘因子Y与宏观防御 (2/2)",
        "color": 3066993,
        "description": desc_t5_2
    }

    embeds_2a = [embed_t1, embed_t2_1, embed_t2_2, embed_t3_1, embed_t3_2]
    embeds_2b = [embed_t4_1, embed_t4_2, embed_t5_1, embed_t5_2]

    payload_2a = {
        "username": "菲欧尼之心·板块巡检",
        "embeds": embeds_2a
    }
    payload_2b = {
        "username": "菲欧尼之心·板块巡检",
        "embeds": embeds_2b
    }

    return payload_1, payload_2a, payload_2b


def _post_with_retry(webhook_url: str, payload: dict, max_retries: int = 2) -> bool:
    """
    发送 Discord Webhook 请求，内置 HTTP 429 速率限制退避与重试机制。
    """
    import requests
    if not webhook_url:
        print("[!] ⚠️ Discord Webhook URL not provided.")
        return False

    headers = {"Content-Type": "application/json"}
    for attempt in range(max_retries):
        try:
            resp = requests.post(webhook_url, json=payload, headers=headers, timeout=10)
            if resp.status_code == 429:
                retry_after = 2.0
                try:
                    retry_after = float(resp.json().get("retry_after", 2.0))
                except Exception:
                    pass
                print(f"[!] ⚠️ Discord 429 Rate Limited. Sleeping {retry_after:.2f}s before retry...")
                time.sleep(retry_after)
                continue
            elif resp.status_code in (200, 204):
                return True
            else:
                print(f"[!] ⚠️ Discord webhook HTTP {resp.status_code}: {resp.text}")
                return False
        except requests.exceptions.RequestException as e:
            print(f"[!] ⚠️ Discord request exception (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(0.5)
            else:
                return False
        except Exception as e:
            print(f"[!] ⚠️ Unexpected exception during Discord post: {e}")
            return False
    return False


def dispatch_discord_messages(
    webhook_url: str,
    payload_1: Optional[dict] = None,
    payload_2a: Optional[Union[dict, List[dict]]] = None,
    payload_2b: Optional[Union[dict, List[dict]]] = None
) -> bool:
    """
    分发 Discord 消息:
    - 消息 1: 量化哨兵共振强警报 (若存在) -> 休眠 1.0s 防 429
    - 消息 2 Part A: 板块巡检 (梯队 1~3, 5 embeds) -> 休眠 1.0s 防 429
    - 消息 2 Part B: 板块巡检 (梯队 4~5, 4 embeds)
    """
    if not webhook_url:
        print("[!] ⚠️ DISCORD_WEBHOOK_URL is empty, skipping dispatch.")
        return False

    success = True

    # 1. Message 1 (Strong Alert)
    if payload_1:
        if isinstance(payload_1, list):
            p1_dict = {"username": "菲欧尼之心·量化哨兵", "embeds": payload_1}
        elif isinstance(payload_1, dict):
            p1_dict = dict(payload_1)
            p1_dict.setdefault("username", "菲欧尼之心·量化哨兵")
        else:
            p1_dict = None

        if p1_dict and p1_dict.get("embeds"):
            s1 = _post_with_retry(webhook_url, p1_dict)
            success = success and s1
            time.sleep(1.0)

    # 2. Message 2 Part A (Tiers 1-3)
    if payload_2a:
        if isinstance(payload_2a, list):
            p2a_dict = {"username": "菲欧尼之心·板块巡检", "embeds": payload_2a}
        elif isinstance(payload_2a, dict):
            p2a_dict = dict(payload_2a)
            p2a_dict.setdefault("username", "菲欧尼之心·板块巡检")
        else:
            p2a_dict = None

        if p2a_dict and p2a_dict.get("embeds"):
            s2a = _post_with_retry(webhook_url, p2a_dict)
            success = success and s2a
            time.sleep(1.0)

    # 3. Message 2 Part B (Tiers 4-5)
    if payload_2b:
        if isinstance(payload_2b, list):
            p2b_dict = {"username": "菲欧尼之心·板块巡检", "embeds": payload_2b}
        elif isinstance(payload_2b, dict):
            p2b_dict = dict(payload_2b)
            p2b_dict.setdefault("username", "菲欧尼之心·板块巡检")
        else:
            p2b_dict = None

        if p2b_dict and p2b_dict.get("embeds"):
            s2b = _post_with_retry(webhook_url, p2b_dict)
            success = success and s2b

    return success


def run_sentinel_scan(
    dry_run: bool = False,
    push_discord: bool = True,
    market_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    完整哨兵巡检与推送流程:
    1. 抓取 / 获取 51 只标的 1H 与 1D 行情数据
    2. 计算 4H K线、POC密集区、1H撑压边界保护、4H/1D RSI 与状态机
    3. 合成神秘因子 Y 与跨资产镜像共振 / 背离判定
    4. 构建 Discord 原生 Markdown 富文本卡片 (Payload 1, Payload 2A, Payload 2B)
    5. 若 push_discord 且非 dry_run，分发至 Discord Webhook 频道
    6. 返回 market_analysis 汇总字典
    """
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", DISCORD_WEBHOOK_URL).strip()

    if market_data is None:
        try:
            market_data = fetch_sentinel_market_data(ALL_SENTINEL_TICKERS)
        except Exception as e:
            print(f"[!] ⚠️ Error fetching market data: {e}")
            market_data = {}

    dl_1h = market_data.get("dl_1h") if isinstance(market_data, dict) else None
    dl_1d = market_data.get("dl_1d") if isinstance(market_data, dict) else None

    tickers_res: Dict[str, Any] = {}
    for sym in ALL_SENTINEL_TICKERS:
        df_1h = pd.DataFrame()
        df_1d = pd.DataFrame()

        if dl_1h is not None and not dl_1h.empty:
            try:
                if isinstance(dl_1h.columns, pd.MultiIndex):
                    if sym in dl_1h.columns.levels[0]:
                        df_1h = dl_1h[sym].dropna(how="all")
                elif "Close" in dl_1h.columns:
                    df_1h = dl_1h.dropna(how="all")
            except Exception:
                df_1h = pd.DataFrame()

        if dl_1d is not None and not dl_1d.empty:
            try:
                if isinstance(dl_1d.columns, pd.MultiIndex):
                    if sym in dl_1d.columns.levels[0]:
                        df_1d = dl_1d[sym].dropna(how="all")
                elif "Close" in dl_1d.columns:
                    df_1d = dl_1d.dropna(how="all")
            except Exception:
                df_1d = pd.DataFrame()

        if not df_1h.empty and len(df_1h) >= 2:
            try:
                active_price = float(df_1h["Close"].dropna().iloc[-1])
            except Exception:
                active_price = 100.0
            df_4h = aggregate_1h_to_4h_df(df_1h, ticker=sym)
            poc = compute_volume_profile_poc(df_1h)
            sup, res, atr = compute_and_protect_1h_sr(df_1h, active_price, poc)
        else:
            active_price = 100.0
            df_4h = pd.DataFrame()
            poc = 100.0
            sup, res, atr = compute_and_protect_1h_sr(pd.DataFrame(), active_price, poc)

        if not df_4h.empty and len(df_4h) >= 14:
            rsi_4h_s = compute_series_rsi(df_4h["Close"], 14)
            rsi_4h = float(rsi_4h_s.dropna().iloc[-1]) if not rsi_4h_s.dropna().empty else 50.0
        else:
            rsi_4h = 50.0

        if not df_1d.empty and len(df_1d) >= 14:
            rsi_1d_s = compute_series_rsi(df_1d["Close"], 14)
            rsi_1d = float(rsi_1d_s.dropna().iloc[-1]) if not rsi_1d_s.dropna().empty else 50.0
        else:
            rsi_1d = 50.0

        status = evaluate_rsi_state(rsi_4h, rsi_1d, active_price, poc)
        disp = "BTC" if sym in ("BTC", "BTC-USD") else sym

        tickers_res[sym] = {
            "price": active_price,
            "rsi_4h": rsi_4h,
            "rsi_1d": rsi_1d,
            "poc": poc,
            "support": sup,
            "resistance": res,
            "status": status,
            "display_name": disp
        }

    factor_y = compute_factor_y(tickers_res)

    market_analysis = {
        "timestamp": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "session": "REGULAR",
        "tickers": tickers_res,
        "factor_y": factor_y,
        "resonance_alerts": [],
        "divergence_alerts": [],
        "dual_extremes": []
    }
    market_analysis = evaluate_cross_asset_resonance(market_analysis)

    payload_1, payload_2a, payload_2b = build_discord_payloads(market_analysis)

    if dry_run:
        print("\n" + "=" * 60)
        print(" [DRY RUN] Discord Sentinel Payloads Generated")
        print("=" * 60)
        p1_chars = sum(len(e.get("description", "")) for e in payload_1.get("embeds", [])) if payload_1 else 0
        p2a_chars = sum(len(e.get("description", "")) for e in payload_2a.get("embeds", []))
        p2b_chars = sum(len(e.get("description", "")) for e in payload_2b.get("embeds", []))
        print(f"  -> Payload 1 (Strong Alert): {'Present' if payload_1 else 'None'} ({p1_chars} chars)")
        print(f"  -> Payload 2A (Tiers 1-3):   {len(payload_2a.get('embeds', []))} embeds ({p2a_chars} chars)")
        print(f"  -> Payload 2B (Tiers 4-5):   {len(payload_2b.get('embeds', []))} embeds ({p2b_chars} chars)")
        print("=" * 60 + "\n")
    elif push_discord:
        if webhook_url:
            print(f"[Discord Sentinel] Dispatching payloads to webhook...")
            dispatch_discord_messages(webhook_url, payload_1, payload_2a, payload_2b)
            print(f"✅ [Discord Sentinel] Webhook dispatch completed successfully.")
        else:
            print("[!] ⚠️ DISCORD_WEBHOOK_URL is not configured. Skipped dispatch.")

    return market_analysis


# -----------------------------------------------------------------------------
# 每日 4 次精准时段定义 (开盘前10分、开盘后/盘中、尾盘后、夜盘)
# -----------------------------------------------------------------------------
SENTINEL_SCHEDULE_SLOTS = [
    {
        "id": "pre_market_10m",
        "name": "🌅 【美股开盘前十分钟·攻防战报】",
        "start_time": "09:20",
        "end_time": "09:30",
        "desc": "开盘前 10 分钟：梳理隔夜结构与 3X 多空关键防守位，准备开盘冲锋"
    },
    {
        "id": "post_open_intraday",
        "name": "⚔️ 【美股开盘后·盘中多空对决与共振巡检】",
        "start_time": "10:30",
        "end_time": "11:30",
        "desc": "开盘后 1 小时：早盘多空拔河分晓，排查 3X 底部共振与芯片/防御背离"
    },
    {
        "id": "post_close",
        "name": "🏁 【美股尾盘后·全天收盘决战复盘】",
        "start_time": "16:05",
        "end_time": "16:30",
        "desc": "尾盘收官后：锁定全天日线与 4H 收盘形态，核算密集区 POC 与极值榜"
    },
    {
        "id": "overnight",
        "name": "🌌 【美股夜盘·隔夜流动性与全球资产巡检】",
        "start_time": "20:30",
        "end_time": "21:30",
        "desc": "夜盘开启后：监控加密货币、大宗黄金与美股夜盘跨时区流动性"
    }
]


def get_current_et_datetime() -> datetime:
    """获取当前准确的美东时间 (America/New_York)"""
    try:
        import zoneinfo
        ny_tz = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        from datetime import timezone, timedelta
        ny_tz = timezone(timedelta(hours=-5))
    return datetime.now(ny_tz)


def check_sentinel_schedule_window(now_dt: Optional[datetime] = None, force: bool = False) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    检查当前美东时间是否匹配 4 个指定推送窗口之一，且当日该窗口尚未推送过:
    1. 🌅 开盘前十分钟 (09:20 ~ 09:30 ET)
    2. ⚔️ 开盘后/盘中 (10:30 ~ 11:30 ET)
    3. 🏁 尾盘后复盘 (16:05 ~ 16:30 ET)
    4. 🌌 美股夜盘 (20:30 ~ 21:30 ET)
    
    返回: (should_push, slot_id, slot_name)
    若 force=True (如命令行 --once)，则直接允许推送。
    """
    if force:
        return True, "manual_force", "⚡ 【手动即时量化巡检】"

    now_et = now_dt or get_current_et_datetime()
    weekday = now_et.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    hm = now_et.strftime("%H:%M")
    date_str = now_et.strftime("%Y-%m-%d")

    # 周末过滤 (周六全天休市；周日白天休市，周日 20:00 后夜盘开启除外)
    if weekday == 5 or (weekday == 6 and hm < "20:00"):
        return False, None, None

    # 周五 20:00 之后夜盘休市
    if weekday == 4 and hm >= "20:00":
        return False, None, None

    active_slot = None
    for slot in SENTINEL_SCHEDULE_SLOTS:
        if slot["start_time"] <= hm < slot["end_time"]:
            active_slot = slot
            break

    if not active_slot:
        return False, None, None

    slot_id = active_slot["id"]
    slot_name = active_slot["name"]

    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".discord_sentinel_state.json")
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}

    pushed_map = state.get("last_pushed_slots", {})
    today_pushed = pushed_map.get(date_str, [])

    if slot_id in today_pushed:
        # 当日该时段已成功推送过，严格去重避免刷屏
        return False, slot_id, slot_name

    return True, slot_id, slot_name


def mark_sentinel_slot_pushed(slot_id: str, slot_name: str, now_dt: Optional[datetime] = None):
    """记录成功推送的时段至 .discord_sentinel_state.json"""
    now_et = now_dt or get_current_et_datetime()
    date_str = now_et.strftime("%Y-%m-%d")
    time_str = now_et.strftime("%Y-%m-%d %H:%M:%S")

    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".discord_sentinel_state.json")
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}

    if "last_pushed_slots" not in state:
        state["last_pushed_slots"] = {}
    if date_str not in state["last_pushed_slots"]:
        state["last_pushed_slots"][date_str] = []

    if slot_id not in state["last_pushed_slots"][date_str]:
        state["last_pushed_slots"][date_str].append(slot_id)

    if "history" not in state:
        state["history"] = []
    state["history"].append({
        "slot_id": slot_id,
        "slot_name": slot_name,
        "et_time": time_str
    })
    state["history"] = state["history"][-100:]

    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[!] Failed to write sentinel state: {e}")


def run_sentinel_cycle_safe(macro_data=None, stocks_sorted=None, weather_info=None, now_str=None, scheduled_only: bool = False, force: bool = False):
    """
    integrated_market_radar.py 联动的安全异步调用入口:
    - 每日固定 4 次准时触发: 开盘前十分钟 (09:20 ET)、开盘后/盘中 (10:30 ET)、尾盘后 (16:05 ET)、夜盘 (20:30 ET)
    - 当日每个窗口仅推一次，其余 60s 轮询周期 0 网络开销静默跳过
    - 捕获所有网络与逻辑异常，安全隔离，绝不影响主雷达循环
    """
    try:
        slot_id = "manual"
        slot_name = "⚡ 【即时量化哨兵巡检】"

        if scheduled_only and not force:
            should_push, s_id, s_name = check_sentinel_schedule_window()
            if not should_push:
                return  # 未到 4 个时段或当日该时段已推送，静默返回
            slot_id = s_id
            slot_name = s_name

        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", DISCORD_WEBHOOK_URL).strip()
        if not webhook_url:
            print("[Discord Sentinel] DISCORD_WEBHOOK_URL not configured, skipping cycle.")
            return

        print(f"[*] 🚀 [Discord Sentinel] 触发推送: {slot_name}...")

        if macro_data is not None and stocks_sorted is not None:
            items_map: Dict[str, Any] = {}
            if isinstance(macro_data, dict):
                for k, v in macro_data.items():
                    if isinstance(v, dict):
                        items_map[k] = v
            if isinstance(stocks_sorted, (list, tuple)):
                for s in stocks_sorted:
                    if isinstance(s, dict) and "ticker" in s:
                        items_map[s["ticker"]] = s

            tickers_res: Dict[str, Any] = {}
            for sym in ALL_SENTINEL_TICKERS:
                item = items_map.get(sym, {})
                active_p = float(item.get("active_price") or item.get("last") or 100.0)
                poc = float(item.get("fib_poc") or item.get("poc") or active_p)
                s = float(item.get("support") or item.get("fib_low") or round(active_p * 0.98, 2))
                r = float(item.get("resistance") or item.get("fib_high") or round(active_p * 1.02, 2))
                r4 = float(item.get("rsi_4h") or item.get("rsi") or 50.0)
                r1d = float(item.get("rsi") or 50.0)
                st = item.get("status") or evaluate_rsi_state(r4, r1d, active_p, poc)
                disp = "BTC" if sym in ("BTC", "BTC-USD") else sym

                tickers_res[sym] = {
                    "price": active_p,
                    "rsi_4h": r4,
                    "rsi_1d": r1d,
                    "poc": poc,
                    "support": s,
                    "resistance": r,
                    "status": st,
                    "display_name": disp
                }

            factor_y = compute_factor_y(tickers_res)
            analysis = {
                "timestamp": now_str or pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                "session": "REGULAR",
                "slot_title": slot_name,
                "tickers": tickers_res,
                "factor_y": factor_y,
                "resonance_alerts": [],
                "divergence_alerts": [],
                "dual_extremes": []
            }
            analysis = evaluate_cross_asset_resonance(analysis)
            payload_1, payload_2a, payload_2b = build_discord_payloads(analysis)
            dispatch_discord_messages(webhook_url, payload_1, payload_2a, payload_2b)
        else:
            run_sentinel_scan(dry_run=False, push_discord=True)

        if scheduled_only and slot_id and slot_id != "manual":
            mark_sentinel_slot_pushed(slot_id, slot_name)
            print(f"[+] ✅ [Discord Sentinel] {slot_name} 成功推送完毕并记录状态锁。")
    except Exception as e:
        print(f"[!] ⚠️ Discord Sentinel cycle isolated exception: {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="US Market Radar - Discord Sentinel Engine")
    parser.add_argument("--once", action="store_true", help="Execute single-pass scan and dispatch to Discord Webhook immediately")
    parser.add_argument("--schedule", action="store_true", help="Run in continuous schedule mode aligned with US market hours (4 times a day)")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds when in schedule daemon mode (default: 30s)")
    parser.add_argument("--dry-run", action="store_true", help="Build embeds and print character counts without dispatching")
    parser.add_argument("--force", action="store_true", help="Force dispatch ignoring daily 4-times schedule window")
    args = parser.parse_args()

    if args.dry_run:
        run_sentinel_scan(dry_run=True, push_discord=False)
    elif args.schedule:
        print("🚀 [Discord Sentinel] 启动每日 4 次美东定时巡检守护引擎...")
        print("   1. 🌅 开盘前十分钟: 09:20 ~ 09:30 ET")
        print("   2. ⚔️ 开盘后 / 盘中: 10:30 ~ 11:30 ET")
        print("   3. 🏁 尾盘后复盘:   16:05 ~ 16:30 ET")
        print("   4. 🌌 美股夜盘巡检: 20:30 ~ 21:30 ET")
        while True:
            try:
                run_sentinel_cycle_safe(scheduled_only=True, force=args.force)
                time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\n[Discord Sentinel] Schedule loop interrupted by user. Exiting.")
                break
            except Exception as e:
                print(f"[!] ⚠️ Error in sentinel schedule cycle: {e}")
                time.sleep(10)
    else:
        # Default to single pass scan (--once)
        run_sentinel_scan(dry_run=False, push_discord=True)


if __name__ == "__main__":
    main()

