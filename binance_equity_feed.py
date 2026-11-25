# -*- coding: utf-8 -*-
"""
Binance 美股 L1 实时报价主源模块 (binance_equity_feed.py)

职责边界（严格最小侵入）：
1. 仅服务 77 只美股/ETF 监控池的实时现价与 L1 盘口 (bid / ask / mid / spread_bps)；
   严禁覆盖 BTC-USD、XAUUSD、KOSPI 及 ^VIX/^VIX3M/^VXN/^VVIX/^TNX 等指数与期货。
2. 不替代 prev_close、5m OHLCV (VWAP/量比/走廊)、Finnhub 7 日财报日历与个股新闻。
3. 严控并发 4~6 线程 (默认 5)，单次请求超时 3.0s；
   - 未配置 BINANCE_API_KEY、超时、非 200 (含 429/418 冷却退避)、或脏盘口 (bid<=0 / ask<=0 / ask<bid)
     时自动返回空项，由调用方无缝回退至 Finnhub / Yahoo。
4. 严禁在日志或异常中打印明文 BINANCE_API_KEY。
"""

import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENV_PATH = os.path.join(BASE_DIR, ".env")
BINANCE_EQUITY_QUOTE_URL = "https://api.binance.com/sapi/v1/equity/market/quote"

DEFAULT_MAX_WORKERS = 5          # 严格控制在 4~6 线程
DEFAULT_TIMEOUT_SEC = 3.0        # 单次请求超时 3s
DEFAULT_RATE_LIMIT_COOLDOWN_SEC = 180.0

NON_BINANCE_EQUITY_EXCLUDES = frozenset({"BTC-USD", "XAUUSD", "KOSPI"})

_RL_LOCK = threading.Lock()
_RATE_LIMIT_UNTIL_TS = 0.0


def reset_rate_limit_cooldown() -> None:
    """仅供单元测试重置冷却状态。"""
    global _RATE_LIMIT_UNTIL_TS
    with _RL_LOCK:
        _RATE_LIMIT_UNTIL_TS = 0.0


def _is_in_rate_limit_cooldown(now_ts: Optional[float] = None) -> bool:
    t = time.time() if now_ts is None else float(now_ts)
    with _RL_LOCK:
        return t < _RATE_LIMIT_UNTIL_TS


def _trigger_rate_limit_cooldown(retry_after_header: Optional[str] = None, now_ts: Optional[float] = None) -> float:
    global _RATE_LIMIT_UNTIL_TS
    t = time.time() if now_ts is None else float(now_ts)
    cooldown = DEFAULT_RATE_LIMIT_COOLDOWN_SEC
    if retry_after_header:
        try:
            ra = float(str(retry_after_header).strip())
            if ra > 0:
                cooldown = max(30.0, min(600.0, ra))
        except Exception:
            pass
    with _RL_LOCK:
        _RATE_LIMIT_UNTIL_TS = max(_RATE_LIMIT_UNTIL_TS, t + cooldown)
    return cooldown


def get_binance_api_key(env_path: str = DEFAULT_ENV_PATH) -> str:
    """仅从环境变量或本机 .env 读取 BINANCE_API_KEY，严禁打印明文。"""
    key = os.environ.get("BINANCE_API_KEY", "").strip()
    if key:
        return key
    if env_path and os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == "BINANCE_API_KEY":
                        return v.strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


def is_eligible_us_equity(ticker: str) -> bool:
    tk = str(ticker or "").strip().upper()
    if not tk:
        return False
    if tk in NON_BINANCE_EQUITY_EXCLUDES:
        return False
    if tk.startswith("^") or "=F" in tk or "=" in tk:
        return False
    return True


def _safe_pos_float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "" or isinstance(val, bool):
            return None
        f = float(val)
        if math.isnan(f) or math.isinf(f) or f <= 0.0:
            return None
        return f
    except Exception:
        return None


def parse_binance_quote_payload(symbol: str, payload: Any) -> Optional[Dict[str, Any]]:
    """
    严格清洗单条 Binance L1 盘口：
    - 要求 bid > 0、ask > 0 且 ask >= bid；否则视为脏盘口返回 None（触发回退）。
    - 计算 mid = (bid + ask) / 2 与 spread_bps = (ask - bid) / mid * 10000。
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None

    bid = _safe_pos_float(data.get("bidPrice") if "bidPrice" in data else data.get("bid"))
    ask = _safe_pos_float(data.get("askPrice") if "askPrice" in data else data.get("ask"))
    if bid is None or ask is None or ask < bid:
        return None

    mid = (bid + ask) / 2.0
    if mid <= 0.0 or math.isnan(mid) or math.isinf(mid):
        return None
    spread_bps = ((ask - bid) / mid) * 10000.0
    bid_sz = _safe_pos_float(data.get("bidSize")) or 0.0
    ask_sz = _safe_pos_float(data.get("askSize")) or 0.0

    return {
        "symbol": str(symbol).strip().upper(),
        "price": round(mid, 4),
        "mid": round(mid, 4),
        "bid": round(bid, 4),
        "ask": round(ask, 4),
        "bid_size": round(bid_sz, 4),
        "ask_size": round(ask_sz, 4),
        "spread_bps": round(spread_bps, 2),
        "source": "BINANCE_EQUITY",
        "has_realtime": True,
    }


def fetch_binance_equity_quotes(
    tickers: Iterable[str],
    max_workers: int = DEFAULT_MAX_WORKERS,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    api_key: Optional[str] = None,
    http_get_fn: Optional[Callable[..., Any]] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """
    并发拉取美股/ETF 监控池的币安 L1 实时盘口。
    返回 (quotes_by_ticker, stats_dict)：
    - 未命中或异常的标的不会出现在 quotes_by_ticker 中，由主雷达自动回退 Finnhub / Yahoo。
    """
    t0 = time.perf_counter()
    eligible = []
    seen = set()
    for t in tickers or []:
        tk = str(t or "").strip().upper()
        if is_eligible_us_equity(tk) and tk not in seen:
            eligible.append(tk)
            seen.add(tk)

    key = (api_key if api_key is not None else get_binance_api_key()).strip()
    if not eligible or not key:
        return {}, {
            "enabled": bool(key),
            "hit": 0,
            "total": len(eligible),
            "fallback_count": len(eligible),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            "rate_limited": False,
            "skipped_reason": "missing_api_key" if not key else "empty_tickers",
        }

    if _is_in_rate_limit_cooldown():
        return {}, {
            "enabled": True,
            "hit": 0,
            "total": len(eligible),
            "fallback_count": len(eligible),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            "rate_limited": True,
            "skipped_reason": "rate_limit_cooldown",
        }

    workers = max(1, min(6, int(max_workers or DEFAULT_MAX_WORKERS)))
    req_timeout = max(0.5, min(10.0, float(timeout or DEFAULT_TIMEOUT_SEC)))
    headers = {
        "X-MBX-APIKEY": key,
        "User-Agent": "Mozilla/5.0 (compatible; RadarBinanceFeed/1.0)",
    }

    sess = None if http_get_fn is not None else requests.Session()
    if sess is not None:
        adapter = requests.adapters.HTTPAdapter(pool_connections=workers + 2, pool_maxsize=workers + 2)
        sess.mount("https://", adapter)
    getter = http_get_fn if http_get_fn is not None else sess.get

    rate_limited_flag = False
    dirty_cnt = 0
    err_cnt = 0

    def _fetch_one(sym: str) -> Tuple[str, Optional[Dict[str, Any]], str]:
        if _is_in_rate_limit_cooldown():
            return sym, None, "cooldown"
        try:
            resp = getter(
                BINANCE_EQUITY_QUOTE_URL,
                params={"symbol": sym},
                headers=headers,
                timeout=req_timeout,
            )
            status = int(getattr(resp, "status_code", 0) or 0)
            if status in (418, 429):
                hdrs = getattr(resp, "headers", None) or {}
                _trigger_rate_limit_cooldown(hdrs.get("Retry-After") if isinstance(hdrs, dict) else None)
                return sym, None, f"http_{status}"
            if status != 200:
                return sym, None, f"http_{status}"
            parsed = parse_binance_quote_payload(sym, resp.json())
            if parsed is None:
                return sym, None, "dirty_book"
            return sym, parsed, "ok"
        except Exception:
            return sym, None, "exception"

    quotes: Dict[str, Dict[str, Any]] = {}
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_fetch_one, tk) for tk in eligible]
            for fut in as_completed(futs):
                sym, q, tag = fut.result()
                if q is not None:
                    quotes[sym] = q
                elif tag in ("http_429", "http_418", "cooldown"):
                    rate_limited_flag = True
                elif tag == "dirty_book":
                    dirty_cnt += 1
                else:
                    err_cnt += 1
    finally:
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    return quotes, {
        "enabled": True,
        "hit": len(quotes),
        "total": len(eligible),
        "fallback_count": len(eligible) - len(quotes),
        "dirty_count": dirty_cnt,
        "error_count": err_cnt,
        "rate_limited": rate_limited_flag,
        "elapsed_ms": elapsed_ms,
    }


def build_realtime_quote_from_binance(
    ticker: str,
    bq: Dict[str, Any],
    df_hist: Any = None,
    ny_today: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """
    将 Binance L1 报价组装为主雷达标准 realtime_quotes 字典，
    prev_close 严格取自本地历史日线 df_hist（不破坏原有昨收口径）。
    """
    mid = _safe_pos_float((bq or {}).get("price") or (bq or {}).get("mid"))
    if mid is None:
        return None

    prev_c = 0.0
    if df_hist is not None and len(df_hist) > 0:
        try:
            if ny_today is None:
                try:
                    from zoneinfo import ZoneInfo
                    ny_today = datetime.now(ZoneInfo("America/New_York")).date()
                except Exception:
                    ny_today = datetime.now().date()
            last_idx = df_hist.index[-1]
            if hasattr(last_idx, "date") and last_idx.date() >= ny_today and len(df_hist) >= 2:
                prev_c = float(df_hist["Close"].iloc[-2])
            else:
                prev_c = float(df_hist["Close"].iloc[-1])
        except Exception:
            prev_c = 0.0

    chg = round(mid - prev_c, 4) if prev_c > 0 else 0.0
    pct_chg = round((chg / prev_c) * 100.0, 2) if prev_c > 0 else 0.0
    return {
        "last": round(mid, 4),
        "price": round(mid, 4),
        "change": chg,
        "pct_change": pct_chg,
        "prev_close": round(prev_c, 4) if prev_c > 0 else 0.0,
        "high": round(mid, 4),
        "low": round(mid, 4),
        "bid": bq.get("bid"),
        "ask": bq.get("ask"),
        "spread_bps": bq.get("spread_bps"),
        "source": "BINANCE_EQUITY",
        "has_realtime": True,
    }
