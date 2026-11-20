# -*- coding: utf-8 -*-
"""Beijing display clocks vs America/New_York session logic.

Frozen instant: 2026-11-20 04:54:56 UTC
  America/New_York (EST): 2026-11-19 23:54:56 Thursday -> OVERNIGHT
  Asia/Shanghai:          2026-11-20 12:54:56 Friday    -> would be REGULAR
"""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import display_time
import create_futu_terminal as cft
import integrated_market_radar as imr
import ml_predictor_5y

FROZEN_UTC = datetime(2026, 11, 20, 4, 54, 56, tzinfo=timezone.utc)
BEIJING_STAMP = "2026-11-20 12:54:56 北京时间"
ET_WALL = "2026-11-19 23:54:56"


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FROZEN_UTC.replace(tzinfo=None)
        return FROZEN_UTC.astimezone(tz)


def _weather():
    return {
        "weather": "阴天防守",
        "icon": "⛅",
        "color": "#9ca3af",
        "desc": "测试风向",
        "tactics": "观望",
        "qqqm_bias": 0.0,
        "smh_bias": 0.0,
        "vixy_last": 20.0,
        "tlt_bias": 0.0,
    }


class TestBeijingDisplayClock(unittest.TestCase):
    def test_page_stamp_is_shanghai_with_label(self):
        with mock.patch.object(display_time, "datetime", _FrozenDateTime):
            stamp = display_time.page_update_stamp()
            short = display_time.compact_beijing_stamp(stamp)
        self.assertEqual(stamp, BEIJING_STAMP)
        self.assertIn("北京时间", stamp)
        self.assertNotIn(ET_WALL, stamp)
        self.assertEqual(short, "11-20 12:54 北京时间")
        aware = display_time.format_beijing(FROZEN_UTC)
        self.assertEqual(aware, BEIJING_STAMP)
        zone = display_time.beijing_tz()
        self.assertIn("Shanghai", getattr(zone, "key", str(zone)))

    def test_market_session_stays_on_new_york(self):
        with mock.patch.object(imr, "datetime", _FrozenDateTime):
            code, _name = imr.get_us_market_session()
        self.assertEqual(code, "OVERNIGHT")
        et = ml_predictor_5y._to_et(FROZEN_UTC)
        self.assertEqual(et.strftime("%Y-%m-%d %H:%M:%S"), ET_WALL)
        self.assertIn("New_York", getattr(et.tzinfo, "key", str(et.tzinfo)))
        beijing = FROZEN_UTC.astimezone(display_time.beijing_tz())
        self.assertEqual(beijing.strftime("%Y-%m-%d %H:%M:%S"), "2026-11-20 12:54:56")
        self.assertEqual(beijing.weekday(), 4)
        self.assertTrue(9 <= beijing.hour < 16)

    def test_notification_chip_is_beijing_and_id_stays_et_date(self):
        with mock.patch.object(imr, "load_target_watchlist", return_value=[]), \
             mock.patch.object(imr, "load_terminal_notifications", return_value=[]), \
             mock.patch.object(imr, "save_terminal_notifications", return_value=None):
            notes = imr.generate_and_sync_notifications(
                {},
                [],
                {"weather": "阴天", "desc": "测试", "tactics": "观望"},
                ET_WALL,
                session_code="OVERNIGHT",
                display_stamp=BEIJING_STAMP,
            )
        weather = [n for n in notes if n.get("category") == "macro_weather"]
        self.assertEqual(len(weather), 1)
        self.assertTrue(weather[0]["id"].endswith("2026-11-19"))
        self.assertEqual(weather[0]["time"], "11-20 12:54 北京时间")
        for note in notes:
            self.assertIn("北京时间", note["time"])
            self.assertNotIn("23:54", note["time"])

    def test_generated_html_uses_beijing_stamp_and_shanghai_chart(self):
        orig_macro = imr.MACRO_HTML_FILE
        orig_stocks = imr.STOCKS_HTML_FILE
        with mock.patch.object(display_time, "datetime", _FrozenDateTime), tempfile.TemporaryDirectory() as tmp:
            imr.MACRO_HTML_FILE = os.path.join(tmp, "macro_radar.html")
            imr.STOCKS_HTML_FILE = os.path.join(tmp, "stocks_radar.html")
            try:
                imr.generate_macro_radar_html({}, _weather(), {}, update_time_str="")
                imr.generate_stocks_radar_html([], {}, update_time_str="")
                futu_html = cft.build_futu_terminal_html(
                    [],
                    {},
                    _weather(),
                    update_time_str="",
                    target_watchlist=[],
                    terminal_notifications=[],
                    session_code="OVERNIGHT",
                    session_name="🌌 美股夜盘",
                    sync_side_effects=False,
                )
                with open(imr.MACRO_HTML_FILE, encoding="utf-8") as f:
                    macro_html = f.read()
                with open(imr.STOCKS_HTML_FILE, encoding="utf-8") as f:
                    stocks_html = f.read()
            finally:
                imr.MACRO_HTML_FILE = orig_macro
                imr.STOCKS_HTML_FILE = orig_stocks

        for html, label in ((macro_html, "最近更新"), (stocks_html, "最近更新")):
            self.assertIn(f"{label}: {BEIJING_STAMP}", html)
            self.assertNotIn(ET_WALL, html)
            self.assertIn("北京时间", html)
        self.assertIn(f"数据更新于: {BEIJING_STAMP}", futu_html)
        self.assertNotIn(ET_WALL, futu_html)
        self.assertIn('"timezone": "Asia/Shanghai"', futu_html)
        self.assertIn("timezone=Asia%2FShanghai", futu_html)
        self.assertNotIn("America/New_York", futu_html)
        self.assertNotIn("America%2FNew_York", futu_html)
        self.assertIn('"extended_hours": true', futu_html)
        self.assertIn("sessionId=${targetSession}", futu_html)
        self.assertIn('const targetSession = "extended"', futu_html)


if __name__ == "__main__":
    unittest.main()
