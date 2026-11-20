# -*- coding: utf-8 -*-
"""
阶段二核心引擎：开盘前 3~5 分钟「当日盘中预测走势走廊图」与 09:45 ET 实时背离纠偏 (intraday_path_predictor.py)

核心保障：
1. 严格零未来函数穿越：历史 KNN 匹配仅使用 date < forecast_date 的已完整收盘 5m K 线，绝不混入当日数据。
2. 60 日 5m 历史底模本地 Parquet 缓存：仅盘前/收盘后各增量刷新一次，盘中 60 秒轮询 0 次全量重下，保护限速。
3. 美东 09:25 ET 快照锁定防篡改：一旦锁定，盘中只增量追加 actual_prices 与 09:45 ET 背离状态，不改写盘前 P25/P50/P75 走廊。
4. 次新股 (<10 日，如 ANT) 与 7×24h 连续品种 (BTC-USD、XAUUSD) 优雅降级不报错。
"""

import argparse
import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

try:
    import zoneinfo
    _NY_TZ = zoneinfo.ZoneInfo("America/New_York")
except Exception:
    _NY_TZ = timezone(timedelta(hours=-5))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARQUET_CACHE_FILE = "data_cache/intraday_5m_60d.parquet"
META_CACHE_FILE = "data_cache/.intraday_5m_60d_meta.json"
FORECAST_JSON_FILE = "data_cache/intraday_path_forecasts.json"

CONTINUOUS_24H_TICKERS = frozenset({"BTC-USD", "XAUUSD"})
MIN_HISTORY_DAYS = 10
MIN_RTH_BARS_PER_DAY = 50
REGIME_SCORE_MAP = {"BEAR_DEFENSE": -1.0, "NEUTRAL_CHOP": 0.0, "BULL_TREND": 1.0}


def _build_rth_time_slots() -> List[str]:
    """生成美股常规交易时段 09:30 ~ 15:55 共 78 个 5 分钟时间槽。"""
    slots: List[str] = []
    cur_min = 9 * 60 + 30
    end_min = 16 * 60
    while cur_min < end_min:
        h, m = divmod(cur_min, 60)
        slots.append(f"{h:02d}:{m:02d}")
        cur_min += 5
    return slots


TIME_SLOTS: List[str] = _build_rth_time_slots()
SLOT_TO_INDEX: Dict[str, int] = {s: idx for idx, s in enumerate(TIME_SLOTS)}


def _resolve_path(path_str: str) -> str:
    if os.path.isabs(path_str):
        return path_str
    return os.path.join(BASE_DIR, path_str)


def _to_et_datetime(now_et: Optional[datetime] = None) -> datetime:
    if now_et is None:
        return datetime.now(_NY_TZ)
    if now_et.tzinfo is None:
        try:
            return now_et.replace(tzinfo=_NY_TZ)
        except Exception:
            return now_et
    return now_et.astimezone(_NY_TZ)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default


def should_refresh_60d_cache(
    cache_path: str = PARQUET_CACHE_FILE,
    meta_path: str = META_CACHE_FILE,
    now_et: Optional[datetime] = None,
    force_refresh: bool = False,
) -> bool:
    """
    判定是否需要拉取全池 60 天 5 分钟底模：
    - 缺失文件或强制刷新 -> True
    - 工作日盘前 (04:00~09:27 ET) 尚未今日刷新 -> True
    - 工作日收盘后 (>=16:15 ET) 尚未今日盘后刷新 -> True
    - 常规盘中 (09:28~16:14 ET) 及同一窗口重复轮询 -> False (60秒守护零全量重下)
    """
    if force_refresh:
        return True
    full_cache = _resolve_path(cache_path)
    full_meta = _resolve_path(meta_path)
    if not os.path.exists(full_cache):
        return True

    cur_et = _to_et_datetime(now_et)
    if cur_et.weekday() >= 5:
        return False

    meta: Dict[str, Any] = {}
    if os.path.exists(full_meta):
        try:
            with open(full_meta, "r", encoding="utf-8") as f:
                meta = json.load(f) or {}
        except Exception:
            meta = {}

    today_str = cur_et.strftime("%Y-%m-%d")
    last_date = str(meta.get("last_refresh_date", ""))
    last_slot = str(meta.get("last_refresh_slot", ""))
    cur_min = cur_et.hour * 60 + cur_et.minute

    if 4 * 60 <= cur_min < 9 * 60 + 28:
        return not (last_date == today_str and last_slot in ("premarket", "postclose"))
    if cur_min >= 16 * 60 + 15:
        return not (last_date == today_str and last_slot == "postclose")
    return False


def _normalize_5m_timestamps_to_ny_naive(idx: pd.Index) -> pd.DatetimeIndex:
    dt_idx = pd.to_datetime(idx)
    if dt_idx.tz is None:
        # yfinance 对美股偶尔返回本地无时区或 UTC，若未带时区按纽约时钟保留，若带时区则转纽约壁钟去时区
        return dt_idx
    return dt_idx.tz_convert(_NY_TZ).tz_localize(None)


def _extract_ticker_df_from_yf(raw_df: pd.DataFrame, ticker: str, is_single: bool) -> Optional[pd.DataFrame]:
    if raw_df is None or raw_df.empty:
        return None
    try:
        if isinstance(raw_df.columns, pd.MultiIndex):
            lvl0 = set(raw_df.columns.get_level_values(0))
            lvl1 = set(raw_df.columns.get_level_values(1))
            if ticker in lvl0:
                sub = raw_df[ticker].copy()
            elif ticker in lvl1:
                sub = raw_df.xs(ticker, level=1, axis=1).copy()
            else:
                return None
        elif is_single:
            sub = raw_df.copy()
        else:
            return None

        need_cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in sub.columns]
        if "Close" not in need_cols:
            return None
        sub = sub[need_cols].dropna(subset=["Close"]).copy()
        if sub.empty:
            return None
        for col in ("Open", "High", "Low"):
            if col not in sub.columns:
                sub[col] = sub["Close"]
            else:
                sub[col] = pd.to_numeric(sub[col], errors="coerce").fillna(pd.to_numeric(sub["Close"], errors="coerce"))
        sub["Close"] = pd.to_numeric(sub["Close"], errors="coerce")
        sub["Volume"] = pd.to_numeric(sub["Volume"], errors="coerce").fillna(0.0) if "Volume" in sub.columns else 0.0
        sub = sub.dropna(subset=["Close"])
        if sub.empty:
            return None

        ny_dt = _normalize_5m_timestamps_to_ny_naive(sub.index)
        out = pd.DataFrame({
            "Datetime": ny_dt,
            "Ticker": str(ticker),
            "Open": sub["Open"].astype(float).values,
            "High": sub["High"].astype(float).values,
            "Low": sub["Low"].astype(float).values,
            "Close": sub["Close"].astype(float).values,
            "Volume": sub["Volume"].astype(float).values,
        })
        return out
    except Exception:
        return None


def refresh_intraday_5m_60d_cache(
    tickers: List[str],
    cache_path: str = PARQUET_CACHE_FILE,
    meta_path: str = META_CACHE_FILE,
    now_et: Optional[datetime] = None,
    force_refresh: bool = False,
    downloader: Optional[Callable[..., pd.DataFrame]] = None,
) -> pd.DataFrame:
    """
    获取或加载全池近 60 个交易日 5m 分时数据缓存 (含盘前盘后)。
    盘中若命中今日有效缓存，直接读盘返回，0 网络请求。
    """
    full_cache = _resolve_path(cache_path)
    full_meta = _resolve_path(meta_path)
    empty_cols = ["Datetime", "Ticker", "Open", "High", "Low", "Close", "Volume"]

    if not should_refresh_60d_cache(cache_path=cache_path, meta_path=meta_path, now_et=now_et, force_refresh=force_refresh):
        if os.path.exists(full_cache):
            try:
                cached = pd.read_parquet(full_cache)
                if not cached.empty and "Datetime" in cached.columns:
                    cached["Datetime"] = pd.to_datetime(cached["Datetime"])
                    return cached
            except Exception:
                pass
        return pd.DataFrame(columns=empty_cols)

    dl_fn = downloader or yf.download
    dl_tickers = [str(t) for t in dict.fromkeys(tickers or []) if str(t) not in CONTINUOUS_24H_TICKERS]
    collected: List[pd.DataFrame] = []

    chunk_size = 20
    for start_idx in range(0, len(dl_tickers), chunk_size):
        chunk = dl_tickers[start_idx:start_idx + chunk_size]
        raw = None
        try:
            raw = dl_fn(
                chunk if len(chunk) > 1 else chunk[0],
                period="60d",
                interval="5m",
                prepost=True,
                group_by="ticker",
                progress=False,
                auto_adjust=True,
                timeout=20,
            )
        except Exception:
            raw = None

        if raw is not None and not raw.empty:
            for tk in chunk:
                parsed = _extract_ticker_df_from_yf(raw, tk, is_single=(len(chunk) == 1))
                if parsed is not None and not parsed.empty:
                    collected.append(parsed)
        else:
            # 单批次失败时逐只降级抓取，确保单票 IPO/退市缺失绝不阻断其余标的
            for tk in chunk:
                try:
                    raw_one = dl_fn(
                        tk,
                        period="60d",
                        interval="5m",
                        prepost=True,
                        progress=False,
                        auto_adjust=True,
                        timeout=15,
                    )
                    parsed = _extract_ticker_df_from_yf(raw_one, tk, is_single=True)
                    if parsed is not None and not parsed.empty:
                        collected.append(parsed)
                except Exception:
                    continue

    existing_df = pd.DataFrame(columns=empty_cols)
    if os.path.exists(full_cache):
        try:
            existing_df = pd.read_parquet(full_cache)
        except Exception:
            existing_df = pd.DataFrame(columns=empty_cols)

    frames = [df for df in ([existing_df] + collected) if df is not None and not df.empty]
    if not frames:
        return pd.DataFrame(columns=empty_cols)

    merged = pd.concat(frames, ignore_index=True)
    merged["Datetime"] = pd.to_datetime(merged["Datetime"]).dt.tz_localize(None)
    merged["Ticker"] = merged["Ticker"].astype(str)
    merged = merged.sort_values(["Ticker", "Datetime"]).drop_duplicates(subset=["Ticker", "Datetime"], keep="last")

    # 裁剪至最近 72 个自然日，防止 Parquet 无限膨胀
    max_dt = merged["Datetime"].max()
    if pd.notna(max_dt):
        cutoff_dt = max_dt - pd.Timedelta(days=72)
        merged = merged[merged["Datetime"] >= cutoff_dt].reset_index(drop=True)

    os.makedirs(os.path.dirname(full_cache), exist_ok=True)
    merged.to_parquet(full_cache, index=False)

    cur_et = _to_et_datetime(now_et)
    cur_min = cur_et.hour * 60 + cur_et.minute
    slot_tag = "postclose" if cur_min >= 16 * 60 + 15 else ("premarket" if cur_min < 9 * 60 + 30 else "intraday")
    try:
        os.makedirs(os.path.dirname(full_meta), exist_ok=True)
        with open(full_meta, "w", encoding="utf-8") as mf:
            json.dump({
                "last_refresh_date": cur_et.strftime("%Y-%m-%d"),
                "last_refresh_slot": slot_tag,
                "updated_at_et": cur_et.strftime("%Y-%m-%d %H:%M:%S"),
                "tickers_count": int(merged["Ticker"].nunique()),
                "rows_count": int(len(merged)),
            }, mf, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return merged


def _build_qqq_context_maps(
    qqq_df: Optional[pd.DataFrame],
    forecast_date: date,
) -> Tuple[Dict[date, float], Dict[date, float]]:
    """从 QQQ 历史 5m 构建每日跳空幅度与截至前一日的体质倾向得分，严格隔离 >= forecast_date 数据。"""
    gap_map: Dict[date, float] = {}
    regime_map: Dict[date, float] = {}
    if qqq_df is None or qqq_df.empty:
        return gap_map, regime_map

    df = qqq_df.copy()
    df["Datetime"] = pd.to_datetime(df["Datetime"])
    df["_date"] = df["Datetime"].dt.date
    df = df[df["_date"] < forecast_date].copy()
    if df.empty:
        return gap_map, regime_map

    df["_min"] = df["Datetime"].dt.hour * 60 + df["Datetime"].dt.minute
    rth = df[(df["_min"] >= 9 * 60 + 30) & (df["_min"] < 16 * 60)].sort_values("Datetime")
    if rth.empty:
        return gap_map, regime_map

    daily_open: Dict[date, float] = {}
    daily_close: Dict[date, float] = {}
    for d, grp in rth.groupby("_date", sort=True):
        if len(grp) >= 20:
            daily_open[d] = float(grp["Open"].iloc[0])
            daily_close[d] = float(grp["Close"].iloc[-1])

    sorted_days = sorted(daily_close.keys())
    closes_hist: List[float] = []
    for idx, d in enumerate(sorted_days):
        if idx > 0:
            pc = daily_close[sorted_days[idx - 1]]
            op = daily_open[d]
            if pc > 0 and op > 0:
                gap_map[d] = ((op - pc) / pc) * 100.0
            else:
                gap_map[d] = 0.0
        else:
            gap_map[d] = 0.0

        # T 日盘前体制分仅使用 <= T-1 日收盘价
        if len(closes_hist) >= 20:
            ma20 = float(np.mean(closes_hist[-20:]))
            bias20 = ((closes_hist[-1] - ma20) / ma20) * 100.0 if ma20 > 0 else 0.0
            regime_map[d] = -1.0 if bias20 <= -1.2 else (1.0 if bias20 >= 0.8 else 0.0)
        elif len(closes_hist) >= 5:
            ma5 = float(np.mean(closes_hist[-5:]))
            bias5 = ((closes_hist[-1] - ma5) / ma5) * 100.0 if ma5 > 0 else 0.0
            regime_map[d] = -1.0 if bias5 <= -1.0 else (1.0 if bias5 >= 0.8 else 0.0)
        else:
            regime_map[d] = 0.0

        closes_hist.append(daily_close[d])

    return gap_map, regime_map


def _align_day_rth_bars_to_slots(day_rth: pd.DataFrame) -> Tuple[np.ndarray, float, float]:
    """将单日 09:30~15:55 RTH 5m K 线映射到 78 个标准时间槽并前向填充偶发缺口。"""
    arr = np.full(len(TIME_SLOTS), np.nan, dtype=float)
    rth_sorted = day_rth.sort_values("Datetime")
    anchor_open = float(rth_sorted["Open"].iloc[0])
    if anchor_open <= 0 or not math.isfinite(anchor_open):
        anchor_open = float(rth_sorted["Close"].iloc[0])
    rth_close = float(rth_sorted["Close"].iloc[-1])

    for dt_val, c_val in zip(rth_sorted["Datetime"], rth_sorted["Close"]):
        if not math.isfinite(c_val) or c_val <= 0:
            continue
        cur_min = int(dt_val.hour) * 60 + int(dt_val.minute)
        slot_idx = (cur_min - 570) // 5
        if 0 <= slot_idx < len(TIME_SLOTS):
            arr[slot_idx] = float(c_val)

    s = pd.Series(arr).ffill().bfill()
    return s.to_numpy(dtype=float), anchor_open, rth_close


def extract_historical_day_profiles(
    df_tk: Optional[pd.DataFrame],
    forecast_date: Any,
    qqq_gap_by_date: Optional[Dict[date, float]] = None,
    qqq_regime_by_date: Optional[Dict[date, float]] = None,
) -> List[Dict[str, Any]]:
    """
    提取严格早于 forecast_date 的历史完整交易日日内走势样本 (Zero Lookahead)。
    """
    if df_tk is None or df_tk.empty:
        return []
    f_date = pd.Timestamp(forecast_date).date()

    df = df_tk.copy()
    df["Datetime"] = pd.to_datetime(df["Datetime"]).dt.tz_localize(None)
    df["_date"] = df["Datetime"].dt.date
    # 红线：在进入任何分组和统计之前彻底剥离 >= forecast_date 的未来数据
    df = df[df["_date"] < f_date].copy()
    if df.empty:
        return []

    df["_min"] = df["Datetime"].dt.hour * 60 + df["Datetime"].dt.minute
    rth = df[(df["_min"] >= 9 * 60 + 30) & (df["_min"] < 16 * 60)].sort_values("Datetime")
    if rth.empty:
        return []

    raw_days: List[Tuple[date, np.ndarray, float, float]] = []
    for d, grp in rth.groupby("_date", sort=True):
        if len(grp) < MIN_RTH_BARS_PER_DAY:
            continue
        slot_closes, anchor_open, rth_close = _align_day_rth_bars_to_slots(grp)
        if anchor_open <= 0 or rth_close <= 0 or np.isnan(slot_closes).any():
            continue
        raw_days.append((d, slot_closes, anchor_open, rth_close))

    if len(raw_days) < 2:
        return []

    profiles: List[Dict[str, Any]] = []
    prev_closes_list: List[float] = [raw_days[0][3]]
    for idx in range(1, len(raw_days)):
        d, slot_closes, anchor_open, rth_close = raw_days[idx]
        prev_c = raw_days[idx - 1][3]
        if prev_c <= 0:
            prev_closes_list.append(rth_close)
            continue

        gap_pct = ((anchor_open - prev_c) / prev_c) * 100.0
        qqq_gap = float((qqq_gap_by_date or {}).get(d, gap_pct * 0.5))
        reg_sc = float((qqq_regime_by_date or {}).get(d, 0.0))

        # 仅利用截至前一日收盘的历史收益率标准差衡量前 5 日波动率
        if len(prev_closes_list) >= 3:
            tail_c = np.asarray(prev_closes_list[-6:], dtype=float)
            rets = np.diff(tail_c) / np.maximum(tail_c[:-1], 1e-9) * 100.0
            vol_5d = float(np.std(rets)) if len(rets) >= 2 else abs(float(rets[-1]))
        else:
            vol_5d = 1.5
        vol_5d = float(np.clip(vol_5d, 0.2, 15.0))

        rel_path = ((slot_closes - anchor_open) / anchor_open) * 100.0
        close_ret = float(rel_path[-1])
        early_peak = float(np.max(rel_path[:18]))  # 09:30 ~ 10:55 ET
        early_trough = float(np.min(rel_path[:18]))

        early_high_fade = bool(early_peak >= 0.40 and close_ret <= early_peak - 0.50)
        early_dip_rebound = bool(early_trough <= -0.40 and close_ret >= early_trough + 0.50)
        trend_up = bool(close_ret >= 0.50 and not early_high_fade)
        trend_down = bool(close_ret <= -0.50 and not early_dip_rebound)

        profiles.append({
            "date": d.strftime("%Y-%m-%d"),
            "date_obj": d,
            "prev_close": round(prev_c, 4),
            "open": round(anchor_open, 4),
            "close": round(rth_close, 4),
            "gap_pct": float(gap_pct),
            "qqq_gap_pct": float(qqq_gap),
            "vol_5d": float(vol_5d),
            "regime_score": float(reg_sc),
            "rel_path": rel_path,
            "close_ret_pct": close_ret,
            "early_high_fade": early_high_fade,
            "early_dip_rebound": early_dip_rebound,
            "trend_up": trend_up,
            "trend_down": trend_down,
        })
        prev_closes_list.append(rth_close)

    return profiles


def _format_pattern_summary(neighbors: List[Dict[str, Any]], median_close_ret: float, gap_pct: float) -> Tuple[str, Dict[str, Any]]:
    k_cnt = len(neighbors)
    fade_cnt = sum(1 for x in neighbors if x["early_high_fade"])
    reb_cnt = sum(1 for x in neighbors if x["early_dip_rebound"])
    up_cnt = sum(1 for x in neighbors if x["trend_up"])
    dn_cnt = sum(1 for x in neighbors if x["trend_down"])

    candidates = [
        ("出现早盘冲高回落", fade_cnt),
        ("出现早盘下探后 V 型回升", reb_cnt),
        ("呈顺风单边推涨", up_cnt),
        ("呈弱势震荡阴跌", dn_cnt),
    ]
    best_label, best_cnt = max(candidates, key=lambda kv: kv[1])
    pct_share = round((best_cnt / max(1, k_cnt)) * 100)
    if pct_share < 30:
        best_label = "全天呈窄幅均衡拉锯"
        pct_share = max(pct_share, round((sum(1 for x in neighbors if abs(x["close_ret_pct"]) < 1.2) / max(1, k_cnt)) * 100))

    gap_tag = f"盘前跳空 {gap_pct:+.2f}%" if abs(gap_pct) >= 0.1 else "平开开盘"
    summary_text = f"历史匹配 {k_cnt} 个相似开盘日（{gap_tag}）：{pct_share}% {best_label}，中位数收盘 {median_close_ret:+.1f}%"
    stats = {
        "k_matched": k_cnt,
        "early_high_fade_pct": round(fade_cnt / max(1, k_cnt) * 100.0, 1),
        "early_dip_rebound_pct": round(reb_cnt / max(1, k_cnt) * 100.0, 1),
        "trend_up_pct": round(up_cnt / max(1, k_cnt) * 100.0, 1),
        "trend_down_pct": round(dn_cnt / max(1, k_cnt) * 100.0, 1),
        "median_close_ret_pct": round(float(median_close_ret), 2),
    }
    return summary_text, stats


def compute_knn_intraday_corridor(
    ticker: str,
    df_tk: Optional[pd.DataFrame],
    forecast_date: Any,
    prev_close: float,
    anchor_open: float,
    pmh: Optional[float] = None,
    pml: Optional[float] = None,
    today_qqq_gap_pct: float = 0.0,
    today_regime_score: float = 0.0,
    qqq_gap_by_date: Optional[Dict[date, float]] = None,
    qqq_regime_by_date: Optional[Dict[date, float]] = None,
    k_neighbors: int = 12,
    today_vol_5d: Optional[float] = None,
) -> Dict[str, Any]:
    """
    计算单个标的在 forecast_date 的 KNN 历史相似日盘中走廊 (P25 / P50 / P75)。
    """
    f_date_str = pd.Timestamp(forecast_date).strftime("%Y-%m-%d")
    tk = str(ticker)
    pc_val = round(_safe_float(prev_close, 0.0), 2)
    ao_val = round(_safe_float(anchor_open, pc_val), 2)
    if ao_val <= 0 and pc_val > 0:
        ao_val = pc_val
    pmh_val = round(_safe_float(pmh, ao_val), 2) if (_safe_float(pmh, 0.0) > 0) else ao_val
    pml_val = round(_safe_float(pml, ao_val), 2) if (_safe_float(pml, 0.0) > 0) else ao_val
    if pmh_val < pml_val:
        pmh_val, pml_val = pml_val, pmh_val
    gap_pct = round(((ao_val - pc_val) / pc_val) * 100.0, 2) if (pc_val > 0 and ao_val > 0) else 0.0

    base_item: Dict[str, Any] = {
        "ticker": tk,
        "forecast_date": f_date_str,
        "prev_close": pc_val,
        "anchor_open": ao_val,
        "pmh": pmh_val,
        "pml": pml_val,
        "gap_pct": gap_pct,
        "qqq_gap_pct": round(_safe_float(today_qqq_gap_pct, 0.0), 2),
        "regime_score": round(_safe_float(today_regime_score, 0.0), 1),
        "p25_prices": [],
        "p50_prices": [],
        "p75_prices": [],
        "p25_pcts": [],
        "p50_pcts": [],
        "p75_pcts": [],
        "actual_prices": [None] * len(TIME_SLOTS),
        "actual_pcts": [None] * len(TIME_SLOTS),
        "matched_dates": [],
        "summary_text": "",
        "pattern_stats": {},
        "latest_slot": None,
        "snapshot_locked": False,
        "lock_window_tag": "DRAFT",
    }

    if tk in CONTINUOUS_24H_TICKERS:
        base_item.update({
            "status": "continuous_24h",
            "status_message": "7×24h 连续交易品种，以日内 VWAP 与 4H 筹码为准",
            "summary_text": "7×24h 连续交易品种，以日内 VWAP 与 4H 筹码为准",
            "divergence": {
                "status": "CONTINUOUS_24H",
                "badge": "🌐 7×24h 连续交易 · 锚定日内 VWAP",
                "color": "#fbbf24",
                "latest_slot": None,
            },
        })
        return base_item

    profiles = extract_historical_day_profiles(
        df_tk=df_tk,
        forecast_date=forecast_date,
        qqq_gap_by_date=qqq_gap_by_date,
        qqq_regime_by_date=qqq_regime_by_date,
    )
    if len(profiles) < MIN_HISTORY_DAYS or ao_val <= 0:
        msg = f"新股分时样本不足（当前 {len(profiles)}/{MIN_HISTORY_DAYS} 日），积累 {MIN_HISTORY_DAYS} 个交易日后自动开启"
        base_item.update({
            "status": "insufficient_history",
            "history_days_count": len(profiles),
            "status_message": "新股分时样本不足，积累 10 个交易日后自动开启",
            "summary_text": msg,
            "divergence": {
                "status": "INSUFFICIENT_HISTORY",
                "badge": "🐣 新股样本积累中 · 满10日开启走廊",
                "color": "#9ca3af",
                "latest_slot": None,
            },
        })
        return base_item

    if today_vol_5d is None or not math.isfinite(float(today_vol_5d)):
        vol_ref = float(np.mean([p["vol_5d"] for p in profiles[-5:]]))
    else:
        vol_ref = float(today_vol_5d)

    feat_mat = np.array(
        [[p["gap_pct"], p["qqq_gap_pct"], p["vol_5d"], p["regime_score"]] for p in profiles],
        dtype=float,
    )
    target_vec = np.array([gap_pct, float(today_qqq_gap_pct), vol_ref, float(today_regime_score)], dtype=float)
    stds = np.std(feat_mat, axis=0)
    stds = np.where(stds < 0.25, 0.25, stds)
    weights = np.array([1.6, 1.1, 0.7, 0.8], dtype=float)

    norm_diff = ((feat_mat - target_vec) / stds) * weights
    dists = np.sqrt(np.sum(norm_diff ** 2, axis=1))

    # 强化同向跳空先验：显著高开/低开日优先匹配同方向历史样本
    if abs(gap_pct) >= 0.35:
        same_dir = (feat_mat[:, 0] * gap_pct) > 0
        dists = dists + np.where(same_dir, 0.0, 1.5)
    if abs(today_qqq_gap_pct) >= 0.25:
        same_q_dir = (feat_mat[:, 1] * today_qqq_gap_pct) > 0
        dists = dists + np.where(same_q_dir, 0.0, 0.8)

    k_use = min(int(k_neighbors), len(profiles))
    top_indices = np.argsort(dists)[:k_use]
    neighbors = [profiles[int(i)] for i in top_indices]

    paths = np.stack([n["rel_path"] for n in neighbors], axis=0)
    p25_pcts = np.percentile(paths, 25, axis=0)
    p50_pcts = np.percentile(paths, 50, axis=0)
    p75_pcts = np.percentile(paths, 75, axis=0)

    p25_prices = [round(ao_val * (1.0 + float(x) / 100.0), 2) for x in p25_pcts]
    p50_prices = [round(ao_val * (1.0 + float(x) / 100.0), 2) for x in p50_pcts]
    p75_prices = [round(ao_val * (1.0 + float(x) / 100.0), 2) for x in p75_pcts]

    summary_text, pattern_stats = _format_pattern_summary(neighbors, float(p50_pcts[-1]), gap_pct)
    matched_dates = sorted([n["date"] for n in neighbors])

    base_item.update({
        "status": "ok",
        "status_message": "",
        "history_days_count": len(profiles),
        "p25_prices": p25_prices,
        "p50_prices": p50_prices,
        "p75_prices": p75_prices,
        "p25_pcts": [round(float(x), 3) for x in p25_pcts],
        "p50_pcts": [round(float(x), 3) for x in p50_pcts],
        "p75_pcts": [round(float(x), 3) for x in p75_pcts],
        "matched_dates": matched_dates,
        "summary_text": summary_text,
        "pattern_stats": pattern_stats,
        "divergence": {
            "status": "PRE_OPEN",
            "badge": "⏳ 等待 09:30 ET 开盘锚定走势",
            "color": "#9ca3af",
            "latest_slot": None,
        },
    })
    return base_item


def evaluate_intraday_divergence(
    forecast_item: Dict[str, Any],
    actual_prices: Optional[List[Optional[float]]],
    now_et: Optional[datetime] = None,
    force_slot_idx: Optional[int] = None,
) -> Dict[str, Any]:
    """
    09:45 ET 起 (slot_idx >= 3) 判定当日实际分时与盘前 P25~P75 预测走廊的实时背离状态。
    """
    status_cur = str(forecast_item.get("status", ""))
    if status_cur == "continuous_24h":
        return {
            "status": "CONTINUOUS_24H",
            "badge": "🌐 7×24h 连续交易 · 锚定日内 VWAP",
            "color": "#fbbf24",
            "latest_slot": None,
        }
    if status_cur != "ok":
        return {
            "status": "INSUFFICIENT_HISTORY",
            "badge": "🐣 新股样本积累中 · 满10日开启走廊",
            "color": "#9ca3af",
            "latest_slot": None,
        }

    p25 = forecast_item.get("p25_prices") or []
    p50 = forecast_item.get("p50_prices") or []
    p75 = forecast_item.get("p75_prices") or []
    anchor_open = _safe_float(forecast_item.get("anchor_open"), 0.0)
    if not p25 or not p75 or not actual_prices or anchor_open <= 0:
        return {
            "status": "PRE_OPEN",
            "badge": "⏳ 等待 09:30 ET 开盘锚定走势",
            "color": "#9ca3af",
            "latest_slot": None,
        }

    latest_idx: Optional[int] = None
    if force_slot_idx is not None and 0 <= force_slot_idx < len(actual_prices):
        val = actual_prices[force_slot_idx]
        if val is not None and _safe_float(val, 0.0) > 0:
            latest_idx = int(force_slot_idx)
    if latest_idx is None:
        for i in range(min(len(actual_prices), len(p25)) - 1, -1, -1):
            v = actual_prices[i]
            if v is not None and _safe_float(v, 0.0) > 0:
                latest_idx = i
                break

    if latest_idx is None:
        return {
            "status": "PRE_OPEN",
            "badge": "⏳ 等待 09:30 ET 开盘锚定走势",
            "color": "#9ca3af",
            "latest_slot": None,
        }

    # 美东 09:45 ET 为第 4 个 5m 槽 (idx == 3: 09:30, 09:35, 09:40 -> idx 0,1,2 属于前 15 分钟观察窗)
    if latest_idx < 3:
        return {
            "status": "OPENING_WINDOW",
            "badge": "⏳ 开盘前15分钟观察窗 (09:45 ET 启动背离纠偏)",
            "color": "#9ca3af",
            "latest_slot": latest_idx,
            "latest_time": TIME_SLOTS[latest_idx],
            "actual_price": round(float(actual_prices[latest_idx]), 2),
        }

    cur_px = float(actual_prices[latest_idx])
    low_band = float(p25[latest_idx])
    mid_band = float(p50[latest_idx]) if latest_idx < len(p50) else round((low_band + float(p75[latest_idx])) / 2.0, 2)
    high_band = float(p75[latest_idx])
    corridor_w = max(0.0, high_band - low_band)
    buf = max(anchor_open * 0.0020, corridor_w * 0.08)

    pct_vs_p50 = round(((cur_px - mid_band) / mid_band) * 100.0, 2) if mid_band > 0 else 0.0

    if cur_px < (low_band - buf):
        div_status = "BELOW_P25"
        badge = "⚠️ 跌破盘前剧本下沿 · 警惕高开低走/加速杀跌"
        color = "#ff5252"
    elif cur_px > (high_band + buf):
        div_status = "ABOVE_P75"
        badge = "🚀 突破盘前剧本上沿 · 多头超预期走强"
        color = "#00e676"
    else:
        div_status = "IN_CORRIDOR"
        badge = "✅ 走势贴合盘前推演走廊"
        color = "#60a5fa"

    return {
        "status": div_status,
        "badge": badge,
        "color": color,
        "latest_slot": latest_idx,
        "latest_time": TIME_SLOTS[latest_idx],
        "actual_price": round(cur_px, 2),
        "slot_p25": round(low_band, 2),
        "slot_p50": round(mid_band, 2),
        "slot_p75": round(high_band, 2),
        "pct_vs_p50": pct_vs_p50,
    }


def _resolve_target_date_and_lock(
    df_60d: pd.DataFrame,
    existing_doc: Dict[str, Any],
    cur_et: datetime,
) -> Tuple[date, bool, str]:
    """
    判定当前推演目标交易日及锁定模式：
    - 交易日 04:00~15:59 ET: 目标日为当日 ET; 09:25 起自动永久锁定当日盘前剧本。
    - 盘后 (>=16:00 ET) 或周末首次初始化时：优先沿用已锁定快照，否则取 60d 底模最新已完成交易日立即生成可用走廊。
    """
    cur_min = cur_et.hour * 60 + cur_et.minute
    is_weekday = cur_et.weekday() < 5

    if is_weekday and (4 * 60 <= cur_min < 16 * 60):
        target_d = cur_et.date()
        if 9 * 60 + 25 <= cur_min < 9 * 60 + 30:
            return target_d, True, "PREOPEN_0925_LOCK"
        if cur_min >= 9 * 60 + 30:
            return target_d, True, "INTRADAY_LOCKED"
        return target_d, False, "PREOPEN_PREVIEW"

    # 非盘中时段 (盘后/夜盘/周末)
    existing_date_str = str(existing_doc.get("forecast_date", "") or "")
    if existing_date_str:
        try:
            ex_d = pd.Timestamp(existing_date_str).date()
            if ex_d == cur_et.date() or not is_weekday:
                return ex_d, True, "POST_MARKET_LOCKED"
        except Exception:
            pass

    if df_60d is not None and not df_60d.empty and "Datetime" in df_60d.columns:
        dts = pd.to_datetime(df_60d["Datetime"]).dt.tz_localize(None)
        mins = dts.dt.hour * 60 + dts.dt.minute
        rth_dts = dts[(mins >= 9 * 60 + 30) & (mins < 16 * 60)]
        if not rth_dts.empty:
            latest_d = rth_dts.max().date()
            return latest_d, True, "LATEST_SESSION_SNAPSHOT"

    fallback_d = cur_et.date()
    while fallback_d.weekday() >= 5:
        fallback_d -= timedelta(days=1)
    return fallback_d, True, "LATEST_SESSION_SNAPSHOT"


def _extract_day_features_for_target(
    tk: str,
    df_tk: Optional[pd.DataFrame],
    target_date: date,
    intraday_info: Dict[str, Any],
    history_df: Optional[pd.DataFrame],
    rq: Optional[Dict[str, Any]],
    cur_et: datetime,
) -> Tuple[float, float, Optional[float], Optional[float], List[Optional[float]]]:
    """
    提取目标日 prev_close, anchor_open, pmh, pml 与已有当日 RTH 实盘分时序列 actual_prices。
    """
    target_str = target_date.strftime("%Y-%m-%d")
    actual_prices: List[Optional[float]] = [None] * len(TIME_SLOTS)

    prev_close = 0.0
    anchor_open = 0.0
    pmh: Optional[float] = None
    pml: Optional[float] = None

    # 1. 从 60d 5m 本地底模提取严格 < target_date 的前收盘价，以及 target_date 本身的盘前/RTH 5m
    if df_tk is not None and not df_tk.empty:
        sub = df_tk.copy()
        sub["Datetime"] = pd.to_datetime(sub["Datetime"]).dt.tz_localize(None)
        sub["_date"] = sub["Datetime"].dt.date
        sub["_min"] = sub["Datetime"].dt.hour * 60 + sub["Datetime"].dt.minute

        hist_rth = sub[(sub["_date"] < target_date) & (sub["_min"] >= 9 * 60 + 30) & (sub["_min"] < 16 * 60)].sort_values("Datetime")
        if not hist_rth.empty:
            prev_close = float(hist_rth["Close"].iloc[-1])

        tgt_pre = sub[(sub["_date"] == target_date) & (sub["_min"] >= 4 * 60) & (sub["_min"] < 9 * 60 + 30)]
        if not tgt_pre.empty:
            pmh = float(tgt_pre["High"].max())
            pml = float(tgt_pre["Low"].min())
            anchor_open = float(tgt_pre["Close"].iloc[-1])

        tgt_rth = sub[(sub["_date"] == target_date) & (sub["_min"] >= 9 * 60 + 30) & (sub["_min"] < 16 * 60)].sort_values("Datetime")
        if not tgt_rth.empty:
            anchor_open = float(tgt_rth["Open"].iloc[0])
            for dt_val, c_val in zip(tgt_rth["Datetime"], tgt_rth["Close"]):
                if math.isfinite(float(c_val)) and float(c_val) > 0:
                    s_idx = (int(dt_val.hour) * 60 + int(dt_val.minute) - 570) // 5
                    if 0 <= s_idx < len(TIME_SLOTS):
                        actual_prices[s_idx] = round(float(c_val), 2)

    # 2. 用每日线兜底前收盘
    if prev_close <= 0 and history_df is not None and not history_df.empty and "Close" in history_df.columns:
        hd = history_df.copy()
        try:
            idx_dates = pd.to_datetime(hd.index).date
            prior_hd = hd[idx_dates < target_date]
            if not prior_hd.empty:
                prev_close = _safe_float(prior_hd["Close"].iloc[-1], 0.0)
            elif len(hd) >= 2:
                prev_close = _safe_float(hd["Close"].iloc[-2], _safe_float(hd["Close"].iloc[-1], 0.0))
            else:
                prev_close = _safe_float(hd["Close"].iloc[-1], 0.0)
        except Exception:
            prev_close = _safe_float(hd["Close"].iloc[-1], 0.0)

    # 3. 融入当前轮询抓到的盘前/盘中最新分时特征 (零额外网络开销)
    info = intraday_info or {}
    if _safe_float(info.get("pmh"), 0.0) > 0:
        cand_pmh = float(info["pmh"])
        pmh = cand_pmh if pmh is None else max(pmh, cand_pmh)
    if _safe_float(info.get("pml"), 0.0) > 0:
        cand_pml = float(info["pml"])
        pml = cand_pml if pml is None else min(pml, cand_pml)

    if str(info.get("rth_date", "")) == target_str:
        if _safe_float(info.get("rth_open"), 0.0) > 0:
            anchor_open = float(info["rth_open"])
        for slot_pair in info.get("rth_5m_series") or []:
            if isinstance(slot_pair, (list, tuple)) and len(slot_pair) == 2:
                si, px_v = int(slot_pair[0]), _safe_float(slot_pair[1], 0.0)
                if 0 <= si < len(TIME_SLOTS) and px_v > 0:
                    actual_prices[si] = round(px_v, 2)

    ext_px = _safe_float(info.get("ext_price"), 0.0)
    live_px = _safe_float((rq or {}).get("last"), 0.0)
    if target_date == cur_et.date():
        if cur_et.hour * 60 + cur_et.minute < 9 * 60 + 30:
            if ext_px > 0:
                anchor_open = ext_px
                pmh = ext_px if pmh is None else max(pmh, ext_px)
                pml = ext_px if pml is None else min(pml, ext_px)
        elif cur_et.hour * 60 + cur_et.minute < 16 * 60:
            if live_px > 0:
                if anchor_open <= 0:
                    anchor_open = _safe_float((rq or {}).get("open"), live_px)
                cur_slot = min(len(TIME_SLOTS) - 1, max(0, ((cur_et.hour * 60 + cur_et.minute) - 570) // 5))
                actual_prices[cur_slot] = round(live_px, 2)

    if anchor_open <= 0:
        anchor_open = live_px if live_px > 0 else (ext_px if ext_px > 0 else prev_close)

    return prev_close, anchor_open, pmh, pml, actual_prices


def _merge_actual_prices(old_arr: Optional[List[Any]], new_arr: Optional[List[Any]]) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(TIME_SLOTS)
    for i in range(len(TIME_SLOTS)):
        nv = new_arr[i] if (new_arr and i < len(new_arr)) else None
        ov = old_arr[i] if (old_arr and i < len(old_arr)) else None
        chosen = nv if (nv is not None and _safe_float(nv, 0.0) > 0) else ov
        if chosen is not None and _safe_float(chosen, 0.0) > 0:
            out[i] = round(float(chosen), 2)
    return out


def update_intraday_path_forecasts(
    tickers: List[str],
    intraday_map: Optional[Dict[str, Dict[str, Any]]] = None,
    history_dict: Optional[Dict[str, pd.DataFrame]] = None,
    realtime_quotes: Optional[Dict[str, Dict[str, Any]]] = None,
    market_regime: str = "NEUTRAL_CHOP",
    now_et: Optional[datetime] = None,
    cache_path: str = PARQUET_CACHE_FILE,
    meta_path: str = META_CACHE_FILE,
    output_path: str = FORECAST_JSON_FILE,
    force_refresh_60d: bool = False,
    downloader: Optional[Callable[..., pd.DataFrame]] = None,
) -> Dict[str, Any]:
    """
    生成或增量更新全池当日盘中预测走廊与 09:45 ET 实时背离状态，落盘至 data_cache/intraday_path_forecasts.json。
    严格遵守 09:25 ET 盘前锁定不可篡改契约与零未来泄漏。
    """
    cur_et = _to_et_datetime(now_et)
    full_out = _resolve_path(output_path)

    existing_doc: Dict[str, Any] = {}
    if os.path.exists(full_out):
        try:
            with open(full_out, "r", encoding="utf-8") as f:
                existing_doc = json.load(f) or {}
        except Exception:
            existing_doc = {}

    df_60d = refresh_intraday_5m_60d_cache(
        tickers=tickers,
        cache_path=cache_path,
        meta_path=meta_path,
        now_et=cur_et,
        force_refresh=force_refresh_60d,
        downloader=downloader,
    )

    target_date, should_lock, lock_tag = _resolve_target_date_and_lock(df_60d, existing_doc, cur_et)
    target_str = target_date.strftime("%Y-%m-%d")

    grouped_60d: Dict[str, pd.DataFrame] = {}
    if df_60d is not None and not df_60d.empty and "Ticker" in df_60d.columns:
        for tk_g, grp in df_60d.groupby("Ticker", sort=False):
            grouped_60d[str(tk_g)] = grp

    qqq_df = grouped_60d.get("QQQ")
    qqq_gap_by_date, qqq_regime_by_date = _build_qqq_context_maps(qqq_df, target_date)

    # 今日 QQQ 跳空特征（同样仅使用目标日盘前/开盘与前日收盘）
    qqq_pc, qqq_ao, _, _, _ = _extract_day_features_for_target(
        "QQQ",
        qqq_df,
        target_date,
        (intraday_map or {}).get("QQQ", {}),
        (history_dict or {}).get("QQQ"),
        (realtime_quotes or {}).get("QQQ"),
        cur_et,
    )
    today_qqq_gap = ((qqq_ao - qqq_pc) / qqq_pc) * 100.0 if (qqq_pc > 0 and qqq_ao > 0) else 0.0
    today_regime_score = REGIME_SCORE_MAP.get(str(market_regime), 0.0)

    prev_by_ticker = existing_doc.get("by_ticker") if isinstance(existing_doc.get("by_ticker"), dict) else {}
    by_ticker: Dict[str, Any] = {}

    for tk in dict.fromkeys(tickers or []):
        tk_s = str(tk)
        df_tk = grouped_60d.get(tk_s)
        prev_c, anchor_o, pmh, pml, new_actual = _extract_day_features_for_target(
            tk_s,
            df_tk,
            target_date,
            (intraday_map or {}).get(tk_s, {}),
            (history_dict or {}).get(tk_s),
            (realtime_quotes or {}).get(tk_s),
            cur_et,
        )

        prev_item = prev_by_ticker.get(tk_s)
        is_already_locked = bool(
            isinstance(prev_item, dict)
            and str(prev_item.get("forecast_date", "")) == target_str
            and bool(prev_item.get("snapshot_locked", False))
            and prev_item.get("status") in ("ok", "insufficient_history", "continuous_24h")
        )

        if is_already_locked:
            item = dict(prev_item)
            merged_actual = _merge_actual_prices(item.get("actual_prices"), new_actual)
            item["actual_prices"] = merged_actual
            ao_locked = _safe_float(item.get("anchor_open"), 0.0)
            if ao_locked > 0:
                item["actual_pcts"] = [
                    round(((float(v) - ao_locked) / ao_locked) * 100.0, 2) if (v is not None and _safe_float(v, 0.0) > 0) else None
                    for v in merged_actual
                ]
            div_info = evaluate_intraday_divergence(item, merged_actual, now_et=cur_et)
            item["divergence"] = div_info
            item["latest_slot"] = div_info.get("latest_slot")
            by_ticker[tk_s] = item
            continue

        item = compute_knn_intraday_corridor(
            ticker=tk_s,
            df_tk=df_tk,
            forecast_date=target_date,
            prev_close=prev_c,
            anchor_open=anchor_o,
            pmh=pmh,
            pml=pml,
            today_qqq_gap_pct=today_qqq_gap,
            today_regime_score=today_regime_score,
            qqq_gap_by_date=qqq_gap_by_date,
            qqq_regime_by_date=qqq_regime_by_date,
            k_neighbors=12,
        )
        item["snapshot_locked"] = bool(should_lock)
        item["lock_window_tag"] = lock_tag
        if should_lock:
            item["locked_at_et"] = cur_et.strftime("%Y-%m-%d %H:%M:%S")
        item["actual_prices"] = new_actual
        ao_cur = _safe_float(item.get("anchor_open"), 0.0)
        if ao_cur > 0:
            item["actual_pcts"] = [
                round(((float(v) - ao_cur) / ao_cur) * 100.0, 2) if (v is not None and _safe_float(v, 0.0) > 0) else None
                for v in new_actual
            ]
        div_info = evaluate_intraday_divergence(item, new_actual, now_et=cur_et)
        item["divergence"] = div_info
        item["latest_slot"] = div_info.get("latest_slot")
        by_ticker[tk_s] = item

    ok_cnt = sum(1 for v in by_ticker.values() if v.get("status") == "ok")
    insuf_cnt = sum(1 for v in by_ticker.values() if v.get("status") == "insufficient_history")
    c24_cnt = sum(1 for v in by_ticker.values() if v.get("status") == "continuous_24h")

    doc = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at_et": cur_et.strftime("%Y-%m-%d %H:%M:%S"),
        "forecast_date": target_str,
        "market_regime": str(market_regime),
        "time_slots": TIME_SLOTS,
        "coverage": {
            "total_count": len(by_ticker),
            "ok_count": ok_cnt,
            "insufficient_history_count": insuf_cnt,
            "continuous_24h_count": c24_cnt,
        },
        "by_ticker": by_ticker,
    }

    try:
        os.makedirs(os.path.dirname(full_out), exist_ok=True)
        tmp_out = full_out + ".tmp"
        with open(tmp_out, "w", encoding="utf-8") as wf:
            json.dump(doc, wf, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_out, full_out)
    except Exception:
        pass

    return doc


def _load_default_tickers_and_regime() -> Tuple[List[str], str]:
    tickers: List[str] = []
    market_regime = "NEUTRAL_CHOP"
    ai_path = _resolve_path("data_cache/latest_ai_predictions.json")
    if os.path.exists(ai_path):
        try:
            with open(ai_path, "r", encoding="utf-8") as f:
                ai_doc = json.load(f) or {}
            market_regime = str(ai_doc.get("market_regime") or "NEUTRAL_CHOP")
            by_tk = ai_doc.get("by_ticker") or {}
            if isinstance(by_tk, dict):
                tickers.extend(list(by_tk.keys()))
        except Exception:
            pass
    for extra in ("ANT", "BTC-USD", "XAUUSD"):
        if extra not in tickers:
            tickers.append(extra)
    return tickers, market_regime


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="US Market Radar - Phase 2 Intraday Path Corridor Predictor")
    parser.add_argument("--refresh", action="store_true", help="强制增量/重拉 60 日 5m 历史底模并生成当日走廊快照")
    args = parser.parse_args()

    pool_tickers, regime_now = _load_default_tickers_and_regime()
    res = update_intraday_path_forecasts(
        tickers=pool_tickers,
        market_regime=regime_now,
        force_refresh_60d=args.refresh,
    )
    cov = res.get("coverage", {})
    print(f"[+] 盘中走廊预测构建完成: date={res.get('forecast_date')} | 总计={cov.get('total_count')} | 有效KNN走廊={cov.get('ok_count')} | 新股等待={cov.get('insufficient_history_count')} | 7x24连续={cov.get('continuous_24h_count')}")
    for sample_tk in ("SPY", "NVDA", "SOXL", "ANT", "XAUUSD"):
        it = (res.get("by_ticker") or {}).get(sample_tk)
        if it:
            print(f"  - {sample_tk:7s} [{it.get('status')}]: {it.get('summary_text')} | {(it.get('divergence') or {}).get('badge')}")
