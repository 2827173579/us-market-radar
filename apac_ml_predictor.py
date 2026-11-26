# -*- coding: utf-8 -*-
"""
亚太/A股 16 只标的「物理隔离 AI 胜率引擎」 (apac_ml_predictor.py)
==================================================================
严格隔离红线：
1. 与美股 `ml_predictor_5y.py` 训练/推理/缓存完全物理隔离，独立输出 `data_cache/apac_ml_predictions.json`；
2. 覆盖全部 16 只亚太/A股标的（6 只亚太纯指数 + 10 只 A 股场内 ETF），严禁消耗 Finnhub 额度；
3. 前复权日线兼容次新 ETF：满 60 交易日即可推理，满 120 交易日纳入面板训练，不足 250 日打 `次新样本` 轻标；
4. 严格因果对齐 `us_date < apac_date`（严禁使用 `<=` 未来函数泄露美股当日未开盘行情）；
5. 防 50% 塌缩架构：
   - 主体由本土横截面技术面因子（MA20/MA50 偏离、14D RSI、ATR%、量比、60日位置、20日超额 RS、均线斜率与动量）驱动；
   - 采用带 Purged 5 日间隔的时间序列 Walk-Forward 扩展窗验证与 Platt 逻辑校准，保留真实横截面区分度；
   - 隔夜美股外盘因子（`SOXX / QQQ / ^VIX` 严格 `T-1` 昨收变化）独立建模并做权重硬封顶（最大概率微调 ±5.5%），
     防止单一隔夜外盘因子把 16 只标的拍扁到同一胜率；
6. 每日仅在亚盘收盘后（>= 16:15 CST / 17:15 KST）增量重算一次；休眠期直读缓存（0 HTTP / 0ms 拖慢）；
   若首次上线/冷启动本地缓存缺失或过期，则立即执行一次全量初始计算。
"""

from __future__ import annotations

import bisect
import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

import apac_ashare_feed as aaf

try:
    from zoneinfo import ZoneInfo

    CST_TZ = ZoneInfo("Asia/Shanghai")
    KST_TZ = ZoneInfo("Asia/Seoul")
except Exception:
    CST_TZ = timezone(timedelta(hours=8))
    KST_TZ = timezone(timedelta(hours=9))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_APAC_ML_CACHE_PATH = os.path.join(BASE_DIR, "data_cache", "apac_ml_predictions.json")

# 样本门槛策略（兼容次新 ETF）
MIN_INFER_DAYS = 60
MIN_TRAIN_DAYS = 120
NEW_LISTING_DAYS = 250

# 预测前瞻窗口与胜率标签阈值
HORIZON_DAYS = 5
LONG_RET_THRESHOLD = 0.005    # 未来 5 交易日涨幅 > +0.5%
SHORT_RET_THRESHOLD = -0.005  # 未来 5 交易日跌幅 < -0.5%

# 美股外盘因子概率微调硬上限（防外盘把 16 只横截面压扁）
MAX_US_MACRO_ADJ_PCT = 5.5

# 亚盘收盘后增量重算触发时间 (16:15 CST == 17:15 KST)
POST_CLOSE_REFRESH_CST_MIN = 16 * 60 + 15

# 各标的相对美股科技/半导体的传导敏感度系数（用于封顶后的外盘微调）
TICKER_US_BETA: Dict[str, float] = {
    "^TWII": 1.00,
    "^N225": 0.90,
    "HSTECH": 0.95,
    "^HSI": 0.75,
    "000688": 0.85,
    "399006": 0.80,
    "588200": 1.00,
    "588170": 1.00,
    "159995": 0.95,
    "515880": 0.95,
    "159530": 0.85,
    "512880": 0.55,
    "512800": 0.35,
    "512530": 0.35,
    "512690": 0.40,
    "159562": 0.45,
}

LOCAL_FEATURE_COLS: List[str] = [
    "bias_ma20",
    "bias_ma50",
    "ma5_vs_ma20",
    "ma20_vs_ma50",
    "ma20_slope_5d",
    "rsi_14_norm",
    "bb_z20",
    "atr_pct",
    "vol_ratio_log",
    "pos_60d",
    "dist_high_20d",
    "up_ratio_10d",
    "rs_20d",
    "ret_5d",
    "ret_10d",
    "ret_20d",
    "ret_60d",
    "vol_20d",
    "cs_rs_z",
    "cs_ret20_z",
    "cs_rsi_z",
    "cs_bias20_z",
]

US_MACRO_COLS: List[str] = [
    "us_soxx_1d",
    "us_qqq_1d",
    "us_vix_1d",
    "us_vix_lvl",
]


def _safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "" or isinstance(val, bool):
            return default
        f = float(val)
        return f if math.isfinite(f) else default
    except Exception:
        return default


def _to_cst_datetime(now_dt: Optional[datetime] = None) -> datetime:
    if now_dt is None:
        return datetime.now(CST_TZ)
    if now_dt.tzinfo is None:
        return now_dt.replace(tzinfo=CST_TZ)
    return now_dt.astimezone(CST_TZ)


def load_apac_ml_predictions(cache_path: str = DEFAULT_APAC_ML_CACHE_PATH) -> Dict[str, Any]:
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _save_apac_ml_predictions(doc: Dict[str, Any], cache_path: str = DEFAULT_APAC_ML_CACHE_PATH) -> None:
    parent = os.path.dirname(os.path.abspath(cache_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{cache_path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
        os.replace(tmp, cache_path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def align_us_macro_strict_lag(
    apac_dates: pd.DatetimeIndex,
    us_macro_df: pd.DataFrame,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    严格因果外盘对齐：对每个亚太交易日 `d_apac`，仅允许映射严格更早的美股收盘日 `d_us < d_apac`。
    采用 ISO `YYYY-MM-DD` 字符串二分查找 (`bisect_left - 1`)，100% 免疫 Pandas 2/3/4 `ns/us/ms`
    时间戳精度差异与时区偏差，数学上杜绝 `d_us >= d_apac` 未来函数泄露。

    返回:
        (aligned_df, matched_us_dates_str_list)
    """
    apac_strs = [pd.to_datetime(d).strftime("%Y-%m-%d") for d in apac_dates]
    cols = list(US_MACRO_COLS)
    if us_macro_df is None or us_macro_df.empty or len(apac_strs) == 0:
        zeros = pd.DataFrame(0.0, index=apac_dates, columns=cols)
        return zeros, ["" for _ in range(len(apac_strs))]

    us_clean = us_macro_df.copy()
    us_clean["_date_str"] = [pd.to_datetime(d).strftime("%Y-%m-%d") for d in us_clean.index]
    us_clean = us_clean.drop_duplicates(subset=["_date_str"], keep="last").sort_values("_date_str")
    us_strs = us_clean["_date_str"].tolist()
    us_vals = us_clean.reindex(columns=cols).fillna(0.0).to_numpy(dtype=float)

    out_arr = np.zeros((len(apac_strs), len(cols)), dtype=float)
    matched_dates: List[str] = []
    for i, d_ap in enumerate(apac_strs):
        # bisect_left 返回首个 >= d_ap 的位置，减 1 即严格满足 us_strs[pos] < d_ap 的最新美股收盘日
        pos = bisect.bisect_left(us_strs, d_ap) - 1
        if pos >= 0:
            out_arr[i] = us_vals[pos]
            matched_dates.append(us_strs[pos])
        else:
            matched_dates.append("")
    return pd.DataFrame(out_arr, index=apac_dates, columns=cols), matched_dates


def _fetch_tencent_qfq_df(qt_sym: str, max_bars: int = 800, timeout: float = 8.0) -> pd.DataFrame:
    if not qt_sym:
        return pd.DataFrame()
    try:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={qt_sym},day,,,{max_bars},qfq"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        if r.status_code != 200:
            return pd.DataFrame()
        payload = r.json()
        node = ((payload or {}).get("data") or {}).get(qt_sym) or {}
        raw_rows = node.get("qfqday") or node.get("day") or []
        if not raw_rows:
            return pd.DataFrame()
        recs = []
        for item in raw_rows:
            if not isinstance(item, (list, tuple)) or len(item) < 5:
                continue
            dt_s = str(item[0])[:10]
            op = _safe_float(item[1])
            cl = _safe_float(item[2])
            hi = _safe_float(item[3])
            lo = _safe_float(item[4])
            vo = _safe_float(item[5], 0.0) if len(item) > 5 else 0.0
            if op and hi and lo and cl and cl > 0:
                recs.append((dt_s, op, hi, lo, cl, max(0.0, vo or 0.0)))
        if len(recs) < 20:
            return pd.DataFrame()
        df = pd.DataFrame(recs, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.drop_duplicates(subset=["Date"], keep="last").set_index("Date").sort_index()
        return df
    except Exception:
        return pd.DataFrame()


def _fetch_yf_daily_batch(symbols: List[str], period: str = "4y") -> Dict[str, pd.DataFrame]:
    import yfinance as yf

    out: Dict[str, pd.DataFrame] = {}
    syms = [s for s in dict.fromkeys(symbols) if s]
    if not syms:
        return out
    try:
        df = yf.download(syms, period=period, interval="1d", auto_adjust=True, progress=False)
        if df is None or getattr(df, "empty", True):
            return out
        if isinstance(df.columns, pd.MultiIndex):
            lvl0 = set(df.columns.get_level_values(0))
            if "Close" in lvl0:
                for s in syms:
                    try:
                        if s not in df["Close"].columns:
                            continue
                        sub = pd.DataFrame({
                            "Open": df["Open"][s],
                            "High": df["High"][s],
                            "Low": df["Low"][s],
                            "Close": df["Close"][s],
                            "Volume": df["Volume"][s] if "Volume" in lvl0 else 0.0,
                        }).dropna(subset=["Close"])
                        if not sub.empty:
                            sub.index = pd.DatetimeIndex(pd.to_datetime(sub.index)).tz_localize(None).normalize()
                            out[s] = sub[~sub.index.duplicated(keep="last")].sort_index()
                    except Exception:
                        continue
        elif len(syms) == 1 and "Close" in df.columns:
            sub = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"]).copy()
            sub.index = pd.DatetimeIndex(pd.to_datetime(sub.index)).tz_localize(None).normalize()
            out[syms[0]] = sub[~sub.index.duplicated(keep="last")].sort_index()
    except Exception:
        pass
    return out


def fetch_apac_and_us_history(
    targets: Optional[List[Dict[str, Any]]] = None,
    max_bars: int = 800,
) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame]:
    """
    拉取全部 16 只亚太/A股前复权历史日线 + 美股外盘基准 (`SOXX`, `QQQ`, `^VIX`)。
    零 Finnhub 消耗。
    """
    tgt_list = targets if targets is not None else aaf.ALL_APAC_ASHARE_TARGETS
    yf_needed = ["SOXX", "QQQ", "^VIX"]
    for m in tgt_list:
        yfs = str(m.get("yf_symbol") or "")
        if yfs and (m.get("source_pref") == "yfinance" or not m.get("qt_symbol")):
            yf_needed.append(yfs)

    yf_map = _fetch_yf_daily_batch(yf_needed, period="4y")

    # 组装美股外盘因子表（仅保留 SOXX/QQQ 真实美股交易日，过滤美股休市日 ^VIX 孤立占位柱）
    us_parts: Dict[str, pd.Series] = {}
    equity_idx: Optional[pd.DatetimeIndex] = None
    if "SOXX" in yf_map:
        us_parts["us_soxx_1d"] = yf_map["SOXX"]["Close"].pct_change().clip(-0.15, 0.15)
        equity_idx = yf_map["SOXX"].index
    if "QQQ" in yf_map:
        us_parts["us_qqq_1d"] = yf_map["QQQ"]["Close"].pct_change().clip(-0.12, 0.12)
        equity_idx = yf_map["QQQ"].index if equity_idx is None else equity_idx.union(yf_map["QQQ"].index)
    if "^VIX" in yf_map:
        vix_c = yf_map["^VIX"]["Close"]
        us_parts["us_vix_1d"] = vix_c.pct_change().clip(-0.40, 0.60)
        us_parts["us_vix_lvl"] = ((vix_c - 18.0) / 10.0).clip(-1.5, 4.0)
    us_macro_df = pd.DataFrame(us_parts).dropna(how="all").sort_index() if us_parts else pd.DataFrame(columns=US_MACRO_COLS)
    if equity_idx is not None and not us_macro_df.empty:
        us_macro_df = us_macro_df.loc[us_macro_df.index.isin(equity_idx)]
    us_macro_df = us_macro_df.fillna(0.0)
    for col in US_MACRO_COLS:
        if col not in us_macro_df.columns:
            us_macro_df[col] = 0.0
    us_macro_df = us_macro_df[US_MACRO_COLS]

    # 拉取 16 只亚太/A股历史前复权日线
    hist_map: Dict[str, pd.DataFrame] = {}
    yf_fallback_syms: List[Tuple[str, str]] = []
    for meta in tgt_list:
        tk = str(meta["ticker"]).upper()
        qsym = str(meta.get("qt_symbol") or "")
        yfsym = str(meta.get("yf_symbol") or "")
        df = pd.DataFrame()
        if meta.get("source_pref") == "yfinance" and yfsym in yf_map:
            df = yf_map[yfsym].tail(max_bars).copy()
        elif qsym:
            df = _fetch_tencent_qfq_df(qsym, max_bars=max_bars)
            if len(df) < MIN_INFER_DAYS and yfsym in yf_map:
                df = yf_map[yfsym].tail(max_bars).copy()
            elif len(df) < MIN_INFER_DAYS and yfsym:
                yf_fallback_syms.append((tk, yfsym))
        elif yfsym in yf_map:
            df = yf_map[yfsym].tail(max_bars).copy()

        if not df.empty:
            hist_map[tk] = df

    if yf_fallback_syms:
        fb_map = _fetch_yf_daily_batch([y for _, y in yf_fallback_syms], period="4y")
        for tk, yfsym in yf_fallback_syms:
            if yfsym in fb_map and len(fb_map[yfsym]) > len(hist_map.get(tk, pd.DataFrame())):
                hist_map[tk] = fb_map[yfsym].tail(max_bars).copy()

    return hist_map, us_macro_df


def _compute_single_local_features(df: pd.DataFrame) -> pd.DataFrame:
    """计算单只标的本土技术面因子与未来 5 日收益标签（零外部泄露）。"""
    d = df.sort_index().copy()
    close = d["Close"].astype(float)
    high = d["High"].astype(float)
    low = d["Low"].astype(float)
    vol = d["Volume"].astype(float).fillna(0.0)

    ma5 = close.rolling(5, min_periods=3).mean()
    ma20 = close.rolling(20, min_periods=10).mean()
    ma50 = close.rolling(50, min_periods=20).mean()
    bias_ma20 = ((close - ma20) / ma20.replace(0.0, np.nan)).clip(-0.35, 0.35)
    bias_ma50 = ((close - ma50) / ma50.replace(0.0, np.nan)).clip(-0.50, 0.50)
    ma5_vs_ma20 = ((ma5 - ma20) / ma20.replace(0.0, np.nan)).clip(-0.25, 0.25)
    ma20_vs_ma50 = ((ma20 - ma50) / ma50.replace(0.0, np.nan)).clip(-0.35, 0.35)
    ma20_slope_5d = (ma20 / ma20.shift(5).replace(0.0, np.nan) - 1.0).clip(-0.20, 0.20)

    # 20日布林 Z-score
    std20 = close.rolling(20, min_periods=10).std().replace(0.0, np.nan)
    bb_z20 = ((close - ma20) / std20).clip(-3.5, 3.5).fillna(0.0)

    # Wilder RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / 14.0, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / 14.0, adjust=False).mean()
    rs = gain / loss.replace(0.0, np.nan)
    rsi14 = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)
    rsi_14_norm = (rsi14 - 50.0) / 50.0

    # ATR(14)%
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr14 = tr.rolling(14, min_periods=5).mean()
    atr_pct = (atr14 / close.replace(0.0, np.nan)).clip(0.002, 0.15)

    # 量比 (当无成交量或为 0 时平滑为 1.0 -> log(1.0)=0)
    vol_ma20 = vol.rolling(20, min_periods=5).mean()
    vol_ratio = np.where((vol_ma20 > 0) & (vol > 0), (vol / vol_ma20).clip(0.2, 4.0), 1.0)
    vol_ratio_log = np.log(vol_ratio)

    # 60日区间位置 & 20日距高点回撤 & 近10日阳线占比
    hi60 = high.rolling(60, min_periods=20).max()
    lo60 = low.rolling(60, min_periods=20).min()
    span60 = (hi60 - lo60).replace(0.0, np.nan)
    pos_60d = ((close - lo60) / span60).clip(0.0, 1.0).fillna(0.5)
    hi20 = high.rolling(20, min_periods=10).max().replace(0.0, np.nan)
    dist_high_20d = ((close - hi20) / hi20).clip(-0.40, 0.0).fillna(0.0)
    up_ratio_10d = (close.pct_change() > 0).astype(float).rolling(10, min_periods=5).mean().fillna(0.5)

    ret_5d = close.pct_change(5).clip(-0.30, 0.30)
    ret_10d = close.pct_change(10).clip(-0.40, 0.40)
    ret_20d = close.pct_change(20).clip(-0.50, 0.50)
    ret_60d = close.pct_change(60).clip(-0.70, 0.70).fillna(ret_20d)
    vol_20d = close.pct_change().rolling(20, min_periods=10).std().clip(0.002, 0.10)

    fwd_5d_ret = (close.shift(-HORIZON_DAYS) / close) - 1.0

    out = pd.DataFrame({
        "close": close,
        "bias_ma20": bias_ma20,
        "bias_ma50": bias_ma50,
        "ma5_vs_ma20": ma5_vs_ma20,
        "ma20_vs_ma50": ma20_vs_ma50,
        "ma20_slope_5d": ma20_slope_5d,
        "rsi_14": rsi14,
        "rsi_14_norm": rsi_14_norm,
        "bb_z20": bb_z20,
        "atr_pct": atr_pct,
        "vol_ratio": vol_ratio,
        "vol_ratio_log": vol_ratio_log,
        "pos_60d": pos_60d,
        "dist_high_20d": dist_high_20d,
        "up_ratio_10d": up_ratio_10d,
        "ret_5d": ret_5d,
        "ret_10d": ret_10d,
        "ret_20d": ret_20d,
        "ret_60d": ret_60d,
        "vol_20d": vol_20d,
        "fwd_5d_ret": fwd_5d_ret,
    }, index=d.index)
    return out


def build_apac_panel(
    hist_map: Dict[str, pd.DataFrame],
    us_macro_df: pd.DataFrame,
    meta_by_ticker: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """
    构建 16 只标的的横截面面板数据集（含 20 日超额 RS + 横截面 Z-Score + 严格 `us_date < apac_date` 美股外盘因子）。
    """
    m_map = meta_by_ticker if meta_by_ticker is not None else aaf.APAC_ASHARE_BY_TICKER
    per_tk: Dict[str, pd.DataFrame] = {}
    sample_counts: Dict[str, int] = {}

    for tk, df in hist_map.items():
        if df is None or len(df) < MIN_INFER_DAYS:
            sample_counts[tk] = 0 if df is None else len(df)
            continue
        feat = _compute_single_local_features(df)
        sample_counts[tk] = len(df)
        per_tk[tk] = feat

    if not per_tk:
        return pd.DataFrame(), sample_counts

    # 计算两套本土基准 20 日收益序列：
    # 1) A 股基准：000688 / 399006 / 512530 均值（等权覆盖硬科技+成长+红利）
    cn_bench_series = [per_tk[b]["ret_20d"] for b in ("000688", "399006", "512530") if b in per_tk]
    cn_bench_ret20 = pd.concat(cn_bench_series, axis=1).mean(axis=1) if cn_bench_series else pd.Series(dtype=float)

    # 2) 亚太纯指数区域均值基准
    idx_tickers = [t for t in per_tk if (m_map.get(t, {}).get("kind") == "APAC_INDEX")]
    idx_bench_series = [per_tk[b]["ret_20d"] for b in idx_tickers if b in per_tk]
    idx_bench_ret20 = pd.concat(idx_bench_series, axis=1).mean(axis=1) if idx_bench_series else pd.Series(dtype=float)

    frames: List[pd.DataFrame] = []
    for tk, feat in per_tk.items():
        meta = m_map.get(tk, {})
        kind = str(meta.get("kind") or "ASHARE_ETF")
        f = feat.copy()
        if kind == "ASHARE_ETF" and not cn_bench_ret20.empty:
            bench = cn_bench_ret20.reindex(f.index).ffill().fillna(0.0)
        elif not idx_bench_ret20.empty:
            bench = idx_bench_ret20.reindex(f.index).ffill().fillna(0.0)
        else:
            bench = pd.Series(0.0, index=f.index)
        f["rs_20d"] = (f["ret_20d"] - bench).clip(-0.40, 0.40)

        # 严格因果外盘对齐 us_date < apac_date
        us_aligned, matched_us_dates = align_us_macro_strict_lag(f.index, us_macro_df)
        for col in US_MACRO_COLS:
            f[col] = us_aligned[col].to_numpy()
        f["us_lead_date"] = matched_us_dates
        f["ticker"] = tk
        f["date"] = f.index
        f["sample_days"] = sample_counts[tk]
        f["eligible_train"] = sample_counts[tk] >= MIN_TRAIN_DAYS
        frames.append(f)

    panel = pd.concat(frames, ignore_index=True)

    # 同日横截面标准化特征（放大 16 只标的真实强弱差异，彻底防止模型输出趋同于 50%）
    for src_col, dst_col in (
        ("rs_20d", "cs_rs_z"),
        ("ret_20d", "cs_ret20_z"),
        ("rsi_14_norm", "cs_rsi_z"),
        ("bias_ma20", "cs_bias20_z"),
    ):
        grp = panel.groupby("date")[src_col]
        mu = grp.transform("mean")
        sd = grp.transform("std").replace(0.0, np.nan).fillna(1.0)
        panel[dst_col] = ((panel[src_col] - mu) / sd).clip(-3.0, 3.0).fillna(0.0)

    return panel.sort_values(["date", "ticker"]).reset_index(drop=True), sample_counts


def _fit_local_ensemble_and_predict(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_pred: np.ndarray,
) -> np.ndarray:
    """
    正则化线性 + 浅层梯度提升 50/50 融合模型（防过拟合且保留平滑横截面分化度）。
    """
    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_train)
    X_pr_s = scaler.transform(X_pred)

    lr = LogisticRegression(C=0.45, max_iter=400, class_weight="balanced", random_state=42)
    lr.fit(X_tr_s, y_train)
    p_lr = lr.predict_proba(X_pr_s)[:, 1]

    hgb = HistGradientBoostingClassifier(
        max_depth=4,
        max_leaf_nodes=12,
        min_samples_leaf=25,
        l2_regularization=2.0,
        learning_rate=0.05,
        max_iter=140,
        random_state=42,
    )
    hgb.fit(X_train, y_train)
    p_hgb = hgb.predict_proba(X_pred)[:, 1]
    return 0.45 * p_lr + 0.55 * p_hgb


def _walk_forward_train_and_calibrate(
    train_df: pd.DataFrame,
    infer_df: pd.DataFrame,
    target_col: str,
    n_splits: int = 5,
) -> Tuple[np.ndarray, np.ndarray, float, np.ndarray]:
    """
    带 Purged 5 日防泄露间隔的时间序列 Walk-Forward 扩展窗验证 + Platt 逻辑校准：
    1. 本土模型仅用 LOCAL_FEATURE_COLS 训练与校准，保证 16 只标的真实横截面区分度；
    2. 独立拟合 US_MACRO_COLS 小模型，输出概率微调并硬封顶在 ±MAX_US_MACRO_ADJ_PCT；
    返回: (calibrated_prob_pct, raw_local_prob_pct, wf_auc, us_adj_pct)
    """
    valid = train_df.dropna(subset=LOCAL_FEATURE_COLS + [target_col]).copy()
    valid = valid.sort_values("date").reset_index(drop=True)
    unique_dates = np.sort(valid["date"].unique())

    X_inf_local = infer_df[LOCAL_FEATURE_COLS].fillna(0.0).to_numpy(dtype=float)
    X_inf_us = infer_df[US_MACRO_COLS].fillna(0.0).to_numpy(dtype=float)

    if len(valid) < 120 or len(unique_dates) < 40 or valid[target_col].nunique() < 2:
        neutral = np.full(len(infer_df), 50.0, dtype=float)
        return neutral, neutral, 0.50, np.zeros(len(infer_df), dtype=float)

    # Expanding Walk-Forward with 5-trading-day purge gap
    oof_probs: List[float] = []
    oof_targets: List[int] = []
    start_idx = max(30, int(len(unique_dates) * 0.40))
    step = max(10, (len(unique_dates) - start_idx) // max(1, n_splits))

    for fold in range(n_splits):
        val_start_i = start_idx + fold * step
        val_end_i = len(unique_dates) if fold == n_splits - 1 else min(len(unique_dates), val_start_i + step)
        if val_start_i >= len(unique_dates) or val_end_i <= val_start_i:
            break
        train_cutoff_i = val_start_i - HORIZON_DAYS - 1
        if train_cutoff_i < 25:
            continue

        tr_cut_dt = unique_dates[train_cutoff_i]
        va_start_dt = unique_dates[val_start_i]
        va_end_dt = unique_dates[val_end_i - 1]

        tr_m = valid["date"] <= tr_cut_dt
        va_m = (valid["date"] >= va_start_dt) & (valid["date"] <= va_end_dt)
        if tr_m.sum() < 80 or va_m.sum() < 15:
            continue
        y_tr = valid.loc[tr_m, target_col].astype(int).to_numpy()
        if len(np.unique(y_tr)) < 2:
            continue
        X_tr = valid.loc[tr_m, LOCAL_FEATURE_COLS].to_numpy(dtype=float)
        X_va = valid.loc[va_m, LOCAL_FEATURE_COLS].to_numpy(dtype=float)
        p_va = _fit_local_ensemble_and_predict(X_tr, y_tr, X_va)
        oof_probs.extend(p_va.tolist())
        oof_targets.extend(valid.loc[va_m, target_col].astype(int).tolist())

    wf_auc = 0.50
    cal_model: Optional[LogisticRegression] = None
    if len(oof_probs) >= 40 and len(set(oof_targets)) == 2:
        oof_p_arr = np.clip(np.asarray(oof_probs, dtype=float), 1e-4, 1.0 - 1e-4)
        oof_y_arr = np.asarray(oof_targets, dtype=int)
        try:
            wf_auc = float(roc_auc_score(oof_y_arr, oof_p_arr))
        except Exception:
            wf_auc = 0.50
        # Platt logistic calibration on OOF logits (preserves continuous ranking & cross-sectional spread)
        oof_logit = np.log(oof_p_arr / (1.0 - oof_p_arr)).reshape(-1, 1)
        try:
            cm = LogisticRegression(C=2.0, max_iter=300)
            cm.fit(oof_logit, oof_y_arr)
            # 仅在正向斜率时启用 Platt 校准，防极端噪声期反号
            if float(cm.coef_[0][0]) > 0.05:
                cal_model = cm
        except Exception:
            cal_model = None

    # 全量历史样本拟合最终本土模型（严格剔除尚未兑现的最后 5 天标签）
    X_all_local = valid[LOCAL_FEATURE_COLS].to_numpy(dtype=float)
    y_all = valid[target_col].astype(int).to_numpy()
    raw_local_p = np.clip(_fit_local_ensemble_and_predict(X_all_local, y_all, X_inf_local), 0.05, 0.95)
    base_rate = float(np.mean(y_all))

    # Walk-Forward 基率锚定 + 防塌缩温度校准：
    # 1) 以 OOF 分位数分布锚定真实基率，同时对横截面 logit 排序做温和温度展开，
    #    彻底消除「全员聚在 45%~52%」的塌缩现象，又不破坏单调排序与真实基率；
    if len(oof_probs) >= 40:
        oof_p_arr = np.clip(np.asarray(oof_probs, dtype=float), 0.05, 0.95)
        oof_logit_arr = np.log(oof_p_arr / (1.0 - oof_p_arr))
        oof_mu = float(np.mean(oof_logit_arr))
        oof_sd = max(0.18, float(np.std(oof_logit_arr)))
    else:
        oof_mu, oof_sd = 0.0, 0.35

    inf_logit = np.log(raw_local_p / (1.0 - raw_local_p))
    # 标准化为 Walk-Forward 分布 Z 值（相对历史全样本排序位置）
    z_hist = np.clip((inf_logit - oof_mu) / oof_sd, -2.5, 2.5)
    base_logit = math.log(max(0.15, min(0.85, base_rate)) / (1.0 - max(0.15, min(0.85, base_rate))))
    # 校准概率：基率 logit + 0.42 * Walk-Forward Z（使 ±1.5σ 对应约 ±14% 胜率分化）
    p_wf_anchored = 1.0 / (1.0 + np.exp(-(base_logit + 0.42 * z_hist)))

    if cal_model is not None:
        p_cal_platt = cal_model.predict_proba(inf_logit.reshape(-1, 1))[:, 1]
        p_local_cal = 0.30 * p_cal_platt + 0.70 * p_wf_anchored
    else:
        p_local_cal = 0.25 * raw_local_p + 0.75 * p_wf_anchored

    # 独立美股外盘因子微调模型（严格封顶 ±MAX_US_MACRO_ADJ_PCT，按标的 US-beta 加权）
    us_adj_pct = np.zeros(len(infer_df), dtype=float)
    try:
        X_all_us = valid[US_MACRO_COLS].fillna(0.0).to_numpy(dtype=float)
        if np.std(X_all_us) > 1e-6:
            us_sc = StandardScaler()
            X_us_tr = us_sc.fit_transform(X_all_us)
            X_us_inf = us_sc.transform(X_inf_us)
            us_lr = LogisticRegression(C=0.10, max_iter=300, random_state=42)
            us_lr.fit(X_us_tr, y_all)
            base_rate = float(np.mean(y_all))
            p_us = us_lr.predict_proba(X_us_inf)[:, 1]
            raw_shift_pct = (p_us - base_rate) * 100.0
            betas = np.array([
                float(TICKER_US_BETA.get(str(tk).upper(), 0.65))
                for tk in infer_df["ticker"].tolist()
            ], dtype=float)
            us_adj_pct = np.clip(raw_shift_pct * betas, -MAX_US_MACRO_ADJ_PCT, MAX_US_MACRO_ADJ_PCT)
    except Exception:
        us_adj_pct = np.zeros(len(infer_df), dtype=float)

    cal_pct = np.clip(p_local_cal * 100.0 + us_adj_pct, 12.0, 88.0)
    return cal_pct, raw_local_p * 100.0, round(wf_auc, 3), np.round(us_adj_pct, 2)


def _normalize_long_short_pair(long_p: float, short_p: float) -> Tuple[float, float]:
    l = max(5.0, min(92.0, float(long_p)))
    s = max(5.0, min(92.0, float(short_p)))
    if l + s > 100.0:
        scale = 100.0 / (l + s)
        l *= scale
        s *= scale
    return round(l, 1), round(s, 1)


def train_and_predict_apac_ml(
    hist_map: Dict[str, pd.DataFrame],
    us_macro_df: pd.DataFrame,
    apac_snapshot_items: Optional[Dict[str, Dict[str, Any]]] = None,
    now_dt: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    纯计算核心：给定历史日线字典 + 美股外盘表，执行严格无泄露 Walk-Forward 训练校准与 16 标的推理。
    （可脱离网络直接被单测调用验证！）
    """
    t0 = time.perf_counter()
    cst_now = _to_cst_datetime(now_dt)
    snap_items = apac_snapshot_items if isinstance(apac_snapshot_items, dict) else {}

    panel, sample_counts = build_apac_panel(hist_map, us_macro_df)
    if panel.empty:
        return {
            "schema_version": "v5.8-apac-ml-isolated",
            "updated_at_cst": cst_now.strftime("%Y-%m-%d %H:%M:%S CST"),
            "as_of_date": cst_now.strftime("%Y-%m-%d"),
            "computed_after_close_date": cst_now.strftime("%Y-%m-%d") if (cst_now.hour * 60 + cst_now.minute) >= POST_CLOSE_REFRESH_CST_MIN else "",
            "model_stats": {"train_rows": 0, "wf_auc_long": 0.5, "wf_auc_short": 0.5},
            "by_ticker": {},
        }

    # 训练集：仅纳入 >= MIN_TRAIN_DAYS (120日) 的标的且已兑现 5 日后标签
    train_df = panel[panel["eligible_train"] & panel["fwd_5d_ret"].notna()].copy()
    train_df["y_long"] = (train_df["fwd_5d_ret"] > LONG_RET_THRESHOLD).astype(int)
    train_df["y_short"] = (train_df["fwd_5d_ret"] < SHORT_RET_THRESHOLD).astype(int)

    # 推理集：每只满足 >= MIN_INFER_DAYS (60日) 标的的最新一个交易日行
    infer_df = panel.sort_values("date").groupby("ticker", as_index=False).tail(1).reset_index(drop=True)

    long_cal, long_raw, auc_long, us_adj_long = _walk_forward_train_and_calibrate(
        train_df=train_df,
        infer_df=infer_df,
        target_col="y_long",
    )
    short_cal, short_raw, auc_short, us_adj_short = _walk_forward_train_and_calibrate(
        train_df=train_df,
        infer_df=infer_df,
        target_col="y_short",
    )

    by_ticker: Dict[str, Dict[str, Any]] = {}
    as_of_dates: List[str] = []

    for i, row in infer_df.iterrows():
        tk = str(row["ticker"]).upper()
        meta = aaf.APAC_ASHARE_BY_TICKER.get(tk, {})
        snap = snap_items.get(tk, {}) if isinstance(snap_items.get(tk), dict) else {}
        dec = int(meta.get("decimals") or 3)
        is_cny = str(meta.get("price_unit") or "cny") == "cny"
        n_days = int(row.get("sample_days") or sample_counts.get(tk, 0))
        is_new_listing = n_days < NEW_LISTING_DAYS

        # 次新样本 (< 250 日) 轻度收缩向 50% 并打标「次新样本」
        if is_new_listing:
            shrink_w = max(0.45, min(0.90, n_days / float(NEW_LISTING_DAYS)))
            l_p = 50.0 + (float(long_cal[i]) - 50.0) * shrink_w
            s_p = 50.0 + (float(short_cal[i]) - 50.0) * shrink_w
        else:
            l_p = float(long_cal[i])
            s_p = float(short_cal[i])

        l_cal, s_cal = _normalize_long_short_pair(l_p, s_p)
        l_raw, s_raw = _normalize_long_short_pair(float(long_raw[i]), float(short_raw[i]))
        dir_score = round(l_cal - s_cal, 1)

        px = _safe_float(snap.get("last")) or _safe_float(row.get("close")) or 0.0
        s1 = _safe_float(snap.get("s1")) or (round(px * 0.965, dec) if px > 0 else 0.0)
        s2 = _safe_float(snap.get("s2")) or (round(px * 0.93, dec) if px > 0 else 0.0)
        r1 = _safe_float(snap.get("r1")) or (round(px * 1.035, dec) if px > 0 else 0.0)
        r2 = _safe_float(snap.get("r2")) or (round(px * 1.07, dec) if px > 0 else 0.0)
        stop_px = _safe_float(snap.get("entry_stop_loss")) or round(max(s2, min(px * 0.985, s1 * 0.992)), dec)

        tp_pct = round(((r1 - px) / px * 100.0), 1) if (px > 0 and r1 > px) else 3.5
        sl_pct = round(abs((px - stop_px) / px * 100.0), 1) if (px > 0 and stop_px > 0) else 3.0
        short_tp_pct = round(abs((px - s1) / px * 100.0), 1) if (px > 0 and s1 > 0 and s1 < px) else 3.5
        short_sl_pct = tp_pct

        # 方向判定文案
        if l_cal >= 58.0 and dir_score >= 12.0:
            sig_cn = "🟢 偏多占优"
            act_txt = "🟢 AI 5日看多偏强 · 回踩S1/VWAP择机低吸"
        elif l_cal >= 52.0 and dir_score >= 5.0:
            sig_cn = "🟢 温和偏多"
            act_txt = "🟢 温和偏多 · 依托均线顺势跟踪"
        elif s_cal >= 58.0 and dir_score <= -12.0:
            sig_cn = "🔴 偏空承压"
            act_txt = "🔴 AI 5日承压偏空 · 严控仓位防回撤"
        elif s_cal >= 52.0 and dir_score <= -5.0:
            sig_cn = "🟠 偏弱震荡"
            act_txt = "🟠 短线偏弱 · 观察S1支撑承接再定"
        else:
            sig_cn = "⚪ 中性拉锯"
            act_txt = "⚪ 多空均衡 · 区间围绕撑压高抛低吸"

        dt_str = pd.to_datetime(row["date"]).strftime("%Y-%m-%d")
        as_of_dates.append(dt_str)
        us_dt = str(row.get("us_lead_date") or "")
        us_adj_v = round(float(us_adj_long[i]), 2)
        rs20_pct = round(float(row.get("rs_20d") or 0.0) * 100.0, 2)
        rsi14_v = round(float(row.get("rsi_14") or 50.0), 1)
        bias20_pct = round(float(row.get("bias_ma20") or 0.0) * 100.0, 2)
        pos60_pct = round(float(row.get("pos_60d") or 0.5) * 100.0, 1)

        fp = lambda v: aaf.format_apac_price(v, "cny" if is_cny else "pts")
        sc_script = (
            f"【独立亚太/A股 AI 5日推演】校准多 {l_cal:.1f}% vs 空 {s_cal:.1f}%（方向分 {dir_score:+.1f}）；"
            f"本土因子：MA20偏离 {bias20_pct:+.1f}% · RSI {rsi14_v:.1f} · 60日分位 {pos60_pct:.0f}% · 20日超额RS {rs20_pct:+.1f}%；"
            f"隔夜美股 T-1 ({us_dt or '--'}) 封顶微调 {us_adj_v:+.1f}%。"
            f"若回踩不破 S1 ({fp(s1)}) 关注反弹上看 R1 ({fp(r1)})，跌破防守止损位 {fp(stop_px)} 暂避观望。"
        )

        by_ticker[tk] = {
            "ticker": tk,
            "name": meta.get("name", tk),
            "sector_cn": meta.get("cat", "亚太/A股"),
            "kind": meta.get("kind", "ASHARE_ETF"),
            "price_unit": meta.get("price_unit", "cny"),
            "as_of_date": dt_str,
            "us_lead_date": us_dt,
            "us_lead_adj_pct": us_adj_v,
            "sample_days": n_days,
            "included_in_train": bool(n_days >= MIN_TRAIN_DAYS),
            "is_short_sample": bool(is_new_listing),
            "sample_shrinkage_tag": "次新样本" if is_new_listing else "",
            "calibrated_win_rate_pct": l_cal,
            "calibrated_short_win_rate_pct": s_cal,
            "predicted_win_rate_pct": l_raw,
            "model_raw_prob_pct": l_raw,
            "model_raw_short_prob_pct": s_raw,
            "direction_score": dir_score,
            "signal_cn": sig_cn,
            "action": act_txt,
            "short_action": act_txt if dir_score <= -8.0 else "",
            "has_options": False,
            "resonance_badge_text": f"🌏 独立亚太AI · 20日RS {rs20_pct:+.1f}% · T-1外盘 {us_adj_v:+.1f}%",
            "resonance_badge_tag": "div-bull" if dir_score >= 8.0 else ("div-bear" if dir_score <= -8.0 else "div-neutral"),
            "anchor_s1": round(s1, dec),
            "anchor_s2": round(s2, dec),
            "anchor_r1": round(r1, dec),
            "anchor_r2": round(r2, dec),
            "s1": round(s1, dec),
            "s2": round(s2, dec),
            "r1": round(r1, dec),
            "r2": round(r2, dec),
            "target_profit_price": round(r1, dec),
            "target_profit_pct": tp_pct,
            "stop_loss_price": round(stop_px, dec),
            "stop_loss_pct": sl_pct,
            "short_target_profit_price": round(s1, dec),
            "short_target_profit_pct": short_tp_pct,
            "short_stop_loss_price": round(r1, dec),
            "short_stop_loss_pct": short_sl_pct,
            "sr_breach_state": "INTACT",
            "scenario_script": sc_script,
            "features_latest": {
                "bias_ma20_pct": bias20_pct,
                "rsi_14": rsi14_v,
                "pos_60d_pct": pos60_pct,
                "rs_20d_pct": rs20_pct,
            },
        }

    # 组内多/空排名 (1..16)
    long_order = sorted(by_ticker.keys(), key=lambda k: (-by_ticker[k]["calibrated_win_rate_pct"], -by_ticker[k]["direction_score"]))
    short_order = sorted(by_ticker.keys(), key=lambda k: (-by_ticker[k]["calibrated_short_win_rate_pct"], by_ticker[k]["direction_score"]))
    for rk, tk in enumerate(long_order, 1):
        by_ticker[tk]["rank"] = rk
    for rk, tk in enumerate(short_order, 1):
        by_ticker[tk]["short_rank"] = rk

    long_vals = [v["calibrated_win_rate_pct"] for v in by_ticker.values()]
    spread_stats = {
        "min_long_wr": round(min(long_vals), 1) if long_vals else 50.0,
        "max_long_wr": round(max(long_vals), 1) if long_vals else 50.0,
        "std_long_wr": round(float(np.std(long_vals)), 2) if long_vals else 0.0,
    }
    latest_as_of = max(as_of_dates) if as_of_dates else cst_now.strftime("%Y-%m-%d")
    after_close_flag = (
        cst_now.strftime("%Y-%m-%d")
        if (cst_now.weekday() < 5 and (cst_now.hour * 60 + cst_now.minute) >= POST_CLOSE_REFRESH_CST_MIN)
        else ""
    )

    return {
        "schema_version": "v5.8-apac-ml-isolated",
        "updated_at_cst": cst_now.strftime("%Y-%m-%d %H:%M:%S CST"),
        "fetched_epoch": time.time(),
        "as_of_date": latest_as_of,
        "computed_after_close_date": after_close_flag,
        "count": len(by_ticker),
        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        "model_stats": {
            "train_rows": int(len(train_df)),
            "train_tickers": int(train_df["ticker"].nunique()) if not train_df.empty else 0,
            "wf_auc_long": auc_long,
            "wf_auc_short": auc_short,
            "max_us_macro_adj_pct": MAX_US_MACRO_ADJ_PCT,
            "horizon_days": HORIZON_DAYS,
            **spread_stats,
        },
        "by_ticker": by_ticker,
    }


def should_recompute_apac_ml(
    cached: Dict[str, Any],
    now_dt: Optional[datetime] = None,
    force_refresh: bool = False,
) -> Tuple[bool, str]:
    """
    智能调度判定：
    1. force_refresh=True -> 强制重算；
    2. 首次冷启动缓存缺失 / 不足 16 只标的 -> 立即触发全量初始计算；
    3. 缓存超过 36 小时过期 -> 触发补算；
    4. 周一至周五亚盘收盘后 (>= 16:15 CST / 17:15 KST) 且今日尚未做收盘后增量重算 -> 触发今日一次重算；
    5. 其余所有时段（含美股盘前/盘中/盘后/夜盘与亚盘盘中）-> 严格返回 False，0 HTTP、0ms 拖慢主循环。
    """
    if force_refresh:
        return True, "FORCE_REFRESH"
    if not isinstance(cached, dict) or not isinstance(cached.get("by_ticker"), dict):
        return True, "COLD_START_MISSING"
    if len(cached.get("by_ticker") or {}) < len(aaf.ALL_APAC_ASHARE_TARGETS):
        return True, "COLD_START_INCOMPLETE"

    cst_now = _to_cst_datetime(now_dt)
    today_str = cst_now.strftime("%Y-%m-%d")
    now_ts = time.time()
    cached_ts = _safe_float(cached.get("fetched_epoch"), 0.0) or 0.0

    # 仅在工作日超过 36 小时未更新时触发陈旧兜底重算（周末休眠不重复拉取）
    if cst_now.weekday() < 5 and cached_ts > 0 and (now_ts - cached_ts) > 36 * 3600:
        return True, "STALE_CACHE_RECOMPUTE"

    # 亚盘收盘后 (>= 16:15 CST) 每日仅重算一次
    cst_min = cst_now.hour * 60 + cst_now.minute
    if cst_now.weekday() < 5 and cst_min >= POST_CLOSE_REFRESH_CST_MIN:
        if str(cached.get("computed_after_close_date") or "") != today_str:
            return True, "DAILY_POST_CLOSE_REFRESH"

    return False, "SLEEP_CACHE"


def get_or_update_apac_ml_predictions(
    cache_path: str = DEFAULT_APAC_ML_CACHE_PATH,
    apac_snapshot_items: Optional[Dict[str, Dict[str, Any]]] = None,
    now_dt: Optional[datetime] = None,
    force_refresh: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    对外唯一入口：休眠期直读本地缓存（0 HTTP / 0ms），亚盘收盘后每日一次增量重算，首次冷启动立算。
    """
    cached = load_apac_ml_predictions(cache_path)
    need_run, reason = should_recompute_apac_ml(cached, now_dt=now_dt, force_refresh=force_refresh)
    if not need_run:
        out = dict(cached)
        out["fetch_mode"] = reason
        return out

    hist_map, us_macro_df = fetch_apac_and_us_history()
    if not hist_map:
        if cached:
            out = dict(cached)
            out["fetch_mode"] = "FALLBACK_STALE_CACHE"
            return out
        return {}

    if apac_snapshot_items is None:
        snap_doc = aaf.load_apac_ashare_cache() or {}
        apac_snapshot_items = snap_doc.get("items") if isinstance(snap_doc.get("items"), dict) else {}

    doc = train_and_predict_apac_ml(
        hist_map=hist_map,
        us_macro_df=us_macro_df,
        apac_snapshot_items=apac_snapshot_items,
        now_dt=now_dt,
    )
    doc["fetch_mode"] = reason
    if doc.get("by_ticker") and not dry_run:
        _save_apac_ml_predictions(doc, cache_path=cache_path)
    return doc


if __name__ == "__main__":
    res = get_or_update_apac_ml_predictions(force_refresh=True)
    ms = res.get("model_stats", {})
    print(
        f"[+] APAC Isolated AI Engine Done: count={res.get('count')} | mode={res.get('fetch_mode')} | "
        f"elapsed={res.get('elapsed_ms')}ms | train_rows={ms.get('train_rows')} | "
        f"WF-AUC long={ms.get('wf_auc_long')} short={ms.get('wf_auc_short')} | "
        f"spread=[{ms.get('min_long_wr')}%, {ms.get('max_long_wr')}%] std={ms.get('std_long_wr')}"
    )
    by_tk = res.get("by_ticker") or {}
    for tk in sorted(by_tk.keys(), key=lambda k: by_tk[k].get("rank", 99)):
        r = by_tk[tk]
        badge = f" [{r['sample_shrinkage_tag']}]" if r.get("sample_shrinkage_tag") else ""
        print(
            f"  #{r['rank']:02d} {tk:8s} {r['name']:10s} | 多 {r['calibrated_win_rate_pct']:5.1f}% | "
            f"空 {r['calibrated_short_win_rate_pct']:5.1f}% | 方向 {r['direction_score']:+5.1f} | "
            f"US({r['us_lead_date']}) {r['us_lead_adj_pct']:+4.1f}% | n={r['sample_days']}{badge} | {r['signal_cn']}"
        )
