# -*- coding: utf-8 -*-
"""Guards for dashboard page-open behavior.

These checks lock the loading changes: chart libraries must not block the
shell or tables, the terminal must not embed the predict-page cycle payload,
and live-price sync markers stay intact.
"""
import json
import os
import re
import tempfile
import unittest

import page_load
import display_time

ROOT = os.path.dirname(os.path.abspath(__file__))


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        return handle.read()


class TestPageLoadHelpers(unittest.TestCase):
    def test_plotly_cdn_fragment_is_not_parser_blocking(self):
        fragment = (
            "<script>window.PlotlyConfig = {MathJaxConfig: 'local'};</script>"
            '<script charset="utf-8" src="https://cdn.plot.ly/plotly-2.27.0.min.js" '
            'integrity="sha256-abc" crossorigin="anonymous"></script>'
            '<div id="chart1" class="plotly-graph-div" style="height:100%; width:100%;"></div>'
            "<script>Plotly.newPlot('chart1', [], {});</script>"
        )
        out = page_load.defer_plotly_cdn(fragment)
        self.assertNotRegex(out, r'<script[^>]+src="https://cdn\.plot\.ly')
        self.assertIn('type="text/plain"', out)
        self.assertIn("Plotly.newPlot", out)
        self.assertIn("cdn.plot.ly/plotly-2.27.0.min.js", out)
        self.assertIn("sha256-abc", out)
        self.assertIn("图表库加载失败", out)
        self.assertLess(out.find('type="text/plain"'), out.find("requestAnimationFrame"))

    def test_terminal_embed_drops_unused_cycle_payload(self):
        full = {
            "by_ticker": {"NVDA": {"calibrated_win_rate_pct": 60, "calibrated_short_win_rate_pct": 40}},
            "top10": [{"ticker": "NVDA"}],
            "top_long": [{"ticker": "NVDA"}] * 5,
            "top_short": [{"ticker": "SQQQ"}] * 5,
            "all_predictions": [{"ticker": "NVDA"}],
            "time_cycle_projections": {"NVDA": {"projection": [1, 2, 3]}},
            "table1": [1],
        }
        embed = page_load.terminal_ai_embed(full)
        self.assertEqual(set(embed), {"by_ticker", "top10", "top_long", "top_short"})
        self.assertNotIn("time_cycle_projections", json.dumps(embed))

    def test_refresh_snippet_busts_cache_and_waits_for_idle(self):
        snippet = page_load.client_refresh_snippet(180000, save_watchlist=True, clear_symbol_params=True)
        self.assertIn("180000", snippet)
        self.assertIn('searchParams.set("_t"', snippet)
        self.assertIn("futu_watchlist_scroll", snippet)
        self.assertIn('searchParams.delete("symbol")', snippet)
        self.assertIn("document.hidden", snippet)
        self.assertNotIn("http-equiv", snippet)


class TestShippedPages(unittest.TestCase):
    def test_chart_pages_do_not_block_on_plotly(self):
        for name in ("macro_radar.html", "stocks_radar.html", "backtest_report_5y.html"):
            html = _read(name)
            self.assertNotRegex(
                html,
                r'<script[^>]+src="https://cdn\.plot\.ly',
                name,
            )
            self.assertIn("requestAnimationFrame", html, name)
            self.assertIn("图表库加载失败", html, name)
            self.assertNotIn('http-equiv="refresh"', html, name)
            self.assertIn('href="futu_pro_terminal.html"', html, name)

    def test_terminal_shell_is_not_blocked_by_tradingview(self):
        html = _read("futu_pro_terminal.html")
        self.assertEqual(html, _read("index.html"))
        self.assertNotIn('src="https://s3.tradingview.com/tv.js"', html)
        self.assertIn("ensureTradingView", html)
        self.assertIn("主图未能从 TradingView 加载", html)
        self.assertIn('"timezone": "Asia/Shanghai"', html)
        self.assertIn("timezone=Asia%2FShanghai", html)
        self.assertIn('const targetSession = "extended"', html)
        self.assertIn("sessionId=${targetSession}", html)
        self.assertIn('"extended_hours": true', html)
        self.assertNotIn("America/New_York", html)
        self.assertNotIn('http-equiv="refresh"', html)
        self.assertIn('href="macro_radar.html"', html)
        self.assertIn('href="predict_radar_5y.html"', html)
        match = re.search(r"const AI_PREDICTIONS\s*=\s*(\{.*?\});", html, re.S)
        self.assertIsNotNone(match)
        ai = json.loads(match.group(1))
        self.assertIn("top_long", ai)
        self.assertIn("top_short", ai)
        self.assertGreaterEqual(len(ai["top_long"]), 5)
        self.assertGreaterEqual(len(ai["by_ticker"]), 70)
        self.assertNotIn("time_cycle_projections", ai)
        self.assertNotIn("all_predictions", ai)
        nvda = ai["by_ticker"]["NVDA"]
        self.assertIn("calibrated_win_rate_pct", nvda)
        self.assertIn("calibrated_short_win_rate_pct", nvda)

    def test_predict_page_keeps_cycle_data_and_defers_its_script(self):
        html = _read("predict_radar_5y.html")
        self.assertIn('id="predict-app"', html)
        self.assertIn('type="text/plain"', html)
        self.assertIn('id="predict-app-boot"', html)
        self.assertIn("DOMContentLoaded", html)
        self.assertIn("switchT4View(savedView)", html)
        self.assertIn('href="futu_pro_terminal.html"', html)
        key = "const timeCycleData = "
        idx = html.find(key)
        self.assertGreaterEqual(idx, 0)
        data, _ = json.JSONDecoder().raw_decode(html[idx + len(key):])
        self.assertGreaterEqual(len(data), 70)
        self.assertIn("SOXL", data)
        self.assertIn("BTC-USD", data)


class TestGeneratorsEmitTheSameLoadingStrategy(unittest.TestCase):
    def test_macro_and_stocks_generators_defer_plotly(self):
        import integrated_market_radar as imr

        weather = {
            "weather": "阴天",
            "icon": "⛅",
            "color": "#9ca3af",
            "desc": "测试",
            "tactics": "观望",
            "qqqm_bias": 0.0,
            "qqqm_ma20_bias": 0.0,
            "qqqm_pct": 0.0,
            "smh_bias": 0.0,
            "smh_ma20_bias": 0.0,
            "smh_pct": 0.0,
            "vixy_last": 1.0,
            "vixy_ma20_bias": 0.0,
            "vixy_pos_60": 50.0,
            "is_vix_panic": False,
            "tlt_bias": 0.0,
            "tlt_color": "#9ca3af",
            "tlt_state": "中性",
        }
        orig_macro = imr.MACRO_HTML_FILE
        orig_stocks = imr.STOCKS_HTML_FILE
        with tempfile.TemporaryDirectory() as tmp:
            imr.MACRO_HTML_FILE = os.path.join(tmp, "macro_radar.html")
            imr.STOCKS_HTML_FILE = os.path.join(tmp, "stocks_radar.html")
            try:
                imr.generate_macro_radar_html({}, weather, {}, update_time_str="11-20 12:00 北京时间")
                imr.generate_stocks_radar_html([], {}, update_time_str="11-20 12:00 北京时间")
                with open(imr.MACRO_HTML_FILE, encoding="utf-8") as handle:
                    macro = handle.read()
                with open(imr.STOCKS_HTML_FILE, encoding="utf-8") as handle:
                    stocks = handle.read()
            finally:
                imr.MACRO_HTML_FILE = orig_macro
                imr.STOCKS_HTML_FILE = orig_stocks
        for html, label in ((macro, "macro"), (stocks, "stocks")):
            self.assertNotRegex(html, r'<script[^>]+src="https://cdn\.plot\.ly', label)
            self.assertIn("requestAnimationFrame", html, label)
            self.assertIn("北京时间", html, label)
            self.assertIn('searchParams.set("_t"', html, label)
            self.assertIn('href="futu_pro_terminal.html"', html, label)

    def test_futu_generator_keeps_shanghai_chart_and_slim_ai(self):
        import create_futu_terminal as cft

        html = cft.build_futu_terminal_html(
            [],
            {},
            {
                "weather": "阴天",
                "icon": "⛅",
                "color": "#9ca3af",
                "desc": "测试",
                "tactics": "观望",
            },
            update_time_str="11-20 12:00 北京时间",
            target_watchlist=[],
            terminal_notifications=[],
            sync_side_effects=False,
        )
        self.assertNotIn('src="https://s3.tradingview.com/tv.js"', html)
        self.assertIn("ensureTradingView", html)
        self.assertIn('"timezone": "Asia/Shanghai"', html)
        self.assertIn("timezone=Asia%2FShanghai", html)
        self.assertEqual(display_time.TV_TIMEZONE, "Asia/Shanghai")
        self.assertNotIn("time_cycle_projections", html)
        self.assertIn("北京时间", html)


if __name__ == "__main__":
    unittest.main()
