# -*- coding: utf-8 -*-
"""
亚太/A 股 16 只标的「物理隔离今日盘中推演走廊引擎」 (apac_intraday_path_predictor.py)
========================================================================================
严格隔离红线：
1. 与美股 `intraday_path_predictor.py`（含 KOSPI 原有链路）完全物理隔离：
   - 独立历史 5m 底模缓存：`data_cache/apac_intraday_5m_60d.parquet`（增量追加并按 60 交易日滚动保留）；
   - 独立推演快照：`data_cache/apac_intraday_path_forecasts.json`；
   - 绝不写入/覆盖美股 `intraday_5m_60d.parquet` 与 `intraday_path_forecasts.json`；
2. 零 Finnhub 消耗、零拖慢 60s 主循环：
   - 亚盘休眠期严格命中 `SLEEP_CACHE` (`http_calls = 0`)；
   - 仅冷启动缓存缺失/为空时立即执行一次全量初算（以最新已完成交易日为锚立即出图）；
   - 亚盘收盘后 (`>= 16:15 CST`) 每日增量追加底模一次；
   - 亚盘活跃窗内直接复用 `apac_ashare_feed` 的 5m 实时切片追加橙色实盘线并更新背离徽章；
3. 分市场交易时段 + 午休剔除 + 异常脉冲清洗（复用 `apac_ashare_feed.sanitize_apac_5m_bars`）：
   - A 股 (12 只：`000688 / 399006` + 10 ETF)：`09:30~11:25` + `13:00~14:55 CST`（48 根，剔除 `11:30~13:00` 午休），锚 `09:30 CST`，观察窗 `09:45 CST`；
   - 港股 (2 只：`^HSI / HSTECH`)：`09:30~11:55` + `13:00~15:55 HKT`（66 根，剔除 `12:00~13:00` 午休），锚 `09:30 HKT`，观察窗 `09:45 HKT`；
   - 日股 (1 只：`^N225`)：`09:00~11:25` + `12:30~15:25 JST`（66 根，剔除 `11:30~12:30` 午休），锚 `09:00 JST`，观察窗 `09:15 JST`；
   - 台股 (1 只：`^TWII`)：`09:00~13:25 CST`（54 根，无午休），锚 `09:00 CST`，观察窗 `09:15 CST`；
4. 严格零未来函数 (`hist_date < forecast_date`) + 自适应 Top-K (`>=20日` Top-12 / `8~19日` `min(10, max(5, int(n*0.5)))` / `<8日` 优雅降级)。
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

import apac_ashare_feed as aaf

try:
    from zoneinfo import ZoneInfo

    CST_TZ = ZoneInfo("Asia/Shanghai")
    HKT_TZ = ZoneInfo("Asia/Hong_Kong")
    JST_TZ = ZoneInfo("Asia/Tokyo")
    TWT_TZ = ZoneInfo("Asia/Taipei")
except Exception:
    CST_TZ = timezone(timedelta(hours=8))
    HKT_TZ = timezone(timedelta(hours=8))
    JST_TZ = timezone(timedelta(hours=9))
    TWT_TZ = timezone(timedelta(hours=8))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_APAC_5M_PARQUET_PATH = os.path.join(BASE_DIR, "data_cache", "apac_intraday_5m_60d.parquet")
DEFAULT_APAC_FORECASTS_JSON_PATH = os.path.join(BASE_DIR, "data_cache", "apac_intraday_path_forecasts.json")

POST_CLOSE_APPEND_CST_MIN = 16 * 60 + 15  # 16:15 CST / 17:15 KST
ROLLING_MAX_TRADING_DAYS = 60


def _gen_slots(start_hhmm: Tuple[int, int], end_hhmm: Tuple[int, int], step: int = 5) -> List[str]:
    cur = start_hhmm[0] * 60 + start_hhmm[1]
    end = end_hhmm[0] * 60 + end_hhmm[1]
    out: List[str] = []
    while cur <= end:
        out.append(f"{cur // 60:02d}:{cur % 60:02d}")
        cur += step
    return out


# 四大亚太/A 股市场规范交易 Bar 序列（严格剔除午休空档）
MARKET_SESSION_SPECS: Dict[str, Dict[str, Any]] = {
    "CN": {
        "tz": CST_TZ,
        "tz_label": "CST",
        "market_cn": "A股",
        "open_min": 9 * 60 + 30,
        "slots": _gen_slots((9, 30), (11, 25)) + _gen_slots((13, 0), (14, 55)),  # 24 + 24 = 48 bars
        "open_label": "09:30 CST",
        "early_window_slot": 3,
        "early_window_label": "09:45 CST 观察窗",
        "lunch_seam_idx": 24,
        "session_desc": "A股交易时段 09:30–11:30 / 13:00–15:00 CST",
    },
    "HK": {
        "tz": HKT_TZ,
        "tz_label": "HKT",
        "market_cn": "港股",
        "open_min": 9 * 60 + 30,
        "slots": _gen_slots((9, 30), (11, 55)) + _gen_slots((13, 0), (15, 55)),  # 30 + 36 = 66 bars
        "open_label": "09:30 HKT",
        "early_window_slot": 3,
        "early_window_label": "09:45 HKT 观察窗",
        "lunch_seam_idx": 30,
        "session_desc": "港股交易时段 09:30–12:00 / 13:00–16:00 HKT",
    },
    "JP": {
        "tz": JST_TZ,
        "tz_label": "JST",
        "market_cn": "日股",
        "open_min": 9 * 60,
        "slots": _gen_slots((9, 0), (11, 25)) + _gen_slots((12, 30), (15, 25)),  # 30 + 36 = 66 bars
        "open_label": "09:00 JST",
        "early_window_slot": 3,
        "early_window_label": "09:15 JST 观察窗",
        "lunch_seam_idx": 30,
        "session_desc": "日股交易时段 09:00–11:30 / 12:30–15:30 JST",
    },
    "TW": {
        "tz": TWT_TZ,
        "tz_label": "CST",
        "market_cn": "台股",
        "open_min": 9 * 60,
        "slots": _gen_slots((9, 0), (13, 25)),  # 54 bars (continuous, no lunch break)
        "open_label": "09:00 CST",
        "early_window_slot": 3,
        "early_window_label": "09:15 CST 观察窗",
        "lunch_seam_idx": None,
        "session_desc": "台股交易时段 09:00–13:30 CST",
    },
}

# 5m 历史拉取专用免费符号映射（HSTECH 使用 yfinance HSTECH.HK 60日5m）
APAC_5M_YF_SYMBOL_OVERRIDE: Dict[str, str] = {
    "HSTECH": "HSTECH.HK",
}


def _safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "" or isinstance(val, bool):
            return default
        f = float(val)
        return f if math.isfinite(f) else default
    except Exception:
        return default


def _to_cst(now_dt: Optional[datetime] = None) -> datetime:
    if now_dt is None:
        return datetime.now(CST_TZ)
    if now_dt.tzinfo is None:
        return now_dt.replace(tzinfo=CST_TZ)
    return now_dt.astimezone(CST_TZ)


def get_market_spec(market: str) -> Dict[str, Any]:
    return MARKET_SESSION_SPECS.get(str(market or "CN").upper(), MARKET_SESSION_SPECS["CN"])


def load_apac_intraday_forecasts(json_path: str = DEFAULT_APAC_FORECASTS_JSON_PATH) -> Dict[str, Any]:
    if not os.path.exists(json_path):
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _save_json_atomic(doc: Dict[str, Any], path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def _load_5m_parquet(parquet_path: str = DEFAULT_APAC_5M_PARQUET_PATH) -> pd.DataFrame:
    cols = ["ticker", "date", "time_slot", "open", "high", "low", "close", "volume"]
    if not os.path.exists(parquet_path):
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_parquet(parquet_path)
        for c in cols:
            if c not in df.columns:
                return pd.DataFrame(columns=cols)
        return df[cols].copy()
    except Exception:
        return pd.DataFrame(columns=cols)


def _save_5m_parquet_atomic(df: pd.DataFrame, parquet_path: str = DEFAULT_APAC_5M_PARQUET_PATH) -> None:
    parent = os.path.dirname(os.path.abspath(parquet_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{parquet_path}.tmp.{os.getpid()}"
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, parquet_path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def _normalize_day_bars_to_canonical_slots(
    raw_day_rows: List[List[Any]],
    market: str,
    decimals: int = 3,
) -> Dict[str, List[float]]:
    """
    先调用 `apac_ashare_feed.sanitize_apac_5m_bars()` 剔除午休占位柱与 >8× 异常累计巨量柱，
    再对齐至对应市场的规范时间槽（CN 48 / HK 66 / JP 66 / TW 54）。
    返回: {slot_hhmm: [open, high, low, close, volume]}
    """
    cleaned, _ = aaf.sanitize_apac_5m_bars(raw_day_rows, market=market, decimals=decimals)
    spec = get_market_spec(market)
    valid_slots = set(spec["slots"])
    slot_map: Dict[str, List[float]] = {}
    for r in cleaned:
        t = str(r[0])[-5:]
        if t in valid_slots:
            slot_map[t] = [float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
        elif t == "15:00" and market == "CN" and "14:55" not in slot_map:
            slot_map["14:55"] = [float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
        elif t == "16:00" and market == "HK" and "15:55" not in slot_map:
            slot_map["15:55"] = [float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
        elif t == "15:30" and market == "JP" and "15:25" not in slot_map:
            slot_map["15:25"] = [float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
        elif t == "13:30" and market == "TW" and "13:25" not in slot_map:
            slot_map["13:25"] = [float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
    return slot_map


def _aggregate_tencent_1m_to_5m(
    raw_min_lines: List[str],
    decimals: int = 2,
) -> List[List[Any]]:
    """
    将腾讯实时分时 1m 序列（如 ['0930 3433.380 74258 ...', '0931 3457.990 ...']）
    无损聚合为标准 5m OHLCV 柱列表 [[HH:MM, open, high, low, close, volume], ...]。
    """
    buckets: Dict[str, List[Tuple[float, float]]] = {}
    bucket_order: List[str] = []
    prev_cum_vol: Optional[float] = None

    for line in raw_min_lines or []:
        parts = str(line or "").strip().split()
        if len(parts) < 2:
            continue
        hhmm_raw = parts[0]
        if len(hhmm_raw) != 4 or not hhmm_raw.isdigit():
            continue
        hh, mm = int(hhmm_raw[:2]), int(hhmm_raw[2:])
        px = _safe_float(parts[1])
        if px is None or px <= 0:
            continue
        cum_v = _safe_float(parts[2], 0.0) if len(parts) > 2 else 0.0
        if cum_v is not None and cum_v >= 0:
            v_delta = max(0.0, cum_v - prev_cum_vol) if (prev_cum_vol is not None and cum_v >= prev_cum_vol) else cum_v
            prev_cum_vol = cum_v
        else:
            v_delta = 0.0

        b_mm = (mm // 5) * 5
        slot = f"{hh:02d}:{b_mm:02d}"
        if slot not in buckets:
            buckets[slot] = []
            bucket_order.append(slot)
        buckets[slot].append((float(px), float(v_delta)))

    rows: List[List[Any]] = []
    for slot in bucket_order:
        pts = buckets[slot]
        if not pts:
            continue
        prices = [p for p, _ in pts]
        vol_sum = sum(v for _, v in pts)
        rows.append([
            slot,
            round(prices[0], decimals),
            round(max(prices), decimals),
            round(min(prices), decimals),
            round(prices[-1], decimals),
            round(max(0.0, vol_sum), 2),
        ])
    return rows


def _fetch_tencent_live_minute_5m(
    qt_sym: str,
    expected_date: Optional[str] = None,
    decimals: int = 2,
    timeout: float = 6.0,
) -> Tuple[List[List[Any]], str]:
    """
    拉取腾讯免费实时分时接口 (`web.ifzq.gtimg.cn/appstock/app/minute/query`) 并聚合成当日 5m 柱。
    实测对 `hkHSTECH` / `hkHSI` 零延迟、免 Key、零限流，彻底解决 HSTECH 盘中 5m 断更问题。
    返回: (rows_5m, date_yyyy_mm_dd)
    """
    if not qt_sym:
        return [], ""
    try:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={qt_sym}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        if r.status_code != 200:
            return [], ""
        jd = r.json()
        node = ((jd or {}).get("data") or {}).get(qt_sym) or {}
        d_node = node.get("data") or {}
        d_raw = str(d_node.get("date") or "").strip()
        d_fmt = f"{d_raw[0:4]}-{d_raw[4:6]}-{d_raw[6:8]}" if len(d_raw) == 8 and d_raw.isdigit() else ""
        if expected_date and d_fmt and d_fmt != expected_date:
            return [], d_fmt
        raw_lines = d_node.get("data") or []
        if not raw_lines:
            return [], d_fmt
        return _aggregate_tencent_1m_to_5m(raw_lines, decimals=decimals), d_fmt
    except Exception:
        return [], ""


def _fetch_yf_live_day_5m(
    yf_sym: str,
    market: str,
    expected_date: str,
    decimals: int = 2,
) -> List[List[Any]]:
    """单票回退拉取 yfinance 近 2 日 5m，严格筛选指定市场时区的 expected_date 当日柱。"""
    if not yf_sym:
        return []
    try:
        import yfinance as yf

        df = yf.download(yf_sym, period="2d", interval="5m", progress=False)
        if df is None or getattr(df, "empty", True):
            return []
        if isinstance(df.columns, pd.MultiIndex):
            lvl0 = set(df.columns.get_level_values(0))
            if "Close" in lvl0 and yf_sym in df["Close"].columns:
                df = pd.DataFrame({
                    "Open": df["Open"][yf_sym],
                    "High": df["High"][yf_sym],
                    "Low": df["Low"][yf_sym],
                    "Close": df["Close"][yf_sym],
                    "Volume": df["Volume"][yf_sym] if "Volume" in lvl0 else 0.0,
                }).dropna(subset=["Close"])
            else:
                return []
        spec = get_market_spec(market)
        tz_obj = spec["tz"]
        idx = pd.to_datetime(df.index)
        idx_local = idx.tz_convert(tz_obj) if idx.tz is not None else idx.tz_localize(tz_obj)
        rows: List[List[Any]] = []
        for ts_loc, (_, r) in zip(idx_local, df.iterrows()):
            if ts_loc.strftime("%Y-%m-%d") != expected_date:
                continue
            op = _safe_float(r.get("Open"))
            hi = _safe_float(r.get("High"))
            lo = _safe_float(r.get("Low"))
            cl = _safe_float(r.get("Close"))
            vo = _safe_float(r.get("Volume"), 0.0) or 0.0
            if op and hi and lo and cl and cl > 0:
                rows.append([
                    ts_loc.strftime("%H:%M"),
                    round(op, decimals),
                    round(hi, decimals),
                    round(lo, decimals),
                    round(cl, decimals),
                    round(max(0.0, vo), 2),
                ])
        return rows
    except Exception:
        return []


def fetch_live_intraday_5m_for_ticker(
    meta: Dict[str, Any],
    expected_date: str,
) -> Tuple[List[List[Any]], int, str]:
    """
    当主缓存 `apac_doc` 中某只亚太标的缺少当日实时 5m 切片（例如 `HSTECH`）时，
    按「腾讯实时分时 1m→5m（零延迟、免 Key）➔ yfinance 2d 5m」自动回退补齐当日 5m 柱。
    返回: (rows_5m, http_calls_used, source_tag)
    """
    tk = str(meta.get("ticker") or "").upper()
    mkt = str(meta.get("market") or "CN").upper()
    dec = int(meta.get("decimals") or 2)
    qsym = str(meta.get("qt_symbol") or "")
    yfsym = APAC_5M_YF_SYMBOL_OVERRIDE.get(tk) or str(meta.get("yf_symbol") or "")
    calls = 0

    if qsym:
        rows_t, d_t = _fetch_tencent_live_minute_5m(qsym, expected_date=expected_date, decimals=dec)
        calls += 1
        if rows_t and (not d_t or d_t == expected_date):
            return rows_t, calls, "tencent:minute_5m"

    if yfsym:
        rows_y = _fetch_yf_live_day_5m(yfsym, market=mkt, expected_date=expected_date, decimals=dec)
        calls += 1
        if rows_y:
            return rows_y, calls, "yfinance:2d_5m"

    return [], calls, "none"


def _fetch_tencent_5m_fallback(qt_sym: str, market: str, decimals: int = 3) -> List[Tuple[str, str, float, float, float, float, float]]:
    """免 Key 免费腾讯 5m 回退接口（最多 320 根，约 6~7 个交易日）。"""
    if not qt_sym:
        return []
    try:
        url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={qt_sym},m5,,320"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8.0)
        if r.status_code != 200:
            return []
        jd = r.json()
        rows = (((jd or {}).get("data") or {}).get(qt_sym) or {}).get("m5") or []
        by_day: Dict[str, List[List[Any]]] = {}
        for item in rows:
            if not isinstance(item, (list, tuple)) or len(item) < 5:
                continue
            ts_raw = str(item[0])
            if len(ts_raw) < 12:
                continue
            d_str = f"{ts_raw[0:4]}-{ts_raw[4:6]}-{ts_raw[6:8]}"
            hhmm = f"{ts_raw[8:10]}:{ts_raw[10:12]}"
            op = _safe_float(item[1])
            cl = _safe_float(item[2])
            hi = _safe_float(item[3])
            lo = _safe_float(item[4])
            vo = _safe_float(item[5], 0.0) if len(item) > 5 else 0.0
            if op and hi and lo and cl and cl > 0:
                by_day.setdefault(d_str, []).append([hhmm, op, hi, lo, cl, max(0.0, vo or 0.0)])
        recs: List[Tuple[str, str, float, float, float, float, float]] = []
        for d_str, raw_rows in sorted(by_day.items()):
            smap = _normalize_day_bars_to_canonical_slots(raw_rows, market=market, decimals=decimals)
            for slot, vals in smap.items():
                recs.append((d_str, slot, vals[0], vals[1], vals[2], vals[3], vals[4]))
        return recs
    except Exception:
        return []


def fetch_apac_5m_history_batch(
    targets: Optional[List[Dict[str, Any]]] = None,
    period: str = "60d",
) -> Tuple[pd.DataFrame, int]:
    """
    批量拉取全部 16 只亚太/A 股 5m 历史（零 Finnhub 消耗）：
    - 优先 yfinance 批量 60d/5d 5m（覆盖全部 16 只，含 HSTECH.HK，约 59~60 个交易日）；
    - 若单只 A 股缺失则自动回退腾讯 mkline 5m；
    - 全部经 `sanitize_apac_5m_bars()` 清洗午休与 >8x 累计异常巨量柱。
    """
    import yfinance as yf

    tgt_list = targets if targets is not None else aaf.ALL_APAC_ASHARE_TARGETS
    yf_sym_by_tk: Dict[str, str] = {}
    for m in tgt_list:
        tk = str(m["ticker"]).upper()
        yfs = APAC_5M_YF_SYMBOL_OVERRIDE.get(tk) or str(m.get("yf_symbol") or "")
        if yfs:
            yf_sym_by_tk[tk] = yfs

    yf_syms = list(dict.fromkeys(yf_sym_by_tk.values()))
    http_calls = 0
    df_multi = pd.DataFrame()
    if yf_syms:
        try:
            df_multi = yf.download(yf_syms, period=period, interval="5m", progress=False)
            http_calls += 1
        except Exception:
            df_multi = pd.DataFrame()

    records: List[Dict[str, Any]] = []
    for meta in tgt_list:
        tk = str(meta["ticker"]).upper()
        mkt = str(meta.get("market") or "CN").upper()
        dec = int(meta.get("decimals") or 3)
        spec = get_market_spec(mkt)
        tz_obj = spec["tz"]
        yfs = yf_sym_by_tk.get(tk, "")

        sub = pd.DataFrame()
        if yfs and df_multi is not None and not getattr(df_multi, "empty", True):
            try:
                if isinstance(df_multi.columns, pd.MultiIndex):
                    lvl0 = set(df_multi.columns.get_level_values(0))
                    if "Close" in lvl0 and yfs in df_multi["Close"].columns:
                        sub = pd.DataFrame({
                            "Open": df_multi["Open"][yfs],
                            "High": df_multi["High"][yfs],
                            "Low": df_multi["Low"][yfs],
                            "Close": df_multi["Close"][yfs],
                            "Volume": df_multi["Volume"][yfs] if "Volume" in lvl0 else 0.0,
                        }).dropna(subset=["Close"])
                elif len(yf_syms) == 1 and "Close" in df_multi.columns:
                    sub = df_multi[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
            except Exception:
                sub = pd.DataFrame()

        # 按本地市场时区切分交易日并做午休+巨量清洗
        tk_recs = 0
        if not sub.empty:
            idx = pd.to_datetime(sub.index)
            if idx.tz is not None:
                idx_local = idx.tz_convert(tz_obj)
            else:
                idx_local = idx.tz_localize(tz_obj)
            by_day: Dict[str, List[List[Any]]] = {}
            for ts_loc, (_, row) in zip(idx_local, sub.iterrows()):
                d_str = ts_loc.strftime("%Y-%m-%d")
                hhmm = ts_loc.strftime("%H:%M")
                op = _safe_float(row.get("Open"))
                hi = _safe_float(row.get("High"))
                lo = _safe_float(row.get("Low"))
                cl = _safe_float(row.get("Close"))
                vo = _safe_float(row.get("Volume"), 0.0) or 0.0
                if op and hi and lo and cl and cl > 0:
                    by_day.setdefault(d_str, []).append([hhmm, op, hi, lo, cl, max(0.0, vo)])
            for d_str, raw_rows in sorted(by_day.items()):
                smap = _normalize_day_bars_to_canonical_slots(raw_rows, market=mkt, decimals=dec)
                for slot, vals in smap.items():
                    records.append({
                        "ticker": tk,
                        "date": d_str,
                        "time_slot": slot,
                        "open": vals[0],
                        "high": vals[1],
                        "low": vals[2],
                        "close": vals[3],
                        "volume": vals[4],
                    })
                    tk_recs += 1

        # 单票回退腾讯免费 5m 历史（A 股 ETF / 指数）
        if tk_recs == 0 and meta.get("qt_symbol"):
            fb_rows = _fetch_tencent_5m_fallback(str(meta["qt_symbol"]), market=mkt, decimals=dec)
            http_calls += 1
            for d_str, slot, op, hi, lo, cl, vo in fb_rows:
                records.append({
                    "ticker": tk,
                    "date": d_str,
                    "time_slot": slot,
                    "open": op,
                    "high": hi,
                    "low": lo,
                    "close": cl,
                    "volume": vo,
                })

    cols = ["ticker", "date", "time_slot", "open", "high", "low", "close", "volume"]
    out_df = pd.DataFrame(records, columns=cols) if records else pd.DataFrame(columns=cols)
    return out_df, http_calls


def merge_and_trim_5m_parquet(
    existing_df: pd.DataFrame,
    new_df: pd.DataFrame,
    max_days: int = ROLLING_MAX_TRADING_DAYS,
) -> pd.DataFrame:
    """增量合并新旧 5m 数据并按每只标的滚动保留最新 60 个交易日。"""
    cols = ["ticker", "date", "time_slot", "open", "high", "low", "close", "volume"]
    parts = [d for d in (existing_df, new_df) if d is not None and not d.empty]
    if not parts:
        return pd.DataFrame(columns=cols)
    merged = pd.concat(parts, ignore_index=True)[cols]
    merged = merged.drop_duplicates(subset=["ticker", "date", "time_slot"], keep="last")
    merged = merged.sort_values(["ticker", "date", "time_slot"]).reset_index(drop=True)

    # 每只标的仅保留最近 max_days 个交易日
    keep_frames: List[pd.DataFrame] = []
    for tk, grp in merged.groupby("ticker", sort=False):
        days = sorted(grp["date"].unique())
        if len(days) > max_days:
            keep_set = set(days[-max_days:])
            grp = grp[grp["date"].isin(keep_set)]
        keep_frames.append(grp)
    return pd.concat(keep_frames, ignore_index=True) if keep_frames else pd.DataFrame(columns=cols)


def choose_adaptive_top_k(n_hist_days: int) -> int:
    """
    自适应相似开盘日 Top-K 匹配数量：
    - >= 20 个有效历史交易日 -> Top-12
    - 8 ~ 19 个有效历史交易日 -> min(10, max(5, int(n_days * 0.5)))
    - < 8 日 -> 0（优雅降级 insufficient_history）
    """
    if n_hist_days >= 20:
        return 12
    if n_hist_days >= 8:
        return min(10, max(5, int(n_hist_days * 0.5)))
    return 0


def _fill_day_close_curve(
    slot_map: Dict[str, List[float]],
    canonical_slots: List[str],
    fill_tail: bool = True,
) -> Tuple[Optional[float], List[Optional[float]], float, float]:
    """
    将单日槽位映射转为与 `canonical_slots` 等长的收盘价序列。
    - 历史日 `fill_tail=True`：对偶发缺失槽做前向平滑填补；
    - 当日实盘 `fill_tail=False`：仅填充至最后一个已有真实成交槽位，未来槽严格留 None。
    返回: (open_0, closes_list, day_high, day_low)
    """
    n = len(canonical_slots)
    raw_closes: List[Optional[float]] = [None] * n
    open_0: Optional[float] = None
    hi_v = -1.0
    lo_v = 1e18
    last_valid_idx = -1

    for i, sl in enumerate(canonical_slots):
        if sl in slot_map:
            op, hi, lo, cl, _ = slot_map[sl]
            if open_0 is None and op > 0:
                open_0 = op
            if cl > 0:
                raw_closes[i] = cl
                last_valid_idx = i
                hi_v = max(hi_v, hi if hi > 0 else cl)
                lo_v = min(lo_v, lo if lo > 0 else cl)

    if open_0 is None or last_valid_idx < 0:
        return None, [None] * n, 0.0, 0.0

    # 对内部偶发 1~2 根缺失做前向填充
    limit_idx = (n - 1) if fill_tail else last_valid_idx
    last_seen = open_0
    for i in range(limit_idx + 1):
        if raw_closes[i] is not None:
            last_seen = float(raw_closes[i])
        else:
            raw_closes[i] = last_seen

    return float(open_0), raw_closes, (hi_v if hi_v > 0 else float(open_0)), (lo_v if lo_v < 1e17 else float(open_0))


def _compute_divergence(
    actual_prices: List[Optional[float]],
    p25_prices: List[float],
    p75_prices: List[float],
    canonical_slots: List[str],
) -> Tuple[Optional[int], Dict[str, Any]]:
    latest_idx: Optional[int] = None
    latest_px: Optional[float] = None
    for i in range(len(actual_prices) - 1, -1, -1):
        v = actual_prices[i]
        if v is not None and v > 0:
            latest_idx = i
            latest_px = float(v)
            break
    if latest_idx is None or latest_px is None:
        return None, {
            "status": "WAIT_OPEN",
            "badge": "⏳ 等待开盘实盘数据",
            "color": "#94a3b8",
            "slot": None,
        }

    lo = float(p25_prices[latest_idx])
    hi = float(p75_prices[latest_idx])
    slot_lbl = canonical_slots[latest_idx]
    if latest_px > hi:
        diff_pct = ((latest_px - hi) / hi * 100.0) if hi > 0 else 0.0
        return latest_idx, {
            "status": "ABOVE_P75",
            "badge": f"🚀 突破推演上沿 (+{diff_pct:.2f}%)",
            "color": "#10b981",
            "slot": slot_lbl,
        }
    if latest_px < lo:
        diff_pct = ((latest_px - lo) / lo * 100.0) if lo > 0 else 0.0
        return latest_idx, {
            "status": "BELOW_P25",
            "badge": f"⚠️ 跌破推演下沿 ({diff_pct:.2f}%)",
            "color": "#ef4444",
            "slot": slot_lbl,
        }
    return latest_idx, {
        "status": "INSIDE_BAND",
        "badge": "✅ 走势贴合推演走廊",
        "color": "#38bdf8",
        "slot": slot_lbl,
    }


def build_single_ticker_corridor(
    ticker: str,
    day_slot_maps: Dict[str, Dict[str, List[float]]],
    meta: Dict[str, Any],
    forecast_date: Optional[str] = None,
    bench_gap_by_date: Optional[Dict[str, float]] = None,
    live_5m_rows: Optional[List[List[Any]]] = None,
    prev_locked_item: Optional[Dict[str, Any]] = None,
    spot_prev_close: Optional[float] = None,
    now_dt: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    为单只亚太/A 股标的构建严格零未来函数 (`hist_date < forecast_date`) 盘中推演走廊。
    纯函数设计，可直接离线单测。
    """
    tk = str(ticker).upper()
    mkt = str(meta.get("market") or "CN").upper()
    dec = int(meta.get("decimals") or 3)
    price_unit = str(meta.get("price_unit") or ("cny" if meta.get("kind") == "ASHARE_ETF" else "pts"))
    spec = get_market_spec(mkt)
    slots: List[str] = list(spec["slots"])
    n_slots = len(slots)
    min_hist_bars = max(12, int(n_slots * 0.55))

    all_dates = sorted(day_slot_maps.keys())
    if not forecast_date:
        forecast_date = all_dates[-1] if all_dates else _to_cst(now_dt).strftime("%Y-%m-%d")

    # 合并当日实时 5m 切片（若传入）
    today_map = dict(day_slot_maps.get(forecast_date) or {})
    if live_5m_rows:
        live_norm = _normalize_day_bars_to_canonical_slots(live_5m_rows, market=mkt, decimals=dec)
        if live_norm:
            today_map.update(live_norm)

    # 严格零穿越：仅允许 hist_date < forecast_date 的完整历史交易日参与特征计算与相似匹配
    hist_dates_raw = [d for d in all_dates if d < forecast_date]

    # 计算每个历史日的开盘价、收盘价、跳空幅度 (gap_pct)、日内振幅与 5 日 trailing 波动率
    day_stats: Dict[str, Dict[str, Any]] = {}
    prev_c_running: Optional[float] = None
    range_hist: List[float] = []

    for d_str in hist_dates_raw:
        smap = day_slot_maps.get(d_str) or {}
        op0, closes, hi_v, lo_v = _fill_day_close_curve(smap, slots, fill_tail=True)
        if op0 is None or op0 <= 0:
            continue
        last_c = float(closes[-1]) if closes[-1] is not None else op0
        intraday_range_pct = ((hi_v - lo_v) / op0 * 100.0) if (op0 > 0 and hi_v >= lo_v) else 1.5
        valid_cnt = len(smap)
        gap_pct = 0.0
        if prev_c_running and prev_c_running > 0:
            raw_gap = (op0 - prev_c_running) / prev_c_running * 100.0
            # 防护未复权除权异常跳变
            if abs(raw_gap) <= 22.0:
                gap_pct = raw_gap
        vol5 = float(np.mean(range_hist[-5:])) if range_hist else intraday_range_pct
        pct_curve = [round(((float(c) - op0) / op0) * 100.0, 4) for c in closes]
        if valid_cnt >= min_hist_bars:
            day_stats[d_str] = {
                "open": op0,
                "close": last_c,
                "gap_pct": gap_pct,
                "vol5": vol5,
                "range_pct": intraday_range_pct,
                "pct_curve": pct_curve,
                "end_pct": pct_curve[-1],
            }
        range_hist.append(intraday_range_pct)
        prev_c_running = last_c

    valid_hist_dates = sorted(day_stats.keys())
    n_hist = len(valid_hist_dates)

    # 当日实际 5m 轨迹与开盘锚
    today_open, today_closes, _, _ = _fill_day_close_curve(today_map, slots, fill_tail=False)
    prev_close = float(prev_c_running) if (prev_c_running and prev_c_running > 0) else (_safe_float(spot_prev_close) or 0.0)
    if today_open is None or today_open <= 0:
        today_open = prev_close if prev_close > 0 else 0.0

    target_gap = 0.0
    if prev_close > 0 and today_open > 0:
        raw_tgap = (today_open - prev_close) / prev_close * 100.0
        if abs(raw_tgap) <= 22.0:
            target_gap = round(raw_tgap, 2)
    target_vol5 = float(np.mean(range_hist[-5:])) if range_hist else 1.5

    # 校验是否已锁定同日走廊（锁定后绝不重绘 P25/P50/P75，仅增量追加实盘橙线与背离徽章）
    if (
        isinstance(prev_locked_item, dict)
        and prev_locked_item.get("status") == "ok"
        and prev_locked_item.get("snapshot_locked") is True
        and str(prev_locked_item.get("forecast_date") or "") == forecast_date
        and isinstance(prev_locked_item.get("p50_prices"), list)
        and len(prev_locked_item["p50_prices"]) == n_slots
    ):
        locked = dict(prev_locked_item)
        anc_open = float(locked.get("anchor_open") or today_open or 1.0)
        act_prices = [round(float(c), dec) if (c is not None and c > 0) else None for c in today_closes]
        act_pcts = [round(((float(c) - anc_open) / anc_open) * 100.0, 2) if (c is not None and c > 0 and anc_open > 0) else None for c in today_closes]
        latest_idx, div_obj = _compute_divergence(act_prices, locked["p25_prices"], locked["p75_prices"], slots)
        locked["actual_prices"] = act_prices
        locked["actual_pcts"] = act_pcts
        locked["latest_slot"] = latest_idx
        locked["divergence"] = div_obj
        return locked

    # 历史样本数不足 8 日 -> 优雅降级
    top_k = choose_adaptive_top_k(n_hist)
    if top_k <= 0 or today_open <= 0:
        return {
            "ticker": tk,
            "name": meta.get("name", tk),
            "market": mkt,
            "market_cn": spec["market_cn"],
            "price_unit": price_unit,
            "decimals": dec,
            "is_index": meta.get("kind") == "APAC_INDEX",
            "is_apac_corridor": True,
            "session_tz": spec["tz_label"],
            "session_label": spec["session_desc"],
            "forecast_date": forecast_date,
            "status": "insufficient_history",
            "status_message": f"正在每日累积 5m 分时样本（当前 {n_hist} 个完整交易日，满 8 日自动激活{spec['market_cn']}盘中推演走廊）",
            "summary_text": f"{tk} {spec['market_cn']} 5m 样本累积中 ({n_hist}/8日)",
            "history_days_count": n_hist,
            "matched_count": 0,
            "matched_dates": [],
            "snapshot_locked": False,
            "anchor_open": round(today_open, dec) if today_open > 0 else 0.0,
            "prev_close": round(prev_close, dec) if prev_close > 0 else 0.0,
            "gap_pct": target_gap,
            "time_slots": slots,
            "early_window_slot": spec["early_window_slot"],
            "early_window_label": spec["early_window_label"],
            "lunch_seam_idx": spec["lunch_seam_idx"],
            "divergence": {"status": "WAIT_HISTORY", "badge": "🐣 5m 样本累积中", "color": "#f59e0b", "slot": None},
        }

    # 计算相似日距离并选取 Top-K
    b_gaps = bench_gap_by_date if isinstance(bench_gap_by_date, dict) else {}
    target_bench_gap = float(b_gaps.get(forecast_date, target_gap))

    scored: List[Tuple[float, str]] = []
    for d_h in valid_hist_dates:
        st = day_stats[d_h]
        g_h = float(st["gap_pct"])
        v_h = float(st["vol5"])
        bg_h = float(b_gaps.get(d_h, g_h))

        # 跳空幅度距离 + 方向一致奖励
        sign_pen = 0.0
        if abs(target_gap) >= 0.25 and (g_h * target_gap < 0):
            sign_pen = 0.65
        dist = abs(g_h - target_gap) * 1.40 + sign_pen + abs(v_h - target_vol5) * 0.70 + abs(bg_h - target_bench_gap) * 0.55
        scored.append((dist, d_h))

    scored.sort(key=lambda x: (x[0], x[1]))
    matched_dates = [d for _, d in scored[:top_k]]
    k_used = len(matched_dates)

    # 计算分位走廊 P25 / P50 / P75
    mat = np.array([day_stats[d]["pct_curve"] for d in matched_dates], dtype=float)  # shape (K, n_slots)
    p25_pcts = np.percentile(mat, 25, axis=0)
    p50_pcts = np.percentile(mat, 50, axis=0)
    p75_pcts = np.percentile(mat, 75, axis=0)

    p25_prices = [round(today_open * (1.0 + float(x) / 100.0), dec) for x in p25_pcts]
    p50_prices = [round(today_open * (1.0 + float(x) / 100.0), dec) for x in p50_pcts]
    p75_prices = [round(today_open * (1.0 + float(x) / 100.0), dec) for x in p75_pcts]

    # 当日实盘橙线与动态背离徽章
    act_prices = [round(float(c), dec) if (c is not None and c > 0) else None for c in today_closes]
    act_pcts = [round(((float(c) - today_open) / today_open) * 100.0, 2) if (c is not None and c > 0) else None for c in today_closes]
    latest_idx, div_obj = _compute_divergence(act_prices, p25_prices, p75_prices, slots)

    # 形态统计与白话结论
    end_rets = [float(day_stats[d]["end_pct"]) for d in matched_dates]
    up_ratio = int(round(sum(1 for r in end_rets if r > 0.05) / max(1, k_used) * 100.0))
    down_ratio = int(round(sum(1 for r in end_rets if r < -0.05) / max(1, k_used) * 100.0))
    p50_end = float(p50_pcts[-1])
    p50_max = float(np.max(p50_pcts))
    p50_min = float(np.min(p50_pcts))

    if up_ratio >= 62 and p50_end >= 0.25:
        pattern_cn = f"{up_ratio}% 呈震荡走强，中位收于 {p50_end:+.2f}%"
    elif down_ratio >= 62 and p50_end <= -0.25:
        pattern_cn = f"{down_ratio}% 呈弱势承压，中位收于 {p50_end:+.2f}%"
    elif p50_max >= 0.45 and p50_end < p50_max - 0.35:
        pattern_cn = f"多呈冲高回落（中位高点 {p50_max:+.2f}% ➔ 收于 {p50_end:+.2f}%）"
    elif p50_min <= -0.45 and p50_end > p50_min + 0.35:
        pattern_cn = f"多呈探底回升（中位低点 {p50_min:+.2f}% ➔ 收于 {p50_end:+.2f}%）"
    else:
        dom_ratio = max(up_ratio, down_ratio, 50)
        bias_txt = "偏强震荡" if p50_end >= 0 else "偏弱震荡"
        pattern_cn = f"{dom_ratio}% 呈{bias_txt}，中位收于 {p50_end:+.2f}%"

    gap_word = f"开盘跳空 {target_gap:+.2f}%"
    summary_text = f"历史匹配 {k_used} 个相似{spec['market_cn']}开盘日 ({gap_word})：{pattern_cn}"

    has_live_open = any(v is not None and v > 0 for v in act_prices)
    anchor_label = f"{spec['open_label']} 开盘已锁定" if has_live_open else f"{spec['open_label']} 开盘参考"

    return {
        "ticker": tk,
        "name": meta.get("name", tk),
        "market": mkt,
        "market_cn": spec["market_cn"],
        "price_unit": price_unit,
        "decimals": dec,
        "is_index": meta.get("kind") == "APAC_INDEX",
        "is_apac_corridor": True,
        "session_tz": spec["tz_label"],
        "session_label": spec["session_desc"],
        "forecast_date": forecast_date,
        "status": "ok",
        "status_message": "OK",
        "summary_text": summary_text,
        "anchor_label": anchor_label,
        "anchor_open": round(today_open, dec),
        "prev_close": round(prev_close, dec) if prev_close > 0 else round(today_open, dec),
        "gap_pct": target_gap,
        "pmh": None,
        "pml": None,
        "snapshot_locked": bool(has_live_open),
        "history_days_count": n_hist,
        "matched_count": k_used,
        "matched_dates": matched_dates,
        "time_slots": slots,
        "early_window_slot": spec["early_window_slot"],
        "early_window_label": spec["early_window_label"],
        "lunch_seam_idx": spec["lunch_seam_idx"],
        "p25_pcts": [round(float(x), 3) for x in p25_pcts],
        "p50_pcts": [round(float(x), 3) for x in p50_pcts],
        "p75_pcts": [round(float(x), 3) for x in p75_pcts],
        "p25_prices": p25_prices,
        "p50_prices": p50_prices,
        "p75_prices": p75_prices,
        "actual_prices": act_prices,
        "actual_pcts": act_pcts,
        "latest_slot": latest_idx,
        "divergence": div_obj,
        "pattern_stats": {
            "up_ratio_pct": up_ratio,
            "down_ratio_pct": down_ratio,
            "p50_end_pct": round(p50_end, 2),
            "p50_high_pct": round(p50_max, 2),
            "p50_low_pct": round(p50_min, 2),
        },
    }


def _df_to_nested_slot_maps(df: pd.DataFrame) -> Dict[str, Dict[str, Dict[str, List[float]]]]:
    out: Dict[str, Dict[str, Dict[str, List[float]]]] = {}
    if df is None or df.empty:
        return out
    for row in df.itertuples(index=False):
        tk = str(row.ticker).upper()
        d_str = str(row.date)
        sl = str(row.time_slot)
        out.setdefault(tk, {}).setdefault(d_str, {})[sl] = [
            float(row.open), float(row.high), float(row.low), float(row.close), float(row.volume)
        ]
    return out


def _build_benchmark_gaps(nested: Dict[str, Dict[str, Dict[str, List[float]]]]) -> Dict[str, Dict[str, float]]:
    """计算各市场代表基准指数的每日跳空序列（CN 用 000688/399006，HK 用 ^HSI，JP 用 ^N225，TW 用 ^TWII）。"""
    bench_map: Dict[str, Dict[str, float]] = {}
    for mkt, bench_tk in (("CN", "000688"), ("HK", "^HSI"), ("JP", "^N225"), ("TW", "^TWII")):
        spec = get_market_spec(mkt)
        slots = spec["slots"]
        dmaps = nested.get(bench_tk) or {}
        gaps: Dict[str, float] = {}
        prev_c: Optional[float] = None
        for d_str in sorted(dmaps.keys()):
            op0, closes, _, _ = _fill_day_close_curve(dmaps[d_str], slots, fill_tail=True)
            if op0 and op0 > 0:
                if prev_c and prev_c > 0:
                    g = (op0 - prev_c) / prev_c * 100.0
                    if abs(g) <= 22.0:
                        gaps[d_str] = round(g, 2)
                last_c = float(closes[-1]) if closes[-1] else op0
                prev_c = last_c
        bench_map[mkt] = gaps
    return bench_map


def should_refresh_apac_intraday(
    cached_doc: Dict[str, Any],
    parquet_exists: bool,
    now_dt: Optional[datetime] = None,
    force_refresh: bool = False,
) -> Tuple[str, bool]:
    """
    判定调度模式，严格保证 60s 主循环休眠期 0 HTTP：
    返回: (mode, need_http_fetch)
      - COLD_START_FULL: 首次缓存或 parquet 缺失/不完整 -> 立即全量拉取 60d 5m 并初算；
      - POST_CLOSE_APPEND: 周一至周五 >= 16:15 CST 且今日尚未增量追加底模 -> 拉取近 5d 增量追加；
      - INTRADAY_LIVE_UPDATE: 亚盘活跃窗内 -> 0 HTTP，直接用 apac_doc 里的实时 5m 增量更新橙线；
      - SLEEP_CACHE: 非亚盘活跃窗（含美股夜间）且缓存完好 -> 0 HTTP，0ms 直读返回。
    """
    if force_refresh:
        return "FORCE_FULL", True
    if not parquet_exists or not isinstance(cached_doc, dict) or not isinstance(cached_doc.get("by_ticker"), dict):
        return "COLD_START_FULL", True
    if len(cached_doc.get("by_ticker") or {}) < len(aaf.ALL_APAC_ASHARE_TARGETS):
        return "COLD_START_FULL", True

    cst_now = _to_cst(now_dt)
    today_str = cst_now.strftime("%Y-%m-%d")
    cst_min = cst_now.hour * 60 + cst_now.minute

    # 亚盘收盘后每日增量追加底模一次 (>= 16:15 CST)
    if cst_now.weekday() < 5 and cst_min >= POST_CLOSE_APPEND_CST_MIN:
        if str(cached_doc.get("last_parquet_refresh_date") or "") != today_str:
            return "POST_CLOSE_APPEND", True

    # 亚盘活跃窗内：复用主雷达刚拉取的 apac_doc 实时 5m 更新橙线（0 额外 HTTP）
    if aaf.is_apac_active_window(now_dt):
        return "INTRADAY_LIVE_UPDATE", False

    return "SLEEP_CACHE", False


def get_or_update_apac_intraday_forecasts(
    parquet_path: str = DEFAULT_APAC_5M_PARQUET_PATH,
    json_path: str = DEFAULT_APAC_FORECASTS_JSON_PATH,
    apac_doc: Optional[Dict[str, Any]] = None,
    now_dt: Optional[datetime] = None,
    force_refresh: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    对外唯一调度入口：
    - 休眠期严格 `SLEEP_CACHE` (`http_calls = 0`, 0ms 拖慢)；
    - 冷启动立即全量初算全部 16 只标的并落盘 parquet + json；
    - 亚盘收盘后每日一次 5d 增量追加至 60d 滚动底模；
    - 亚盘盘中复用 `apac_doc` 实时 5m 增量画橙线与更新背离徽章。
    """
    t0 = time.perf_counter()
    cached = load_apac_intraday_forecasts(json_path)
    mode, need_http = should_refresh_apac_intraday(
        cached_doc=cached,
        parquet_exists=os.path.exists(parquet_path),
        now_dt=now_dt,
        force_refresh=force_refresh,
    )

    if mode == "SLEEP_CACHE":
        out = dict(cached)
        out["fetch_mode"] = "SLEEP_CACHE"
        out["http_calls"] = 0
        out["elapsed_ms"] = 0.0
        return out

    cst_now = _to_cst(now_dt)
    today_cst = cst_now.strftime("%Y-%m-%d")
    hist_df = _load_5m_parquet(parquet_path)
    http_calls = 0
    last_pq_date = str(cached.get("last_parquet_refresh_date") or "")

    if need_http:
        fetch_period = "60d" if (mode in ("COLD_START_FULL", "FORCE_FULL") or hist_df.empty) else "5d"
        new_df, http_calls = fetch_apac_5m_history_batch(period=fetch_period)
        if not new_df.empty:
            hist_df = merge_and_trim_5m_parquet(hist_df, new_df, max_days=ROLLING_MAX_TRADING_DAYS)
            if not dry_run:
                _save_5m_parquet_atomic(hist_df, parquet_path=parquet_path)
            if cst_now.weekday() < 5 and (cst_now.hour * 60 + cst_now.minute) >= POST_CLOSE_APPEND_CST_MIN:
                last_pq_date = today_cst

    nested = _df_to_nested_slot_maps(hist_df)
    bench_gaps = _build_benchmark_gaps(nested)
    apac_items = (apac_doc.get("items") if isinstance(apac_doc, dict) else None) or (aaf.load_apac_ashare_cache().get("items") or {})
    prev_by_tk = cached.get("by_ticker") if isinstance(cached.get("by_ticker"), dict) else {}

    by_ticker: Dict[str, Dict[str, Any]] = {}
    ok_cnt = 0
    insuff_cnt = 0
    forecast_dates: List[str] = []

    for meta in aaf.ALL_APAC_ASHARE_TARGETS:
        tk = str(meta["ticker"]).upper()
        mkt = str(meta.get("market") or "CN").upper()
        spec = get_market_spec(mkt)
        dmaps = nested.get(tk) or {}
        snap_item = apac_items.get(tk) if isinstance(apac_items, dict) else None
        live_rows = (snap_item.get("intraday_5m") if isinstance(snap_item, dict) else None) or None
        spot_prev_c = _safe_float(snap_item.get("prev_close")) if isinstance(snap_item, dict) else None

        # 本地市场当前日期与是否已开盘判定
        loc_now = cst_now.astimezone(spec["tz"])
        loc_today = loc_now.strftime("%Y-%m-%d")
        loc_min = loc_now.hour * 60 + loc_now.minute
        market_opened_today = (loc_now.weekday() < 5) and (loc_min >= int(spec.get("open_min", 9 * 60 + 30)))

        # 盘中实时补齐：若 apac_doc 缺少该标的当日 5m（典型如 HSTECH）或尚无今日实盘柱，走免费实时分时/5m 回退链
        if mode in ("INTRADAY_LIVE_UPDATE", "FORCE_FULL", "COLD_START_FULL") and market_opened_today:
            has_today_in_parquet = bool(dmaps.get(loc_today))
            if (not live_rows) and (not has_today_in_parquet or tk == "HSTECH"):
                fb_rows, fb_calls, _fb_src = fetch_live_intraday_5m_for_ticker(meta, expected_date=loc_today)
                http_calls += fb_calls
                if fb_rows:
                    live_rows = fb_rows

        # 目标日：若处于亚盘盘中/已开盘且拿到今日 live_rows 用今日，否则锚定底模最新已完成交易日
        if market_opened_today and live_rows:
            tgt_date = loc_today
        else:
            tgt_date = max(dmaps.keys()) if dmaps else loc_today

        item = build_single_ticker_corridor(
            ticker=tk,
            day_slot_maps=dmaps,
            meta=meta,
            forecast_date=tgt_date,
            bench_gap_by_date=bench_gaps.get(mkt),
            live_5m_rows=live_rows if (market_opened_today and live_rows) else None,
            prev_locked_item=prev_by_tk.get(tk),
            spot_prev_close=spot_prev_c,
            now_dt=now_dt,
        )
        by_ticker[tk] = item
        if item.get("status") == "ok":
            ok_cnt += 1
        else:
            insuff_cnt += 1
        if item.get("forecast_date"):
            forecast_dates.append(str(item["forecast_date"]))

    doc: Dict[str, Any] = {
        "schema_version": "v5.8-apac-intraday-corridor",
        "updated_at_cst": cst_now.strftime("%Y-%m-%d %H:%M:%S CST"),
        "forecast_date": max(forecast_dates) if forecast_dates else today_cst,
        "last_parquet_refresh_date": last_pq_date,
        "fetch_mode": mode,
        "http_calls": http_calls,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        "coverage": {
            "total_count": len(by_ticker),
            "ok_count": ok_cnt,
            "insufficient_history_count": insuff_cnt,
        },
        "by_ticker": by_ticker,
    }
    if by_ticker and not dry_run:
        _save_json_atomic(doc, json_path)
    return doc


if __name__ == "__main__":
    res = get_or_update_apac_intraday_forecasts(force_refresh=True)
    cov = res.get("coverage") or {}
    print(
        f"[+] APAC Intraday Corridor Done: ok={cov.get('ok_count')}/{cov.get('total_count')} | "
        f"insuff={cov.get('insufficient_history_count')} | mode={res.get('fetch_mode')} | "
        f"http={res.get('http_calls')} | elapsed={res.get('elapsed_ms')}ms | date={res.get('forecast_date')}"
    )
    for tk, it in (res.get("by_ticker") or {}).items():
        div_b = (it.get("divergence") or {}).get("badge", "--")
        print(
            f"  {tk:8s} ({it.get('market'):2s} {len(it.get('time_slots') or []):2d}bar) | "
            f"hist={it.get('history_days_count'):2d}d | K={it.get('matched_count'):2d} | "
            f"st={it.get('status'):4s} | {div_b:18s} | {it.get('summary_text')}"
        )
