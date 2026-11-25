# -*- coding: utf-8 -*-
"""
纯只读探测脚本：检测 Binance Equity API (`sapi/v1/equity/market/*`)
对当前美股/ETF 监控池的覆盖率、盘前盘后/夜盘支持度及实时盘口点差质量。

安全约束：
- 仅从本地 .env 读取 BINANCE_API_KEY，严禁打印明文 Key；
- 绝对只读：不修改任何生产代码、账本或守护进程。
"""

import ast
import concurrent.futures
import datetime
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")
RADAR_PY_PATH = os.path.join(BASE_DIR, "integrated_market_radar.py")
SNAPSHOT_PATH = os.path.join(BASE_DIR, "data_cache", "latest_radar_snapshot.json")
SHORT_AUDIT_REPORT_PATH = os.path.join(BASE_DIR, "reports", "binance_short_channels_audit.md")

EXCHANGE_INFO_URL = "https://api.binance.com/sapi/v1/equity/market/exchangeInfo"
QUOTE_URL = "https://api.binance.com/sapi/v1/equity/market/quote"
FAPI_EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
FAPI_BOOK_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/bookTicker"
FAPI_PREMIUM_INDEX_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"

NON_US_EQUITY_EXCLUDES = frozenset({"BTC-USD", "XAUUSD", "KOSPI"})
SAMPLE_CORE_SYMBOLS = [
    "NVDA", "SOXL", "SOXS", "TQQQ", "SQQQ", "QQQ", "SPY",
    "MU", "AVGO", "TSM", "ASML", "GLD", "TLT", "VIXY",
]

# 板块 & 宽基反向 ETF 清单（symbol -> (底层指数/板块, 倍数, 说明)）
SECTOR_INVERSE_ETFS: List[Tuple[str, str, str, str]] = [
    ("SOXS", "半导体 (ICE Semi)", "-3x", "半导体三倍反向，费半/AI芯片/光通信核心做空工具"),
    ("SQQQ", "纳斯达克100 (QQQ)", "-3x", "纳指三倍反向，科技成长核心做空工具"),
    ("QID", "纳斯达克100 (QQQ)", "-2x", "纳指两倍反向，损耗低于 SQQQ"),
    ("PSQ", "纳斯达克100 (QQQ)", "-1x", "纳指一倍反向，适合波段对冲"),
    ("SPXU", "标普500 (SPY)", "-3x", "标普三倍反向，大盘系统性下跌工具"),
    ("SPXS", "标普500 (SPY)", "-3x", "标普三倍反向 (Direxion)"),
    ("SDS", "标普500 (SPY)", "-2x", "标普两倍反向"),
    ("SH", "标普500 (SPY)", "-1x", "标普一倍反向，低损耗大盘对冲"),
    ("SDOW", "道琼斯30 (DIA)", "-3x", "道指三倍反向，适配 DIA/蓝筹防御板块"),
    ("TZA", "罗素2000 (IWM)", "-3x", "小盘股三倍反向，适配高 Beta 中小盘"),
    ("RWM", "罗素2000 (IWM)", "-1x", "小盘股一倍反向"),
    ("TBF", "20Y+美债 (TLT)", "-1x", "长端美债一倍反向，利率上行对冲"),
    ("TBT", "20Y+美债 (TLT)", "-2x", "长端美债两倍反向"),
    ("TMV", "20Y+美债 (TLT)", "-3x", "长端美债三倍反向"),
]

# 已核验的个股反向 ETF 清单（underlying -> [(symbol, leverage, issuer_desc), ...]）
SINGLE_STOCK_INVERSE_ETFS: Dict[str, List[Tuple[str, str, str]]] = {
    "NVDA": [
        ("NVDQ", "-2x", "T-Rex 2X Inverse NVDA"),
        ("NVD", "-2x", "GraniteShares 2x Short NVDA"),
        ("NVDS", "-1.5x", "Tradr 1.5X Short NVDA"),
        ("NVDD", "-1x", "Direxion Daily NVDA Bear 1X"),
    ],
    "TSLA": [
        ("TSLQ", "-2x", "Tradr 2X Short TSLA"),
        ("TSLZ", "-2x", "T-Rex 2X Inverse TSLA"),
        ("TSDD", "-2x", "GraniteShares 2x Short TSLA"),
        ("TSLS", "-1x", "Direxion Daily TSLA Bear 1X"),
    ],
    "MSTR": [
        ("MSTZ", "-2x", "T-Rex 2X Inverse MSTR"),
        ("SMST", "-2x", "Defiance 2x Short MSTR"),
    ],
    "COIN": [
        ("CONI", "-2x", "GraniteShares 2x Short COIN"),
    ],
    "AMD": [
        ("AMDD", "-1x", "Direxion Daily AMD Bear 1X"),
    ],
    "PLTR": [
        ("PLTD", "-1x", "Direxion Daily PLTR Bear 1X"),
        ("PLTZ", "-2x", "Defiance 2x Short PLTR"),
    ],
    "META": [
        ("METD", "-1x", "Direxion Daily META Bear 1X"),
    ],
    "AMZN": [
        ("AMZD", "-1x", "Direxion Daily AMZN Bear 1X"),
    ],
    "MSFT": [
        ("MSFD", "-1x", "Direxion Daily MSFT Bear 1X"),
    ],
    "AAPL": [
        ("AAPD", "-1x", "Direxion Daily AAPL Bear 1X"),
    ],
    "GOOGL": [
        ("GGLS", "-1x", "Direxion Daily GOOGL Bear 1X"),
    ],
    "TSM": [
        ("TSMZ", "-1x", "Direxion Daily TSM Bear 1X"),
    ],
    "MU": [
        ("MUD", "-1x", "Direxion Daily MU Bear 1X"),
    ],
    "AVGO": [
        ("AVS", "-1x", "Direxion Daily AVGO Bear 1X"),
    ],
}

# 本身已经是反向/对冲型的标的（做空思路不同）
NATIVE_INVERSE_OR_HEDGE = frozenset({"SOXS", "SQQQ", "VIXY"})

# 板块兜底映射规则（针对无 TradFi 永续的剩余标的）
SEMI_CAT_KEYWORDS = ("半导体", "芯片", "光通信", "存储", "硬件", "算力", "AI基建", "制造设备", "高速互联", "晶圆")
BROAD_VALUE_CAT_KEYWORDS = ("金融", "消费", "价值", "医药", "能源", "工业", "道琼斯")


def load_binance_api_key(env_path: str) -> str:
    key = os.environ.get("BINANCE_API_KEY", "").strip()
    if key:
        return key
    if not os.path.exists(env_path):
        return ""
    with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "BINANCE_API_KEY":
                return v.strip().strip('"').strip("'")
    return ""


def load_monitored_us_targets(radar_py_path: str) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """纯 AST 只读提取 MONITORED_TARGETS（零执行、零副作用）。"""
    with open(radar_py_path, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    targets_dict: Dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "MONITORED_TARGETS":
                    targets_dict = ast.literal_eval(node.value)
                    break
        if targets_dict:
            break

    meta: Dict[str, Dict[str, Any]] = {}
    excluded: List[str] = []
    for group_key in ("ETFS", "STOCKS"):
        grp = targets_dict.get(group_key) or {}
        for tk, info in grp.items():
            tk_u = str(tk).strip().upper()
            if tk_u in NON_US_EQUITY_EXCLUDES:
                excluded.append(tk_u)
                continue
            row = dict(info) if isinstance(info, dict) else {"name": str(info)}
            row["group"] = group_key
            meta[tk_u] = row
    return meta, sorted(set(excluded))


def load_local_snapshot_prices(snapshot_path: str) -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(snapshot_path):
        return {}
    try:
        with open(snapshot_path, "r", encoding="utf-8") as f:
            doc = json.load(f) or {}
        out: Dict[str, Dict[str, Any]] = {}
        raw = doc.get("tickers") or {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(v, dict):
                    out[str(k).upper()] = v
        elif isinstance(raw, list):
            for v in raw:
                if isinstance(v, dict) and v.get("ticker"):
                    out[str(v["ticker"]).upper()] = v
        return out
    except Exception:
        return {}


def _extract_symbol_records(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for k in ("symbols", "data", "assets", "instruments", "rows", "list"):
            v = payload.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                for sk in ("symbols", "assets", "list", "rows"):
                    sv = v.get(sk)
                    if isinstance(sv, list):
                        return [x for x in sv if isinstance(x, dict)]
    return []


def _truthy(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    s = str(val or "").strip().lower()
    return s in ("true", "1", "yes", "enabled", "supported", "tradable", "active", "open")


def _safe_float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "" or isinstance(val, bool):
            return None
        f = float(val)
        return f if f == f else None
    except Exception:
        return None


def main() -> None:
    api_key = load_binance_api_key(ENV_PATH)
    print(f"[1] BINANCE_API_KEY 检测: present={bool(api_key)} (length={len(api_key)})")
    if not api_key:
        print("[!] 本地 .env 未检测到 BINANCE_API_KEY，仍尝试发送请求以查看服务端响应码。")

    us_pool, excluded = load_monitored_us_targets(RADAR_PY_PATH)
    snap_prices = load_local_snapshot_prices(SNAPSHOT_PATH)
    print(f"[2] 本地监控池提取: 美股/ETF 有效标的 = {len(us_pool)} 只 (已剔除非美股: {', '.join(excluded)})")

    headers = {"X-MBX-APIKEY": api_key} if api_key else {}
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; EquityProbe/1.0)"})

    t0 = time.perf_counter()
    try:
        resp = session.get(EXCHANGE_INFO_URL, headers=headers, timeout=15)
        dt_ms = (time.perf_counter() - t0) * 1000.0
    except Exception as exc:
        print(f"[!] exchangeInfo 请求异常: {type(exc).__name__}: {exc}")
        return

    print(f"[3] exchangeInfo HTTP 状态: {resp.status_code} | 耗时: {dt_ms:.1f} ms")
    try:
        ex_json = resp.json()
    except Exception:
        ex_json = None
        print(f"[!] 非 JSON 响应体 (前 300 字符): {resp.text[:300]}")

    if resp.status_code != 200 or ex_json is None:
        if isinstance(ex_json, dict):
            print(f"[!] 服务端错误详情: code={ex_json.get('code')} msg={ex_json.get('msg') or ex_json.get('message')}")
        elif resp.text:
            print(f"[!] 响应预览: {resp.text[:300]}")
        return

    if isinstance(ex_json, dict):
        print(f"    顶层字段: {sorted(ex_json.keys())}")
    records = _extract_symbol_records(ex_json)
    print(f"    解析币安美股总上架数量: {len(records)} 只")
    if records:
        print(f"    单条样本字段键: {sorted(records[0].keys())}")

    by_sym: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        sym = str(rec.get("symbol") or rec.get("ticker") or rec.get("asset") or "").strip().upper()
        if sym:
            by_sym[sym] = rec

    covered = sorted([tk for tk in us_pool if tk in by_sym])
    uncovered = sorted([tk for tk in us_pool if tk not in by_sym])

    print(f"\n[4] 监控池覆盖率: {len(covered)}/{len(us_pool)} ({(len(covered)/max(1,len(us_pool))*100):.1f}%)")
    print(f"    已覆盖 ({len(covered)}): {', '.join(covered)}")
    print(f"    未覆盖 ({len(uncovered)}): {', '.join(uncovered)}")

    # 重点分类核查
    lev_3x_check = ["SOXL", "SOXS", "TQQQ", "SQQQ", "KORU"]
    semi_core = ["NVDA", "AVGO", "TSM", "AMD", "MU", "SNDK", "AMAT", "ASML", "MRVL", "QCOM", "INTC", "SMH", "SOXX"]
    optical_small = ["AAOI", "COHR", "LITE", "FN", "CRDO", "ALAB", "NBIS", "CRWV"]
    print("\n    [重点分类覆盖核查]")
    for title, lst in (
        ("3x 杠杆 ETF", lev_3x_check),
        ("核心半导体", semi_core),
        ("光通信/算力中小盘", optical_small),
    ):
        yes_l = [t for t in lst if t in by_sym]
        no_l = [t for t in lst if t not in by_sym]
        print(f"    - {title}: 支持({len(yes_l)})={yes_l} | 不支持({len(no_l)})={no_l}")

    # 时段与字段统计
    tradable_cnt = 0
    ext_cnt = 0
    overnight_cnt = 0
    frac_cnt = 0
    tradability_vals: Dict[str, int] = {}
    ext_supported_list: List[str] = []
    ov_supported_list: List[str] = []
    for tk in covered:
        r = by_sym[tk]
        tr_v = r.get("tradability")
        tradability_vals[str(tr_v)] = tradability_vals.get(str(tr_v), 0) + 1
        if _truthy(tr_v) or str(tr_v or "").upper() in ("TRADABLE", "BUY_AND_SELL", "ENABLED", "ACTIVE"):
            tradable_cnt += 1
        if _truthy(r.get("extendedSession")):
            ext_cnt += 1
            ext_supported_list.append(tk)
        if _truthy(r.get("overnightSupported")):
            overnight_cnt += 1
            ov_supported_list.append(tk)
        if _truthy(r.get("fractionable")):
            frac_cnt += 1

    print("\n[5] 已覆盖标的时段与属性统计:")
    print(f"    - tradability 分布: {tradability_vals} (可交易约 {tradable_cnt}/{len(covered)})")
    print(f"    - extendedSession (盘前/盘后): {ext_cnt}/{len(covered)}")
    print(f"    - overnightSupported (夜盘): {overnight_cnt}/{len(covered)}")
    print(f"    - fractionable (碎股): {frac_cnt}/{len(covered)}")

    # 抽样实时报价探测
    probe_syms = [s for s in SAMPLE_CORE_SYMBOLS if s in by_sym]
    if len(probe_syms) < 6:
        for s in covered:
            if s not in probe_syms:
                probe_syms.append(s)
            if len(probe_syms) >= 10:
                break

    print(f"\n[6] 实时报价抽样探测 ({len(probe_syms)} 只已上架标的):")
    if probe_syms:
        sample_keys_printed = False
        print(f"    {'Symbol':<7} | {'HTTP':<4} | {'Lat(ms)':>7} | {'Bid':>9} | {'Ask':>9} | {'Last/Mid':>9} | {'Spread(bps)':>11} | {'LocalCache':>10} | {'Diff%':>7}")
        print("    " + "-" * 96)
        for sym in probe_syms:
            qt0 = time.perf_counter()
            try:
                qr = session.get(QUOTE_URL, params={"symbol": sym}, headers=headers, timeout=10)
                qlat = (time.perf_counter() - qt0) * 1000.0
                qjson = qr.json() if qr.headers.get("content-type", "").startswith("application/json") or qr.text.startswith("{") else {}
            except Exception as exc:
                print(f"    {sym:<7} | ERR  |     N/A | {type(exc).__name__}: {exc}")
                continue

            qdata = qjson.get("data") if isinstance(qjson, dict) and isinstance(qjson.get("data"), dict) else qjson
            if isinstance(qdata, dict) and not sample_keys_printed and qr.status_code == 200:
                print(f"    [quote 字段样例 ({sym})]: {sorted(qdata.keys())}")
                sample_keys_printed = True

            bid = _safe_float(qdata.get("bidPrice") or qdata.get("bid") or qdata.get("bp")) if isinstance(qdata, dict) else None
            ask = _safe_float(qdata.get("askPrice") or qdata.get("ask") or qdata.get("ap")) if isinstance(qdata, dict) else None
            last_p = _safe_float(
                qdata.get("lastPrice") or qdata.get("price") or qdata.get("last") or qdata.get("latestPrice") or qdata.get("markPrice")
            ) if isinstance(qdata, dict) else None
            mid = ((bid + ask) / 2.0) if (bid and ask and bid > 0 and ask > 0) else last_p
            spread_bps = (((ask - bid) / mid) * 10000.0) if (bid and ask and mid and mid > 0) else None

            sn = snap_prices.get(sym) or {}
            loc_px = _safe_float(sn.get("ext_price")) or _safe_float(sn.get("price")) or _safe_float(sn.get("last"))
            ref_px = last_p or mid
            diff_pct = (((ref_px - loc_px) / loc_px) * 100.0) if (ref_px and loc_px and loc_px > 0) else None

            def _f(v: Optional[float], fmt: str = "{:.2f}") -> str:
                return fmt.format(v) if v is not None else "N/A"

            print(
                f"    {sym:<7} | {qr.status_code:<4} | {qlat:7.1f} | {_f(bid):>9} | {_f(ask):>9} | "
                f"{_f(ref_px):>9} | {_f(spread_bps, '{:.1f}'):>11} | {_f(loc_px):>10} | {_f(diff_pct, '{:+.2f}%'):>7}"
            )
            if qr.status_code != 200 and isinstance(qjson, dict):
                print(f"      -> err: code={qjson.get('code')} msg={qjson.get('msg') or qjson.get('message')}")
    else:
        print("    无可抽样的已上架标的。")

    # 任务二：只读盘点币安做空通道（TradFi 永续合约 + 板块/个股反向 ETF + 77 标的分层对照表）
    if "--skip-short-audit" not in sys.argv:
        audit_short_channels(
            session=session,
            headers=headers,
            us_pool=us_pool,
            equity_by_sym=by_sym,
            total_equity_listed=len(records),
            report_path=SHORT_AUDIT_REPORT_PATH,
        )


def _fetch_equity_quote_batch(
    symbols: List[str],
    headers: Dict[str, str],
    max_workers: int = 5,
    timeout: float = 3.0,
) -> Dict[str, Dict[str, Any]]:
    """并发拉取币安美股 L1 实时报价（只读、4~6 线程、单次 3s 超时）。"""
    out: Dict[str, Dict[str, Any]] = {}
    if not symbols:
        return out

    def _one(sym: str) -> Tuple[str, Dict[str, Any]]:
        t0 = time.perf_counter()
        try:
            r = requests.get(
                QUOTE_URL,
                params={"symbol": sym},
                headers={"User-Agent": "Mozilla/5.0 (compatible; EquityProbe/1.0)", **headers},
                timeout=timeout,
            )
            lat_ms = (time.perf_counter() - t0) * 1000.0
            if r.status_code != 200:
                return sym, {"ok": False, "status": r.status_code, "lat_ms": lat_ms}
            raw = r.json()
            qdata = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
            if not isinstance(qdata, dict):
                return sym, {"ok": False, "status": r.status_code, "lat_ms": lat_ms}
            bid = _safe_float(qdata.get("bidPrice") or qdata.get("bid") or qdata.get("bp"))
            ask = _safe_float(qdata.get("askPrice") or qdata.get("ask") or qdata.get("ap"))
            last_p = _safe_float(
                qdata.get("lastPrice") or qdata.get("price") or qdata.get("last") or qdata.get("latestPrice")
            )
            valid_book = (bid is not None and ask is not None and bid > 0 and ask > 0 and ask >= bid)
            mid = ((bid + ask) / 2.0) if valid_book else last_p
            spread_bps = (((ask - bid) / mid) * 10000.0) if (valid_book and mid and mid > 0) else None
            return sym, {
                "ok": valid_book or (last_p is not None and last_p > 0),
                "status": r.status_code,
                "lat_ms": lat_ms,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread_bps": spread_bps,
            }
        except Exception as exc:
            return sym, {"ok": False, "status": "ERR", "error": type(exc).__name__}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(6, max_workers))) as ex:
        for sym, res in ex.map(_one, symbols):
            out[sym] = res
    return out


def _fmt_num(val: Optional[float], fmt: str = "{:.2f}") -> str:
    return fmt.format(val) if val is not None else "N/A"


def _fmt_bps(val: Optional[float]) -> str:
    return f"{val:.1f} bps" if val is not None else "N/A"


def _fmt_funding(rate: Optional[float]) -> str:
    if rate is None:
        return "N/A"
    return f"{rate * 100.0:+.4f}%"


def _choose_sector_fallback(tk: str, meta: Dict[str, Any]) -> Tuple[str, str]:
    """为无 TradFi 永续或需板块备选的标的分配合理的板块/代理做空通道。"""
    if tk == "GLD":
        return "XAUUSDT (TradFi 永续)", "黄金现货同源 1x 永续合约直接替代做空"
    if tk == "TLT":
        return "TBF (-1x) / TBT (-2x) / TMV (-3x)", "20Y+ 长端美债专项反向 ETF"
    if tk == "DIA":
        return "SDOW (-3x) / SH (-1x) / SPXU (-3x)", "道指专项反向 ETF（注意已排除加密货币 DIAUSDT）"
    if tk == "SPY":
        return "SH (-1x) / SDS (-2x) / SPXU (-3x)", "标普500专项反向 ETF"
    if tk == "QQQ":
        return "PSQ (-1x) / QID (-2x) / SQQQ (-3x)", "纳斯达克100专项反向 ETF"
    if tk == "IWM":
        return "RWM (-1x) / TZA (-3x)", "罗素2000小盘股专项反向 ETF"
    if tk == "SOXL":
        return "SOXS (-3x)", "费城半导体三倍反向 ETF 镜像对冲"
    if tk in NATIVE_INVERSE_OR_HEDGE:
        return "本身为反向/对冲工具", "多头看空时直接做多本标的（或做空对应正向基准）"
    cat = str(meta.get("cat") or "")
    sub = str(meta.get("sub") or "")
    text = f"{cat} {sub}"
    if any(k in text for k in SEMI_CAT_KEYWORDS):
        return "SOXS (-3x) / SQQQ (-3x)", f"属于「{cat or '硬科技'}」，无直连永续，统一映射半导体/纳指反向 ETF"
    if any(k in text for k in BROAD_VALUE_CAT_KEYWORDS):
        return "SH (-1x) / SPXU (-3x) / SDOW (-3x)", f"属于「{cat or '蓝筹防守'}」，无直连永续，映射标普/道指反向 ETF"
    return "SQQQ (-3x) / TZA (-3x)", f"属于「{cat or '高弹性成长'}」，无直连永续，映射纳指/罗素小盘反向 ETF"


def audit_short_channels(
    session: requests.Session,
    headers: Dict[str, str],
    us_pool: Dict[str, Dict[str, Any]],
    equity_by_sym: Dict[str, Dict[str, Any]],
    total_equity_listed: int,
    report_path: str = SHORT_AUDIT_REPORT_PATH,
) -> Dict[str, Any]:
    """
    纯只读盘点币安做空通道：
    1) fapi TradFi USDT 本位永续合约（严格过滤 TRADIFI_PERPETUAL / TradFi，排除加密同名币碰撞）；
    2) 币安美股清单中的板块反向 ETF 与核心个股反向 ETF 覆盖及实时 /quote 点差；
    3) 输出「77 标的币安做空通道分层对照表」至 reports/binance_short_channels_audit.md。
    """
    print("\n" + "=" * 76)
    print("[7] 开始只读盘点币安做空通道 (TradFi 永续 + 板块/个股反向 ETF)...")
    print("=" * 76)

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- 7.1 拉取 Binance Futures 公开三接口 ---
    t0 = time.perf_counter()
    fapi_ex = session.get(FAPI_EXCHANGE_INFO_URL, timeout=15)
    fapi_bt = session.get(FAPI_BOOK_TICKER_URL, timeout=15)
    fapi_pi = session.get(FAPI_PREMIUM_INDEX_URL, timeout=15)
    fapi_ms = (time.perf_counter() - t0) * 1000.0
    print(
        f"    FAPI 公开接口状态: exchangeInfo={fapi_ex.status_code}, "
        f"bookTicker={fapi_bt.status_code}, premiumIndex={fapi_pi.status_code} | 耗时 {fapi_ms:.0f} ms"
    )

    fapi_symbols = (fapi_ex.json() or {}).get("symbols", []) if fapi_ex.status_code == 200 else []
    bt_map: Dict[str, Dict[str, Any]] = {}
    if fapi_bt.status_code == 200:
        for row in fapi_bt.json() or []:
            if isinstance(row, dict) and row.get("symbol"):
                bt_map[str(row["symbol"]).upper()] = row
    pi_map: Dict[str, Dict[str, Any]] = {}
    if fapi_pi.status_code == 200:
        for row in fapi_pi.json() or []:
            if isinstance(row, dict) and row.get("symbol"):
                pi_map[str(row["symbol"]).upper()] = row

    all_tradfi_perps: Dict[str, Dict[str, Any]] = {}
    crypto_collisions: List[Dict[str, Any]] = []
    for s in fapi_symbols:
        if s.get("status") != "TRADING" or s.get("quoteAsset") != "USDT":
            continue
        base = str(s.get("baseAsset") or "").upper()
        sym = str(s.get("symbol") or "").upper()
        ctype = str(s.get("contractType") or "")
        subtypes = list(s.get("underlyingSubType") or [])
        is_tradfi = (ctype == "TRADIFI_PERPETUAL") or any("tradfi" in str(x).lower() for x in subtypes)
        if is_tradfi:
            bt = bt_map.get(sym) or {}
            pi = pi_map.get(sym) or {}
            bid = _safe_float(bt.get("bidPrice"))
            ask = _safe_float(bt.get("askPrice"))
            valid_book = (bid is not None and ask is not None and bid > 0 and ask > 0 and ask >= bid)
            mid = ((bid + ask) / 2.0) if valid_book else _safe_float(pi.get("markPrice"))
            spread_bps = (((ask - bid) / mid) * 10000.0) if (valid_book and mid and mid > 0) else None
            funding_rate = _safe_float(pi.get("lastFundingRate"))
            mark_price = _safe_float(pi.get("markPrice"))
            all_tradfi_perps[base] = {
                "symbol": sym,
                "base": base,
                "contractType": ctype,
                "underlyingSubType": subtypes,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "mark_price": mark_price,
                "spread_bps": spread_bps,
                "funding_rate": funding_rate,
            }
        elif base in us_pool:
            crypto_collisions.append({
                "ticker": base,
                "symbol": sym,
                "contractType": ctype,
                "underlyingSubType": subtypes,
            })

    pool_tradfi_hits = sorted([tk for tk in us_pool if tk in all_tradfi_perps])
    pool_tradfi_misses = sorted([tk for tk in us_pool if tk not in all_tradfi_perps])
    tradfi_spreads = [
        all_tradfi_perps[tk]["spread_bps"]
        for tk in pool_tradfi_hits
        if all_tradfi_perps[tk]["spread_bps"] is not None
    ]
    med_tradfi_spread = sorted(tradfi_spreads)[len(tradfi_spreads) // 2] if tradfi_spreads else None

    print(
        f"    TradFi USDT 永续总上架: {len(all_tradfi_perps)} 个 | 命中监控池: {len(pool_tradfi_hits)}/{len(us_pool)} "
        f"({len(pool_tradfi_hits)/max(1, len(us_pool))*100:.1f}%) | 中位点差: {_fmt_bps(med_tradfi_spread)}"
    )
    if crypto_collisions:
        print(
            "    已自动剔除加密货币同名碰撞: "
            + ", ".join(f"{c['symbol']}({ '/'.join(c['underlyingSubType']) })" for c in crypto_collisions)
        )

    # --- 7.2 核对板块 & 个股反向 ETF 并并发拉取实时 /quote ---
    sector_listed = [row for row in SECTOR_INVERSE_ETFS if row[0] in equity_by_sym]
    sector_missing = [row for row in SECTOR_INVERSE_ETFS if row[0] not in equity_by_sym]

    single_listed_rows: List[Tuple[str, str, str, str]] = []
    single_missing_rows: List[Tuple[str, str, str, str]] = []
    single_by_underlying: Dict[str, List[Tuple[str, str, str]]] = {}
    for underlying, etfs in SINGLE_STOCK_INVERSE_ETFS.items():
        for sym, lev, desc in etfs:
            if sym in equity_by_sym:
                single_listed_rows.append((underlying, sym, lev, desc))
                single_by_underlying.setdefault(underlying, []).append((sym, lev, desc))
            else:
                single_missing_rows.append((underlying, sym, lev, desc))

    all_inv_syms = sorted({r[0] for r in sector_listed} | {r[1] for r in single_listed_rows})
    qt0 = time.perf_counter()
    inv_quotes = _fetch_equity_quote_batch(all_inv_syms, headers=headers, max_workers=5, timeout=3.0)
    qt_ms = (time.perf_counter() - qt0) * 1000.0
    inv_ok = sum(1 for v in inv_quotes.values() if v.get("ok"))
    print(
        f"    反向 ETF 实时 /quote 拉取: 板块 {len(sector_listed)}/{len(SECTOR_INVERSE_ETFS)} + "
        f"个股 {len(single_listed_rows)} 只 | 报价有效 {inv_ok}/{len(all_inv_syms)} | 耗时 {qt_ms:.0f} ms"
    )

    # --- 7.3 组装 77 标的币安做空通道分层对照表 ---
    tier1_direct_perp: List[str] = []
    tier1_with_single_inv: List[str] = []
    tier2_dedicated_inv_only: List[str] = []
    tier3_sector_proxy: List[str] = []
    tier_native_inv: List[str] = []

    tiered_rows: List[Dict[str, Any]] = []
    for tk in sorted(us_pool.keys()):
        meta = us_pool[tk]
        cat = str(meta.get("cat") or meta.get("group") or "-")
        perp = all_tradfi_perps.get(tk)
        single_invs = single_by_underlying.get(tk) or []

        # 选出该标的流动性（点差）最好的个股反向 ETF
        best_single_str = "-"
        if single_invs:
            scored = []
            for s_sym, s_lev, _ in single_invs:
                sq = inv_quotes.get(s_sym) or {}
                sp = sq.get("spread_bps")
                scored.append((sp if sp is not None else 99999.0, s_sym, s_lev, sp))
            scored.sort(key=lambda x: x[0])
            best_single_str = ", ".join(
                f"`{s_sym}`({s_lev}, {_fmt_bps(sp)})" for _, s_sym, s_lev, sp in scored
            )

        if tk in NATIVE_INVERSE_OR_HEDGE:
            tier_native_inv.append(tk)
            tier_tag = "反向/对冲本体"
            primary_ch = f"`{perp['symbol']}` (可双向)" if perp else "现货多头即做空大盘"
            backup_ch = "做多本标的即看空基准"
            note = "本身为反向 ETF / 恐慌对冲工具"
        elif perp:
            tier1_direct_perp.append(tk)
            if single_invs:
                tier1_with_single_inv.append(tk)
            tier_tag = "T1: 1x TradFi 永续直空"
            primary_ch = f"`{perp['symbol']}` ({_fmt_bps(perp['spread_bps'])} · 费率 {_fmt_funding(perp['funding_rate'])})"
            if single_invs:
                backup_ch = best_single_str
                note = "永续 1x 直空 + 个股反向 ETF 双通道"
            else:
                fb_ch, _ = _choose_sector_fallback(tk, meta)
                backup_ch = fb_ch
                note = "优先 1x TradFi 永续直空，无杠杆损耗"
        elif tk in ("TLT", "DIA"):
            tier2_dedicated_inv_only.append(tk)
            fb_ch, fb_note = _choose_sector_fallback(tk, meta)
            tier_tag = "T2: 专项反向 ETF"
            primary_ch = fb_ch
            backup_ch = "`SH`/`SPXU`" if tk == "DIA" else "-"
            note = fb_note
        elif tk == "GLD":
            tier2_dedicated_inv_only.append(tk)
            xau = all_tradfi_perps.get("XAU")
            xau_str = (
                f"`XAUUSDT` ({_fmt_bps(xau['spread_bps'])} · 费率 {_fmt_funding(xau['funding_rate'])})"
                if xau else "`XAUUSDT`"
            )
            tier_tag = "T2: 同源商品永续替代"
            primary_ch = xau_str
            backup_ch = "暂无币安反向黄金 ETF"
            note = "黄金现货同源 TradFi 永续直接做空"
        else:
            tier3_sector_proxy.append(tk)
            fb_ch, fb_note = _choose_sector_fallback(tk, meta)
            tier_tag = "T3: 板块反向 ETF 映射"
            primary_ch = fb_ch
            backup_ch = "`SQQQ` / `SOXS`"
            note = fb_note

        tiered_rows.append({
            "ticker": tk,
            "name": str(meta.get("name") or tk),
            "cat": cat,
            "tier": tier_tag,
            "primary": primary_ch,
            "backup": backup_ch,
            "note": note,
        })

    # --- 7.4 生成 Markdown 报告 ---
    lines: List[str] = []
    lines.append("# 币安 77 只美股/ETF 做空通道只读盘点报告")
    lines.append("")
    lines.append(f"> 生成时间：`{now_str}`（纯只读探测，零下单、零私钥签名、零改动线上交易逻辑与生产账本）")
    lines.append("")
    lines.append("## 0. 核心结论总览")
    lines.append("")
    lines.append(f"- **币安 TradFi USDT 永续总上架**：`{len(all_tradfi_perps)}` 个合约（严格过滤 `contractType=TRADIFI_PERPETUAL` / `underlyingSubType=TradFi`，已剔除加密货币同名碰撞：`DIAUSDT`、`STXUSDT`）。")
    lines.append(
        f"- **77 只监控池 TradFi 永续直接覆盖**：**`{len(pool_tradfi_hits)} / {len(us_pool)}`（`{len(pool_tradfi_hits)/max(1,len(us_pool))*100:.1f}%`）**，"
        f"中位实时盘口点差 **`{_fmt_bps(med_tradfi_spread)}`**"
        + (f"（范围 `{min(tradfi_spreads):.2f} ~ {max(tradfi_spreads):.2f} bps`）" if tradfi_spreads else "")
        + "，支持 1x~低倍无日度复利损耗的精准单标的做空。"
    )
    lines.append(
        f"- **币安美股清单（共 `{total_equity_listed}` 只）反向 ETF 覆盖**："
        f"主流板块/宽基/美债反向 ETF **`{len(sector_listed)} / {len(SECTOR_INVERSE_ETFS)}`** 全覆盖；"
        f"核心个股反向 ETF **`{len(single_listed_rows)}`** 只（覆盖 `{len(single_by_underlying)}` 只高弹性核心个股，如 `NVDA/TSLA/MSTR/COIN/AMD/PLTR/META/AMZN/MSFT/AAPL/GOOGL/TSM/MU/AVGO`）。"
    )
    lines.append(
        f"- **分层架构统计（合计 77 只）**："
        f"**T1 TradFi 永续直空 `{len(tier1_direct_perp)}` 只**（其中 `{len(tier1_with_single_inv)}` 只同时具备个股反向 ETF 备选） | "
        f"**T2 专项反向 ETF / 同源永续 `{len(tier2_dedicated_inv_only)}` 只**（`GLD->XAUUSDT`、`TLT->TBF/TBT/TMV`、`DIA->SDOW`） | "
        f"**T3 板块反向 ETF 兜底映射 `{len(tier3_sector_proxy)}` 只**（`{', '.join(tier3_sector_proxy)}`） | "
        f"**反向/对冲本体 `{len(tier_native_inv)}` 只**（`{', '.join(tier_native_inv)}`）。"
    )
    if crypto_collisions:
        lines.append(
            "- **防同名碰撞护栏**：FAPI 中存在与美股代码同名的加密货币永续合约："
            + "、".join(f"`{c['symbol']}`（分类：`{'/'.join(c['underlyingSubType'])}`）" for c in crypto_collisions)
            + "，实盘对接时**严禁**仅按 `baseAsset` 模糊匹配，必须强校验 `contractType == 'TRADIFI_PERPETUAL'`。"
        )
    lines.append("")

    # Section 1: TradFi Perps
    lines.append(f"## 1. TradFi 美股 USDT 本位永续合约覆盖盘点（{len(pool_tradfi_hits)}/{len(us_pool)}）")
    lines.append("")
    lines.append("| 标的 | 名称 | 分类 | 永续合约 | Bid | Ask | Mid/Mark | 实时点差 | 当前资金费率(8h) |")
    lines.append("| :--- | :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: |")
    for tk in sorted(
        pool_tradfi_hits,
        key=lambda x: (all_tradfi_perps[x]["spread_bps"] is None, all_tradfi_perps[x]["spread_bps"] or 99999.0),
    ):
        p = all_tradfi_perps[tk]
        m = us_pool[tk]
        lines.append(
            f"| `{tk}` | {m.get('name', tk)} | {m.get('cat', m.get('group', '-'))} | `{p['symbol']}` | "
            f"{_fmt_num(p['bid'])} | {_fmt_num(p['ask'])} | {_fmt_num(p['mid'])} | "
            f"{_fmt_bps(p['spread_bps'])} | {_fmt_funding(p['funding_rate'])} |"
        )
    lines.append("")
    lines.append(f"**未上线 TradFi 永续的 12 只监控标的**：`{', '.join(pool_tradfi_misses)}`。")
    if "XAU" in all_tradfi_perps:
        xau = all_tradfi_perps["XAU"]
        lines.append(
            f"（补充：黄金现货 TradFi 永续 `XAUUSDT` 已上线，Bid `{_fmt_num(xau['bid'])}` / Ask `{_fmt_num(xau['ask'])}` / "
            f"点差 `{_fmt_bps(xau['spread_bps'])}` / 资金费率 `{_fmt_funding(xau['funding_rate'])}`，可直接作为 `GLD` 的 1x 做空通道。）"
        )
    lines.append("")

    # Section 2: Sector & Broad Inverse ETFs
    lines.append(f"## 2. 板块 / 宽基 / 美债反向 ETF 盘口盘点（{len(sector_listed)}/{len(SECTOR_INVERSE_ETFS)} 已上架）")
    lines.append("")
    lines.append("| 反向 ETF | 追踪方向/底层 | 倍数 | 盘前盘后 | 夜盘 | Bid | Ask | Mid | 实时点差 | 说明 |")
    lines.append("| :--- | :--- | :---: | :---: | :---: | ---: | ---: | ---: | ---: | :--- |")
    for sym, target_desc, lev, note in sector_listed:
        rec = equity_by_sym[sym]
        q = inv_quotes.get(sym) or {}
        ext_ok = "✅" if _truthy(rec.get("extendedSession")) else "❌"
        ov_ok = "✅" if _truthy(rec.get("overnightSupported")) else "❌"
        lines.append(
            f"| `{sym}` | {target_desc} | {lev} | {ext_ok} | {ov_ok} | "
            f"{_fmt_num(q.get('bid'))} | {_fmt_num(q.get('ask'))} | {_fmt_num(q.get('mid'))} | "
            f"{_fmt_bps(q.get('spread_bps'))} | {note} |"
        )
    if sector_missing:
        lines.append("")
        lines.append("未上架的板块反向 ETF：" + ", ".join(f"`{r[0]}`" for r in sector_missing))
    lines.append("")

    # Section 3: Single-Stock Inverse ETFs
    lines.append(f"## 3. 核心高弹性个股反向 ETF 盘口盘点（{len(single_listed_rows)} 只已上架，覆盖 {len(single_by_underlying)} 只个股）")
    lines.append("")
    lines.append("| 正股 | 个股反向 ETF | 倍数 | 发行方/说明 | 盘前盘后 | 夜盘 | Bid | Ask | Mid | 实时点差 | 同标的 TradFi 永续点差 |")
    lines.append("| :--- | :--- | :---: | :--- | :---: | :---: | ---: | ---: | ---: | ---: | ---: |")
    for underlying, sym, lev, desc in single_listed_rows:
        rec = equity_by_sym[sym]
        q = inv_quotes.get(sym) or {}
        ext_ok = "✅" if _truthy(rec.get("extendedSession")) else "❌"
        ov_ok = "✅" if _truthy(rec.get("overnightSupported")) else "❌"
        perp_sp = _fmt_bps(all_tradfi_perps[underlying]["spread_bps"]) if underlying in all_tradfi_perps else "无永续"
        lines.append(
            f"| `{underlying}` | `{sym}` | {lev} | {desc} | {ext_ok} | {ov_ok} | "
            f"{_fmt_num(q.get('bid'))} | {_fmt_num(q.get('ask'))} | {_fmt_num(q.get('mid'))} | "
            f"{_fmt_bps(q.get('spread_bps'))} | {perp_sp} |"
        )
    if single_missing_rows:
        lines.append("")
        lines.append("未上架的候选个股反向 ETF：" + ", ".join(f"`{r[1]}`({r[0]})" for r in single_missing_rows))
    lines.append("")

    # Section 4: 77-ticker tiered matrix
    lines.append("## 4. 77 标的币安做空通道分层对照表")
    lines.append("")
    lines.append("| # | 标的 | 名称 | 分类 | 分层归属 | 推荐主做空通道 | 备选/板块兜底通道 | 备注 |")
    lines.append("| ---: | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
    for idx, row in enumerate(tiered_rows, 1):
        lines.append(
            f"| {idx} | `{row['ticker']}` | {row['name']} | {row['cat']} | {row['tier']} | "
            f"{row['primary']} | {row['backup']} | {row['note']} |"
        )
    lines.append("")

    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"    [+] 已生成只读做空通道盘点报告: {report_path}")

    return {
        "total_tradfi_perps": len(all_tradfi_perps),
        "pool_tradfi_hits": len(pool_tradfi_hits),
        "pool_total": len(us_pool),
        "med_tradfi_spread_bps": med_tradfi_spread,
        "sector_inv_listed": len(sector_listed),
        "single_inv_listed": len(single_listed_rows),
        "report_path": report_path,
    }


if __name__ == "__main__":
    main()
