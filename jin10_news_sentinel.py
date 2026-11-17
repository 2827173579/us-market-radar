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
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

HTTP_TIMEOUT = 3

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
    """优先读取 DISCORD_JIN10_WEBHOOK_URL，未配置时回退至 DISCORD_CLAW_WEBHOOK_URL。"""
    return (
        os.environ.get("DISCORD_JIN10_WEBHOOK_URL", "").strip()
        or os.environ.get("DISCORD_CLAW_WEBHOOK_URL", "").strip()
    )


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
    if s in EMPTY_ACTUAL_VALUES:
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
