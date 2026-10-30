/**
 * test_frontend_adversarial.js - Empirical Challenger Adversarial Stress Harness
 * 
 * Verifies:
 * 1. Rapid alternating switches between crash-halt and normal tickers (1,000+ switches)
 * 2. Asymmetric burst switches (10x crash -> 1x normal, 10x normal -> 1x crash)
 * 3. Extreme boundary and corrupted ticker data (missing fields, zero prices, negative/extreme RSI, missing VWAP, null/NaN/Infinity)
 * 4. Recovery resilience: switching from corrupted ticker back to clean ticker restores DOM
 * 5. Memory footprint and leak audit across 10,000 iterations
 * 6. UI filter & mobile tab state machine transitions
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

console.log('='.repeat(80));
console.log(' ⚔️ EMPIRICAL CHALLENGER: FRONTEND ADVERSARIAL STRESS HARNESS');
console.log('='.repeat(80));

const htmlPath = path.resolve(__dirname, 'futu_pro_terminal.html');
if (!fs.existsSync(htmlPath)) {
    console.error(`[FATAL] Terminal HTML not found at: ${htmlPath}`);
    process.exit(1);
}

const html = fs.readFileSync(htmlPath, 'utf8');

// Extract Main Script
const scriptRegex = /<script(?:\s+[^>]*)?>([\s\S]*?)<\/script>/g;
let match;
let mainScript = '';
while ((match = scriptRegex.exec(html)) !== null) {
    if (match[1].includes('const STOCKS_DATA =')) {
        mainScript = match[1];
        break;
    }
}

if (!mainScript) {
    console.error('[FATAL] Main application script containing STOCKS_DATA not found in futu_pro_terminal.html');
    process.exit(1);
}

// Mock DOM Factory
const domNodes = {};

function createMockElement(id) {
    return {
        id: id,
        innerText: '',
        innerHTML: '',
        title: '',
        style: {
            display: 'block',
            color: '',
            background: '',
            backgroundColor: '',
            borderColor: '',
            fontSize: '',
            borderLeftColor: '',
            margin: '',
            padding: ''
        },
        classList: {
            classes: new Set(),
            add(c) { this.classes.add(c); },
            remove(c) { this.classes.delete(c); },
            toggle(c, force) {
                if (force !== undefined) {
                    if (force) this.classes.add(c);
                    else this.classes.delete(c);
                } else {
                    if (this.classes.has(c)) this.classes.delete(c);
                    else this.classes.add(c);
                }
                return this.classes.has(c);
            },
            contains(c) { return this.classes.has(c); }
        },
        dataset: {},
        onclick: null,
        appendChild(child) {},
        remove() {},
        querySelector(sel) { return createMockElement('sub_' + sel); },
        querySelectorAll(sel) { return []; },
        closest(sel) { return createMockElement('closest_' + sel); },
        getAttribute(name) { return ''; },
        setAttribute(name, val) {},
        addEventListener(event, fn) {}
    };
}

const mockElementIds = [
    'curSym', 'curName', 'curPrice', 'curChange', 'curExtBadge', 'curEarningsBadge',
    'pSector', 'pMa50', 'pBias', 'pEma20', 'pVolRatio', 'pRsi', 'pAtr',
    'intcTacticalBox', 'intcCardTitle', 'intcCostBase', 'intcTotalCost', 'intcCurrentVal',
    'intcPnlRatio', 'intcTrim1Val', 'intcTargetVal', 'intcStopVal', 'intcStatusDesc',
    'pSignalBanner', 'pSignalText', 'pSignalTag', 'tacticalDecisionBox', 'pDecisionTitle',
    'pActionTypeBadge', 'pMaReason', 'pVolReason', 'pFibReason', 'pMacroReason',
    'pActionDirective', 'pAllocLabel', 'pAllocSub', 'pFractional',
    'pIntradayBanner', 'pIntradayStatusText', 'pIntradayReason',
    'pVwap', 'pRsi5m', 'pRs20d', 'pEarningsDate',
    'pOptimalLimit', 'pOptimalNote', 'pBatch1Shares', 'pBatch1Val', 'pEntryStopLoss', 'pEntryFibBand',
    'optionsAnomalyBox', 'pPcrSentimentBadge', 'pOptionsPcr', 'pOptionsVolRatio',
    'pOptionsIv', 'pOptionsExpMove', 'pTopCallStrike', 'pTopPutStrike', 'pOptionsExpTip',
    'starBtn', 'starIcon', 'starBtnText', 'pStarStatusBadge', 'starredCount', 'tv_chart_container',
    'watchlistContainer', 'notifListContainer', 'notifBadge', 'notifTotalCount',
    'futuMain', 'sidebarLeft', 'sidebarRight', 'btnToggleSidebar', 'toastBox',
    'portfolioModalOverlay', 'portfolioTableBody', 'notifDrawerOverlay',
    'soundIcon', 'drawerSoundLabel', 'btnSoundToggle', 'minDropdownBtn', 'minDropdownMenu',
    'currentMinLabel'
];

mockElementIds.forEach(id => {
    domNodes[id] = createMockElement(id);
});

const stockItems = [];
const mockLocalStorage = {
    _data: {},
    getItem(k) { return this._data[k] || null; },
    setItem(k, v) { this._data[k] = String(v); },
    removeItem(k) { delete this._data[k]; }
};

const sandbox = {
    setTimeout: (fn, ms) => {},
    clearTimeout: (id) => {},
    Event: function(type) { this.type = type; },
    window: {
        innerWidth: 1920,
        innerHeight: 1080,
        location: { hostname: 'localhost', origin: 'http://localhost:8765', protocol: 'http:' },
        addEventListener: () => {},
        setInterval: () => {},
        setTimeout: (fn, ms) => {},
        clearTimeout: (id) => {},
        dispatchEvent: () => {}
    },
    document: {
        getElementById: (id) => {
            if (!domNodes[id]) {
                domNodes[id] = createMockElement(id);
            }
            return domNodes[id];
        },
        querySelectorAll: (sel) => {
            if (sel === '.stock-item') return stockItems;
            if (sel === '.period-tab' || sel === '.dropdown-item' || sel === '.w-tab' || sel === '.m-nav-tab' || sel === '.m-period-tab' || sel === '.p-filter-pill' || sel === '#sidebarRight .panel-box') {
                return [createMockElement('el_' + sel)];
            }
            return [];
        },
        createElement: (tag) => createMockElement('dyn_' + tag),
        addEventListener: () => {},
        documentElement: { requestFullscreen: () => Promise.resolve() }
    },
    localStorage: mockLocalStorage,
    TradingView: {
        widget: function(opts) {}
    },
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
    Audio: function(src) {
        return {
            volume: 1.0,
            play: () => Promise.resolve()
        };
    },
    console: {
        log: () => {},
        warn: () => {},
        error: (...args) => console.error('[VM ERROR]', ...args)
    }
};
sandbox.window.document = sandbox.document;
sandbox.window.localStorage = sandbox.localStorage;

const context = vm.createContext(sandbox);

try {
    vm.runInContext(mainScript, context);
} catch (e) {
    console.error('[FATAL] Failed to evaluate script in Node VM:', e);
    process.exit(1);
}

const macroData = vm.runInContext('MACRO_DATA', context) || {};
const stocksData = vm.runInContext('STOCKS_DATA', context) || [];
const selectTicker = vm.runInContext('selectTicker', context);
const switchMobileTab = vm.runInContext('switchMobileTab', context);
const filterRightCards = vm.runInContext('filterRightCards', context);

const macroTickers = Object.keys(macroData);
const stockTickers = stocksData.map(s => s.ticker);
const allTickers = [...macroTickers, ...stockTickers];

allTickers.forEach(t => {
    const item = createMockElement('item_' + t);
    item.dataset.ticker = t;
    stockItems.push(item);
});

// Identify Crash & Normal Tickers
const crashPool = allTickers.filter(t => {
    const data = stocksData.find(s => s.ticker === t) || macroData[t];
    return Boolean(data && (data.is_crash_halt || (data.optimal_limit === 0 && data.limit_note && data.limit_note.includes("破位"))));
});

const normalPool = allTickers.filter(t => {
    const data = stocksData.find(s => s.ticker === t) || macroData[t];
    return Boolean(data && !data.is_crash_halt && !(data.optimal_limit === 0 && data.limit_note && data.limit_note.includes("破位")));
});

console.log(`[INIT] Loaded ${allTickers.length} tickers: ${crashPool.length} Crash-Halt, ${normalPool.length} Normal`);

// Test Report Aggregators
const report = {
    test1_alternating: { iterations: 0, exceptions: 0, remnants: 0, errors: [] },
    test2_burst_asymmetric: { iterations: 0, exceptions: 0, remnants: 0, errors: [] },
    test3_random_chaos: { iterations: 0, exceptions: 0, remnants: 0, errors: [] },
    test4_boundary_mutations: { tests: 0, exceptions: 0, remnants: 0, errors: [] },
    test5_memory_audit: { initialHeapMB: 0, finalHeapMB: 0, deltaHeapMB: 0, iterations: 0 },
    test6_ui_state_machine: { transitions: 0, exceptions: 0, errors: [] }
};

// Helper: Assert Clean Normal State
function assertNormalState(ticker, fromTicker, contextTag) {
    const errors = [];
    if (domNodes.pBatch1Shares.style.color === '#9ca3af') {
        errors.push(`[${contextTag}] ${fromTicker} -> ${ticker}: pBatch1Shares leaked gray (#9ca3af)`);
    }
    if (domNodes.pBatch1Val.style.color === '#ff5252') {
        errors.push(`[${contextTag}] ${fromTicker} -> ${ticker}: pBatch1Val leaked red (#ff5252)`);
    }
    if (domNodes.pOptimalLimit.style.color === '#ff5252') {
        errors.push(`[${contextTag}] ${fromTicker} -> ${ticker}: pOptimalLimit leaked red (#ff5252)`);
    }
    if (domNodes.pOptimalLimit.style.fontSize === '15px') {
        errors.push(`[${contextTag}] ${fromTicker} -> ${ticker}: pOptimalLimit leaked 15px font size`);
    }
    if (domNodes.pOptimalLimit.innerText.includes("暂停挂单")) {
        errors.push(`[${contextTag}] ${fromTicker} -> ${ticker}: pOptimalLimit retained halt text`);
    }
    if (domNodes.pBatch1Shares.innerText.includes("冻结")) {
        errors.push(`[${contextTag}] ${fromTicker} -> ${ticker}: pBatch1Shares retained frozen text`);
    }
    return errors;
}

// -----------------------------------------------------------------------------
// TEST 1: Rapid Ping-Pong Alternating Switches (1,000 Switches)
// -----------------------------------------------------------------------------
console.log('\n[TEST 1] Running 1,000 Rapid Ping-Pong Switches (Crash <-> Normal)...');
const test1CrashSubset = ['SMH', 'SOXL', 'SOXS', 'TSM', 'ARM'].filter(t => crashPool.includes(t));
const test1NormalSubset = ['QQQM', 'IGV', 'AAPL', 'MSFT', 'NVDA', 'GOOGL'].filter(t => normalPool.includes(t));

let lastTicker = 'INIT';
for (let i = 0; i < 1000; i++) {
    const isCrashTurn = (i % 2 === 0);
    const targetTicker = isCrashTurn 
        ? test1CrashSubset[i % test1CrashSubset.length] 
        : test1NormalSubset[i % test1NormalSubset.length];

    try {
        selectTicker(targetTicker);
        report.test1_alternating.iterations++;

        if (!isCrashTurn) {
            const errs = assertNormalState(targetTicker, lastTicker, 'Ping-Pong');
            if (errs.length > 0) {
                report.test1_alternating.remnants += errs.length;
                report.test1_alternating.errors.push(...errs);
            }
        }
    } catch (e) {
        report.test1_alternating.exceptions++;
        report.test1_alternating.errors.push(`Ping-Pong ${targetTicker} threw: ${e.message}`);
    }
    lastTicker = targetTicker;
}
console.log(`  -> Completed 1,000 iterations. Exceptions: ${report.test1_alternating.exceptions}, Remnants: ${report.test1_alternating.remnants}`);

// -----------------------------------------------------------------------------
// TEST 2: Asymmetric Burst Switches (10x Crash -> 1x Normal, 10x Normal -> 1x Crash)
// -----------------------------------------------------------------------------
console.log('\n[TEST 2] Running Asymmetric Burst Stress Test (10x Crash -> 1x Normal)...');
for (let round = 0; round < 50; round++) {
    // 10 consecutive crash switches
    for (let c = 0; c < 10; c++) {
        const cTk = crashPool[c % crashPool.length];
        try {
            selectTicker(cTk);
            report.test2_burst_asymmetric.iterations++;
            lastTicker = cTk;
        } catch (e) {
            report.test2_burst_asymmetric.exceptions++;
        }
    }
    // 1 normal switch
    const nTk = normalPool[round % normalPool.length];
    try {
        selectTicker(nTk);
        report.test2_burst_asymmetric.iterations++;
        const errs = assertNormalState(nTk, lastTicker, 'Burst-Crash-To-Normal');
        if (errs.length > 0) {
            report.test2_burst_asymmetric.remnants += errs.length;
            report.test2_burst_asymmetric.errors.push(...errs);
        }
        lastTicker = nTk;
    } catch (e) {
        report.test2_burst_asymmetric.exceptions++;
        report.test2_burst_asymmetric.errors.push(`Burst ${nTk} threw: ${e.message}`);
    }
}

console.log('  Running Asymmetric Burst Stress Test (10x Normal -> 1x Crash)...');
for (let round = 0; round < 50; round++) {
    // 10 consecutive normal switches
    for (let n = 0; n < 10; n++) {
        const nTk = normalPool[n % normalPool.length];
        try {
            selectTicker(nTk);
            report.test2_burst_asymmetric.iterations++;
            lastTicker = nTk;
        } catch (e) {
            report.test2_burst_asymmetric.exceptions++;
        }
    }
    // 1 crash switch
    const cTk = crashPool[round % crashPool.length];
    try {
        selectTicker(cTk);
        report.test2_burst_asymmetric.iterations++;
        lastTicker = cTk;
    } catch (e) {
        report.test2_burst_asymmetric.exceptions++;
    }
}
console.log(`  -> Completed ${report.test2_burst_asymmetric.iterations} burst iterations. Exceptions: ${report.test2_burst_asymmetric.exceptions}, Remnants: ${report.test2_burst_asymmetric.remnants}`);

// -----------------------------------------------------------------------------
// TEST 3: Randomized Chaos Switching (2,000 Iterations)
// -----------------------------------------------------------------------------
console.log('\n[TEST 3] Running 2,000 Randomized Chaos Ticker Switches...');
for (let i = 0; i < 2000; i++) {
    const randomTicker = allTickers[Math.floor(Math.random() * allTickers.length)];
    const isNormal = normalPool.includes(randomTicker);
    try {
        selectTicker(randomTicker);
        report.test3_random_chaos.iterations++;

        if (isNormal) {
            const errs = assertNormalState(randomTicker, lastTicker, 'Chaos');
            if (errs.length > 0) {
                report.test3_random_chaos.remnants += errs.length;
                report.test3_random_chaos.errors.push(...errs);
            }
        }
    } catch (e) {
        report.test3_random_chaos.exceptions++;
        report.test3_random_chaos.errors.push(`Chaos ${randomTicker} threw: ${e.message}`);
    }
    lastTicker = randomTicker;
}
console.log(`  -> Completed 2,000 chaos iterations. Exceptions: ${report.test3_random_chaos.exceptions}, Remnants: ${report.test3_random_chaos.remnants}`);

// -----------------------------------------------------------------------------
// TEST 4: Extreme Boundary & Mutated Ticker Data Injection
// -----------------------------------------------------------------------------
console.log('\n[TEST 4] Injecting Extreme Boundary and Corrupted Ticker Data...');

const boundaryMutations = [
    {
        name: 'Empty Object (All Undefined)',
        ticker: 'SYM_EMPTY',
        data: { ticker: 'SYM_EMPTY' }
    },
    {
        name: 'All Zero Fields',
        ticker: 'SYM_ZERO',
        data: {
            ticker: 'SYM_ZERO',
            name: 'Zero Tech',
            last: 0,
            active_price: 0,
            ma50: 0,
            ema20: 0,
            atr: 0,
            vwap: 0,
            rsi: 0,
            vol_ratio: 0,
            fib_low: 0,
            fib_high: 0,
            pct_change: 0,
            shares: 0,
            cost: 0
        }
    },
    {
        name: 'Negative RSI (-99.9) & Intraday 5m RSI (-50.0)',
        ticker: 'SYM_NEG_RSI',
        data: {
            ticker: 'SYM_NEG_RSI',
            name: 'Neg RSI Corp',
            last: 100.0,
            active_price: 100.0,
            ma50: 95.0,
            rsi: -99.9,
            rsi_5m: -50.0,
            vwap: 101.0,
            vol_ratio: 1.2
        }
    },
    {
        name: 'Extreme High RSI (+999.9) & Extreme Bias (+500%)',
        ticker: 'SYM_MAX_RSI',
        data: {
            ticker: 'SYM_MAX_RSI',
            name: 'Max RSI Inc',
            last: 500.0,
            active_price: 500.0,
            ma50: 100.0,
            bias: 400.0,
            rsi: 999.9,
            rsi_5m: 800.0,
            signal: '高位超买冲顶'
        }
    },
    {
        name: 'Missing VWAP (null & undefined & NaN)',
        ticker: 'SYM_NO_VWAP',
        data: {
            ticker: 'SYM_NO_VWAP',
            name: 'No VWAP Ltd',
            last: 50.0,
            active_price: 50.0,
            vwap: null
        }
    },
    {
        name: 'Extreme Negative Change (-85.0% Flash Crash)',
        ticker: 'SYM_FLASH_CRASH',
        data: {
            ticker: 'SYM_FLASH_CRASH',
            name: 'Flash Crash',
            last: 12.0,
            active_price: 12.0,
            pct_change: -85.0,
            is_crash_halt: true,
            limit_note: '单日/盘后剧烈跳空破位，严禁首日左侧挂单接飞刀'
        }
    },
    {
        name: 'Extreme Surge (+120.0% Overnight Reversal)',
        ticker: 'SYM_REVERSAL',
        data: {
            ticker: 'SYM_REVERSAL',
            name: 'Super Reversal',
            last: 200.0,
            ext_price: 240.0,
            ext_pct: 20.0,
            session_tag: '盘前',
            pct_change: 120.0,
            is_pit: true,
            signal: '黄金坑就位'
        }
    },
    {
        name: 'Completely Unknown Ticker (NotInUniverse)',
        ticker: 'SYM_UNKNOWN_123',
        data: null // not injected into data arrays at all!
    },
    {
        name: 'NaN & Infinite Numeric Quantities',
        ticker: 'SYM_NAN_INF',
        data: {
            ticker: 'SYM_NAN_INF',
            name: 'NaN Quantities',
            last: NaN,
            active_price: NaN,
            pct_change: NaN,
            bias: Infinity,
            atr: -5.0,
            vol_ratio: NaN,
            rsi: NaN
        }
    },
    {
        name: 'Negative Last Price (-25.50)',
        ticker: 'SYM_NEG_PRICE',
        data: {
            ticker: 'SYM_NEG_PRICE',
            name: 'Negative Price Corp',
            last: -25.50,
            active_price: -25.50,
            pct_change: -12.0
        }
    },
    {
        name: 'Corrupted Options Structure (has_options: true, null strikes/PCR)',
        ticker: 'SYM_BAD_OPT',
        data: {
            ticker: 'SYM_BAD_OPT',
            name: 'Malformed Options',
            last: 80.0,
            active_price: 80.0,
            has_options: true,
            pcr: 0.0,
            dollar_pcr: 0.0,
            call_vol: 0,
            put_vol: 0,
            exp_low: 0,
            exp_high: 0
        }
    },
    {
        name: 'Huge Held Position (Fractional shares: 123456.78901, cost: 0.01)',
        ticker: 'SYM_WHALE_HOLD',
        data: {
            ticker: 'SYM_WHALE_HOLD',
            name: 'Whale Position',
            last: 150.0,
            active_price: 150.0,
            shares: 123456.78901,
            cost: 0.01,
            trim1: 160.0,
            trim1_shares: 50000.0,
            base_val: 100.0,
            target: 200.0
        }
    }
];

boundaryMutations.forEach(mut => {
    report.test4_boundary_mutations.tests++;
    try {
        if (mut.data) {
            stocksData.push(mut.data);
        }

        // 1. Select the mutated ticker
        selectTicker(mut.ticker);

        // 2. Select normal ticker immediately to verify recovery
        selectTicker('AAPL');
        const errs = assertNormalState('AAPL', mut.ticker, `Recovery-${mut.name}`);
        if (errs.length > 0) {
            report.test4_boundary_mutations.remnants += errs.length;
            report.test4_boundary_mutations.errors.push(...errs);
        }

        if (mut.data) {
            const idx = stocksData.findIndex(s => s.ticker === mut.ticker);
            if (idx >= 0) stocksData.splice(idx, 1);
        }
    } catch (e) {
        report.test4_boundary_mutations.exceptions++;
        report.test4_boundary_mutations.errors.push(`Boundary [${mut.name}] threw: ${e.message}\nStack: ${e.stack}`);
        if (mut.data) {
            const idx = stocksData.findIndex(s => s.ticker === mut.ticker);
            if (idx >= 0) stocksData.splice(idx, 1);
        }
    }
});
console.log(`  -> Completed ${report.test4_boundary_mutations.tests} boundary tests. Exceptions: ${report.test4_boundary_mutations.exceptions}, Recovery Remnants: ${report.test4_boundary_mutations.remnants}`);

// -----------------------------------------------------------------------------
// TEST 4B: Pairwise Functional Isolation Matrix (Held, Options, Ext, Earnings)
// -----------------------------------------------------------------------------
console.log('\n[TEST 4B] Testing Pairwise Functional Isolation Matrix...');
let isolationTransitions = 0;
let isolationErrors = 0;

// Test Held vs Unheld Transitions
const heldTickers = allTickers.filter(t => {
    const d = stocksData.find(s => s.ticker === t) || macroData[t];
    return Boolean(d && d.shares && d.shares > 0);
});
const unheldTickers = allTickers.filter(t => {
    const d = stocksData.find(s => s.ticker === t) || macroData[t];
    return !d || !d.shares || d.shares <= 0;
});

heldTickers.forEach(hTk => {
    unheldTickers.slice(0, 5).forEach(uTk => {
        // Switch to held
        selectTicker(hTk);
        isolationTransitions++;
        if (domNodes.intcTacticalBox.style.display !== 'block') {
            isolationErrors++;
            console.error(`[ISOLATION] Expected intcTacticalBox visible for held ${hTk}`);
        }
        // Switch to unheld
        selectTicker(uTk);
        isolationTransitions++;
        if (domNodes.intcTacticalBox.style.display !== 'none') {
            isolationErrors++;
            console.error(`[ISOLATION] intcTacticalBox leaked visible for unheld ${uTk}`);
        }
    });
});

// Test Options vs No-Options Transitions
const optionsTickers = allTickers.filter(t => {
    const d = stocksData.find(s => s.ticker === t) || macroData[t];
    return Boolean(d && d.has_options);
});
const noOptionsTickers = allTickers.filter(t => {
    const d = stocksData.find(s => s.ticker === t) || macroData[t];
    return !d || !d.has_options;
});

optionsTickers.slice(0, 5).forEach(oTk => {
    noOptionsTickers.slice(0, 5).forEach(noTk => {
        selectTicker(oTk);
        isolationTransitions++;
        if (domNodes.optionsAnomalyBox.style.display !== 'block') {
            isolationErrors++;
            console.error(`[ISOLATION] Expected optionsAnomalyBox visible for ${oTk}`);
        }
        selectTicker(noTk);
        isolationTransitions++;
        if (domNodes.optionsAnomalyBox.style.display !== 'none') {
            isolationErrors++;
            console.error(`[ISOLATION] optionsAnomalyBox leaked visible for ${noTk}`);
        }
    });
});
console.log(`  -> Completed ${isolationTransitions} isolation transitions. Isolation Errors: ${isolationErrors}`);

// -----------------------------------------------------------------------------
// TEST 5: Memory Leak & Heap Stress Audit (10,000 Rapid Switches)
// -----------------------------------------------------------------------------
console.log('\n[TEST 5] Running 10,000 Iteration Heap & Memory Leak Audit...');

if (global.gc) {
    global.gc();
}
const initialMem = process.memoryUsage();
const initialHeapMB = initialMem.heapUsed / (1024 * 1024);

const MEM_ITERATIONS = 10000;
for (let i = 0; i < MEM_ITERATIONS; i++) {
    const tk = allTickers[i % allTickers.length];
    selectTicker(tk);
}

if (global.gc) {
    global.gc();
}
const finalMem = process.memoryUsage();
const finalHeapMB = finalMem.heapUsed / (1024 * 1024);
const deltaHeapMB = finalHeapMB - initialHeapMB;

report.test5_memory_audit.iterations = MEM_ITERATIONS;
report.test5_memory_audit.initialHeapMB = initialHeapMB.toFixed(2);
report.test5_memory_audit.finalHeapMB = finalHeapMB.toFixed(2);
report.test5_memory_audit.deltaHeapMB = deltaHeapMB.toFixed(2);

console.log(`  -> Initial Heap: ${initialHeapMB.toFixed(2)} MB`);
console.log(`  -> Final Heap:   ${finalHeapMB.toFixed(2)} MB`);
console.log(`  -> Delta Heap:   ${deltaHeapMB.toFixed(2)} MB over ${MEM_ITERATIONS} switches`);

// -----------------------------------------------------------------------------
// TEST 6: UI State Machine Transitions (Mobile Tabs & Filter Pills)
// -----------------------------------------------------------------------------
console.log('\n[TEST 6] Testing UI State Machine Transitions...');
const mobileTabs = ['watchlist', 'chart', 'tactics', 'options', 'portfolio'];
const filterPills = ['all', 'entry', 'options', 'risk'];

mobileTabs.forEach(tab => {
    try {
        if (typeof switchMobileTab === 'function') {
            switchMobileTab(tab);
            report.test6_ui_state_machine.transitions++;
        }
    } catch (e) {
        report.test6_ui_state_machine.exceptions++;
        report.test6_ui_state_machine.errors.push(`switchMobileTab(${tab}) threw: ${e.message}`);
    }
});

filterPills.forEach(pill => {
    try {
        if (typeof filterRightCards === 'function') {
            filterRightCards(pill);
            report.test6_ui_state_machine.transitions++;
        }
    } catch (e) {
        report.test6_ui_state_machine.exceptions++;
        report.test6_ui_state_machine.errors.push(`filterRightCards(${pill}) threw: ${e.message}`);
    }
});
console.log(`  -> Completed ${report.test6_ui_state_machine.transitions} UI transitions. Exceptions: ${report.test6_ui_state_machine.exceptions}`);

// -----------------------------------------------------------------------------
// Overall Verdict
// -----------------------------------------------------------------------------
console.log('\n' + '='.repeat(80));
console.log(' 📊 EMPIRICAL CHALLENGER ADVERSARIAL TEST RESULTS SUMMARY');
console.log('='.repeat(80));
console.log(` Test 1 (Alternating 1,000x):      ${report.test1_alternating.exceptions} Exceptions, ${report.test1_alternating.remnants} Remnants`);
console.log(` Test 2 (Burst Asymmetric):       ${report.test2_burst_asymmetric.exceptions} Exceptions, ${report.test2_burst_asymmetric.remnants} Remnants`);
console.log(` Test 3 (Chaos 2,000x):            ${report.test3_random_chaos.exceptions} Exceptions, ${report.test3_random_chaos.remnants} Remnants`);
console.log(` Test 4 (Boundary Mutations):      ${report.test4_boundary_mutations.exceptions} Exceptions, ${report.test4_boundary_mutations.remnants} Recovery Remnants`);
console.log(` Test 5 (Memory 10,000x):          Delta Heap ${report.test5_memory_audit.deltaHeapMB} MB (Initial: ${report.test5_memory_audit.initialHeapMB} MB -> Final: ${report.test5_memory_audit.finalHeapMB} MB)`);
console.log(` Test 6 (UI State Transitions):    ${report.test6_ui_state_machine.exceptions} Exceptions across ${report.test6_ui_state_machine.transitions} transitions`);
console.log('='.repeat(80));

const totalExceptions = 
    report.test1_alternating.exceptions +
    report.test2_burst_asymmetric.exceptions +
    report.test3_random_chaos.exceptions +
    report.test4_boundary_mutations.exceptions +
    report.test6_ui_state_machine.exceptions;

const totalRemnants = 
    report.test1_alternating.remnants +
    report.test2_burst_asymmetric.remnants +
    report.test3_random_chaos.remnants +
    report.test4_boundary_mutations.remnants;

// Memory leak threshold: delta heap > 50MB across 10,000 switches would indicate unbounded accumulation
const isMemoryClean = Math.abs(parseFloat(report.test5_memory_audit.deltaHeapMB)) < 50.0;

const allPassed = (totalExceptions === 0 && totalRemnants === 0 && isMemoryClean);

if (!allPassed) {
    console.log('\n❌ ADVERSARIAL CHALLENGE FAILED:');
    if (report.test1_alternating.errors.length > 0) {
        console.log('Test 1 Errors:', report.test1_alternating.errors.slice(0, 5));
    }
    if (report.test2_burst_asymmetric.errors.length > 0) {
        console.log('Test 2 Errors:', report.test2_burst_asymmetric.errors.slice(0, 5));
    }
    if (report.test3_random_chaos.errors.length > 0) {
        console.log('Test 3 Errors:', report.test3_random_chaos.errors.slice(0, 5));
    }
    if (report.test4_boundary_mutations.errors.length > 0) {
        console.log('Test 4 Errors:', report.test4_boundary_mutations.errors);
    }
    if (report.test6_ui_state_machine.errors.length > 0) {
        console.log('Test 6 Errors:', report.test6_ui_state_machine.errors);
    }
    if (!isMemoryClean) {
        console.log(`Memory Leak Detected: Heap grew by ${report.test5_memory_audit.deltaHeapMB} MB!`);
    }
    process.exit(1);
} else {
    console.log('\n✅ EMPIRICAL CHALLENGE VERDICT: APPROVE');
    console.log('   - 0 uncaught exceptions across 13,000+ stress transitions');
    console.log('   - 0 style remnant leaks (#9ca3af, #ff5252, font sizes, text)');
    console.log('   - 100% resilient under extreme boundary/mutated inputs with instant clean recovery');
    console.log(`   - Stable memory footprint (${report.test5_memory_audit.deltaHeapMB} MB delta over 10k iterations)`);
    process.exit(0);
}
