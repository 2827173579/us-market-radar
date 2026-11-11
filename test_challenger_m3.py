#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_challenger_m3.py - Empirical Adversarial Stress Test Suite
for Milestone 3: Frontend Table 4 Dual Rankings & Futu Pro Terminal Tug-of-War Integration

Author: Challenger M3 (teamwork_challenger)
Roles: Critic, Specialist
Target: Milestone 3 Deliverables
- HTML DOM & Column Structure: predict_radar_5y.html & futu_pro_terminal.html
- Client-Side JS Logic & State Persistence Fuzzing: switchT4View, selectTicker, localStorage
- Dual-Pool Watchlist Filtering & TradingView Map Completeness
"""

import os
import re
import sys
import json
import unittest
import subprocess
from typing import Dict, Any, List

# Ensure UTF-8 output encoding on Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 动态定位项目根目录（兼容旧 SCRATCH_DIR/RADAR_DIR 变量名，避免任何旧电脑绝对路径）
_HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH_DIR = _HERE
RADAR_DIR = _HERE

for p in [SCRATCH_DIR, RADAR_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

import ml_predictor_5y as ml
import create_futu_terminal as cft


class TestTable4HtmlDomIntegrity(unittest.TestCase):
    """Rigorous DOM element and structure verification for predict_radar_5y.html."""

    @classmethod
    def setUpClass(cls):
        html_path = os.path.join(RADAR_DIR, "predict_radar_5y.html")
        if not os.path.exists(html_path):
            html_path = os.path.join(SCRATCH_DIR, "predict_radar_5y.html")
        with open(html_path, "r", encoding="utf-8") as f:
            cls.html = f.read()

    def test_t4_segmented_bar_structure(self):
        """Verify .t4-segmented-bar exists and contains all 4 view mode buttons."""
        self.assertIn('class="t4-segmented-bar"', self.html)
        self.assertIn('data-view="dual"', self.html)
        self.assertIn('data-view="long"', self.html)
        self.assertIn('data-view="short"', self.html)
        self.assertIn('data-view="all"', self.html)
        # Check onclick handler wiring
        self.assertIn("switchT4View('dual')", self.html)
        self.assertIn("switchT4View('long')", self.html)
        self.assertIn("switchT4View('short')", self.html)
        self.assertIn("switchT4View('all')", self.html)

    def test_t4_dual_cards_existence(self):
        """Verify both Card 4-A (#cardT4Long) and Card 4-B (#cardT4Short) exist."""
        self.assertIn('id="cardT4Long"', self.html)
        self.assertIn('id="cardT4Short"', self.html)
        # Verify Card 4-B has red theme border
        self.assertIn('border-left: 4px solid #f85149;', self.html)
        # Verify card header titles
        self.assertIn("表 4-A:", self.html)
        self.assertIn("表 4-B:", self.html)
        self.assertIn("做空/破位下杀胜率", self.html)

    def test_t4_short_table_all_9_columns(self):
        """Verify Card 4-B table header contains all 9 required short columns."""
        # Locate Card 4-B section
        short_card_match = re.search(r'id="cardT4Short"(.*?)</table>', self.html, re.DOTALL)
        self.assertIsNotNone(short_card_match, "Card 4-B table not found in HTML")
        short_card_content = short_card_match.group(1)

        # 9 column headers
        col_patterns = [
            (r'排名', "Col 1: Rank / 排名"),
            (r'标的代码', "Col 2: Ticker / 标的代码"),
            (r'所属赛道\s*&\s*评级', "Col 3: Sector & Rating / 所属赛道 & 评级"),
            (r'现价\s*/\s*波动偏离', "Col 4: Price & Volatility / 现价 / 波动偏离"),
            (r'AI\s*做空胜率', "Col 5: AI Short Win Rate / AI 做空胜率"),
            (r'空头动态止盈', "Col 6: Short TP / 空头动态止盈"),
            (r'空头防守止损', "Col 7: Short SL / 空头防守止损"),
            (r'期权\s*Dollar\s*PCR\s*评级', "Col 8: Options PCR / 期权 Dollar PCR 评级"),
            (r'AI\s*综合做空决胜指令', "Col 9: Action Directive / AI 综合做空决胜指令"),
        ]

        for pat, desc in col_patterns:
            self.assertTrue(
                bool(re.search(pat, short_card_content)),
                f"Missing required Short column: {desc} (pattern: {pat})"
            )

    def test_t4_short_table_row_rendering_and_styling(self):
        """Verify Card 4-B renders rows with correct medals, links, and #f85149 styling."""
        short_card_match = re.search(r'id="cardT4Short"(.*?)</table>', self.html, re.DOTALL)
        short_card_content = short_card_match.group(1)

        # Check for rows with class t4-short-stock-row
        short_rows = re.findall(r'<tr class="t4-short-stock-row"[^>]*>', short_card_content)
        self.assertGreaterEqual(len(short_rows), 5, f"Card 4-B should render at least 5 rows, found {len(short_rows)}")

        # Check row data attributes
        self.assertIn('data-short-rank="1"', short_card_content)
        self.assertIn('data-short-rank="2"', short_card_content)
        self.assertIn('data-short-rank="3"', short_card_content)
        self.assertIn('data-short-rank="4"', short_card_content)
        self.assertIn('data-short-rank="5"', short_card_content)

        # Check jump link format
        self.assertIn('href="futu_pro_terminal.html?symbol=', short_card_content)
        self.assertIn('class="tk-jump-icon">📉 看空</span>', short_card_content)

        # Check blood-red color code #f85149 applied to badges and bars
        self.assertIn('#f85149', short_card_content)
        self.assertIn('🩸#1', short_card_content)

    def test_t4_localstorage_persistence_code(self):
        """Verify localStorage persistence code for predict_t4_view exists."""
        self.assertIn('localStorage.setItem("predict_t4_view"', self.html)
        self.assertIn("localStorage.getItem('predict_t4_view')", self.html)
        self.assertIn("DOMContentLoaded", self.html)
        self.assertIn("switchT4View(savedView)", self.html)


class TestFutuProTerminalDomIntegrity(unittest.TestCase):
    """Rigorous DOM element and structure verification for futu_pro_terminal.html."""

    @classmethod
    def setUpClass(cls):
        html_path = os.path.join(RADAR_DIR, "futu_pro_terminal.html")
        if not os.path.exists(html_path):
            html_path = os.path.join(SCRATCH_DIR, "futu_pro_terminal.html")
        with open(html_path, "r", encoding="utf-8") as f:
            cls.html = f.read()

    def test_ai_prediction_box_elements(self):
        """Verify all 12+ required IDs inside #aiPredictionBox exist."""
        required_ids = [
            "aiPredictionBox",
            "pAiRankBadge",
            "pAiTvLink",
            "pAiActionBanner",
            "pAiActionText",
            "pAiSectorStar",
            "pAiLongWr",
            "pAiShortWr",
            "pAiLongBar",
            "pAiShortBar",
            "pAiLongRaw",
            "pAiShortRaw",
            "pAiPcrSentiment",
            "pAiTargetPrice",
            "pAiStopPrice",
            "pAiShortTargetPrice",
            "pAiShortStopPrice",
        ]
        for el_id in required_ids:
            self.assertIn(f'id="{el_id}"', self.html, f"Missing required element #{el_id} in futu_pro_terminal.html")

    def test_sidebar_short_tab_presence(self):
        """Verify left sidebar has data-filter='ai_short' tab with blood-red styling."""
        self.assertIn('data-filter="ai_short"', self.html)
        self.assertIn('🩸 AI做空榜 (Top 5)', self.html)
        self.assertIn('#ff5252', self.html)
        self.assertIn('rgba(248, 81, 73', self.html)

    def test_entry_plan_no_position_module_new_layout(self):
        """Verify V3.1 position-size module removed and new dual-card layout present."""
        self.assertNotIn('id="pBatch1Shares"', self.html, "V3.1 position card must be removed")
        self.assertNotIn('V3.1 建议仓位', self.html, "V3.1 position label must be removed")
        self.assertIn('id="pEntryStopDist"', self.html, "Stop-loss distance sub-label missing")
        self.assertIn('💡 参考挂单价', self.html, "Reference limit-price card label missing")
        self.assertIn('🛡️ 防守止损线', self.html, "Defensive stop-loss card label missing")
        self.assertIn('黄金坑支撑带', self.html, "Golden-pit support band row missing")

    def test_tv_symbol_map_completeness(self):
        """Verify SYMBOL_MAP contains all 70 tickers with correct exchanges."""
        # Extract SYMBOL_MAP from script
        map_match = re.search(r'const SYMBOL_MAP\s*=\s*(\{.*?\});', self.html, re.DOTALL)
        self.assertIsNotNone(map_match, "SYMBOL_MAP JavaScript object not found")
        map_json_str = map_match.group(1)
        # Parse map object
        symbol_map = json.loads(re.sub(r',\s*}', '}', map_json_str))
        self.assertEqual(len(symbol_map), 70, f"SYMBOL_MAP should contain 70 tickers, got {len(symbol_map)}")

        # Verify key additions from Defect 6 and Short ETFs
        self.assertEqual(symbol_map.get("NOW"), "NYSE:NOW")
        self.assertEqual(symbol_map.get("APLD"), "NASDAQ:APLD")
        self.assertEqual(symbol_map.get("COIN"), "NASDAQ:COIN")
        self.assertEqual(symbol_map.get("MARA"), "NASDAQ:MARA")
        self.assertEqual(symbol_map.get("SQQQ"), "NASDAQ:SQQQ")
        self.assertEqual(symbol_map.get("SOXS"), "AMEX:SOXS")
        self.assertEqual(symbol_map.get("VIXY"), "AMEX:VIXY")

    def test_ai_predictions_data_contract_injection(self):
        """Verify AI_PREDICTIONS injected into HTML client script contains both long and short pools."""
        pred_match = re.search(r'const AI_PREDICTIONS\s*=\s*(\{.*?\});', self.html, re.DOTALL)
        self.assertIsNotNone(pred_match, "AI_PREDICTIONS constant not found in HTML")
        ai_data = json.loads(pred_match.group(1))

        self.assertIn("top_long", ai_data)
        self.assertIn("top_short", ai_data)
        self.assertIn("by_ticker", ai_data)
        self.assertGreaterEqual(len(ai_data["top_short"]), 5)
        self.assertGreaterEqual(len(ai_data["top_long"]), 5)

        # Check by_ticker fields for NVDA and QQQ
        for tk in ["NVDA", "QQQ"]:
            item = ai_data["by_ticker"].get(tk)
            self.assertIsNotNone(item, f"{tk} missing from AI_PREDICTIONS.by_ticker")
            self.assertIn("calibrated_win_rate_pct", item)
            self.assertIn("calibrated_short_win_rate_pct", item)
            self.assertIn("short_target_profit_price", item)
            self.assertIn("short_stop_loss_price", item)


class TestClientSideJsExecutionAndFuzzing(unittest.TestCase):
    """Execute real JavaScript in Node.js to fuzz switchT4View, selectTicker, and localStorage."""

    @classmethod
    def setUpClass(cls):
        # Prepare Node.js test runner script
        cls.js_script_path = os.path.join(RADAR_DIR, "_test_js_harness.js")
        js_code = """
const fs = require('fs');
const path = require('path');

// Mock a lightweight browser DOM environment
function createMockDOM() {
    const store = {};
    const elements = {};

    function createElement(id, tag = 'div', attrs = {}) {
        const el = {
            id,
            tagName: tag.toUpperCase(),
            style: {},
            classList: {
                _classes: new Set(),
                add(c) { this._classes.add(c); },
                remove(c) { this._classes.delete(c); },
                contains(c) { return this._classes.has(c); },
                toggle(c, force) {
                    if (force !== undefined) {
                        force ? this._classes.add(c) : this._classes.delete(c);
                    } else {
                        this._classes.has(c) ? this._classes.delete(c) : this._classes.add(c);
                    }
                }
            },
            attributes: { ...attrs },
            getAttribute(k) { return this.attributes[k] || null; },
            setAttribute(k, v) { this.attributes[k] = v; },
            innerText: '',
            textContent: '',
            innerHTML: '',
            dataset: {},
            children: []
        };
        elements[id] = el;
        return el;
    }

    // Populate required elements for Table 4
    const btnDual = createElement('btn-dual', 'button', { 'data-view': 'dual' });
    const btnLong = createElement('btn-long', 'button', { 'data-view': 'long' });
    const btnShort = createElement('btn-short', 'button', { 'data-view': 'short' });
    const btnAll = createElement('btn-all', 'button', { 'data-view': 'all' });
    const segButtons = [btnDual, btnLong, btnShort, btnAll];

    const cardT4Long = createElement('cardT4Long');
    const cardT4Short = createElement('cardT4Short');
    const t4LongTitle = createElement('t4-long-title');
    const t4ModeBtn = createElement('t4-mode-btn');
    const t4Search = createElement('t4-search', 'input');
    t4Search.value = '';
    const t4CountLabel = createElement('t4-count-label');

    // Futu Pro Terminal Elements
    const curSym = createElement('curSym');
    const curName = createElement('curName');
    const curPrice = createElement('curPrice');
    const curChange = createElement('curChange');
    const pSector = createElement('pSector');
    const pMa50 = createElement('pMa50');
    const pBias = createElement('pBias');
    const pEma20 = createElement('pEma20');
    const pVolRatio = createElement('pVolRatio');
    const pRsi = createElement('pRsi');
    const pAtr = createElement('pAtr');
    const intcTacticalBox = createElement('intcTacticalBox');
    const aiPredictionBox = createElement('aiPredictionBox');
    const pAiRankBadge = createElement('pAiRankBadge');
    const pAiTvLink = createElement('pAiTvLink', 'a');
    const pAiActionBanner = createElement('pAiActionBanner');
    const pAiActionText = createElement('pAiActionText');
    const pAiSectorStar = createElement('pAiSectorStar');
    const pAiLongWr = createElement('pAiLongWr');
    const pAiShortWr = createElement('pAiShortWr');
    const pAiLongBar = createElement('pAiLongBar');
    const pAiShortBar = createElement('pAiShortBar');
    const pAiLongRaw = createElement('pAiLongRaw');
    const pAiShortRaw = createElement('pAiShortRaw');
    const pAiPcrSentiment = createElement('pAiPcrSentiment');
    const pAiTargetPrice = createElement('pAiTargetPrice');
    const pAiStopPrice = createElement('pAiStopPrice');
    const pAiShortTargetPrice = createElement('pAiShortTargetPrice');
    const pAiShortStopPrice = createElement('pAiShortStopPrice');

    const mockDocument = {
        getElementById(id) { return elements[id] || null; },
        querySelector(sel) { return null; },
        querySelectorAll(sel) {
            if (sel === '.t4-seg-btn') return segButtons;
            if (sel === '.t4-all-short-sub') return [];
            if (sel === '#t4-tbody .t4-stock-row') return [];
            if (sel === '#t4-sector-pills .filter-pill') return [];
            return [];
        },
        createElement(tag) { return createElement(`dyn-${Math.random()}`, tag); }
    };

    const mockLocalStorage = {
        _throwOnSet: false,
        setItem(k, v) {
            if (this._throwOnSet) throw new Error("SecurityError: QuotaExceeded or Private Browsing");
            store[k] = String(v);
        },
        getItem(k) { return store[k] !== undefined ? store[k] : null; },
        removeItem(k) { delete store[k]; }
    };

    return { mockDocument, mockLocalStorage, elements, segButtons };
}

// -----------------------------------------------------------------------------
// Test 1: switchT4View Logic & Fuzzing
// -----------------------------------------------------------------------------
function testSwitchT4View() {
    const { mockDocument, mockLocalStorage, elements, segButtons } = createMockDOM();
    const results = [];

    // Extract switchT4View code from predict_radar_5y.html
    const htmlPath = path.resolve(__dirname, 'predict_radar_5y.html');
    const htmlContent = fs.readFileSync(htmlPath, 'utf-8');
    
    // Create an isolated sandbox environment
    const sandbox = {
        document: mockDocument,
        localStorage: mockLocalStorage,
        currentSector: 'ALL',
        currentT4View: 'dual',
        showFullPool: false,
        applyT4Filters: (limit) => {}
    };

    const fnMatch = htmlContent.match(/function switchT4View\\(view\\)\\s*\\{([\\s\\S]*?)\\n\\s*\\}\\n\\s*function toggleT4PoolMode/);
    if (!fnMatch) throw new Error("Could not extract switchT4View from predict_radar_5y.html");
    
    const switchT4View = new Function('view', fnMatch[1]).bind(sandbox);

    // 1.1 Test standard views
    const views = ['dual', 'long', 'short', 'all'];
    for (const v of views) {
        switchT4View(v);
        const saved = mockLocalStorage.getItem('predict_t4_view');
        const longDisplay = elements['cardT4Long'].style.display;
        const shortDisplay = elements['cardT4Short'].style.display;

        if (v === 'dual') {
            if (longDisplay !== '' || shortDisplay !== '') throw new Error(`Dual view failed: long=${longDisplay}, short=${shortDisplay}`);
        } else if (v === 'long') {
            if (longDisplay !== '' || shortDisplay !== 'none') throw new Error(`Long view failed: long=${longDisplay}, short=${shortDisplay}`);
        } else if (v === 'short') {
            if (longDisplay !== 'none' || shortDisplay !== '') throw new Error(`Short view failed: long=${longDisplay}, short=${shortDisplay}`);
        } else if (v === 'all') {
            if (longDisplay !== '' || shortDisplay !== 'none') throw new Error(`All view failed: long=${longDisplay}, short=${shortDisplay}`);
        }
        if (saved !== v) throw new Error(`localStorage mismatch for ${v}: got ${saved}`);
    }
    results.push({ test: 'switchT4View_standard_modes', status: 'PASS' });

    // 1.2 Fuzz with edge and invalid inputs (null, undefined, '', 123, NaN, object)
    const edgeInputs = [null, undefined, '', 'INVALID_MODE', 123, NaN, {}, []];
    for (const inp of edgeInputs) {
        try {
            switchT4View(inp);
        } catch (e) {
            throw new Error(`switchT4View crashed on edge input ${inp}: ${e.message}`);
        }
    }
    results.push({ test: 'switchT4View_edge_fuzzing', status: 'PASS' });

    // 1.3 Test localStorage failure resilience
    mockLocalStorage._throwOnSet = true;
    try {
        switchT4View('dual'); // Must NOT throw uncaught error
    } catch (e) {
        throw new Error(`switchT4View failed to handle localStorage exception: ${e.message}`);
    }
    mockLocalStorage._throwOnSet = false;
    results.push({ test: 'switchT4View_localStorage_resilience', status: 'PASS' });

    return results;
}

// -----------------------------------------------------------------------------
// Test 2: selectTicker Tug-of-War Bar & Edge Case Fuzzing
// -----------------------------------------------------------------------------
function testSelectTicker() {
    const { mockDocument, mockLocalStorage, elements } = createMockDOM();
    const results = [];

    const futuPath = path.resolve(__dirname, 'futu_pro_terminal.html');
    const futuContent = fs.readFileSync(futuPath, 'utf-8');

    // Extract AI_PREDICTIONS and SYMBOL_MAP
    const aiPredMatch = futuContent.match(/const AI_PREDICTIONS\\s*=\\s*(\\{[\\s\\S]*?\\}\\});/);
    const symMapMatch = futuContent.match(/const SYMBOL_MAP\\s*=\\s*(\\{[\\s\\S]*?\\});/);
    
    if (!aiPredMatch || !symMapMatch) throw new Error("Could not extract AI_PREDICTIONS or SYMBOL_MAP from futu_pro_terminal.html");
    
    const AI_PREDICTIONS = JSON.parse(aiPredMatch[1]);
    const SYMBOL_MAP = JSON.parse(symMapMatch[1]);

    const STOCKS_DATA = [
        { ticker: 'NVDA', name: '英伟达', last: 130.0, pct_change: 2.5, cat: '芯片算力', ma50: 120.0, atr: 3.5, rsi: 45 },
        { ticker: 'SQQQ', name: '做空纳指', last: 8.5, pct_change: -3.0, cat: '反向杠杆', is_inverse_hedge: true }
    ];
    const MACRO_DATA = {
        'QQQ': { ticker: 'QQQ', name: '纳指ETF', last: 200.0, pct_change: 0.8 }
    };

    const sandbox = {
        document: mockDocument,
        localStorage: mockLocalStorage,
        AI_PREDICTIONS,
        SYMBOL_MAP,
        STOCKS_DATA,
        MACRO_DATA,
        currentTicker: 'NVDA',
        currentMobileTab: 'watchlist',
        encodeURIComponent: encodeURIComponent
    };

    // Extract selectTicker function body
    const fnMatch = futuContent.match(/function selectTicker\\(ticker,\\s*fromUserClick\\s*=\\s*false\\)\\s*\\{([\\s\\S]*?)\\n\\s*\\}\\n\\s*\\/\\/ 周期切换/);
    if (!fnMatch) throw new Error("Could not extract selectTicker from futu_pro_terminal.html");

    const selectTicker = new Function('ticker', 'fromUserClick', fnMatch[1]).bind(sandbox);

    // 2.1 Standard selection for NVDA
    selectTicker('NVDA', false);
    const lwrText = elements['pAiLongWr'].innerText;
    const swrText = elements['pAiShortWr'].innerText;
    const lBarWidth = parseFloat(elements['pAiLongBar'].style.width);
    const sBarWidth = parseFloat(elements['pAiShortBar'].style.width);
    const tvHref = elements['pAiTvLink'].href;

    if (!lwrText.includes('%') || !swrText.includes('%')) throw new Error("Missing % in win rate text");
    if (Math.abs((lBarWidth + sBarWidth) - 100.0) > 0.5) throw new Error(`Bar width sum != 100%: ${lBarWidth} + ${sBarWidth}`);
    if (!tvHref.includes('https://www.tradingview.com/chart/?symbol=')) throw new Error(`Invalid TV chart URL: ${tvHref}`);
    results.push({ test: 'selectTicker_standard_nvda', status: 'PASS' });

    // 2.2 Tug-of-War Clamping Fuzzing: simulate extreme probabilities
    const testCases = [
        { long: 99.0, short: 1.0, desc: 'extreme_long' },
        { long: 1.0, short: 99.0, desc: 'extreme_short' },
        { long: 50.0, short: 50.0, desc: 'balanced' },
        { long: 0.0, short: 0.0, desc: 'zeros' } // division by zero safeguard
    ];

    for (const tc of testCases) {
        AI_PREDICTIONS.by_ticker['FUZZ_TEST'] = {
            rank: 1, short_rank: 1,
            calibrated_win_rate_pct: tc.long,
            calibrated_short_win_rate_pct: tc.short,
            target_profit_price: 110, stop_loss_price: 90,
            short_target_profit_price: 90, short_stop_loss_price: 110,
            action: '🔥 极高确定性右侧做多',
            short_action: '🩸 均线承压·逢高买Put对冲'
        };
        selectTicker('FUZZ_TEST', false);
        const lw = parseFloat(elements['pAiLongBar'].style.width);
        const sw = parseFloat(elements['pAiShortBar'].style.width);

        if (isNaN(lw) || isNaN(sw)) throw new Error(`NaN bar width on ${tc.desc}`);
        if (lw < 10.0 || lw > 90.0) throw new Error(`Long bar width not clamped in [10, 90]: ${lw}`);
        if (sw < 10.0 || sw > 90.0) throw new Error(`Short bar width not clamped in [10, 90]: ${sw}`);
    }
    results.push({ test: 'selectTicker_tug_of_war_clamping', status: 'PASS' });

    return results;
}

try {
    const r1 = testSwitchT4View();
    const r2 = testSelectTicker();
    console.log(JSON.stringify({ success: true, results: [...r1, ...r2] }));
} catch (err) {
    console.error(JSON.stringify({ success: false, error: err.message, stack: err.stack }));
    process.exit(1);
}
"""
        with open(cls.js_script_path, "w", encoding="utf-8") as f:
            f.write(js_code)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.js_script_path):
            try:
                os.remove(cls.js_script_path)
            except Exception:
                pass

    def test_run_headless_node_js_fuzzing(self):
        """Execute Node.js test harness and verify 100% pass on JS DOM and fuzzing tests."""
        res = subprocess.run(
            ["node", self.js_script_path],
            cwd=RADAR_DIR,
            capture_output=True,
            text=True
        )
        if res.returncode != 0:
            self.fail(f"Node.js JS test runner failed with exit code {res.returncode}:\nStdout: {res.stdout}\nStderr: {res.stderr}")

        output_data = json.loads(res.stdout)
        self.assertTrue(output_data.get("success"), f"JS test runner reported failure: {output_data.get('error')}")
        results = output_data.get("results", [])
        self.assertGreaterEqual(len(results), 4, f"Expected at least 4 JS test assertions, got {len(results)}")
        for r in results:
            self.assertEqual(r.get("status"), "PASS", f"JS subtest {r.get('test')} failed")


if __name__ == "__main__":
    unittest.main()
