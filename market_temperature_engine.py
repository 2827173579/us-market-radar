# -*- coding: utf-8 -*-
"""
美股量化雷达 V5.0 — 股指期货先导哨 (NQ=F / ES=F) & 大盘/日内泡沫与抄底四维温度计引擎
================================================================================
低耦合独立模块（与 vix_regime_engine.py 同构）：
1. 独立抓取并本地缓存 (TTL 180s) 纳指100期货 (NQ=F) 与标普500期货 (ES=F) 5m/15m 及 60d 日线，
   计算最新价、相对前结算涨跌幅、24H 高低点、24H 累积 VWAP 偏离%、5m RSI(14)、20日 ATR% 与
   轻量 24H 迷你曲线数据；严禁混入 79 只可交易监控标的池。
2. 严格纯美股口径（EXCLUDED_TEMPERATURE_TICKERS），彻底排除 BTC-USD / IBIT / MSTR / COIN / MARA
   等加密资产、XAUUSD / GLD / TLT 等商品美债，以及 SQQQ / SOXS / VIXY 等反向与波动率 ETF，
   确保非美股资产极端波动对 4 项温度计分数的扰动严格为 0.0。
3. 输出 4 项严格钳制在 [0, 100] 的温度计指标（零未来函数）：
   - macro_bubble_score     (大盘整体泡沫评分)
   - intraday_bubble_score  (日内泡沫评分，盘中现货 / 盘前夜盘自动切期货)
   - macro_bottom_score     (大盘整体抄底指数，含 VIX 倒挂/单边破位硬接刀锁 <= 50)
   - intraday_bottom_score  (日内抄底指数，含单边阴跌硬接刀锁 <= 50 与倒挂期提示)
"""

from __future__ import annotations

import html
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:
    from zoneinfo import ZoneInfo

    _ET_TZ = ZoneInfo("America/New_York")
except Exception:
    _ET_TZ = timezone(timedelta(hours=-5))

SCRATCH_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FUTURES_TEMP_CACHE_PATH = os.path.join(SCRATCH_DIR, "data_cache", "futures_temp_cache.json")
DEFAULT_CACHE_TTL_SECONDS = 180

FUTURES_SYMBOLS: Tuple[str, ...] = ("NQ=F", "ES=F")
FUTURES_META: Dict[str, Dict[str, str]] = {
    "NQ=F": {"short_name": "纳指100期货", "code": "NQ"},
    "ES=F": {"short_name": "标普500期货", "code": "ES"},
}

# 严格排除的资产名单：加密/加密概念股、黄金/大宗、美债、波动率与反向杠杆 ETF
EXCLUDED_TEMPERATURE_TICKERS = frozenset(
    {
        "BTC-USD",
        "BTC",
        "ETH-USD",
        "IBIT",
        "BITO",
        "MSTR",
        "COIN",
        "MARA",
        "RIOT",
        "CLSK",
        "XAUUSD",
        "GC=F",
        "GLD",
        "SLV",
        "USO",
        "TLT",
        "IEF",
        "SHY",
        "SQQQ",
        "SOXS",
        "VIXY",
        "UVXY",
        "SDOW",
        "SPXU",
        "SPXS",
        "TZA",
        "QID",
        "SDS",
        "SH",
    }
)

EXCLUDED_TEMPERATURE_CATS = frozenset(
    {
        "数字资产",
        "加密资产",
        "加密货币",
        "大宗商品",
        "避险美债",
        "恐慌指数",
        "反向做空",
        "反向对冲",
    }
)


def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    try:
        v = float(val)
    except Exception:
        return lo
    if not math.isfinite(v):
        return lo
    return max(lo, min(hi, v))


def _safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return default
    try:
        v = float(val)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def _linear_score(val: Optional[float], low: float, high: float, max_pts: float) -> float:
    """将指标线性映射到 [0, max_pts]；当 low > high 时支持反向（越低分越高）。"""
    v = _safe_float(val)
    if v is None or max_pts <= 0:
        return 0.0
    if abs(high - low) < 1e-12:
        return max_pts if v >= high else 0.0
    if high > low:
        ratio = (v - low) / (high - low)
    else:
        ratio = (low - v) / (low - high)
    return max(0.0, min(max_pts, ratio * max_pts))


def is_pure_us_equity(ticker: str, item: Optional[Dict[str, Any]] = None) -> bool:
    """判断标的是否属于纯美股权益统计口径（严格排除加密、商品、美债、反向与波动率 ETF）。"""
    tk = str(ticker or "").strip().upper()
    if not tk or tk in EXCLUDED_TEMPERATURE_TICKERS:
        return False
    if tk.endswith("-USD") or tk.endswith("=X") or tk.startswith("^"):
        return False
    if isinstance(item, dict):
        cat = str(item.get("cat") or item.get("category") or "").strip()
        if cat in EXCLUDED_TEMPERATURE_CATS:
            return False
        for kw in ("反向", "做空", "加密", "数字资产", "黄金", "大宗", "美债", "恐慌"):
            if kw in cat:
                return False
        name = str(item.get("name") or "")
        for kw in ("反向", "做空", "比特币", "黄金", "美债", "恐慌"):
            if kw in name:
                return False
        lev = _safe_float(item.get("leverage"))
        if lev is not None and lev < 0:
            return False
    return True


def _extract_symbol_df(raw_df: Any, symbol: str) -> Any:
    """兼容 yfinance 单标/多标 MultiIndex 列格式，提取指定标的 OHLCV DataFrame。"""
    if raw_df is None or getattr(raw_df, "empty", True):
        return None
    try:
        import pandas as pd

        cols = raw_df.columns
        if isinstance(cols, pd.MultiIndex):
            lv0 = set(cols.get_level_values(0))
            lv1 = set(cols.get_level_values(1))
            if symbol in lv0:
                sub = raw_df[symbol].copy()
            elif symbol in lv1:
                sub = raw_df.xs(symbol, axis=1, level=1).copy()
            else:
                return None
        else:
            sub = raw_df.copy()
        if sub is None or getattr(sub, "empty", True):
            return None
        if "Close" not in sub.columns:
            return None
        return sub.dropna(subset=["Close"])
    except Exception:
        return None


def _compute_rsi_series(close_series: Sequence[float], period: int = 14) -> List[float]:
    """轻量零依赖 Wilder/EMA RSI 序列计算（零未来函数）。"""
    vals = [float(x) for x in close_series if _safe_float(x) is not None]
    n = len(vals)
    if n < 2:
        return []
    deltas = [vals[i] - vals[i - 1] for i in range(1, n)]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    p = min(period, len(deltas))
    avg_gain = sum(gains[:p]) / p
    avg_loss = sum(losses[:p]) / p
    out: List[float] = []
    for i in range(p - 1, len(deltas)):
        if i >= p:
            avg_gain = (avg_gain * (p - 1) + gains[i]) / p
            avg_loss = (avg_loss * (p - 1) + losses[i]) / p
        if avg_loss <= 1e-12:
            rsi = 100.0 if avg_gain > 1e-12 else 50.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))
        out.append(round(_clamp(rsi, 0.0, 100.0), 2))
    return out


def _compute_ema_series(close_series: Sequence[float], span: int = 9) -> List[float]:
    vals = [float(x) for x in close_series if _safe_float(x) is not None]
    if not vals:
        return []
    alpha = 2.0 / (span + 1.0)
    out = [vals[0]]
    for x in vals[1:]:
        out.append(alpha * x + (1.0 - alpha) * out[-1])
    return out


def _compute_atr_pct_from_daily(df_daily: Any, period: int = 20) -> Optional[float]:
    if df_daily is None or getattr(df_daily, "empty", True):
        return None
    try:
        closes = [float(x) for x in df_daily["Close"].dropna().tolist()]
        if len(closes) < 3:
            return None
        highs = [float(x) for x in df_daily["High"].dropna().tolist()] if "High" in df_daily.columns else closes
        lows = [float(x) for x in df_daily["Low"].dropna().tolist()] if "Low" in df_daily.columns else closes
        m = min(len(closes), len(highs), len(lows))
        closes, highs, lows = closes[-m:], highs[-m:], lows[-m:]
        trs = []
        for i in range(1, m):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            if math.isfinite(tr) and tr >= 0:
                trs.append(tr)
        if not trs:
            return None
        window = trs[-min(period, len(trs)) :]
        atr = sum(window) / len(window)
        last = closes[-1]
        if last <= 0:
            return None
        return round((atr / last) * 100.0, 3)
    except Exception:
        return None


def _is_futures_weekend_closed(now_et: Optional[datetime] = None, session_code: str = "") -> bool:
    """CME 股指期货美东休市窗口：周五 17:00 ET 至周日 18:00 ET。"""
    if str(session_code or "").upper() == "WEEKEND":
        dt = now_et or datetime.now(_ET_TZ)
        # 周日 18:00 ET 之后期货已重开，即便美股现货仍标注 WEEKEND，期货已为 24H 实时
        if dt.weekday() == 6 and dt.hour >= 18:
            return False
        return True
    dt = now_et or datetime.now(_ET_TZ)
    wd = dt.weekday()  # Mon=0 .. Sun=6
    if wd == 5:
        return True
    if wd == 4 and dt.hour >= 17:
        return True
    if wd == 6 and dt.hour < 18:
        return True
    return False


def _build_single_future_item(
    symbol: str,
    df_intra: Any,
    df_daily: Any,
    now_et: Optional[datetime] = None,
    session_code: str = "",
) -> Dict[str, Any]:
    meta = FUTURES_META.get(symbol, {"short_name": symbol, "code": symbol})
    weekend_closed = _is_futures_weekend_closed(now_et=now_et, session_code=session_code)
    status_tag = "周末休市·最近24H参考" if weekend_closed else "24H 期货实时"

    item: Dict[str, Any] = {
        "symbol": symbol,
        "code": meta["code"],
        "short_name": meta["short_name"],
        "available": False,
        "status_tag": status_tag,
        "weekend_closed": weekend_closed,
        "last": None,
        "prev_close": None,
        "pct_chg": None,
        "high_24h": None,
        "low_24h": None,
        "vwap_24h": None,
        "vwap_diff_pct": None,
        "rsi_5m": None,
        "atr_20d_pct": None,
        "close_vs_ema9_pct": None,
        "near_intraday_low": False,
        "intraday_stabilized": False,
        "reclaim_ema9": False,
        "reclaim_vwap": False,
        "green_reversal_bar": False,
        "rsi_bull_div": False,
        "series_24h": {"time_labels": [], "prices": [], "vwap_prices": []},
    }

    atr_20d_pct = _compute_atr_pct_from_daily(df_daily, period=20)
    if atr_20d_pct is None:
        atr_20d_pct = 1.65 if symbol == "NQ=F" else 1.20
    item["atr_20d_pct"] = round(float(atr_20d_pct), 3)

    daily_closes: List[float] = []
    if df_daily is not None and not getattr(df_daily, "empty", True):
        try:
            daily_closes = [float(x) for x in df_daily["Close"].dropna().tolist() if _safe_float(x) is not None]
        except Exception:
            daily_closes = []

    intra_ok = df_intra is not None and not getattr(df_intra, "empty", True)
    if intra_ok:
        try:
            sub = df_intra.dropna(subset=["Close"]).copy()
            if len(sub) > 0:
                # 仅截取最近 24 小时内的有效 K 线（若周末停盘则以最后一根 K 线往前推 24 小时）
                idx = sub.index
                try:
                    last_ts = idx[-1]
                    cutoff = last_ts - timedelta(hours=24)
                    sub_24h = sub[idx >= cutoff]
                    if len(sub_24h) < 4:
                        sub_24h = sub.tail(min(len(sub), 288))
                except Exception:
                    sub_24h = sub.tail(min(len(sub), 288))

                closes_all = [float(x) for x in sub["Close"].tolist()]
                closes_24h = [float(x) for x in sub_24h["Close"].tolist()]
                opens_24h = (
                    [float(x) for x in sub_24h["Open"].tolist()]
                    if "Open" in sub_24h.columns
                    else closes_24h
                )
                highs_24h = (
                    [float(x) for x in sub_24h["High"].tolist()]
                    if "High" in sub_24h.columns
                    else closes_24h
                )
                lows_24h = (
                    [float(x) for x in sub_24h["Low"].tolist()]
                    if "Low" in sub_24h.columns
                    else closes_24h
                )
                vols_24h = (
                    [max(0.0, float(x)) if _safe_float(x) is not None else 0.0 for x in sub_24h["Volume"].tolist()]
                    if "Volume" in sub_24h.columns
                    else [0.0] * len(closes_24h)
                )

                last_px = closes_24h[-1]
                hi_24 = max(highs_24h)
                lo_24 = min(lows_24h)

                # 24H 累积 VWAP（若期货某段无量则回退等权典型价均值）
                cum_pv = 0.0
                cum_v = 0.0
                vwap_series: List[float] = []
                tp_sum = 0.0
                for i in range(len(closes_24h)):
                    tp = (highs_24h[i] + lows_24h[i] + closes_24h[i]) / 3.0
                    tp_sum += tp
                    v = vols_24h[i]
                    if v > 0:
                        cum_pv += tp * v
                        cum_v += v
                    if cum_v > 0:
                        vwap_series.append(cum_pv / cum_v)
                    else:
                        vwap_series.append(tp_sum / (i + 1))

                vwap_last = vwap_series[-1] if vwap_series else last_px
                vwap_diff_pct = ((last_px - vwap_last) / vwap_last * 100.0) if vwap_last > 0 else 0.0

                # 相对前结算价涨跌幅：优先用日线倒数第二根（若最新日线即今日），否则倒数第一根
                prev_close = None
                if len(daily_closes) >= 2:
                    # 若最新日线收盘与当前期货价极接近（同日），则前结算为 daily_closes[-2]
                    if abs(daily_closes[-1] - last_px) / max(last_px, 1e-6) < 0.002:
                        prev_close = daily_closes[-2]
                    else:
                        prev_close = daily_closes[-1]
                elif len(daily_closes) == 1:
                    prev_close = daily_closes[0]
                else:
                    prev_close = closes_24h[0]

                pct_chg = ((last_px - prev_close) / prev_close * 100.0) if (prev_close and prev_close > 0) else 0.0

                # 5m RSI(14) 与 EMA9 及右侧企稳信号检测（最近 3~6 根 5m K 线）
                rsi_seq = _compute_rsi_series(closes_all[-120:], period=14)
                rsi_5m = rsi_seq[-1] if rsi_seq else 50.0
                ema9_seq = _compute_ema_series(closes_all[-60:], span=9)
                ema9_last = ema9_seq[-1] if ema9_seq else last_px
                close_vs_ema9_pct = ((last_px - ema9_last) / ema9_last * 100.0) if ema9_last > 0 else 0.0

                reclaim_ema9 = bool(
                    len(ema9_seq) >= 3
                    and closes_24h[-1] >= ema9_seq[-1]
                    and (closes_24h[-2] < ema9_seq[-2] or closes_24h[-3] < ema9_seq[-3])
                )
                reclaim_vwap = bool(
                    len(vwap_series) >= 3
                    and closes_24h[-1] >= vwap_series[-1]
                    and min(closes_24h[-3:-1]) < vwap_series[-2]
                )
                # 放量阳包阴/长下影阳线企稳检测
                green_rev = False
                if len(closes_24h) >= 4:
                    c_last, o_last, l_last, h_last = closes_24h[-1], opens_24h[-1], lows_24h[-1], highs_24h[-1]
                    body = c_last - o_last
                    rng = max(h_last - l_last, 1e-6)
                    lower_wick = min(o_last, c_last) - l_last
                    avg_vol = sum(vols_24h[-6:-1]) / max(len(vols_24h[-6:-1]), 1)
                    vol_ok = (vols_24h[-1] >= avg_vol * 1.05) if avg_vol > 0 else True
                    if body > 0 and vol_ok and (
                        c_last > max(opens_24h[-2], closes_24h[-2])
                        or (lower_wick / rng >= 0.40 and body / rng >= 0.25)
                    ):
                        green_rev = True

                # 5m RSI 底背离检测（最近 6 根创新低或平底但 RSI 抬升）
                rsi_bull_div = False
                if len(rsi_seq) >= 6 and len(closes_24h) >= 6:
                    prev_min_px = min(closes_24h[-6:-2])
                    curr_min_px = min(closes_24h[-3:])
                    prev_min_rsi = min(rsi_seq[-6:-2])
                    curr_rsi = rsi_seq[-1]
                    if curr_min_px <= prev_min_px * 1.0015 and curr_rsi >= prev_min_rsi + 3.0 and closes_24h[-1] > closes_24h[-2]:
                        rsi_bull_div = True

                day_rng = max(hi_24 - lo_24, 1e-6)
                pos_in_24h = (last_px - lo_24) / day_rng
                near_low = bool(pos_in_24h <= 0.22 and last_px < ema9_last)
                stabilized = bool(reclaim_ema9 or reclaim_vwap or green_rev or rsi_bull_div)

                # 下采样 24H 轻量曲线（最多 60 点供 SVG 渲染）
                n_pts = len(closes_24h)
                step = max(1, math.ceil(n_pts / 60))
                pick_idx = list(range(0, n_pts, step))
                if pick_idx[-1] != n_pts - 1:
                    pick_idx.append(n_pts - 1)
                t_labels: List[str] = []
                for k in pick_idx:
                    try:
                        ts = sub_24h.index[k]
                        t_labels.append(ts.strftime("%H:%M"))
                    except Exception:
                        t_labels.append(str(k))

                item.update(
                    {
                        "available": True,
                        "last": round(last_px, 2),
                        "prev_close": round(prev_close, 2) if prev_close is not None else None,
                        "pct_chg": round(pct_chg, 2),
                        "high_24h": round(hi_24, 2),
                        "low_24h": round(lo_24, 2),
                        "vwap_24h": round(vwap_last, 2),
                        "vwap_diff_pct": round(vwap_diff_pct, 2),
                        "rsi_5m": round(rsi_5m, 1),
                        "close_vs_ema9_pct": round(close_vs_ema9_pct, 3),
                        "near_intraday_low": near_low,
                        "intraday_stabilized": stabilized,
                        "reclaim_ema9": reclaim_ema9,
                        "reclaim_vwap": reclaim_vwap,
                        "green_reversal_bar": green_rev,
                        "rsi_bull_div": rsi_bull_div,
                        "series_24h": {
                            "time_labels": t_labels,
                            "prices": [round(closes_24h[k], 2) for k in pick_idx],
                            "vwap_prices": [round(vwap_series[k], 2) for k in pick_idx],
                        },
                    }
                )
                return item
        except Exception:
            pass

    # 若日内 5m 缺失但 60d 日线可用，静默降级至日线参考
    if len(daily_closes) >= 2:
        last_px = daily_closes[-1]
        prev_close = daily_closes[-2]
        pct_chg = ((last_px - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
        item.update(
            {
                "available": True,
                "status_tag": "上日收盘参考",
                "last": round(last_px, 2),
                "prev_close": round(prev_close, 2),
                "pct_chg": round(pct_chg, 2),
                "high_24h": round(last_px, 2),
                "low_24h": round(last_px, 2),
                "vwap_24h": round(last_px, 2),
                "vwap_diff_pct": 0.0,
                "rsi_5m": 50.0,
            }
        )
    return item


def _read_cache_if_fresh(cache_path: str, ttl_seconds: int) -> Optional[Dict[str, Any]]:
    if not cache_path or not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        if not isinstance(doc, dict):
            return None
        ts = _safe_float(doc.get("cached_at_ts"))
        if ts is None:
            return None
        if (time.time() - ts) <= max(0, int(ttl_seconds)):
            return doc
    except Exception:
        return None
    return None


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    target = os.path.abspath(path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{target}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
    os.replace(tmp_path, target)


def fetch_futures_snapshot(
    force_refresh: bool = False,
    cache_path: str = DEFAULT_FUTURES_TEMP_CACHE_PATH,
    ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    allow_network: bool = True,
    session_code: str = "",
    downloader: Optional[Callable[..., Any]] = None,
    now_et: Optional[datetime] = None,
) -> Dict[str, Any]:
    """独立抓取并缓存 NQ=F 与 ES=F 期货快照（单标的异常隔离，永不阻塞主雷达）。"""
    if not force_refresh:
        cached = _read_cache_if_fresh(cache_path, ttl_seconds)
        if isinstance(cached, dict) and isinstance(cached.get("symbols"), dict):
            # 动态刷新 status_tag 防止跨周末缓存文案陈旧
            wk = _is_futures_weekend_closed(now_et=now_et, session_code=session_code)
            for sym, obj in cached["symbols"].items():
                if isinstance(obj, dict) and obj.get("available"):
                    obj["weekend_closed"] = wk
                    if obj.get("status_tag") != "上日收盘参考":
                        obj["status_tag"] = "周末休市·最近24H参考" if wk else "24H 期货实时"
            cached["from_cache"] = True
            return cached

    if not allow_network:
        # 优先回退旧缓存
        stale = _read_cache_if_fresh(cache_path, ttl_seconds=86400 * 30)
        if isinstance(stale, dict) and isinstance(stale.get("symbols"), dict):
            stale["from_cache"] = True
            return stale
        return {
            "degraded": True,
            "from_cache": False,
            "cached_at_ts": time.time(),
            "updated_at_et": (now_et or datetime.now(_ET_TZ)).strftime("%Y-%m-%d %H:%M:%S ET"),
            "symbols": {
                sym: _build_single_future_item(sym, None, None, now_et=now_et, session_code=session_code)
                for sym in FUTURES_SYMBOLS
            },
        }

    dl_fn = downloader
    if dl_fn is None:
        try:
            import yfinance as yf

            dl_fn = yf.download
        except Exception:
            dl_fn = None

    raw_intra_5m = None
    raw_intra_15m = None
    raw_daily = None
    if dl_fn is not None:
        try:
            raw_intra_5m = dl_fn(
                list(FUTURES_SYMBOLS),
                period="5d",
                interval="5m",
                progress=False,
                auto_adjust=False,
                group_by="ticker",
                threads=False,
            )
        except Exception:
            raw_intra_5m = None
        try:
            raw_daily = dl_fn(
                list(FUTURES_SYMBOLS),
                period="60d",
                interval="1d",
                progress=False,
                auto_adjust=False,
                group_by="ticker",
                threads=False,
            )
        except Exception:
            raw_daily = None

    symbols_out: Dict[str, Dict[str, Any]] = {}
    avail_count = 0
    for sym in FUTURES_SYMBOLS:
        df_i = _extract_symbol_df(raw_intra_5m, sym)
        if (df_i is None or getattr(df_i, "empty", True)) and dl_fn is not None:
            if raw_intra_15m is None:
                try:
                    raw_intra_15m = dl_fn(
                        list(FUTURES_SYMBOLS),
                        period="5d",
                        interval="15m",
                        progress=False,
                        auto_adjust=False,
                        group_by="ticker",
                        threads=False,
                    )
                except Exception:
                    raw_intra_15m = False
            if raw_intra_15m is not False:
                df_i = _extract_symbol_df(raw_intra_15m, sym)
        df_d = _extract_symbol_df(raw_daily, sym)
        item = _build_single_future_item(sym, df_i, df_d, now_et=now_et, session_code=session_code)
        if item.get("available"):
            avail_count += 1
        symbols_out[sym] = item

    payload = {
        "degraded": avail_count == 0,
        "from_cache": False,
        "cached_at_ts": time.time(),
        "updated_at_et": (now_et or datetime.now(_ET_TZ)).strftime("%Y-%m-%d %H:%M:%S ET"),
        "symbols": symbols_out,
    }
    if avail_count > 0 and cache_path:
        try:
            _atomic_write_json(cache_path, payload)
        except Exception:
            pass
    return payload


def _extract_spot_intraday_metrics(
    ticker: str,
    macro_data: Dict[str, Any],
    intraday_map: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """提取盘中现货 (QQQ/SPY) 的 VWAP 偏离、5m RSI、涨跌幅与 5m 右侧企稳信号。"""
    m_item = (macro_data or {}).get(ticker) if isinstance(macro_data, dict) else None
    if not isinstance(m_item, dict):
        return None
    last = _safe_float(m_item.get("last"))
    if last is None or last <= 0:
        return None
    pct = _safe_float(m_item.get("pct_chg"), 0.0) or 0.0
    vwap_val = _safe_float(m_item.get("vwap"))
    vwap_diff = _safe_float(m_item.get("vwap_diff_pct"))
    if vwap_diff is None and vwap_val and vwap_val > 0:
        vwap_diff = ((last - vwap_val) / vwap_val) * 100.0
    if vwap_diff is None:
        vwap_diff = 0.0
    rsi_5m = _safe_float(m_item.get("rsi_5m"))
    if rsi_5m is None:
        rsi_5m = _safe_float(m_item.get("rsi"), 50.0) or 50.0
    atr_abs = _safe_float(m_item.get("atr"))
    atr_pct = ((atr_abs / last) * 100.0) if (atr_abs and atr_abs > 0) else (1.55 if ticker == "QQQ" else 1.15)
    atr_pct = max(0.4, atr_pct)

    near_low = False
    stabilized = False
    reclaim_ema9 = False
    reclaim_vwap = False
    green_rev = False
    rsi_bull_div = False

    # 若传入 intraday_map[ticker] (5m DataFrame)，基于最近 3~6 根 5m K 线精准检测右侧企稳
    df_5m = (intraday_map or {}).get(ticker) if isinstance(intraday_map, dict) else None
    if df_5m is not None and not getattr(df_5m, "empty", True) and "Close" in df_5m.columns:
        try:
            sub = df_5m.dropna(subset=["Close"]).tail(78)
            closes = [float(x) for x in sub["Close"].tolist()]
            opens = [float(x) for x in sub["Open"].tolist()] if "Open" in sub.columns else closes
            highs = [float(x) for x in sub["High"].tolist()] if "High" in sub.columns else closes
            lows = [float(x) for x in sub["Low"].tolist()] if "Low" in sub.columns else closes
            vols = (
                [max(0.0, float(x)) if _safe_float(x) is not None else 0.0 for x in sub["Volume"].tolist()]
                if "Volume" in sub.columns
                else [0.0] * len(closes)
            )
            if len(closes) >= 6:
                rsi_seq = _compute_rsi_series(closes, period=14)
                if rsi_seq:
                    rsi_5m = rsi_seq[-1]
                ema9_seq = _compute_ema_series(closes, span=9)
                ema9_last = ema9_seq[-1]
                reclaim_ema9 = bool(
                    closes[-1] >= ema9_last and (closes[-2] < ema9_seq[-2] or closes[-3] < ema9_seq[-3])
                )
                if vwap_val and vwap_val > 0:
                    reclaim_vwap = bool(closes[-1] >= vwap_val and min(closes[-3:-1]) < vwap_val)
                c_last, o_last, l_last, h_last = closes[-1], opens[-1], lows[-1], highs[-1]
                body = c_last - o_last
                rng = max(h_last - l_last, 1e-6)
                lower_wick = min(o_last, c_last) - l_last
                avg_vol = sum(vols[-6:-1]) / max(len(vols[-6:-1]), 1)
                vol_ok = (vols[-1] >= avg_vol * 1.05) if avg_vol > 0 else True
                if body > 0 and vol_ok and (
                    c_last > max(opens[-2], closes[-2])
                    or (lower_wick / rng >= 0.40 and body / rng >= 0.25)
                ):
                    green_rev = True
                if len(rsi_seq) >= 6:
                    if min(closes[-3:]) <= min(closes[-6:-2]) * 1.0015 and rsi_seq[-1] >= min(rsi_seq[-6:-2]) + 3.0 and closes[-1] > closes[-2]:
                        rsi_bull_div = True
                hi_d, lo_d = max(highs), min(lows)
                pos = (closes[-1] - lo_d) / max(hi_d - lo_d, 1e-6)
                near_low = bool(pos <= 0.22 and closes[-1] < ema9_last)
                stabilized = bool(reclaim_ema9 or reclaim_vwap or green_rev or rsi_bull_div)
        except Exception:
            pass
    else:
        # 无 5m 序列时的保守近似：显式字段优先
        if m_item.get("intraday_stabilized") is not None:
            stabilized = bool(m_item.get("intraday_stabilized"))
        if m_item.get("near_intraday_low") is not None:
            near_low = bool(m_item.get("near_intraday_low"))
        elif pct <= -0.8 and vwap_diff <= -0.35 and rsi_5m <= 36.0:
            near_low = True

    return {
        "symbol": ticker,
        "last": last,
        "pct_chg": pct,
        "vwap_diff_pct": vwap_diff,
        "rsi_5m": rsi_5m,
        "atr_20d_pct": atr_pct,
        "near_intraday_low": near_low,
        "intraday_stabilized": stabilized,
        "reclaim_ema9": reclaim_ema9,
        "reclaim_vwap": reclaim_vwap,
        "green_reversal_bar": green_rev,
        "rsi_bull_div": rsi_bull_div,
    }


def _compute_pure_us_breadth(stocks_data: Optional[Sequence[Dict[str, Any]]]) -> Dict[str, Any]:
    """仅统计纯美股权益标的池的 MA20 宽度与 RSI14>=70 过热占比（严格零加密/商品/反向污染）。"""
    if not stocks_data:
        return {"count": 0, "pct_above_ma20": 50.0, "pct_rsi_overbought": 15.0, "pct_rsi_oversold": 15.0}
    total = 0
    above_ma20 = 0
    rsi_ob = 0
    rsi_os = 0
    for item in stocks_data:
        if not isinstance(item, dict):
            continue
        tk = str(item.get("ticker") or item.get("symbol") or "").upper()
        if not is_pure_us_equity(tk, item):
            continue
        total += 1
        last = _safe_float(item.get("last"))
        ma20 = _safe_float(item.get("ma20"))
        bias20 = _safe_float(item.get("bias20"))
        bias50 = _safe_float(item.get("bias"))
        if ma20 is not None and ma20 > 0 and last is not None:
            if last > ma20:
                above_ma20 += 1
        elif bias20 is not None:
            if bias20 > 0:
                above_ma20 += 1
        elif bias50 is not None:
            if bias50 > 0:
                above_ma20 += 1
        rsi14 = _safe_float(item.get("rsi"))
        if rsi14 is not None:
            if rsi14 >= 70.0:
                rsi_ob += 1
            elif rsi14 <= 32.0:
                rsi_os += 1

    if total == 0:
        return {"count": 0, "pct_above_ma20": 50.0, "pct_rsi_overbought": 15.0, "pct_rsi_oversold": 15.0}
    return {
        "count": total,
        "pct_above_ma20": round((above_ma20 / total) * 100.0, 2),
        "pct_rsi_overbought": round((rsi_ob / total) * 100.0, 2),
        "pct_rsi_oversold": round((rsi_os / total) * 100.0, 2),
    }


def _check_macro_daily_stabilization(
    macro_data: Dict[str, Any],
    history_dict: Optional[Dict[str, Any]] = None,
) -> bool:
    """检查 QQQ/SPY 是否出现放量反包阳线、长下影或重返 EMA5（零未来函数）。"""
    for tk in ("QQQ", "SPY"):
        m_item = (macro_data or {}).get(tk) if isinstance(macro_data, dict) else None
        if isinstance(m_item, dict):
            pct = _safe_float(m_item.get("pct_chg"), 0.0) or 0.0
            vwap_d = _safe_float(m_item.get("vwap_diff_pct"), 0.0) or 0.0
            if pct >= 0.6 and vwap_d >= 0.0:
                return True
        df = (history_dict or {}).get(tk) if isinstance(history_dict, dict) else None
        if df is not None and not getattr(df, "empty", True) and "Close" in df.columns and len(df) >= 6:
            try:
                closes = [float(x) for x in df["Close"].dropna().tail(10).tolist()]
                if len(closes) >= 6:
                    ema5 = _compute_ema_series(closes, span=5)
                    if closes[-1] >= ema5[-1] and closes[-2] < ema5[-2] and closes[-1] > closes[-2]:
                        return True
                    if "Open" in df.columns and "Low" in df.columns and "High" in df.columns:
                        o = float(df["Open"].iloc[-1])
                        c = float(df["Close"].iloc[-1])
                        l = float(df["Low"].iloc[-1])
                        h = float(df["High"].iloc[-1])
                        rng = max(h - l, 1e-6)
                        if c > o and (min(o, c) - l) / rng >= 0.42:
                            return True
            except Exception:
                pass
    return False


def compute_market_temperature(
    macro_data: Optional[Dict[str, Any]] = None,
    stocks_data: Optional[Sequence[Dict[str, Any]]] = None,
    vix_regime: Optional[Dict[str, Any]] = None,
    cross_market_liquidity: Optional[Dict[str, Any]] = None,
    futures_snapshot: Optional[Dict[str, Any]] = None,
    session_code: str = "REGULAR",
    history_dict: Optional[Dict[str, Any]] = None,
    intraday_map: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    计算 V5.0 大盘与日内「泡沫评分 + 抄底指数」四维温度计（纯美股口径，零 BTC/加密/反向资产污染）。
    """
    md = macro_data if isinstance(macro_data, dict) else {}
    vr = vix_regime if isinstance(vix_regime, dict) else {}
    liq = cross_market_liquidity if isinstance(cross_market_liquidity, dict) else {}
    fut_snap = futures_snapshot if isinstance(futures_snapshot, dict) else {}
    fut_syms = fut_snap.get("symbols") if isinstance(fut_snap.get("symbols"), dict) else {}
    nq_f = fut_syms.get("NQ=F") if isinstance(fut_syms.get("NQ=F"), dict) else {}
    es_f = fut_syms.get("ES=F") if isinstance(fut_syms.get("ES=F"), dict) else {}

    sess = str(session_code or "REGULAR").upper()
    breadth = _compute_pure_us_breadth(stocks_data)

    # 核心纯美股基准（绝不读取 BTC-USD / GLD / TLT / SQQQ / VIXY）
    qqq = md.get("QQQ") if isinstance(md.get("QQQ"), dict) else (md.get("QQQM") if isinstance(md.get("QQQM"), dict) else {})
    spy = md.get("SPY") if isinstance(md.get("SPY"), dict) else {}
    smh = md.get("SMH") if isinstance(md.get("SMH"), dict) else {}

    qqq_bias50 = _safe_float(qqq.get("bias"), 0.0) or 0.0
    spy_bias50 = _safe_float(spy.get("bias"), 0.0) or 0.0
    smh_bias50 = _safe_float(smh.get("bias"), 0.0) or 0.0
    qqq_bias20 = _safe_float(qqq.get("bias20"))
    spy_bias20 = _safe_float(spy.get("bias20"))
    smh_bias20 = _safe_float(smh.get("bias20"))
    if qqq_bias20 is None:
        qqq_bias20 = qqq_bias50 * 0.65
    if spy_bias20 is None:
        spy_bias20 = spy_bias50 * 0.65
    if smh_bias20 is None:
        smh_bias20 = smh_bias50 * 0.65

    avg_idx_bias50 = (qqq_bias50 + spy_bias50) / 2.0
    avg_idx_bias20 = (qqq_bias20 + spy_bias20) / 2.0

    # VIX 与跨市场信贷指标
    vix_spot = _safe_float(vr.get("vix_spot"))
    vix_ratio = _safe_float(vr.get("vix_term_ratio"))
    vix_state = str(vr.get("vix_state") or "").upper()
    vix_bottom_signal = bool(vr.get("vix_bottom_signal", False))
    vix_dd_peak = _safe_float(vr.get("vix_drawdown_from_peak_pct"), 0.0) or 0.0
    hyg_5d = _safe_float(liq.get("hyg_ief_5d_pct"), 0.0) or 0.0

    # =====================================================================
    # 1. 大盘整体泡沫评分 (macro_bubble_score, 0~100)
    # =====================================================================
    # 权重：均线偏离 35% + 低VIX自满 25% + 纯美股宽度过热 25% + SMH动量拉伸 15%
    mb_trend = _linear_score(avg_idx_bias50, 0.0, 12.0, 24.0) + _linear_score(avg_idx_bias20, 0.0, 7.0, 11.0)
    mb_vix_low = _linear_score(vix_spot, 24.0, 12.0, 15.0) if vix_spot is not None else 7.5
    mb_contango = _linear_score(vix_ratio, 0.96, 0.80, 10.0) if vix_ratio is not None else 5.0
    mb_vix = mb_vix_low + mb_contango
    mb_breadth = (
        _linear_score(breadth["pct_above_ma20"], 45.0, 90.0, 14.0)
        + _linear_score(breadth["pct_rsi_overbought"], 10.0, 55.0, 11.0)
    )
    smh_rsi = _safe_float(smh.get("rsi"), 50.0) or 50.0
    mb_smh = _linear_score(smh_bias20, 1.0, 12.0, 9.0) + _linear_score(smh_rsi, 55.0, 80.0, 6.0)
    macro_bubble_score = round(_clamp(mb_trend + mb_vix + mb_breadth + mb_smh, 0.0, 100.0), 1)

    if macro_bubble_score >= 80.0:
        mb_tier = "高危泡沫·分批止盈"
        mb_color = "down"
    elif macro_bubble_score >= 65.0:
        mb_tier = "偏热防追高"
        mb_color = "warn"
    elif macro_bubble_score >= 31.0:
        mb_tier = "正常健康"
        mb_color = "neutral"
    else:
        mb_tier = "冰点无泡沫"
        mb_color = "up"

    # =====================================================================
    # 2. 日内数据源自动切换（盘中现货 vs 盘前/夜盘/亚盘期货）
    # =====================================================================
    spot_qqq = _extract_spot_intraday_metrics("QQQ", md, intraday_map=intraday_map)
    spot_spy = _extract_spot_intraday_metrics("SPY", md, intraday_map=intraday_map)
    futures_avail = bool(nq_f.get("available") or es_f.get("available"))
    futures_weekend = bool(nq_f.get("weekend_closed") and es_f.get("weekend_closed"))

    if sess == "REGULAR" and (spot_qqq is not None or spot_spy is not None):
        source_tag = "盘中现货"
        active_legs = [x for x in (spot_qqq, spot_spy) if x is not None]
    elif futures_avail and not futures_weekend:
        source_tag = "期货实时"
        active_legs = [x for x in (nq_f, es_f) if isinstance(x, dict) and x.get("available")]
    elif futures_avail:
        source_tag = "上日收盘参考"
        active_legs = [x for x in (nq_f, es_f) if isinstance(x, dict) and x.get("available")]
    else:
        source_tag = "上日收盘参考"
        active_legs = [x for x in (spot_qqq, spot_spy) if x is not None]

    if active_legs:
        intra_pct = sum(float(_safe_float(x.get("pct_chg"), 0.0) or 0.0) for x in active_legs) / len(active_legs)
        intra_vwap_diff = sum(float(_safe_float(x.get("vwap_diff_pct"), 0.0) or 0.0) for x in active_legs) / len(active_legs)
        intra_rsi5m = sum(float(_safe_float(x.get("rsi_5m"), 50.0) or 50.0) for x in active_legs) / len(active_legs)
        intra_atr_pct = sum(max(0.4, float(_safe_float(x.get("atr_20d_pct"), 1.4) or 1.4)) for x in active_legs) / len(active_legs)
        intra_near_low = any(bool(x.get("near_intraday_low")) for x in active_legs)
        intra_stabilized = any(bool(x.get("intraday_stabilized")) for x in active_legs)
    else:
        intra_pct = 0.0
        intra_vwap_diff = 0.0
        intra_rsi5m = 50.0
        intra_atr_pct = 1.4
        intra_near_low = False
        intra_stabilized = False

    # =====================================================================
    # 3. 日内泡沫评分 (intraday_bubble_score, 0~100)
    # =====================================================================
    # 权重：VWAP 正偏离 ATR 倍数 45% + 5m RSI 超买 30% + 日内涨幅/ATR 25%
    pos_vwap_atr = max(0.0, intra_vwap_diff) / max(intra_atr_pct, 0.4)
    pos_gain_atr = max(0.0, intra_pct) / max(intra_atr_pct, 0.4)
    ib_vwap = _linear_score(pos_vwap_atr, 0.0, 0.90, 45.0)
    ib_rsi = _linear_score(intra_rsi5m, 52.0, 78.0, 30.0)
    ib_stretch = _linear_score(pos_gain_atr, 0.0, 1.25, 25.0)
    raw_intra_bubble = ib_vwap + ib_rsi + ib_stretch
    # 当日内涨幅 <= 0 或现价低于 VWAP 时自动收敛至 <= 25
    if intra_pct <= 0.0 or intra_vwap_diff < 0.0:
        raw_intra_bubble = min(raw_intra_bubble, 25.0)
    intraday_bubble_score = round(_clamp(raw_intra_bubble, 0.0, 100.0), 1)

    if intraday_bubble_score >= 75.0:
        ib_tier = "日内急拉透支 · 严禁追高，等回踩 VWAP"
        ib_color = "down"
    elif intraday_bubble_score >= 60.0:
        ib_tier = "日内偏热 · 不宜追高"
        ib_color = "warn"
    elif intraday_bubble_score >= 30.0:
        ib_tier = "日内节奏健康"
        ib_color = "neutral"
    else:
        ib_tier = "日内无追高泡沫"
        ib_color = "up"

    # =====================================================================
    # 4. 大盘整体抄底指数 (macro_bottom_score, 0~100 + 硬接刀锁)
    # =====================================================================
    # 第一层：超卖与恐慌深度（上限 60）
    bt_vix = _linear_score(vix_spot, 18.0, 36.0, 22.0) if vix_spot is not None else 6.0
    bt_idx_drop = _linear_score(-avg_idx_bias20, 1.0, 10.0, 10.0) + _linear_score(-avg_idx_bias50, 2.0, 14.0, 8.0)
    bt_breadth = _linear_score(breadth["pct_above_ma20"], 50.0, 12.0, 12.0)
    bt_credit = _linear_score(-hyg_5d, 0.3, 2.5, 8.0)
    macro_layer1 = _clamp(bt_vix + bt_idx_drop + bt_breadth + bt_credit, 0.0, 60.0)

    # 第二层：右侧企稳确认（上限 40）
    macro_daily_stab = _check_macro_daily_stabilization(md, history_dict=history_dict)
    vix_backwardation = bool(
        (vix_ratio is not None and vix_ratio >= 1.00) or vix_state == "BACKWARDATION_BRAKE"
    )
    qqq_pct = _safe_float(qqq.get("pct_chg"), intra_pct) or 0.0
    spy_pct = _safe_float(spy.get("pct_chg"), intra_pct) or 0.0
    continuous_breakdown = bool(
        qqq_pct <= -1.0 and spy_pct <= -0.8 and not macro_daily_stab and (vix_ratio is None or vix_ratio >= 0.98)
    )

    macro_knife_locked = bool((vix_backwardation and not vix_bottom_signal) or (continuous_breakdown and not vix_bottom_signal))

    if macro_knife_locked:
        macro_layer2 = 0.0
        macro_bottom_score = round(min(50.0, macro_layer1), 1)
        mbt_tier = "⛔ 超卖观察坑 · 倒挂未解禁抄底"
        mbt_color = "down"
    else:
        l2 = 0.0
        if vix_bottom_signal:
            l2 += 30.0
        elif vix_dd_peak >= 8.0 and (vix_ratio is None or vix_ratio < 0.98):
            l2 += 16.0
        elif vix_dd_peak >= 4.0 and (vix_ratio is None or vix_ratio < 1.00):
            l2 += 8.0
        if macro_daily_stab:
            l2 += 12.0
        macro_layer2 = _clamp(l2, 0.0, 40.0)
        macro_bottom_score = round(_clamp(macro_layer1 + macro_layer2, 0.0, 100.0), 1)
        if macro_bottom_score >= 65.0:
            mbt_tier = "🟢 右侧恐慌出清 · 分批抄底窗"
            mbt_color = "up"
        elif macro_bottom_score >= 45.0:
            mbt_tier = "🟡 温和回踩区 · 观察右侧信号"
            mbt_color = "warn"
        else:
            mbt_tier = "⚪ 未到抄底区 · 正常观望"
            mbt_color = "neutral"

    # =====================================================================
    # 5. 日内抄底指数 (intraday_bottom_score, 0~100 + 硬接刀锁)
    # =====================================================================
    # 第一层：日内超卖深度（上限 60）
    neg_vwap_atr = max(0.0, -intra_vwap_diff) / max(intra_atr_pct, 0.4)
    neg_drop_atr = max(0.0, -intra_pct) / max(intra_atr_pct, 0.4)
    ibt_vwap = _linear_score(neg_vwap_atr, 0.10, 0.95, 26.0)
    ibt_rsi = _linear_score(intra_rsi5m, 42.0, 22.0, 18.0)
    ibt_drop = _linear_score(neg_drop_atr, 0.20, 1.15, 16.0)
    intra_layer1 = _clamp(ibt_vwap + ibt_rsi + ibt_drop, 0.0, 60.0)

    # 第二层：日内右侧企稳（上限 40）
    one_way_selloff = bool(
        (intra_near_low or (intra_pct <= -0.6 and intra_vwap_diff <= -0.25))
        and not intra_stabilized
    )
    intraday_knife_locked = bool(one_way_selloff)

    back_suffix = "（大盘倒挂期仅限轻仓反抽）" if vix_backwardation else ""

    if intraday_knife_locked:
        intra_layer2 = 0.0
        intraday_bottom_score = round(min(50.0, intra_layer1), 1)
        ibt_tier = f"⛔ 日内阴跌寻底 · 禁左侧接刀{back_suffix}"
        ibt_color = "down"
    else:
        il2 = 0.0
        if intra_stabilized:
            il2 += 28.0
            if any(bool(x.get("reclaim_vwap")) for x in active_legs):
                il2 += 10.0
            elif any(bool(x.get("reclaim_ema9")) for x in active_legs):
                il2 += 8.0
            if any(bool(x.get("rsi_bull_div")) for x in active_legs):
                il2 += 6.0
        elif intra_vwap_diff >= 0.0 and intra_pct > -0.3:
            il2 += 6.0
        intra_layer2 = _clamp(il2, 0.0, 40.0)
        intraday_bottom_score = round(_clamp(intra_layer1 + intra_layer2, 0.0, 100.0), 1)
        if intraday_bottom_score >= 65.0:
            ibt_tier = f"🟢 日内右侧企稳 · 可轻仓低吸{back_suffix}"
            ibt_color = "up"
        elif intraday_bottom_score >= 40.0:
            ibt_tier = f"🟡 日内回踩观察 · 等企稳确认{back_suffix}"
            ibt_color = "warn"
        else:
            ibt_tier = f"⚪ 日内未见超卖抄底点{back_suffix}"
            ibt_color = "neutral"

    # 一句话通俗总览
    if macro_knife_locked or intraday_knife_locked:
        headline_cn = "大盘或日内处于单边压制/恐慌倒挂期，接刀锁已生效，严禁左侧盲目抄底。"
    elif macro_bubble_score >= 80.0 or intraday_bubble_score >= 75.0:
        headline_cn = "短线/大盘明显偏热透支，严禁追高，耐心等回踩均价线再看。"
    elif macro_bottom_score >= 65.0:
        headline_cn = "右侧恐慌出清信号已亮起，可围绕抗跌真龙头分批低吸。"
    elif intraday_bottom_score >= 65.0:
        headline_cn = "日内出现超卖后右侧企稳信号，可依托均价线轻仓快进快出。"
    else:
        headline_cn = "大盘温度整体可控，既无极端泡沫也未到系统性抄底点，按个股强弱节奏操作。"

    return {
        "schema_version": "v5.0-market-temperature",
        "excluded_btc": True,
        "pure_us_equity_count": breadth["count"],
        "source_tag": source_tag,
        "session_code": sess,
        "vix_backwardation_active": vix_backwardation,
        "headline_cn": headline_cn,
        "macro_bubble_score": macro_bubble_score,
        "macro_bubble_tier": mb_tier,
        "macro_bubble_color": mb_color,
        "macro_bubble_breakdown": {
            "trend_stretch": round(mb_trend, 1),
            "vix_complacency": round(mb_vix, 1),
            "pure_breadth_ob": round(mb_breadth, 1),
            "smh_stretch": round(mb_smh, 1),
            "pure_breadth_ma20_pct": breadth["pct_above_ma20"],
        },
        "intraday_bubble_score": intraday_bubble_score,
        "intraday_bubble_tier": ib_tier,
        "intraday_bubble_color": ib_color,
        "intraday_bubble_breakdown": {
            "vwap_pos_atr": round(ib_vwap, 1),
            "rsi5m_ob": round(ib_rsi, 1),
            "gain_atr_stretch": round(ib_stretch, 1),
            "intra_pct": round(intra_pct, 2),
            "intra_vwap_diff_pct": round(intra_vwap_diff, 2),
            "intra_rsi_5m": round(intra_rsi5m, 1),
        },
        "macro_bottom_score": macro_bottom_score,
        "macro_bottom_tier": mbt_tier,
        "macro_bottom_color": mbt_color,
        "knife_catch_locked": macro_knife_locked,
        "macro_bottom_breakdown": {
            "layer1_oversold": round(macro_layer1, 1),
            "layer2_right_side": round(macro_layer2, 1),
            "vix_depth": round(bt_vix, 1),
            "idx_drop": round(bt_idx_drop, 1),
            "breadth_washout": round(bt_breadth, 1),
            "credit_drawdown": round(bt_credit, 1),
        },
        "intraday_bottom_score": intraday_bottom_score,
        "intraday_bottom_tier": ibt_tier,
        "intraday_bottom_color": ibt_color,
        "intraday_knife_catch_locked": intraday_knife_locked,
        "intraday_bottom_breakdown": {
            "layer1_oversold": round(intra_layer1, 1),
            "layer2_right_side": round(intra_layer2, 1),
            "vwap_neg_atr": round(ibt_vwap, 1),
            "rsi5m_os": round(ibt_rsi, 1),
            "drop_atr": round(ibt_drop, 1),
            "intra_stabilized": intra_stabilized,
        },
        "futures": {
            "degraded": bool(fut_snap.get("degraded", not futures_avail)),
            "updated_at_et": str(fut_snap.get("updated_at_et") or ""),
            "NQ=F": nq_f,
            "ES=F": es_f,
        },
    }


def _render_mini_svg_24h(series_24h: Optional[Dict[str, Any]], stroke_color: str) -> str:
    """渲染轻量纯内联 SVG 24H 价格折线 + VWAP 虚线（0 外部依赖，暗/亮色自适应）。"""
    if not isinstance(series_24h, dict):
        return '<div style="height:44px;display:flex;align-items:center;justify-content:center;font-size:11px;color:var(--text-muted);">暂无 24H 曲线</div>'
    prices = [float(x) for x in (series_24h.get("prices") or []) if _safe_float(x) is not None]
    vwaps = [float(x) for x in (series_24h.get("vwap_prices") or []) if _safe_float(x) is not None]
    if len(prices) < 2:
        return '<div style="height:44px;display:flex;align-items:center;justify-content:center;font-size:11px;color:var(--text-muted);">24H 样本积累中</div>'

    all_y = prices + (vwaps if len(vwaps) == len(prices) else [])
    y_min, y_max = min(all_y), max(all_y)
    if abs(y_max - y_min) < 1e-6:
        y_min -= 1.0
        y_max += 1.0
    w, h, pad = 260.0, 46.0, 4.0
    n = len(prices)

    def _pts(arr: List[float]) -> str:
        coords = []
        for i, v in enumerate(arr):
            x = pad + (w - 2 * pad) * (i / max(n - 1, 1))
            y = h - pad - (h - 2 * pad) * ((v - y_min) / (y_max - y_min))
            coords.append(f"{x:.1f},{y:.1f}")
        return " ".join(coords)

    px_pts = _pts(prices)
    vwap_line = ""
    if len(vwaps) == len(prices):
        vwap_line = f'<polyline fill="none" stroke="var(--warning-gold, #f59e0b)" stroke-width="1.2" stroke-dasharray="3,2" opacity="0.85" points="{_pts(vwaps)}" />'
    return (
        f'<svg viewBox="0 0 {int(w)} {int(h)}" width="100%" height="46" preserveAspectRatio="none" '
        f'style="display:block;overflow:visible;">'
        f'{vwap_line}'
        f'<polyline fill="none" stroke="{stroke_color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" points="{px_pts}" />'
        f'</svg>'
    )


def render_macro_futures_and_temperature_html(temp_payload: Optional[Dict[str, Any]]) -> str:
    """生成 macro_radar.html 主舞台 HTML 区块：【美股指期货先导哨】 + 【大盘温度计 · 泡沫与抄底四维仪表】。"""
    tp = temp_payload if isinstance(temp_payload, dict) else {}
    fut = tp.get("futures") if isinstance(tp.get("futures"), dict) else {}

    def _color_var(tag: str) -> str:
        return {
            "up": "var(--color-up, #10b981)",
            "down": "var(--color-down, #ef4444)",
            "warn": "var(--warning-gold, #f59e0b)",
        }.get(tag, "var(--accent-blue, #38bdf8)")

    fut_cards = []
    for sym in FUTURES_SYMBOLS:
        item = fut.get(sym) if isinstance(fut.get(sym), dict) else {}
        meta = FUTURES_META[sym]
        last = _safe_float(item.get("last"))
        pct = _safe_float(item.get("pct_chg"))
        vwap = _safe_float(item.get("vwap_24h"))
        vdiff = _safe_float(item.get("vwap_diff_pct"))
        rsi5 = _safe_float(item.get("rsi_5m"))
        hi24 = _safe_float(item.get("high_24h"))
        lo24 = _safe_float(item.get("low_24h"))
        stag = html.escape(str(item.get("status_tag") or "24H 期货实时"))
        p_col = "var(--color-up, #10b981)" if (pct is not None and pct >= 0) else "var(--color-down, #ef4444)"
        last_str = f"{last:,.2f}" if last is not None else "--"
        pct_str = f"{pct:+.2f}%" if pct is not None else "--"
        vdiff_str = f"{vdiff:+.2f}%" if vdiff is not None else "--"
        vwap_str = f"{vwap:,.2f}" if vwap is not None else "--"
        rsi_str = f"{rsi5:.1f}" if rsi5 is not None else "--"
        rng_str = f"{lo24:,.1f} ~ {hi24:,.1f}" if (lo24 is not None and hi24 is not None) else "--"
        svg_html = _render_mini_svg_24h(item.get("series_24h"), p_col)

        fut_cards.append(
            f"""
            <div class="v50-fut-card" data-symbol="{html.escape(sym)}">
              <div class="v50-fut-head">
                <div>
                  <span class="v50-fut-title">{html.escape(meta['short_name'])}</span>
                  <span class="v50-fut-sym">{html.escape(sym)}</span>
                </div>
                <span class="v50-status-pill">{stag}</span>
              </div>
              <div class="v50-fut-price-row">
                <span class="v50-fut-price">{last_str}</span>
                <span class="v50-fut-pct" style="color:{p_col};">{pct_str}</span>
              </div>
              <div class="v50-fut-svg">{svg_html}</div>
              <div class="v50-fut-meta">
                <span>24H VWAP: <b>{vwap_str}</b> ({vdiff_str})</span>
                <span>5m RSI: <b>{rsi_str}</b></span>
                <span>24H 区间: <b>{rng_str}</b></span>
              </div>
            </div>
            """
        )

    mb_score = _safe_float(tp.get("macro_bubble_score"), 0.0) or 0.0
    ib_score = _safe_float(tp.get("intraday_bubble_score"), 0.0) or 0.0
    mbt_score = _safe_float(tp.get("macro_bottom_score"), 0.0) or 0.0
    ibt_score = _safe_float(tp.get("intraday_bottom_score"), 0.0) or 0.0

    src_tag = html.escape(str(tp.get("source_tag") or "期货实时"))
    pure_cnt = int(tp.get("pure_us_equity_count") or 0)
    mb_bd = tp.get("macro_bubble_breakdown") or {}
    ib_bd = tp.get("intraday_bubble_breakdown") or {}
    mbt_bd = tp.get("macro_bottom_breakdown") or {}
    ibt_bd = tp.get("intraday_bottom_breakdown") or {}

    cards_spec = [
        (
            "macro-bubble",
            "🔥 大盘整体泡沫评分",
            mb_score,
            str(tp.get("macro_bubble_tier") or "正常健康"),
            _color_var(str(tp.get("macro_bubble_color") or "neutral")),
            f"均线偏离 {mb_bd.get('trend_stretch', 0)} · VIX自满 {mb_bd.get('vix_complacency', 0)} · 纯美股宽度 {mb_bd.get('pure_breadth_ob', 0)} · SMH {mb_bd.get('smh_stretch', 0)}",
            "",
        ),
        (
            "intraday-bubble",
            f"⚡ 日内泡沫评分 ({src_tag})",
            ib_score,
            str(tp.get("intraday_bubble_tier") or "日内无追高泡沫"),
            _color_var(str(tp.get("intraday_bubble_color") or "neutral")),
            f"偏离VWAP {ib_bd.get('intra_vwap_diff_pct', 0):+.2f}% · 5m RSI {ib_bd.get('intra_rsi_5m', 50)} · 日内涨跌 {ib_bd.get('intra_pct', 0):+.2f}%",
            "",
        ),
        (
            "macro-bottom",
            "🧲 大盘整体抄底指数",
            mbt_score,
            str(tp.get("macro_bottom_tier") or "未到抄底区"),
            _color_var(str(tp.get("macro_bottom_color") or "neutral")),
            f"超卖深度 {mbt_bd.get('layer1_oversold', 0)}/60 · 右侧企稳 {mbt_bd.get('layer2_right_side', 0)}/40",
            '<span class="v50-lock-badge">🔒 硬接刀锁生效 (≤50)</span>' if tp.get("knife_catch_locked") else "",
        ),
        (
            "intraday-bottom",
            f"🎯 日内抄底指数 ({src_tag})",
            ibt_score,
            str(tp.get("intraday_bottom_tier") or "日内未见超卖抄底点"),
            _color_var(str(tp.get("intraday_bottom_color") or "neutral")),
            f"日内超卖 {ibt_bd.get('layer1_oversold', 0)}/60 · 5m企稳 {ibt_bd.get('layer2_right_side', 0)}/40",
            '<span class="v50-lock-badge">🔒 日内接刀锁生效 (≤50)</span>' if tp.get("intraday_knife_catch_locked") else "",
        ),
    ]

    temp_cards = []
    for key, title, score, tier, col, sub, lock_html in cards_spec:
        pct_w = max(2.0, min(100.0, float(score)))
        temp_cards.append(
            f"""
            <div class="v50-temp-card" data-temp-card="{key}">
              <div class="v50-temp-head">
                <span class="v50-temp-title">{title}</span>
                <span class="v50-temp-score" style="color:{col};">{score:.1f}<small>/100</small></span>
              </div>
              <div class="v50-temp-bar-bg">
                <div class="v50-temp-bar-fill" style="width:{pct_w:.1f}%;background:{col};"></div>
              </div>
              <div class="v50-temp-tier" style="color:{col};">{html.escape(tier)}</div>
              {lock_html}
              <div class="v50-temp-sub">{html.escape(sub)}</div>
            </div>
            """
        )

    headline = html.escape(str(tp.get("headline_cn") or ""))
    return f"""
    <style>
      .v50-stage {{ margin: 14px 0 18px; display: flex; flex-direction: column; gap: 14px; }}
      .v50-block {{ background: var(--bg-card, rgba(22,27,38,0.88)); border: 1px solid var(--border-subtle, rgba(148,163,184,0.18)); border-radius: 12px; padding: 14px 16px; }}
      .v50-block-hdr {{ display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 12px; }}
      .v50-block-title {{ font-size: 14px; font-weight: 700; color: var(--text-primary, #f1f5f9); letter-spacing: 0.2px; }}
      .v50-badge-row {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }}
      .v50-pure-badge {{ font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 999px; background: rgba(16,185,129,0.14); color: var(--color-up, #10b981); border: 1px solid rgba(16,185,129,0.35); }}
      .v50-status-pill {{ font-size: 11px; padding: 2px 8px; border-radius: 999px; background: rgba(56,189,248,0.12); color: var(--accent-blue, #38bdf8); border: 1px solid rgba(56,189,248,0.28); white-space: nowrap; }}
      .v50-fut-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }}
      .v50-fut-card {{ background: var(--bg-elevated, rgba(15,23,42,0.45)); border: 1px solid var(--border-subtle, rgba(148,163,184,0.16)); border-radius: 10px; padding: 12px 14px; min-width: 0; }}
      .v50-fut-head {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; }}
      .v50-fut-title {{ font-size: 13px; font-weight: 700; color: var(--text-primary, #f1f5f9); }}
      .v50-fut-sym {{ font-size: 11px; color: var(--text-muted, #94a3b8); margin-left: 6px; font-family: monospace; }}
      .v50-fut-price-row {{ display: flex; align-items: baseline; gap: 10px; margin-bottom: 6px; }}
      .v50-fut-price {{ font-size: 20px; font-weight: 800; color: var(--text-primary, #f1f5f9); font-variant-numeric: tabular-nums; }}
      .v50-fut-pct {{ font-size: 14px; font-weight: 700; font-variant-numeric: tabular-nums; }}
      .v50-fut-svg {{ margin: 4px 0 8px; }}
      .v50-fut-meta {{ display: flex; flex-wrap: wrap; gap: 8px 14px; font-size: 11px; color: var(--text-muted, #94a3b8); }}
      .v50-fut-meta b {{ color: var(--text-primary, #e2e8f0); font-weight: 600; }}
      .v50-temp-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
      .v50-temp-card {{ background: var(--bg-elevated, rgba(15,23,42,0.45)); border: 1px solid var(--border-subtle, rgba(148,163,184,0.16)); border-radius: 10px; padding: 12px 13px; display: flex; flex-direction: column; gap: 6px; min-width: 0; }}
      .v50-temp-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 6px; }}
      .v50-temp-title {{ font-size: 12px; font-weight: 700; color: var(--text-primary, #f1f5f9); }}
      .v50-temp-score {{ font-size: 20px; font-weight: 800; font-variant-numeric: tabular-nums; white-space: nowrap; }}
      .v50-temp-score small {{ font-size: 11px; font-weight: 500; opacity: 0.72; margin-left: 1px; }}
      .v50-temp-bar-bg {{ width: 100%; height: 7px; border-radius: 999px; background: rgba(148,163,184,0.18); overflow: hidden; }}
      .v50-temp-bar-fill {{ height: 100%; border-radius: 999px; transition: width 0.3s ease; }}
      .v50-temp-tier {{ font-size: 12px; font-weight: 700; line-height: 1.35; }}
      .v50-lock-badge {{ align-self: flex-start; font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 6px; background: rgba(239,68,68,0.16); color: var(--color-down, #ef4444); border: 1px solid rgba(239,68,68,0.38); }}
      .v50-temp-sub {{ font-size: 11px; color: var(--text-muted, #94a3b8); line-height: 1.4; }}
      .v50-headline {{ font-size: 12px; color: var(--text-secondary, #cbd5e1); margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--border-subtle, rgba(148,163,184,0.18)); }}
      @media (max-width: 960px) {{
        .v50-temp-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      }}
      @media (max-width: 520px) {{
        .v50-temp-grid {{ grid-template-columns: 1fr; }}
      }}
    </style>
    <section id="v50FuturesTempStage" class="v50-stage">
      <div class="v50-block" id="v50FuturesSentinelBlock">
        <div class="v50-block-hdr">
          <div class="v50-block-title">📡 【美股指期货先导哨】NQ=F &amp; ES=F 24H 实时走廊</div>
          <div class="v50-badge-row">
            <span class="v50-status-pill">仅作宏观先导 · 0 污染 79 标的池</span>
          </div>
        </div>
        <div class="v50-fut-grid">
          {''.join(fut_cards)}
        </div>
      </div>
      <div class="v50-block" id="v50TempDashboardBlock">
        <div class="v50-block-hdr">
          <div class="v50-block-title">🌡️ 【大盘温度计 · 泡沫与抄底四维仪表】</div>
          <div class="v50-badge-row">
            <span class="v50-pure-badge">纯美股口径 · 不含BTC（样本 {pure_cnt} 只）</span>
            <span class="v50-status-pill">日内数据源：{src_tag}</span>
          </div>
        </div>
        <div class="v50-temp-grid">
          {''.join(temp_cards)}
        </div>
        <div class="v50-headline">💬 温度计结论：{headline}</div>
      </div>
    </section>
    """
