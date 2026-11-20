# -*- coding: utf-8 -*-
"""User-facing clocks use Asia/Shanghai. US session logic stays on America/New_York.

Page footers, notification chips, Discord report clocks, and git sync subjects
are formatted here and always include the label 北京时间.
TradingView's displayed chart timezone is also Beijing (Asia/Shanghai).

Do not use this module for get_us_market_session(), trading-day/holiday checks,
or intraday bar alignment. Those stay on America/New_York.
"""
from datetime import datetime, timezone, timedelta

BEIJING_TZ_NAME = "Asia/Shanghai"
EASTERN_TZ_NAME = "America/New_York"
BEIJING_LABEL = "北京时间"
EASTERN_LABEL = "美东时间"
PAGE_FMT = "%Y-%m-%d %H:%M:%S"
# TradingView widget + widgetembed fallback. sessionId stays "extended"
# (US pre/post hours). Only the axis clock moves to Beijing.
TV_TIMEZONE = "Asia/Shanghai"
TV_TIMEZONE_QUERY = "Asia%2FShanghai"


def beijing_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(BEIJING_TZ_NAME)
    except Exception:
        return timezone(timedelta(hours=8))


def eastern_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(EASTERN_TZ_NAME)
    except Exception:
        return timezone(timedelta(hours=-5))


def _as_zone(dt, tz):
    if dt is None:
        return datetime.now(tz)
    if dt.tzinfo is None:
        raise ValueError("display clock requires a timezone-aware datetime")
    return dt.astimezone(tz)


def format_beijing(dt=None, with_label=True):
    """YYYY-MM-DD HH:MM:SS 北京时间. dt must be aware when provided."""
    text = _as_zone(dt, beijing_tz()).strftime(PAGE_FMT)
    if with_label:
        return f"{text} {BEIJING_LABEL}"
    return text


def format_beijing_short(dt=None):
    """MM-DD HH:MM 北京时间 for notification chips and WeChat cards."""
    text = _as_zone(dt, beijing_tz()).strftime("%m-%d %H:%M")
    return f"{text} {BEIJING_LABEL}"


def page_update_stamp(dt=None):
    """Stamp written into dashboard footers and sync subjects."""
    return format_beijing(dt, with_label=True)


def compact_beijing_stamp(stamp=None):
    """Shorten a page stamp to MM-DD HH:MM 北京时间. Empty input uses now."""
    if not stamp:
        return format_beijing_short()
    bare = str(stamp).replace(BEIJING_LABEL, "").strip()
    if len(bare) >= 16 and bare[4] == "-" and bare[10] == " ":
        return f"{bare[5:16]} {BEIJING_LABEL}"
    if bare:
        return f"{bare} {BEIJING_LABEL}"
    return format_beijing_short()


def log_clock():
    """HH:MM:SS 北京时间 for operator logs that sit next to the dashboards."""
    text = _as_zone(None, beijing_tz()).strftime("%H:%M:%S")
    return f"{text} {BEIJING_LABEL}"
