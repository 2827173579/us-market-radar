/**
 * test_frontend_stress.js - Comprehensive Node.js Frontend Stress Test for Futu Pro Terminal
 *
 * Requirements (from ORIGINAL_REQUEST.md & PROJECT.md M0/Acceptance Criteria):
 * - Loads futu_pro_terminal.html
 * - Iterates through all 66 tickers calling selectTicker(ticker)
 * - Checks for 0 uncaught exceptions across all 66 tickers (66/66 PASS)
 * - Checks for DOM remnants: verifies that switching from a crash-halt ticker (e.g. SMH, SOXL)
 *   to a normal ticker (e.g. IGV, AAPL) does NOT leave pBatch1Shares colored gray (#9ca3af)
 *   or pBatch1Val colored red (#ff5252)
 * - Verifies intcTacticalBox isolation and optionsAnomalyBox visibility
 * - Outputs pass/fail count and exits with code 0 on success, code 1 on failure.
 *
 * Usage:
 *   node test_frontend_stress.js
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

console.log('='.repeat(80));
console.log(' 🚀 FUTU PRO TERMINAL FRONTEND STRESS TEST (66 TICKERS)');
console.log('='.repeat(80));

// 1. Load HTML file
const htmlPath = path.resolve(__dirname, 'futu_pro_terminal.html');
if (!fs.existsSync(htmlPath)) {
    console.error(`[FATAL] Terminal HTML not found at: ${htmlPath}`);
    process.exit(1);
}

const html = fs.readFileSync(htmlPath, 'utf8');

// 2. Extract Main Script
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

// 3. Build Realistic Mock DOM
const domNodes = {};

function createMockElement(id) {
    return {
        id: id,
        innerText: '',
        innerHTML: '',
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
            },
            contains(c) { return this.classes.has(c); }
        },
        dataset: {},
        onclick: null,
        appendChild(child) {},
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
    'watchlistContainer', 'notifListContainer', 'notifBadge', 'notifTotalCount'
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
    window: {
        innerWidth: 1920,
        innerHeight: 1080,
        location: { hostname: 'localhost', origin: 'http://localhost:8765', protocol: 'http:' },
        addEventListener: () => {},
        setInterval: () => {},
        setTimeout: () => {}
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
            return [];
        },
        createElement: (tag) => createMockElement('dyn_' + tag),
        addEventListener: () => {}
    },
    localStorage: mockLocalStorage,
    TradingView: {
        widget: function(opts) {}
    },
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
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

if (typeof selectTicker !== 'function') {
    console.error('[FATAL] selectTicker function not exported to VM global scope');
    process.exit(1);
}

const macroTickers = Object.keys(macroData);
const stockTickers = stocksData.map(s => s.ticker);
const allTickers = [...macroTickers, ...stockTickers];

console.log(`[INFO] Loaded Ticker Universe: ${allTickers.length} tickers (${macroTickers.length} Macro/ETFs + ${stockTickers.length} Equities)`);

// Populate stock-item mocks
allTickers.forEach(t => {
    const item = createMockElement('item_' + t);
    item.dataset.ticker = t;
    stockItems.push(item);
});

// -----------------------------------------------------------------------------
// Phase 1: Sequential Stress Testing Across All 66 Tickers
// -----------------------------------------------------------------------------
let passCount = 0;
let failCount = 0;
const uncaughtExceptions = [];
const sequentialRemnants = [];

allTickers.forEach((ticker, idx) => {
    try {
        selectTicker(ticker);

        // 1. Symbol Header Verification
        if (domNodes.curSym.innerText !== ticker) {
            throw new Error(`curSym mismatch: expected '${ticker}', got '${domNodes.curSym.innerText}'`);
        }

        const data = stocksData.find(s => s.ticker === ticker) || macroData[ticker];

        // 2. intcTacticalBox isolation: only displayed if ticker is held (shares > 0)
        const isHeld = Boolean(data && data.shares && data.shares > 0);
        if (!isHeld && domNodes.intcTacticalBox.style.display !== 'none') {
            sequentialRemnants.push({
                ticker,
                issue: 'intcTacticalBox remained visible for unheld position'
            });
        }

        // 3. optionsAnomalyBox visibility: only displayed if ticker has options
        const hasOptions = Boolean(data && data.has_options);
        if (!hasOptions && domNodes.optionsAnomalyBox.style.display !== 'none') {
            sequentialRemnants.push({
                ticker,
                issue: 'optionsAnomalyBox remained visible for ticker without options data'
            });
        }

        // 4. Style Remnants Verification (pBatch1Shares & pBatch1Val)
        const isCrashHalt = Boolean(data && (data.is_crash_halt || (data.optimal_limit === 0 && data.limit_note && data.limit_note.includes("破位"))));
        const isCoolingHalt = Boolean(data && (data.is_cooling_halt || (data.intraday_status_text && data.intraday_status_text.includes("冷却期"))));

        if (!isCrashHalt && !isCoolingHalt) {
            if (domNodes.pBatch1Shares.style.color === '#9ca3af') {
                sequentialRemnants.push({
                    ticker,
                    issue: `pBatch1Shares color remained gray (#9ca3af) from previous crash-halt state`
                });
            }
            if (domNodes.pBatch1Val.style.color === '#ff5252') {
                sequentialRemnants.push({
                    ticker,
                    issue: `pBatch1Val color remained red (#ff5252) from previous crash-halt state`
                });
            }
        }

        passCount++;
    } catch (err) {
        failCount++;
        uncaughtExceptions.push({ ticker, error: err.message, stack: err.stack });
    }
});

// -----------------------------------------------------------------------------
// Phase 2: Explicit Crash-to-Normal Transition Stress Test
// -----------------------------------------------------------------------------
const crashTickers = allTickers.filter(t => {
    const data = stocksData.find(s => s.ticker === t) || macroData[t];
    return Boolean(data && (data.is_crash_halt || (data.optimal_limit === 0 && data.limit_note && data.limit_note.includes("破位"))));
});

const normalTickers = allTickers.filter(t => {
    const data = stocksData.find(s => s.ticker === t) || macroData[t];
    return Boolean(data && !data.is_crash_halt && !(data.optimal_limit === 0 && data.limit_note && data.limit_note.includes("破位")));
});

console.log(`[INFO] Identified ${crashTickers.length} Crash-Halt Tickers and ${normalTickers.length} Normal Tickers`);

const transitionRemnants = [];

// Pair each crash ticker with key normal tickers (e.g. IGV, AAPL, MSFT, GOOGL)
const probeNormalTickers = normalTickers.filter(t => ['IGV', 'AAPL', 'MSFT', 'GOOGL', 'NVDA', 'QQQM'].includes(t));

crashTickers.forEach(crashTk => {
    probeNormalTickers.forEach(normTk => {
        // Step 1: Select crash ticker
        selectTicker(crashTk);

        // Step 2: Select normal ticker
        selectTicker(normTk);

        // Step 3: Assert clean state restoration
        if (domNodes.pBatch1Shares.style.color === '#9ca3af') {
            transitionRemnants.push({
                from: crashTk,
                to: normTk,
                issue: `pBatch1Shares stayed gray (#9ca3af) when transitioning ${crashTk} -> ${normTk}`
            });
        }
        if (domNodes.pBatch1Val.style.color === '#ff5252') {
            transitionRemnants.push({
                from: crashTk,
                to: normTk,
                issue: `pBatch1Val stayed red (#ff5252) when transitioning ${crashTk} -> ${normTk}`
            });
        }
        if (domNodes.pOptimalLimit.style.color === '#ff5252') {
            transitionRemnants.push({
                from: crashTk,
                to: normTk,
                issue: `pOptimalLimit stayed red (#ff5252) when transitioning ${crashTk} -> ${normTk}`
            });
        }
    });
});

// -----------------------------------------------------------------------------
// Summary & Verdict
// -----------------------------------------------------------------------------
console.log('\n' + '='.repeat(80));
console.log(' 📊 FRONTEND STRESS TEST RESULTS');
console.log('='.repeat(80));
console.log(` Tickers Evaluated:               ${allTickers.length}/66`);
console.log(` Successful Selections (No Throw): ${passCount}/${allTickers.length}`);
console.log(` Uncaught Exceptions:             ${uncaughtExceptions.length}`);
console.log(` Sequential Style Remnants:       ${sequentialRemnants.length}`);
console.log(` Cross-Transition Remnants:       ${transitionRemnants.length}`);
console.log('='.repeat(80));

if (uncaughtExceptions.length > 0) {
    console.log('\n❌ Uncaught Exceptions Detected:');
    uncaughtExceptions.forEach(e => console.log(`  - [${e.ticker}]: ${e.error}`));
}

if (sequentialRemnants.length > 0) {
    console.log(`\n❌ Sequential Style Remnants Detected (${sequentialRemnants.length}):`);
    sequentialRemnants.slice(0, 10).forEach(r => console.log(`  - [${r.ticker}]: ${r.issue}`));
    if (sequentialRemnants.length > 10) {
        console.log(`    ... and ${sequentialRemnants.length - 10} more`);
    }
}

if (transitionRemnants.length > 0) {
    console.log(`\n❌ Cross-Transition Remnants Detected (${transitionRemnants.length}):`);
    transitionRemnants.slice(0, 10).forEach(r => console.log(`  - [${r.from} -> ${r.to}]: ${r.issue}`));
    if (transitionRemnants.length > 10) {
        console.log(`    ... and ${transitionRemnants.length - 10} more`);
    }
}

const totalRemnants = sequentialRemnants.length + transitionRemnants.length;
const isSuccess = (uncaughtExceptions.length === 0 && totalRemnants === 0);

if (isSuccess) {
    console.log('\n✅ 100% PASS: All 66 tickers switched cleanly with 0 exceptions and 0 DOM remnants.');
    process.exit(0);
} else {
    console.log(`\n⚠️  TEST FAILED: ${uncaughtExceptions.length} exceptions, ${totalRemnants} DOM remnant defects found.`);
    process.exit(1);
}
