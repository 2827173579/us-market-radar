# -*- coding: utf-8 -*-
"""
美股量化雷达 V5.1 — 双引擎大模型大白话解盘引擎 (Gemini 优先 + DeepSeek 自动兜底)
================================================================================
零新增重型依赖（纯 Python 标准库 urllib.request），提供：
1. 双引擎主备容灾：主力 Gemini 3.8 Flash (`gemini-3.8-flash`，带低思考档位自动兼容重试) +
   兜底 DeepSeek V4.1 Flash (`deepseek-flash`)；429/5xx/超时无缝切换，双 Key 缺失优雅降级。
2. 硬风控铁笼（`risk_lock_active`）：VIX 倒挂 / 暴雨空仓 / 抄底接刀锁 / 标的熊市惯性下，
   Prompt 写死禁抄底铁律 + 代码层后置安全网强制追加硬风控覆盖红警。
3. 方案 B 四合一打包刷新 + 25 分钟盘中节流（休市复用缓存、干跑 0 外发、零密钥泄露）。
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

try:
    from zoneinfo import ZoneInfo

    _ET_TZ = ZoneInfo("America/New_York")
    _KST_TZ = ZoneInfo("Asia/Seoul")
except Exception:
    _ET_TZ = timezone(timedelta(hours=-5))
    _KST_TZ = timezone(timedelta(hours=9))

SCRATCH_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENV_PATH = os.path.join(SCRATCH_DIR, ".env")
DEFAULT_CACHE_PATH = os.path.join(SCRATCH_DIR, "data_cache", "ai_commentary_cache.json")
DEFAULT_SNAPSHOT_PATH = os.path.join(SCRATCH_DIR, "data_cache", "latest_radar_snapshot.json")
DEFAULT_NEWS_PATH = os.path.join(SCRATCH_DIR, "data_cache", "latest_market_news.json")

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
DEFAULT_WEB_REFRESH_MINUTES = 25
DEFAULT_HTTP_TIMEOUT = 30

WEB_BATCH_TARGETS: Tuple[str, ...] = ("OVERVIEW", "QQQ", "SPY", "SOXX")
VALID_TARGETS: Tuple[str, ...] = ("OVERVIEW", "QQQ", "SPY", "SOXX", "KOSPI")
TARGET_META: Dict[str, Dict[str, str]] = {
    "OVERVIEW": {
        "tab_label": "大盘全景",
        "title": "大盘全景",
        "note": "美股整体大盘、恐慌中枢与跨市场流动性",
    },
    "QQQ": {
        "tab_label": "QQQ (纳指)",
        "title": "QQQ (纳指 100)",
        "note": "纳斯达克 100 科技核心基准",
    },
    "SPY": {
        "tab_label": "SPY / VOO (标普)",
        "title": "SPY / VOO (标普500)",
        "note": "标普 500 基准（与 VOO 走势 100% 同步）",
    },
    "SOXX": {
        "tab_label": "SOXX (半导体)",
        "title": "SOXX (半导体 ETF)",
        "note": "半导体产业链核心基准 ETF",
    },
    "KOSPI": {
        "tab_label": "KOSPI (韩国综指)",
        "title": "KOSPI (韩国综合指数)",
        "note": "韩国股市总基准（三星/SK海力士 · 交易时段 09:00–15:30 KST · 点数计价无$）",
    },
}

# 代码层后置安全网：在 risk_lock_active=True 时拦截未经否定的激进抄底话术
_AGGRESSIVE_BUY_PATTERNS = (
    "大胆抄底",
    "建议满仓",
    "直接抄底",
    "闭眼抄底",
    "满仓抄底",
    "立即抄底",
    "逢低重仓买入",
    "强烈建议抄底",
    "果断抄底",
    "放心抄底",
)
_NEGATION_PREFIXES = ("严禁", "禁止", "不要", "切忌", "切勿", "不可", "不能", "别", "勿", "而非", "不是")
RISK_OVERRIDE_BANNER = (
    "⚠️ [系统硬风控覆盖：当前处于VIX倒挂/防接刀锁定状态，严禁左侧抄底，以上仅作盘面观察参考]"
)


def _safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return default
    try:
        v = float(val)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def load_env_config(
    env_path: Optional[str] = None,
    env_override: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """安全读取 .env 与环境变量（绝不打印或外泄密钥）。"""
    cfg: Dict[str, str] = {}
    path = env_path or DEFAULT_ENV_PATH
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k:
                        cfg[k] = v
        except Exception:
            pass
    for k in (
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "GEMINI_MODEL",
        "DEEPSEEK_MODEL",
        "AI_OWNER_DISCORD_USER_IDS",
        "AI_WEB_AUTO_BRIEF_ENABLED",
        "AI_WEB_REFRESH_MINUTES",
        "RADAR_DISABLE_EXTERNAL_ALERTS",
    ):
        ev = os.environ.get(k)
        if ev is not None and str(ev).strip() != "":
            cfg[k] = str(ev).strip()
    if isinstance(env_override, dict):
        for k, v in env_override.items():
            cfg[str(k)] = "" if v is None else str(v).strip()
    return cfg


def parse_owner_discord_ids(
    env_path: Optional[str] = None,
    env_override: Optional[Dict[str, str]] = None,
) -> Set[str]:
    cfg = load_env_config(env_path=env_path, env_override=env_override)
    raw = str(cfg.get("AI_OWNER_DISCORD_USER_IDS") or "").strip()
    if not raw:
        return set()
    return {p.strip() for p in raw.split(",") if p.strip().isdigit()}


def is_discord_owner(
    user_id: Any,
    env_path: Optional[str] = None,
    env_override: Optional[Dict[str, str]] = None,
) -> bool:
    """严格白名单校验：未配置白名单或 ID 不在白名单内一律返回 False。"""
    owners = parse_owner_discord_ids(env_path=env_path, env_override=env_override)
    if not owners:
        return False
    uid = str(user_id or "").strip()
    return bool(uid and uid in owners)


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    target = os.path.abspath(path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{target}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
    os.replace(tmp_path, target)


def load_ai_commentary_cache(cache_path: str = DEFAULT_CACHE_PATH) -> Dict[str, Any]:
    if not cache_path or not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _load_recent_macro_flashes(news_path: str = DEFAULT_NEWS_PATH, limit: int = 3) -> List[str]:
    if not news_path or not os.path.exists(news_path):
        return []
    try:
        with open(news_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        flashes = doc.get("macro_flashes") if isinstance(doc, dict) else []
        out: List[str] = []
        for item in (flashes or [])[:limit]:
            if isinstance(item, dict):
                title = str(item.get("title") or "").strip()
                if title:
                    out.append(title[:120])
        return out
    except Exception:
        return []


def evaluate_risk_lock(
    snapshot: Optional[Dict[str, Any]],
    target: str = "OVERVIEW",
) -> Tuple[bool, List[str]]:
    """代码级硬风控一票否决判定。"""
    snap = snapshot if isinstance(snapshot, dict) else {}
    vr = snap.get("vix_regime") if isinstance(snap.get("vix_regime"), dict) else {}
    mt = snap.get("market_temperature") if isinstance(snap.get("market_temperature"), dict) else {}
    tickers = snap.get("tickers") if isinstance(snap.get("tickers"), dict) else {}

    reasons: List[str] = []
    ratio = _safe_float(vr.get("vix_term_ratio"))
    if ratio is not None and ratio >= 1.00:
        reasons.append(f"VIX期限倒挂(VIX/VIX3M={ratio:.2f}>=1.00)")
    weather = str(snap.get("weather") or "")
    if "暴雨" in weather:
        reasons.append(f"宏观天气处于{weather}")
    if bool(mt.get("knife_catch_locked", False)):
        reasons.append("大盘抄底硬接刀锁生效(<=50)")

    tk = str(target or "OVERVIEW").upper()
    if tk in ("QQQ", "SPY", "SOXX"):
        t_item = tickers.get(tk) if isinstance(tickers.get(tk), dict) else {}
        if bool(t_item.get("bear_inertia_active", False)) or str(t_item.get("v46_regime_mode") or "") == "BEAR_INERTIA_C5":
            reasons.append(f"{tk}处于熊市惯性模式(BEAR_INERTIA_C5)")
    return (len(reasons) > 0), reasons


def extract_hard_context(
    snapshot: Optional[Dict[str, Any]],
    news_flashes: Optional[List[str]] = None,
    target: str = "OVERVIEW",
) -> Dict[str, Any]:
    """从 latest_radar_snapshot.json 提取结构化硬数据，严格杜绝喂入垃圾或秘密字段。"""
    snap = snapshot if isinstance(snapshot, dict) else {}
    vr = snap.get("vix_regime") if isinstance(snap.get("vix_regime"), dict) else {}
    liq = snap.get("cross_market_liquidity") if isinstance(snap.get("cross_market_liquidity"), dict) else {}
    opx = snap.get("opex_shield") if isinstance(snap.get("opex_shield"), dict) else {}
    mt = snap.get("market_temperature") if isinstance(snap.get("market_temperature"), dict) else {}
    futs = snap.get("futures") if isinstance(snap.get("futures"), dict) else (mt.get("futures") if isinstance(mt.get("futures"), dict) else {})
    tickers = snap.get("tickers") if isinstance(snap.get("tickers"), dict) else {}

    def _pick_future(sym: str) -> Dict[str, Any]:
        f = futs.get(sym) if isinstance(futs.get(sym), dict) else {}
        return {
            "last": f.get("last"),
            "pct_chg": f.get("pct_chg"),
            "vwap_24h": f.get("vwap_24h"),
            "vwap_diff_pct": f.get("vwap_diff_pct"),
            "status_tag": f.get("status_tag"),
        }

    def _pick_ticker(tk: str) -> Dict[str, Any]:
        t = tickers.get(tk) if isinstance(tickers.get(tk), dict) else {}
        ext_px = t.get("ext_price") if t.get("ext_price") is not None else t.get("extended_price")
        ext_chg = t.get("ext_change_pct") if t.get("ext_change_pct") is not None else t.get("ext_pct")
        ext_lbl = t.get("ext_label") or t.get("session_tag") or ""
        rth_pct = t.get("pct_chg") if t.get("pct_chg") is not None else t.get("pct_change")
        return {
            "symbol": tk,
            "display_name": TARGET_META.get(tk, {}).get("title", tk),
            "last": t.get("last") if t.get("last") is not None else t.get("price"),
            "pct_chg": rth_pct,
            "ext_price": ext_px,
            "extended_price": ext_px,
            "ext_change_pct": ext_chg,
            "ext_label": ext_lbl,
            "vwap": t.get("vwap") if t.get("vwap") is not None else t.get("vwap_rth"),
            "vwap_label": t.get("vwap_label") or "",
            "vwap_diff_pct": t.get("vwap_diff_pct"),
            "rsi_5m": t.get("rsi_5m"),
            "s1": t.get("anchor_s1") if t.get("anchor_s1") is not None else t.get("s1"),
            "s2": t.get("anchor_s2") if t.get("anchor_s2") is not None else t.get("s2"),
            "r1": t.get("anchor_r1") if t.get("anchor_r1") is not None else t.get("r1"),
            "r2": t.get("anchor_r2") if t.get("anchor_r2") is not None else t.get("r2"),
            "stop_loss": t.get("stop_loss") if t.get("stop_loss") is not None else t.get("sl"),
            "sr_breach_state": t.get("sr_breach_state"),
            "v46_regime_mode": t.get("v46_regime_mode"),
            "bear_inertia_active": bool(t.get("bear_inertia_active", False)),
            "p15_t5_pct": t.get("p15_t5_pct"),
            "ai_long_wr": t.get("ai_long_wr") if t.get("ai_long_wr") is not None else t.get("ai_long_win_rate_pct"),
            "ai_short_wr": t.get("ai_short_wr") if t.get("ai_short_wr") is not None else t.get("ai_short_win_rate_pct"),
            "decision_title": t.get("decision_title") or t.get("stance_title"),
        }

    locked, reasons = evaluate_risk_lock(snap, target=target)
    sess_code = str(snap.get("session_code") or "").strip().upper()
    sess_label = str(snap.get("session_name") or snap.get("session_label") or sess_code)
    ctx: Dict[str, Any] = {
        "updated_at_et": snap.get("updated_at_et"),
        "session_code": sess_code,
        "session_label": sess_label,
        "session_name": sess_label,
        "weather": snap.get("weather"),
        "market_regime": snap.get("market_regime"),
        "risk_lock_active": locked,
        "risk_lock_reasons": reasons,
        "vix": {
            "vix_spot": vr.get("vix_spot"),
            "vix3m_spot": vr.get("vix3m_spot"),
            "vix_term_ratio": vr.get("vix_term_ratio"),
            "vxn_spot": vr.get("vxn_spot"),
            "vix_state": vr.get("vix_state"),
        },
        "futures": {
            "NQ=F": _pick_future("NQ=F"),
            "ES=F": _pick_future("ES=F"),
        },
        "temperature": {
            "source_tag": mt.get("source_tag"),
            "macro_bubble_score": mt.get("macro_bubble_score"),
            "macro_bubble_tier": mt.get("macro_bubble_tier"),
            "intraday_bubble_score": mt.get("intraday_bubble_score"),
            "intraday_bubble_tier": mt.get("intraday_bubble_tier"),
            "macro_bottom_score": mt.get("macro_bottom_score"),
            "macro_bottom_tier": mt.get("macro_bottom_tier"),
            "knife_catch_locked": bool(mt.get("knife_catch_locked", False)),
            "intraday_bottom_score": mt.get("intraday_bottom_score"),
            "intraday_bottom_tier": mt.get("intraday_bottom_tier"),
            "intraday_knife_catch_locked": bool(mt.get("intraday_knife_catch_locked", False)),
        },
        "liquidity": {
            "hyg_ief_ratio": liq.get("hyg_ief_ratio"),
            "hyg_ief_5d_pct": liq.get("hyg_ief_5d_pct"),
            "dxy_spot": liq.get("dxy_spot"),
            "dxy_5d_pct": liq.get("dxy_5d_pct"),
            "usdjpy_spot": liq.get("usdjpy_spot"),
            "usdjpy_5d_pct": liq.get("usdjpy_5d_pct"),
            "stress_level": liq.get("stress_level"),
        },
        "opex_shield": {
            "active": bool(opx.get("active", False)),
            "next_opex_date": opx.get("next_opex_date"),
            "days_to_opex": opx.get("days_to_opex"),
        },
        "macro_flashes": list(news_flashes or [])[:3],
    }
    tk = str(target or "OVERVIEW").upper()
    if tk == "OVERVIEW":
        ctx["core_etfs"] = {k: _pick_ticker(k) for k in ("QQQ", "SPY", "SOXX")}
    elif tk in ("QQQ", "SPY", "SOXX", "KOSPI"):
        t_obj = _pick_ticker(tk)
        if tk == "KOSPI":
            t_obj["price_unit"] = "pts"
            t_obj["is_index"] = True
            t_obj["krx_session_note"] = "韩国交易所 KRX (09:00–15:30 KST)，所有点位为指数点数 pt，严禁加美元符号 $"
            if isinstance(tickers.get("KORU"), dict):
                t_obj["us_etf_proxy_koru"] = _pick_ticker("KORU")
        ctx["target_etf"] = t_obj
    return ctx


def build_system_prompt(
    risk_lock_active: bool,
    batch_mode: bool = False,
    session_code: str = "",
) -> str:
    """构建带四条铁律（含盘前/盘后实时价与夜盘防误判铁律）的系统提示词。"""
    lock_clause = (
        "当前 risk_lock_active=True（硬风控锁已生效）：严禁给出任何“抄底/逢低买入/左侧做多/加仓博反弹”建议！"
        "你只能用大白话讲清当前为什么危险，并明确指出必须至少收回输入 JSON 中的哪一条关键点位（如 VWAP 或 R1）才算解除警报。"
        if risk_lock_active
        else "当前 risk_lock_active=False：按客观强弱与关键点位给出克制、不盲目追高的交易剧本。"
    )
    sess = str(session_code or "").strip().upper()
    if sess in ("OVERNIGHT", "WEEKEND"):
        session_clause = (
            "当前为美股夜盘（20:00~04:00 ET）或周末休市：免费接口无个股/ETF 夜盘实时成交价，"
            "输入 JSON 中 ETF 的 last 仅为上日收盘价、ext_price 仅为盘后定格价，严禁当成此刻盘中正在下跌或正在交易！"
            "夜盘/休市解盘必须以实时 NQ=F / ES=F 期货走势结合上日现货结构位（VWAP / S1 / R1）推导下一交易日开盘剧本。"
        )
    elif sess in ("PRE_MARKET", "POST_MARKET"):
        session_clause = (
            "当前为美股盘前（04:00~09:30 ET）或盘后（16:00~20:00 ET）：若输入 JSON 存在有效 ext_price / extended_price，"
            "必须优先以盘前/盘后实时价作为最新价对照 VWAP / S1 / R1 分析，并明确标注“盘前价 $X（较昨收 +Y%）”或“盘后价 $X（较收盘 +Y%）”，"
            "严禁拿昨日收盘价冒充当前最新价！"
        )
    else:
        session_clause = (
            "若处于美股盘前/盘后且存在有效 ext_price，须优先使用盘前/盘后价并明确标注；"
            "若处于美股夜盘/休市，须注明当前 ETF 价为上日收盘价/盘后定格价，以实时 NQ=F / ES=F 期货结合上日结构位推导开盘剧本。"
        )
    fmt_clause = (
        "请严格返回一个合法 JSON 对象，键名必须且仅包含 \"OVERVIEW\"、\"QQQ\"、\"SPY\"、\"SOXX\" 四个字符串字段；"
        "不要输出任何 Markdown 代码块以外的多余前后缀。其中 SPY 的文案开头或正文需自然提及“SPY / VOO（走势100%同步）”。"
        if batch_mode
        else "直接输出纯中文大白话三段剧本，不要输出多余寒暄。"
    )
    return (
        "你是美股量化雷达的「AI 首席策略师」，只对资深个人交易者用大白话讲透盘面。\n"
        "必须严格遵守以下四条铁律：\n"
        "1. 【零编造】所有价格、点位、百分比与指标数值必须 100% 源自输入 JSON，严禁自行编造任何点位或数据。\n"
        f"2. 【风控铁笼】{lock_clause}\n"
        f"3. 【时段与盘前/盘后口径】{session_clause}\n"
        "4. 【大白话三段式】每份解盘严格控制在 200~320 字，禁止研报套话，必须包含以下三段：\n"
        "【一句话定性】...\n"
        "【关键多空分水岭】...\n"
        "【开盘/盘中条件剧本】如果……就……；否则……（强制使用双边条件句）\n"
        f"{fmt_clause}"
    )


def apply_post_risk_safety_net(text: str, risk_lock_active: bool) -> str:
    """代码层后置复核：若 risk_lock_active=True 且命中未经否定的激进抄底词，强制追加红警覆盖。"""
    cleaned = str(text or "").strip()
    if not risk_lock_active or not cleaned:
        return cleaned
    if RISK_OVERRIDE_BANNER in cleaned:
        return cleaned
    triggered = False
    for pat in _AGGRESSIVE_BUY_PATTERNS:
        for m in re.finditer(re.escape(pat), cleaned):
            start = m.start()
            prefix = cleaned[max(0, start - 6) : start]
            if not any(neg in prefix for neg in _NEGATION_PREFIXES):
                triggered = True
                break
        if triggered:
            break
    if triggered:
        return f"{cleaned}\n\n{RISK_OVERRIDE_BANNER}"
    return cleaned


def _default_http_post(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: int = DEFAULT_HTTP_TIMEOUT,
) -> Tuple[int, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url=url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            raw = resp.read().decode("utf-8", errors="replace")
            return status, raw
    except urllib.error.HTTPError as he:
        err_body = ""
        try:
            err_body = he.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return int(he.code), err_body


def _call_gemini_once(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    http_post_fn: Optional[Callable[..., Tuple[int, str]]] = None,
) -> Tuple[bool, str, str]:
    post_fn = http_post_fn or _default_http_post
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    base_payload: Dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }

    for attempt in range(2):
        payload = base_payload
        if attempt == 1:
            payload = {
                "systemInstruction": base_payload["systemInstruction"],
                "contents": base_payload["contents"],
                "generationConfig": {"temperature": 0.3},
            }
        try:
            status, raw = post_fn(url, headers, payload, timeout)
        except Exception as exc:
            return False, "", f"timeout/net:{type(exc).__name__}"

        if status == 200:
            try:
                doc = json.loads(raw)
                cands = doc.get("candidates") or []
                parts = (((cands[0] or {}).get("content") or {}).get("parts") or []) if cands else []
                text = "\n".join(str(p.get("text") or "") for p in parts if isinstance(p, dict) and p.get("text")).strip()
                if text:
                    return True, text, ""
                return False, "", "empty_candidates"
            except Exception:
                return False, "", "invalid_json"

        # 若第 1 次因 thinkingConfig 参数不兼容返回 400，则自动剥离 thinkingConfig 重试一次
        if attempt == 0 and status == 400 and ("thinking" in raw.lower() or "unknown name" in raw.lower() or "invalid" in raw.lower()):
            continue
        return False, "", f"http_{status}"
    return False, "", "gemini_failed"


def _call_deepseek_once(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    http_post_fn: Optional[Callable[..., Tuple[int, str]]] = None,
) -> Tuple[bool, str, str]:
    post_fn = http_post_fn or _default_http_post
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
    }
    try:
        status, raw = post_fn(url, headers, payload, timeout)
    except Exception as exc:
        return False, "", f"timeout/net:{type(exc).__name__}"
    if status == 200:
        try:
            doc = json.loads(raw)
            choices = doc.get("choices") or []
            msg = (choices[0] or {}).get("message") if choices else {}
            text = str((msg or {}).get("content") or "").strip()
            if text:
                return True, text, ""
            return False, "", "empty_choices"
        except Exception:
            return False, "", "invalid_json"
    return False, "", f"http_{status}"


def _format_provider_label(engine: str, model: str, is_fallback: bool = False) -> str:
    if engine == "gemini":
        base = "Gemini 3.8 Flash" if model == "gemini-3.8-flash" else f"Gemini ({model})"
    else:
        base = "DeepSeek V4.1 Flash" if model == "deepseek-flash" else f"DeepSeek ({model})"
    return f"{base} (兜底)" if is_fallback else base


def _format_short_error_code(err_code: str) -> str:
    """将内部错误码转换为脱敏简短展示文案（严禁包含任何密钥或 URL）。"""
    e = str(err_code or "").lower()
    if "http_503" in e:
        return "HTTP 503 高峰繁忙"
    if "http_429" in e:
        return "HTTP 429 限流"
    if "http_404" in e:
        return "HTTP 404 模型不可用"
    if "http_403" in e or "http_401" in e:
        return "HTTP 403 权限/地区限制"
    if "http_400" in e:
        return "HTTP 400 参数错误"
    if "http_5" in e:
        return "HTTP 5xx 服务端异常"
    if "timeout" in e:
        return "请求超时"
    if "net" in e:
        return "网络异常"
    return "接口异常"


def call_llm_text(
    system_prompt: str,
    user_prompt: str,
    provider: str = "auto",
    env_path: Optional[str] = None,
    env_override: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    http_post_fn: Optional[Callable[..., Tuple[int, str]]] = None,
) -> Dict[str, Any]:
    """双引擎主备调用（仅调用 GEMINI_MODEL，失败直切 DeepSeek V4.1 Flash，严禁静默降级旧版 Gemini）。"""
    cfg = load_env_config(env_path=env_path, env_override=env_override)
    gem_key = str(cfg.get("GEMINI_API_KEY") or "").strip()
    ds_key = str(cfg.get("DEEPSEEK_API_KEY") or "").strip()
    gem_model = str(cfg.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    ds_model = str(cfg.get("DEEPSEEK_MODEL") or DEFAULT_DEEPSEEK_MODEL).strip() or DEFAULT_DEEPSEEK_MODEL
    if ds_model == "deepseek-chat":
        ds_model = DEFAULT_DEEPSEEK_MODEL

    mode = str(provider or "auto").strip().lower()
    if mode not in ("auto", "gemini", "deepseek"):
        mode = "auto"

    if mode == "auto" and not gem_key and not ds_key:
        return {"status": "UNCONFIGURED", "text": "", "provider_used": "未配置", "error": "no_api_key", "error_short": "未配置 Key"}
    if mode == "gemini" and not gem_key:
        return {"status": "UNCONFIGURED", "text": "", "provider_used": "Gemini 未配置", "error": "no_gemini_key", "error_short": "未配置 Key"}
    if mode == "deepseek" and not ds_key:
        return {"status": "UNCONFIGURED", "text": "", "provider_used": "DeepSeek 未配置", "error": "no_deepseek_key", "error_short": "未配置 Key"}

    errors: List[str] = []
    if mode in ("auto", "gemini") and gem_key:
        ok, txt, err = _call_gemini_once(
            api_key=gem_key,
            model=gem_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout=timeout,
            http_post_fn=http_post_fn,
        )
        if ok:
            return {
                "status": "OK",
                "text": txt,
                "provider_used": _format_provider_label("gemini", gem_model, is_fallback=False),
                "engine_code": "gemini",
                "model_id": gem_model,
                "fallback_used": False,
                "error": "",
                "error_short": "",
            }
        errors.append(f"gemini({gem_model}):{err}")
        if mode == "gemini":
            short_e = _format_short_error_code(err)
            gem_tag = "Gemini 3.8" if gem_model == "gemini-3.8-flash" else f"Gemini ({gem_model})"
            return {
                "status": "ERROR",
                "text": "",
                "provider_used": f"{gem_tag} 失败 ({short_e})",
                "error": ";".join(errors),
                "error_short": short_e,
            }

    if mode in ("auto", "deepseek") and ds_key:
        is_fb = (mode == "auto")
        ok, txt, err = _call_deepseek_once(
            api_key=ds_key,
            model=ds_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout=timeout,
            http_post_fn=http_post_fn,
        )
        if ok:
            return {
                "status": "OK",
                "text": txt,
                "provider_used": _format_provider_label("deepseek", ds_model, is_fallback=is_fb),
                "engine_code": "deepseek",
                "model_id": ds_model,
                "fallback_used": is_fb,
                "error": ";".join(errors),
                "error_short": "",
            }
        errors.append(f"deepseek:{err}")

    short_final = _format_short_error_code(errors[-1] if errors else "")
    return {
        "status": "ERROR",
        "text": "",
        "provider_used": f"双引擎降级 ({short_final})",
        "error": ";".join(errors),
        "error_short": short_final,
    }


def _parse_batch_json_response(raw_text: str) -> Optional[Dict[str, str]]:
    s = str(raw_text or "").strip()
    if not s:
        return None
    # 剥离 ```json ... ``` 包裹
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, flags=re.DOTALL | re.IGNORECASE)
    if m:
        s = m.group(1).strip()
    elif "{" in s and "}" in s:
        s = s[s.find("{") : s.rfind("}") + 1]
    try:
        obj = json.loads(s)
        if not isinstance(obj, dict):
            return None
        out: Dict[str, str] = {}
        for k in VALID_TARGETS:
            val = obj.get(k)
            if isinstance(val, str) and val.strip():
                out[k] = val.strip()
        return out if out else None
    except Exception:
        return None


def generate_single_brief(
    target: str,
    snapshot: Optional[Dict[str, Any]],
    provider: str = "auto",
    news_flashes: Optional[List[str]] = None,
    env_path: Optional[str] = None,
    env_override: Optional[Dict[str, str]] = None,
    http_post_fn: Optional[Callable[..., Tuple[int, str]]] = None,
    cache_path: Optional[str] = DEFAULT_CACHE_PATH,
    write_back_cache: bool = True,
    now_dt: Optional[datetime] = None,
) -> Dict[str, Any]:
    """为单个视图 (OVERVIEW / QQQ / SPY / SOXX) 实时生成解盘并可选回写缓存。"""
    tk = str(target or "OVERVIEW").strip().upper()
    if tk not in VALID_TARGETS:
        tk = "OVERVIEW"
    ctx = extract_hard_context(snapshot, news_flashes=news_flashes, target=tk)
    risk_locked = bool(ctx.get("risk_lock_active", False))
    sys_p = build_system_prompt(risk_lock_active=risk_locked, batch_mode=False, session_code=str(ctx.get("session_code") or ""))
    extra_note = (
        "（注意：请明确写出 SPY / VOO 走势 100% 同步）"
        if tk == "SPY"
        else (
            "（注意：KOSPI 为韩国综合指数，交易时段 09:00–15:30 KST，所有点位与支撑阻力均为指数点数，绝对禁止加美元符号 $！）"
            if tk == "KOSPI"
            else ""
        )
    )
    user_p = (
        f"请基于以下实盘量化硬数据，为【{TARGET_META[tk]['title']}】{extra_note}输出三段式大白话解盘：\n"
        f"{json.dumps(ctx, ensure_ascii=False)}"
    )
    res = call_llm_text(
        system_prompt=sys_p,
        user_prompt=user_p,
        provider=provider,
        env_path=env_path,
        env_override=env_override,
        http_post_fn=http_post_fn,
    )
    now_et = (now_dt or datetime.now(_ET_TZ)).astimezone(_ET_TZ)
    now_kst = now_et.astimezone(_KST_TZ)
    if res.get("status") != "OK":
        return {
            "status": res.get("status", "ERROR"),
            "target": tk,
            "text": "",
            "provider_used": res.get("provider_used", ""),
            "risk_lock_active": risk_locked,
            "risk_lock_reasons": ctx.get("risk_lock_reasons", []),
            "updated_at_et": now_et.strftime("%Y-%m-%d %H:%M ET"),
            "updated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M KST"),
            "error": res.get("error", ""),
            "error_short": res.get("error_short", ""),
        }

    safe_text = apply_post_risk_safety_net(res.get("text", ""), risk_lock_active=risk_locked)
    if tk == "KOSPI" and "$" in safe_text:
        safe_text = re.sub(r"\$([0-9,]+(?:\.[0-9]+)?)", r"\1", safe_text)
    item_payload = {
        "status": "OK",
        "target": tk,
        "text": safe_text,
        "provider_used": res.get("provider_used", ""),
        "risk_lock_active": risk_locked,
        "risk_lock_reasons": ctx.get("risk_lock_reasons", []),
        "updated_at_et": now_et.strftime("%Y-%m-%d %H:%M ET"),
        "updated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M KST"),
        "fallback_used": bool(res.get("fallback_used", False)),
    }

    if write_back_cache and cache_path:
        try:
            existing = load_ai_commentary_cache(cache_path)
            items = existing.get("items") if isinstance(existing.get("items"), dict) else {}
            items[tk] = safe_text
            existing.update(
                {
                    "schema_version": "v5.1-ai-commentary",
                    "status": "OK",
                    "updated_at_ts": now_et.timestamp(),
                    "updated_at_et": item_payload["updated_at_et"],
                    "updated_at_kst": item_payload["updated_at_kst"],
                    "provider_used": item_payload["provider_used"],
                    "risk_lock_active": risk_locked,
                    "risk_lock_reasons": ctx.get("risk_lock_reasons", []),
                    "session_label": str((snapshot or {}).get("session_name") or (snapshot or {}).get("session_code") or ""),
                    "items": items,
                }
            )
            _atomic_write_json(cache_path, existing)
        except Exception:
            pass
    return item_payload


def generate_all_four_briefs(
    snapshot: Optional[Dict[str, Any]],
    provider: str = "auto",
    news_flashes: Optional[List[str]] = None,
    env_path: Optional[str] = None,
    env_override: Optional[Dict[str, str]] = None,
    http_post_fn: Optional[Callable[..., Tuple[int, str]]] = None,
    cache_path: Optional[str] = DEFAULT_CACHE_PATH,
    now_dt: Optional[datetime] = None,
) -> Dict[str, Any]:
    """四合一打包刷新（优先 1 次 JSON 请求生成 4 份解盘，解析失败自动降级单条补齐）。"""
    ctx = extract_hard_context(snapshot, news_flashes=news_flashes, target="OVERVIEW")
    # 若任一核心 ETF 处于熊市惯性，也纳入对应标的的独立风险锁
    macro_locked = bool(ctx.get("risk_lock_active", False))
    sys_p = build_system_prompt(risk_lock_active=macro_locked, batch_mode=True, session_code=str(ctx.get("session_code") or ""))
    user_p = (
        "请基于以下实盘量化硬数据，一次性以 JSON 对象输出 4 个键（OVERVIEW、QQQ、SPY、SOXX）的三段式大白话解盘：\n"
        f"{json.dumps(ctx, ensure_ascii=False)}"
    )
    res = call_llm_text(
        system_prompt=sys_p,
        user_prompt=user_p,
        provider=provider,
        env_path=env_path,
        env_override=env_override,
        http_post_fn=http_post_fn,
    )
    now_et = (now_dt or datetime.now(_ET_TZ)).astimezone(_ET_TZ)
    now_kst = now_et.astimezone(_KST_TZ)

    if res.get("status") != "OK":
        return {
            "schema_version": "v5.1-ai-commentary",
            "status": res.get("status", "ERROR"),
            "updated_at_ts": time.time(),
            "updated_at_et": now_et.strftime("%Y-%m-%d %H:%M ET"),
            "updated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M KST"),
            "provider_used": res.get("provider_used", ""),
            "risk_lock_active": macro_locked,
            "risk_lock_reasons": ctx.get("risk_lock_reasons", []),
            "session_label": str((snapshot or {}).get("session_name") or (snapshot or {}).get("session_code") or ""),
            "items": {},
            "error": res.get("error", ""),
        }

    parsed = _parse_batch_json_response(res.get("text", "")) or {}
    items_out: Dict[str, str] = {}
    provider_used = res.get("provider_used", "")

    for tk in WEB_BATCH_TARGETS:
        tk_locked, _ = evaluate_risk_lock(snapshot, target=tk)
        if tk in parsed and parsed[tk]:
            items_out[tk] = apply_post_risk_safety_net(parsed[tk], risk_lock_active=tk_locked)
        else:
            # 降级单条补齐缺失项
            single = generate_single_brief(
                target=tk,
                snapshot=snapshot,
                provider=provider,
                news_flashes=news_flashes,
                env_path=env_path,
                env_override=env_override,
                http_post_fn=http_post_fn,
                cache_path=None,
                write_back_cache=False,
                now_dt=now_et,
            )
            if single.get("status") == "OK" and single.get("text"):
                items_out[tk] = single["text"]
                if not provider_used:
                    provider_used = single.get("provider_used", "")

    doc = {
        "schema_version": "v5.1-ai-commentary",
        "status": "OK" if items_out else "ERROR",
        "updated_at_ts": now_et.timestamp(),
        "updated_at_et": now_et.strftime("%Y-%m-%d %H:%M ET"),
        "updated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M KST"),
        "provider_used": provider_used,
        "risk_lock_active": macro_locked,
        "risk_lock_reasons": ctx.get("risk_lock_reasons", []),
        "session_label": str((snapshot or {}).get("session_name") or (snapshot or {}).get("session_code") or ""),
        "items": items_out,
    }
    if cache_path and items_out:
        try:
            _atomic_write_json(cache_path, doc)
        except Exception:
            pass
    return doc


def _is_in_web_auto_window(now_et: datetime) -> bool:
    """美东周一至周五 08:30 ~ 16:15 盘前及正规交易时段。"""
    if now_et.weekday() >= 5:
        return False
    mins = now_et.hour * 60 + now_et.minute
    return (8 * 60 + 30) <= mins <= (16 * 60 + 15)


def maybe_refresh_web_brief(
    snapshot: Optional[Dict[str, Any]],
    cache_path: str = DEFAULT_CACHE_PATH,
    news_path: str = DEFAULT_NEWS_PATH,
    env_path: Optional[str] = None,
    env_override: Optional[Dict[str, str]] = None,
    now_dt: Optional[datetime] = None,
    force_refresh: bool = False,
    allow_cold_start: bool = True,
    http_post_fn: Optional[Callable[..., Tuple[int, str]]] = None,
) -> Dict[str, Any]:
    """
    方案 B 智能节流调度：
    - RADAR_DISABLE_EXTERNAL_ALERTS=1 干跑下严格 0 外部 LLM 调用；
    - AI_WEB_AUTO_BRIEF_ENABLED=0 时关闭自动刷新，直接返回已有缓存；
    - 美东周一至周五 08:30~16:15 按 AI_WEB_REFRESH_MINUTES (默认 25 分钟) 打包刷新；
    - 休市/周末已有有效缓存则 0 消耗直接复用；若缓存为空且 allow_cold_start=True 则补刷 1 次收盘剧本。
    """
    cfg = load_env_config(env_path=env_path, env_override=env_override)
    existing = load_ai_commentary_cache(cache_path)
    has_valid_items = bool(isinstance(existing.get("items"), dict) and len(existing.get("items") or {}) >= 1)
    now_et = (now_dt or datetime.now(_ET_TZ)).astimezone(_ET_TZ)
    now_ts = now_et.timestamp()

    # 干跑保护：严禁真实外发 LLM 请求
    if str(cfg.get("RADAR_DISABLE_EXTERNAL_ALERTS") or "").strip().lower() in ("1", "true", "yes", "on"):
        if has_valid_items:
            existing["from_cache"] = True
            existing["dry_run_skipped"] = True
            return existing
        locked, reasons = evaluate_risk_lock(snapshot, target="OVERVIEW")
        return {
            "schema_version": "v5.1-ai-commentary",
            "status": "DRY_RUN_SKIPPED",
            "updated_at_et": now_et.strftime("%Y-%m-%d %H:%M ET"),
            "updated_at_kst": now_et.astimezone(_KST_TZ).strftime("%Y-%m-%d %H:%M KST"),
            "provider_used": "干跑跳过外部 LLM",
            "risk_lock_active": locked,
            "risk_lock_reasons": reasons,
            "session_label": str((snapshot or {}).get("session_name") or ""),
            "items": {},
            "dry_run_skipped": True,
        }

    enabled = str(cfg.get("AI_WEB_AUTO_BRIEF_ENABLED", "1")).strip().lower() not in ("0", "false", "no", "off")
    if not enabled and not force_refresh:
        if has_valid_items:
            existing["from_cache"] = True
            return existing
        locked, reasons = evaluate_risk_lock(snapshot, target="OVERVIEW")
        return {
            "schema_version": "v5.1-ai-commentary",
            "status": "DISABLED",
            "updated_at_et": now_et.strftime("%Y-%m-%d %H:%M ET"),
            "updated_at_kst": now_et.astimezone(_KST_TZ).strftime("%Y-%m-%d %H:%M KST"),
            "provider_used": "网页自动解盘已关闭",
            "risk_lock_active": locked,
            "risk_lock_reasons": reasons,
            "session_label": str((snapshot or {}).get("session_name") or ""),
            "items": {},
        }

    try:
        refresh_mins = max(5, int(float(cfg.get("AI_WEB_REFRESH_MINUTES") or DEFAULT_WEB_REFRESH_MINUTES)))
    except Exception:
        refresh_mins = DEFAULT_WEB_REFRESH_MINUTES

    last_ts = _safe_float(existing.get("updated_at_ts"), 0.0) or 0.0
    age_sec = max(0.0, now_ts - last_ts) if last_ts > 0 else float("inf")
    in_window = _is_in_web_auto_window(now_et)

    should_refresh = False
    if force_refresh:
        should_refresh = True
    elif in_window:
        should_refresh = (not has_valid_items) or (age_sec >= refresh_mins * 60.0)
    else:
        # 休市/周末：有缓存直接复用（0 消耗）；缓存为空时允许冷启动补刷 1 次
        should_refresh = (not has_valid_items) and bool(allow_cold_start)

    if not should_refresh:
        if has_valid_items:
            existing["from_cache"] = True
            return existing
        locked, reasons = evaluate_risk_lock(snapshot, target="OVERVIEW")
        return {
            "schema_version": "v5.1-ai-commentary",
            "status": "OFF_HOURS_IDLE",
            "updated_at_et": now_et.strftime("%Y-%m-%d %H:%M ET"),
            "updated_at_kst": now_et.astimezone(_KST_TZ).strftime("%Y-%m-%d %H:%M KST"),
            "provider_used": "休市待机",
            "risk_lock_active": locked,
            "risk_lock_reasons": reasons,
            "session_label": str((snapshot or {}).get("session_name") or ""),
            "items": {},
        }

    flashes = _load_recent_macro_flashes(news_path=news_path, limit=3)
    fresh = generate_all_four_briefs(
        snapshot=snapshot,
        provider="auto",
        news_flashes=flashes,
        env_path=env_path,
        env_override=env_override,
        http_post_fn=http_post_fn,
        cache_path=cache_path,
        now_dt=now_et,
    )
    if fresh.get("status") == "OK":
        fresh["from_cache"] = False
        return fresh
    if has_valid_items:
        existing["from_cache"] = True
        return existing
    return fresh


def sanitize_ai_commentary_for_web(ai_cache: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """严格白名单投影：仅输出纯文字剧本与展示元数据，绝对禁止任何密钥或配置渗入静态 HTML。"""
    doc = ai_cache if isinstance(ai_cache, dict) else {}
    raw_items = doc.get("items") if isinstance(doc.get("items"), dict) else {}
    clean_items = {
        tk: str(raw_items.get(tk) or "").strip()
        for tk in VALID_TARGETS
        if isinstance(raw_items.get(tk), str) and raw_items.get(tk).strip()
    }
    return {
        "status": str(doc.get("status") or ("OK" if clean_items else "UNCONFIGURED")),
        "updated_at_et": str(doc.get("updated_at_et") or ""),
        "updated_at_kst": str(doc.get("updated_at_kst") or ""),
        "provider_used": str(doc.get("provider_used") or "待首次刷新"),
        "risk_lock_active": bool(doc.get("risk_lock_active", False)),
        "risk_lock_reasons": [str(x) for x in (doc.get("risk_lock_reasons") or [])[:4]],
        "session_label": str(doc.get("session_label") or ""),
        "items": clean_items,
    }


def render_macro_ai_commentary_html(ai_cache: Optional[Dict[str, Any]]) -> str:
    """渲染 macro_radar.html 【🧠 AI 首席策略师 · 大白话盘中剧本】4-Tab 卡片（深浅双色自适应，零密钥）。"""
    safe = sanitize_ai_commentary_for_web(ai_cache)
    items = safe["items"]
    risk_locked = safe["risk_lock_active"]
    provider_used = html.escape(safe["provider_used"] or "待刷新")
    upd_et = html.escape(safe["updated_at_et"] or "--")
    upd_kst = html.escape(safe["updated_at_kst"] or "")
    ts_display = f"{upd_et}" + (f" / {upd_kst}" if upd_kst else "")

    lock_badge = (
        '<span class="v51-lock-pill">⛔ 硬风控锁生效中</span>'
        if risk_locked
        else '<span class="v51-safe-pill">🛡️ 硬风控监控正常</span>'
    )

    tabs_html: List[str] = []
    panels_html: List[str] = []
    for idx, tk in enumerate(WEB_BATCH_TARGETS):
        meta = TARGET_META[tk]
        active_cls = " active" if idx == 0 else ""
        tabs_html.append(
            f'<button type="button" class="v51-ai-tab{active_cls}" data-ai-tab="{tk}" '
            f'onclick="switchV51AiTab(\'{tk}\')">{html.escape(meta["tab_label"])}</button>'
        )
        body_txt = items.get(tk, "")
        if body_txt:
            rendered_body = html.escape(body_txt).replace("\n", "<br>")
        else:
            rendered_body = (
                f"当前尚未生成【{html.escape(meta['title'])}】最新大白话剧本。"
                "美股盘前/盘中（美东 08:30–16:15）每 25 分钟自动刷新，或由管理员在 Discord 使用 <code>/ai</code> 实时触发。"
            )
        disp_style = "block" if idx == 0 else "none"
        panels_html.append(
            f'<div class="v51-ai-panel" data-ai-panel="{tk}" style="display:{disp_style};">'
            f'<div class="v51-ai-panel-note">{html.escape(meta["note"])}</div>'
            f'<div class="v51-ai-body">{rendered_body}</div>'
            f"</div>"
        )

    return f"""
    <style>
      .v51-ai-card {{
        background: var(--bg-card, rgba(22,27,38,0.88));
        border: 1px solid var(--border-subtle, rgba(148,163,184,0.18));
        border-radius: 12px;
        padding: 14px 16px;
        margin: 0 0 18px;
      }}
      .v51-ai-hdr {{
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        margin-bottom: 10px;
      }}
      .v51-ai-title {{
        font-size: 14px;
        font-weight: 700;
        color: var(--text-primary, var(--text-main, #f1f5f9));
      }}
      .v51-ai-meta {{
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 6px;
        font-size: 11px;
        color: var(--text-muted, #94a3b8);
      }}
      .v51-engine-pill {{
        padding: 2px 8px;
        border-radius: 999px;
        background: rgba(56,189,248,0.12);
        color: var(--accent-blue, var(--color-accent, #38bdf8));
        border: 1px solid rgba(56,189,248,0.28);
        font-weight: 600;
      }}
      .v51-lock-pill {{
        padding: 2px 8px;
        border-radius: 999px;
        background: rgba(239,68,68,0.16);
        color: var(--color-down, #ef4444);
        border: 1px solid rgba(239,68,68,0.38);
        font-weight: 700;
      }}
      .v51-safe-pill {{
        padding: 2px 8px;
        border-radius: 999px;
        background: rgba(16,185,129,0.14);
        color: var(--color-up, #10b981);
        border: 1px solid rgba(16,185,129,0.32);
        font-weight: 600;
      }}
      .v51-ai-tabs {{
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-bottom: 10px;
      }}
      .v51-ai-tab {{
        padding: 5px 11px;
        border-radius: 8px;
        border: 1px solid var(--border-subtle, rgba(148,163,184,0.22));
        background: var(--bg-elevated, rgba(15,23,42,0.45));
        color: var(--text-muted, #94a3b8);
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        font-family: inherit;
        transition: all 0.15s ease;
      }}
      .v51-ai-tab.active {{
        background: rgba(56,189,248,0.16);
        border-color: var(--color-accent, #38bdf8);
        color: var(--text-primary, var(--text-main, #f1f5f9));
      }}
      .v51-ai-panel {{
        background: var(--bg-elevated, rgba(15,23,42,0.45));
        border: 1px solid var(--border-subtle, rgba(148,163,184,0.16));
        border-radius: 10px;
        padding: 12px 14px;
      }}
      .v51-ai-panel-note {{
        font-size: 11px;
        color: var(--text-muted, #94a3b8);
        margin-bottom: 6px;
      }}
      .v51-ai-body {{
        font-size: 13px;
        line-height: 1.7;
        color: var(--text-primary, var(--text-main, #e2e8f0));
        word-break: break-word;
      }}
    </style>
    <section id="v51AiCommentaryCard" class="v51-ai-card">
      <div class="v51-ai-hdr">
        <div class="v51-ai-title">🧠 【AI 首席策略师 · 大白话盘中剧本】</div>
        <div class="v51-ai-meta">
          {lock_badge}
          <span class="v51-engine-pill">引擎：{provider_used}</span>
          <span>更新：{ts_display}</span>
        </div>
      </div>
      <div class="v51-ai-tabs" role="tablist" aria-label="AI 首席策略师解盘视角">
        {''.join(tabs_html)}
      </div>
      {''.join(panels_html)}
    </section>
    <script>
      function switchV51AiTab(targetKey) {{
        var card = document.getElementById("v51AiCommentaryCard");
        if (!card) return;
        card.querySelectorAll(".v51-ai-tab").forEach(function(btn) {{
          btn.classList.toggle("active", btn.getAttribute("data-ai-tab") === targetKey);
        }});
        card.querySelectorAll(".v51-ai-panel").forEach(function(p) {{
          p.style.display = (p.getAttribute("data-ai-panel") === targetKey) ? "block" : "none";
        }});
      }}
    </script>
    """
