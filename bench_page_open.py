#!/usr/bin/env python3
"""Headless Chrome page-open benchmark for the radar dashboards.

Modes:
  desktop  — this machine, cache disabled, no CPU/network throttle
  blocked  — chart CDNs forced to a closed port (external chart failure)
  mobile   — 4x CPU throttle and ~1.6 Mbps / 150 ms RTT (lab stand-in, not a real phone)
"""
import argparse
import asyncio
import json
import os
import signal
import subprocess
import time
import urllib.request

import websockets

INJECT = r"""
window.__radarMarks = {};
window.__radarErrors = [];
(function () {
  function mark(name) {
    if (window.__radarMarks[name] == null) window.__radarMarks[name] = performance.now();
  }
  setInterval(function () {
    if (document.querySelector('.futu-header, .header h1, .suite-nav, .title-box h1, h1')) mark('shell');
    var wl = document.querySelectorAll('#watchlistContainer .stock-item').length;
    var rows = document.querySelectorAll('table tbody tr, #t4-tbody tr').length;
    if (wl >= 5 || rows >= 5) mark('content');
    if (typeof renderWatchlist === 'function' || typeof filterBySector === 'function' || typeof switchT4View === 'function' || rows >= 5) {
      if (wl >= 5 || rows >= 5 || document.querySelector('.futu-header') && typeof renderWatchlist === 'function' && wl >= 1) mark('interactive');
    }
    if (document.querySelector('.js-plotly-plot .main-svg, .plotly .main-svg')) mark('chart');
    var slot = document.querySelector('#tv_chart_container');
    if (slot && (slot.querySelector('iframe') || slot.querySelector('.tv-fallback') || (window.TradingView && slot.childNodes.length))) mark('chartSlot');
  }, 16);
  window.addEventListener('error', function (e) {
    window.__radarErrors.push(String((e && e.message) || 'error').slice(0, 240));
  }, true);
})();
"""

SNAPSHOT = r"""
(() => {
  const nav = performance.getEntriesByType('navigation')[0];
  const paints = performance.getEntriesByType('paint').map(p => [p.name, Math.round(p.startTime)]);
  const resources = performance.getEntriesByType('resource').map(r => ({
    n: r.name.split('/').slice(-1)[0].slice(0, 80),
    host: (r.name.split('/')[2] || ''),
    dur: Math.round(r.duration),
    start: Math.round(r.startTime),
    transfer: r.transferSize || 0,
    encoded: r.encodedBodySize || 0
  }));
  return {
    marks: window.__radarMarks || {},
    errors: window.__radarErrors || [],
    paints,
    wl: document.querySelectorAll('#watchlistContainer .stock-item').length,
    rows: document.querySelectorAll('table tbody tr').length,
    plotly: typeof Plotly !== 'undefined',
    tv: typeof TradingView !== 'undefined',
    chartSvg: !!document.querySelector('.js-plotly-plot .main-svg, .plotly .main-svg'),
    tvIframe: !!document.querySelector('#tv_chart_container iframe'),
    nav: nav ? {
      encoded: nav.encodedBodySize,
      decoded: nav.decodedBodySize,
      transfer: nav.transferSize,
      responseEnd: Math.round(nav.responseEnd),
      domInteractive: Math.round(nav.domInteractive),
      dcl: Math.round(nav.domContentLoadedEventEnd),
      load: Math.round(nav.loadEventEnd)
    } : null,
    resources
  };
})()
"""


async def cdp(ws, method, params=None, timeout=30):
    cdp.n = getattr(cdp, "n", 0) + 1
    mid = cdp.n
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
        msg = json.loads(raw)
        if msg.get("id") == mid:
            if "error" in msg:
                raise RuntimeError(f"{method} {msg['error']}")
            return msg.get("result") or {}
    raise TimeoutError(method)


def launch_chrome(port, mode):
    user = f"/tmp/chrome-radar-{mode}-{port}"
    os.makedirs(user, exist_ok=True)
    cmd = [
        "google-chrome",
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--disable-extensions",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user}",
        "--disk-cache-size=1",
        "--media-cache-size=1",
    ]
    if mode == "blocked":
        cmd.append("--host-resolver-rules=MAP cdn.plot.ly 127.0.0.1:9, MAP s3.tradingview.com 127.0.0.1:9, MAP s.tradingview.com 127.0.0.1:9, MAP www.tradingview.com 127.0.0.1:9")
    cmd.append("about:blank")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.3) as r:
                info = json.loads(r.read().decode())
                return proc, info["webSocketDebuggerUrl"]
        except Exception:
            time.sleep(0.1)
    proc.send_signal(signal.SIGKILL)
    raise RuntimeError("chrome did not start")


async def measure_page(ws_url, url, mode, wait_s):
    async with websockets.connect(ws_url, max_size=20_000_000) as ws:
        await cdp(ws, "Target.setDiscoverTargets", {"discover": True})
        created = await cdp(ws, "Target.createTarget", {"url": "about:blank"})
        tid = created["targetId"]
        # attach to the page target
        sess = await cdp(ws, "Target.attachToTarget", {"targetId": tid, "flatten": True})
        sid = sess["sessionId"]

        async def send(method, params=None, timeout=30):
            cdp.n = getattr(cdp, "n", 0) + 1
            mid = cdp.n
            await ws.send(json.dumps({
                "id": mid,
                "method": method,
                "params": params or {},
                "sessionId": sid,
            }))
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
                msg = json.loads(raw)
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(f"{method} {msg['error']}")
                    return msg.get("result") or {}
            raise TimeoutError(method)

        await send("Page.enable")
        await send("Runtime.enable")
        await send("Network.enable")
        await send("Network.setCacheDisabled", {"cacheDisabled": True})
        if mode == "mobile":
            await send("Emulation.setCPUThrottlingRate", {"rate": 4})
            await send("Network.emulateNetworkConditions", {
                "offline": False,
                "latency": 150,
                "downloadThroughput": int(1.6 * 1024 * 1024 / 8),
                "uploadThroughput": int(750 * 1024 / 8),
            })
        await send("Page.addScriptToEvaluateOnNewDocument", {"source": INJECT})
        await send("Page.navigate", {"url": url})
        deadline = time.time() + wait_s
        last = {}
        while time.time() < deadline:
            await asyncio.sleep(0.25)
            try:
                ev = await send("Runtime.evaluate", {
                    "expression": SNAPSHOT,
                    "returnByValue": True,
                }, timeout=8)
                last = (ev.get("result") or {}).get("value") or {}
            except Exception as exc:
                last = {"evalError": str(exc)}
                continue
            # stop early when content is up and either chart settled or we are blocked
            marks = last.get("marks") or {}
            if mode == "blocked" and marks.get("content") and time.time() > deadline - wait_s + 3:
                break
            if mode != "blocked" and marks.get("content") and (marks.get("chart") or marks.get("chartSlot") or last.get("plotly") or last.get("tv")):
                # give chart a moment, then stop if both content and chart exist
                if marks.get("chart") or marks.get("chartSlot"):
                    await asyncio.sleep(0.3)
                    break
            if marks.get("content") and "backtest" not in url and "macro" not in url and "stocks" not in url and "predict" in url:
                break
        await send("Target.closeTarget", {}) if False else None
        try:
            await cdp(ws, "Target.closeTarget", {"targetId": tid})
        except Exception:
            pass
        return last


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8766")
    ap.add_argument("--mode", default="desktop", choices=["desktop", "blocked", "mobile"])
    ap.add_argument("--label", default="before")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages", nargs="*", default=[
        "index.html", "futu_pro_terminal.html", "macro_radar.html",
        "stocks_radar.html", "predict_radar_5y.html", "backtest_report_5y.html",
    ])
    ap.add_argument("--wait", type=float, default=20)
    args = ap.parse_args()
    port = 9333 + (abs(hash(args.mode + args.label)) % 500)
    proc, ws = launch_chrome(port, args.mode)
    results = {"label": args.label, "mode": args.mode, "base": args.base, "pages": {}}
    try:
        for page in args.pages:
            url = args.base.rstrip("/") + "/" + page
            t0 = time.perf_counter()
            try:
                data = await measure_page(ws, url, args.mode, args.wait)
            except Exception as exc:
                data = {"fatal": str(exc)}
            data["wall_s"] = round(time.perf_counter() - t0, 2)
            results["pages"][page] = data
            marks = data.get("marks") or {}
            print(f"{args.label}/{args.mode} {page} wall={data['wall_s']}s shell={marks.get('shell')} content={marks.get('content')} chart={marks.get('chart')} slot={marks.get('chartSlot')} rows={data.get('rows')} wl={data.get('wl')} dcl={((data.get('nav') or {}).get('dcl'))} errs={len(data.get('errors') or [])}", flush=True)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f)
    print("wrote", args.out)


if __name__ == "__main__":
    asyncio.run(main())
