# -*- coding: utf-8 -*-
"""Page-open helpers for the static radar dashboards.

The generators call these so a later daemon rewrite keeps the same loading
behavior. Nothing here changes quotes, model outputs, or alert delivery.
"""
import json
import re

# GitHub Pages caches HTML for 10 minutes (max-age=600). A query token is what
# actually bypasses that cache; <meta http-equiv="Cache-Control"> does not.
_REFRESH_JS = """
<script>
    // 定时换新 HTML，避开 GitHub Pages / Fastly 约 10 分钟强缓存。
    // 正在操作时最多再等 __EXTRA__ ms，避免整页重载打断点击和滚动。
    (function () {
        var INTERVAL = __INTERVAL__;
        var IDLE_MS = 12000;
        var EXTRA_CAP = __EXTRA__;
        var started = Date.now();
        var lastInput = 0;
        ["pointerdown", "keydown", "touchstart", "wheel"].forEach(function (ev) {
            window.addEventListener(ev, function () { lastInput = Date.now(); }, { passive: true });
        });
        function saveState() {
            if (!__SAVE_WL__) return;
            try {
                var wl = document.getElementById("watchlistContainer");
                if (wl) localStorage.setItem("futu_watchlist_scroll", String(wl.scrollTop));
            } catch (e) {}
        }
        function go() {
            saveState();
            try {
                var cleanPath = window.location.pathname;
                var searchParams = new URLSearchParams(window.location.search);
                if (__CLEAR_SYMBOL__) {
                    searchParams.delete("symbol");
                    searchParams.delete("ticker");
                }
                searchParams.set("_t", String(Date.now()));
                window.location.replace(cleanPath + "?" + searchParams.toString() + window.location.hash);
            } catch (e) {
                window.location.reload();
            }
        }
        function tick() {
            var now = Date.now();
            var elapsed = now - started;
            if (elapsed < INTERVAL) {
                setTimeout(tick, Math.min(1000, INTERVAL - elapsed));
                return;
            }
            var busy = !document.hidden && lastInput && (now - lastInput) < IDLE_MS;
            if (busy && elapsed < INTERVAL + EXTRA_CAP) {
                setTimeout(tick, 1000);
                return;
            }
            go();
        }
        setTimeout(tick, Math.min(1000, INTERVAL));
    })();
</script>
"""


def client_refresh_snippet(interval_ms, *, save_watchlist=False, clear_symbol_params=False):
    """Return a tiny head script that reloads on a timer without blocking paint."""
    extra = 45000
    return (
        _REFRESH_JS
        .replace("__INTERVAL__", str(int(interval_ms)))
        .replace("__EXTRA__", str(extra))
        .replace("__SAVE_WL__", "true" if save_watchlist else "false")
        .replace("__CLEAR_SYMBOL__", "true" if clear_symbol_params else "false")
    )


def terminal_ai_embed(ai_predictions):
    """Fields the terminal actually reads.

    The full snapshot also carries ``time_cycle_projections`` (~230KB) and
    ``all_predictions`` (a second copy of ``by_ticker``). The watchlist and the
    right-hand panel only use ``by_ticker`` plus the long/short leaderboards.
    The predict page keeps the full cycle payload.
    """
    if not isinstance(ai_predictions, dict):
        return {}
    embed = {}
    for key in ("by_ticker", "top10", "top_long", "top_short"):
        if key in ai_predictions:
            embed[key] = ai_predictions[key]
    return embed


_PLOTLY_RE = re.compile(
    r"<script[^>]*>\s*window\.PlotlyConfig\s*=\s*\{MathJaxConfig:\s*'local'\};\s*</script>\s*"
    r"<script(?P<attrs>[^>]*?)\ssrc=\"(?P<src>https://cdn\.plot\.ly/[^\"]+)\"(?P<attrs2>[^>]*)></script>\s*"
    r"(?P<div><div id=\"[^\"]+\" class=\"plotly-graph-div\"[^>]*></div>)\s*"
    r"<script>(?P<body>.*?)</script>",
    re.S,
)
_plotly_seq = 0


def _attr(blob, name):
    match = re.search(rf'\b{name}="([^"]*)"', blob)
    return match.group(1) if match else ""


def defer_plotly_cdn(fragment, status_text=None):
    """Turn Plotly's blocking CDN snippet into a post-paint loader.

    The chart div stays where the generator put it. The 3+ MB library is not
    requested until the parser has passed this point, so a following table can
    paint first. If the CDN fails, the status line says so and the table remains.
    """
    if not fragment or "cdn.plot.ly" not in fragment:
        return fragment

    def replacer(match):
        global _plotly_seq
        _plotly_seq += 1
        seq = _plotly_seq
        attrs = (match.group("attrs") or "") + (match.group("attrs2") or "")
        src = match.group("src")
        integrity = _attr(attrs, "integrity")
        status_id = f"chart-status-{seq}"
        payload_id = f"plotly-payload-{seq}"
        message = status_text or (
            "外部图表加载中（cdn.plot.ly）。下方表格可以先看；"
            "若这里一直停着，就是图表 CDN 没连上，表格数据仍然有效。"
        )
        fail_message = (
            "图表库加载失败：连不上 cdn.plot.ly。表格和指标仍可使用，图表交互暂时不可用。"
        )
        body = match.group("body")
        # Keep the generated newPlot call inert until Plotly exists.
        payload = (
            "try {\n"
            + body
            + "\n} catch (e) {\n"
            + f"  if (window.__radarChartFail_{seq}) window.__radarChartFail_{seq}(String(e && e.message || e));\n"
            + "}\n"
        )
        integrity_js = json.dumps(integrity)
        return f"""<div class="chart-deferred">
  <p class="chart-status" id="{status_id}" style="margin:0 0 8px; color:#9ca3af; font-size:13px; line-height:1.45;">{message}</p>
  {match.group("div")}
  <script type="text/plain" id="{payload_id}">{payload}</script>
  <script>
    (function () {{
      var STATUS = {json.dumps(status_id)};
      var PAYLOAD = {json.dumps(payload_id)};
      var SRC = {json.dumps(src)};
      var INTEGRITY = {integrity_js};
      var FAIL = {json.dumps(fail_message, ensure_ascii=False)};
      function setStatus(msg) {{
        var el = document.getElementById(STATUS);
        if (!el) return;
        el.textContent = msg;
        el.style.color = "#f85149";
      }}
      window.__radarChartFail_{seq} = function () {{ setStatus(FAIL); }};
      function run() {{
        var s = document.createElement("script");
        s.src = SRC;
        s.async = true;
        s.crossOrigin = "anonymous";
        if (INTEGRITY) s.integrity = INTEGRITY;
        s.onload = function () {{
          if (!window.Plotly) {{ setStatus(FAIL); return; }}
          var payload = document.getElementById(PAYLOAD);
          if (!payload) {{ setStatus(FAIL); return; }}
          var node = document.createElement("script");
          node.text = payload.textContent;
          document.body.appendChild(node);
          var el = document.getElementById(STATUS);
          if (el && el.style.color !== "rgb(248, 81, 73)" && el.style.color !== "#f85149") el.remove();
        }};
        s.onerror = function () {{ setStatus(FAIL); }};
        document.head.appendChild(s);
      }}
      if (window.requestAnimationFrame) {{
        requestAnimationFrame(function () {{ requestAnimationFrame(run); }});
      }} else {{
        setTimeout(run, 0);
      }}
    }})();
  </script>
</div>"""

    updated, count = _PLOTLY_RE.subn(replacer, fragment, count=1)
    return updated if count else fragment


def plotly_boot_js(draw_fn, src, status_ids):
    """JS that loads Plotly after first paint, then calls ``draw_fn``."""
    ids = json.dumps(list(status_ids))
    fail = json.dumps(
        "图表库加载失败：连不上 cdn.plot.ly。表格和指标仍可使用，图表交互暂时不可用。",
        ensure_ascii=False,
    )
    return f"""
    (function () {{
      var IDS = {ids};
      var FAIL = {fail};
      function setStatus(msg) {{
        IDS.forEach(function (id) {{
          var el = document.getElementById(id);
          if (!el) return;
          el.textContent = msg;
          el.style.color = "#f85149";
        }});
      }}
      function start() {{
        var s = document.createElement("script");
        s.src = {json.dumps(src)};
        s.async = true;
        s.onload = function () {{
          if (!window.Plotly || typeof {draw_fn} !== "function") {{ setStatus(FAIL); return; }}
          try {{
            {draw_fn}();
            IDS.forEach(function (id) {{
              var el = document.getElementById(id);
              if (el) el.remove();
            }});
          }} catch (e) {{
            setStatus(FAIL);
          }}
        }};
        s.onerror = function () {{ setStatus(FAIL); }};
        document.head.appendChild(s);
      }}
      if (window.requestAnimationFrame) {{
        requestAnimationFrame(function () {{ requestAnimationFrame(start); }});
      }} else {{
        setTimeout(start, 0);
      }}
    }})();
"""


def futu_tv_loader_js(tv_timezone, tv_timezone_query):
    """TradingView loader that does not block the watchlist script.

    The widget options stay the same, including the Beijing display timezone.
    """
    js = r"""
        let tvLoadPromise = null;
        function ensureTradingView(timeoutMs) {
            if (window.TradingView && typeof window.TradingView.widget === "function") {
                return Promise.resolve(true);
            }
            if (tvLoadPromise) return tvLoadPromise;
            tvLoadPromise = new Promise(function (resolve) {
                var done = false;
                function finish(ok) {
                    if (done) return;
                    done = true;
                    resolve(!!ok && !!(window.TradingView && window.TradingView.widget));
                }
                var s = document.createElement("script");
                s.src = "https://s3.tradingview.com/tv.js";
                s.async = true;
                s.onload = function () { finish(true); };
                s.onerror = function () { finish(false); };
                setTimeout(function () { finish(false); }, timeoutMs || 8000);
                document.head.appendChild(s);
            });
            return tvLoadPromise;
        }

        let tvMountSeq = 0;
        function showTvFailure(container, tvSymbol, interval, style, reason) {
            const targetSession = "extended";
            const iframeUrl = `https://s.tradingview.com/widgetembed/?frameElementId=tv_widget&symbol=${encodeURIComponent(tvSymbol)}&interval=${interval}&hidesidetoolbar=0&symboledit=1&saveimage=0&toolbarbg=14161b&theme=dark&style=${style||'1'}&timezone=__TZQ__&locale=zh_CN&extended_hours=1&sessionId=${targetSession}`;
            const extUrl = "https://www.tradingview.com/chart/?symbol=" + encodeURIComponent(tvSymbol);
            container.innerHTML = `<div class="tv-fallback" style="height:100%; min-height:160px; display:flex; flex-direction:column; gap:8px; padding:12px; box-sizing:border-box; color:#d1d5db; background:#14161b;">
                <div style="font-size:13px; line-height:1.45;">主图未能从 TradingView 加载（${reason}）。左侧列表、报价和量化面板可以继续使用。</div>
                <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
                    <a href="${extUrl}" target="_blank" rel="noopener" style="color:#60a5fa;">在 TradingView 打开</a>
                    <button type="button" id="tvRetryBtn" style="background:#1f2937; color:#e5e7eb; border:1px solid #374151; border-radius:4px; padding:2px 8px; cursor:pointer;">重试图表</button>
                </div>
                <iframe title="TradingView" src="${iframeUrl}" style="flex:1; width:100%; min-height:220px; border:none;"></iframe>
            </div>`;
            var retry = document.getElementById("tvRetryBtn");
            if (retry) {
                retry.onclick = function () {
                    tvLoadPromise = null;
                    loadTvWidget(currentTicker, currentInterval, currentStyle);
                };
            }
        }

        // 初始化 TradingView 图表挂载 (默认开启 Pre/Post 盘前盘后延展时段)
        function loadTvWidget(ticker, interval, style) {
            const container = document.getElementById("tv_chart_container");
            if (!container) return;
            const tvSymbol = SYMBOL_MAP[ticker] || ("NASDAQ:" + ticker);
            const targetSession = "extended";
            const seq = ++tvMountSeq;
            const extUrl = "https://www.tradingview.com/chart/?symbol=" + encodeURIComponent(tvSymbol);
            container.innerHTML = `<div class="tv-status" style="height:100%; min-height:120px; display:flex; align-items:center; justify-content:center; color:#9ca3af; font-size:13px; padding:16px; text-align:center; line-height:1.45;">图表加载中，列表和指标可先使用。<a href="${extUrl}" target="_blank" rel="noopener" style="color:#60a5fa; margin-left:6px;">TradingView ↗</a></div>`;
            ensureTradingView(8000).then(function (ok) {
                if (seq !== tvMountSeq) return;
                if (!ok) {
                    showTvFailure(container, tvSymbol, interval, style, "脚本超时或网络失败");
                    return;
                }
                try {
                    container.innerHTML = "";
                    new TradingView.widget({
                        "autosize": true,
                        "symbol": tvSymbol,
                        "interval": interval,
                        "timezone": "__TZ__",
                        "theme": "dark",
                        "style": style || "1",
                        "locale": "zh_CN",
                        "toolbar_bg": "#14161b",
                        "enable_publishing": false,
                        "hide_side_toolbar": false,
                        "allow_symbol_change": true,
                        "save_image": false,
                        "extended_hours": true,
                        "container_id": "tv_chart_container",
                        "enabled_features": [
                            "pre_post_market_sessions"
                        ],
                        "overrides": {
                            "mainSeriesProperties.sessionId": targetSession
                        },
                        "studies": [
                            "MASimple@tv-basicstudies",
                            "MACD@tv-basicstudies",
                            "RSI@tv-basicstudies"
                        ]
                    });
                } catch (e) {
                    showTvFailure(container, tvSymbol, interval, style, "组件初始化失败");
                }
            });
        }
"""
    return (
        js.replace("__TZQ__", tv_timezone_query)
        .replace("__TZ__", tv_timezone)
    )


PREDICT_BOOT_JS = """
<script>
    // 周期图脚本约 270KB。先把表和导航画出来，再编译这段脚本。
    (function () {
        function start() {
            var src = document.getElementById("predict-app");
            if (!src || src.getAttribute("data-ran") === "1") return;
            src.setAttribute("data-ran", "1");
            var s = document.createElement("script");
            s.text = src.textContent;
            document.body.appendChild(s);
        }
        if (window.requestAnimationFrame) {
            requestAnimationFrame(function () { requestAnimationFrame(start); });
        } else {
            setTimeout(start, 0);
        }
    })();
</script>
"""


_FUTU_HEAD_RE = re.compile(
    r"[ \t]*<meta http-equiv=\"refresh\"[^>]*>\s*"
    r"(?:[ \t]*<meta http-equiv=\"(?:Cache-Control|Pragma|Expires)\"[^>]*>\s*)*"
    r"[ \t]*<script>\s*// ⚡ 客户端智能防缓存自动刷新.*?</script>",
    re.S,
)
_PREDICT_HEAD_RE = re.compile(
    r"[ \t]*<meta http-equiv=\"(?:Cache-Control|Pragma|Expires)\"[^>]*>\s*"
    r"(?:[ \t]*<meta http-equiv=\"(?:Cache-Control|Pragma|Expires)\"[^>]*>\s*)*"
    r"[ \t]*<script>\s*// ⚡ 客户端智能防缓存自动刷新.*?</script>",
    re.S,
)


def rewrite_futu_document(html, tv_timezone, tv_timezone_query):
    """Apply the terminal loading changes to an already rendered document."""
    refresh = client_refresh_snippet(60_000, save_watchlist=True, clear_symbol_params=True)
    html, n_head = _FUTU_HEAD_RE.subn(refresh.strip() + "\n", html, count=1)
    if n_head != 1:
        raise RuntimeError(f"futu refresh head not found ({n_head})")
    html = html.replace(
        '    <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>\n',
        "    <!-- TradingView tv.js 在列表渲染后按需加载，失败时主图区会说明原因 -->\n",
    )
    start = html.find("        function loadTvWidget(")
    end = html.find("        // 盯盘状态 UI 刷新函数")
    if start < 0 or end < 0 or end < start:
        raise RuntimeError("loadTvWidget block not found")
    # Drop the comment immediately above the old function if it is the old banner.
    banner = "        // 初始化 TradingView 图表挂载 (默认开启 Pre/Post 盘前盘后延展时段)\n"
    if html.startswith(banner, start - len(banner)):
        start -= len(banner)
    html = html[:start] + futu_tv_loader_js(tv_timezone, tv_timezone_query).strip() + "\n\n" + html[end:]
    key = "const AI_PREDICTIONS = "
    idx = html.find(key)
    if idx < 0:
        raise RuntimeError("AI_PREDICTIONS not found")
    payload, end_rel = json.JSONDecoder().raw_decode(html[idx + len(key):])
    slim = json.dumps(terminal_ai_embed(payload), ensure_ascii=False, separators=(",", ":"))
    html = html[: idx + len(key)] + slim + html[idx + len(key) + end_rel :]
    return html


def rewrite_predict_document(html):
    """Idle-aware refresh, and defer the cycle-chart script until after first paint."""
    refresh = client_refresh_snippet(60_000)
    html, n_head = _PREDICT_HEAD_RE.subn(refresh.strip() + "\n", html, count=1)
    if n_head != 1:
        raise RuntimeError(f"predict refresh head not found ({n_head})")
    old_open = "    <script>\n        let showFullPool = false;"
    new_open = '    <script type="text/plain" id="predict-app">\n        let showFullPool = false;'
    if old_open not in html:
        raise RuntimeError("predict app script not found")
    html = html.replace(old_open, new_open, 1)
    old_boot = """        document.addEventListener('DOMContentLoaded' , function() {
            let savedView = 'dual';
            try {
                savedView = localStorage.getItem('predict_t4_view') || 'dual';
            } catch(e) {}
            switchT4View(savedView);
            setTimeout(function() { renderTimeCycleView(); }, 50);
            window.addEventListener('resize', function() { renderTimeCycleView(); });
        });"""
    new_boot = """        function bootPredictPage() {
            let savedView = 'dual';
            try {
                savedView = localStorage.getItem('predict_t4_view') || 'dual';
            } catch(e) {}
            switchT4View(savedView);
            setTimeout(function() { renderTimeCycleView(); }, 50);
            window.addEventListener('resize', function() { renderTimeCycleView(); });
        }
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", bootPredictPage);
        } else {
            bootPredictPage();
        }"""
    if old_boot not in html:
        raise RuntimeError("predict DOMContentLoaded boot not found")
    html = html.replace(old_boot, new_boot, 1)
    if "predict-app-boot" not in html:
        html = html.replace(
            "</body>",
            PREDICT_BOOT_JS.replace("<script>", '<script id="predict-app-boot">', 1) + "\n</body>",
            1,
        )
    return html
