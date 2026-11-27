# -*- coding: utf-8 -*-
"""
独立币安公开带单 KOL 实时监控与 Discord 分频道推送守护进程 (binance_kol_monitor.py)

设计原则：
1. 与主美股雷达、AI 模型及双跟单账本完全物理隔离（零依赖、零互相写入）；
2. 零币安 API Key、仅调用币安公开带单只读接口（detail / positions / order-history）；
3. 支持多 KOL 独立 Webhook 分频道 + 可选汇总频道 + 配置热加载；
4. 自动聚合同一轮询周期内的同向 TWAP 拆单（防鲸鱼拆单刷屏），展示加权均价、名义价值、占带单余额比例与平仓盈亏；
5. 严格持久化去重游标（data_cache/binance_kol_state.json），冷启动不刷历史旧单，仅发一张初始化状态卡。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "data_cache", "binance_kol_config.json")
DEFAULT_STATE_PATH = os.path.join(BASE_DIR, "data_cache", "binance_kol_state.json")

BINANCE_DETAIL_URL = "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/lead-portfolio/detail"
BINANCE_POSITIONS_URL = "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/lead-data/positions"
BINANCE_ORDER_HISTORY_URL = "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/lead-portfolio/order-history"
BINANCE_LEAD_PAGE_URL = "https://www.binance.com/zh-CN/copy-trading/lead-details/{portfolio_id}"

CST_TZ = timezone(timedelta(hours=8))
KST_TZ = timezone(timedelta(hours=9))

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "lang": "zh-CN",
}

_STOP_REQUESTED = False


def _handle_signal(signum, frame):
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or val == "":
            return default
        f = float(val)
        return f if (f == f and abs(f) != float("inf")) else default
    except Exception:
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        if val is None or val == "":
            return default
        return int(float(val))
    except Exception:
        return default


def extract_portfolio_id(raw: str) -> str:
    """支持直接传入纯数字 portfolioId 或币安分享链接。"""
    s = str(raw or "").strip()
    m = re.search(r"lead-details/(\d+)", s)
    if m:
        return m.group(1)
    m2 = re.search(r"(\d{10,25})", s)
    return m2.group(1) if m2 else s


def fmt_price(px: float) -> str:
    p = abs(px)
    if p == 0:
        return "0"
    if p >= 1000:
        return f"{px:,.2f}"
    if p >= 10:
        return f"{px:,.3f}"
    if p >= 1:
        return f"{px:,.4f}"
    if p >= 0.01:
        return f"{px:.6f}".rstrip("0").rstrip(".")
    return f"{px:.8f}".rstrip("0").rstrip(".")


def fmt_qty(qty: float) -> str:
    q = abs(qty)
    if q >= 1000:
        return f"{qty:,.2f}" if (q % 1 != 0) else f"{int(round(qty)):,}"
    if q >= 1:
        return f"{qty:,.4f}".rstrip("0").rstrip(".")
    return f"{qty:.6f}".rstrip("0").rstrip(".")


def fmt_usd(val: float, signed: bool = False) -> str:
    if signed:
        sign = "+" if val >= 0 else "-"
        return f"{sign}${abs(val):,.2f}"
    return f"${val:,.2f}"


def fmt_ts_cst(ts_ms: int) -> str:
    if not ts_ms:
        return "-"
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=CST_TZ)
    return dt.strftime("%Y-%m-%d %H:%M:%S") + " UTC+8"


def classify_order_action(side: str, position_side: str, total_pnl: float = 0.0) -> Dict[str, Any]:
    """将币安合约 side + positionSide 映射为中文动作、图标与 Embed 颜色。"""
    s = str(side or "").strip().upper()
    ps = str(position_side or "").strip().upper()

    if ps == "LONG" and s == "BUY":
        return {"code": "OPEN_LONG", "label": "开多 / 加多", "emoji": "🟢", "color": 0x22C55E, "is_close": False}
    if ps == "LONG" and s == "SELL":
        color = 0x10B981 if total_pnl > 0 else (0xEF4444 if total_pnl < 0 else 0xF59E0B)
        return {"code": "CLOSE_LONG", "label": "平多 / 减多", "emoji": "🟡", "color": color, "is_close": True}
    if ps == "SHORT" and s == "SELL":
        return {"code": "OPEN_SHORT", "label": "开空 / 加空", "emoji": "🔴", "color": 0xEF4444, "is_close": False}
    if ps == "SHORT" and s == "BUY":
        color = 0x10B981 if total_pnl > 0 else (0xEF4444 if total_pnl < 0 else 0x38BDF8)
        return {"code": "CLOSE_SHORT", "label": "平空 / 减空", "emoji": "🔵", "color": color, "is_close": True}

    if s == "BUY":
        return {"code": "BUY", "label": "买入 (单向)", "emoji": "🟢", "color": 0x22C55E, "is_close": abs(total_pnl) > 0}
    if s == "SELL":
        return {"code": "SELL", "label": "卖出 (单向)", "emoji": "🔴", "color": 0xEF4444, "is_close": abs(total_pnl) > 0}
    return {"code": "UNKNOWN", "label": f"{s}/{ps}", "emoji": "⚪", "color": 0x94A3B8, "is_close": False}


def make_order_fingerprint(order: Dict[str, Any]) -> str:
    """为单笔成交构造确定性去重指纹（不依赖不稳定字段）。"""
    key_parts = [
        str(order.get("symbol") or ""),
        str(order.get("side") or "").upper(),
        str(order.get("positionSide") or "").upper(),
        str(_safe_int(order.get("orderUpdateTime") or order.get("orderTime"), 0)),
        f"{_safe_float(order.get('executedQty'), 0.0):.8f}",
        f"{_safe_float(order.get('avgPrice'), 0.0):.8f}",
        f"{_safe_float(order.get('totalPnl'), 0.0):.6f}",
    ]
    return hashlib.sha1("|".join(key_parts).encode("utf-8")).hexdigest()[:20]


def aggregate_orders_for_alert(orders: List[Dict[str, Any]], merge_window_ms: int = 120_000) -> List[Dict[str, Any]]:
    """
    将时间先后排列的新成交按 (symbol, side, positionSide) 且时间间隔 <= merge_window_ms
    智能合并（防鲸鱼 API 拆单 TWAP 瞬间连发 7 条消息刷屏）。
    输入可乱序，内部按 orderUpdateTime 升序处理，返回按时间升序的聚合组。
    """
    if not orders:
        return []

    sorted_orders = sorted(
        orders,
        key=lambda o: (_safe_int(o.get("orderUpdateTime") or o.get("orderTime"), 0), make_order_fingerprint(o)),
    )

    groups: List[Dict[str, Any]] = []
    for o in sorted_orders:
        sym = str(o.get("symbol") or "").strip()
        side = str(o.get("side") or "").strip().upper()
        ps = str(o.get("positionSide") or "").strip().upper()
        qty = _safe_float(o.get("executedQty"), 0.0)
        px = _safe_float(o.get("avgPrice"), 0.0)
        pnl = _safe_float(o.get("totalPnl"), 0.0)
        ts = _safe_int(o.get("orderUpdateTime") or o.get("orderTime"), 0)
        notional = qty * px

        merged = False
        for g in reversed(groups):
            if (
                g["symbol"] == sym
                and g["side"] == side
                and g["positionSide"] == ps
                and abs(ts - g["last_ts"]) <= merge_window_ms
            ):
                g["total_qty"] += qty
                g["total_notional"] += notional
                g["total_pnl"] += pnl
                g["fill_count"] += 1
                g["first_ts"] = min(g["first_ts"], ts)
                g["last_ts"] = max(g["last_ts"], ts)
                g["avg_price"] = (g["total_notional"] / g["total_qty"]) if g["total_qty"] > 0 else px
                merged = True
                break

        if not merged:
            groups.append({
                "symbol": sym,
                "baseAsset": str(o.get("baseAsset") or sym.replace("USDT", "")),
                "quoteAsset": str(o.get("quoteAsset") or "USDT"),
                "side": side,
                "positionSide": ps,
                "type": str(o.get("type") or "MARKET").upper(),
                "total_qty": qty,
                "total_notional": notional,
                "avg_price": px,
                "total_pnl": pnl,
                "fill_count": 1,
                "first_ts": ts,
                "last_ts": ts,
            })

    return groups


def fetch_kol_detail(session: requests.Session, portfolio_id: str, timeout: int = 12) -> Dict[str, Any]:
    r = session.get(
        BINANCE_DETAIL_URL,
        params={"portfolioId": portfolio_id},
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    r.raise_for_status()
    doc = r.json()
    if str(doc.get("code")) != "000000":
        raise RuntimeError(f"detail api code={doc.get('code')} msg={doc.get('message')}")
    return doc.get("data") or {}


def fetch_kol_positions(session: requests.Session, portfolio_id: str, timeout: int = 12) -> List[Dict[str, Any]]:
    r = session.get(
        BINANCE_POSITIONS_URL,
        params={"portfolioId": portfolio_id},
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    r.raise_for_status()
    doc = r.json()
    if str(doc.get("code")) != "000000":
        return []
    rows = doc.get("data") or []
    return [p for p in rows if abs(_safe_float(p.get("positionAmount"), 0.0)) > 0]


def fetch_kol_order_history(
    session: requests.Session,
    portfolio_id: str,
    page_size: int = 20,
    timeout: int = 12,
) -> List[Dict[str, Any]]:
    r = session.post(
        BINANCE_ORDER_HISTORY_URL,
        json={"portfolioId": portfolio_id, "pageNumber": 1, "pageSize": page_size},
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    r.raise_for_status()
    doc = r.json()
    if str(doc.get("code")) != "000000":
        raise RuntimeError(f"order-history api code={doc.get('code')} msg={doc.get('message')}")
    data = doc.get("data") or {}
    return data.get("list") or []


def build_trade_embed(
    kol_name: str,
    portfolio_id: str,
    group: Dict[str, Any],
    detail: Dict[str, Any],
    active_positions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """构造单组开/平仓 Discord Embed 卡片。"""
    act = classify_order_action(group["side"], group["positionSide"], group["total_pnl"])
    sym = group["symbol"]
    notional = group["total_notional"]
    margin_bal = _safe_float(detail.get("marginBalance"), 0.0)
    size_ratio_str = f"{(notional / margin_bal * 100.0):.1f}% 带单余额" if margin_bal > 0 else "—"

    title = f"{act['emoji']} 【{kol_name}】{act['label']} · {sym}"
    if group["fill_count"] > 1:
        title += f"（合并 {group['fill_count']} 笔拆单）"

    fields = [
        {"name": "合约标的", "value": f"`{sym}` ({group['type']})", "inline": True},
        {"name": "成交均价", "value": f"**{fmt_price(group['avg_price'])}** {group['quoteAsset']}", "inline": True},
        {"name": "成交数量", "value": f"{fmt_qty(group['total_qty'])} {group['baseAsset']}", "inline": True},
        {"name": "名义价值", "value": f"**{fmt_usd(notional)}**", "inline": True},
        {"name": "体量占比", "value": size_ratio_str, "inline": True},
    ]

    if act["is_close"] or abs(group["total_pnl"]) > 1e-6:
        pnl_emoji = "💰" if group["total_pnl"] >= 0 else "🩸"
        fields.append({
            "name": "已实现盈亏",
            "value": f"{pnl_emoji} **{fmt_usd(group['total_pnl'], signed=True)}**",
            "inline": True,
        })
    else:
        fields.append({
            "name": "带单余额",
            "value": fmt_usd(margin_bal) if margin_bal > 0 else "—",
            "inline": True,
        })

    if active_positions:
        pos_lines = []
        for p in active_positions[:5]:
            p_sym = p.get("symbol", "")
            p_side = "多" if str(p.get("positionSide", "")).upper() == "LONG" else ("空" if str(p.get("positionSide", "")).upper() == "SHORT" else str(p.get("positionSide", "")))
            p_lev = p.get("leverage", "?")
            p_entry = fmt_price(_safe_float(p.get("entryPrice"), 0.0))
            p_upnl = fmt_usd(_safe_float(p.get("unrealizedProfit"), 0.0), signed=True)
            pos_lines.append(f"`{p_sym}` {p_side} {p_lev}x · 均价 {p_entry} · 浮盈 {p_upnl}")
        if pos_lines:
            fields.append({"name": "当前公开持仓", "value": "\n".join(pos_lines), "inline": False})

    embed: Dict[str, Any] = {
        "title": title,
        "url": BINANCE_LEAD_PAGE_URL.format(portfolio_id=portfolio_id),
        "color": act["color"],
        "fields": fields,
        "footer": {
            "text": f"成交时间 {fmt_ts_cst(group['last_ts'])} · 币安公开带单实时监控 · 仅供数据跟踪，不构成投资建议"
        },
        "timestamp": datetime.fromtimestamp(group["last_ts"] / 1000.0, tz=timezone.utc).isoformat()
        if group["last_ts"] > 0
        else datetime.now(timezone.utc).isoformat(),
    }
    avatar = str(detail.get("avatarUrl") or "").strip()
    if avatar.startswith("http"):
        embed["thumbnail"] = {"url": avatar}
    return embed


def build_startup_embed(
    kol_name: str,
    portfolio_id: str,
    detail: Dict[str, Any],
    recent_orders: List[Dict[str, Any]],
    active_positions: List[Dict[str, Any]],
    poll_interval: int,
) -> Dict[str, Any]:
    """首次挂载某位 KOL 时发送一张上线状态与最近成交概览卡（绝不刷历史旧单）。"""
    margin_bal = _safe_float(detail.get("marginBalance"), 0.0)
    aum = _safe_float(detail.get("aumAmount"), 0.0)
    sharpe = _safe_float(detail.get("sharpRatio"), 0.0)
    cur_cp = _safe_int(detail.get("currentCopyCount"), 0)
    max_cp = _safe_int(detail.get("maxCopyCount"), 0)
    pos_show = bool(detail.get("positionShow"))

    groups = aggregate_orders_for_alert(recent_orders, merge_window_ms=120_000)
    recent_lines = []
    for g in reversed(groups[-5:]):
        act = classify_order_action(g["side"], g["positionSide"], g["total_pnl"])
        t_str = datetime.fromtimestamp(g["last_ts"] / 1000.0, tz=CST_TZ).strftime("%m-%d %H:%M") if g["last_ts"] else "-"
        pnl_part = f" · 盈亏 **{fmt_usd(g['total_pnl'], signed=True)}**" if (act["is_close"] or abs(g["total_pnl"]) > 1e-6) else ""
        cnt_part = f"({g['fill_count']}笔)" if g["fill_count"] > 1 else ""
        recent_lines.append(
            f"{act['emoji']} `{t_str}` **{act['label']}** `{g['symbol']}`{cnt_part} @ {fmt_price(g['avg_price'])} · {fmt_usd(g['total_notional'])}{pnl_part}"
        )

    fields = [
        {"name": "带单余额", "value": f"**{fmt_usd(margin_bal)}**", "inline": True},
        {"name": "资产管理规模 (AUM)", "value": fmt_usd(aum), "inline": True},
        {"name": "跟单人数 / 夏普", "value": f"{cur_cp}/{max_cp} · Sharpe `{sharpe:.2f}`", "inline": True},
        {
            "name": "实时持仓可见性",
            "value": "公开 (`positions + order-history`)" if pos_show else "仅逐笔成交 (`order-history` 实时监控)",
            "inline": False,
        },
        {
            "name": "最近 5 笔聚合成交基线（历史已跳过，仅推送此后新单）",
            "value": "\n".join(recent_lines) if recent_lines else "暂无历史成交记录",
            "inline": False,
        },
    ]

    if active_positions:
        pos_lines = []
        for p in active_positions[:5]:
            pos_lines.append(
                f"`{p.get('symbol')}` {p.get('positionSide')} {p.get('leverage')}x · 均价 {fmt_price(_safe_float(p.get('entryPrice')))} · 浮盈 {fmt_usd(_safe_float(p.get('unrealizedProfit')), signed=True)}"
            )
        fields.append({"name": "当前公开持仓", "value": "\n".join(pos_lines), "inline": False})

    desc = str(detail.get("description") or "").strip()
    embed: Dict[str, Any] = {
        "title": f"📡 币安带单监控已上线 · {kol_name}",
        "url": BINANCE_LEAD_PAGE_URL.format(portfolio_id=portfolio_id),
        "description": f"> {desc}\n\n每 **{poll_interval} 秒**静默轮询币安公开带单记录，自动聚合短时 TWAP 拆单并实时推送开/平仓卡片。" if desc else f"每 **{poll_interval} 秒**静默轮询币安公开带单记录，自动聚合短时 TWAP 拆单并实时推送开/平仓卡片。",
        "color": 0x3B82F6,
        "fields": fields,
        "footer": {"text": "币安公开带单独立监控守护进程 · 仅供数据跟踪，不构成投资建议"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    avatar = str(detail.get("avatarUrl") or "").strip()
    if avatar.startswith("http"):
        embed["thumbnail"] = {"url": avatar}
    return embed


def post_discord_embed(
    session: requests.Session,
    webhook_url: str,
    embed: Dict[str, Any],
    username: Optional[str] = None,
    avatar_url: Optional[str] = None,
    dry_run: bool = False,
) -> bool:
    if not webhook_url or not webhook_url.startswith("http"):
        return False
    if dry_run:
        print(f"  [DRY-RUN] skip Discord post: {embed.get('title')}")
        return True

    payload: Dict[str, Any] = {"embeds": [embed]}
    if username:
        payload["username"] = username[:80]
    if avatar_url and avatar_url.startswith("http"):
        payload["avatar_url"] = avatar_url

    r = session.post(webhook_url, json=payload, timeout=12)
    if r.status_code == 429:
        retry_after = _safe_float((r.json() or {}).get("retry_after"), 2.0)
        time.sleep(min(max(retry_after, 1.0), 10.0))
        r = session.post(webhook_url, json=payload, timeout=12)
    r.raise_for_status()
    return True


def load_json_file(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_atomic(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def poll_single_kol(
    session: requests.Session,
    kol_cfg: Dict[str, Any],
    state: Dict[str, Any],
    poll_interval: int,
    summary_webhook_url: str = "",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """执行单个 KOL 的一轮轮询、增量识别、拆单聚合并推送。返回本轮统计。"""
    pid = extract_portfolio_id(kol_cfg.get("portfolio_id", ""))
    if not pid:
        return {"status": "invalid_portfolio_id", "pushed": 0}

    webhook_url = str(kol_cfg.get("webhook_url") or "").strip()
    min_notional = _safe_float(kol_cfg.get("min_notional_usd"), 0.0)
    send_startup = bool(kol_cfg.get("send_startup_card", True))

    detail = fetch_kol_detail(session, pid)
    kol_name = str(kol_cfg.get("name") or detail.get("nickname") or f"KOL-{pid[-6:]}").strip()
    avatar_url = str(detail.get("avatarUrl") or "").strip()
    orders = fetch_kol_order_history(session, pid, page_size=25)
    positions = fetch_kol_positions(session, pid) if detail.get("positionShow") else []

    kol_states = state.setdefault("kols", {})
    ks = kol_states.get(pid)

    # 首次冷启动：只记录基线游标，绝不把 20 条历史旧单当新单刷屏
    if ks is None:
        max_ts = max((_safe_int(o.get("orderUpdateTime") or o.get("orderTime"), 0) for o in orders), default=0)
        seen_fps = [make_order_fingerprint(o) for o in orders]
        kol_states[pid] = {
            "name": kol_name,
            "portfolio_id": pid,
            "last_order_ts": max_ts,
            "seen_fingerprints": seen_fps[-200:],
            "initialized_at": datetime.now(CST_TZ).isoformat(),
            "last_poll_at": datetime.now(CST_TZ).isoformat(),
            "total_alerts_pushed": 0,
        }
        startup_pushed = 0
        if send_startup and webhook_url:
            embed = build_startup_embed(kol_name, pid, detail, orders, positions, poll_interval)
            if post_discord_embed(session, webhook_url, embed, username=f"币安带单 · {kol_name}", avatar_url=avatar_url, dry_run=dry_run):
                startup_pushed = 1
        print(f"[+] 🆕 初始化 KOL 基线 [{kol_name} ({pid})]: 跳过 {len(orders)} 笔历史单, 上线卡推送={startup_pushed}")
        return {"status": "initialized", "kol": kol_name, "pushed": startup_pushed, "new_fills": 0}

    # 常规增量检测：严格过滤未见指纹且时间 >= last_order_ts - 60s（容忍同毫秒/乱序落地）
    last_ts = _safe_int(ks.get("last_order_ts"), 0)
    seen_list: List[str] = list(ks.get("seen_fingerprints") or [])
    seen_set = set(seen_list)

    new_orders: List[Dict[str, Any]] = []
    for o in orders:
        fp = make_order_fingerprint(o)
        ts = _safe_int(o.get("orderUpdateTime") or o.get("orderTime"), 0)
        if fp in seen_set:
            continue
        # 若历史已超出窗口（比如页面补齐更老的记录），严禁误当新单
        if last_ts > 0 and ts < (last_ts - 300_000):
            continue
        new_orders.append(o)
        seen_set.add(fp)
        seen_list.append(fp)

    pushed = 0
    if new_orders:
        groups = aggregate_orders_for_alert(new_orders, merge_window_ms=120_000)
        for g in groups:
            if min_notional > 0 and g["total_notional"] < min_notional:
                continue
            embed = build_trade_embed(kol_name, pid, g, detail, positions)
            sent_main = False
            if webhook_url:
                sent_main = post_discord_embed(
                    session, webhook_url, embed, username=f"币安带单 · {kol_name}", avatar_url=avatar_url, dry_run=dry_run
                )
            if summary_webhook_url and summary_webhook_url != webhook_url:
                try:
                    post_discord_embed(
                        session, summary_webhook_url, embed, username=f"币安带单 · {kol_name}", avatar_url=avatar_url, dry_run=dry_run
                    )
                except Exception as e_sum:
                    print(f"  [!] 汇总频道推送失败: {e_sum}")
            if sent_main:
                pushed += 1
            time.sleep(0.35)

        max_new_ts = max(_safe_int(o.get("orderUpdateTime") or o.get("orderTime"), 0) for o in new_orders)
        ks["last_order_ts"] = max(last_ts, max_new_ts)
        ks["seen_fingerprints"] = seen_list[-300:]
        ks["total_alerts_pushed"] = _safe_int(ks.get("total_alerts_pushed"), 0) + pushed
        print(f"[+] 🔔 [{kol_name}] 检测到 {len(new_orders)} 笔新成交 -> 聚合推送 {pushed} 张卡片")

    ks["name"] = kol_name
    ks["last_poll_at"] = datetime.now(CST_TZ).isoformat()
    ks["margin_balance_usd"] = round(_safe_float(detail.get("marginBalance"), 0.0), 2)
    return {"status": "ok", "kol": kol_name, "pushed": pushed, "new_fills": len(new_orders)}


def run_once(config_path: str, state_path: str, dry_run: bool = False) -> List[Dict[str, Any]]:
    cfg = load_json_file(config_path, {})
    kols = cfg.get("kols") or []
    poll_interval = max(10, _safe_int(cfg.get("poll_interval_seconds"), 20))
    summary_webhook = str(cfg.get("summary_webhook_url") or "").strip()

    state = load_json_file(state_path, {"schema_version": "binance-kol-v1", "kols": {}})
    session = requests.Session()
    results = []

    for kol_cfg in kols:
        if not kol_cfg.get("enabled", True):
            continue
        try:
            res = poll_single_kol(
                session=session,
                kol_cfg=kol_cfg,
                state=state,
                poll_interval=poll_interval,
                summary_webhook_url=summary_webhook,
                dry_run=dry_run,
            )
            results.append(res)
        except Exception as e:
            name = kol_cfg.get("name") or kol_cfg.get("portfolio_id")
            print(f"[!] 轮询 KOL [{name}] 异常: {e}")
            results.append({"status": "error", "kol": name, "error": str(e), "pushed": 0})

    if not dry_run:
        save_json_atomic(state_path, state)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="独立币安公开带单 KOL 实时监控与 Discord 推送守护进程")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="KOL 配置文件路径")
    parser.add_argument("--state", default=DEFAULT_STATE_PATH, help="本地去重游标状态文件路径")
    parser.add_argument("--once", action="store_true", help="仅执行单轮检查后退出")
    parser.add_argument("--dry-run", action="store_true", help="试运行模式（不实际发 Discord、不落盘状态）")
    parser.add_argument("--interval", type=int, default=0, help="覆盖轮询间隔秒数（默认读配置，最小 10s）")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        res = run_once(args.config, args.state, dry_run=args.dry_run)
        print(f"[*] 单轮检查完毕: {json.dumps(res, ensure_ascii=False)}")
        return

    print(f"🚀 启动独立币安公开带单 KOL 监控守护进程 (config={args.config}, state={args.state})")
    round_no = 0
    while not _STOP_REQUESTED:
        round_no += 1
        cfg = load_json_file(args.config, {})
        interval = max(10, args.interval if args.interval > 0 else _safe_int(cfg.get("poll_interval_seconds"), 20))
        res = run_once(args.config, args.state, dry_run=args.dry_run)
        if round_no == 1 or round_no % 30 == 0 or any(r.get("pushed", 0) > 0 for r in res):
            now_s = datetime.now(CST_TZ).strftime("%Y-%m-%d %H:%M:%S")
            print(f"⏱️ [{now_s} UTC+8] [第 {round_no} 轮] 心跳正常 | 监控 {len(res)} 位 KOL | 本轮推送 {sum(r.get('pushed', 0) for r in res)} 张")
        for _ in range(interval):
            if _STOP_REQUESTED:
                break
            time.sleep(1)

    print("[*] 收到退出信号，币安 KOL 监控守护进程已优雅停止。")


if __name__ == "__main__":
    main()
