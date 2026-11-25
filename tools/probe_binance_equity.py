# -*- coding: utf-8 -*-
"""
纯只读探测脚本：检测 Binance Equity API (`sapi/v1/equity/market/*`)
对当前美股/ETF 监控池的覆盖率、盘前盘后/夜盘支持度及实时盘口点差质量。

安全约束：
- 仅从本地 .env 读取 BINANCE_API_KEY，严禁打印明文 Key；
- 绝对只读：不修改任何生产代码、账本或守护进程。
"""

import ast
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

EXCHANGE_INFO_URL = "https://api.binance.com/sapi/v1/equity/market/exchangeInfo"
QUOTE_URL = "https://api.binance.com/sapi/v1/equity/market/quote"

NON_US_EQUITY_EXCLUDES = frozenset({"BTC-USD", "XAUUSD", "KOSPI"})
SAMPLE_CORE_SYMBOLS = [
    "NVDA", "SOXL", "SOXS", "TQQQ", "SQQQ", "QQQ", "SPY",
    "MU", "AVGO", "TSM", "ASML", "GLD", "TLT", "VIXY",
]


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


if __name__ == "__main__":
    main()
