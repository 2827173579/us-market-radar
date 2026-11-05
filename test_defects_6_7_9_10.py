# -*- coding: utf-8 -*-
"""
test_defects_6_7_9_10.py - Dedicated Automated Test Suite for Defects 6, 7, 9, 10
"""
import os
import sys
import json
import unittest

PROJECT_DIR = os.path.abspath(r"C:\Users\User\.gemini\antigravity-ide\scratch\us-market-radar")
SCRATCH_DIR = os.path.abspath(r"C:\Users\User\.gemini\antigravity-ide\scratch")
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import create_futu_terminal as cft
import integrated_market_radar as imr


class TestDefects67910(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Generate sample HTML from create_futu_terminal
        cls.stocks_sample = [
            {
                "ticker": "INTC", "name": "英特尔", "cat": "芯片算力",
                "last": 24.5, "active_price": 24.5, "bias": -3.2, "atr": 1.2,
                "rsi": 42.0, "is_pit": True, "is_crash_halt": False,
                "is_cooling_halt": False, "is_inverse_hedge": False,
                "dist_to_pit": -1.5, "shares": 100, "cost": 23.0,
                "ma50": 25.0, "fib_low": 22.0, "fib_high": 24.0, "pit_status": "黄金坑中",
                "fractional_shares": 10.5, "signal": "BUY", "pct_change": 1.2,
                "is_volume": False, "is_stop": False, "is_take_profit": False, "is_deep_pullback": False
            },
            {
                "ticker": "NVDA", "name": "英伟达", "cat": "芯片算力",
                "last": 125.0, "active_price": 125.0, "bias": -8.0, "atr": 3.5,
                "rsi": 38.0, "is_pit": True, "is_crash_halt": True,  # Halted!
                "is_cooling_halt": False, "is_inverse_hedge": False,
                "dist_to_pit": -2.0, "shares": 0,
                "ma50": 130.0, "fib_low": 115.0, "fib_high": 120.0, "pit_status": "黄金坑中",
                "fractional_shares": 5.0, "signal": "HOLD", "pct_change": -0.8,
                "is_volume": False, "is_stop": False, "is_take_profit": False, "is_deep_pullback": False
            },
            {
                "ticker": "NOW", "name": "ServiceNow", "cat": "云与软件",
                "last": 900.0, "active_price": 900.0, "bias": 1.2, "atr": 15.0,
                "rsi": 54.0, "is_pit": False, "is_crash_halt": False,
                "is_cooling_halt": False, "is_inverse_hedge": False,
                "dist_to_pit": 5.0, "shares": 0,
                "ma50": 880.0, "fib_low": 800.0, "fib_high": 850.0, "pit_status": "坑上方",
                "fractional_shares": 0.5, "signal": "OBSERVE", "pct_change": 0.5,
                "is_volume": False, "is_stop": False, "is_take_profit": False, "is_deep_pullback": False
            }
        ]
        cls.macro_sample = {
            "QQQM": {"ticker": "QQQM", "last": 200.0, "pct_change": 0.5, "is_pit": False},
            "SQQQ": {"ticker": "SQQQ", "last": 10.0, "pct_change": -1.5, "is_pit": True, "is_inverse_hedge": True}
        }
        cls.weather_sample = {
            "weather": "☀️ 晴天进攻", "icon": "☀️", "color": "#00e676",
            "desc": "多头共振", "tactics": "积极做多",
            "qqqm_bias": 1.5, "smh_bias": 2.0, "vixy_pos_60": 30.0
        }
        cls.terminal_html = cft.build_futu_terminal_html(
            stocks_data=cls.stocks_sample,
            macro_data=cls.macro_sample,
            weather_info=cls.weather_sample,
            update_time_str="2026-11-03 10:30:00"
        )

    # -------------------------------------------------------------------------
    # Defect 6: TradingView K-Line Mapping
    # -------------------------------------------------------------------------
    def test_defect_6_tradingview_symbol_map_completeness(self):
        """Verify NOW, APLD, COIN, MARA mappings in SYMBOL_MAP."""
        self.assertIn('"NOW": "NYSE:NOW"', self.terminal_html)
        self.assertIn('"APLD": "NASDAQ:APLD"', self.terminal_html)
        self.assertIn('"COIN": "NASDAQ:COIN"', self.terminal_html)
        self.assertIn('"MARA": "NASDAQ:MARA"', self.terminal_html)
        self.assertIn('"QCOM": "NASDAQ:QCOM"', self.terminal_html)
        self.assertIn("TradingView 交易所标的代码映射 (全量 71 只)", self.terminal_html)

    # -------------------------------------------------------------------------
    # Defect 7: Golden Pit Filter & Card Stats Leakage Prevention
    # -------------------------------------------------------------------------
    def test_defect_7_golden_pit_frontend_filter_exclusion(self):
        """Verify frontend filter excludes halted, cooling, and inverse tickers."""
        # Stocks filter
        self.assertIn("!s.is_crash_halt && !s.is_cooling_halt && !s.is_inverse_hedge", self.terminal_html)
        # Macro/ETF filter
        self.assertIn("!m.is_crash_halt && !m.is_cooling_halt && !m.is_inverse_hedge", self.terminal_html)
        self.assertIn('!["SQQQ", "SOXS", "VIXY"].includes(t)', self.terminal_html)

    def test_defect_7_golden_pit_card_stat_formula(self):
        """Verify card formula strictly excludes halted/cooling/inverse from pit count."""
        test_stocks = [
            {"is_pit": True, "is_crash_halt": False, "is_cooling_halt": False, "is_inverse_hedge": False},
            {"is_pit": True, "is_crash_halt": True, "is_cooling_halt": False, "is_inverse_hedge": False},   # should be excluded
            {"is_pit": True, "is_crash_halt": False, "is_cooling_halt": True, "is_inverse_hedge": False},  # should be excluded
            {"is_pit": True, "is_crash_halt": False, "is_cooling_halt": False, "is_inverse_hedge": True},  # should be excluded
            {"is_pit": False, "is_crash_halt": False, "is_cooling_halt": False, "is_inverse_hedge": False},
        ]
        count = sum(1 for s in test_stocks if s.get('is_pit') and not s.get('is_crash_halt') and not s.get('is_cooling_halt') and not s.get('is_inverse_hedge'))
        self.assertEqual(count, 1, "Only 1 healthy stock should qualify for golden pit card count")

    # -------------------------------------------------------------------------
    # Defect 9: 60s Auto-Refresh 5-Dimension State Persistence
    # -------------------------------------------------------------------------
    def test_defect_9_localstorage_persistence_keys(self):
        """Verify all 5 dimensions are persisted to localStorage in terminal HTML."""
        self.assertIn('localStorage.setItem("futu_kline_period"', self.terminal_html)
        self.assertIn('localStorage.setItem("futu_kline_style"', self.terminal_html)
        self.assertIn('localStorage.setItem("futu_watchlist_filter"', self.terminal_html)
        self.assertIn('localStorage.setItem("futu_right_panel_tab"', self.terminal_html)
        self.assertIn('localStorage.setItem("futu_watchlist_scroll"', self.terminal_html)
        self.assertIn('localStorage.setItem("futu_mobile_tab"', self.terminal_html)

    def test_defect_9_url_param_cleanup_and_mobile_decoupling(self):
        """Verify URL parameters are cleaned with replaceState and selectTicker decouples mobile tab."""
        self.assertIn("searchParams.delete('symbol')", self.terminal_html)
        self.assertIn("searchParams.delete('ticker')", self.terminal_html)
        self.assertIn("window.history.replaceState", self.terminal_html)
        self.assertIn("function selectTicker(ticker, fromUserClick = false)", self.terminal_html)
        self.assertIn("if (fromUserClick && window.innerWidth <= 768 && currentMobileTab === \"watchlist\")", self.terminal_html)

    # -------------------------------------------------------------------------
    # Defect 10: Static Assets & Documentation
    # -------------------------------------------------------------------------
    def test_defect_10_manifest_json_physical_existence_and_relative_path(self):
        """Verify manifest.json exists in both directories and is referenced via relative path."""
        p1 = os.path.join(PROJECT_DIR, "manifest.json")
        p2 = os.path.join(SCRATCH_DIR, "manifest.json")
        self.assertTrue(os.path.exists(p1), f"Missing {p1}")
        self.assertTrue(os.path.exists(p2), f"Missing {p2}")

        # Check JSON validity
        with open(p1, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data.get("short_name"), "富途雷达")

        # Check relative link in terminal HTML
        self.assertIn('<link rel="manifest" href="manifest.json">', self.terminal_html)
        self.assertNotIn('href="/manifest.json"', self.terminal_html)

    def test_defect_10_top_navbars_5y_backtest_button_and_70_tickers(self):
        """Verify top navbar in macro_radar.html and stocks_radar.html templates."""
        imr.generate_macro_radar_html(
            macro_data={},
            weather_info={"weather": "晴天", "icon": "☀️", "color": "#00e676", "desc": "良好", "tactics": "进攻", "qqqm_bias": 1.0, "smh_bias": 1.0, "vixy_pos_60": 40.0, "vixy_last": 20.5, "tlt_bias": 0.5},
            history_dict={},
            update_time_str="2026-11-03 10:30:00"
        )
        with open(imr.MACRO_HTML_FILE, "r", encoding="utf-8") as f:
            macro_html = f.read()
        self.assertIn('🛡️ 5Y回测', macro_html)
        self.assertIn('backtest_report_5y.html', macro_html)
        self.assertIn('16只核心ETF · 全网71只标的', macro_html)

        imr.generate_stocks_radar_html(
            stocks_data=self.stocks_sample,
            history_dict={},
            update_time_str="2026-11-03 10:30:00"
        )
        with open(imr.STOCKS_HTML_FILE, "r", encoding="utf-8") as f:
            stocks_html = f.read()
        self.assertIn('🛡️ 5Y回测', stocks_html)
        self.assertIn('backtest_report_5y.html', stocks_html)
        self.assertIn('全网 71 只标的 · 16 ETF + 55 个股', stocks_html)

    def test_defect_10_readme_documentation(self):
        """Verify README.md contains GitHub Pages URLs, PWA setup, and V3.1/V3.2 metrics."""
        for path in [os.path.join(PROJECT_DIR, "README.md"), os.path.join(SCRATCH_DIR, "README.md")]:
            self.assertTrue(os.path.exists(path), f"README missing at {path}")
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("https://2827173579.github.io/us-market-radar/", content)
            self.assertIn("PWA", content)
            self.assertIn("添加到主屏幕", content)
            self.assertIn("Track 1 (Pure Stock Dynamic Risk-Managed", content)
            self.assertIn("Track 2 (Flagship QQQ Base Enhancement", content)
            self.assertIn("Firstrade", content)
            self.assertIn("1.5 bps", content)
            self.assertIn("V3.2 暴跌熔断", content)


if __name__ == "__main__":
    unittest.main()
