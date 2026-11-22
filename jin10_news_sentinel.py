#!/usr/bin/env python3
"""
jin10_news_sentinel.py - 轻量级金十数据宏观快讯与财经日历哨兵模块

特性：
1. 请求金十 7x24 快讯流 (flash-api.jin10.com) 与当日美国重磅经济日历（支持 Jin10 CDN / 华尔街见闻 / ForexFactory / akshare 多级免 Key 回退）；
2. 所有外部请求严格限制 timeout=3 秒并捕获全部异常，遇波动或海外拦截静默降级，绝不拖慢主雷达；
3. 版权安全隔离：仅通过 Discord 私有频道推送，绝不写入任何公开 HTML 页面；
4. 仅推送标红重要快讯 (important==1) 且命中美股/宏观白名单关键词的消息；
5. 美国三星/五星级重磅数据 (CPI/核心CPI/PPI/PCE/非农NFP/FOMC利率决议) 发布前 15 分钟防插针倒计时预警 + 公布值（前值/预期值/实际公布值）瞬时战报；
6. 本地 .jin10_pushed_ids.json + shared_alert_state.py (data_cache/shared_alert_dedupe.json) 双重持久化去重锁。
"""

import html as html_lib
import json
import math
import os
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import requests

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

HTTP_TIMEOUT = 3
TICKER_NEWS_TTL_SECONDS = 1500
MAX_MACRO_FLASHES = 25

JIN10_FLASH_URL = "https://flash-api.jin10.com/get_flash_list?channel=-8200&vip=1"
JIN10_RILI_URL_TMPL = "https://cdn-rili.jin10.com/web_data/{year}/daily/{month:02d}/{day:02d}/economics.json"
WSCN_CALENDAR_URL = "https://api-one-wscn.awtmt.com/apiv1/finance/macrodatas"
FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

JIN10_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "x-app-id": "bVBF4FyRTn5NJF5n",
    "x-version": "1.0.0",
    "Referer": "https://www.jin10.com/",
    "Origin": "https://www.jin10.com",
}

WHITELIST_KEYWORDS: Tuple[str, ...] = (
    "美联储", "鲍威尔", "降息", "加息", "非农", "CPI", "PPI", "PCE",
    "关税", "芯片", "半导体", "英伟达", "台积电", "微软", "苹果",
    "特朗普", "中东", "原油", "美债", "纳斯达克", "标普",
)

# 识别当日美国重磅经济数据（顺序按细分优先级排列）
HEAVYWEIGHT_MACRO_PATTERNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("核心CPI", ("核心cpi", "core cpi")),
    ("CPI", ("cpi", "消费者物价")),
    ("核心PPI", ("核心ppi", "core ppi")),
    ("PPI", ("ppi", "生产者物价")),
    ("核心PCE", ("核心pce", "core pce")),
    ("PCE", ("pce", "个人消费支出")),
    ("非农就业(NFP)", ("非农", "non-farm", "nonfarm", "nfp")),
    ("FOMC利率决议", ("fomc", "利率决议", "联邦基金利率", "interest rate decision", "fed funds rate", "federal funds rate")),
)

US_COUNTRY_TOKENS = {"美国", "US", "USA", "USD", "UNITED STATES"}
EMPTY_ACTUAL_VALUES = {"", "--", "-", "None", "none", "null", "NULL", "nan", "NaN"}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATE_FILE = os.path.join(BASE_DIR, ".jin10_pushed_ids.json")
DEFAULT_MARKET_NEWS_CACHE = os.path.join(BASE_DIR, "data_cache", "latest_market_news.json")
TICKER_NEWS_URL_YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
FINNHUB_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"

_JIN10_LOCK = threading.Lock()

try:
    import shared_alert_state
except Exception:
    shared_alert_state = None


def load_env_file(env_path: Optional[str] = None) -> None:
    """安全加载本地 .env 文件至 os.environ（不覆盖已有环境变量）。"""
    if env_path is None:
        env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


load_env_file()


def get_jin10_webhook_url() -> str:
    """金十快讯与财经日历唯一来源：DISCORD_JIN10_WEBHOOK_URL；未配置时返回空，严禁回退串台至买点预警频道。"""
    return os.environ.get("DISCORD_JIN10_WEBHOOK_URL", "").strip()


def _get_tz(tz_name: str, fallback_hours: int):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return timezone(timedelta(hours=fallback_hours))


def _et_date_str(ts: Optional[float] = None) -> str:
    tz = _get_tz("America/New_York", -5)
    dt = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=tz)
    return dt.strftime("%Y-%m-%d")


def _shanghai_date_str(ts: Optional[float] = None) -> str:
    tz = _get_tz("Asia/Shanghai", 8)
    dt = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=tz)
    return dt.strftime("%Y-%m-%d")


def _parse_shanghai_dt(dt_str: str) -> Optional[float]:
    s = str(dt_str or "").strip()
    if not s:
        return None
    tz = _get_tz("Asia/Shanghai", 8)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s[:19], fmt).replace(tzinfo=tz)
            return dt.timestamp()
        except Exception:
            continue
    return None


def _parse_iso_dt(dt_str: str) -> Optional[float]:
    s = str(dt_str or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_get_tz("America/New_York", -5))
        return dt.timestamp()
    except Exception:
        return _parse_shanghai_dt(s)


def clean_flash_text(raw: Any) -> str:
    """去除 HTML 标签与多余空白，还原真实文本。"""
    if raw is None:
        return ""
    text = str(raw)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def _is_red_important(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    try:
        return int(val) == 1
    except Exception:
        return False


def matches_macro_whitelist(text: str) -> List[str]:
    """返回命中的美股/宏观白名单关键词列表（保留原词顺序，英文不区分大小写）。"""
    if not text:
        return []
    low = text.lower()
    hits = []
    for kw in WHITELIST_KEYWORDS:
        if kw.lower() in low:
            hits.append(kw)
    return hits


def extract_flash_payload(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """提取 type=0/2 快讯文本并严格校验 important==1、非广告且命中白名单。"""
    if not isinstance(item, dict):
        return None
    item_type = item.get("type", 0)
    if item_type == 1:
        return None
    if not _is_red_important(item.get("important")):
        return None
    extras = item.get("extras")
    if isinstance(extras, dict) and extras.get("ad") is True:
        return None

    data = item.get("data") or {}
    if not isinstance(data, dict):
        return None
    title = clean_flash_text(data.get("title", ""))
    content = clean_flash_text(data.get("content", ""))
    combined = f"{title}\n{content}".strip() if (title and title not in content) else (content or title)
    if not combined:
        return None

    hits = matches_macro_whitelist(combined)
    if not hits:
        return None

    item_id = str(item.get("id") or "").strip()
    if not item_id:
        return None

    ts_str = str(item.get("time") or "").strip()
    pub_ts = _parse_shanghai_dt(ts_str)
    return {
        "id": item_id,
        "title": title,
        "content": content,
        "text": combined,
        "time_str": ts_str,
        "pub_ts": pub_ts,
        "keywords": hits,
    }


def filter_important_flashes(
    items: List[Dict[str, Any]],
    now_ts: Optional[float] = None,
    max_age_seconds: int = 7200,
) -> List[Dict[str, Any]]:
    """过滤标红 + 白名单命中 + 时效窗口内的快讯。"""
    cur_ts = now_ts if now_ts is not None else time.time()
    out: List[Dict[str, Any]] = []
    for item in items or []:
        parsed = extract_flash_payload(item)
        if not parsed:
            continue
        if max_age_seconds > 0 and parsed["pub_ts"] is not None:
            age = cur_ts - parsed["pub_ts"]
            if age > max_age_seconds:
                continue
        out.append(parsed)
    return out


def fetch_jin10_flash_list(timeout: float = HTTP_TIMEOUT) -> List[Dict[str, Any]]:
    """抓取金十公开 7x24 快讯流（强制 timeout=3s，异常静默降级返回空列表）。"""
    try:
        resp = requests.get(JIN10_FLASH_URL, headers=JIN10_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            return []
        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _format_val_with_unit(val: Any, unit: Any = "") -> str:
    s = str(val if val is not None else "").strip()
    if s in EMPTY_ACTUAL_VALUES or s == "待公布":
        return "待公布"
    u = str(unit or "").strip()
    if u and u not in s and len(u) <= 6:
        return f"{s}{u}"
    return s


def _normalize_star(raw_star: Any = None, raw_importance: Any = None, raw_impact: Any = None) -> int:
    if raw_star is not None:
        try:
            return int(float(raw_star))
        except Exception:
            pass
    if raw_impact is not None:
        imp = str(raw_impact).strip().lower()
        if imp == "high":
            return 5
        if imp == "medium":
            return 2
        if imp == "low":
            return 1
    if raw_importance is not None:
        try:
            lvl = int(float(raw_importance))
            if lvl >= 3:
                return 5
            if lvl == 2:
                return 2
            return 1
        except Exception:
            pass
    return 0


def _canonical_macro_info(name: str) -> Tuple[Optional[str], str]:
    """判断是否为核心重磅经济指标并生成跨数据源一致的规范化后缀。"""
    raw = str(name or "").strip()
    low = raw.lower()
    matched_tag = None
    for tag, pats in HEAVYWEIGHT_MACRO_PATTERNS:
        if any(p in low for p in pats):
            matched_tag = tag
            break
    if not matched_tag:
        return None, ""

    # 排除非重磅官员闲谈类发言（仅保留 FOMC 决议/声明/纪要）
    if matched_tag == "FOMC利率决议":
        if ("speaks" in low or "讲话" in raw or "谈话" in raw) and not any(
            k in low for k in ("利率决议", "rate decision", "statement", "press conference", "minutes", "纪要")
        ):
            return None, ""

    freq = ""
    if "核心" in raw or "core" in low:
        prefix = "CORE_"
    else:
        prefix = ""
    if any(k in low for k in ("年率", "同比", "y/y", "yoy")):
        freq = "_YOY"
    elif any(k in low for k in ("月率", "环比", "m/m", "mom")):
        freq = "_MOM"
    return matched_tag, f"{prefix}{matched_tag}{freq}"


def is_heavyweight_us_macro(event: Dict[str, Any]) -> bool:
    """校验是否属于当日美国三星/五星级重磅经济数据。"""
    if not isinstance(event, dict):
        return False
    country = str(event.get("country") or "").strip().upper()
    name = str(event.get("name") or event.get("title") or "").strip()
    if country:
        if country not in US_COUNTRY_TOKENS:
            return False
    elif not name.startswith("美国"):
        return False

    star = int(event.get("star") or 0)
    if star < 3:
        return False

    tag, _ = _canonical_macro_info(name)
    return tag is not None


def canonical_event_dedupe_id(event: Dict[str, Any], stage: str) -> str:
    """构造跨数据源（Jin10/WSCN/FF/akshare）与跨设备通用的唯一幂等键。"""
    pub_ts = event.get("pub_time_ts")
    date_tag = _et_date_str(pub_ts)
    name = str(event.get("name") or "")
    _, canon_slug = _canonical_macro_info(name)
    slug = canon_slug or re.sub(r"\s+", "_", name)[:40]
    return f"JIN10_{stage}:{date_tag}:{slug}"


def parse_jin10_type1_release(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """解析金十 flash-api 中 type==1 的宏观经济指标即时公布条目。"""
    if not isinstance(item, dict) or item.get("type") != 1:
        return None
    d = item.get("data") or {}
    if not isinstance(d, dict):
        return None
    name = clean_flash_text(d.get("name") or "")
    period = clean_flash_text(d.get("time_period") or "")
    full_name = f"{period}{name}" if period and period not in name else name
    country = clean_flash_text(d.get("country") or "")
    if country == "美国" and not full_name.startswith("美国"):
        full_name = f"美国{full_name}"
    pub_ts = _parse_shanghai_dt(d.get("pub_time") or item.get("time") or "")
    return {
        "event_id": str(d.get("data_id") or item.get("id") or ""),
        "name": full_name,
        "country": country,
        "star": _normalize_star(raw_star=d.get("star")),
        "pub_time_ts": pub_ts,
        "pub_time_str": str(d.get("pub_time") or item.get("time") or ""),
        "previous": d.get("previous"),
        "forecast": d.get("consensus"),
        "actual": d.get("actual"),
        "unit": d.get("unit") or "",
        "source": "jin10_flash_type1",
    }


def _normalize_calendar_row(row: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    if source == "jin10_rili":
        name = clean_flash_text(row.get("name") or row.get("title") or "")
        period = clean_flash_text(row.get("time_period") or "")
        full_name = f"{period}{name}" if period and period not in name else name
        country = clean_flash_text(row.get("country") or "")
        if country == "美国" and not full_name.startswith("美国"):
            full_name = f"美国{full_name}"
        pub_str = str(row.get("pub_time") or row.get("time") or "")
        return {
            "event_id": str(row.get("id") or row.get("data_id") or ""),
            "name": full_name,
            "country": country,
            "star": _normalize_star(raw_star=row.get("star")),
            "pub_time_ts": _parse_shanghai_dt(pub_str),
            "pub_time_str": pub_str,
            "previous": row.get("previous"),
            "forecast": row.get("consensus") if row.get("consensus") is not None else row.get("forecast"),
            "actual": row.get("actual"),
            "unit": row.get("unit") or "",
            "source": source,
        }
    if source == "wscn":
        name = clean_flash_text(row.get("title") or "")
        country = clean_flash_text(row.get("country") or row.get("country_id") or "")
        if country == "美国" and not name.startswith("美国"):
            name = f"美国{name}"
        pub_epoch = row.get("public_date")
        pub_ts = float(pub_epoch) if pub_epoch else None
        tz_bj = _get_tz("Asia/Shanghai", 8)
        pub_str = datetime.fromtimestamp(pub_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if pub_ts else ""
        return {
            "event_id": str(row.get("id") or ""),
            "name": name,
            "country": country,
            "star": _normalize_star(raw_star=row.get("stars"), raw_importance=row.get("importance")),
            "pub_time_ts": pub_ts,
            "pub_time_str": pub_str,
            "previous": row.get("previous"),
            "forecast": row.get("forecast"),
            "actual": row.get("actual"),
            "unit": row.get("unit") or "",
            "source": source,
        }
    if source == "forexfactory":
        name = clean_flash_text(row.get("title") or "")
        country = clean_flash_text(row.get("country") or "")
        pub_str = str(row.get("date") or "")
        pub_ts = _parse_iso_dt(pub_str)
        tz_bj = _get_tz("Asia/Shanghai", 8)
        bj_str = datetime.fromtimestamp(pub_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if pub_ts else pub_str
        return {
            "event_id": f"ff_{name}_{pub_str}",
            "name": f"美国{name}" if country.upper() == "USD" and not name.startswith("美国") else name,
            "country": country,
            "star": _normalize_star(raw_impact=row.get("impact")),
            "pub_time_ts": pub_ts,
            "pub_time_str": bj_str,
            "previous": row.get("previous"),
            "forecast": row.get("forecast"),
            "actual": row.get("actual"),
            "unit": "",
            "source": source,
        }
    return None


def _fetch_akshare_calendar(date_str: str) -> List[Dict[str, Any]]:
    try:
        import akshare as ak
        fn = getattr(ak, "news_economic_baidu", None)
        if fn is None:
            return []
        df = fn(date=date_str.replace("-", ""))
        if df is None or df.empty:
            return []
        out = []
        for _, row in df.iterrows():
            d_str = str(row.get("日期") or date_str)
            t_str = str(row.get("时间") or "")
            pub_ts = _parse_shanghai_dt(f"{d_str} {t_str}".strip())
            out.append({
                "event_id": f"ak_{row.get('事件', '')}_{t_str}",
                "name": clean_flash_text(row.get("事件") or ""),
                "country": clean_flash_text(row.get("地区") or ""),
                "star": _normalize_star(raw_star=row.get("重要性")),
                "pub_time_ts": pub_ts,
                "pub_time_str": f"{d_str} {t_str}".strip(),
                "previous": row.get("前值"),
                "forecast": row.get("预期"),
                "actual": row.get("公布"),
                "unit": "",
                "source": "akshare",
            })
        return out
    except Exception:
        return []


def fetch_us_economic_calendar(
    target_date: Optional[datetime] = None,
    timeout: float = HTTP_TIMEOUT,
) -> List[Dict[str, Any]]:
    """抓取当日财经日历，按 金十CDN -> 华尔街见闻 -> ForexFactory -> akshare 顺序无阻塞回退。"""
    et_tz = _get_tz("America/New_York", -5)
    bj_tz = _get_tz("Asia/Shanghai", 8)
    now_et = target_date or datetime.now(et_tz)
    now_bj = now_et.astimezone(bj_tz)

    # 1) 金十官方当日财经日历静态流
    try:
        url = JIN10_RILI_URL_TMPL.format(year=now_bj.year, month=now_bj.month, day=now_bj.day)
        r = requests.get(url, headers=JIN10_HEADERS, timeout=timeout)
        if r.status_code == 200:
            raw_list = r.json()
            if isinstance(raw_list, dict):
                raw_list = raw_list.get("data") or []
            if isinstance(raw_list, list) and raw_list:
                events = [ev for ev in (_normalize_calendar_row(x, "jin10_rili") for x in raw_list) if ev]
                if events:
                    return events
    except Exception:
        pass

    # 2) 华尔街见闻财经日历公开接口（中文、带前值/预期/实际公布值）
    try:
        day_start = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
        start_ts = int(day_start.timestamp())
        end_ts = start_ts + 86400
        r = requests.get(
            f"{WSCN_CALENDAR_URL}?start={start_ts}&end={end_ts}",
            headers={"User-Agent": JIN10_HEADERS["User-Agent"]},
            timeout=timeout,
        )
        if r.status_code == 200:
            items = ((r.json() or {}).get("data") or {}).get("items") or []
            events = [ev for ev in (_normalize_calendar_row(x, "wscn") for x in items) if ev]
            if events:
                return events
    except Exception:
        pass

    # 3) ForexFactory 周度公共 JSON 日历兜底
    try:
        r = requests.get(FF_CALENDAR_URL, headers={"User-Agent": JIN10_HEADERS["User-Agent"]}, timeout=timeout)
        if r.status_code == 200 and isinstance(r.json(), list):
            target_et_s = now_et.strftime("%Y-%m-%d")
            events = []
            for row in r.json():
                ev = _normalize_calendar_row(row, "forexfactory")
                if ev and ev["pub_time_ts"] is not None and _et_date_str(ev["pub_time_ts"]) == target_et_s:
                    events.append(ev)
            if events:
                return events
    except Exception:
        pass

    # 4) akshare 可选兜底
    return _fetch_akshare_calendar(now_bj.strftime("%Y-%m-%d"))


def _has_actual_value(event: Dict[str, Any]) -> bool:
    val = event.get("actual")
    if val is None:
        return False
    return str(val).strip() not in EMPTY_ACTUAL_VALUES


def evaluate_calendar_alerts(
    events: List[Dict[str, Any]],
    now_ts: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """识别 15 分钟防插针倒计时预警 & 公布后瞬时战报。"""
    cur_ts = now_ts if now_ts is not None else time.time()
    countdowns: List[Dict[str, Any]] = []
    actuals: List[Dict[str, Any]] = []

    for ev in events or []:
        if not is_heavyweight_us_macro(ev):
            continue
        pub_ts = ev.get("pub_time_ts")
        if pub_ts is None:
            continue
        delta_to_release = pub_ts - cur_ts

        # 公布前 15 分钟倒计时（0 < 剩余 <= 900s，且尚未出结果）
        if 0 < delta_to_release <= 900 and not _has_actual_value(ev):
            countdowns.append({
                **ev,
                "dedupe_id": canonical_event_dedupe_id(ev, "COUNTDOWN"),
                "minutes_left": max(1, int(round(delta_to_release / 60.0))),
            })

        # 公布后瞬时战报（已出实际结果且在发布前后合理实盘窗口内）
        if _has_actual_value(ev) and -300 <= (cur_ts - pub_ts) <= 7200:
            actuals.append({
                **ev,
                "dedupe_id": canonical_event_dedupe_id(ev, "ACTUAL"),
            })

    return countdowns, actuals


def load_pushed_state(state_path: str = DEFAULT_STATE_FILE) -> Dict[str, float]:
    """读取本地 .jin10_pushed_ids.json 已推送记录。"""
    if not os.path.exists(state_path):
        return {}
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("pushed"), dict):
            return {str(k): float(v) for k, v in data["pushed"].items()}
        if isinstance(data, dict):
            return {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}
    except Exception:
        pass
    return {}


def save_pushed_state(pushed: Dict[str, float], state_path: str = DEFAULT_STATE_FILE) -> None:
    """持久化本地去重表，仅保留最近 1000 条记录防文件膨胀。"""
    try:
        ordered = sorted(pushed.items(), key=lambda kv: kv[1])[-1000:]
        payload = {"pushed": dict(ordered), "updated_at": datetime.now(timezone.utc).isoformat()}
        tmp_path = state_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, state_path)
    except Exception:
        pass


def is_already_pushed(dedupe_id: str, local_state: Dict[str, float]) -> bool:
    """双重去重校验：本地 .jin10_pushed_ids.json + shared_alert_state.py。"""
    if dedupe_id in local_state:
        return True
    if shared_alert_state is not None:
        try:
            if shared_alert_state.is_sent(dedupe_id):
                return True
        except Exception:
            pass
    return False


def mark_pushed(dedupe_id: str, local_state: Dict[str, float], state_path: str = DEFAULT_STATE_FILE) -> None:
    """发送确认成功后双端落盘写锁。"""
    local_state[dedupe_id] = time.time()
    save_pushed_state(local_state, state_path=state_path)
    if shared_alert_state is not None:
        try:
            dev = "ANDROID" if ("--android" in sys.argv or os.environ.get("TERMUX_VERSION")) else "PC"
            shared_alert_state.mark(dedupe_id, dev)
        except Exception:
            pass


def build_flash_embed(flash: Dict[str, Any]) -> Dict[str, Any]:
    kw_badges = " · ".join(f"`{k}`" for k in flash.get("keywords", [])[:6])
    title_text = flash.get("title") or "🚨 金十重要红字快讯"
    body = flash.get("content") or flash.get("text") or ""
    if len(body) > 1800:
        body = body[:1797] + "..."
    return {
        "title": f"🔴 {title_text[:180]}",
        "description": f"{body}\n\n🏷️ 命中关键词：{kw_badges}",
        "color": 15158332,
        "footer": {"text": f"金十 7×24 红字快讯 · {flash.get('time_str', '')} (UTC+8)"},
    }


def build_countdown_embed(ev: Dict[str, Any]) -> Dict[str, Any]:
    name = ev.get("name", "美国重磅数据")
    unit = ev.get("unit", "")
    prev_s = _format_val_with_unit(ev.get("previous"), unit)
    fore_s = _format_val_with_unit(ev.get("forecast"), unit)
    msg = f"⚠️ 宏观大考倒计时：距离【{name}】公布还有 15 分钟，谨防上下剧烈插针，暂停盲目挂单！"
    desc = (
        f"**{msg}**\n\n"
        f"• 重要度：{'⭐' * max(3, min(5, int(ev.get('star', 3))))}\n"
        f"• 距离公布：约 **{ev.get('minutes_left', 15)} 分钟**（预计 `{ev.get('pub_time_str', '--')}`）\n"
        f"• 前值：`{prev_s}` ｜ 预期值：`{fore_s}`"
    )
    return {
        "title": msg[:250],
        "description": desc,
        "color": 16750848,
        "footer": {"text": "金十财经日历 · 15分钟防插针风控哨兵"},
    }


def build_actual_embed(ev: Dict[str, Any]) -> Dict[str, Any]:
    name = ev.get("name", "美国重磅数据")
    unit = ev.get("unit", "")
    prev_s = _format_val_with_unit(ev.get("previous"), unit)
    fore_s = _format_val_with_unit(ev.get("forecast"), unit)
    act_s = _format_val_with_unit(ev.get("actual"), unit)
    desc = (
        f"• **前值**：`{prev_s}`\n"
        f"• **预期值**：`{fore_s}`\n"
        f"• **实际公布值**：`{act_s}`\n"
        f"• 公布时间：`{ev.get('pub_time_str', '--')}`"
    )
    return {
        "title": f"📊 【宏观重磅公布】{name}",
        "description": desc,
        "color": 3447003,
        "footer": {"text": f"金十财经日历 · 公布值瞬时战报 ({ev.get('source', 'jin10')})"},
    }


def send_discord_embed(
    webhook_url: str,
    embed: Dict[str, Any],
    username: str = "金十·宏观快讯哨兵",
    timeout: float = HTTP_TIMEOUT,
) -> bool:
    """推送单条 Discord Embed（严格 timeout=3s，异常降级不抛异常）。"""
    if not webhook_url or not embed:
        return False
    try:
        resp = requests.post(
            webhook_url,
            json={"username": username, "embeds": [embed]},
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        return resp.status_code in (200, 204)
    except Exception:
        return False


# ==============================================================================
# 网页端消息面聚合、自动脱敏清洗、防插针横幅与个股资讯引擎
# ==============================================================================

_MARKET_NEWS_LOCK = threading.Lock()

_JIN10_WATERMARK_RE = re.compile(
    r"(?:【\s*(?:据)?金十[^】]*】|"
    r"[（(]\s*(?:来源\s*[：:]\s*)?(?:据)?金十(?:数据|期货|快讯|VIP|独家|图示|App|消息|财经)?[^）)]*[）)]|"
    r"(?:^|(?<=[\s。；;！!]))(?:据|来自)?金十(?:数据|期货|快讯|VIP|独家|整理|图示|消息|财经)?(?:\s*\d{1,2}月\d{1,2}日)?(?:电|讯|报道|消息)?\s*[：:,，、]?|"
    r"(?:来源|出处)\s*[：:]\s*(?:金十数据|金十快讯|金十财经|jin10\.com)[^\s。；;]*|"
    r"https?://[^\s]*jin10\.com[^\s]*|"
    r"点击查看(?:更多|详情|大图|全文|原文)[。！!…]*|"
    r"[@＠]?金十(?:数据|快讯|VIP|图示|财经))",
    flags=re.IGNORECASE,
)


def sanitize_flash_text(raw: Any) -> str:
    """自动正则清洗金十来源前缀与水印，输出纯客观事件摘要文本。"""
    text = clean_flash_text(raw)
    if not text:
        return ""
    text = _JIN10_WATERMARK_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.lstrip("：:，,、·|-— ")
    return text.strip()


def _format_dual_tz(ts: Optional[float]) -> Tuple[str, str, str, str]:
    """返回 (time_et, time_bj, time_kst, time_display)。"""
    if ts is None:
        return ("-- ET", "-- BJ", "-- KST", "--")
    try:
        dt_et = datetime.fromtimestamp(float(ts), tz=_get_tz("America/New_York", -5))
        dt_bj = datetime.fromtimestamp(float(ts), tz=_get_tz("Asia/Shanghai", 8))
        dt_kst = datetime.fromtimestamp(float(ts), tz=_get_tz("Asia/Seoul", 9))
        t_et = dt_et.strftime("%m-%d %H:%M ET")
        t_bj = dt_bj.strftime("%H:%M BJ")
        t_kst = dt_kst.strftime("%H:%M KST")
        return (t_et, t_bj, t_kst, f"{dt_et.strftime('%H:%M')} ET / {t_bj}")
    except Exception:
        return ("-- ET", "-- BJ", "-- KST", "--")


def _is_empty_macro_val(val: Any) -> bool:
    if val is None:
        return True
    s = str(val).strip()
    return s in EMPTY_ACTUAL_VALUES or s == "待公布"


def _parse_numeric_value(val: Any) -> Optional[float]:
    if _is_empty_macro_val(val):
        return None
    s = str(val).strip().replace(",", "")
    mult = 1.0
    low = s.lower()
    if low.endswith("%"):
        s = s[:-1].strip()
    elif low.endswith("k"):
        mult = 1e3
        s = s[:-1].strip()
    elif low.endswith("m"):
        mult = 1e6
        s = s[:-1].strip()
    elif low.endswith("b"):
        mult = 1e9
        s = s[:-1].strip()
    elif s.endswith("万"):
        mult = 1e4
        s = s[:-1].strip()
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0)) * mult
    except Exception:
        return None


def interpret_macro_release(name: str, actual: Any, forecast: Any = None, previous: Any = None) -> Dict[str, str]:
    """根据指标类型与实际公布值生成多空定性解读。"""
    if _is_empty_macro_val(actual):
        return {"tone": "pending", "note": "待公布（数据发布前后盘口易上下剧烈插针）"}

    act_num = _parse_numeric_value(actual)
    fore_num = _parse_numeric_value(forecast)
    prev_num = _parse_numeric_value(previous)
    tag, _ = _canonical_macro_info(name)

    if tag in ("CPI", "核心CPI", "PPI", "核心PPI", "PCE", "核心PCE"):
        ref = fore_num if fore_num is not None else prev_num
        if act_num is not None and ref is not None:
            if act_num > ref + 1e-6:
                return {"tone": "bearish", "note": "偏鹰·通胀粘性高于预期（压制降息预期 · 成长股承压）"}
            if act_num < ref - 1e-6:
                return {"tone": "bullish", "note": "偏鸽·通胀超预期回落（强化降息交易 · 利多美股）"}
            return {"tone": "neutral", "note": "符合预期·通胀温和持平（中性博弈）"}
        return {"tone": "neutral", "note": "已公布（关注分项通胀趋势与美债反应）"}

    if tag == "非农就业(NFP)":
        ref = fore_num if fore_num is not None else prev_num
        if act_num is not None and ref is not None:
            if act_num > ref + 1e-6:
                return {"tone": "neutral", "note": "就业强于预期（紧缩担忧压制宽松预期 · 留意美债收益率）"}
            if act_num < ref - 1e-6:
                return {"tone": "neutral", "note": "就业放缓低于预期（强化降息预期 · 关注衰退风险）"}
            return {"tone": "neutral", "note": "符合预期·就业表现平稳（中性影响）"}
        return {"tone": "neutral", "note": "已公布（关注失业率与时薪分项）"}

    if tag == "FOMC利率决议":
        ref = prev_num if prev_num is not None else fore_num
        if act_num is not None and ref is not None:
            if act_num < ref - 1e-6:
                return {"tone": "bullish", "note": "落实降息·货币转向宽松（紧盯点阵图与鲍威尔表态）"}
            if act_num > ref + 1e-6:
                return {"tone": "bearish", "note": "意外加息·立场偏鹰收紧（高估值成长股承压）"}
            return {"tone": "neutral", "note": "按兵不动·维持利率不变（聚焦声明措辞与发布会）"}
        return {"tone": "neutral", "note": "已公布（关注美联储声明措辞）"}

    return {"tone": "neutral", "note": "重磅宏观数据已落地"}


def build_macro_calendar_view(
    events: List[Dict[str, Any]],
    now_ts: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """筛出当日美国三星/五星级重磅数据并标注状态（upcoming/countdown_15m/released）。"""
    cur_ts = now_ts if now_ts is not None else time.time()
    today_et = _et_date_str(cur_ts)
    dedup: Dict[str, Dict[str, Any]] = {}

    for ev in events or []:
        if not is_heavyweight_us_macro(ev):
            continue
        pub_ts = ev.get("pub_time_ts")
        if pub_ts is not None:
            delta = float(pub_ts) - cur_ts
            ev_et_day = _et_date_str(float(pub_ts))
            if ev_et_day != today_et and not (0 < delta <= 900):
                continue

        key = canonical_event_dedupe_id(ev, "CAL")
        has_act = not _is_empty_macro_val(ev.get("actual"))
        prev_existing = dedup.get(key)
        if prev_existing is not None:
            prev_has_act = not _is_empty_macro_val(prev_existing.get("actual"))
            if prev_has_act and not has_act:
                continue
            if prev_has_act == has_act and int(prev_existing.get("star", 0)) >= int(ev.get("star", 0)):
                continue
        dedup[key] = ev

    results: List[Dict[str, Any]] = []
    for ev in dedup.values():
        pub_ts = ev.get("pub_time_ts")
        unit = ev.get("unit") or ""
        has_act = not _is_empty_macro_val(ev.get("actual"))
        minutes_left = 0
        if pub_ts is not None and float(pub_ts) > cur_ts:
            minutes_left = max(1, int(math.ceil((float(pub_ts) - cur_ts) / 60.0)))

        if has_act:
            status = "released"
        elif pub_ts is not None and 0 < (float(pub_ts) - cur_ts) <= 900:
            status = "countdown_15m"
        else:
            status = "upcoming"

        t_et, t_bj, t_kst, _ = _format_dual_tz(pub_ts)
        interp = interpret_macro_release(
            str(ev.get("name") or ""),
            ev.get("actual"),
            ev.get("forecast"),
            ev.get("previous"),
        )
        results.append({
            "event_id": str(ev.get("event_id") or ""),
            "name": str(ev.get("name") or "美国重磅数据"),
            "country": str(ev.get("country") or "美国"),
            "star": int(ev.get("star") or 3),
            "pub_time_ts": pub_ts,
            "pub_time_et": t_et,
            "pub_time_bj": t_bj,
            "pub_time_kst": t_kst,
            "previous": _format_val_with_unit(ev.get("previous"), unit),
            "forecast": _format_val_with_unit(ev.get("forecast"), unit),
            "actual": _format_val_with_unit(ev.get("actual"), unit),
            "unit": str(unit),
            "status": status,
            "minutes_left": minutes_left,
            "impact_tone": interp["tone"],
            "impact_note": interp["note"],
            "source": str(ev.get("source") or "calendar"),
        })

    order_rank = {"countdown_15m": 0, "upcoming": 1, "released": 2}
    results.sort(key=lambda r: (
        order_rank.get(r["status"], 9),
        float(r["pub_time_ts"] or 0.0) if r["status"] != "released" else -float(r["pub_time_ts"] or 0.0),
    ))
    return results


def build_sanitized_flash_item(flash: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """构造可供公开网页使用的完全脱敏快讯对象。"""
    if not isinstance(flash, dict):
        return None
    raw_summary = flash.get("summary") or flash.get("content") or flash.get("text") or flash.get("title") or ""
    summary = sanitize_flash_text(raw_summary)
    if not summary:
        return None
    raw_title = flash.get("title") or ""
    title = sanitize_flash_text(raw_title)
    if not title or title == summary:
        title = summary[:44] + ("…" if len(summary) > 44 else "")
    pub_ts = flash.get("pub_ts")
    t_et, t_bj, t_kst, t_disp = _format_dual_tz(pub_ts)
    if t_disp == "--" and flash.get("time_str"):
        t_disp = str(flash["time_str"])
    return {
        "id": str(flash.get("id") or ""),
        "title": title,
        "summary": summary,
        "keywords": list(flash.get("keywords") or [])[:6],
        "pub_ts": pub_ts,
        "time_et": flash.get("time_et") or t_et,
        "time_bj": flash.get("time_bj") or t_bj,
        "time_kst": flash.get("time_kst") or t_kst,
        "time_display": t_disp if t_disp != "--" else (flash.get("time_display") or "--"),
    }


def third_friday_of_month(year: int, month: int) -> date:
    """计算指定年月的美股第三个星期五 (Monthly OPEX / Quad Witching 期权到期日)。"""
    d1 = date(int(year), int(month), 1)
    first_friday_day = 1 + (4 - d1.weekday()) % 7
    return date(int(year), int(month), first_friday_day + 14)


def compute_opex_shield_info(target_date: Optional[Any] = None) -> Dict[str, Any]:
    """
    自动计算美股月度 OPEX（每月第 3 个周五）与四巫日 Quad Witching（3/6/9/12 月第 3 个周五），
    并在到期日当天及前 1 个交易日（T-1 周四）激活顶部宏观防踩踏护盾提示。
    """
    if target_date is None:
        d = datetime.now(_get_tz("America/New_York", -5)).date()
    elif isinstance(target_date, datetime):
        d = target_date.date()
    elif isinstance(target_date, date):
        d = target_date
    else:
        d = datetime.strptime(str(target_date).strip()[:10], "%Y-%m-%d").date()

    opex_day = third_friday_of_month(d.year, d.month)
    is_quad = d.month in (3, 6, 9, 12)
    t_minus_1 = opex_day - timedelta(days=1)

    if d <= opex_day:
        next_opex = opex_day
        next_is_quad = is_quad
    else:
        ny = d.year + (1 if d.month == 12 else 0)
        nm = 1 if d.month == 12 else (d.month + 1)
        next_opex = third_friday_of_month(ny, nm)
        next_is_quad = nm in (3, 6, 9, 12)

    active = False
    timing = "NONE"
    kind = "NONE"
    badge = ""
    headline = ""
    detail = ""

    if d == opex_day:
        active = True
        timing = "TODAY"
        kind = "QUAD_WITCHING" if is_quad else "MONTHLY_OPEX"
        badge = "四巫日护盾" if is_quad else "OPEX 护盾"
        if is_quad:
            headline = "⚠️ 四巫日 (Quad Witching) · 警惕全天及尾盘负 Gamma 剧烈波动"
            detail = "股指期货/股指期权/个股期权/个股期货四重到期，机构尾盘再平衡易放大急涨急跌，严防尾盘踩踏。"
        else:
            headline = "⚠️ 月度 OPEX 期权到期日 · 警惕尾盘负 Gamma 剧烈波动"
            detail = "大量月度期权集中到期与做市商 Gamma 对冲再平衡，尾盘易出现剧烈插针，控制杠杆切勿追高。"
    elif d == t_minus_1:
        active = True
        timing = "T_MINUS_1"
        kind = "QUAD_WITCHING" if is_quad else "MONTHLY_OPEX"
        badge = "四巫日 T-1" if is_quad else "OPEX T-1"
        if is_quad:
            headline = "⚠️ 明日四巫日 (Quad Witching T-1) · 警惕机构提前换仓与负 Gamma 波动"
            detail = f"明日 ({opex_day.isoformat()}) 为四巫日，机构提前展期换仓易诱发盘口假突破或急杀，谨慎重仓。"
        else:
            headline = "⚠️ 明日月度 OPEX 期权到期 (T-1) · 警惕机构换仓与尾盘负 Gamma 波动"
            detail = f"明日 ({opex_day.isoformat()}) 为月度 OPEX 期权到期日，防范盘中做市商调仓引发的剧烈波动。"

    return {
        "date": d.isoformat(),
        "active": active,
        "timing": timing,
        "kind": kind,
        "is_quad_witching": is_quad if d in (opex_day, t_minus_1) else False,
        "opex_date": opex_day.isoformat(),
        "next_opex_date": next_opex.isoformat(),
        "next_is_quad_witching": next_is_quad,
        "days_to_opex": int((next_opex - d).days),
        "badge": badge,
        "headline": headline,
        "detail": detail,
    }


def build_calendar_banner_summary(
    macro_calendar: List[Dict[str, Any]],
    macro_flashes: List[Dict[str, Any]],
    target_date: Optional[Any] = None,
) -> Dict[str, Any]:
    """为网页顶部 #macroCalendarBanner 生成统一的展示摘要结构（含 OPEX / 四巫日防踩踏护盾）。"""
    opex = compute_opex_shield_info(target_date)
    apply_opex = bool(target_date is not None and opex.get("active"))

    countdowns = [ev for ev in (macro_calendar or []) if ev.get("status") == "countdown_15m"]
    if countdowns:
        ev = countdowns[0]
        mins = max(1, int(ev.get("minutes_left") or 15))
        name = ev.get("name", "美国重磅数据")
        det = f"公布时间 {ev.get('pub_time_et', '--')} / {ev.get('pub_time_kst', '--')} ｜ 前值 {ev.get('previous', '待公布')} · 预期 {ev.get('forecast', '待公布')}"
        if apply_opex:
            det = f"{det} ｜ {opex['headline']}"
        return {
            "level": "countdown_15m",
            "headline": f"⚠️ 宏观防插针预警：距离【{name}】公布还有 {mins} 分钟，盘口极易上下剧烈插针，暂停左侧盲目挂单！",
            "detail": det,
            "event_name": name,
            "minutes_left": mins,
            "opex_shield": opex,
        }

    if macro_calendar:
        released = [ev for ev in macro_calendar if ev.get("status") == "released"]
        upcoming = [ev for ev in macro_calendar if ev.get("status") == "upcoming"]
        ev = released[0] if released else upcoming[0]
        name = ev.get("name", "美国重磅数据")
        t_pair = f"{ev.get('pub_time_et', '--')} / {ev.get('pub_time_kst', '--')}"
        if ev.get("status") == "released":
            extra = f"（今日另有 {len(upcoming)} 项待公布）" if upcoming else ""
            det = f"{ev.get('impact_note', '')} · {t_pair}{extra}"
            if apply_opex:
                det = f"{det} ｜ {opex['headline']}"
            return {
                "level": "released",
                "headline": f"📊 今日重磅公布【{name}】· 公布值 {ev.get('actual', '--')}（前值 {ev.get('previous', '--')} / 预期 {ev.get('forecast', '--')}）",
                "detail": det,
                "event_name": name,
                "minutes_left": 0,
                "opex_shield": opex,
            }
        det = f"前值 {ev.get('previous', '待公布')} ｜ 预期 {ev.get('forecast', '待公布')} ｜ 临近公布前15分钟将自动触发防插针预警"
        if apply_opex:
            det = f"{det} ｜ {opex['headline']}"
        return {
            "level": "upcoming",
            "headline": f"📅 今日美股重磅预告【{name}】· 距公布约 {ev.get('minutes_left', '--')} 分钟（{t_pair}）",
            "detail": det,
            "event_name": name,
            "minutes_left": int(ev.get("minutes_left") or 0),
            "opex_shield": opex,
        }

    latest_flash = (macro_flashes or [None])[0]
    if apply_opex:
        det = opex["detail"]
        if isinstance(latest_flash, dict) and latest_flash.get("summary"):
            det = f"{det} ｜ ⚡ 快讯 ({latest_flash.get('time_display', '--')})：{latest_flash['summary'][:68]}"
        return {
            "level": "opex_shield",
            "headline": opex["headline"],
            "detail": det,
            "event_name": opex["kind"],
            "minutes_left": 0,
            "opex_shield": opex,
        }

    if isinstance(latest_flash, dict) and latest_flash.get("summary"):
        kw_str = " ".join(f"[{k}]" for k in latest_flash.get("keywords", [])[:3])
        return {
            "level": "idle_with_flash",
            "headline": f"📡 宏观消息面监控中 · 今日暂无美股五星级经济数据大考",
            "detail": f"⚡ 最新核心快讯 ({latest_flash.get('time_display', '--')}) {kw_str}：{latest_flash['summary'][:95]}",
            "event_name": "",
            "minutes_left": 0,
            "opex_shield": opex,
        }

    return {
        "level": "idle",
        "headline": "📡 宏观消息面监控中 · 今日暂无美股五星级经济数据大考",
        "detail": "已接入美联储 / CPI / PPI / PCE / 非农 / 关税与半导体要闻 7×24 实时监控（异常静默降级）",
        "event_name": "",
        "minutes_left": 0,
        "opex_shield": opex,
    }


def _fetch_yahoo_rss_news(symbol: str, timeout: float = HTTP_TIMEOUT, max_items: int = 5) -> Tuple[List[Dict[str, Any]], bool]:
    """返回 (news_items, transport_ok)；transport_ok=False 表示请求超时或网络异常。"""
    url = TICKER_NEWS_URL_YAHOO_RSS.format(symbol=quote_plus(symbol))
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": JIN10_HEADERS["User-Agent"]},
            timeout=timeout,
        )
        if resp.status_code != 200 or not resp.text:
            return ([], False)
        root = ET.fromstring(resp.text)
    except Exception:
        return ([], False)

    out: List[Dict[str, Any]] = []
    seen_titles = set()
    for node in root.findall(".//item"):
        title = clean_flash_text(node.findtext("title") or "")
        link = (node.findtext("link") or "").strip()
        if not title or not link.startswith(("http://", "https://")):
            continue
        t_key = title.lower()[:80]
        if t_key in seen_titles:
            continue
        seen_titles.add(t_key)

        pub_ts = None
        pub_raw = (node.findtext("pubDate") or "").strip()
        if pub_raw:
            try:
                dt_pub = parsedate_to_datetime(pub_raw)
                pub_ts = dt_pub.timestamp()
            except Exception:
                pub_ts = None
        t_et, _, _, t_disp = _format_dual_tz(pub_ts)
        source = clean_flash_text(node.findtext("source") or "") or "Yahoo Finance"
        out.append({
            "title": title,
            "url": link,
            "source": source,
            "time": t_et if t_et != "-- ET" else t_disp,
            "pub_ts": pub_ts,
        })
        if len(out) >= max_items:
            break
    return (out, True)


def _fetch_finnhub_ticker_news(
    symbol: str,
    finnhub_api_key: str,
    timeout: float = HTTP_TIMEOUT,
    max_items: int = 5,
) -> List[Dict[str, Any]]:
    if not finnhub_api_key or "-" in symbol or "=" in symbol:
        return []
    now_et = datetime.now(_get_tz("America/New_York", -5))
    from_d = (now_et - timedelta(days=7)).strftime("%Y-%m-%d")
    to_d = now_et.strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            FINNHUB_COMPANY_NEWS_URL,
            params={"symbol": symbol, "from": from_d, "to": to_d, "token": finnhub_api_key},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return []
        rows = resp.json()
        if not isinstance(rows, list):
            return []
    except Exception:
        return []

    rows_sorted = sorted(rows, key=lambda r: float(r.get("datetime") or 0), reverse=True)
    out: List[Dict[str, Any]] = []
    seen_titles = set()
    for row in rows_sorted:
        if not isinstance(row, dict):
            continue
        title = clean_flash_text(row.get("headline") or "")
        link = str(row.get("url") or "").strip()
        if not title or not link.startswith(("http://", "https://")):
            continue
        t_key = title.lower()[:80]
        if t_key in seen_titles:
            continue
        seen_titles.add(t_key)
        pub_ts = float(row["datetime"]) if row.get("datetime") else None
        t_et, _, _, _ = _format_dual_tz(pub_ts)
        out.append({
            "title": title,
            "url": link,
            "source": clean_flash_text(row.get("source") or "") or "Finnhub",
            "time": t_et,
            "pub_ts": pub_ts,
        })
        if len(out) >= max_items:
            break
    return out


def fetch_single_ticker_news(
    symbol: str,
    finnhub_api_key: str = "",
    timeout: float = HTTP_TIMEOUT,
    max_items: int = 5,
) -> List[Dict[str, Any]]:
    """抓取单只标的最新 3~5 条资讯（Yahoo RSS 优先 -> Finnhub 回退 -> yfinance Search 兜底）。"""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return []

    rss_items, transport_ok = _fetch_yahoo_rss_news(sym, timeout=timeout, max_items=max_items)
    if rss_items:
        return rss_items

    fh_items = _fetch_finnhub_ticker_news(sym, finnhub_api_key=finnhub_api_key, timeout=timeout, max_items=max_items)
    if fh_items:
        return fh_items

    if not transport_ok:
        return []

    try:
        import yfinance as yf
        search_obj = getattr(yf, "Search", None)
        if search_obj is not None:
            raw_news = getattr(search_obj(sym, news_count=max_items), "news", None) or []
            out: List[Dict[str, Any]] = []
            for n in raw_news:
                if not isinstance(n, dict):
                    continue
                title = clean_flash_text(n.get("title") or "")
                link = str(n.get("link") or "").strip()
                if not title or not link.startswith(("http://", "https://")):
                    continue
                pub_ts = float(n["providerPublishTime"]) if n.get("providerPublishTime") else None
                t_et, _, _, _ = _format_dual_tz(pub_ts)
                out.append({
                    "title": title,
                    "url": link,
                    "source": clean_flash_text(n.get("publisher") or "") or "Yahoo Finance",
                    "time": t_et,
                    "pub_ts": pub_ts,
                })
                if len(out) >= max_items:
                    break
            return out
    except Exception:
        pass
    return []


def update_ticker_news_map(
    tickers: List[str],
    prev_news: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    prev_ts_map: Optional[Dict[str, float]] = None,
    now_ts: Optional[float] = None,
    ttl_seconds: int = TICKER_NEWS_TTL_SECONDS,
    timeout: float = HTTP_TIMEOUT,
    max_wall_seconds: float = 6.0,
    max_refresh: int = 25,
    finnhub_api_key: str = "",
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, float]]:
    """增量并发更新个股新闻映射（严格限流与本地缓存回退，绝不阻塞主雷达轮询）。"""
    cur_ts = now_ts if now_ts is not None else time.time()
    news_map: Dict[str, List[Dict[str, Any]]] = dict(prev_news or {})
    ts_map: Dict[str, float] = dict(prev_ts_map or {})
    unique_tickers = list(dict.fromkeys(str(t).strip().upper() for t in (tickers or []) if t))

    stale = [
        tk for tk in unique_tickers
        if not news_map.get(tk) or (cur_ts - float(ts_map.get(tk, 0.0))) >= ttl_seconds
    ]
    stale.sort(key=lambda tk: float(ts_map.get(tk, 0.0)))
    batch = stale[:max(0, int(max_refresh))]
    if not batch:
        return (news_map, ts_map)

    fh_key = finnhub_api_key or os.environ.get("FINNHUB_API_KEY", "").strip()
    start_mono = time.monotonic()
    ex = ThreadPoolExecutor(max_workers=min(8, max(1, len(batch))))
    future_to_tk = {ex.submit(fetch_single_ticker_news, tk, fh_key, timeout, 5): tk for tk in batch}
    try:
        remaining = max(0.2, float(max_wall_seconds) - (time.monotonic() - start_mono))
        for fut in as_completed(future_to_tk, timeout=remaining):
            tk = future_to_tk[fut]
            try:
                items = fut.result()
                if items:
                    news_map[tk] = items
                    ts_map[tk] = cur_ts
            except Exception:
                pass
    except Exception:
        pass
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    return (news_map, ts_map)


def load_latest_market_news_cache(cache_path: str = DEFAULT_MARKET_NEWS_CACHE) -> Dict[str, Any]:
    """安全加载本地 data_cache/latest_market_news.json。"""
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {
        "updated_at_ts": 0,
        "updated_at_et": "--",
        "updated_at_kst": "--",
        "macro_calendar": [],
        "macro_flashes": [],
        "ticker_news": {},
        "ticker_news_ts": {},
        "calendar_banner": build_calendar_banner_summary([], []),
    }


def build_latest_market_news_cache(
    tickers: Optional[List[str]] = None,
    cache_path: str = DEFAULT_MARKET_NEWS_CACHE,
    now_ts: Optional[float] = None,
    timeout: float = HTTP_TIMEOUT,
    max_wall_seconds: float = 6.0,
    refresh_all: bool = False,
    finnhub_api_key: str = "",
) -> Dict[str, Any]:
    """
    聚合生成网页端专用脱敏消息面缓存 (data_cache/latest_market_news.json)。
    全程严格 timeout=3s 且遇异常时平滑回退上一轮本地 JSON，绝不阻塞主雷达 90s 轮询。
    """
    cur_ts = now_ts if now_ts is not None else time.time()
    prev_cache = load_latest_market_news_cache(cache_path)

    raw_flashes: List[Dict[str, Any]] = []
    calendar_events: List[Dict[str, Any]] = []
    try:
        raw_flashes = fetch_jin10_flash_list(timeout=timeout)
    except Exception:
        raw_flashes = []

    try:
        target_et = datetime.fromtimestamp(cur_ts, tz=_get_tz("America/New_York", -5))
        calendar_events = fetch_us_economic_calendar(target_date=target_et, timeout=timeout)
    except Exception:
        calendar_events = []

    for item in raw_flashes:
        ev1 = parse_jin10_type1_release(item)
        if ev1:
            calendar_events.append(ev1)

    if not calendar_events and isinstance(prev_cache.get("macro_calendar"), list):
        calendar_events = prev_cache["macro_calendar"]

    macro_calendar = build_macro_calendar_view(calendar_events, now_ts=cur_ts)

    # 仅保留重要红字 + 白名单命中快讯并彻底脱敏（最多保留最新 25 条）
    fresh_flashes = filter_important_flashes(raw_flashes, now_ts=cur_ts, max_age_seconds=86400)
    merged_flashes: Dict[str, Dict[str, Any]] = {}
    for old_f in (prev_cache.get("macro_flashes") or []):
        san = build_sanitized_flash_item(old_f)
        if san and san["id"]:
            merged_flashes[san["id"]] = san
    for new_f in fresh_flashes:
        san = build_sanitized_flash_item(new_f)
        if san and san["id"]:
            merged_flashes[san["id"]] = san

    macro_flashes = sorted(
        merged_flashes.values(),
        key=lambda x: (float(x.get("pub_ts") or 0.0), str(x.get("id") or "")),
        reverse=True,
    )[:MAX_MACRO_FLASHES]

    prev_tnews = prev_cache.get("ticker_news") if isinstance(prev_cache.get("ticker_news"), dict) else {}
    prev_tts = prev_cache.get("ticker_news_ts") if isinstance(prev_cache.get("ticker_news_ts"), dict) else {}
    target_tickers = list(tickers or [])
    if target_tickers:
        max_ref = max(len(target_tickers), 80) if (refresh_all or len(prev_tnews) < 20) else 25
        wall_cap = 12.0 if (refresh_all or len(prev_tnews) < 20) else max_wall_seconds
        ticker_news, ticker_news_ts = update_ticker_news_map(
            target_tickers,
            prev_news=prev_tnews,
            prev_ts_map=prev_tts,
            now_ts=cur_ts,
            ttl_seconds=TICKER_NEWS_TTL_SECONDS,
            timeout=timeout,
            max_wall_seconds=wall_cap,
            max_refresh=max_ref,
            finnhub_api_key=finnhub_api_key,
        )
    else:
        ticker_news, ticker_news_ts = prev_tnews, prev_tts

    t_et, _, t_kst, _ = _format_dual_tz(cur_ts)
    target_d_et = datetime.fromtimestamp(cur_ts, tz=_get_tz("America/New_York", -5)).date()
    opex_shield = compute_opex_shield_info(target_d_et)
    payload = {
        "updated_at_ts": round(cur_ts, 3),
        "updated_at_et": t_et,
        "updated_at_kst": t_kst,
        "macro_calendar": macro_calendar,
        "macro_flashes": macro_flashes,
        "ticker_news": ticker_news,
        "ticker_news_ts": ticker_news_ts,
        "opex_shield": opex_shield,
        "calendar_banner": build_calendar_banner_summary(macro_calendar, macro_flashes, target_date=target_d_et),
    }

    if cache_path:
        with _MARKET_NEWS_LOCK:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
                tmp_path = f"{cache_path}.tmp"
                with open(tmp_path, "w", encoding="utf-8") as wf:
                    json.dump(payload, wf, ensure_ascii=False, indent=2)
                os.replace(tmp_path, cache_path)
            except Exception:
                return prev_cache

    return payload


def render_macro_calendar_banner_html(
    market_news: Optional[Dict[str, Any]] = None,
    variant: str = "terminal",
    target_date: Optional[Any] = None,
) -> str:
    """统一渲染网页顶部 #macroCalendarBanner 横幅（支持 terminal 与 predict 两种页面皮肤及 OPEX 护盾）。"""
    data = market_news if isinstance(market_news, dict) else load_latest_market_news_cache()
    banner = data.get("calendar_banner")
    if target_date is not None or not isinstance(banner, dict) or not banner.get("headline"):
        banner = build_calendar_banner_summary(
            data.get("macro_calendar") or [],
            data.get("macro_flashes") or [],
            target_date=target_date,
        )

    level = str(banner.get("level") or "idle")
    opex_info = banner.get("opex_shield") if isinstance(banner.get("opex_shield"), dict) else {}
    headline = html_lib.escape(str(banner.get("headline") or ""))
    detail = html_lib.escape(str(banner.get("detail") or ""))

    if level == "countdown_15m":
        bg = "linear-gradient(90deg, rgba(234, 88, 12, 0.32), rgba(220, 38, 38, 0.24))"
        border_col = "rgba(249, 115, 22, 0.78)"
        head_col = "#fed7aa"
        det_col = "#fecaca"
        badge_bg = "rgba(239, 68, 68, 0.35)"
        badge_txt = "防插针警戒"
    elif level == "opex_shield":
        bg = "linear-gradient(90deg, rgba(245, 158, 11, 0.28), rgba(234, 88, 12, 0.22))"
        border_col = "rgba(251, 191, 36, 0.78)"
        head_col = "#fde68a"
        det_col = "#fef3c7"
        badge_bg = "rgba(245, 158, 11, 0.32)"
        badge_txt = html_lib.escape(str(opex_info.get("badge") or "OPEX 护盾"))
    elif level == "released":
        bg = "linear-gradient(90deg, rgba(37, 99, 235, 0.20), rgba(15, 23, 42, 0.85))"
        border_col = "rgba(96, 165, 250, 0.45)"
        head_col = "#93c5fd"
        det_col = "#cbd5e1"
        badge_bg = "rgba(59, 130, 246, 0.22)"
        badge_txt = "重磅公布"
    elif level == "upcoming":
        bg = "linear-gradient(90deg, rgba(245, 158, 11, 0.16), rgba(30, 41, 59, 0.85))"
        border_col = "rgba(251, 191, 36, 0.45)"
        head_col = "#fde68a"
        det_col = "#e2e8f0"
        badge_bg = "rgba(245, 158, 11, 0.22)"
        badge_txt = "今日大考"
    else:
        bg = "linear-gradient(90deg, rgba(15, 23, 42, 0.92), rgba(30, 41, 59, 0.85))"
        border_col = "rgba(56, 189, 248, 0.28)"
        head_col = "#7dd3fc"
        det_col = "#cbd5e1"
        badge_bg = "rgba(56, 189, 248, 0.16)"
        badge_txt = "宏观监控"

    if variant == "predict":
        box_style = (
            f"background: {bg}; border: 1px solid {border_col}; border-radius: 10px; "
            f"padding: 10px 16px; margin-bottom: 14px; display: flex; align-items: center; "
            f"justify-content: space-between; gap: 10px; flex-wrap: wrap;"
        )
        flash_btn = (
            '<a href="futu_pro_terminal.html#open_flash" '
            'onclick="if(document.getElementById(\'notifDrawer\')||document.getElementById(\'notifDrawerOverlay\'))'
            '{event.preventDefault();if(typeof openMacroFlashDrawer===\'function\')openMacroFlashDrawer();}" '
            'style="font-size: 11.5px; color: #7dd3fc; '
            'background: rgba(56,189,248,0.14); border: 1px solid rgba(56,189,248,0.35); '
            'padding: 3px 10px; border-radius: 6px; text-decoration: none; font-weight: 700; white-space: nowrap;">'
            '⚡ 查看实时快讯 ↗</a>'
        )
    else:
        box_style = (
            f"background: {bg}; border-bottom: 1px solid {border_col}; padding: 5px 14px; "
            f"display: flex; align-items: center; justify-content: space-between; gap: 10px; "
            f"flex-wrap: wrap; flex-shrink: 0; font-size: 12px;"
        )
        flash_btn = (
            '<button type="button" onclick="openMacroFlashDrawer()" '
            'style="font-size: 11px; color: #7dd3fc; background: rgba(56,189,248,0.14); '
            'border: 1px solid rgba(56,189,248,0.35); padding: 2px 9px; border-radius: 4px; '
            'cursor: pointer; font-weight: 700; white-space: nowrap;">⚡ 实时快讯</button>'
        )

    extra_opex_badge = ""
    if level != "opex_shield" and isinstance(opex_info, dict) and opex_info.get("active"):
        opx_hl = str(opex_info.get("headline") or "")
        if opx_hl and opx_hl in str(banner.get("detail") or ""):
            opx_b = html_lib.escape(str(opex_info.get("badge") or "OPEX 护盾"))
            extra_opex_badge = (
                f'<span id="macroOpexBadge" style="font-size: 10.5px; font-weight: 800; color: #fde68a; '
                f'background: rgba(245, 158, 11, 0.28); border: 1px solid rgba(251, 191, 36, 0.55); '
                f'padding: 1px 7px; border-radius: 4px; white-space: nowrap;">⚠️ {opx_b}</span>'
            )

    return (
        f'<!-- MACRO_CALENDAR_BANNER_START -->\n'
        f'<div id="macroCalendarBanner" data-level="{html_lib.escape(level)}" style="{box_style}">'
        f'<div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; flex: 1;">'
        f'<span style="font-size: 10.5px; font-weight: 800; color: {head_col}; background: {badge_bg}; '
        f'padding: 1px 7px; border-radius: 4px; white-space: nowrap;">{badge_txt}</span>'
        f'{extra_opex_badge}'
        f'<span id="macroCalHeadline" style="font-weight: 700; color: {head_col};">{headline}</span>'
        f'<span id="macroCalDetail" style="font-size: 11.5px; color: {det_col};">{detail}</span>'
        f'</div>'
        f'{flash_btn}'
        f'</div>\n'
        f'<!-- MACRO_CALENDAR_BANNER_END -->'
    )


def run_jin10_sentinel_safe(
    state_path: str = DEFAULT_STATE_FILE,
    now_ts: Optional[float] = None,
    dry_run: bool = False,
    timeout: float = HTTP_TIMEOUT,
) -> Dict[str, int]:
    """
    执行单轮金十红字快讯 + 美国重磅日历倒计时/公布值检查。
    绝不向公开 HTML 写入任何消息内容；任一步骤异常静默跳过不阻塞主雷达。
    """
    summary = {"flashes_sent": 0, "countdowns_sent": 0, "actuals_sent": 0}
    if not _JIN10_LOCK.acquire(blocking=False):
        return summary
    try:
        webhook_url = get_jin10_webhook_url()
        if not webhook_url and not dry_run:
            print("[!] 未配置 DISCORD_JIN10_WEBHOOK_URL，本轮跳过金十推送，严禁串台至买点频道。")
            return summary

        cur_ts = now_ts if now_ts is not None else time.time()
        local_state = load_pushed_state(state_path=state_path)

        # 1. 拉取金十 7x24 快讯流
        raw_flashes = fetch_jin10_flash_list(timeout=timeout)
        matched_flashes = filter_important_flashes(raw_flashes, now_ts=cur_ts, max_age_seconds=7200)
        for fl in reversed(matched_flashes):
            did = f"JIN10_FLASH:{fl['id']}"
            if is_already_pushed(did, local_state):
                continue
            embed = build_flash_embed(fl)
            ok = True if dry_run else send_discord_embed(webhook_url, embed, timeout=timeout)
            if ok and not dry_run:
                mark_pushed(did, local_state, state_path=state_path)
            if ok:
                summary["flashes_sent"] += 1

        # 2. 合并 type==1 即时公布快讯与当日美国重磅经济日历
        type1_events = [ev for ev in (parse_jin10_type1_release(x) for x in raw_flashes) if ev]
        et_tz = _get_tz("America/New_York", -5)
        cal_events = fetch_us_economic_calendar(
            target_date=datetime.fromtimestamp(cur_ts, tz=et_tz),
            timeout=timeout,
        )
        all_events = type1_events + cal_events
        countdowns, actuals = evaluate_calendar_alerts(all_events, now_ts=cur_ts)

        for ev in countdowns:
            did = ev["dedupe_id"]
            if is_already_pushed(did, local_state):
                continue
            embed = build_countdown_embed(ev)
            ok = True if dry_run else send_discord_embed(webhook_url, embed, username="金十·宏观防插针预警", timeout=timeout)
            if ok and not dry_run:
                mark_pushed(did, local_state, state_path=state_path)
            if ok:
                summary["countdowns_sent"] += 1

        for ev in actuals:
            did = ev["dedupe_id"]
            if is_already_pushed(did, local_state):
                continue
            embed = build_actual_embed(ev)
            ok = True if dry_run else send_discord_embed(webhook_url, embed, username="金十·重磅公布战报", timeout=timeout)
            if ok and not dry_run:
                mark_pushed(did, local_state, state_path=state_path)
            if ok:
                summary["actuals_sent"] += 1

        return summary
    except Exception:
        return summary
    finally:
        _JIN10_LOCK.release()


def run_jin10_sentinel_async(
    state_path: str = DEFAULT_STATE_FILE,
    timeout: float = HTTP_TIMEOUT,
) -> Optional[threading.Thread]:
    """在独立守护线程中异步触发金十哨兵，绝不阻塞主雷达 90 秒轮询。"""
    try:
        t = threading.Thread(
            target=run_jin10_sentinel_safe,
            kwargs={"state_path": state_path, "timeout": timeout},
            daemon=True,
            name="Jin10NewsSentinel",
        )
        t.start()
        return t
    except Exception:
        return None


if __name__ == "__main__":
    print(json.dumps(run_jin10_sentinel_safe(dry_run=True), ensure_ascii=False))
