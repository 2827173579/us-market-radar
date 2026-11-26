# -*- coding: utf-8 -*-
"""
美股官方交易日历与半日市 (Early Close) 统一时间源 (us_market_calendar.py)
==========================================================================
零外部重型依赖，供 copy_trade_engine / copy_trade_kol_engine / integrated_market_radar /
intraday_path_predictor / ml_predictor_5y / ai_commentary_engine 共享唯一真值来源：

1. 常规美股休市日 (NYSE / NASDAQ):
   元旦、马丁路德金日、总统日、耶稣受难日、阵亡将士纪念日、六月节、独立日、劳工节、感恩节、圣诞节。
2. 官方美股半日市 (Early Close，美东 13:00 ET 收盘，常规盘后延伸至 17:00 ET):
   - 感恩节次日黑色星期五（如 2025-11-28、2026-11-27、2027-11-26）
   - 平安夜 12-24（当 12-24 为非休市工作日时，如 2025-12-24、2026-12-24、2027-12-24）
   - 独立日前日 07-03（当 07-03 为非休市工作日时，如 2025-07-03、2028-07-03；若 07-04 为周六则 07-03 为全天休市而非半日市）
3. 动态锚定收盘偏移：
   - 尾盘强平/禁开窗：close_min - 10（正常日 15:50 ET / 半日市 12:50 ET）
   - V5.5.1 尾盘停开新仓：close_min - 40（正常日 15:20 ET / 半日市 12:20 ET）
   - 盘后 AI 重算：close_min + 5（正常日 16:05 ET / 半日市 13:05 ET）
   - 60 日 5m 缓存刷新：close_min + 15（正常日 16:15 ET / 半日市 13:15 ET）
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Dict, Optional, Tuple

try:
    from zoneinfo import ZoneInfo

    NY_TZ = ZoneInfo("America/New_York")
except Exception:
    NY_TZ = timezone(timedelta(hours=-5))

REGULAR_OPEN_MIN = 9 * 60 + 30        # 09:30 ET
REGULAR_CLOSE_MIN = 16 * 60           # 16:00 ET
EARLY_CLOSE_MIN = 13 * 60             # 13:00 ET
REGULAR_AFTER_HOURS_END_MIN = 20 * 60 # 20:00 ET
EARLY_AFTER_HOURS_END_MIN = 17 * 60   # 17:00 ET
PRE_MARKET_OPEN_MIN = 4 * 60          # 04:00 ET

# 显式登记近年的官方半日市日期（同时结合算法规则支持任意年份）
EXPLICIT_US_EARLY_CLOSE_DATES = frozenset({
    date(2025, 7, 3),
    date(2025, 11, 28),
    date(2025, 12, 24),
    date(2026, 11, 27),
    date(2026, 12, 24),
    date(2027, 11, 26),
    date(2027, 12, 24),
    date(2028, 7, 3),
    date(2028, 11, 24),
})


def to_et_datetime(now_et: Optional[datetime] = None) -> datetime:
    if now_et is None:
        return datetime.now(NY_TZ)
    if now_et.tzinfo is None:
        try:
            return now_et.replace(tzinfo=NY_TZ)
        except Exception:
            return now_et
    return now_et.astimezone(NY_TZ)


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm for Easter Sunday (to compute Good Friday)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed_date(d: date) -> date:
    if d.weekday() == 5:  # Saturday -> Friday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday -> Monday
        return d + timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1) - timedelta(days=1)
    offset = (nxt.weekday() - weekday) % 7
    return nxt - timedelta(days=offset)


@lru_cache(maxsize=32)
def get_us_market_holidays(year: int) -> frozenset:
    """返回指定年份美股 NYSE/NASDAQ 常规全天休市日集合。"""
    hols = {
        _observed_date(date(year, 1, 1)),          # New Year's Day
        _nth_weekday(year, 1, 0, 3),               # MLK Day (3rd Mon Jan)
        _nth_weekday(year, 2, 0, 3),               # Presidents' Day (3rd Mon Feb)
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),                 # Memorial Day (last Mon May)
        _observed_date(date(year, 6, 19)),         # Juneteenth
        _observed_date(date(year, 7, 4)),          # Independence Day
        _nth_weekday(year, 9, 0, 1),               # Labor Day (1st Mon Sep)
        _nth_weekday(year, 11, 3, 4),              # Thanksgiving (4th Thu Nov)
        _observed_date(date(year, 12, 25)),        # Christmas Day
    }
    if date(year + 1, 1, 1).weekday() == 5:
        hols.add(date(year, 12, 31))
    return frozenset(hols)


def is_us_market_holiday(d: date) -> bool:
    """判断指定日期是否为美股工作日全天法定休市日（如感恩节、圣诞节、独立日等）。"""
    if d.weekday() >= 5:
        return False
    return d in get_us_market_holidays(d.year)


def is_us_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in get_us_market_holidays(d.year)


@lru_cache(maxsize=32)
def get_us_early_close_days(year: int) -> frozenset:
    """
    返回指定年份美股官方半日市 (13:00 ET 收盘, 盘后至 17:00 ET) 日期集合：
    - 感恩节次日黑色星期五 (4th Thu Nov + 1d)
    - 平安夜 12-24（若为正常交易日）
    - 独立日前日 07-03（若为正常交易日且非独立日调休全天休市）
    """
    hols = get_us_market_holidays(year)
    cands = {
        _nth_weekday(year, 11, 3, 4) + timedelta(days=1),  # Black Friday
        date(year, 12, 24),                                # Christmas Eve
        date(year, 7, 3),                                  # Day before Independence Day
    }
    for d in EXPLICIT_US_EARLY_CLOSE_DATES:
        if d.year == year:
            cands.add(d)
    return frozenset(d for d in cands if d.weekday() < 5 and d not in hols)


def is_us_early_close_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d in get_us_early_close_days(d.year)


def get_us_market_close_minutes(d: date) -> int:
    """返回当日美股常规盘收盘分钟数 (正常日 16:00=960，半日市 13:00=780)。"""
    return EARLY_CLOSE_MIN if is_us_early_close_day(d) else REGULAR_CLOSE_MIN


def get_us_after_hours_end_minutes(d: date) -> int:
    """返回当日美股常规盘后结束分钟数 (正常日 20:00=1200，半日市 17:00=1020)。"""
    return EARLY_AFTER_HOURS_END_MIN if is_us_early_close_day(d) else REGULAR_AFTER_HOURS_END_MIN


def get_us_session_schedule(d: date) -> Dict[str, int]:
    """返回当日全套动态收盘偏移时间点（单位：美东分钟数）。"""
    early = is_us_early_close_day(d)
    close_min = EARLY_CLOSE_MIN if early else REGULAR_CLOSE_MIN
    ah_end_min = EARLY_AFTER_HOURS_END_MIN if early else REGULAR_AFTER_HOURS_END_MIN
    return {
        "is_trading_day": 1 if is_us_trading_day(d) else 0,
        "is_early_close": 1 if early else 0,
        "open_min": REGULAR_OPEN_MIN,
        "close_min": close_min,
        "after_hours_end_min": ah_end_min,
        "force_exit_min": close_min - 10,     # 15:50 ET / 半日市 12:50 ET
        "kol_entry_end_min": close_min - 40,  # 15:20 ET / 半日市 12:20 ET
        "ml_refresh_min": close_min + 5,      # 16:05 ET / 半日市 13:05 ET
        "cache_refresh_min": close_min + 15,  # 16:15 ET / 半日市 13:15 ET
    }


def classify_us_market_session(
    now_et: Optional[datetime] = None,
    post_market_code: str = "AFTER_HOURS",
) -> Tuple[str, str]:
    """
    判断美东当前交易时段（精准支持全天法定休市日 + 半日市 13:00 ET 收盘、13:00~17:00 ET 盘后）：
    - 周末休市 (WEEKEND): 周五盘后结束后 (常规 20:00 / 半日市 17:00) ~ 周日 20:00 ET
    - 节假日休市 (HOLIDAY): 美股工作日全天法定休市日（如感恩节 2026-11-26、圣诞节等），全天不误报盘前/盘中
    - 盘前交易 (PRE_MARKET): 正常交易日 04:00 ~ 09:30 ET
    - 常规盘中 (REGULAR): 正常交易日 09:30 ~ close_min (16:00 / 半日市 13:00)
    - 盘后交易 (post_market_code): 正常交易日 close_min ~ ah_end_min (20:00 / 半日市 17:00)
    - 美股夜盘 (OVERNIGHT): 其余 24/5 夜间时段
    """
    et = to_et_datetime(now_et)
    d = et.date()
    weekday = d.weekday()  # 0=Mon ... 4=Fri, 5=Sat, 6=Sun
    et_min = et.hour * 60 + et.minute
    close_min = get_us_market_close_minutes(d)
    ah_end_min = get_us_after_hours_end_minutes(d)

    if weekday == 5 or (weekday == 4 and et_min >= ah_end_min) or (weekday == 6 and et_min < 20 * 60):
        return "WEEKEND", "☕ 周末休市"
    if weekday == 6 and et_min >= 20 * 60:
        return "OVERNIGHT", "🌌 美股夜盘"
    if is_us_market_holiday(d):
        return "HOLIDAY", "🏖️ 节假日休市"
    if REGULAR_OPEN_MIN <= et_min < close_min:
        return "REGULAR", "🟢 常规盘中"
    if PRE_MARKET_OPEN_MIN <= et_min < REGULAR_OPEN_MIN:
        return "PRE_MARKET", "🌅 盘前交易"
    if close_min <= et_min < ah_end_min:
        return post_market_code, "🌙 盘后交易"
    return "OVERNIGHT", "🌌 美股夜盘"
