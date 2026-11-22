# -*- coding: utf-8 -*-
"""
Phase 4 Supplement Playwright Verification (Desktop 1440x900 + Mobile 390x844):
- Shared Light/Dark theme persistence across futu_pro_terminal -> predict_radar_5y / stocks_radar / macro_radar
- Canvas 15-day chart live theme redraw on predict_radar_5y.html
- First-screen slimming on predict_radar_5y.html (15-day chart visible at 1440x900 without scrolling)
- Default-collapsed #modelMacroDrawer & #backtestDetailsDrawer + expand/collapse toggle
- Table 4 zero horizontal truncation at 1440x900, Bear-Defense smart default + warning hint, expandable script row
- Zero duplicate DOM IDs, zero 🪙 missing-glyph, and 0 JS pageerrors
"""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

WORKSPACE = Path(r"E:\wendang\deepseek-harness\default-workspace")
CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

FUTU_HTML = WORKSPACE / "futu_pro_terminal.html"
PREDICT_HTML = WORKSPACE / "predict_radar_5y.html"
STOCKS_HTML = WORKSPACE / "stocks_radar.html"
MACRO_HTML = WORKSPACE / "macro_radar.html"


def check_static_subpages():
    for fp in (PREDICT_HTML, STOCKS_HTML, MACRO_HTML):
        assert fp.exists(), f"Missing generated HTML: {fp}"
        txt = fp.read_text(encoding="utf-8")
        assert "\U0001fa99" not in txt, f"Found missing-glyph 🪙 in {fp.name}"
        assert 'localStorage.getItem("futu_theme")' in txt, f"Missing shared futu_theme anti-flash script in {fp.name}"
        assert 'id="btnThemeToggle"' in txt, f"Missing theme toggle button in {fp.name}"

    pred_txt = PREDICT_HTML.read_text(encoding="utf-8")
    assert "28标的" not in pred_txt, "Outdated '28标的' text still present in predict_radar_5y.html"
    assert "79标的" in pred_txt, "Expected '79标的' in predict_radar_5y.html"
    body_only = pred_txt.split("<body>")[1].split("const timeCycleData")[0]
    ids = re.findall(r'\bid="([^"]+)"', body_only)
    dups = sorted({i for i in ids if ids.count(i) > 1})
    assert not dups, f"Duplicate DOM IDs in predict_radar_5y.html: {dups}"
    print(f"[PASS] Static subpages audit: 0 coin glyphs, 79标的 ok, {len(ids)} unique IDs in predict_radar_5y.html.")


def verify_desktop(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors = []
    page.route("**/*tradingview.com/**", lambda route: route.abort())
    page.on("pageerror", lambda e: errors.append(f"{page.url.split('/')[-1]}: {e}"))

    # 1. Start on futu_pro_terminal.html -> toggle to Light mode
    page.goto(FUTU_HTML.as_uri(), wait_until="domcontentloaded")
    page.wait_for_timeout(350)
    if page.evaluate("document.documentElement.getAttribute('data-theme')") != "light":
        page.locator("#btnThemeToggle").click()
        page.wait_for_timeout(200)
    assert page.evaluate("localStorage.getItem('futu_theme')") == "light"

    # 2. Navigate to stocks_radar.html & macro_radar.html -> verify Light theme auto-applied
    for sub_name, sub_path in (("stocks_radar.html", STOCKS_HTML), ("macro_radar.html", MACRO_HTML)):
        page.goto(sub_path.as_uri(), wait_until="domcontentloaded")
        page.wait_for_timeout(400)
        info = page.evaluate("""() => ({
            theme: document.documentElement.getAttribute('data-theme'),
            bg: getComputedStyle(document.body).backgroundColor,
            sw: document.documentElement.scrollWidth
        })""")
        assert info["theme"] == "light", f"{sub_name} did not inherit light theme: {info}"
        assert info["bg"] == "rgb(244, 246, 249)", f"{sub_name} unexpected light bg: {info['bg']}"
        assert info["sw"] <= 1441, f"{sub_name} horizontal overflow on 1440px: {info['sw']}"
        assert page.locator('a[href="futu_pro_terminal.html"]').first.is_visible()
        assert page.locator("#siteDisclaimerBanner").is_visible()
        print(f"[PASS Desktop] {sub_name}: light theme synced ({info['bg']}), scrollWidth={info['sw']}")

    # 3. Navigate to predict_radar_5y.html -> verify Light theme, Canvas redraw, slimming & Table 4
    page.goto(PREDICT_HTML.as_uri(), wait_until="domcontentloaded")
    page.wait_for_timeout(450)
    pred_theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
    pred_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert pred_theme == "light", f"predict_radar_5y.html did not inherit light theme: {pred_theme}"
    assert pred_bg == "rgb(244, 246, 249)", f"predict_radar_5y.html unexpected light bg: {pred_bg}"

    # Verify canvas painted bright white corner pixel in Light mode, then Dark corner pixel after toggle
    light_rgb = page.evaluate("""() => {
        const c = document.getElementById('cycleChartCanvas');
        const ctx = c.getContext('2d');
        const d = ctx.getImageData(4, 4, 1, 1).data;
        return [d[0], d[1], d[2]];
    }""")
    assert all(v > 230 for v in light_rgb), f"Canvas did not redraw light background: {light_rgb}"

    page.locator("#btnThemeToggle").click()
    page.wait_for_timeout(250)
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(11, 15, 25)"
    dark_rgb = page.evaluate("""() => {
        const c = document.getElementById('cycleChartCanvas');
        const ctx = c.getContext('2d');
        const d = ctx.getImageData(4, 4, 1, 1).data;
        return [d[0], d[1], d[2]];
    }""")
    assert all(v < 45 for v in dark_rgb), f"Canvas did not redraw dark background: {dark_rgb}"
    print(f"[PASS Desktop] Canvas theme redraw: light={light_rgb} -> dark={dark_rgb}")

    # First-screen slimming checks
    assert page.locator("#marketRegimeBadge").is_visible()
    assert page.locator("#liveScorecardBadge").is_visible()
    assert page.locator("#macroCalendarBanner").is_visible()
    assert not page.locator("#signalLedgerDrawer").is_visible(), "#signalLedgerDrawer must be collapsed by default"
    assert not page.locator("#modelMacroDrawer").is_visible(), "#modelMacroDrawer must be collapsed by default"
    assert not page.locator("#backtestDetailsDrawer").is_visible(), "#backtestDetailsDrawer must be collapsed by default"

    canvas_box = page.locator("#cycleChartCanvas").bounding_box()
    assert canvas_box and canvas_box["y"] >= 0 and (canvas_box["y"] + canvas_box["height"]) <= 900, (
        f"15-day chart not visible in first screen (1440x900) without scrolling: {canvas_box}"
    )
    print(f"[PASS Desktop] First-screen 15-day canvas bbox={canvas_box} (<= 900px).")

    # Anti-regression: 20 consecutive mousemove events over 15-day canvas + 1s wait MUST NOT grow height
    h_before = page.evaluate("() => document.getElementById('cycleChartCanvas').getBoundingClientRect().height")
    assert 380.0 <= h_before <= 520.0, f"Canvas height out of locked range [380, 520]: {h_before}"
    for i in range(20):
        mx = canvas_box["x"] + canvas_box["width"] * (0.08 + 0.84 * (i / 19.0))
        my = canvas_box["y"] + canvas_box["height"] * (0.25 + 0.50 * ((i % 4) / 3.0))
        page.mouse.move(mx, my)
        page.wait_for_timeout(20)
    page.wait_for_timeout(1000)
    h_after = page.evaluate("() => document.getElementById('cycleChartCanvas').getBoundingClientRect().height")
    assert h_after == h_before, f"Canvas infinite-height growth regression detected: before={h_before}px -> after={h_after}px (delta={h_after - h_before}px)"
    assert 380.0 <= h_after <= 520.0, f"Canvas height after hover out of range [380, 520]: {h_after}"
    print(f"[PASS Desktop] Canvas hover anti-growth: 20 mousemoves + 1s wait -> before={h_before}px, after={h_after}px (delta=0px).")

    # Expand/Collapse all drawers cleanly
    page.locator("#btnSignalLedgerDrawer").click()
    page.wait_for_timeout(150)
    assert page.locator("#signalLedgerDrawer").is_visible(), "#signalLedgerDrawer failed to expand"
    assert page.evaluate("document.documentElement.scrollWidth") <= 1441, "Signal ledger drawer caused horizontal overflow"
    page.locator("#btnSignalLedgerDrawer").click()
    page.wait_for_timeout(150)
    assert not page.locator("#signalLedgerDrawer").is_visible(), "#signalLedgerDrawer failed to collapse"

    page.locator("#btnModelMacroDrawer").click()
    page.wait_for_timeout(150)
    assert page.locator("#modelMacroDrawer").is_visible(), "#modelMacroDrawer failed to expand"
    page.locator("#btnModelMacroDrawer").click()
    page.wait_for_timeout(150)
    assert not page.locator("#modelMacroDrawer").is_visible(), "#modelMacroDrawer failed to collapse"

    page.locator("#btnBacktestDrawer").click()
    page.wait_for_timeout(150)
    assert page.locator("#backtestDetailsDrawer").is_visible(), "#backtestDetailsDrawer failed to expand"
    page.locator("#btnBacktestDrawer").click()
    page.wait_for_timeout(150)
    assert not page.locator("#backtestDetailsDrawer").is_visible(), "#backtestDetailsDrawer failed to collapse"

    # Table 4 Bear-Defense default tab & Long hint & expandable script row & zero horizontal overflow
    regime_text = page.locator("#marketRegimeBadge").inner_text()
    if "空头防守体制" in regime_text:
        assert page.locator("#cardT4Short").is_visible(), "Under BEAR_DEFENSE, #cardT4Short should be visible by default"
        assert not page.locator("#cardT4Long").is_visible(), "Under BEAR_DEFENSE, #cardT4Long should not lead by default"

    # Switch to Short view check overflow
    short_overflow = page.evaluate("""() => {
        const card = document.getElementById('cardT4Short');
        const tbl = card ? card.querySelector('table') : null;
        return { cardW: card ? card.clientWidth : 0, tblW: tbl ? tbl.scrollWidth : 0 };
    }""")
    assert short_overflow["tblW"] <= short_overflow["cardW"] + 2, f"Card 4-B horizontal overflow: {short_overflow}"

    # Switch to Long view -> verify Bear warning hint + zero horizontal overflow + expandable script row
    page.locator(".t4-seg-btn[data-view='long']").click()
    page.wait_for_timeout(200)
    assert page.locator("#cardT4Long").is_visible()
    if "空头防守体制" in regime_text:
        hint_txt = page.locator("#t4BearLongHint").inner_text()
        assert "当前空头防守期，常规多头胜率已压制，仅作观察" in hint_txt, f"Missing bear hint: {hint_txt}"

    long_overflow = page.evaluate("""() => {
        const card = document.getElementById('cardT4Long');
        const tbl = card ? card.querySelector('table') : null;
        return { cardW: card ? card.clientWidth : 0, tblW: tbl ? tbl.scrollWidth : 0, pageW: document.documentElement.scrollWidth };
    }""")
    assert long_overflow["tblW"] <= long_overflow["cardW"] + 2, f"Card 4-A horizontal overflow: {long_overflow}"
    assert long_overflow["pageW"] <= 1441, f"predict_radar_5y.html page horizontal overflow: {long_overflow}"

    # Expand first script row & recheck no horizontal overflow
    first_pill = page.locator("#t4-tbody .t4-script-pill").first
    assert first_pill.is_visible(), "Missing compact scenario script pill in Table 4-A"
    first_pill.click()
    page.wait_for_timeout(150)
    visible_details = page.locator("#t4-tbody .t4-script-detail-row:visible").count()
    assert visible_details == 1, f"Expected 1 expanded script detail row, got {visible_details}"
    expanded_overflow = page.evaluate("""() => ({
        cardW: document.getElementById('cardT4Long').clientWidth,
        tblW: document.getElementById('cardT4Long').querySelector('table').scrollWidth
    })""")
    assert expanded_overflow["tblW"] <= expanded_overflow["cardW"] + 2, f"Expanded row caused overflow: {expanded_overflow}"
    print(f"[PASS Desktop] Table 4 zero overflow (Short={short_overflow}, Long={long_overflow}, Expanded={expanded_overflow})")

    assert not errors, f"Desktop JS pageerrors: {errors}"
    ctx.close()


def verify_mobile(browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    page = ctx.new_page()
    errors = []
    page.route("**/*tradingview.com/**", lambda route: route.abort())
    page.on("pageerror", lambda e: errors.append(f"{page.url.split('/')[-1]}: {e}"))

    # Toggle Light mode on mobile futu_pro_terminal -> navigate to all 3 subpages
    page.goto(FUTU_HTML.as_uri(), wait_until="domcontentloaded")
    page.wait_for_timeout(300)
    if page.evaluate("document.documentElement.getAttribute('data-theme')") != "light":
        page.locator("#btnThemeToggle").click()
        page.wait_for_timeout(200)

    for sub_name, sub_path in (("predict_radar_5y.html", PREDICT_HTML), ("stocks_radar.html", STOCKS_HTML), ("macro_radar.html", MACRO_HTML)):
        page.goto(sub_path.as_uri(), wait_until="domcontentloaded")
        page.wait_for_timeout(400)
        m_info = page.evaluate("""() => ({
            theme: document.documentElement.getAttribute('data-theme'),
            bg: getComputedStyle(document.body).backgroundColor,
            sw: document.documentElement.scrollWidth
        })""")
        assert m_info["theme"] == "light" and m_info["bg"] == "rgb(244, 246, 249)", f"[Mobile] {sub_name} theme failed: {m_info}"
        assert m_info["sw"] <= 391, f"[Mobile] {sub_name} outer page overflowed 390px: {m_info['sw']}"
        ret = page.locator('a[href="futu_pro_terminal.html"]').first
        assert ret.is_visible() and "看盘" in ret.inner_text()
        if sub_name == "predict_radar_5y.html":
            assert not page.locator("#signalLedgerDrawer").is_visible(), "[Mobile] #signalLedgerDrawer must start collapsed"
            page.locator("#btnSignalLedgerDrawer").click()
            page.wait_for_timeout(150)
            assert page.locator("#signalLedgerDrawer").is_visible(), "[Mobile] #signalLedgerDrawer failed to expand"
            assert page.evaluate("document.documentElement.scrollWidth") <= 391, "[Mobile] #signalLedgerDrawer overflowed 390px"
            page.locator("#btnSignalLedgerDrawer").click()
            page.wait_for_timeout(120)
        print(f"[PASS Mobile 390x844] {sub_name}: light theme ok, scrollWidth={m_info['sw']}")

    assert not errors, f"Mobile JS pageerrors: {errors}"
    ctx.close()


def main():
    check_static_subpages()
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
    print("\nALL PHASE 4 SUBPAGE PLAYWRIGHT CHECKS PASSED 100%!")


if __name__ == "__main__":
    main()
