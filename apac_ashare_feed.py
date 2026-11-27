# -*- coding: utf-8 -*-
"""
V5.6 独立数据源模块：亚太纯指数 (6 只) + A 股场内 ETF (10 只) + 亚盘半导体风向聚合 (apac_ashare_feed.py)
====================================================================================================
核心红线：
1. 时区智能休眠：仅在亚盘活跃窗（08:50 ~ 17:15 KST / 07:50 ~ 16:15 CST，周一至周五）按 TTL 实时拉取；
   美股盘前/盘中及休市时段严格读取本地缓存快照，0 次外部 HTTP 请求、0ms 额外延迟。
2. 严禁消耗 Finnhub 额度：全部走 akshare (新浪/东财免费免 Key) + 腾讯免费批量行情 + yfinance 免费兜底。
3. 严格单位格式化：
   - 6 只亚太纯指数：单位 `pt`，全链路零 `$`；
   - 10 只 A 股 ETF：单位 `¥`，严格保留 3 位小数（如 `¥1.766`），全链路零 `$`。
4. 严格隔离：本模块数据绝不混入纯美股温度计 (`market_temperature_engine.py`) 与 V5.4/V5.5.1 模拟跟单池。
"""

from __future__ import annotations

import html as _html
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

try:
    from zoneinfo import ZoneInfo

    KST_TZ = ZoneInfo("Asia/Seoul")
    CST_TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    KST_TZ = timezone(timedelta(hours=9))
    CST_TZ = timezone(timedelta(hours=8))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_APAC_CACHE_PATH = os.path.join(BASE_DIR, "data_cache", "apac_ashare_cache.json")

# 亚盘活跃窗口（KST 08:50 ~ 17:15 == CST 07:50 ~ 16:15）
APAC_ACTIVE_START_KST_MIN = 8 * 60 + 50   # 08:50 KST (07:50 CST)
APAC_ACTIVE_END_KST_MIN = 17 * 60 + 15    # 17:15 KST (16:15 CST)
LIVE_QUOTE_TTL_SECONDS = 180              # 亚盘内 3 分钟 TTL，防每轮 43s 重复冲击
DAILY_K_TTL_SECONDS = 900                 # 日 K / 5m 15 分钟 TTL

# 6 只亚太纯指数（单位 pt，零 $）
APAC_PURE_INDICES: List[Dict[str, Any]] = [
    {
        "ticker": "^N225",
        "name": "日经225",
        "sub": "日本股市总基准 (东京电子/爱德万测试先导)",
        "cat": "亚太指数",
        "market": "JP",
        "kind": "APAC_INDEX",
        "price_unit": "pts",
        "decimals": 2,
        "session_label": "日股交易时段 09:00–15:30 JST",
        "source_pref": "yfinance",
        "yf_symbol": "^N225",
        "ak_symbol": "",
        "qt_symbol": "",
        "aliases": ["n225", "^n225", "日经", "日经225", "日股", "日本指数", "nikkei"],
    },
    {
        "ticker": "^TWII",
        "name": "台湾加权",
        "sub": "台湾股市总基准 (台积电权重核心先导)",
        "cat": "亚太指数",
        "market": "TW",
        "kind": "APAC_INDEX",
        "price_unit": "pts",
        "decimals": 2,
        "session_label": "台股交易时段 09:00–13:30 CST",
        "source_pref": "yfinance",
        "yf_symbol": "^TWII",
        "ak_symbol": "",
        "qt_symbol": "",
        "aliases": ["twii", "^twii", "台股", "台湾加权", "台湾指数", "加权指数"],
    },
    {
        "ticker": "^HSI",
        "name": "恒生指数",
        "sub": "香港主板核心蓝筹指数",
        "cat": "亚太指数",
        "market": "HK",
        "kind": "APAC_INDEX",
        "price_unit": "pts",
        "decimals": 2,
        "session_label": "港股交易时段 09:30–16:00 HKT",
        "source_pref": "yfinance",
        "yf_symbol": "^HSI",
        "ak_symbol": "HSI",
        "qt_symbol": "hkHSI",
        "aliases": ["hsi", "^hsi", "恒指", "恒生", "恒生指数", "港股"],
    },
    {
        "ticker": "HSTECH",
        "name": "恒生科技",
        "sub": "香港科技互联网与半导体核心风向",
        "cat": "亚太指数",
        "market": "HK",
        "kind": "APAC_INDEX",
        "price_unit": "pts",
        "decimals": 2,
        "session_label": "港股交易时段 09:30–16:00 HKT",
        "source_pref": "akshare",
        "yf_symbol": "",
        "ak_symbol": "HSTECH",
        "qt_symbol": "hkHSTECH",
        "aliases": ["hstech", "恒科", "恒生科技", "港股科技"],
    },
    {
        "ticker": "000688",
        "name": "科创50",
        "sub": "上交所科创板50硬科技半导体基准",
        "cat": "亚太指数",
        "market": "CN",
        "kind": "APAC_INDEX",
        "price_unit": "pts",
        "decimals": 2,
        "session_label": "A股交易时段 09:30–15:00 CST",
        "source_pref": "akshare",
        "yf_symbol": "000688.SS",
        "ak_symbol": "sh000688",
        "qt_symbol": "sh000688",
        "aliases": ["000688", "科创50", "科创板", "科创指数"],
    },
    {
        "ticker": "399006",
        "name": "创业板指",
        "sub": "深交所创业板成长核心风向",
        "cat": "亚太指数",
        "market": "CN",
        "kind": "APAC_INDEX",
        "price_unit": "pts",
        "decimals": 2,
        "session_label": "A股交易时段 09:30–15:00 CST",
        "source_pref": "akshare",
        "yf_symbol": "399006.SZ",
        "ak_symbol": "sz399006",
        "qt_symbol": "sz399006",
        "aliases": ["399006", "创业板", "创业板指", "创指"],
    },
]

# 10 只 A 股场内 ETF（单位 ¥，严格保留 3 位小数）
ASHARE_ETFS: List[Dict[str, Any]] = [
    {
        "ticker": "512530",
        "name": "沪深300红利ETF",
        "sub": "A股大盘高股息红利防守核心",
        "cat": "A股红利",
        "market": "CN",
        "kind": "ASHARE_ETF",
        "price_unit": "cny",
        "decimals": 3,
        "session_label": "A股交易时段 09:30–15:00 CST",
        "ak_symbol": "sh512530",
        "yf_symbol": "512530.SS",
        "qt_symbol": "sh512530",
        "aliases": ["512530", "300红利", "沪深300红利", "沪深300红利etf", "红利etf"],
    },
    {
        "ticker": "512880",
        "name": "证券ETF国泰",
        "sub": "A股券商牛市旗手与风险偏好温度计",
        "cat": "A股金融",
        "market": "CN",
        "kind": "ASHARE_ETF",
        "price_unit": "cny",
        "decimals": 3,
        "session_label": "A股交易时段 09:30–15:00 CST",
        "ak_symbol": "sh512880",
        "yf_symbol": "512880.SS",
        "qt_symbol": "sh512880",
        "aliases": ["512880", "证券etf", "券商etf", "证券etf国泰", "券商"],
    },
    {
        "ticker": "512800",
        "name": "银行ETF华宝",
        "sub": "A股银行红利与大盘稳定器",
        "cat": "A股金融",
        "market": "CN",
        "kind": "ASHARE_ETF",
        "price_unit": "cny",
        "decimals": 3,
        "session_label": "A股交易时段 09:30–15:00 CST",
        "ak_symbol": "sh512800",
        "yf_symbol": "512800.SS",
        "qt_symbol": "sh512800",
        "aliases": ["512800", "银行etf", "银行etf华宝", "银行"],
    },
    {
        "ticker": "512690",
        "name": "酒ETF",
        "sub": "A股白酒消费核心风向标",
        "cat": "A股消费",
        "market": "CN",
        "kind": "ASHARE_ETF",
        "price_unit": "cny",
        "decimals": 3,
        "session_label": "A股交易时段 09:30–15:00 CST",
        "ak_symbol": "sh512690",
        "yf_symbol": "512690.SS",
        "qt_symbol": "sh512690",
        "aliases": ["512690", "酒etf", "白酒etf", "白酒", "酒"],
    },
    {
        "ticker": "159530",
        "name": "机器人ETF易方达",
        "sub": "人形机器人与物理AI产业链核心",
        "cat": "A股科技",
        "market": "CN",
        "kind": "ASHARE_ETF",
        "price_unit": "cny",
        "decimals": 3,
        "session_label": "A股交易时段 09:30–15:00 CST",
        "ak_symbol": "sz159530",
        "yf_symbol": "159530.SZ",
        "qt_symbol": "sz159530",
        "aliases": ["159530", "机器人etf", "机器人etf易方达", "机器人"],
    },
    {
        "ticker": "159995",
        "name": "芯片ETF华夏",
        "sub": "A股全市场半导体芯片龙头篮子",
        "cat": "A股芯片",
        "market": "CN",
        "kind": "ASHARE_ETF",
        "price_unit": "cny",
        "decimals": 3,
        "session_label": "A股交易时段 09:30–15:00 CST",
        "ak_symbol": "sz159995",
        "yf_symbol": "159995.SZ",
        "qt_symbol": "sz159995",
        "aliases": ["159995", "芯片etf", "芯片etf华夏", "华夏芯片"],
    },
    {
        "ticker": "159562",
        "name": "黄金股ETF华夏",
        "sub": "A股黄金采掘贵金属高弹性链条",
        "cat": "A股贵金属",
        "market": "CN",
        "kind": "ASHARE_ETF",
        "price_unit": "cny",
        "decimals": 3,
        "session_label": "A股交易时段 09:30–15:00 CST",
        "ak_symbol": "sz159562",
        "yf_symbol": "159562.SZ",
        "qt_symbol": "sz159562",
        "aliases": ["159562", "黄金股etf", "黄金股etf华夏", "黄金股"],
    },
    {
        "ticker": "588200",
        "name": "科创芯片ETF嘉实",
        "sub": "科创板高弹性芯片龙头核心",
        "cat": "A股芯片",
        "market": "CN",
        "kind": "ASHARE_ETF",
        "price_unit": "cny",
        "decimals": 3,
        "session_label": "A股交易时段 09:30–15:00 CST",
        "ak_symbol": "sh588200",
        "yf_symbol": "588200.SS",
        "qt_symbol": "sh588200",
        "aliases": ["588200", "科创芯片", "科创芯片etf", "科创芯片etf嘉实"],
    },
    {
        "ticker": "515880",
        "name": "通信ETF国泰",
        "sub": "A股光模块/CPO/算力通信核心",
        "cat": "A股通信",
        "market": "CN",
        "kind": "ASHARE_ETF",
        "price_unit": "cny",
        "decimals": 3,
        "session_label": "A股交易时段 09:30–15:00 CST",
        "ak_symbol": "sh515880",
        "yf_symbol": "515880.SS",
        "qt_symbol": "sh515880",
        "aliases": ["515880", "通信etf", "通信etf国泰", "光模块etf"],
    },
    {
        "ticker": "588170",
        "name": "科创半导体ETF华夏",
        "sub": "科创板半导体设备与材料细分核心",
        "cat": "A股芯片",
        "market": "CN",
        "kind": "ASHARE_ETF",
        "price_unit": "cny",
        "decimals": 3,
        "session_label": "A股交易时段 09:30–15:00 CST",
        "ak_symbol": "sh588170",
        "yf_symbol": "588170.SS",
        "qt_symbol": "sh588170",
        "aliases": ["588170", "科创半导体", "科创半导体etf", "科创半导体etf华夏"],
    },
]

ALL_APAC_ASHARE_TARGETS: List[Dict[str, Any]] = APAC_PURE_INDICES + ASHARE_ETFS
APAC_ASHARE_BY_TICKER: Dict[str, Dict[str, Any]] = {
    str(x["ticker"]).upper(): x for x in ALL_APAC_ASHARE_TARGETS
}
APAC_ASHARE_TICKER_SET = frozenset(APAC_ASHARE_BY_TICKER.keys())

CHIP_GROUP_WEIGHTS: List[Tuple[str, float, str]] = [
    ("^TWII", 0.25, "台湾加权 (台积电权重)"),
    ("KOSPI", 0.25, "韩国综指 (三星/海力士)"),
    ("^N225", 0.15, "日经225 (半导体设备)"),
    ("588200", 0.12, "科创芯片ETF"),
    ("588170", 0.12, "科创半导体ETF"),
    ("159995", 0.11, "芯片ETF华夏"),
]
GREATER_CHINA_GROUP: List[Tuple[str, str]] = [
    ("^HSI", "恒生指数"),
    ("HSTECH", "恒生科技"),
    ("000688", "科创50"),
    ("399006", "创业板指"),
]


def _safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "" or isinstance(val, bool):
            return default
        f = float(val)
        return f if math.isfinite(f) else default
    except Exception:
        return default


def format_apac_price(val: Any, price_unit: str = "cny") -> str:
    """严格格式化价格：A 股 ETF `¥0.000` (3 位小数)，亚太指数 `0,000.00 pt` (绝不出现 `$`)。"""
    fv = _safe_float(val)
    if fv is None or fv <= 0:
        return "--"
    u = str(price_unit or "").strip().lower()
    if u == "cny":
        return f"¥{fv:.3f}"
    if u in ("pts", "pt"):
        return f"{fv:,.2f} pt"
    return f"{fv:,.2f}"


def is_apac_active_window(now_dt: Optional[datetime] = None) -> bool:
    """
    判断当前是否处于亚盘活跃实时抓取窗：
    - 周一至周五 08:50 ~ 17:15 KST (即 07:50 ~ 16:15 CST)；
    - 美股盘前/盘中（09:30~16:00 ET == 23:30~06:00 KST）及周末返回 False，触发 0 HTTP 休眠。
    """
    if now_dt is None:
        kst_now = datetime.now(KST_TZ)
    elif now_dt.tzinfo is None:
        kst_now = now_dt.replace(tzinfo=KST_TZ)
    else:
        kst_now = now_dt.astimezone(KST_TZ)
    if kst_now.weekday() >= 5:
        return False
    mins = kst_now.hour * 60 + kst_now.minute
    return APAC_ACTIVE_START_KST_MIN <= mins <= APAC_ACTIVE_END_KST_MIN


def _load_cache(cache_path: str = DEFAULT_APAC_CACHE_PATH) -> Dict[str, Any]:
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


load_apac_ashare_cache = _load_cache


def _save_cache(doc: Dict[str, Any], cache_path: str = DEFAULT_APAC_CACHE_PATH) -> None:
    parent = os.path.dirname(os.path.abspath(cache_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{cache_path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.flush()
        os.replace(tmp, cache_path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def _sanitize_unadjusted_splits(
    rows: List[List[Any]],
    decimals: int = 3,
    jump_ratio_thresh: float = 0.35,
) -> List[List[Any]]:
    """
    兜底防污染护栏：若备用未复权源出现单日 >35% 的 ETF 拆分/折算断层跳变（非正常单日涨跌停），
    从最新价向历史回推拆分比例做前复权平滑，彻底杜绝未复权拆分污染 MA50 / R2。
    """
    if len(rows) < 2:
        return rows
    out = [list(r) for r in rows]
    for i in range(len(out) - 1, 0, -1):
        c_prev = _safe_float(out[i - 1][4])
        o_curr = _safe_float(out[i][1]) or _safe_float(out[i][4])
        if not c_prev or not o_curr or c_prev <= 0 or o_curr <= 0:
            continue
        rel_jump = abs(o_curr - c_prev) / c_prev
        if rel_jump >= jump_ratio_thresh:
            ratio = o_curr / c_prev
            # 仅对典型拆分/合并比例范围生效
            if 0.15 <= ratio <= 0.68 or 1.45 <= ratio <= 6.5:
                for j in range(0, i):
                    for col_idx in (1, 2, 3, 4):
                        v = _safe_float(out[j][col_idx])
                        if v is not None:
                            out[j][col_idx] = round(v * ratio, decimals)
    return out


def _compute_volume_poc(bars: List[List[Any]], bins: int = 18) -> Optional[float]:
    """计算近区间成交密集区（Volume-Weighted POC）。"""
    if len(bars) < 5:
        return None
    tps: List[float] = []
    vols: List[float] = []
    for r in bars:
        h = _safe_float(r[2])
        l = _safe_float(r[3])
        c = _safe_float(r[4])
        v = _safe_float(r[5], 1.0) or 1.0
        if h and l and c and c > 0:
            tps.append((h + l + c) / 3.0)
            vols.append(max(1.0, v))
    if len(tps) < 5:
        return None
    lo, hi = min(tps), max(tps)
    if hi - lo <= 1e-6:
        return None
    step = (hi - lo) / bins
    buckets = [0.0] * bins
    for tp, v in zip(tps, vols):
        idx = min(bins - 1, max(0, int((tp - lo) / step)))
        buckets[idx] += v
    best_i = max(range(bins), key=lambda k: buckets[k])
    return lo + (best_i + 0.5) * step


def sanitize_apac_5m_bars(
    rows: List[List[Any]],
    market: str = "CN",
    decimals: int = 3,
    outlier_vol_mult: float = 8.0,
) -> Tuple[List[List[Any]], bool]:
    """
    清洗亚太/A 股 5m 分时切片，一次性解决两类数据源畸变：
    1. 午休空档剔除（杜绝 11:30~13:00 横盘死直线）：
       - A 股 (CN): 剔除 11:30 < t < 13:00 午休占位柱及 11:30 零成交死柱；
       - 港股 (HK): 剔除 12:00 < t < 13:00 午休占位柱；
       - 日股 (JP): 剔除 11:30 < t < 12:30 午休占位柱；
    2. 极端脉冲成交量清洗（如 Yahoo .SZ 源在 13:00 / 14:55 误把累计成交量灌入单根 5m 柱，达中位数 40~65 倍）：
       - 单根成交量 > 非零中位数 * outlier_vol_mult (默认 8x) 时，用中位成交量平滑替代，
         保护下方柱图比例与日内 VWAP 尾部不被单根异常巨量拉歪。
    返回: (cleaned_rows, outlier_capped_flag)
    """
    if not rows:
        return [], False
    mkt = str(market or "CN").upper()
    filtered: List[List[Any]] = []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 5:
            continue
        t_raw = str(r[0] or "").strip()
        hhmm = t_raw[-5:] if len(t_raw) >= 5 else t_raw
        mins: Optional[int] = None
        if ":" in hhmm:
            try:
                p = hhmm.split(":")
                mins = int(p[0]) * 60 + int(p[1])
            except Exception:
                mins = None
        op = _safe_float(r[1])
        hi = _safe_float(r[2])
        lo = _safe_float(r[3])
        cl = _safe_float(r[4])
        vo = max(0.0, _safe_float(r[5], 0.0) or 0.0) if len(r) > 5 else 0.0
        if not (op and hi and lo and cl and cl > 0):
            continue

        if mins is not None:
            if mkt == "CN":
                # A 股午休 11:30 ~ 13:00（严格剔除 11:31~12:59，及 11:30 零量平盘占位）
                if 11 * 60 + 30 < mins < 13 * 60:
                    continue
                if mins == 11 * 60 + 30 and vo <= 0 and abs(hi - lo) <= 1e-9:
                    continue
                # 剔除非正盘时间
                if mins < 9 * 60 + 25 or mins > 15 * 60 + 5:
                    continue
            elif mkt == "HK":
                if 12 * 60 < mins < 13 * 60:
                    continue
                if mins == 12 * 60 and vo <= 0 and abs(hi - lo) <= 1e-9:
                    continue
            elif mkt == "JP":
                if 11 * 60 + 30 < mins < 12 * 60 + 30:
                    continue
                if mins == 11 * 60 + 30 and vo <= 0 and abs(hi - lo) <= 1e-9:
                    continue
        elif vo <= 0 and abs(hi - lo) <= 1e-9:
            continue

        filtered.append([
            hhmm,
            round(op, decimals),
            round(hi, decimals),
            round(lo, decimals),
            round(cl, decimals),
            round(vo, 2),
        ])

    if not filtered:
        return [], False

    nz_vols = sorted(float(r[5]) for r in filtered if float(r[5]) > 0)
    outlier_capped = False
    if len(nz_vols) >= 6:
        # 剔除最大 2 根后再求中位数，防止 13:00/14:55 累计巨量拉高中位数基线
        core_vols = nz_vols[:-2] if len(nz_vols) >= 10 else nz_vols[:-1]
        med_v = float(np.median(core_vols))
        if med_v > 0:
            cap_thresh = med_v * float(outlier_vol_mult)
            for r in filtered:
                if float(r[5]) > cap_thresh:
                    r[5] = round(med_v, 2)
                    outlier_capped = True

    return filtered, outlier_capped


def compute_apac_5m_microstructure(
    clean_5m_rows: List[List[Any]],
    spot_price: Optional[float] = None,
    decimals: int = 3,
) -> Dict[str, Any]:
    """
    基于清洗后的 5m 分时序列计算真实日内微观指标：
    - vwap / vwap_diff_pct：与前端 5m 分时图逐根累积 TP*V / V 口径 100% 一致；
    - rsi_5m：5m Wilder's RSI(14)；
    - rvol_5m：近 3 根 5m 均量 / 全日清洗后 5m 均量；
    - has_5m_intraday：有效 5m 柱 >= 5 根时为 True。
    """
    if not clean_5m_rows or len(clean_5m_rows) < 2:
        return {
            "vwap": None,
            "vwap_diff_pct": None,
            "rsi_5m": None,
            "rvol_5m": None,
            "has_5m_intraday": False,
        }

    cum_pv = 0.0
    cum_v = 0.0
    closes: List[float] = []
    vols: List[float] = []
    tps: List[float] = []
    for r in clean_5m_rows:
        hi = _safe_float(r[2])
        lo = _safe_float(r[3])
        cl = _safe_float(r[4])
        vo = max(0.0, _safe_float(r[5], 0.0) or 0.0)
        if hi and lo and cl and cl > 0:
            tp = (hi + lo + cl) / 3.0
            tps.append(tp)
            closes.append(cl)
            vols.append(vo)
            if vo > 0:
                cum_pv += tp * vo
                cum_v += vo

    if not closes:
        return {
            "vwap": None,
            "vwap_diff_pct": None,
            "rsi_5m": None,
            "rvol_5m": None,
            "has_5m_intraday": False,
        }

    if cum_v > 0:
        vwap_val = cum_pv / cum_v
    else:
        vwap_val = float(np.mean(tps))

    ref_px = float(spot_price) if (spot_price is not None and spot_price > 0) else closes[-1]
    vwap_diff = ((ref_px - vwap_val) / vwap_val * 100.0) if vwap_val > 0 else 0.0

    # 5m Wilder RSI(14)
    rsi_5m: Optional[float] = None
    if len(closes) >= 5:
        s_cl = pd.Series(closes, dtype=float)
        delta = s_cl.diff()
        gain = delta.clip(lower=0.0).ewm(alpha=1.0 / 14.0, adjust=False).mean()
        loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / 14.0, adjust=False).mean()
        rs = gain / loss.replace(0.0, np.nan)
        rsi_s = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)
        rsi_5m = round(float(rsi_s.iloc[-1]), 1)

    # 5m 量比（近 3 根非零均量 / 全日非零均量）
    nz_v = [v for v in vols if v > 0]
    rvol_5m: Optional[float] = None
    if len(nz_v) >= 4:
        avg_all = float(np.mean(nz_v))
        recent_3 = float(np.mean(nz_v[-3:]))
        if avg_all > 0:
            rvol_5m = round(max(0.10, min(9.90, recent_3 / avg_all)), 2)

    return {
        "vwap": round(vwap_val, decimals),
        "vwap_diff_pct": round(vwap_diff, 2),
        "rsi_5m": rsi_5m,
        "rvol_5m": rvol_5m,
        "has_5m_intraday": bool(len(closes) >= 5),
    }


def hydrate_missing_5m_from_local_parquet(
    apac_doc: Optional[Dict[str, Any]],
    parquet_path: Optional[str] = None,
    persist_cache_path: Optional[str] = None,
) -> int:
    """
    零新增外部网络请求：当 apac_doc 中某只亚太标的（典型如 HSTECH）缺少 intraday_5m / rsi_5m / vwap 时，
    直接从本地 data_cache/apac_intraday_5m_60d.parquet 读取其最新交易日 5m K 线，
    计算真实日内 VWAP（无量指数自动用 5m 典型均价 TWAP）、5m RSI 与量比并回填。
    返回成功补齐的标的数量。
    """
    if not isinstance(apac_doc, dict):
        return 0
    items = apac_doc.get("items")
    if not isinstance(items, dict) or not items:
        return 0

    need_tks: List[str] = []
    for tk, row in items.items():
        if not isinstance(row, dict):
            continue
        if (
            not row.get("intraday_5m")
            or row.get("rsi_5m") is None
            or row.get("vwap") is None
            or not bool(row.get("has_5m_intraday"))
        ):
            need_tks.append(tk)
    if not need_tks:
        return 0

    pq_path = parquet_path or os.path.join(BASE_DIR, "data_cache", "apac_intraday_5m_60d.parquet")
    if not os.path.exists(pq_path):
        return 0

    try:
        df_pq = pd.read_parquet(pq_path)
    except Exception:
        return 0
    if df_pq is None or df_pq.empty or "ticker" not in df_pq.columns or "date" not in df_pq.columns:
        return 0

    hydrated = 0
    for tk in need_tks:
        row = items.get(tk)
        if not isinstance(row, dict):
            continue
        sub = df_pq[df_pq["ticker"] == tk]
        if sub.empty:
            continue
        latest_d = str(sub["date"].astype(str).max())
        day_df = sub[sub["date"].astype(str) == latest_d].sort_values("time_slot")
        if len(day_df) < 2:
            continue
        meta = APAC_ASHARE_BY_TICKER.get(tk, {})
        mkt = str(row.get("market") or meta.get("market") or "HK")
        dec = int(row.get("decimals") or meta.get("decimals") or 2)
        raw_rows: List[List[Any]] = []
        for _, r in day_df.iterrows():
            ts = str(r.get("time_slot") or "")
            op = _safe_float(r.get("open"))
            hi = _safe_float(r.get("high"))
            lo = _safe_float(r.get("low"))
            cl = _safe_float(r.get("close"))
            vo = max(0.0, _safe_float(r.get("volume"), 0.0) or 0.0)
            if ts and cl and cl > 0:
                raw_rows.append([
                    ts,
                    round(op if (op and op > 0) else cl, dec),
                    round(hi if (hi and hi > 0) else cl, dec),
                    round(lo if (lo and lo > 0) else cl, dec),
                    round(cl, dec),
                    round(vo, 2),
                ])
        if len(raw_rows) < 2:
            continue
        clean_rows, vol_capped = sanitize_apac_5m_bars(raw_rows, market=mkt, decimals=dec)
        if len(clean_rows) < 2:
            continue
        spot_px = _safe_float(row.get("last") or row.get("price"))
        micro = compute_apac_5m_microstructure(clean_rows, spot_price=spot_px, decimals=dec)
        row["intraday_5m"] = clean_rows
        row["vwap"] = micro["vwap"]
        row["vwap_diff_pct"] = micro["vwap_diff_pct"]
        row["rsi_5m"] = micro["rsi_5m"]
        row["rvol_5m"] = micro["rvol_5m"]
        row["has_5m_intraday"] = micro["has_5m_intraday"]
        row["vol_outlier_capped"] = bool(vol_capped)
        hydrated += 1

    if hydrated > 0 and persist_cache_path:
        try:
            _tmp_p = f"{persist_cache_path}.tmp.{os.getpid()}"
            with open(_tmp_p, "w", encoding="utf-8") as _wf:
                json.dump(apac_doc, _wf, ensure_ascii=False, indent=2)
            os.replace(_tmp_p, persist_cache_path)
        except Exception:
            pass
    return hydrated


def _compute_technicals_from_daily(
    daily_ohlc: List[List[Any]],
    spot_price: Optional[float] = None,
    spot_prev_close: Optional[float] = None,
    spot_pct: Optional[float] = None,
    decimals: int = 3,
) -> Dict[str, Any]:
    """
    基于前复权日 K 序列计算均线 (MA10/MA20/EMA20/MA50)、MA20 偏离、RSI(14)、ATR、
    日线量比、60日成交密集区 POC + 黄金坑支撑带、贴近 S1 的日内短线防守止损位与
    梯次可交易结构撑压 S1/S2/R1/R2（杜绝用 60/120/250 日山顶僵尸高点硬当 R1/R2）。
    """
    if not daily_ohlc:
        px = round(float(spot_price), decimals) if (spot_price and spot_price > 0) else 0.0
        s1_fb = round(px * 0.97, decimals) if px > 0 else 0.0
        return {
            "last": px,
            "prev_close": round(float(spot_prev_close), decimals) if (spot_prev_close and spot_prev_close > 0) else px,
            "pct_change": round(float(spot_pct), 2) if spot_pct is not None else 0.0,
            "ma10": px,
            "ma20": px,
            "ema20": px,
            "ma50": px,
            "bias_ma20": 0.0,
            "bias": 0.0,
            "rsi": 50.0,
            "atr": 0.0,
            "vol_ratio": 1.0,
            "poc": px,
            "fib_low": round(px * 0.95, decimals) if px > 0 else 0.0,
            "fib_high": round(px * 0.98, decimals) if px > 0 else 0.0,
            "ret_20d": 0.0,
            "s1": s1_fb,
            "s2": round(px * 0.94, decimals) if px > 0 else 0.0,
            "r1": round(px * 1.03, decimals) if px > 0 else 0.0,
            "r2": round(px * 1.06, decimals) if px > 0 else 0.0,
            "entry_stop_loss": round(px * 0.962, decimals) if px > 0 else 0.0,
        }

    closes = pd.Series([float(r[4]) for r in daily_ohlc], dtype=float)
    highs = pd.Series([float(r[2]) for r in daily_ohlc], dtype=float)
    lows = pd.Series([float(r[3]) for r in daily_ohlc], dtype=float)
    vols = pd.Series([max(0.0, _safe_float(r[5], 0.0) or 0.0) for r in daily_ohlc], dtype=float)

    last_bar_c = float(closes.iloc[-1])
    prev_bar_c = float(closes.iloc[-2]) if len(closes) >= 2 else last_bar_c
    px = float(spot_price) if (spot_price is not None and spot_price > 0) else last_bar_c
    prev_c = float(spot_prev_close) if (spot_prev_close is not None and spot_prev_close > 0) else prev_bar_c
    if spot_pct is not None:
        pct = float(spot_pct)
    elif prev_c > 0:
        pct = ((px - prev_c) / prev_c) * 100.0
    else:
        pct = 0.0

    ma10 = float(closes.tail(10).mean())
    ma20 = float(closes.tail(20).mean())
    ema20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
    ma50 = float(closes.tail(50).mean())
    bias_ma20 = ((px - ma20) / ma20 * 100.0) if ma20 > 0 else 0.0
    bias = ((px - ma50) / ma50 * 100.0) if ma50 > 0 else 0.0

    # 20 日涨跌幅（供横截面 20 日超额 RS 计算）
    if len(closes) >= 21 and float(closes.iloc[-21]) > 0:
        ret_20d = ((px - float(closes.iloc[-21])) / float(closes.iloc[-21])) * 100.0
    elif len(closes) >= 2 and float(closes.iloc[0]) > 0:
        ret_20d = ((px - float(closes.iloc[0])) / float(closes.iloc[0])) * 100.0
    else:
        ret_20d = pct

    # 日线量比（最新一日成交量 / 近 20 日均量）
    vol_ratio = 1.0
    if len(vols) >= 6:
        nz_tail = vols.tail(20)
        nz_pos = nz_tail[nz_tail > 0]
        last_v = float(vols.iloc[-1])
        if len(nz_pos) >= 5 and last_v > 0:
            avg_v20 = float(nz_pos.mean())
            if avg_v20 > 0:
                vol_ratio = max(0.15, min(6.0, last_v / avg_v20))

    # 20 日布林带
    std20 = float(closes.tail(20).std(ddof=0)) if len(closes) >= 5 else 0.0
    bb_upper = ma20 + 2.0 * std20 if std20 > 0 else 0.0
    bb_lower = max(0.001, ma20 - 2.0 * std20) if std20 > 0 else 0.0

    # Wilder's RSI(14)
    delta = closes.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / 14.0, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / 14.0, adjust=False).mean()
    rs = gain / loss.replace(0.0, np.nan)
    rsi_s = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)
    rsi = float(rsi_s.iloc[-1]) if len(rsi_s) else 50.0

    # ATR(14)
    tr1 = highs - lows
    tr2 = (highs - closes.shift(1)).abs()
    tr3 = (lows - closes.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = float(tr.tail(14).mean()) if len(tr) else max(px * 0.015, 0.001)
    atr_pct = (atr / px) if px > 0 else 0.02

    # 梯次结构撑压候选池（严禁直接用 60/120/250 日山顶最高价硬当 R1/R2）
    recent_bars = daily_ohlc[-60:]
    hi_arr = [float(r[2]) for r in recent_bars]
    lo_arr = [float(r[3]) for r in recent_bars]
    n_b = len(recent_bars)

    # 局部 Swing High / Swing Low（近 5~25 日真实可交易波段拐点 + 5/10/20 日近端高低）
    swing_highs: List[float] = []
    swing_lows: List[float] = []
    look_start = max(1, n_b - 25)
    for i in range(look_start, n_b - 1):
        if hi_arr[i] >= hi_arr[i - 1] and hi_arr[i] >= hi_arr[i + 1]:
            swing_highs.append(hi_arr[i])
        if lo_arr[i] <= lo_arr[i - 1] and lo_arr[i] <= lo_arr[i + 1]:
            swing_lows.append(lo_arr[i])

    # 近 5/10/20 日波段高低 + 跌破/突破颈线（原支撑转阻力、原阻力转支撑）
    for w in (5, 10, 20):
        if n_b >= w:
            swing_highs.append(max(hi_arr[-w:]))
            swing_lows.append(min(lo_arr[-w:]))

    poc_20 = _compute_volume_poc(recent_bars[-20:])
    poc_45 = _compute_volume_poc(recent_bars[-45:])
    poc_60 = _compute_volume_poc(recent_bars) or poc_45 or poc_20 or ma20

    # 60日斐波那契 0.5~0.618 黄金坑支撑带（结合 60d QFQ 成交密集区 POC）
    hi_60 = max(hi_arr) if hi_arr else px
    lo_60 = min(lo_arr) if lo_arr else px
    span_60 = max(hi_60 - lo_60, px * 0.02)
    fib_low = lo_60 + span_60 * 0.382
    fib_high = lo_60 + span_60 * 0.500
    if fib_low > fib_high:
        fib_low, fib_high = fib_high, fib_low

    # 所有结构候选位（阻力取高于现价、支撑取低于现价，含 MA10/MA20/MA50、布林中/上/下轨、颈线与 POC）
    all_struct_levels: List[float] = []
    for lv in [ma10, ma20, ema20, ma50, bb_upper, bb_lower, poc_20, poc_45, poc_60] + swing_highs + swing_lows:
        if lv is not None and math.isfinite(lv) and lv > 0:
            all_struct_levels.append(float(lv))

    # 合理操作区间与最小间距去重：R1 至少高于现价 +1.2%，典型落在 +2% ~ +8%(高波不超 +11%)
    min_gap_1 = max(px * 0.012, atr * 0.55)
    min_gap_2 = max(px * 0.018, atr * 0.90)
    r1_max_reach = px * (1.0 + max(0.075, min(0.115, atr_pct * 3.2)))
    r2_max_reach = px * (1.0 + max(0.14, min(0.22, atr_pct * 5.8)))
    s1_max_reach = px * (1.0 - max(0.075, min(0.115, atr_pct * 3.2)))
    s2_max_reach = px * (1.0 - max(0.14, min(0.22, atr_pct * 5.8)))

    # 去重聚合（0.5% 内视为同一结构位）
    def _dedup_sorted(vals: List[float], reverse: bool = False) -> List[float]:
        s = sorted(vals, reverse=reverse)
        res: List[float] = []
        for v in s:
            if not res or abs(v - res[-1]) / max(px, 1e-6) >= 0.005:
                res.append(v)
        return res

    res_cands = _dedup_sorted([v for v in all_struct_levels if (px + min_gap_1) <= v <= r2_max_reach], reverse=False)
    r1_band = [v for v in res_cands if v <= r1_max_reach]
    if r1_band:
        r1 = r1_band[0]
    else:
        r1 = px + max(atr * 1.4, px * 0.025)

    r2_band = [v for v in res_cands if v >= (r1 + min_gap_2)]
    if r2_band:
        r2 = r2_band[0]
    else:
        r2 = r1 + max(atr * 1.8, px * 0.035)

    sup_cands = _dedup_sorted([v for v in all_struct_levels if max(0.001, s2_max_reach) <= v <= (px - min_gap_1)], reverse=True)
    s1_band = [v for v in sup_cands if v >= s1_max_reach]
    if s1_band:
        s1 = s1_band[0]
    else:
        s1 = max(0.001, px - max(atr * 1.4, px * 0.025))

    s2_band = [v for v in sup_cands if v <= (s1 - min_gap_2)]
    if s2_band:
        s2 = s2_band[0]
    else:
        s2 = max(0.001, s1 - max(atr * 1.8, px * 0.035))

    s1_clean = max(0.001, s1)
    s2_clean = max(0.001, min(s1_clean * 0.995, s2))
    # 日内短线防守止损线：锚定贴近 S1 (S1 * 0.992)，严格限制在距现价 [-8.0%, -1.5%] 合理短线防守带内，绝不拿 -17% 的 S2 充当短线止损
    sl_anchor = s1_clean * 0.992
    sl_floor = px * (1.0 - min(0.080, max(0.035, atr_pct * 2.4)))
    sl_ceil = px * 0.985
    entry_sl = max(0.001, min(sl_ceil, max(sl_floor, sl_anchor)))

    return {
        "last": round(px, decimals),
        "prev_close": round(prev_c, decimals),
        "pct_change": round(pct, 2),
        "ma10": round(ma10, decimals),
        "ma20": round(ma20, decimals),
        "ema20": round(ema20, decimals),
        "ma50": round(ma50, decimals),
        "bias_ma20": round(bias_ma20, 2),
        "bias": round(bias, 2),
        "rsi": round(rsi, 1),
        "atr": round(atr, decimals),
        "vol_ratio": round(vol_ratio, 2),
        "poc": round(poc_60, decimals),
        "fib_low": round(max(0.001, fib_low), decimals),
        "fib_high": round(max(0.001, fib_high), decimals),
        "ret_20d": round(ret_20d, 2),
        "s1": round(s1_clean, decimals),
        "s2": round(s2_clean, decimals),
        "r1": round(max(px * 1.005, r1), decimals),
        "r2": round(max(r1 * 1.010, r2), decimals),
        "entry_stop_loss": round(entry_sl, decimals),
    }


def _fetch_tencent_batch_spot(qt_symbols: List[str], timeout: float = 4.0) -> Dict[str, Dict[str, float]]:
    """腾讯免费批量实时盘口（1 次 HTTP 请求拿全部 A 股 ETF + CN/HK 指数，零 Key、零限流）。"""
    out: Dict[str, Dict[str, float]] = {}
    syms = [s for s in qt_symbols if s]
    if not syms:
        return out
    try:
        url = "https://qt.gtimg.cn/q=" + ",".join(syms)
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        if r.status_code != 200 or not r.text:
            return out
        for line in r.text.strip().splitlines():
            if "=" not in line or "~" not in line:
                continue
            left, right = line.split("=", 1)
            qsym = left.replace("v_", "").strip()
            parts = right.strip().strip(";").strip('"').split("~")
            if len(parts) < 38:
                continue
            px = _safe_float(parts[3])
            prev_c = _safe_float(parts[4])
            pct = _safe_float(parts[32])
            # parts[37] 对 A 股为成交额(万元)，港股索引为港元成交额
            amt_raw = _safe_float(parts[37], 0.0) or 0.0
            turnover = amt_raw * 10000.0 if not qsym.startswith("hk") else amt_raw
            if px and px > 0:
                out[qsym] = {
                    "price": px,
                    "prev_close": prev_c or px,
                    "pct_change": pct if pct is not None else (((px - prev_c) / prev_c * 100.0) if (prev_c and prev_c > 0) else 0.0),
                    "turnover": turnover,
                }
    except Exception:
        pass
    return out


def _df_to_ohlc_rows(df: Optional[pd.DataFrame], decimals: int = 3, max_bars: int = 90) -> List[List[Any]]:
    if df is None or df.empty:
        return []
    cols = {str(c).lower(): c for c in df.columns}
    d_col = cols.get("date") or cols.get("日期") or cols.get("day") or cols.get("datetime")
    o_col = cols.get("open") or cols.get("开盘")
    h_col = cols.get("high") or cols.get("最高")
    l_col = cols.get("low") or cols.get("最低")
    c_col = cols.get("close") or cols.get("收盘")
    v_col = cols.get("volume") or cols.get("成交量")
    if not (o_col and h_col and l_col and c_col):
        return []

    sub = df.tail(max_bars)
    rows: List[List[Any]] = []
    for idx, r in sub.iterrows():
        dt_val = r[d_col] if d_col else idx
        dt_str = str(dt_val)[:16]
        op = _safe_float(r[o_col])
        hi = _safe_float(r[h_col])
        lo = _safe_float(r[l_col])
        cl = _safe_float(r[c_col])
        vo = _safe_float(r[v_col], 0.0) if v_col else 0.0
        if op and hi and lo and cl and cl > 0:
            rows.append([
                dt_str,
                round(op, decimals),
                round(hi, decimals),
                round(lo, decimals),
                round(cl, decimals),
                round(max(0.0, vo or 0.0), 2),
            ])
    return rows


def _fetch_tencent_qfq_daily(qt_sym: str, decimals: int = 3, max_bars: int = 90, timeout: float = 5.0) -> Tuple[List[List[Any]], str]:
    """腾讯免费原生前复权日 K 接口 (`qfq`)，免 Key、零限流，优先解决 ETF 除权/拆分跳变污染。"""
    if not qt_sym:
        return [], "none"
    try:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={qt_sym},day,,,{max(max_bars, 100)},qfq"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        if r.status_code != 200:
            return [], "none"
        payload = r.json()
        node = ((payload or {}).get("data") or {}).get(qt_sym) or {}
        raw_rows = node.get("qfqday") or node.get("day") or []
        if not raw_rows:
            return [], "none"
        rows: List[List[Any]] = []
        for item in raw_rows[-max_bars:]:
            if not isinstance(item, (list, tuple)) or len(item) < 5:
                continue
            # 腾讯格式: [date, open, close, high, low, volume, ...]
            dt_str = str(item[0])[:16]
            op = _safe_float(item[1])
            cl = _safe_float(item[2])
            hi = _safe_float(item[3])
            lo = _safe_float(item[4])
            vo = _safe_float(item[5], 0.0) if len(item) > 5 else 0.0
            if op and hi and lo and cl and cl > 0:
                rows.append([
                    dt_str,
                    round(op, decimals),
                    round(hi, decimals),
                    round(lo, decimals),
                    round(cl, decimals),
                    round(max(0.0, vo or 0.0), 2),
                ])
        if len(rows) >= 2:
            src_tag = "tencent:fqkline_qfq" if "qfqday" in node else "tencent:fqkline_day"
            return _sanitize_unadjusted_splits(rows, decimals=decimals), src_tag
    except Exception:
        pass
    return [], "none"


def _fetch_single_akshare_daily(meta: Dict[str, Any]) -> Tuple[List[List[Any]], Optional[float], str]:
    """按标的类型获取前复权日 K + 成交额（A 股 ETF 强制前复权 qfq，严防未复权拆分污染）。"""
    tk = str(meta["ticker"]).upper()
    ak_sym = str(meta.get("ak_symbol") or "")
    qt_sym = str(meta.get("qt_symbol") or ak_sym)
    dec = int(meta.get("decimals") or 3)
    kind = str(meta.get("kind") or "")
    turnover: Optional[float] = None

    if kind == "ASHARE_ETF":
        # 1) 优先腾讯原生前复权日 K (零限流 + qfq)
        t_rows, t_src = _fetch_tencent_qfq_daily(qt_sym, decimals=dec, max_bars=90)
        if len(t_rows) >= 2:
            return t_rows, turnover, t_src

        # 2) 回退东财原生前复权 (adjust="qfq")，最后才用新浪不复权并经过拆分平滑护栏
        try:
            import akshare as ak
        except Exception:
            return [], None, "none"
        for fn_name, fn, need_sanitize in (
            ("ak:fund_etf_hist_em_qfq", lambda: ak.fund_etf_hist_em(
                symbol=tk, period="daily",
                start_date=(datetime.now() - timedelta(days=180)).strftime("%Y%m%d"),
                end_date=(datetime.now() + timedelta(days=2)).strftime("%Y%m%d"),
                adjust="qfq",
            ), False),
            ("ak:fund_etf_hist_sina_sanitized", lambda: ak.fund_etf_hist_sina(symbol=ak_sym), True),
        ):
            try:
                df = fn()
                if df is not None and not df.empty:
                    if "amount" in df.columns:
                        turnover = _safe_float(df["amount"].iloc[-1])
                    elif "成交额" in df.columns:
                        turnover = _safe_float(df["成交额"].iloc[-1])
                    rows = _df_to_ohlc_rows(df, decimals=dec, max_bars=90)
                    if need_sanitize:
                        rows = _sanitize_unadjusted_splits(rows, decimals=dec)
                    if len(rows) >= 2:
                        return rows, turnover, fn_name
            except Exception:
                continue
        return [], None, "none"

    import akshare as ak

    # 亚太指数 (HSTECH / 000688 / 399006)
    if tk == "HSTECH":
        for fn_name, fn in (
            ("ak:index_hk_daily_sina", lambda: ak.index_hk_daily_sina(symbol="HSTECH")),
            ("ak:index_hk_daily_em", lambda: ak.index_hk_daily_em(symbol="HSTECH")),
        ):
            try:
                df = fn()
                if df is not None and not df.empty:
                    if "amount" in df.columns:
                        turnover = _safe_float(df["amount"].iloc[-1])
                    rows = _df_to_ohlc_rows(df, decimals=dec, max_bars=90)
                    if len(rows) >= 2:
                        return rows, turnover, fn_name
            except Exception:
                continue
        return [], None, "none"

    for fn_name, fn in (
        ("ak:index_zh_daily_sina", lambda: ak.index_zh_daily_sina(symbol=ak_sym)),
        ("ak:index_zh_hist_em", lambda: ak.index_zh_hist_em(symbol=tk, period="daily")),
    ):
        try:
            df = fn()
            if df is not None and not df.empty:
                rows = _df_to_ohlc_rows(df, decimals=dec, max_bars=90)
                if len(rows) >= 2:
                    return rows, turnover, fn_name
        except Exception:
            continue
    return [], None, "none"


def _fetch_yf_batch_daily_and_5m(yf_symbols: List[str]) -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
    """通过 yfinance 批量拉取日 K (6mo) 与 5m 分时 (2d)，零 Finnhub 消耗。"""
    import yfinance as yf

    daily_map: Dict[str, pd.DataFrame] = {}
    intra_map: Dict[str, pd.DataFrame] = {}
    syms = [s for s in dict.fromkeys(yf_symbols) if s]
    if not syms:
        return daily_map, intra_map

    def _split_multi(df: Any, target_syms: List[str]) -> Dict[str, pd.DataFrame]:
        res: Dict[str, pd.DataFrame] = {}
        if df is None or getattr(df, "empty", True):
            return res
        if isinstance(df.columns, pd.MultiIndex):
            lvl0 = set(df.columns.get_level_values(0))
            if "Close" in lvl0:
                for s in target_syms:
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
                            res[s] = sub
                    except Exception:
                        continue
        elif len(target_syms) == 1 and "Close" in df.columns:
            sub = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
            if not sub.empty:
                res[target_syms[0]] = sub
        return res

    try:
        df_d = yf.download(syms, period="6mo", interval="1d", auto_adjust=True, progress=False)
        daily_map = _split_multi(df_d, syms)
    except Exception:
        pass

    try:
        df_5 = yf.download(syms, period="2d", interval="5m", progress=False)
        intra_map = _split_multi(df_5, syms)
    except Exception:
        pass

    return daily_map, intra_map


def _extract_latest_day_5m_rows(df_5m: Optional[pd.DataFrame], decimals: int = 3) -> List[List[Any]]:
    if df_5m is None or df_5m.empty:
        return []
    try:
        idx = pd.to_datetime(df_5m.index)
        last_d = idx[-1].date()
        sub = df_5m[idx.date == last_d]
        if len(sub) < 2:
            sub = df_5m.tail(48)
        rows: List[List[Any]] = []
        for ts, r in sub.iterrows():
            op = _safe_float(r.get("Open"))
            hi = _safe_float(r.get("High"))
            lo = _safe_float(r.get("Low"))
            cl = _safe_float(r.get("Close"))
            vo = _safe_float(r.get("Volume"), 0.0) or 0.0
            if op and hi and lo and cl and cl > 0:
                rows.append([
                    pd.to_datetime(ts).strftime("%H:%M"),
                    round(op, decimals),
                    round(hi, decimals),
                    round(lo, decimals),
                    round(cl, decimals),
                    round(max(0.0, vo), 2),
                ])
        return rows
    except Exception:
        return []


def compute_apac_chip_sentiment(
    apac_items: Dict[str, Dict[str, Any]],
    kospi_item: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    计算 V5.6「亚盘半导体风向聚合层 (`apac_chip_sentiment`)」：
    - 亚盘芯片权重组 (`chip_group`): ^TWII + KOSPI + ^N225 + A股芯片三剑客 (588200 / 588170 / 159995)
    - 大中华风险情绪组 (`greater_china_group`): ^HSI + HSTECH + 000688 + 399006
    为下一阶段灵活版 KOL 引擎驱动 SOXX / SOXL / SOXS 预留标准化只读字段。
    """
    items = apac_items if isinstance(apac_items, dict) else {}

    def _get_pct(tk: str) -> Optional[float]:
        if tk == "KOSPI" and isinstance(kospi_item, dict):
            for _k in ("pct_change", "change_pct", "effective_pct"):
                if kospi_item.get(_k) is not None:
                    return _safe_float(kospi_item.get(_k))
            return None
        row = items.get(tk)
        if isinstance(row, dict):
            return _safe_float(
                row.get("pct_change")
                if row.get("pct_change") is not None
                else row.get("change_pct")
            )
        return None

    def _get_px(tk: str) -> Optional[float]:
        if tk == "KOSPI" and isinstance(kospi_item, dict):
            return _safe_float(kospi_item.get("last") or kospi_item.get("price"))
        row = items.get(tk)
        if isinstance(row, dict):
            return _safe_float(row.get("last") or row.get("price"))
        return None

    # 1) 亚盘芯片权重组
    chip_members: List[Dict[str, Any]] = []
    w_sum = 0.0
    w_pct_sum = 0.0
    up_c = 0
    dn_c = 0
    for tk, wt, lbl in CHIP_GROUP_WEIGHTS:
        p = _get_pct(tk)
        px = _get_px(tk)
        unit = "pts" if (tk in ("^TWII", "KOSPI", "^N225")) else "cny"
        chip_members.append({
            "ticker": tk,
            "label": lbl,
            "weight": wt,
            "price": px,
            "price_unit": unit,
            "pct_change": round(p, 2) if p is not None else None,
        })
        if p is not None:
            w_sum += wt
            w_pct_sum += p * wt
            if p > 0.05:
                up_c += 1
            elif p < -0.05:
                dn_c += 1

    chip_w_pct = round(w_pct_sum / w_sum, 2) if w_sum > 0 else 0.0
    # 映射到 [0, 100]（0% -> 50；+2.5% -> 100；-2.5% -> 0）
    chip_score = round(max(0.0, min(100.0, 50.0 + chip_w_pct * 20.0)), 1)
    if chip_w_pct >= 0.8 and up_c >= max(2, dn_c + 1):
        chip_bias, chip_lbl = "BULLISH", "🟢 亚盘芯片强共振偏多"
    elif chip_w_pct <= -0.8 and dn_c >= max(2, up_c + 1):
        chip_bias, chip_lbl = "BEARISH", "🔴 亚盘芯片共振承压"
    else:
        chip_bias, chip_lbl = "NEUTRAL", "🟡 亚盘芯片震荡分化"

    # 2) 大中华风险情绪组
    gc_members: List[Dict[str, Any]] = []
    gc_vals: List[float] = []
    gc_up = 0
    gc_dn = 0
    for tk, lbl in GREATER_CHINA_GROUP:
        p = _get_pct(tk)
        px = _get_px(tk)
        gc_members.append({
            "ticker": tk,
            "label": lbl,
            "price": px,
            "price_unit": "pts",
            "pct_change": round(p, 2) if p is not None else None,
        })
        if p is not None:
            gc_vals.append(p)
            if p > 0.05:
                gc_up += 1
            elif p < -0.05:
                gc_dn += 1

    gc_avg_pct = round(sum(gc_vals) / len(gc_vals), 2) if gc_vals else 0.0
    gc_score = round(max(0.0, min(100.0, 50.0 + gc_avg_pct * 20.0)), 1)
    if gc_avg_pct >= 0.8 and gc_up >= 3:
        gc_bias, gc_lbl = "RISK_ON", "🟢 大中华风险偏好回暖"
    elif gc_avg_pct <= -0.8 and gc_dn >= 3:
        gc_bias, gc_lbl = "RISK_OFF", "🔴 大中华风险情绪偏弱"
    else:
        gc_bias, gc_lbl = "NEUTRAL", "🟡 大中华情绪中性震荡"

    return {
        "schema_version": "v5.6-apac-chip-sentiment",
        "chip_group": {
            "weighted_pct": chip_w_pct,
            "score": chip_score,
            "bias": chip_bias,
            "label_cn": chip_lbl,
            "up_count": up_c,
            "down_count": dn_c,
            "total_valid": sum(1 for m in chip_members if m["pct_change"] is not None),
            "members": chip_members,
        },
        "greater_china_group": {
            "avg_pct": gc_avg_pct,
            "score": gc_score,
            "bias": gc_bias,
            "label_cn": gc_lbl,
            "up_count": gc_up,
            "down_count": gc_dn,
            "total_valid": len(gc_vals),
            "members": gc_members,
        },
        "soxx_lead_bias": chip_bias,
        "summary_cn": f"{chip_lbl} ({chip_w_pct:+.2f}% · 评分 {chip_score:.0f}/100) ｜ {gc_lbl} ({gc_avg_pct:+.2f}%)",
    }


def fetch_apac_ashare_snapshot(
    cache_path: str = DEFAULT_APAC_CACHE_PATH,
    now_dt: Optional[datetime] = None,
    kospi_item: Optional[Dict[str, Any]] = None,
    force_refresh: bool = False,
    allow_cold_start_outside_window: bool = False,
    dry_run: bool = False,
    fetcher_override: Optional[Callable[[], Tuple[Dict[str, Dict[str, Any]], int]]] = None,
) -> Dict[str, Any]:
    """
    获取亚太 6 指数 + A 股 10 ETF 快照与 `apac_chip_sentiment` 聚合层：
    - 非亚盘活跃窗（含美股盘前/盘中）：直接读缓存快照，严格 0 次外部 HTTP 请求、0ms 额外延迟；
    - 亚盘活跃窗内：按 TTL (180s) 刷新，单票失败独立降级回退旧缓存，绝不影响主雷达。
    """
    cached = _load_cache(cache_path)
    has_cached_items = bool(isinstance(cached.get("items"), dict) and len(cached.get("items") or {}) > 0)
    active = is_apac_active_window(now_dt)

    now_ts = time.time()
    cached_ts = _safe_float(cached.get("fetched_epoch"), 0.0) or 0.0
    ttl_fresh = has_cached_items and ((now_ts - cached_ts) < LIVE_QUOTE_TTL_SECONDS)

    # 1) 美股时段/休市时区智能休眠 -> 0 次外部 HTTP 请求
    should_fetch = False
    if force_refresh:
        should_fetch = True
    elif active and (not ttl_fresh):
        should_fetch = True
    elif (not active) and (not has_cached_items) and allow_cold_start_outside_window:
        should_fetch = True

    if not should_fetch:
        items_out = dict(cached.get("items") or {})
        sentiment = compute_apac_chip_sentiment(items_out, kospi_item=kospi_item)
        out_cached = {
            "schema_version": "v5.6-apac-ashare-snapshot",
            "active_window": active,
            "fetch_mode": "SLEEP_CACHE" if (not active) else "TTL_CACHE",
            "http_calls": 0,
            "elapsed_ms": 0.0,
            "updated_at_cst": str(cached.get("updated_at_cst") or ""),
            "fetched_epoch": cached_ts,
            "count": len(items_out),
            "items": items_out,
            "apac_chip_sentiment": sentiment,
        }
        hydrate_missing_5m_from_local_parquet(out_cached, persist_cache_path=cache_path)
        return out_cached

    # 2) 执行实时拉取（零 Finnhub 消耗）
    t0 = time.perf_counter()
    http_calls = 0
    prev_items = cached.get("items") if isinstance(cached.get("items"), dict) else {}
    new_items: Dict[str, Dict[str, Any]] = {}

    if fetcher_override is not None:
        new_items, http_calls = fetcher_override()
    else:
        # 2a) 腾讯单次批量实时盘口（10 只 A 股 ETF + 000688 / 399006 / HSTECH / HSI）
        qt_syms = [str(m.get("qt_symbol") or "") for m in ALL_APAC_ASHARE_TARGETS if m.get("qt_symbol")]
        qt_quotes = _fetch_tencent_batch_spot(qt_syms, timeout=4.0)
        http_calls += 1

        # 2b) yfinance 批量拉取 6mo 日 K（主要给 ^N225/^TWII/^HSI 与其余兜底）+ 2d 5m 分时（全 16 只）
        yf_syms = [str(m.get("yf_symbol") or "") for m in ALL_APAC_ASHARE_TARGETS if m.get("yf_symbol")]
        yf_daily_map, yf_5m_map = _fetch_yf_batch_daily_and_5m(yf_syms)
        http_calls += 2

        # 2c) 逐票组装（单票独立 try/except 降级，绝不连累其他标的）
        for meta in ALL_APAC_ASHARE_TARGETS:
            tk = str(meta["ticker"]).upper()
            dec = int(meta.get("decimals") or 3)
            qsym = str(meta.get("qt_symbol") or "")
            yfsym = str(meta.get("yf_symbol") or "")
            spot = qt_quotes.get(qsym) or {}

            daily_rows: List[List[Any]] = []
            intra_5m_rows: List[List[Any]] = []
            turnover = _safe_float(spot.get("turnover"))
            src_used = ""

            try:
                if meta.get("source_pref") == "yfinance":
                    if yfsym in yf_daily_map:
                        daily_rows = _df_to_ohlc_rows(yf_daily_map[yfsym], decimals=dec, max_bars=90)
                        src_used = "yfinance"
                else:
                    # A 股 ETF & HSTECH / 000688 / 399006 优先走 akshare，失败自动回退 yfinance
                    daily_rows, ak_turn, src_used = _fetch_single_akshare_daily(meta)
                    http_calls += 1
                    if turnover is None or turnover <= 0:
                        turnover = ak_turn
                    if len(daily_rows) < 2 and yfsym in yf_daily_map:
                        daily_rows = _df_to_ohlc_rows(yf_daily_map[yfsym], decimals=dec, max_bars=90)
                        if meta.get("kind") == "ASHARE_ETF":
                            daily_rows = _sanitize_unadjusted_splits(daily_rows, decimals=dec)
                        src_used = "yfinance_fallback"

                # 5m 分时：优先用 yfinance 批量结果（0 额外请求），若缺失对 A 股可回退 sina 5m
                if yfsym in yf_5m_map:
                    intra_5m_rows = _extract_latest_day_5m_rows(yf_5m_map[yfsym], decimals=dec)
                if len(intra_5m_rows) < 2 and meta.get("ak_symbol") and meta.get("kind") == "ASHARE_ETF":
                    try:
                        import akshare as ak
                        df_m = ak.stock_zh_a_hist_min_sina(symbol=str(meta["ak_symbol"]), period="5")
                        http_calls += 1
                        intra_5m_rows = _df_to_ohlc_rows(df_m, decimals=dec, max_bars=48)
                        for r in intra_5m_rows:
                            r[0] = str(r[0])[-5:]
                    except Exception:
                        pass

                # 清洗 5m 分时切片（剔除午休死直线 + 清洗 13:00/14:55 累计巨量异常柱）并计算真实 VWAP / 5m RSI / 量比
                clean_5m_rows, vol_outlier_capped = sanitize_apac_5m_bars(
                    intra_5m_rows,
                    market=str(meta.get("market") or "CN"),
                    decimals=dec,
                )

                # 合并实时 spot 价格到最新日 K 尾部（保证现价与最后 K 线一致）
                spot_px = _safe_float(spot.get("price"))
                spot_prev = _safe_float(spot.get("prev_close"))
                spot_pct = _safe_float(spot.get("pct_change"))
                if spot_px is None and clean_5m_rows:
                    spot_px = _safe_float(clean_5m_rows[-1][4])

                if not daily_rows and (spot_px is None or spot_px <= 0):
                    # 单票完全失败 -> 回退旧缓存
                    if tk in prev_items:
                        old_copy = dict(prev_items[tk])
                        old_copy["stale_fallback"] = True
                        new_items[tk] = old_copy
                    continue

                tech = _compute_technicals_from_daily(
                    daily_rows,
                    spot_price=spot_px,
                    spot_prev_close=spot_prev,
                    spot_pct=spot_pct,
                    decimals=dec,
                )
                micro_5m = compute_apac_5m_microstructure(
                    clean_5m_rows,
                    spot_price=tech["last"],
                    decimals=dec,
                )
                row_out: Dict[str, Any] = {
                    "ticker": tk,
                    "name": meta["name"],
                    "sub": meta["sub"],
                    "cat": meta["cat"],
                    "market": meta["market"],
                    "kind": meta["kind"],
                    "is_apac_ashare": True,
                    "is_index": meta["kind"] == "APAC_INDEX",
                    "is_etf": meta["kind"] == "ASHARE_ETF",
                    "price_unit": meta["price_unit"],
                    "decimals": dec,
                    "session_label": meta["session_label"],
                    "has_options": False,
                    "is_earnings_risk": False,
                    "last": tech["last"],
                    "price": tech["last"],
                    "active_price": tech["last"],
                    "prev_close": tech["prev_close"],
                    "pct_change": tech["pct_change"],
                    "effective_pct": tech["pct_change"],
                    "turnover": round(turnover, 2) if (turnover is not None and turnover > 0) else None,
                    "ma10": tech["ma10"],
                    "ma20": tech["ma20"],
                    "ema20": tech["ema20"],
                    "ma50": tech["ma50"],
                    "bias_ma20": tech["bias_ma20"],
                    "bias": tech["bias"],
                    "rsi": tech["rsi"],
                    "atr": tech["atr"],
                    "vol_ratio": tech["vol_ratio"],
                    "poc": tech["poc"],
                    "fib_low": tech["fib_low"],
                    "fib_high": tech["fib_high"],
                    "ret_20d": tech["ret_20d"],
                    "vwap": micro_5m["vwap"],
                    "vwap_diff_pct": micro_5m["vwap_diff_pct"],
                    "rsi_5m": micro_5m["rsi_5m"],
                    "rvol_5m": micro_5m["rvol_5m"],
                    "has_5m_intraday": micro_5m["has_5m_intraday"],
                    "vol_outlier_capped": vol_outlier_capped,
                    "s1": tech["s1"],
                    "s2": tech["s2"],
                    "r1": tech["r1"],
                    "r2": tech["r2"],
                    "anchor_s1": tech["s1"],
                    "anchor_s2": tech["s2"],
                    "anchor_r1": tech["r1"],
                    "anchor_r2": tech["r2"],
                    "entry_stop_loss": tech["entry_stop_loss"],
                    "optimal_limit": tech["s1"],
                    "daily_ohlc": daily_rows,
                    "intraday_5m": clean_5m_rows,
                    "source": src_used or ("tencent_spot" if spot else "unknown"),
                    "stale_fallback": False,
                }
                new_items[tk] = row_out
            except Exception:
                if tk in prev_items:
                    old_copy = dict(prev_items[tk])
                    old_copy["stale_fallback"] = True
                    new_items[tk] = old_copy

        # 2d) 计算 20 日超额 RS：A 股 ETF 相对 000688/399006/512530 基准均值；亚太纯指数相对区域均值
        cn_bench_vals = [
            _safe_float(new_items[b].get("ret_20d"))
            for b in ("000688", "399006", "512530")
            if b in new_items and _safe_float(new_items[b].get("ret_20d")) is not None
        ]
        cn_bench_20d = float(np.mean(cn_bench_vals)) if cn_bench_vals else 0.0
        idx_vals = [
            _safe_float(v.get("ret_20d"))
            for v in new_items.values()
            if isinstance(v, dict) and v.get("kind") == "APAC_INDEX" and _safe_float(v.get("ret_20d")) is not None
        ]
        idx_bench_20d = float(np.mean(idx_vals)) if idx_vals else 0.0
        for tk_r, item_r in new_items.items():
            r20 = _safe_float(item_r.get("ret_20d"))
            if r20 is None:
                continue
            bench_ref = cn_bench_20d if item_r.get("kind") == "ASHARE_ETF" else idx_bench_20d
            item_r["rs_20d"] = round(r20 - bench_ref, 2)

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    cst_str = (now_dt.astimezone(CST_TZ) if (now_dt and now_dt.tzinfo) else datetime.now(CST_TZ)).strftime("%Y-%m-%d %H:%M:%S CST")
    sentiment = compute_apac_chip_sentiment(new_items, kospi_item=kospi_item)
    doc = {
        "schema_version": "v5.6-apac-ashare-snapshot",
        "active_window": active,
        "fetch_mode": "LIVE_FETCH",
        "http_calls": http_calls,
        "elapsed_ms": elapsed_ms,
        "updated_at_cst": cst_str,
        "fetched_epoch": now_ts,
        "count": len(new_items),
        "items": new_items,
        "apac_chip_sentiment": sentiment,
    }
    if new_items and not dry_run:
        _save_cache(doc, cache_path=cache_path)
    return doc


def render_macro_apac_section_html(apac_doc: Optional[Dict[str, Any]]) -> str:
    """生成 macro_radar.html「🌏 亚太指数与半导体风向」紧凑双主题卡片（零 `$` 符号）。"""
    if not isinstance(apac_doc, dict):
        return ""
    sent = apac_doc.get("apac_chip_sentiment") if isinstance(apac_doc.get("apac_chip_sentiment"), dict) else {}
    cg = sent.get("chip_group") if isinstance(sent.get("chip_group"), dict) else {}
    gc = sent.get("greater_china_group") if isinstance(sent.get("greater_china_group"), dict) else {}
    if not cg and not gc:
        return ""

    def _pct_badge(p: Optional[float]) -> str:
        if p is None:
            return '<span style="color:var(--text-muted);">--</span>'
        clr = "var(--color-up)" if p >= 0 else "var(--color-down)"
        return f'<span style="color:{clr}; font-weight:700;">{p:+.2f}%</span>'

    def _member_pills(members: List[Dict[str, Any]]) -> str:
        chips: List[str] = []
        for m in members:
            tk = _html.escape(str(m.get("ticker") or ""))
            lbl = _html.escape(str(m.get("label") or tk).split("(")[0].strip())
            px_str = format_apac_price(m.get("price"), str(m.get("price_unit") or "pts"))
            p_html = _pct_badge(_safe_float(m.get("pct_change")))
            chips.append(
                f'<div style="background:var(--bg-elevated); border:1px solid var(--border-subtle); '
                f'border-radius:8px; padding:7px 10px; display:flex; flex-direction:column; gap:2px;">'
                f'<div style="display:flex; justify-content:space-between; gap:8px; font-size:11.5px;">'
                f'<strong style="color:var(--text-main);">{lbl}</strong>'
                f'<span style="color:var(--text-muted); font-family:monospace;">{tk}</span></div>'
                f'<div style="display:flex; justify-content:space-between; align-items:baseline; gap:8px; font-size:12.5px; font-variant-numeric:tabular-nums;">'
                f'<span style="color:var(--text-sub);">{_html.escape(px_str)}</span>{p_html}</div>'
                f'</div>'
            )
        return "".join(chips)

    cg_pct = _safe_float(cg.get("weighted_pct"), 0.0) or 0.0
    gc_pct = _safe_float(gc.get("avg_pct"), 0.0) or 0.0
    cg_score = _safe_float(cg.get("score"), 50.0) or 50.0
    gc_score = _safe_float(gc.get("score"), 50.0) or 50.0
    upd = _html.escape(str(apac_doc.get("updated_at_cst") or "--"))
    mode_tag = "休眠零延迟缓存" if str(apac_doc.get("fetch_mode") or "") == "SLEEP_CACHE" else "亚盘实时联动"

    return f"""
    <section id="apacChipSentimentCard" class="liquidity-card" style="margin-top:16px;">
        <div class="liquidity-head">
            <div class="liquidity-title">🌏 亚太指数与半导体风向 (台日韩 + A股芯片先导 · 零美股温度计污染)</div>
            <span class="liquidity-badge">{_html.escape(str(cg.get("label_cn") or "亚盘风向"))} · {mode_tag}</span>
        </div>
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:14px; margin-top:10px;">
            <div style="background:var(--bg-card); border:1px solid var(--border-subtle); border-radius:10px; padding:12px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; font-size:13px;">
                    <strong style="color:var(--text-main);">⚡ 亚盘芯片权重组 (SOXX 先导)</strong>
                    <span>{_pct_badge(cg_pct)} · <strong style="color:var(--color-accent);">{cg_score:.0f}/100</strong></span>
                </div>
                <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(145px, 1fr)); gap:8px;">
                    {_member_pills(cg.get("members") or [])}
                </div>
            </div>
            <div style="background:var(--bg-card); border:1px solid var(--border-subtle); border-radius:10px; padding:12px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; font-size:13px;">
                    <strong style="color:var(--text-main);">🇨🇳 大中华风险情绪组</strong>
                    <span>{_pct_badge(gc_pct)} · <strong style="color:var(--color-accent);">{gc_score:.0f}/100</strong></span>
                </div>
                <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(145px, 1fr)); gap:8px;">
                    {_member_pills(gc.get("members") or [])}
                </div>
            </div>
        </div>
        <div class="liquidity-summary" style="margin-top:10px;">
            🧭 <b>亚盘传导总览：</b>{_html.escape(str(sent.get("summary_cn") or "--"))} · <span style="color:var(--text-muted);">快照更新: {upd}</span>
        </div>
    </section>
"""
