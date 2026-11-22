# -*- coding: utf-8 -*-
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
_local_futu = Path(__file__).resolve().parent / "futu_pro_terminal.html"
_ws_futu = Path(r"E:\wendang\deepseek-harness\default-workspace\futu_pro_terminal.html")
futu_uri = (_local_futu if _local_futu.exists() else (_ws_futu if _ws_futu.exists() else Path("futu_pro_terminal.html").resolve())).as_uri()
errors = []


def _assert_24h_chart_rendered(pg, tk24, label):
    pg.evaluate(f"selectTicker('{tk24}');")
    pg.wait_for_timeout(180)
    assert pg.locator("#ipChartWrap").is_visible(), f"{label}: {tk24} #ipChartWrap not visible"
    assert not pg.locator("#ipFallbackBox").is_visible(), f"{label}: {tk24} #ipFallbackBox should be hidden"
    assert pg.locator("#ipCorridorPoly").count() == 1, f"{label}: {tk24} missing #ipCorridorPoly"
    assert pg.locator("#ipP50Line").count() == 1, f"{label}: {tk24} missing #ipP50Line"
    assert pg.locator("#ipActualLine").count() == 1, f"{label}: {tk24} missing #ipActualLine"
    assert pg.locator("#ipVwapLine").count() == 1, f"{label}: {tk24} missing #ipVwapLine"

    meta_txt = pg.locator("#ipMetaTag").inner_text()
    sum_txt = pg.locator("#ipSummaryText").inner_text()
    div_txt = pg.locator("#ipDivergenceBadge").inner_text()
    svg_html = pg.locator("#ipChartSvg").inner_html()

    for bad in ("09:25盘前走廊", "NaN", "undefined"):
        assert bad not in meta_txt, f"{label}: {tk24} meta contains {bad!r}: {meta_txt}"
        assert bad not in sum_txt, f"{label}: {tk24} summary contains {bad!r}: {sum_txt}"
        assert bad not in div_txt, f"{label}: {tk24} badge contains {bad!r}: {div_txt}"
    assert "NaN" not in svg_html and "undefined" not in svg_html, f"{label}: {tk24} SVG contains NaN/undefined"

    vwap_lbl = (pg.locator("#pVwapLabel").text_content() or "").strip()
    rsi5_lbl = (pg.locator("#pRsi5mLabel").text_content() or "").strip()
    assert "09:30重置" not in vwap_lbl, f"{label}: {tk24} #pVwapLabel still shows 09:30重置: {vwap_lbl!r}"
    assert "09:30重置" not in rsi5_lbl, f"{label}: {tk24} #pRsi5mLabel still shows 09:30重置: {rsi5_lbl!r}"

    if tk24 == "BTC-USD":
        assert "UTC 00:00" in meta_txt, f"{label}: BTC-USD missing UTC 00:00 in meta: {meta_txt}"
        assert "VWAP" in sum_txt and "VWAP" in div_txt
        assert "(24H 实时滚动)" in vwap_lbl, f"{label}: BTC-USD #pVwapLabel missing '(24H 实时滚动)': {vwap_lbl!r}"
        assert "(24H 实时滚动)" in rsi5_lbl, f"{label}: BTC-USD #pRsi5mLabel missing '(24H 实时滚动)': {rsi5_lbl!r}"
    else:
        assert "24H" in meta_txt and "VWAP" in meta_txt and "VWAP" in sum_txt
        assert ("(最近24H参考 · 休市)" in vwap_lbl or "(24H 实时滚动)" in vwap_lbl), f"{label}: XAUUSD unexpected #pVwapLabel: {vwap_lbl!r}"
        assert ("(最近24H参考 · 休市)" in rsi5_lbl or "(24H 实时滚动)" in rsi5_lbl), f"{label}: XAUUSD unexpected #pRsi5mLabel: {rsi5_lbl!r}"

    ref_overlaps = pg.evaluate("""() => {
        const labels = Array.from(document.querySelectorAll('#ipChartSvg text.ip-ref-label'));
        const boxes = labels.map(el => {
            const b = el.getBBox();
            return {
                text: el.textContent || '',
                side: el.getAttribute('data-side') || 'left',
                x: b.x, y: b.y, w: b.width, h: b.height
            };
        });
        const overlaps = [];
        for (let i = 0; i < boxes.length; i++) {
            for (let j = i + 1; j < boxes.length; j++) {
                const a = boxes[i], b = boxes[j];
                const xOverlap = (a.x < b.x + b.w) && (a.x + a.w > b.x);
                const yOverlap = (a.y < b.y + b.h) && (a.y + a.h > b.y);
                if (xOverlap && yOverlap) {
                    overlaps.push([a, b]);
                }
            }
        }
        return { count: boxes.length, overlaps };
    }""")
    assert ref_overlaps["count"] >= 2, f"{label}: {tk24} expected >=2 ref labels, got {ref_overlaps}"
    assert not ref_overlaps["overlaps"], f"{label}: {tk24} SVG ref labels overlapped: {ref_overlaps['overlaps']}"
    print(f"[{label}] {tk24}: meta='{meta_txt}' | vwapLbl='{vwap_lbl}' | refLabels={ref_overlaps['count']} (0 overlaps)")


def _assert_microstructure_no_clipping(pg, label):
    pg.evaluate("""() => {
        const d = document.getElementById('proQuantDrawer');
        if (d && getComputedStyle(d).display === 'none') toggleProQuantDrawer();
    }""")
    pg.wait_for_timeout(150)
    clip_issues = pg.evaluate("""() => {
        const box = document.getElementById('entryPlanBox');
        if (!box) return ['missing entryPlanBox'];
        const bRect = box.getBoundingClientRect();
        const issues = [];
        box.querySelectorAll('.m-item').forEach((el, idx) => {
            const r = el.getBoundingClientRect();
            if (el.scrollWidth > el.clientWidth + 2) {
                issues.push(`m-item[${idx}] scrollWidth=${el.scrollWidth} > clientWidth=${el.clientWidth}`);
            }
            if (r.right > bRect.right + 2) {
                issues.push(`m-item[${idx}] right=${r.right} > boxRight=${bRect.right}`);
            }
        });
        return issues;
    }""")
    assert not clip_issues, f"{label}: microstructure card clipped: {clip_issues}"
    pg.evaluate("""() => {
        const d = document.getElementById('proQuantDrawer');
        if (d && getComputedStyle(d).display !== 'none') toggleProQuantDrawer();
    }""")
    pg.wait_for_timeout(100)


def audit_viewport(pw_browser, width, height, label, is_mobile=False):
    ctx = pw_browser.new_context(viewport={"width": width, "height": height})
    pg = ctx.new_page()
    pg.route("**/*tradingview.com/**", lambda route: route.abort())
    pg.on("pageerror", lambda e: errors.append(f"{label} pageerror: {e}"))

    pg.goto(futu_uri, wait_until="domcontentloaded")
    pg.wait_for_timeout(350)

    if is_mobile:
        pg.evaluate("if (typeof switchMobileTab === 'function') switchMobileTab('chart');")
        pg.wait_for_timeout(200)

    tab_btn = pg.locator("#tabIntradayPathView")
    assert tab_btn.is_visible(), f"{label}: #tabIntradayPathView not visible"
    tab_btn.click()
    pg.wait_for_timeout(250)

    # 1. NVDA (ok)
    pg.evaluate("selectTicker('NVDA');")
    pg.wait_for_timeout(200)
    assert pg.locator("#intradayPathView").is_visible(), f"{label}: #intradayPathView not visible for NVDA"
    assert pg.locator("#ipChartWrap").is_visible(), f"{label}: #ipChartWrap not visible for NVDA"
    assert pg.locator("#ipCorridorPoly").count() == 1, f"{label}: NVDA missing #ipCorridorPoly"
    assert pg.locator("#ipP50Line").count() == 1, f"{label}: NVDA missing #ipP50Line"
    nvda_sum = pg.locator("#ipSummaryText").inner_text()
    nvda_div = pg.locator("#ipDivergenceBadge").inner_text()
    nvda_svg = pg.locator("#ipChartSvg").inner_html()
    assert "NaN" not in nvda_svg and "undefined" not in nvda_svg, f"{label}: NVDA SVG contains NaN/undefined"
    assert "NaN" not in nvda_sum and "undefined" not in nvda_sum, f"{label}: NVDA summary invalid"
    print(f"[{label}] NVDA: sum='{nvda_sum}' | div='{nvda_div}' | poly={pg.locator('#ipCorridorPoly').count()} | actLine={pg.locator('#ipActualLine').count()}")

    # 2. SPY (ok)
    pg.evaluate("selectTicker('SPY');")
    pg.wait_for_timeout(150)
    assert pg.locator("#ipCorridorPoly").count() == 1, f"{label}: SPY missing #ipCorridorPoly"
    spy_svg = pg.locator("#ipChartSvg").inner_html()
    assert "NaN" not in spy_svg and "undefined" not in spy_svg, f"{label}: SPY SVG contains NaN/undefined"
    print(f"[{label}] SPY : sum='{pg.locator('#ipSummaryText').inner_text()}' | div='{pg.locator('#ipDivergenceBadge').inner_text()}'")

    # 3. ANT (insufficient_history fallback)
    pg.evaluate("selectTicker('ANT');")
    pg.wait_for_timeout(150)
    assert pg.locator("#ipFallbackBox").is_visible(), f"{label}: ANT fallback not visible"
    ant_fb = pg.locator("#ipFallbackBox").inner_text()
    assert "样本不足" in ant_fb and "NaN" not in ant_fb and "undefined" not in ant_fb
    print(f"[{label}] ANT : fallback='{ant_fb.replace(chr(10), ' | ')}'")

    # 4. V4.5 & V4.5.1: XAUUSD & BTC-USD 24H 真实走势 + VWAP 战术走廊 + 副标 & 防重叠（深/浅双主题验证）
    for tk24 in ("XAUUSD", "BTC-USD"):
        _assert_24h_chart_rendered(pg, tk24, f"{label}-Dark")

    # 切换到浅色主题并重验 BTC-USD & XAUUSD 24H 图表与微结构防裁切
    pg.locator("#btnThemeToggle").click()
    pg.wait_for_timeout(200)
    for tk24 in ("BTC-USD", "XAUUSD"):
        _assert_24h_chart_rendered(pg, tk24, f"{label}-Light")

    if is_mobile:
        pg.evaluate("if (typeof switchMobileTab === 'function') switchMobileTab('decision');")
        pg.wait_for_timeout(150)
    _assert_microstructure_no_clipping(pg, f"{label}-Light")

    # 切回深色并验证深色下微结构零裁切
    pg.locator("#btnThemeToggle").click()
    pg.wait_for_timeout(180)
    _assert_microstructure_no_clipping(pg, f"{label}-Dark")

    if is_mobile:
        pg.evaluate("if (typeof switchMobileTab === 'function') switchMobileTab('chart');")
        pg.wait_for_timeout(150)

    # 检查主图视窗容器与整页无横向溢出
    panel_box = pg.locator("#intradayPathView").bounding_box()
    tabs_box = pg.locator("#centerViewTabs").bounding_box()
    assert tabs_box and tabs_box["x"] + tabs_box["width"] <= width + 2, f"{label}: #centerViewTabs overflowed {tabs_box}"
    assert panel_box and panel_box["x"] + panel_box["width"] <= width + 2, f"{label}: #intradayPathView overflowed {panel_box}"
    assert pg.evaluate("document.getElementById('intradayPathView').scrollWidth <= document.getElementById('intradayPathView').clientWidth + 2"), f"{label}: #intradayPathView internal scrollWidth > clientWidth"
    assert pg.evaluate("document.documentElement.scrollWidth") <= width + 1, f"{label}: document horizontal overflow"

    # 切回 K线 Tab 确保开关可往返
    pg.locator("#tabKlineView").click()
    pg.wait_for_timeout(150)
    assert pg.locator("#tv_chart_container").is_visible(), f"{label}: switching back to kline failed"

    ctx.close()


with sync_playwright() as p:
    b = p.chromium.launch(headless=True, executable_path=chrome_path if os.path.exists(chrome_path) else None)
    audit_viewport(b, 1440, 900, "Desktop-1440x900", is_mobile=False)
    audit_viewport(b, 390, 844, "Mobile-390x844", is_mobile=True)
    b.close()

print("JS PageErrors:", len(errors), errors)
assert len(errors) == 0, f"Playwright pageerrors: {errors}"
print("[PASS] ALL PHASE 2 & V4.5/V4.5.1 24H DESKTOP & MOBILE PLAYWRIGHT CHECKS PASSED (0 JS ERRORS)!")
