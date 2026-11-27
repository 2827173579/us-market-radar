# -*- coding: utf-8 -*-
"""
Frontend UX Regression Tests: Non-Intrusive Silent Version Detection,
Auto-Reload Toggle (Default OFF), 20s Active Idle Guard & Seamless UI State Restore.
"""

import http.server
import os
import re
import socketserver
import threading
import unittest

import create_futu_terminal as cft


def _build_minimal_terminal_html(build_ts_override=None):
    stocks = [
        {
            "ticker": "NVDA",
            "name": "英伟达",
            "cat": "芯片与半导体",
            "last": 149.30,
            "active_price": 149.30,
            "pct_change": -1.20,
            "vwap": 148.00,
            "rs_20d": 5.0,
        },
        {
            "ticker": "AMD",
            "name": "超微半导体",
            "cat": "芯片与半导体",
            "last": 140.00,
            "active_price": 140.00,
            "pct_change": 1.10,
            "vwap": 139.00,
            "rs_20d": 3.0,
        },
    ]
    macro = {
        "QQQ": {
            "ticker": "QQQ",
            "name": "纳指100ETF",
            "cat": "大盘基准",
            "last": 633.93,
            "active_price": 633.93,
            "pct_change": -0.65,
            "vwap": 620.08,
            "rs_20d": 0.0,
        },
        "SPY": {
            "ticker": "SPY",
            "name": "标普500",
            "cat": "大盘基准",
            "last": 716.70,
            "active_price": 716.70,
            "pct_change": -0.29,
            "vwap": 707.36,
            "rs_20d": 0.0,
        },
    }
    html = cft.build_futu_terminal_html(
        stocks_data=stocks,
        macro_data=macro,
        weather_info={"weather": "暴雨空仓", "market_regime": "BEAR_DEFENSE"},
        session_code="OVERNIGHT",
    )
    if build_ts_override is not None:
        html = re.sub(
            r'(<meta\s+name="radar-build-ts"\s+content=")[^"]+(")',
            rf"\g<1>{build_ts_override}\g<2>",
            html,
        )
    return html


class TestTerminalNonIntrusiveRefresh(unittest.TestCase):
    def test_01_static_html_no_forced_meta_or_timer_reload(self):
        html = _build_minimal_terminal_html()
        # 1) 严禁保留 <meta http-equiv="refresh">
        self.assertNotRegex(html, r'http-equiv\s*=\s*["\']refresh["\']')
        # 2) 严禁保留旧版无条件 60s setTimeout 强刷代码块
        self.assertNotIn("客户端智能防缓存自动刷新", html)
        # 3) 必须包含静默版本检测、轻提示胶囊、自动刷新默认关开关、20秒活跃护栏与状态保存
        for token in (
            'name="radar-build-ts"',
            'id="btnNewUpdatePill"',
            'id="btnFooterUpdatePill"',
            'id="btnAutoReloadToggle"',
            "自动刷新: 关",
            "60秒静默检测",
            "radar_auto_reload_enabled",
            "radar_refresh_state_v1",
            "IDLE_GUARD_MS = 20000",
            "检测到操作，空闲后自动更新",
            "发现新行情 · 点击刷新",
        ):
            self.assertIn(token, html)

    def test_02_playwright_silent_check_idle_guard_and_state_restore(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            self.skipTest(f"playwright unavailable: {exc}")

        state = {
            "html": _build_minimal_terminal_html(build_ts_override="1700000000001"),
            "etag": '"v1-etag"',
            "last_mod": "Fri, 27 Nov 2026 05:00:00 GMT",
        }

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("ETag", state["etag"])
                self.send_header("Last-Modified", state["last_mod"])
                self.end_headers()

            def do_GET(self):
                path_only = self.path.split("?", 1)[0]
                if path_only in ("/", "/futu_pro_terminal.html"):
                    body = state["html"].encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("ETag", state["etag"])
                    self.send_header("Last-Modified", state["last_mod"])
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(204)
                    self.end_headers()

        httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
        port = httpd.server_address[1]
        srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        srv_thread.start()

        try:
            with sync_playwright() as p:
                browser = None
                last_err = None
                for launch_kwargs in (
                    {"headless": True, "channel": "msedge"},
                    {"headless": True, "channel": "chrome"},
                    {"headless": True},
                ):
                    try:
                        browser = p.chromium.launch(**launch_kwargs)
                        break
                    except Exception as exc:
                        last_err = exc
                if browser is None:
                    self.fail(f"Failed to launch Chromium/Edge via Playwright: {last_err}")
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()

                # 拦截外部 TradingView 脚本，避免测试依赖外网
                page.route("https://s3.tradingview.com/**", lambda route: route.fulfill(status=200, content_type="application/javascript", body="window.TradingView = { widget: function(){} };"))
                page.route("**/manifest.json", lambda route: route.fulfill(status=200, content_type="application/json", body="{}"))

                url = f"http://127.0.0.1:{port}/futu_pro_terminal.html"
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_function("() => !!window.__RADAR_REFRESH_GUARD__")

                # 1) 默认关闭自动刷新，且初始无更新胶囊隐藏
                self.assertEqual(page.locator("#autoReloadLabel").inner_text().strip(), "自动刷新: 关")
                self.assertEqual(page.locator("#btnAutoReloadToggle").get_attribute("aria-pressed"), "false")
                self.assertFalse(page.locator("#btnNewUpdatePill").is_visible())

                # 2) 安装导航拦截器，确认默认模式下即便远端 ETag 变化 + 超过 20s 空闲，也绝不强制刷新
                page.evaluate(
                    """() => {
                        window.__mockNavs = [];
                        window.__RADAR_REFRESH_GUARD__.setNavigatorFn((u) => window.__mockNavs.push(u));
                        window.__RADAR_REFRESH_GUARD__.setLastActivityAt(Date.now() - 60000);
                    }"""
                )
                state["etag"] = '"v2-etag"'
                state["html"] = _build_minimal_terminal_html(build_ts_override="1700000000002")
                res_poll = page.evaluate("() => window.__RADAR_REFRESH_GUARD__.checkRemoteVersionUpdate(false)")
                self.assertTrue(res_poll["checked"])
                self.assertTrue(res_poll["updated"])
                self.assertEqual(page.evaluate("() => window.__mockNavs.length"), 0)
                self.assertTrue(page.locator("#btnNewUpdatePill").is_visible())
                self.assertIn("发现新行情 · 点击刷新", page.locator("#newUpdatePillText").inner_text())

                # 3) 用户开启「自动刷新: 开」+ 处于活跃态（刚操作过）-> 必须被 20s Idle Guard 拦截延后
                page.click("#btnAutoReloadToggle")
                self.assertEqual(page.locator("#autoReloadLabel").inner_text().strip(), "自动刷新: 开")
                self.assertEqual(page.evaluate("() => localStorage.getItem('radar_auto_reload_enabled')"), "true")
                self.assertEqual(page.evaluate("() => window.__mockNavs.length"), 0)
                self.assertIn("检测到操作，空闲后自动更新", page.locator("#newUpdatePillText").inner_text())

                # 4) 即使超过 20s 无鼠标操作，只要打开了预警抽屉或搜索框聚焦 -> 仍必须被 Idle Guard 拦截
                page.evaluate("() => window.__RADAR_REFRESH_GUARD__.setLastActivityAt(Date.now() - 30000)")
                page.click("#btnNotifCenter")
                ev_drawer = page.evaluate("() => window.__RADAR_REFRESH_GUARD__.evaluateAutoReload()")
                self.assertFalse(ev_drawer["reloaded"])
                self.assertEqual(ev_drawer["reason"], "notif_drawer")
                page.click(".notif-btn-close")

                page.focus("#quickSearchInput")
                page.evaluate("() => window.__RADAR_REFRESH_GUARD__.setLastActivityAt(Date.now() - 30000)")
                ev_search = page.evaluate("() => window.__RADAR_REFRESH_GUARD__.evaluateAutoReload()")
                self.assertFalse(ev_search["reloaded"])
                self.assertEqual(ev_search["reason"], "search_active")
                page.evaluate("() => document.getElementById('quickSearchInput').blur()")

                # 5) 满足 20s 空闲且无弹窗 -> 触发安全自动刷新（调用 navigatorFn 且带 _t=）
                page.evaluate("() => window.__RADAR_REFRESH_GUARD__.setLastActivityAt(Date.now() - 25000)")
                ev_ok = page.evaluate("() => window.__RADAR_REFRESH_GUARD__.evaluateAutoReload()")
                self.assertTrue(ev_ok["reloaded"])
                navs = page.evaluate("() => window.__mockNavs")
                self.assertEqual(len(navs), 1)
                self.assertIn("_t=", navs[0])

                # 6) 真实点击轻提示刷新前的状态记忆与重载后无缝恢复（ticker=QQQ, filter=macro, view=intraday_path, theme=light）
                page.evaluate("() => window.__RADAR_REFRESH_GUARD__.setNavigatorFn(null)")
                page.evaluate(
                    """() => {
                        applyTheme('light', true);
                        setWatchlistFilter('macro');
                        selectTicker('QQQ', false);
                        switchCenterChartView('intraday_path');
                    }"""
                )
                with page.expect_navigation(wait_until="domcontentloaded"):
                    page.click("#btnNewUpdatePill")
                page.wait_for_function("() => !!window.__RADAR_REFRESH_GUARD__")

                restored = page.evaluate(
                    """() => ({
                        ticker: currentTicker,
                        filter: currentFilter,
                        centerView: currentCenterView,
                        theme: document.documentElement.getAttribute('data-theme'),
                        autoReloadOn: localStorage.getItem('radar_auto_reload_enabled'),
                        autoReloadLabel: document.getElementById('autoReloadLabel').innerText.trim(),
                        hasTsParam: window.location.search.includes('_t='),
                    })"""
                )
                self.assertEqual(restored["ticker"], "QQQ")
                self.assertEqual(restored["filter"], "macro")
                self.assertEqual(restored["centerView"], "intraday_path")
                self.assertEqual(restored["theme"], "light")
                self.assertEqual(restored["autoReloadOn"], "true")
                self.assertEqual(restored["autoReloadLabel"], "自动刷新: 开")
                self.assertTrue(restored["hasTsParam"])

                browser.close()
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
