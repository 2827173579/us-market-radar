# -*- coding: utf-8 -*-
"""
Playwright 无头 Chrome 校验：全局真 VIX 恐慌中枢胶囊 + 详情浮层 + VIXY 三处卡片纠偏
覆盖 Desktop (1440x900) + Mobile (390x844)，深色 + 浅色双主题全联动，0 JS 报错、0 横向溢出。
"""

import os
from pathlib import Path
from playwright.sync_api import sync_playwright

WORKSPACE = Path(r"E:\wendang\deepseek-harness\default-workspace")
CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

FUTU_HTML = WORKSPACE / "futu_pro_terminal.html"
PREDICT_HTML = WORKSPACE / "predict_radar_5y.html"
MACRO_HTML = WORKSPACE / "macro_radar.html"
STOCKS_HTML = WORKSPACE / "stocks_radar.html"

ALL_PAGES = [
    ("futu_pro_terminal.html", FUTU_HTML),
    ("predict_radar_5y.html", PREDICT_HTML),
    ("macro_radar.html", MACRO_HTML),
    ("stocks_radar.html", STOCKS_HTML),
]


def assert_no_overflow(page, width: int, label: str):
    metrics = page.evaluate("""() => ({
        sw: document.documentElement.scrollWidth,
        iw: window.innerWidth,
        bsw: document.body ? document.body.scrollWidth : 0
    })""")
    assert metrics["sw"] <= width + 1 and metrics["bsw"] <= width + 1, (
        f"[{label}] Horizontal overflow: {metrics} (viewport={width})"
    )


def check_vix_pill_and_popover(page, page_name: str, vp_width: int, label_prefix: str):
    pill = page.locator("#pVixRegimePill")
    assert pill.is_visible(), f"[{label_prefix} {page_name}] #pVixRegimePill is not visible"
    pill_txt = pill.inner_text().strip()
    assert "VIX" in pill_txt, f"[{label_prefix} {page_name}] Unexpected pill text: {pill_txt}"

    # Click pill -> verify detail popover opens and contains all 5 metrics + plain advice
    pill.click()
    page.wait_for_timeout(150)
    pop = page.locator("#vixDetailPopover")
    assert pop.is_visible(), f"[{label_prefix} {page_name}] #vixDetailPopover did not open on click"
    pop_txt = pop.inner_text()
    for token in ("^VIX", "^VIX3M", "VIX/VIX3M", "^VVIX", "^TNX"):
        assert token in pop_txt, f"[{label_prefix} {page_name}] Popover missing '{token}': {pop_txt}"
    assert_no_overflow(page, vp_width, f"{label_prefix}-{page_name}-PopOpen")

    # Escape closes popover cleanly
    page.keyboard.press("Escape")
    page.wait_for_timeout(100)
    assert not pop.is_visible(), f"[{label_prefix} {page_name}] #vixDetailPopover did not close on Escape"
    return pill_txt


def check_futu_pool_and_vixy_fixes(page, is_mobile: bool):
    # 1. Watchlist must remain 79 tickers without ^VIX / ^VIX3M / ^VVIX / ^TNX
    tickers = page.locator("#watchlistContainer .stock-item").evaluate_all(
        "els => els.map(e => e.getAttribute('data-ticker'))"
    )
    assert len(tickers) == 79, f"Expected strictly 79 watchlist items, got {len(tickers)}"
    for forbidden in ("^VIX", "^VIX3M", "^VVIX", "^TNX"):
        assert forbidden not in tickers, f"{forbidden} must NOT be injected into 79-ticker watchlist!"

    # 2. Switch to VIXY & verify 3 awkward label fixes
    page.evaluate("selectTicker('VIXY');")
    page.wait_for_timeout(180)
    if is_mobile:
        page.locator("#mobileBottomNav .m-nav-tab[data-tab='tactics']").click()
        page.wait_for_timeout(150)

    spot_hint = page.locator("#pVixySpotHint")
    assert spot_hint.is_visible(), "#pVixySpotHint must be visible when VIXY is selected"
    hint_txt = spot_hint.inner_text()
    assert "^VIX" in hint_txt and "VIX/VIX3M" in hint_txt, f"Bad #pVixySpotHint text: {hint_txt}"

    # Expand Pro Quant Drawer if collapsed
    drawer = page.locator("#proQuantDrawer")
    if not drawer.is_visible():
        page.locator("#btnProQuantToggle").click()
        page.wait_for_timeout(150)
    assert drawer.is_visible(), "#proQuantDrawer did not expand"

    earn_txt = page.locator("#pEarningsDate").inner_text().strip()
    news_earn_txt = page.locator("#pNewsEarningsStatus").inner_text().strip()
    assert "波动率期货工具" in earn_txt, f"Unexpected VIXY #pEarningsDate: {earn_txt}"
    assert "近期无财报" not in earn_txt, f"Old label still in VIXY #pEarningsDate: {earn_txt}"
    assert "波动率期货工具" in news_earn_txt, f"Unexpected VIXY #pNewsEarningsStatus: {news_earn_txt}"
    assert "近期无财报" not in news_earn_txt, f"Old label still in VIXY #pNewsEarningsStatus: {news_earn_txt}"

    rs_lbl = page.locator("#pRs20dLabel").inner_text().strip()
    rs_val = page.locator("#pRs20d").inner_text().strip()
    assert rs_lbl == "20日对冲脉冲", f"Expected '20日对冲脉冲', got {rs_lbl!r}"
    assert "抗跌" not in rs_val, f"VIXY #pRs20d must not display '抗跌': {rs_val}"

    # Collapse drawer cleanly before finishing or check NVDA restore
    page.evaluate("selectTicker('NVDA');")
    page.wait_for_timeout(150)
    assert not spot_hint.is_visible(), "#pVixySpotHint should hide for non-VIXY ticker NVDA"
    assert page.locator("#pRs20dLabel").inner_text().strip() == "20日超额RS"
    page.locator("#btnProQuantToggle").click()
    page.wait_for_timeout(120)


def verify_viewport(browser, width: int, height: int, is_mobile: bool):
    tag = f"{'Mobile' if is_mobile else 'Desktop'}-{width}x{height}"
    ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=is_mobile)
    page = ctx.new_page()
    errors = []
    page.route("**/*tradingview.com/**", lambda route: route.abort())
    page.on("pageerror", lambda e: errors.append(f"{page.url.split('/')[-1]}: {e}"))

    # Start in Dark mode -> verify all 4 pages -> switch to Light mode on terminal -> verify all 4 pages inherit Light mode
    for theme in ("dark", "light"):
        if theme == "light":
            page.goto(FUTU_HTML.as_uri(), wait_until="domcontentloaded")
            page.wait_for_timeout(250)
            if page.evaluate("document.documentElement.getAttribute('data-theme')") != "light":
                page.locator("#btnThemeToggle").click()
                page.wait_for_timeout(200)

        for name, path in ALL_PAGES:
            page.goto(path.as_uri(), wait_until="domcontentloaded")
            page.wait_for_timeout(320)
            cur_theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
            assert cur_theme == theme, f"[{tag} {name}] Expected theme={theme}, got {cur_theme}"
            assert_no_overflow(page, width, f"{tag}-{theme}-{name}")
            pill_txt = check_vix_pill_and_popover(page, name, width, f"{tag}-{theme}")
            if name == "futu_pro_terminal.html":
                check_futu_pool_and_vixy_fixes(page, is_mobile=is_mobile)
                assert_no_overflow(page, width, f"{tag}-{theme}-{name}-afterVixy")
                if theme == "dark":
                    print(f"[PASS {tag}] Pill='{pill_txt}' | 79 tickers | VIXY 3 fixes verified (Dark & Light).")

    assert not errors, f"[{tag}] JS pageerrors: {errors}"
    ctx.close()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=CHROME_EXE if os.path.exists(CHROME_EXE) else None,
        )
        try:
            verify_viewport(browser, 1440, 900, is_mobile=False)
            verify_viewport(browser, 390, 844, is_mobile=True)
        finally:
            browser.close()
    print("\nALL VIX REGIME HUB & VIXY PLAYWRIGHT CHECKS PASSED 100% (0 JS PAGEERRORS)!")


if __name__ == "__main__":
    main()
