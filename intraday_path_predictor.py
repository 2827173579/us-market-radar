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
NON_US_SESSION_TICKERS = frozenset({"KOSPI"})
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


_SPLIT_CANDIDATE_RATIOS = (
    2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0, 20.0, 25.0,
    0.5, 0.25, 0.2, 0.1, 0.05,
)


def _match_intraday_split_factor(raw_ratio: float) -> Optional[float]:
    if raw_ratio <= 0 or not np.isfinite(raw_ratio):
        return None
    best_r = min(_SPLIT_CANDIDATE_RATIOS, key=lambda r: abs(raw_ratio - r) / r)
    rel_err = abs(raw_ratio - best_r) / best_r
    if best_r >= 3.0 or best_r <= 0.35:
        if rel_err <= 0.18:
            return float(best_r)
    elif rel_err <= 0.08:
        return float(best_r)
    return None


def sanitize_5m_split_jumps(df: pd.DataFrame) -> pd.DataFrame:
    """
    阶段三合并层拆股跳变保护（与阶段一 ±40% 阈值一致）：
    对单只标的按时间排序的 5m 序列检测相邻 bar 是否出现 >=40% 的除权断层
    (curr/prev <= 0.60 或 >= 1.67)。
    - 若比值匹配标准拆股/合股因子 (如 SNDK 1拆8)，自动将断层之前的所有 OHLC /= factor、Volume *= factor；
    - 避免未除权历史尾巴污染 60 日波动率与 KNN 匹配。
    """
    if df is None or df.empty or "Close" not in df.columns or "Ticker" not in df.columns:
        return df

    healed_parts: List[pd.DataFrame] = []
    for tk, grp in df.groupby("Ticker", sort=False):
        g = grp.sort_values("Datetime").reset_index(drop=True).copy()
        if str(tk) in ("ANT", "BTC-USD", "XAUUSD") or len(g) < 2:
            healed_parts.append(g)
            continue

        closes = pd.to_numeric(g["Close"], errors="coerce").to_numpy(dtype=float, copy=True)
        # 从最新往最旧扫描，确保连续拆股也能逐层向最新复权基准对齐
        for idx in range(len(closes) - 1, 0, -1):
            curr_c = closes[idx]
            prev_c = closes[idx - 1]
            if not (np.isfinite(curr_c) and np.isfinite(prev_c) and curr_c > 0 and prev_c > 0):
                continue
            step_ratio = curr_c / prev_c
            if step_ratio <= 0.60 or step_ratio >= 1.67:
                raw_factor = prev_c / curr_c
                factor = _match_intraday_split_factor(raw_factor)
                if factor is not None and factor > 0:
                    for col in ("Open", "High", "Low", "Close"):
                        if col in g.columns:
                            g.loc[: idx - 1, col] = pd.to_numeric(g.loc[: idx - 1, col], errors="coerce") / factor
                    if "Volume" in g.columns:
                        g.loc[: idx - 1, "Volume"] = pd.to_numeric(g.loc[: idx - 1, "Volume"], errors="coerce") * factor
                    closes[:idx] = closes[:idx] / factor
        healed_parts.append(g)

    if not healed_parts:
        return df.iloc[0:0].copy()
    return pd.concat(healed_parts, ignore_index=True)


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
    dl_tickers = [
        str(t)
        for t in dict.fromkeys(tickers or [])
        if str(t) not in CONTINUOUS_24H_TICKERS and str(t) not in NON_US_SESSION_TICKERS
    ]
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

    # 阶段三修复：凡是单只标的本次成功拉取到新的 60d 5m 历史数据，直接整票覆盖该标的旧缓存，
    # 仅在单票拉取失败时才保留其旧缓存，彻底杜绝拆股后第 61~72 天未除权老尾巴残留
    fetched_tickers = {
        str(df["Ticker"].iloc[0])
        for df in collected
        if df is not None and not df.empty and "Ticker" in df.columns
    }
    if not existing_df.empty and fetched_tickers and "Ticker" in existing_df.columns:
        existing_df = existing_df[~existing_df["Ticker"].astype(str).isin(fetched_tickers)].copy()

    frames = [df for df in ([existing_df] + collected) if df is not None and not df.empty]
    if not frames:
        return pd.DataFrame(columns=empty_cols)

    merged = pd.concat(frames, ignore_index=True)
    merged["Datetime"] = pd.to_datetime(merged["Datetime"]).dt.tz_localize(None)
    merged["Ticker"] = merged["Ticker"].astype(str)
    merged = merged.sort_values(["Ticker", "Datetime"]).drop_duplicates(subset=["Ticker", "Datetime"], keep="last")

    # 合并层拆股跳变自愈防护（±40% 断层检测与复权对齐）
    merged = sanitize_5m_split_jumps(merged)

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


def _build_24h_series_from_df(df_tk: Optional[pd.DataFrame], ticker: str) -> Optional[Dict[str, Any]]:
    """从通用 5m/15m DataFrame 提取滚动近 24H 价格与累计 VWAP 曲线（供离线测试或兜底使用）。"""
    if df_tk is None or not isinstance(df_tk, pd.DataFrame) or df_tk.empty or "Close" not in df_tk.columns:
        return None
    try:
        sub = df_tk.copy()
        if "Datetime" in sub.columns:
            dt_ser = pd.to_datetime(sub["Datetime"], errors="coerce")
            sub = sub.loc[dt_ser.notna()].copy()
            sub.index = pd.DatetimeIndex(dt_ser[dt_ser.notna()])
        else:
            dt_idx = pd.to_datetime(sub.index, errors="coerce")
            sub = sub.loc[dt_idx.notna()].copy()
            sub.index = pd.DatetimeIndex(dt_idx[dt_idx.notna()])

        if sub.empty:
            return None
        if getattr(sub.index, "tz", None) is not None:
            sub.index = sub.index.tz_convert("UTC")
        else:
            sub.index = sub.index.tz_localize("UTC")

        for col in ("Open", "High", "Low"):
            if col not in sub.columns:
                sub[col] = sub["Close"]
            sub[col] = pd.to_numeric(sub[col], errors="coerce").fillna(pd.to_numeric(sub["Close"], errors="coerce"))
        sub["Close"] = pd.to_numeric(sub["Close"], errors="coerce")
        sub["Volume"] = pd.to_numeric(sub["Volume"], errors="coerce").fillna(0.0) if "Volume" in sub.columns else 0.0
        sub = sub.dropna(subset=["Close"]).sort_index()
        sub = sub[sub["Close"] > 0]
        if len(sub) < 2:
            return None

        last_ts = sub.index[-1]
        win = sub[sub.index >= (last_ts - pd.Timedelta(hours=24))]
        if len(win) < 2:
            win = sub
        high_24h = float(win["High"].max())
        low_24h = float(win["Low"].min())
        if len(win) > 96:
            bars = (
                win.resample("15min")
                .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
                .dropna(subset=["Close"])
            )
            interval_lbl = "15m"
            if len(bars) < 2:
                bars = win.copy()
                interval_lbl = "5m"
        else:
            bars = win.copy()
            interval_lbl = "5m"

        last_date_utc = last_ts.date()
        same_day = sub[sub.index.date == last_date_utc]
        utc_open = float(same_day["Open"].iloc[0]) if not same_day.empty else float(bars["Open"].iloc[0])
        same_utc_idxs = [idx_b for idx_b, ts_b in enumerate(bars.index) if ts_b.date() == last_date_utc]
        utc00_idx = None
        if same_utc_idxs:
            cand_i = same_utc_idxs[0]
            ts0 = bars.index[cand_i]
            if cand_i > 0 or (ts0.hour == 0 and ts0.minute < 30):
                utc00_idx = int(cand_i)

        actual_24h: List[float] = []
        vwap_24h: List[float] = []
        cum_pv, cum_vol, tp_sum, tp_cnt = 0.0, 0.0, 0.0, 0
        for _, row_b in bars.iterrows():
            cl_b = float(row_b["Close"])
            hi_b = float(row_b["High"]) if math.isfinite(float(row_b["High"])) else cl_b
            lo_b = float(row_b["Low"]) if math.isfinite(float(row_b["Low"])) else cl_b
            vo_b = max(0.0, float(row_b["Volume"])) if math.isfinite(float(row_b["Volume"])) else 0.0
            tp_b = (hi_b + lo_b + cl_b) / 3.0
            tp_sum += tp_b
            tp_cnt += 1
            if vo_b > 0:
                cum_pv += tp_b * vo_b
                cum_vol += vo_b
            vw_b = (cum_pv / cum_vol) if cum_vol > 0 else (tp_sum / max(1, tp_cnt))
            actual_24h.append(round(cl_b, 2))
            vwap_24h.append(round(vw_b, 2))

        is_wknd_closed = bool(
            str(ticker) == "XAUUSD"
            and (datetime.now(timezone.utc) - last_ts.to_pydatetime()) > timedelta(hours=3)
        )
        return {
            "time_labels": [ts_b.strftime("%H:%M") for ts_b in bars.index],
            "time_labels_full": [ts_b.strftime("%m-%d %H:%M UTC") for ts_b in bars.index],
            "actual_prices": actual_24h,
            "vwap_prices": vwap_24h,
            "utc_open": round(utc_open, 2),
            "utc00_idx": utc00_idx,
            "high_24h": round(high_24h, 2),
            "low_24h": round(low_24h, 2),
            "last_bar_utc": last_ts.strftime("%Y-%m-%d %H:%M UTC"),
            "window_start_utc": bars.index[0].strftime("%Y-%m-%d %H:%M UTC"),
            "interval_label": interval_lbl,
            "is_weekend_closed": is_wknd_closed,
        }
    except Exception:
        return None


def partition_24h_sr_for_viewport(
    spot: Any,
    core_min: Any,
    core_max: Any,
    anchor_sr: Optional[Dict[str, Any]] = None,
    max_pct_dist: float = 0.08,
    max_span_expand: float = 0.35,
) -> Dict[str, Any]:
    """
    Y 轴防压扁分流器：
    仅将在 24H 价格合理视窗内（默认距现价 <= ±8% 且不把当前波动带拉垮 >35%）的 S/R 静态锚画入主图虚线，
    远端 S/R 转为图表边缘提示标签，严格保留 ATR_ESTIMATE 非绿分色标记。
    """
    spot_f = _safe_float(spot, 0.0)
    c_min = _safe_float(core_min, spot_f * 0.98 if spot_f > 0 else 95.0)
    c_max = _safe_float(core_max, spot_f * 1.02 if spot_f > 0 else 105.0)
    if spot_f <= 0:
        spot_f = (c_min + c_max) / 2.0 if (c_min > 0 and c_max > 0) else 100.0
    if c_max <= c_min:
        c_min, c_max = spot_f * 0.985, spot_f * 1.015
    core_span = max(c_max - c_min, spot_f * 0.015)

    anc = anchor_sr if isinstance(anchor_sr, dict) else {}
    raw_levels = [
        ("S1", "support", anc.get("anchor_s1") if anc.get("anchor_s1") is not None else anc.get("s1"), anc.get("s1_source")),
        ("S2", "support", anc.get("anchor_s2") if anc.get("anchor_s2") is not None else anc.get("s2"), anc.get("s2_source")),
        ("R1", "resistance", anc.get("anchor_r1") if anc.get("anchor_r1") is not None else anc.get("r1"), anc.get("r1_source")),
        ("R2", "resistance", anc.get("anchor_r2") if anc.get("anchor_r2") is not None else anc.get("r2"), anc.get("r2_source")),
    ]

    in_window: List[Dict[str, Any]] = []
    far_upper: List[Dict[str, Any]] = []
    far_lower: List[Dict[str, Any]] = []
    for key, side, val_raw, src_raw in raw_levels:
        lv = _safe_float(val_raw, 0.0)
        if lv <= 0:
            continue
        src_u = str(src_raw or "ATR_ESTIMATE").strip().upper() or "ATR_ESTIMATE"
        pct_vs_spot = round(((lv - spot_f) / spot_f) * 100.0, 1)
        entry = {
            "key": key,
            "side": side,
            "price": round(lv, 2),
            "source": src_u,
            "is_estimate": (src_u == "ATR_ESTIMATE"),
            "pct_vs_spot": pct_vs_spot,
        }
        in_pct = abs(lv - spot_f) / spot_f <= max_pct_dist
        in_span = (c_min - core_span * max_span_expand) <= lv <= (c_max + core_span * max_span_expand)
        if in_pct and in_span:
            in_window.append(entry)
        elif lv >= spot_f:
            far_upper.append(entry)
        else:
            far_lower.append(entry)

    y_vals = [c_min, c_max] + [x["price"] for x in in_window]
    raw_min = min(y_vals)
    raw_max = max(y_vals)
    span_y = max(raw_max - raw_min, spot_f * 0.01)
    return {
        "in_window": in_window,
        "far_upper": far_upper,
        "far_lower": far_lower,
        "y_min": round(raw_min - span_y * 0.08, 2),
        "y_max": round(raw_max + span_y * 0.08, 2),
    }


def _compute_24h_vol_quantiles(ticker: str, history_df: Optional[pd.DataFrame], forecast_date: date) -> Tuple[float, float, float]:
    """从历史 60~120 日 K 线无前瞻提取 24H 日内振幅 P25/P50/P75 分布。"""
    default_bounds = (-0.022, 0.0, 0.022) if str(ticker) == "BTC-USD" else (-0.009, 0.0, 0.009)
    min_half = 0.007 if str(ticker) == "BTC-USD" else 0.0035
    if history_df is None or not isinstance(history_df, pd.DataFrame) or len(history_df) < 10 or "Close" not in history_df.columns:
        return default_bounds
    try:
        hd = history_df.copy()
        idx_dt = pd.to_datetime(hd.index, errors="coerce")
        valid_mask = idx_dt.notna()
        if valid_mask.any():
            hd = hd.loc[valid_mask].copy()
            idx_clean = idx_dt[valid_mask]
            if getattr(idx_clean, "tz", None) is not None:
                idx_clean = idx_clean.tz_localize(None)
            prior = hd[np.array([x.date() for x in idx_clean]) < forecast_date]
            if len(prior) >= 10:
                hd = prior
        tail_hd = hd.tail(120)
        closes = pd.to_numeric(tail_hd["Close"], errors="coerce")
        if "Open" in tail_hd.columns:
            opens = pd.to_numeric(tail_hd["Open"], errors="coerce").fillna(closes)
        else:
            opens = closes.shift(1).fillna(closes)
        valid = (opens > 0) & (closes > 0) & closes.notna() & opens.notna()
        rets = ((closes[valid] - opens[valid]) / opens[valid]).to_numpy(dtype=float)
        if len(rets) < 10:
            return default_bounds
        q25 = float(np.percentile(rets, 25))
        q50 = float(np.percentile(rets, 50))
        q75 = float(np.percentile(rets, 75))
        if (q50 - q25) < min_half:
            q25 = q50 - min_half
        if (q75 - q50) < min_half:
            q75 = q50 + min_half
        return float(q25), float(q50), float(q75)
    except Exception:
        return default_bounds


def compute_continuous_24h_corridor(
    ticker: str,
    intraday_info: Optional[Dict[str, Any]] = None,
    df_tk: Optional[pd.DataFrame] = None,
    history_df: Optional[pd.DataFrame] = None,
    realtime_quote: Optional[Dict[str, Any]] = None,
    prev_item: Optional[Dict[str, Any]] = None,
    now_et: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    为 BTC-USD（7×24h 连续）与 XAUUSD（24H 连续现货黄金）生成真实 24H 滚动走势 + 实时 VWAP + 历史波动带 (P25~P75)。
    周末黄金休市时优雅回退至最近一个完整 24H 有效交易时段。
    """
    tk = str(ticker or "").strip().upper()
    cur_et = _to_et_datetime(now_et)
    f_date = cur_et.date()
    f_date_str = f_date.strftime("%Y-%m-%d")

    info = intraday_info if isinstance(intraday_info, dict) else {}
    c24 = info.get("continuous_24h") if isinstance(info.get("continuous_24h"), dict) else None
    if not (isinstance(c24, dict) and isinstance(c24.get("actual_prices"), list) and len(c24["actual_prices"]) >= 2):
        c24 = _build_24h_series_from_df(df_tk, tk)
    reused_prev_weekend = False
    if not (isinstance(c24, dict) and isinstance(c24.get("actual_prices"), list) and len(c24["actual_prices"]) >= 2):
        if (
            isinstance(prev_item, dict)
            and prev_item.get("status") == "continuous_24h"
            and isinstance(prev_item.get("actual_prices"), list)
            and len(prev_item["actual_prices"]) >= 2
        ):
            c24 = {
                "time_labels": list(prev_item.get("time_labels") or []),
                "time_labels_full": list(prev_item.get("time_labels_full") or []),
                "actual_prices": list(prev_item.get("actual_prices") or []),
                "vwap_prices": list(prev_item.get("vwap_prices") or prev_item.get("actual_prices") or []),
                "utc_open": prev_item.get("utc_open") or prev_item.get("anchor_open"),
                "utc00_idx": prev_item.get("utc00_idx"),
                "high_24h": prev_item.get("high_24h"),
                "low_24h": prev_item.get("low_24h"),
                "last_bar_utc": prev_item.get("last_bar_utc", ""),
                "window_start_utc": prev_item.get("window_start_utc", ""),
                "interval_label": prev_item.get("interval_label", "15m"),
                "is_weekend_closed": True if tk == "XAUUSD" else bool(prev_item.get("is_weekend_closed", False)),
            }
            reused_prev_weekend = True

    live_px = _safe_float((realtime_quote or {}).get("last"), 0.0)
    if not (isinstance(c24, dict) and len(c24.get("actual_prices") or []) >= 2):
        fb_px = live_px if live_px > 0 else _safe_float(info.get("rth_close"), 0.0)
        msg = "7×24h 连续交易品种，以日内 VWAP 与 4H 筹码为准" if tk == "BTC-USD" else "24H 连续走势，以实时 VWAP 与 4H 筹码为准"
        return {
            "ticker": tk,
            "forecast_date": f_date_str,
            "prev_close": round(fb_px, 2),
            "anchor_open": round(fb_px, 2),
            "utc_open": round(fb_px, 2),
            "status": "continuous_24h",
            "has_24h_series": False,
            "is_weekend_closed": bool(tk == "XAUUSD" and cur_et.weekday() >= 5),
            "meta_header": "UTC 00:00 日锚 · 24H 滚动刷新" if tk == "BTC-USD" else "24H 连续走势 · 实时 VWAP 锚定",
            "status_message": msg,
            "summary_text": msg,
            "p25_prices": [],
            "p50_prices": [],
            "p75_prices": [],
            "actual_prices": [],
            "vwap_prices": [],
            "snapshot_locked": False,
            "lock_window_tag": "CONTINUOUS_24H_ROLLING",
            "divergence": {
                "status": "CONTINUOUS_24H",
                "badge": "🌐 7×24h 实时滚动 · 锚定 VWAP",
                "color": "#38bdf8",
                "latest_slot": None,
            },
        }

    actual_24h = [round(float(v), 2) for v in c24["actual_prices"] if _safe_float(v, 0.0) > 0]
    vwap_raw = c24.get("vwap_prices") or actual_24h
    vwap_24h = [round(float(v), 2) for v in vwap_raw[:len(actual_24h)] if _safe_float(v, 0.0) > 0]
    while len(vwap_24h) < len(actual_24h):
        vwap_24h.append(vwap_24h[-1] if vwap_24h else actual_24h[len(vwap_24h)])

    n_pts = len(actual_24h)
    time_labels = list((c24.get("time_labels") or [])[:n_pts])
    if len(time_labels) < n_pts:
        time_labels = [f"{i:02d}" for i in range(n_pts)]
    time_labels_full = list((c24.get("time_labels_full") or time_labels)[:n_pts])

    is_wknd_closed = bool(c24.get("is_weekend_closed", False)) or reused_prev_weekend
    if tk == "BTC-USD":
        is_wknd_closed = False

    utc_open = _safe_float(c24.get("utc_open"), actual_24h[0])
    if utc_open <= 0:
        utc_open = actual_24h[0]
    high_24h = max(_safe_float(c24.get("high_24h"), max(actual_24h)), max(actual_24h))
    low_24h = min(_safe_float(c24.get("low_24h"), min(actual_24h)), min(actual_24h))

    if live_px > 0 and not is_wknd_closed:
        ref_last = actual_24h[-1]
        if ref_last > 0 and 0.95 <= (live_px / ref_last) <= 1.05:
            actual_24h[-1] = round(live_px, 2)
            high_24h = max(high_24h, live_px)
            low_24h = min(low_24h, live_px)

    cur_px = actual_24h[-1]
    cur_vwap = vwap_24h[-1] if vwap_24h else cur_px

    q25, q50, q75 = _compute_24h_vol_quantiles(tk, history_df, f_date)
    p25_val = round(utc_open * (1.0 + q25), 2)
    p50_val = round(utc_open * (1.0 + q50), 2)
    p75_val = round(utc_open * (1.0 + q75), 2)

    p25_prices = [p25_val] * n_pts
    p50_prices = [p50_val] * n_pts
    p75_prices = [p75_val] * n_pts

    vwap_diff_pct = round(((cur_px - cur_vwap) / cur_vwap) * 100.0, 2) if cur_vwap > 0 else 0.0
    pct_vs_p50 = round(((cur_px - p50_val) / p50_val) * 100.0, 2) if p50_val > 0 else 0.0

    if cur_px > p75_val:
        band_status = "ABOVE_P75"
        band_cn = "突破近 24H 正常波动带上沿"
    elif cur_px < p25_val:
        band_status = "BELOW_P25"
        band_cn = "跌破近 24H 正常波动带下沿"
    else:
        band_status = "IN_CORRIDOR"
        band_cn = "处于近 24H 正常波动带内"

    pos_vwap = f"上方 +{vwap_diff_pct:.1f}%" if vwap_diff_pct >= 0 else f"下方 {vwap_diff_pct:.1f}%"
    weekend_note = "（周末休市，展示最近完整 24H 交易走势）" if (tk == "XAUUSD" and is_wknd_closed) else ""
    summary_text = f"现价位于 24H 均价线 (VWAP) {pos_vwap}，{band_cn}{weekend_note}"

    if tk == "BTC-USD":
        meta_header = f"UTC 00:00 日锚 · 24H 滚动刷新 | UTC开盘 ${utc_open:,.2f} · 24H高 ${high_24h:,.2f} · 24H低 ${low_24h:,.2f}"
        badge = "🌐 7×24h 实时滚动 · 锚定 VWAP"
    else:
        wk_tag = " · 周末展示最近24H" if is_wknd_closed else ""
        meta_header = f"24H 连续走势 · 实时 VWAP 锚定{wk_tag} | 开盘锚 ${utc_open:,.2f} · 24H高 ${high_24h:,.2f} · 24H低 ${low_24h:,.2f}"
        badge = "🌙 最近 24H 交易走势 · 周末休市" if is_wknd_closed else "🌐 24H 连续走势 · 实时 VWAP 锚定"

    badge_clr = "#10b981" if (band_status == "ABOVE_P75" or (vwap_diff_pct >= 0 and band_status == "IN_CORRIDOR")) else ("#ef4444" if band_status == "BELOW_P25" else "#38bdf8")

    return {
        "ticker": tk,
        "forecast_date": f_date_str,
        "prev_close": round(utc_open, 2),
        "anchor_open": round(utc_open, 2),
        "utc_open": round(utc_open, 2),
        "utc00_idx": c24.get("utc00_idx"),
        "high_24h": round(high_24h, 2),
        "low_24h": round(low_24h, 2),
        "last_bar_utc": str(c24.get("last_bar_utc") or ""),
        "window_start_utc": str(c24.get("window_start_utc") or ""),
        "interval_label": str(c24.get("interval_label") or "15m"),
        "is_weekend_closed": is_wknd_closed,
        "time_labels": time_labels,
        "time_labels_full": time_labels_full,
        "p25_prices": p25_prices,
        "p50_prices": p50_prices,
        "p75_prices": p75_prices,
        "actual_prices": actual_24h,
        "vwap_prices": vwap_24h,
        "status": "continuous_24h",
        "has_24h_series": True,
        "meta_header": meta_header,
        "summary_text": summary_text,
        "status_message": summary_text,
        "latest_slot": n_pts - 1,
        "snapshot_locked": False,
        "lock_window_tag": "CONTINUOUS_24H_ROLLING",
        "divergence": {
            "status": band_status,
            "badge": badge,
            "color": badge_clr,
            "latest_slot": n_pts - 1,
            "latest_time": time_labels[-1],
            "actual_price": round(cur_px, 2),
            "vwap_price": round(cur_vwap, 2),
            "vwap_diff_pct": vwap_diff_pct,
            "slot_p25": p25_val,
            "slot_p50": p50_val,
            "slot_p75": p75_val,
            "pct_vs_p50": pct_vs_p50,
        },
    }


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
    history_df: Optional[pd.DataFrame] = None,
    intraday_info: Optional[Dict[str, Any]] = None,
    realtime_quote: Optional[Dict[str, Any]] = None,
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

    if tk in NON_US_SESSION_TICKERS:
        non_us_msg = "韩国交易时段 (09:00–15:30 KST) · 盘中以实时 K 线与日线支撑阻力为准"
        return {
            "ticker": tk,
            "forecast_date": f_date_str,
            "prev_close": round(pc_val, 2),
            "anchor_open": round(ao_val if ao_val > 0 else pc_val, 2),
            "status": "non_us_session",
            "is_index": True,
            "price_unit": "pts",
            "meta_header": "KRX 韩国交易所 (09:00–15:30 KST) · 亚太基准指数",
            "status_message": non_us_msg,
            "summary_text": non_us_msg,
            "p25_prices": [],
            "p50_prices": [],
            "p75_prices": [],
            "actual_prices": [],
            "snapshot_locked": False,
            "lock_window_tag": "NON_US_SESSION",
            "divergence": {
                "status": "NON_US_SESSION",
                "badge": "🇰🇷 韩国交易时段 (09:00–15:30 KST) · 实时K线与日线锚点",
                "color": "#38bdf8",
                "latest_slot": None,
            },
        }

    if tk in CONTINUOUS_24H_TICKERS:
        return compute_continuous_24h_corridor(
            ticker=tk,
            intraday_info=intraday_info,
            df_tk=df_tk,
            history_df=history_df,
            realtime_quote=realtime_quote or ({"last": ao_val} if ao_val > 0 else None),
            now_et=pd.Timestamp(f_date_str).to_pydatetime(),
        )

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


def apply_rth_open_anchor_translation(
    item: Dict[str, Any],
    actual_prices: Optional[List[Optional[float]]],
    threshold_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Zero-Lookahead 09:30 真实开盘锚纯平移对齐（相对走廊跨度自适应阈值）：
    - 严格保留 09:25 ET 已锁定的相对百分比形态 (p25_pcts / p50_pcts / p75_pcts 绝不事后重算)；
    - 自适应触发阈值：eff_threshold = max(0.5%, 0.5 * corridor_half_pct)，避免 1x ETF 跳动 1% 悬在走廊外；
    - 一旦收到当日 09:30 ET 首根真实 RTH 5m 价格 rth_open (actual_prices[0])，
      若 |rth_open - premarket_anchor_open| / premarket_anchor_open >= eff_threshold，
      则以 rth_open 对价格走廊做纯平移缩放，并保留 premarket_anchor_open 供副标题展示。
    """
    if not isinstance(item, dict) or item.get("status") != "ok":
        return item
    cur_ao = _safe_float(item.get("anchor_open"), 0.0)
    pre_ao = _safe_float(item.get("premarket_anchor_open"), 0.0)
    if pre_ao <= 0 and cur_ao > 0:
        pre_ao = cur_ao
        item["premarket_anchor_open"] = round(pre_ao, 2)

    rth_open = 0.0
    if isinstance(actual_prices, list) and len(actual_prices) > 0 and actual_prices[0] is not None:
        rth_open = _safe_float(actual_prices[0], 0.0)

    if rth_open > 0 and pre_ao > 0:
        p25_pcts = item.get("p25_pcts")
        p50_pcts = item.get("p50_pcts")
        p75_pcts = item.get("p75_pcts")
        if isinstance(p25_pcts, list) and isinstance(p50_pcts, list) and isinstance(p75_pcts, list) and len(p50_pcts) > 0:
            if threshold_pct is not None:
                eff_threshold_pct = float(threshold_pct)
            else:
                half_widths = [
                    abs(float(hi) - float(lo)) / 2.0
                    for hi, lo in zip(p75_pcts, p25_pcts)
                    if hi is not None and lo is not None
                ]
                corridor_half_pct = (sum(half_widths) / len(half_widths)) if half_widths else 1.0
                eff_threshold_pct = max(0.5, 0.5 * corridor_half_pct)
            diff_pct = abs(rth_open - pre_ao) / pre_ao * 100.0
            if diff_pct >= eff_threshold_pct:
                item["p25_prices"] = [round(rth_open * (1.0 + float(x) / 100.0), 2) for x in p25_pcts]
                item["p50_prices"] = [round(rth_open * (1.0 + float(x) / 100.0), 2) for x in p50_pcts]
                item["p75_prices"] = [round(rth_open * (1.0 + float(x) / 100.0), 2) for x in p75_pcts]
                item["anchor_open"] = round(rth_open, 2)
                item["rth_open_aligned"] = True
                item["rth_open_price"] = round(rth_open, 2)
                item["rth_open_shift_pct"] = round(((rth_open - pre_ao) / pre_ao) * 100.0, 2)
    return item


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
        prev_item = prev_by_ticker.get(tk_s)

        if tk_s in NON_US_SESSION_TICKERS:
            rq_k = (realtime_quotes or {}).get(tk_s) or {}
            hist_k = (history_dict or {}).get(tk_s)
            last_k = _safe_float(rq_k.get("last"), 0.0)
            if last_k <= 0 and hist_k is not None and not getattr(hist_k, "empty", True) and "Close" in hist_k.columns:
                last_k = _safe_float(hist_k["Close"].iloc[-1], 0.0)
            prev_k = _safe_float(rq_k.get("prev_close"), last_k)
            by_ticker[tk_s] = compute_knn_intraday_corridor(
                ticker=tk_s,
                df_tk=None,
                forecast_date=target_date,
                prev_close=prev_k,
                anchor_open=last_k,
                realtime_quote=rq_k,
            )
            continue

        if tk_s in CONTINUOUS_24H_TICKERS:
            by_ticker[tk_s] = compute_continuous_24h_corridor(
                ticker=tk_s,
                intraday_info=(intraday_map or {}).get(tk_s, {}),
                df_tk=df_tk,
                history_df=(history_dict or {}).get(tk_s),
                realtime_quote=(realtime_quotes or {}).get(tk_s),
                prev_item=prev_item,
                now_et=cur_et,
            )
            continue

        prev_c, anchor_o, pmh, pml, new_actual = _extract_day_features_for_target(
            tk_s,
            df_tk,
            target_date,
            (intraday_map or {}).get(tk_s, {}),
            (history_dict or {}).get(tk_s),
            (realtime_quotes or {}).get(tk_s),
            cur_et,
        )

        is_already_locked = bool(
            isinstance(prev_item, dict)
            and str(prev_item.get("forecast_date", "")) == target_str
            and bool(prev_item.get("snapshot_locked", False))
            and prev_item.get("status") in ("ok", "insufficient_history")
        )

        if is_already_locked:
            item = dict(prev_item)
            merged_actual = _merge_actual_prices(item.get("actual_prices"), new_actual)
            item["actual_prices"] = merged_actual
            apply_rth_open_anchor_translation(item, merged_actual)
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
        apply_rth_open_anchor_translation(item, new_actual)
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
