# -*- coding: utf-8 -*-
"""
Phase 4 Playwright Headless Chrome Verification
Desktop (1440x900) + Mobile (390x844):
- Default dark mode (#0B0F19 / #121826), 0 JS pageerrors, 0 horizontal overflow
- One-click Dark <-> Light toggle (#F4F6F9 / #FFFFFF), localStorage persistence, TradingView & SVG theme sync
- Top bar beginner traffic-light regime pill + scorecard pill + quick search + restrained macro strip
- Watchlist 4 primary pills + collapsible sectors + strictly 1 status pill per row
- Right Layer A 3-second decision card + default-collapsed Pro Quant Drawer (0 NaN/undefined)
- Full ticker linkage across NVDA, SNDK, ANT, BTC-USD, XAUUSD
"""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

WORKSPACE = Path(r"E:\wendang\deepseek-harness\default-workspace")
CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
FUTU_HTML = WORKSPACE / "futu_pro_terminal.html"
INDEX_HTML = WORKSPACE / "index.html"


def check_static_html():
    assert FUTU_HTML.exists(), f"Missing {FUTU_HTML}"
    assert INDEX_HTML.exists(), f"Missing {INDEX_HTML}"
    futu_txt = FUTU_HTML.read_text(encoding="utf-8")
    index_txt = INDEX_HTML.read_text(encoding="utf-8")
    assert futu_txt == index_txt, "index.html does not match futu_pro_terminal.html!"
    assert "\U0001fa99" not in futu_txt, "Missing-glyph coin emoji found in futu_pro_terminal.html!"
    assert "__PY_TPL_" not in futu_txt, "Unreplaced template token found in futu_pro_terminal.html!"

    # Check duplicate HTML element ids (only outside <script> tag)
    body_only = futu_txt.split('<script type="text/javascript"')[0]
    ids = re.findall(r'\bid="([^"]+)"', body_only)
    dups = sorted({i for i in ids if ids.count(i) > 1})
    assert not dups, f"Duplicate DOM IDs detected: {dups}"
    print(f"[PASS] Static HTML audit: futu==index, 0 coin emojis, {len(ids)} unique DOM IDs.")


def assert_no_horizontal_overflow(page, width, label):
    metrics = page.evaluate("""() => ({
        sw: document.documentElement.scrollWidth,
        iw: window.innerWidth,
        bsw: document.body ? document.body.scrollWidth : 0
    })""")
    assert metrics["sw"] <= width + 1 and metrics["bsw"] <= width + 1, (
        f"[{label}] Horizontal overflow detected: {metrics} (viewport={width})"
    )


def verify_desktop(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors = []
    page.route("**/*tradingview.com/**", lambda route: route.abort())
    page.on("pageerror", lambda e: errors.append(str(e)))

    url = FUTU_HTML.as_uri()
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(450)

    # 1. Default Dark Theme & Colors
    theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
    assert theme == "dark", f"Expected default dark theme, got {theme}"
    colors = page.evaluate("""() => ({
        bodyBg: getComputedStyle(document.body).backgroundColor,
        cardBg: getComputedStyle(document.getElementById('beginnerDecisionCard')).backgroundColor
    })""")
    assert colors["bodyBg"] == "rgb(11, 15, 25)", f"Unexpected dark body bg: {colors['bodyBg']}"
    assert colors["cardBg"] == "rgb(18, 24, 38)", f"Unexpected dark card bg: {colors['cardBg']}"
    assert_no_horizontal_overflow(page, 1440, "Desktop-Dark-Init")

    # 2. Top bar regime pill, scorecard pill, quick search & macro strip
    reg_txt = page.locator("#pAiRegimePill").inner_text().strip()
    sc_txt = page.locator("#pAiScorecardPill").inner_text().strip()
    assert any(k in reg_txt for k in ("市场偏弱", "多头顺风", "震荡分化")), f"Bad regime pill: {reg_txt}"
    assert "胜率" in sc_txt, f"Bad scorecard pill: {sc_txt}"

    # Quick search by Chinese name ("闪迪" -> SNDK) and ticker ("NVDA" -> NVDA)
    page.fill("#quickSearchInput", "闪迪")
    page.wait_for_timeout(150)
    assert page.locator("#quickSearchDropdown").is_visible(), "Quick search dropdown did not open for Chinese name"
    page.locator("#quickSearchDropdown .qs-item[data-ticker='SNDK']").click()
    page.wait_for_timeout(150)
    assert page.locator("#curSym").inner_text().strip() == "SNDK"

    page.fill("#quickSearchInput", "NVDA")
    page.wait_for_timeout(150)
    assert page.locator("#quickSearchDropdown").is_visible(), "Quick search dropdown did not open for ticker"
    page.locator("#quickSearchDropdown .qs-item[data-ticker='NVDA']").click()
    page.wait_for_timeout(200)
    assert page.locator("#curSym").inner_text().strip() == "NVDA"

    # Macro strip toggle
    page.locator("#macroCalHeadline").click()
    page.wait_for_timeout(100)
    assert "expanded" in (page.locator("#macroStripWrap").get_attribute("class") or "")
    page.locator("#macroCalHeadline").click()

    # 3. Left Watchlist 4 primary pills + collapsed secondary sectors + strictly 1 badge per row
    assert page.locator("#wlPrimaryFilters .wl-pill-btn").count() == 4
    assert not page.locator("#watchlistCategories").is_visible(), "#watchlistCategories should be collapsed by default"
    badge_counts = page.locator("#watchlistContainer .stock-item").evaluate_all(
        "els => els.map(e => e.querySelectorAll('.s-status-pill').length)"
    )
    assert len(badge_counts) == 79, f"Expected 79 watchlist rows, got {len(badge_counts)}"
    assert all(c == 1 for c in badge_counts), f"Each stock item must have strictly 1 status pill: {set(badge_counts)}"

    for flt in ("opportunity", "risk", "gold_crypto", "all"):
        page.locator(f"#wlPrimaryFilters .wl-pill-btn[data-filter='{flt}']").click()
        page.wait_for_timeout(120)
        assert page.locator("#watchlistContainer .stock-item").count() >= 1, f"Filter {flt} returned 0 rows"

    page.locator("#btnToggleMoreFilters").click()
    page.wait_for_timeout(120)
    assert page.locator("#watchlistCategories").is_visible(), "Secondary sector bar failed to expand"
    page.locator("#btnToggleMoreFilters").click()

    # 4. Right Layer A visible & Pro Drawer collapsed by default
    assert not page.locator("#proQuantDrawer").is_visible(), "#proQuantDrawer must be collapsed by default"
    for sel in ("#beginnerVerdictTitle", "#pOptimalLimit", "#pBeginnerTakeProfit", "#pEntryStopLoss", "#pEntryStopDist", "#pAiLongWr", "#pAiShortWr"):
        loc = page.locator(sel)
        assert loc.is_visible(), f"Layer A element {sel} must be visible by default"
        txt = loc.inner_text().strip()
        assert txt and "NaN" not in txt and "undefined" not in txt, f"Invalid Layer A {sel}: {txt!r}"

    # Expand Pro Quant Drawer & verify content + persistence
    page.locator("#btnProQuantToggle").click()
    page.wait_for_timeout(200)
    assert page.locator("#proQuantDrawer").is_visible(), "#proQuantDrawer failed to expand"
    assert page.evaluate("localStorage.getItem('futu_pro_drawer_open')") == "true"
    pro_text = page.locator("#proQuantDrawer").inner_text()
    for must_have in ("日内 VWAP 均价", "5m RSI / 量比", "20日超额RS", "波段防守止损", "宏观司令塔气象"):
        assert must_have in pro_text, f"Pro drawer missing {must_have}"
    assert "NaN" not in pro_text and "undefined" not in pro_text, "Pro drawer contains NaN/undefined"

    # Collapse drawer back so default test passes cleanly
    page.locator("#btnProQuantToggle").click()
    page.wait_for_timeout(150)
    assert not page.locator("#proQuantDrawer").is_visible()
    assert page.evaluate("localStorage.getItem('futu_pro_drawer_open')") == "false"

    # 5. Theme Toggle -> Light Mode + Persistence + SVG & TradingView Sync
    page.locator("#btnThemeToggle").click()
    page.wait_for_timeout(250)
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "light"
    assert page.evaluate("localStorage.getItem('futu_theme')") == "light"
    light_colors = page.evaluate("""() => ({
        bodyBg: getComputedStyle(document.body).backgroundColor,
        cardBg: getComputedStyle(document.getElementById('beginnerDecisionCard')).backgroundColor,
        tvTheme: document.getElementById('tv_chart_container').dataset.tvTheme
    })""")
    assert light_colors["bodyBg"] == "rgb(244, 246, 249)", f"Unexpected light body bg: {light_colors['bodyBg']}"
    assert light_colors["cardBg"] == "rgb(255, 255, 255)", f"Unexpected light card bg: {light_colors['cardBg']}"
    assert light_colors["tvTheme"] == "light", f"TV chart did not sync light theme: {light_colors['tvTheme']}"

    # Reload & verify light theme persists
    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(350)
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "light", "Light theme did not persist across reload"

    # Open Intraday Path SVG in light mode and verify white rect & corridor
    page.evaluate("selectTicker('NVDA');")
    page.locator("#tabIntradayPathView").click()
    page.wait_for_timeout(250)
    svg_html_light = page.locator("#ipChartSvg").inner_html()
    assert 'fill="#FFFFFF"' in svg_html_light, "SVG did not redraw with light background #FFFFFF"
    assert "ipCorridorPoly" in svg_html_light and "NaN" not in svg_html_light and "undefined" not in svg_html_light

    # Toggle back to Dark Mode & verify SVG redraws dark
    page.locator("#btnThemeToggle").click()
    page.wait_for_timeout(250)
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"
    svg_html_dark = page.locator("#ipChartSvg").inner_html()
    assert 'fill="#121826"' in svg_html_dark, "SVG did not redraw with dark background #121826"
    page.locator("#tabKlineView").click()
    page.wait_for_timeout(150)
    assert page.evaluate("document.getElementById('tv_chart_container').dataset.tvTheme") == "dark"

    # 6. Five Core Tickers Linkage (NVDA, SNDK, ANT, BTC-USD, XAUUSD)
    page.evaluate("selectTicker('NVDA');")
    page.wait_for_timeout(150)
    assert not page.locator("#pAiShrinkBadge").is_visible()

    page.evaluate("selectTicker('SNDK');")
    page.wait_for_timeout(150)
    assert page.locator("#pAiShrinkBadge").is_visible()
    assert "445日" in page.locator("#pAiShrinkBadge").inner_text()

    page.evaluate("selectTicker('ANT');")
    page.wait_for_timeout(150)
    assert page.locator("#pIpoNoticeBadge").is_visible()
    assert "待开盘 / --" in (page.locator("#pRsi5m").text_content() or "")
    dec_ant = page.locator("#beginnerDecisionCard").inner_text()
    assert "NaN" not in dec_ant and "undefined" not in dec_ant

    for tk24 in ("BTC-USD", "XAUUSD"):
        page.evaluate(f"selectTicker('{tk24}');")
        page.wait_for_timeout(150)
        assert "无美股期权" in (page.locator("#pAiPcrSentiment").text_content() or "")
        dec_txt = page.locator("#beginnerDecisionCard").inner_text()
        assert "NaN" not in dec_txt and "undefined" not in dec_txt

    assert not errors, f"Desktop JS pageerrors: {errors}"
    ctx.close()
    print("[PASS] Desktop (1440x900) full Phase 4 verification passed (0 JS pageerrors, 0 horizontal overflow).")


def verify_mobile(browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    page = ctx.new_page()
    errors = []
    page.route("**/*tradingview.com/**", lambda route: route.abort())
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(FUTU_HTML.as_uri(), wait_until="domcontentloaded")
    page.wait_for_timeout(400)

    # Bottom 3 tabs: 选股 / 看图 / 决策
    nav_tabs = page.locator("#mobileBottomNav .m-nav-tab")
    assert nav_tabs.count() == 3, f"Expected 3 mobile bottom tabs, got {nav_tabs.count()}"
    assert_no_horizontal_overflow(page, 390, "Mobile-Watchlist-Dark")

    # Disclaimer must not overlap bottom nav
    nav_box = page.locator("#mobileBottomNav").bounding_box()
    ban_box = page.locator("#siteDisclaimerBanner").bounding_box()
    assert nav_box and ban_box
    assert ban_box["y"] + ban_box["height"] <= nav_box["y"] + 2, f"Disclaimer overlaps bottom nav: {ban_box} vs {nav_box}"

    # Tap a stock in watchlist -> auto transitions to 看图 (chart)
    page.locator("#watchlistContainer .stock-item[data-ticker='NVDA']").click()
    page.wait_for_timeout(250)
    assert page.locator("#tv_chart_container").is_visible()
    assert_no_horizontal_overflow(page, 390, "Mobile-Chart-Kline")

    # Switch to Intraday Path SVG on mobile
    page.locator("#tabIntradayPathView").click()
    page.wait_for_timeout(250)
    assert page.locator("#ipChartSvg").is_visible()
    assert_no_horizontal_overflow(page, 390, "Mobile-Chart-IntradayPath")

    # Switch to 决策 (tactics) collapsed & expanded, and toggle Light theme
    page.locator("#mobileBottomNav .m-nav-tab[data-tab='tactics']").click()
    page.wait_for_timeout(200)
    assert page.locator("#beginnerDecisionCard").is_visible()
    assert not page.locator("#proQuantDrawer").is_visible()
    assert_no_horizontal_overflow(page, 390, "Mobile-Tactics-Collapsed")

    page.locator("#btnProQuantToggle").click()
    page.wait_for_timeout(200)
    assert page.locator("#proQuantDrawer").is_visible()
    assert_no_horizontal_overflow(page, 390, "Mobile-Tactics-Expanded")

    # Toggle Light mode on mobile
    page.locator("#btnThemeToggle").click()
    page.wait_for_timeout(200)
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "light"
    assert_no_horizontal_overflow(page, 390, "Mobile-Light-Mode")

    assert not errors, f"Mobile JS pageerrors: {errors}"
    ctx.close()
    print("[PASS] Mobile (390x844) full Phase 4 verification passed (0 JS pageerrors, 0 horizontal overflow).")


def main():
    check_static_html()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=CHROME_EXE if os.path.exists(CHROME_EXE) else None,
        )
        try:
            verify_desktop(browser)
            verify_mobile(browser)
        finally:
            browser.close()
    print("\nALL PHASE 4 DESKTOP & MOBILE PLAYWRIGHT CHECKS PASSED 100%!")


if __name__ == "__main__":
    main()
