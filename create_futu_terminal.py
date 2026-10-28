# -*- coding: utf-8 -*-
"""
Helper module to build the Futu-Style Pro Terminal (futu_pro_terminal.html)
支持 55 只全量标的池 (12 ETF + 43 个股)，全周期切换与分钟下拉菜单
"""
import json

def build_futu_terminal_html(stocks_data, macro_data, weather_info, update_time_str="", has_realtime=False, target_watchlist=None, user_portfolio=None, terminal_notifications=None, session_code="REGULAR", session_name="常规盘中"):
    is_overnight = (session_code == "OVERNIGHT")
    if session_code == "OVERNIGHT":
        session_badge_html = '<span class="tool-link" style="color: #c084fc; border: 1px solid rgba(192, 132, 252, 0.45); background: rgba(192, 132, 252, 0.12); padding: 4px 7px; font-weight: 700;">🌌 美股夜盘</span>'
    elif session_code == "POST_MARKET":
        session_badge_html = '<span class="tool-link" style="color: #93c5fd; border: 1px solid rgba(147, 197, 253, 0.4); background: rgba(147, 197, 253, 0.1); padding: 4px 7px; font-weight: 700;">🌙 盘后交易</span>'
    elif session_code == "PRE_MARKET":
        session_badge_html = '<span class="tool-link" style="color: #fb923c; border: 1px solid rgba(251, 146, 60, 0.4); background: rgba(251, 146, 60, 0.1); padding: 4px 7px; font-weight: 700;">🌅 盘前交易</span>'
    elif session_code == "WEEKEND":
        session_badge_html = '<span class="tool-link" style="color: #9ca3af; border: 1px solid rgba(156, 163, 175, 0.4); background: rgba(156, 163, 175, 0.1); padding: 4px 7px;">☕ 周末休市</span>'
    else:
        session_badge_html = '<span class="tool-link" style="color: #4ade80; border: 1px solid rgba(74, 222, 128, 0.4); background: rgba(74, 222, 128, 0.1); padding: 4px 7px; font-weight: 700;">🟢 常规盘中</span>'

    if target_watchlist is None:
        try:
            import os
            sc_dir = os.path.dirname(os.path.abspath(__file__))
            w_file = os.path.join(sc_dir, "target_watchlist.json")
            if os.path.exists(w_file):
                with open(w_file, "r", encoding="utf-8") as f:
                    target_watchlist = json.load(f)
            else:
                target_watchlist = []
        except Exception:
            target_watchlist = []

    if user_portfolio is None:
        try:
            import os
            sc_dir = os.path.dirname(os.path.abspath(__file__))
            p_file = os.path.join(sc_dir, "user_portfolio.json")
            if os.path.exists(p_file):
                with open(p_file, "r", encoding="utf-8") as f:
                    user_portfolio = json.load(f)
            else:
                user_portfolio = {}
        except Exception:
            user_portfolio = {}

    if terminal_notifications is None:
        try:
            import os
            sc_dir = os.path.dirname(os.path.abspath(__file__))
            n_file = os.path.join(sc_dir, ".terminal_notifications.json")
            if os.path.exists(n_file):
                with open(n_file, "r", encoding="utf-8") as f:
                    terminal_notifications = json.load(f)
            else:
                terminal_notifications = []
        except Exception:
            terminal_notifications = []

    # 将标的数据转为 JSON 供前端动态渲染
    stocks_json = json.dumps(stocks_data, ensure_ascii=False)
    macro_json = json.dumps(macro_data, ensure_ascii=False)
    weather_json = json.dumps(weather_info, ensure_ascii=False)
    watchlist_json = json.dumps(target_watchlist, ensure_ascii=False)
    portfolio_json = json.dumps(user_portfolio, ensure_ascii=False)
    notifications_json = json.dumps(terminal_notifications, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="90">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <script>
        // ⚡ 客户端智能防缓存自动刷新 (彻底突破 GitHub Pages / Fastly CDN 10分钟 max-age=600 强缓存)
        setTimeout(function() {{
            try {{
                var cleanPath = window.location.pathname;
                var searchParams = new URLSearchParams(window.location.search);
                searchParams.set('_t', Date.now());
                window.location.replace(cleanPath + '?' + searchParams.toString() + window.location.hash);
            }} catch(e) {{
                window.location.reload();
            }}
        }}, 60000);
    </script>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="富途雷达">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="theme-color" content="#0b0e14">
    <link rel="manifest" href="/manifest.json">
    <title>富途牛牛风格 · 专业全周期量化看盘终端 (Pro Terminal · 55标的全量版)</title>
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            background-color: #111317;
            color: #d1d5db;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            user-select: none;
        }}

        /* 顶部导航与周期控制栏 (富途牛牛原版配色与交互) */
        .futu-header {{
            background: #16181d;
            border-bottom: 1px solid #23272e;
            height: 48px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 16px;
            flex-shrink: 0;
            z-index: 100;
        }}
        .header-left {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .futu-logo {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 15px;
            font-weight: 800;
            color: #ff6900;
            letter-spacing: 0.5px;
        }}
        .futu-logo span {{
            background: #ff6900;
            color: #fff;
            font-size: 11px;
            padding: 2px 6px;
            border-radius: 4px;
            font-weight: 700;
        }}
        .current-stock-badge {{
            display: flex;
            align-items: baseline;
            gap: 8px;
            padding-left: 12px;
            border-left: 1px solid rgba(255,255,255,0.1);
        }}
        .cur-sym {{
            font-size: 16px;
            font-weight: 800;
            color: #f3f4f6;
        }}
        .cur-name {{
            font-size: 13px;
            color: #9ca3af;
        }}
        .cur-price {{
            font-size: 16px;
            font-weight: 800;
            font-family: 'DIN Alternate', -apple-system, sans-serif;
        }}
        .cur-change {{
            font-size: 12px;
            font-weight: 600;
            padding: 1px 6px;
            border-radius: 3px;
        }}

        /* 核心：全周期控制栏 (分时、5日、日K、周K、月K、分钟下拉菜单) */
        .period-bar {{
            display: flex;
            align-items: center;
            gap: 2px;
            height: 100%;
        }}
        .period-tab {{
            padding: 0 13px;
            height: 48px;
            line-height: 48px;
            font-size: 13px;
            font-weight: 500;
            color: #9ca3af;
            cursor: pointer;
            transition: all 0.15s ease;
            position: relative;
            white-space: nowrap;
        }}
        .period-tab:hover {{
            color: #f3f4f6;
            background: rgba(255,255,255,0.03);
        }}
        .period-tab.active {{
            color: #ff6900;
            font-weight: 700;
        }}
        .period-tab.active::after {{
            content: '';
            position: absolute;
            bottom: 0;
            left: 10%;
            right: 10%;
            height: 2.5px;
            background: #ff6900;
            border-radius: 2px 2px 0 0;
        }}

        /* 分钟下拉菜单组件 (完全还原富途) */
        .dropdown-wrapper {{
            position: relative;
            height: 100%;
            display: flex;
            align-items: center;
        }}
        .period-dropdown-btn {{
            display: flex;
            align-items: center;
            gap: 4px;
            padding: 0 12px;
            height: 32px;
            border-radius: 4px;
            font-size: 13px;
            font-weight: 500;
            color: #9ca3af;
            cursor: pointer;
            transition: all 0.15s ease;
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
        }}
        .period-dropdown-btn:hover, .period-dropdown-btn.active {{
            color: #ff6900;
            border-color: rgba(255,105,0,0.4);
            background: rgba(255,105,0,0.08);
        }}
        .arrow-icon {{
            font-size: 10px;
            transition: transform 0.2s;
        }}
        .dropdown-menu {{
            display: none;
            position: absolute;
            top: 44px;
            left: 0;
            width: 140px;
            background: #1e2128;
            border: 1px solid #2f3440;
            border-radius: 6px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.6);
            padding: 6px 0;
            z-index: 999;
        }}
        .dropdown-menu.show {{
            display: block;
        }}
        .dropdown-item {{
            padding: 8px 16px;
            font-size: 13px;
            color: #cbd5e1;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: space-between;
            transition: all 0.15s;
        }}
        .dropdown-item:hover {{
            background: rgba(255,105,0,0.15);
            color: #ff6900;
        }}
        .dropdown-item.active {{
            color: #ff6900;
            font-weight: 700;
            background: rgba(255,105,0,0.1);
        }}
        .dropdown-divider {{
            height: 1px;
            background: rgba(255,255,255,0.08);
            margin: 4px 0;
        }}

        /* 顶部右侧辅助快捷按钮 */
        .header-right {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .tool-link {{
            color: #9ca3af;
            text-decoration: none;
            font-size: 12px;
            padding: 5px 10px;
            border-radius: 4px;
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 4px;
        }}
        .tool-link:hover {{
            color: #60a5fa;
            border-color: rgba(96,165,250,0.3);
            background: rgba(96,165,250,0.1);
        }}
        .live-tag {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(0, 230, 118, 0.15);
            border: 1px solid rgba(0, 230, 118, 0.4);
            color: #00e676;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        }}
        .live-dot {{
            width: 6px;
            height: 6px;
            background: #00e676;
            border-radius: 50%;
            box-shadow: 0 0 6px #00e676;
        }}

        /* 主体三栏工作台布局 */
        .futu-main {{
            display: flex;
            flex: 1;
            height: calc(100vh - 48px - 28px);
            overflow: hidden;
        }}

        /* 左栏：自选列表与标的池 (Futu Watchlist) */
        .sidebar-left {{
            width: 280px;
            background: #14161b;
            border-right: 1px solid #23272e;
            display: flex;
            flex-direction: column;
            flex-shrink: 0;
            transition: width 0.2s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease;
            position: relative;
        }}
        .sidebar-left.collapsed {{
            width: 0 !important;
            min-width: 0 !important;
            opacity: 0;
            overflow: hidden;
            border-right: none;
        }}
        .sidebar-toggle-btn {{
            position: absolute;
            top: 54px;
            left: 280px;
            z-index: 1000;
            width: 14px;
            height: 44px;
            background: #1e222a;
            border: 1px solid #374151;
            border-left: none;
            border-radius: 0 4px 4px 0;
            color: #9ca3af;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            transition: all 0.2s ease;
            box-shadow: 2px 0 6px rgba(0,0,0,0.4);
        }}
        .sidebar-toggle-btn.btn-is-collapsed {{
            left: 0 !important;
            border-left: 1px solid #374151;
        }}
        .sidebar-toggle-btn:hover {{
            background: #ff6900;
            color: #fff;
        }}
        .watchlist-categories {{
            display: flex;
            background: #181a20;
            border-bottom: 1px solid #23272e;
            overflow-x: auto;
            scrollbar-width: thin;
            scrollbar-color: rgba(255,255,255,0.2) transparent;
        }}
        .watchlist-categories::-webkit-scrollbar {{
            height: 3px;
        }}
        .watchlist-categories::-webkit-scrollbar-thumb {{
            background: rgba(255,255,255,0.2);
            border-radius: 3px;
        }}
        .w-tab {{
            padding: 9px 12px;
            font-size: 12px;
            color: #9ca3af;
            cursor: pointer;
            transition: all 0.15s;
            white-space: nowrap;
            flex-shrink: 0;
        }}
        .w-tab:hover {{ color: #f3f4f6; }}
        .w-tab.active {{
            color: #ff6900;
            font-weight: 700;
            border-bottom: 2px solid #ff6900;
            background: rgba(255,105,0,0.05);
        }}
        .watchlist-container {{
            flex: 1;
            overflow-y: auto;
        }}
        .stock-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 14px;
            border-bottom: 1px solid rgba(255,255,255,0.04);
            cursor: pointer;
            transition: background 0.15s;
        }}
        .stock-item:hover {{
            background: #1a1d24;
        }}
        .stock-item.active {{
            background: #20242d;
            border-left: 3px solid #ff6900;
        }}
        .s-info {{
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}
        .s-ticker {{
            font-size: 14px;
            font-weight: 700;
            color: #f3f4f6;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .s-name {{
            font-size: 11px;
            color: #9ca3af;
        }}
        .s-quotes {{
            text-align: right;
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}
        .s-price {{
            font-size: 14px;
            font-weight: 700;
            font-family: 'DIN Alternate', -apple-system, sans-serif;
            color: #f3f4f6;
        }}
        .s-pct {{
            font-size: 11px;
            font-weight: 600;
        }}

        /* 中栏：TradingView 专业交互式图表区 */
        .chart-center {{
            flex: 1;
            background: #111317;
            position: relative;
            display: flex;
            flex-direction: column;
        }}
        .tv-container {{
            width: 100%;
            height: 100%;
            position: relative;
        }}

        /* 右栏：富途风控与量化战术指挥面板 */
        .sidebar-right {{
            width: 340px;
            background: #14161b;
            border-left: 1px solid #23272e;
            display: flex;
            flex-direction: column;
            overflow-y: auto;
            flex-shrink: 0;
            padding: 10px 12px;
            gap: 8px;
        }}
        .panel-box {{
            background: #181b22;
            border: 1px solid #272c37;
            border-radius: 6px;
            padding: 10px 12px;
            transition: all 0.2s ease;
        }}
        .panel-box.card-hidden {{
            display: none !important;
        }}
        .panel-box.collapsed .panel-box-content {{
            display: none !important;
        }}
        .panel-filter-bar {{
            display: flex;
            gap: 4px;
            background: #181b22;
            padding: 3px;
            border-radius: 6px;
            border: 1px solid #272c37;
            margin-bottom: 2px;
        }}
        .p-filter-pill {{
            flex: 1;
            text-align: center;
            padding: 5px 0;
            font-size: 11px;
            font-weight: 700;
            color: #9ca3af;
            cursor: pointer;
            border-radius: 4px;
            transition: all 0.15s;
            user-select: none;
        }}
        .p-filter-pill:hover {{
            color: #f3f4f6;
            background: rgba(255,255,255,0.06);
        }}
        .p-filter-pill.active {{
            color: #fff;
            background: #ff6900;
        }}
        .card-toggle-btn {{
            font-size: 10px;
            color: #9ca3af;
            cursor: pointer;
            padding: 1px 6px;
            border-radius: 3px;
            background: rgba(255,255,255,0.06);
            user-select: none;
            transition: all 0.15s;
        }}
        .card-toggle-btn:hover {{
            background: rgba(255,255,255,0.18);
            color: #fff;
        }}
        .box-title {{
            font-size: 12px;
            font-weight: 700;
            color: #9ca3af;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        /* 报价小网格 */
        .metrics-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px 12px;
            font-size: 11px;
        }}
        .m-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .m-lbl {{ color: #9ca3af; }}
        .m-val {{ font-weight: 600; color: #f3f4f6; }}

        /* INTC 专属风控雷达卡 */
        .intc-card {{
            border-left: 4px solid #ff1744;
            background: linear-gradient(135deg, rgba(255, 23, 68, 0.08), #181b22);
        }}
        .intc-title {{
            font-size: 13px;
            font-weight: 800;
            color: #ff5252;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .intc-rule-item {{
            font-size: 12px;
            margin-bottom: 6px;
            display: flex;
            justify-content: space-between;
        }}

        /* 量化战术研判与深度决策卡片 */
        .tactical-decision-card {{
            border-left: 4px solid #00e676;
            background: linear-gradient(135deg, rgba(0, 230, 118, 0.06), #181b22);
            border-radius: 8px;
            padding: 12px;
            transition: all 0.25s ease;
        }}
        .fractional-badge {{
            font-size: 18px;
            font-weight: 800;
            color: #00e5ff;
        }}

        /* 宏观司令塔微型气象 */
        .weather-mini {{
            border: 1px solid rgba(255, 105, 0, 0.3);
            background: linear-gradient(135deg, rgba(255, 105, 0, 0.06), #181b22);
        }}

        /* 底部指标栏 (MA, BOLL, EMA, RSI, MACD...) */
        .futu-footer {{
            height: 28px;
            background: #14161b;
            border-top: 1px solid #23272e;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 16px;
            font-size: 11px;
            color: #9ca3af;
        }}
        .indicator-chips {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .chip {{
            cursor: pointer;
            padding: 2px 6px;
            border-radius: 3px;
            transition: all 0.15s;
        }}
        .chip:hover {{
            color: #ff6900;
            background: rgba(255,105,0,0.1);
        }}

        /* 持仓独立管理弹窗 */
        .modal-overlay {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(0, 0, 0, 0.78);
            backdrop-filter: blur(4px);
            z-index: 9999;
            display: none;
            align-items: center;
            justify-content: center;
        }}
        .modal-card {{
            background: #1a1d24;
            border: 1px solid #374151;
            border-radius: 10px;
            width: 92%;
            max-width: 860px;
            max-height: 85vh;
            display: flex;
            flex-direction: column;
            box-shadow: 0 25px 50px rgba(0,0,0,0.8);
            overflow: hidden;
            animation: modalFadeIn 0.2s ease-out;
        }}
        @keyframes modalFadeIn {{
            from {{ opacity: 0; transform: scale(0.96); }}
            to {{ opacity: 1; transform: scale(1); }}
        }}
        .modal-header {{
            padding: 14px 18px;
            background: #14161b;
            border-bottom: 1px solid #282c35;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .modal-title {{
            font-size: 15px;
            font-weight: 800;
            color: #ff6900;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .modal-close {{
            background: none;
            border: none;
            color: #9ca3af;
            font-size: 18px;
            cursor: pointer;
            padding: 4px;
        }}
        .modal-close:hover {{ color: #fff; }}
        .modal-body {{
            padding: 16px 18px;
            overflow-y: auto;
            flex: 1;
        }}
        .modal-footer {{
            padding: 12px 18px;
            background: #14161b;
            border-top: 1px solid #282c35;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .portfolio-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
        }}
        .portfolio-table th {{
            padding: 8px 10px;
            text-align: left;
            color: #9ca3af;
            border-bottom: 1px solid #2d333f;
            font-weight: 600;
        }}
        .portfolio-table td {{
            padding: 6px 8px;
            border-bottom: 1px solid rgba(255,255,255,0.04);
        }}
        .p-input {{
            background: #111317;
            border: 1px solid #374151;
            border-radius: 4px;
            padding: 5px 8px;
            color: #f3f4f6;
            font-size: 12px;
            width: 100%;
        }}
        .p-input:focus {{
            outline: none;
            border-color: #ff6900;
        }}
        .btn-del {{
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid rgba(239, 68, 68, 0.35);
            color: #ef4444;
            border-radius: 4px;
            padding: 3px 8px;
            cursor: pointer;
            font-size: 11px;
        }}
        .btn-del:hover {{
            background: #ef4444;
            color: #fff;
        }}
        .btn-primary {{
            background: #ff6900;
            color: #fff;
            font-weight: 700;
            border: none;
            border-radius: 4px;
            padding: 7px 18px;
            cursor: pointer;
            font-size: 12px;
            transition: all 0.15s;
        }}
        .btn-primary:hover {{
            background: #ff8533;
        }}
        .btn-secondary {{
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.15);
            color: #d1d5db;
            border-radius: 4px;
            padding: 6px 14px;
            cursor: pointer;
            font-size: 12px;
        }}
        .btn-secondary:hover {{
            background: rgba(255,255,255,0.12);
        }}
        .toast-box {{
            position: fixed;
            bottom: 40px;
            left: 50%;
            transform: translateX(-50%);
            background: #059669;
            color: #fff;
            padding: 10px 22px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 700;
            box-shadow: 0 10px 25px rgba(0,0,0,0.6);
            z-index: 10000;
            display: none;
        }}

        /* 🔔 预警通知中心抽屉 */
        .notif-drawer-overlay {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(0,0,0,0.55);
            backdrop-filter: blur(2px);
            z-index: 9998;
            display: none;
            justify-content: flex-end;
        }}
        .notif-drawer {{
            width: 460px;
            height: 100vh;
            background: #16181e;
            border-left: 1px solid #282c35;
            display: flex;
            flex-direction: column;
            box-shadow: -15px 0 35px rgba(0,0,0,0.7);
            animation: drawerSlideIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
        }}
        @keyframes drawerSlideIn {{
            from {{ transform: translateX(100%); }}
            to {{ transform: translateX(0); }}
        }}
        .notif-drawer-header {{
            padding: 12px 16px;
            background: #14161b;
            border-bottom: 1px solid #23272e;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .notif-category-bar {{
            display: flex;
            background: #1a1d24;
            border-bottom: 1px solid #23272e;
            padding: 6px 8px;
            gap: 4px;
            overflow-x: auto;
            scrollbar-width: none;
        }}
        .notif-category-bar::-webkit-scrollbar {{ display: none; }}
        .n-tab {{
            padding: 4px 8px;
            font-size: 11px;
            font-weight: 700;
            color: #9ca3af;
            background: transparent;
            border: 1px solid transparent;
            border-radius: 4px;
            cursor: pointer;
            white-space: nowrap;
            transition: all 0.15s;
        }}
        .n-tab:hover {{
            color: #f3f4f6;
            background: rgba(255,255,255,0.06);
        }}
        .n-tab.active {{
            color: #ff6900;
            background: rgba(255,105,0,0.12);
            border-color: rgba(255,105,0,0.35);
        }}
        .notif-drawer-body {{
            flex: 1;
            overflow-y: auto;
            padding: 12px;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }}
        .notif-card {{
            background: #1a1d24;
            border: 1px solid #282c37;
            border-radius: 6px;
            padding: 10px 12px;
            cursor: pointer;
            transition: all 0.15s ease;
            display: flex;
            flex-direction: column;
            gap: 5px;
        }}
        .notif-card:hover {{
            background: #20242e;
            border-color: #3b82f6;
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.4);
        }}
        .notif-card.level-success {{ border-left: 4px solid #00e676; }}
        .notif-card.level-danger {{ border-left: 4px solid #ff5252; }}
        .notif-card.level-warning {{ border-left: 4px solid #ffa726; }}
        .notif-card.level-purple {{ border-left: 4px solid #d946ef; }}
        .notif-card.level-info {{ border-left: 4px solid #38bdf8; }}
        .notif-drawer-footer {{
            padding: 10px 14px;
            background: #14161b;
            border-top: 1px solid #23272e;
            font-size: 11px;
            color: #9ca3af;
            text-align: center;
        }}
        .notif-btn-clear {{
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.12);
            color: #9ca3af;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            cursor: pointer;
        }}
        .notif-btn-clear:hover {{
            color: #fff;
            background: rgba(255,255,255,0.12);
        }}
        .notif-btn-close {{
            background: none;
            border: none;
            color: #9ca3af;
            font-size: 16px;
            cursor: pointer;
            padding: 2px 6px;
        }}
        .notif-btn-close:hover {{ color: #fff; }}
        .sound-test-chip {{
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.14);
            color: #cbd5e1;
            padding: 2px 5px;
            border-radius: 3px;
            font-size: 10px;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .sound-test-chip:hover {{
            background: #ff6900;
            color: #fff;
            border-color: #ff6900;
        }}

        /* 📱 手机端自适应 (<= 768px) 与 PWA 专属体验 */
        .mobile-bottom-nav {{
            display: none;
        }}
        .mobile-period-bar {{
            display: none;
        }}
        .mobile-only {{
            display: none;
        }}

        @media (max-width: 768px) {{
            body {{
                overflow: hidden;
            }}
            .futu-header {{
                height: 46px;
                padding: 0 8px;
                gap: 4px;
            }}
            .futu-logo span {{
                display: none !important;
            }}
            .current-stock-badge {{
                padding-left: 6px;
                gap: 5px;
            }}
            .cur-sym {{
                font-size: 14px;
            }}
            .cur-name {{
                display: none !important;
            }}
            .cur-price {{
                font-size: 14px;
            }}
            .cur-change {{
                font-size: 11px;
                padding: 1px 4px;
            }}
            #curExtBadge, #curEarningsBadge {{
                display: none !important;
            }}
            #starBtnText {{
                display: none !important;
            }}
            #starBtn {{
                padding: 2px 6px !important;
                margin-left: 2px !important;
            }}
            .period-bar {{
                display: none !important;
            }}
            .header-right .tool-link:not(#btnNotifCenter):not(#btnSoundToggle) {{
                display: none !important;
            }}
            .header-right .live-tag {{
                display: none !important;
            }}
            #btnNotifCenter {{
                padding: 3px 6px !important;
                font-size: 11px !important;
            }}
            #btnNotifCenter span:first-child {{
                display: none !important;
            }}
            #btnNotifCenter::before {{
                content: "🔔";
            }}

            /* 核心布局切换：单屏占据 */
            .futu-main {{
                flex-direction: column !important;
                height: calc(100vh - 46px - 54px) !important;
                height: calc(100dvh - 46px - 54px) !important;
            }}
            .sidebar-toggle-btn {{
                display: none !important;
            }}

            .sidebar-left, .chart-center, .sidebar-right {{
                width: 100% !important;
                max-width: 100% !important;
                min-width: 100% !important;
                height: 100% !important;
                border: none !important;
                position: absolute;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                display: none;
            }}

            .futu-main.m-view-watchlist .sidebar-left {{
                display: flex !important;
            }}
            .futu-main.m-view-chart .chart-center {{
                display: flex !important;
            }}
            .futu-main.m-view-tactics .sidebar-right,
            .futu-main.m-view-options .sidebar-right,
            .futu-main.m-view-portfolio .sidebar-right {{
                display: flex !important;
            }}

            /* 手机端 K 线周期切换条 */
            .mobile-period-bar {{
                display: flex !important;
                align-items: center;
                background: #14161b;
                border-bottom: 1px solid #23272e;
                overflow-x: auto;
                scrollbar-width: none;
                flex-shrink: 0;
                padding: 0 4px;
                height: 34px;
            }}
            .mobile-period-bar::-webkit-scrollbar {{ display: none; }}
            .m-period-tab {{
                padding: 4px 10px;
                font-size: 12px;
                color: #9ca3af;
                cursor: pointer;
                white-space: nowrap;
            }}
            .m-period-tab.active {{
                color: #ff6900;
                font-weight: 700;
                border-bottom: 2px solid #ff6900;
            }}

            /* 手机底部 5 栏导航 */
            .mobile-bottom-nav {{
                display: flex !important;
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                height: 54px;
                background: #14161b;
                border-top: 1px solid #23272e;
                z-index: 999;
                padding-bottom: env(safe-area-inset-bottom, 0px);
            }}
            .m-nav-tab {{
                flex: 1;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                gap: 2px;
                cursor: pointer;
                color: #9ca3af;
                transition: all 0.15s;
                user-select: none;
            }}
            .m-nav-tab.active {{
                color: #ff6900;
            }}
            .m-nav-icon {{
                font-size: 16px;
                line-height: 1;
            }}
            .m-nav-label {{
                font-size: 10px;
                font-weight: 600;
            }}

            .mobile-only {{
                display: block !important;
            }}

            /* 弹窗与抽屉 */
            .portfolio-modal {{
                width: 95vw !important;
                max-width: 95vw !important;
                max-height: 85vh !important;
                padding: 12px 10px !important;
            }}
            .notif-drawer {{
                width: 100vw !important;
                max-width: 100vw !important;
                border-radius: 0 !important;
            }}
            .p-input {{
                font-size: 13px !important;
                padding: 6px 4px !important;
                min-width: 60px !important;
            }}
            .stock-item {{
                padding: 12px 14px !important;
            }}
            .btn-primary, .btn-secondary {{
                min-height: 38px;
            }}
        }}
    </style>
</head>
<body>

    <!-- 顶部状态栏与全周期切换栏 -->
    <header class="futu-header">
        <div class="header-left">
            <div class="futu-logo">
                🐮 富途看盘 <span>PRO · {len(stocks_data) + len(macro_data)}标的</span>
            </div>
            <div class="current-stock-badge" id="headerStockBadge">
                <span class="cur-sym" id="curSym">INTC</span>
                <span class="cur-name" id="curName">Intel</span>
                <span class="cur-price" id="curPrice">$86.96</span>
                <span class="cur-change" id="curChange" style="color: #00e676; background: rgba(0,230,118,0.15);">+1.72%</span>
                <span id="curExtBadge" style="display: none; font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 4px; background: rgba(147, 197, 253, 0.12); color: #93c5fd; border: 1px solid rgba(147, 197, 253, 0.28); margin-left: 4px;">🌙 盘后 --</span>
                <span id="curEarningsBadge" style="display: none; font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 4px; background: rgba(255, 167, 38, 0.15); color: #ffa726; border: 1px solid rgba(255, 167, 38, 0.35); margin-left: 4px;">📅 财报 --</span>
                <button id="starBtn" onclick="toggleCurrentStar()" title="加入/取消日内建仓盯盘" style="background: rgba(255,215,0,0.15); border: 1px solid rgba(255,215,0,0.35); color: #ffd700; border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700; cursor: pointer; display: flex; align-items: center; gap: 4px; transition: all 0.2s; margin-left: 8px;">
                    <span id="starIcon">⭐</span> <span id="starBtnText">盯盘中</span>
                </button>
            </div>
        </div>

        <!-- 核心全周期导航条 (分时 / 5日 / 日K / 周K / 月K / 季K / 年K / 分钟下拉) -->
        <div class="period-bar" id="periodBar">
            <div class="period-tab" data-period="1" data-style="3" data-type="min">分时</div>
            <div class="period-tab" data-period="5" data-style="1" data-type="min">5日</div>
            <div class="period-tab active" data-period="D" data-style="1" data-type="day">日K</div>
            <div class="period-tab" data-period="W" data-style="1" data-type="day">周K</div>
            <div class="period-tab" data-period="M" data-style="1" data-type="day">月K</div>
            <div class="period-tab" data-period="3M" data-style="1" data-type="day">季K</div>
            <div class="period-tab" data-period="12M" data-style="1" data-type="day">年K</div>

            <!-- 分钟级别下拉菜单 (完全模拟富途牛牛原版) -->
            <div class="dropdown-wrapper">
                <div class="period-dropdown-btn" id="minDropdownBtn">
                    <span id="currentMinLabel">5分</span>
                    <span class="arrow-icon">▾</span>
                </div>
                <div class="dropdown-menu" id="minDropdownMenu">
                    <div class="dropdown-item" data-period="1" data-label="1分">1分钟</div>
                    <div class="dropdown-item" data-period="3" data-label="3分">3分钟</div>
                    <div class="dropdown-item active" data-period="5" data-label="5分">5分钟</div>
                    <div class="dropdown-item" data-period="10" data-label="10分">10分钟</div>
                    <div class="dropdown-item" data-period="15" data-label="15分">15分钟</div>
                    <div class="dropdown-item" data-period="30" data-label="30分">30分钟</div>
                    <div class="dropdown-divider"></div>
                    <div class="dropdown-item" data-period="60" data-label="1小时">1小时</div>
                    <div class="dropdown-item" data-period="120" data-label="2小时">2小时</div>
                    <div class="dropdown-item" data-period="240" data-label="4小时">4小时</div>
                </div>
            </div>


        </div>

        <div class="header-right">
            <!-- 预警中心 & 声音切换 -->
            <div style="display: flex; align-items: center; gap: 4px;">
                <button id="btnNotifCenter" onclick="toggleNotifDrawer()" class="tool-link" style="background: rgba(239, 68, 68, 0.16); border: 1px solid rgba(239, 68, 68, 0.45); color: #f87171; font-weight: 700; cursor: pointer; display: flex; align-items: center; gap: 5px; padding: 4px 8px;">
                    <span>🔔 预警中心</span>
                    <span id="notifBadge" style="background: #ef4444; color: #fff; font-size: 10px; padding: 1px 6px; border-radius: 10px; font-weight: 800;">0</span>
                </button>
                <button id="btnSoundToggle" onclick="toggleSoundAlerts()" class="tool-link" title="声音提示：已开启 (点击切换静音)" style="cursor: pointer; padding: 4px 7px; font-size: 13px; color: #4ade80;">
                    <span id="soundIcon">🔊</span>
                </button>
            </div>
            <!-- 持仓管理 -->
            <button onclick="openPortfolioModal()" class="tool-link" style="background: rgba(255,105,0,0.18); border: 1px solid rgba(255,105,0,0.45); color: #ff6900; font-weight: 700; cursor: pointer; padding: 4px 8px;">💼 实盘持仓</button>
            <!-- 市场时段状态标签 (动态四元划分) -->
            {session_badge_html}
            <!-- 外部看板精简按钮 -->
            <a href="stocks_radar.html" target="_blank" class="tool-link" style="padding: 4px 7px;">🎯 个股雷达</a>
            <a href="macro_radar.html" target="_blank" class="tool-link" style="padding: 4px 7px;">🏛️ 司令塔</a>
        </div>
    </header>

    <!-- 主体区域 -->
    <main class="futu-main m-view-watchlist" id="futuMain" style="position: relative;">
        <!-- 左侧折叠切换浮动按钮 -->
        <button id="btnToggleSidebar" onclick="toggleSidebar()" title="折叠/展开左侧标的列表" class="sidebar-toggle-btn">◀</button>

        <!-- 左侧：自选股列表 -->
        <aside class="sidebar-left" id="sidebarLeft">
            <div class="watchlist-categories">
                <div class="w-tab active" data-filter="all">全部 ({len(stocks_data) + len(macro_data)})</div>
                <div class="w-tab" data-filter="starred" style="color: #ffd700; font-weight: 700; background: rgba(255, 215, 0, 0.08);">⭐ 建仓盯盘 (<span id="starredCount">0</span>)</div>
                <div class="w-tab" data-filter="pit" style="color: #00e676; font-weight: 700; background: rgba(0, 230, 118, 0.08);">🟢 黄金坑</div>
                <div class="w-tab" data-filter="take_profit" style="color: #d946ef; font-weight: 700; background: rgba(217, 70, 239, 0.08);">🟣 冲顶减仓</div>
                <div class="w-tab" data-filter="stop" style="color: #ff5252; font-weight: 700; background: rgba(255, 82, 82, 0.08);">🔴 破位/回踩</div>
                <div class="w-tab" data-filter="stocks">个股 ({len(stocks_data)})</div>
                <div class="w-tab" data-filter="macro">ETF ({len(macro_data)})</div>
                <div class="w-tab" data-filter="存储闪存">存储与NAND</div>
                <div class="w-tab" data-filter="芯片与半导体">芯片半导体</div>
                <div class="w-tab" data-filter="液冷服务器">液冷与服务器</div>
                <div class="w-tab" data-filter="云与软件">云巨头AI</div>
                <div class="w-tab" data-filter="光通信">光通信</div>
                <div class="w-tab" data-filter="能源电力">能源与核电</div>
                <div class="w-tab" data-filter="太空商业">太空商业</div>
                <div class="w-tab" data-filter="物理与安全">物理AI与资产</div>
                <div class="w-tab" data-filter="生物医药">生物医药</div>
                <div class="w-tab" data-filter="价值消费">价值消费</div>
            </div>
            <div class="watchlist-container" id="watchlistContainer">
                <!-- 由 JavaScript 动态渲染自选股条目 -->
            </div>
        </aside>

        <!-- 中部：TradingView 图表视窗 -->
        <section class="chart-center" id="chartMiddle">
            <!-- 📱 手机端专属 K 线周期切换条 (<=768px 显示) -->
            <div class="mobile-period-bar" id="mobilePeriodBar">
                <div class="m-period-tab" data-period="1" data-style="3">分时</div>
                <div class="m-period-tab" data-period="5" data-style="1">5分</div>
                <div class="m-period-tab" data-period="15" data-style="1">15分</div>
                <div class="m-period-tab" data-period="60" data-style="1">1时</div>
                <div class="m-period-tab active" data-period="D" data-style="1">日K</div>
                <div class="m-period-tab" data-period="W" data-style="1">周K</div>
                <div class="m-period-tab" data-period="M" data-style="1">月K</div>
            </div>
            <div class="tv-container" id="tv_chart_container">
                <!-- TradingView 实例将在此挂载 -->
            </div>
        </section>

        <!-- 右侧：个股深度行情与量化风控指令室 -->
        <aside class="sidebar-right" id="sidebarRight">
            <!-- 📱 手机端专属快捷操作条 (编辑持仓 / 预警通知) -->
            <div class="mobile-only" style="margin-bottom: 6px;">
                <div style="display: flex; gap: 8px;">
                    <button onclick="openPortfolioModal()" class="btn-primary" style="flex: 1; padding: 7px; font-size: 12px; display: flex; align-items: center; justify-content: center; gap: 4px; border-radius: 4px;">
                        <span>⚙️ 编辑实盘持仓</span>
                    </button>
                    <button onclick="toggleNotifDrawer()" class="btn-secondary" style="flex: 1; padding: 7px; font-size: 12px; display: flex; align-items: center; justify-content: center; gap: 4px; border-radius: 4px;">
                        <span>🔔 预警中心</span>
                    </button>
                </div>
            </div>
            <!-- 视图过滤切换栏 (解决拥挤，支持一键筛选卡片) -->
            <div class="panel-filter-bar">
                <div class="p-filter-pill active" data-filter="all" onclick="filterRightCards('all')">全景</div>
                <div class="p-filter-pill" data-filter="options" onclick="filterRightCards('options')">⚡期权</div>
                <div class="p-filter-pill" data-filter="entry" onclick="filterRightCards('entry')">🎯挂单</div>
                <div class="p-filter-pill" data-filter="tactical" onclick="filterRightCards('tactical')">📊战术</div>
                <div class="p-filter-pill" data-filter="risk" onclick="filterRightCards('risk')">💼持仓</div>
            </div>

            <!-- 1. 当前标的基本面与分时水温 -->
            <div class="panel-box" id="boxQuotes" data-cat="quotes">
                <div class="box-title">
                    <span>行情即时盘面</span>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span id="pSector" style="color: #60a5fa; font-weight: normal;">专属风控</span>
                        <span class="card-toggle-btn" onclick="toggleCard(this)">▲</span>
                    </div>
                </div>
                <div class="panel-box-content">
                    <!-- 核心状态徽章大胶囊 -->
                    <div id="pSignalBanner" style="padding: 7px 10px; border-radius: 6px; font-weight: 700; font-size: 13px; margin-bottom: 8px; display: flex; align-items: center; justify-content: space-between; background: rgba(0,230,118,0.15); color: #00e676; border: 1px solid rgba(0,230,118,0.3); transition: all 0.25s;">
                        <span id="pSignalText">🟢 黄金坑就位</span>
                        <span id="pSignalTag" style="font-size: 11px; padding: 2px 7px; border-radius: 4px; background: rgba(255,255,255,0.12); font-weight: 600;">低吸建仓</span>
                    </div>
                    <!-- 六维量化矩阵 -->
                    <div class="metrics-grid">
                        <div class="m-item"><span class="m-lbl">50日均线</span><span class="m-val" id="pMa50">--</span></div>
                        <div class="m-item"><span class="m-lbl">MA50偏离</span><span class="m-val" id="pBias">--</span></div>
                        <div class="m-item"><span class="m-lbl">EMA20趋势</span><span class="m-val" id="pEma20" style="color: #93c5fd;">--</span></div>
                        <div class="m-item"><span class="m-lbl">成交量比</span><span class="m-val" id="pVolRatio">--</span></div>
                        <div class="m-item"><span class="m-lbl">14日 RSI</span><span class="m-val" id="pRsi">--</span></div>
                        <div class="m-item"><span class="m-lbl">14日 ATR</span><span class="m-val" id="pAtr">--</span></div>
                    </div>
                </div>
            </div>

            <!-- 2. 实盘持仓专属风控与解套跟踪哨 -->
            <div class="panel-box intc-card" id="intcTacticalBox" data-cat="risk">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <div class="intc-title" id="intcCardTitle" style="margin-bottom: 0;">🛡️ 实盘底仓风控与解套哨</div>
                    <span class="card-toggle-btn" onclick="toggleCard(this)">▲</span>
                </div>
                <div class="panel-box-content">
                    <div class="intc-rule-item">
                        <span style="color: #9ca3af;">持仓成本基线</span>
                        <span style="font-weight: 700; color: #f3f4f6;" id="intcCostBase">$95.53 (持仓 7 股)</span>
                    </div>
                    <div class="intc-rule-item">
                        <span style="color: #9ca3af;">持仓总成本</span>
                        <span style="font-weight: 700; color: #f3f4f6;" id="intcTotalCost">$668.74</span>
                    </div>
                    <div class="intc-rule-item">
                        <span style="color: #9ca3af;">当前持仓市值</span>
                        <span style="font-weight: 700; color: #f3f4f6;" id="intcCurrentVal">$630.14</span>
                    </div>
                    <div class="intc-rule-item">
                        <span style="color: #60a5fa;">未实现盈亏</span>
                        <span style="font-weight: 700; color: #ff5252;" id="intcPnlRatio">-5.77% (-$38.60)</span>
                    </div>
                    <div class="intc-rule-item">
                        <span style="color: #ffd700;">🎯 第一阶段目标/反抽减压位</span>
                        <span style="font-weight: 700; color: #ffd700;" id="intcTrim1Val">$91.80 (卖 5 股回笼 ~$459)</span>
                    </div>
                    <div class="intc-rule-item">
                        <span style="color: #00e676;">🎉 解套目标价位</span>
                        <span style="font-weight: 700; color: #00e676;" id="intcTargetVal">$95.53</span>
                    </div>
                    <div class="intc-rule-item">
                        <span style="color: #ff5252;">🚨 铁血硬止损线</span>
                        <span style="font-weight: 700; color: #ff5252;" id="intcStopVal">$88.00</span>
                    </div>
                    <div id="intcStatusDesc" style="font-size: 11px; margin-top: 8px; line-height: 1.45; border-top: 1px solid rgba(255,255,255,0.08); padding-top: 6px;">
                        🟡 紧密风控：现价处于成本与硬止损之间，紧密监测减仓线与防守线。
                    </div>
                </div>
            </div>

            <!-- 2.5 🎯 日内精准建仓挂单指南 (自选盯盘核心执行卡) -->
            <div class="panel-box" id="entryPlanBox" data-cat="entry" style="background: linear-gradient(135deg, rgba(255, 215, 0, 0.05), #181b22); border-left: 4px solid #ffd700; border-radius: 6px; padding: 10px 12px; margin-bottom: 8px;">
                <div class="box-title" style="margin-bottom: 8px;">
                    <span style="color: #ffd700; font-weight: 800; display: flex; align-items: center; gap: 6px;">
                        <span>🎯 日内精准建仓挂单指南</span>
                    </span>
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span id="pStarStatusBadge" style="font-size: 11px; padding: 2px 7px; border-radius: 4px; background: rgba(255,215,0,0.15); color: #ffd700; font-weight: 700;">⭐ 已加入盯盘</span>
                        <span class="card-toggle-btn" onclick="toggleCard(this)">▲</span>
                    </div>
                </div>
                <div class="panel-box-content">
                    <!-- 5分钟高频量化建仓实时监测状态 -->
                    <div id="pIntradayBanner" style="background: rgba(0, 230, 118, 0.12); border: 1px solid rgba(0, 230, 118, 0.35); border-radius: 5px; padding: 6px 9px; margin-bottom: 8px;">
                        <div id="pIntradayStatusText" style="font-size: 12px; font-weight: 800; color: #00e676; margin-bottom: 3px;">⏳ 蹲守挂单价</div>
                        <div id="pIntradayReason" style="font-size: 11px; color: #d1d5db; line-height: 1.35;">自动融合 5m VWAP 均价、5m RSI 超卖拐头与盘前盘后实时报价，满足共振立即触发微信提醒。</div>
                    </div>
                    <!-- 挂单参数矩阵 -->
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 6px;">
                        <div style="background: rgba(0,0,0,0.3); border-radius: 6px; padding: 6px 8px; border: 1px solid rgba(255,215,0,0.2);">
                            <div style="font-size: 11px; color: #9ca3af;">💡 建议挂单买入价</div>
                            <div id="pOptimalLimit" style="font-size: 16px; font-weight: 800; color: #ffd700; font-family: 'DIN Alternate', sans-serif;">$0.00</div>
                            <div style="font-size: 10px; color: #6b7280;" id="pOptimalNote">黄金坑中下轴支撑</div>
                        </div>
                        <div style="background: rgba(0,0,0,0.3); border-radius: 6px; padding: 6px 8px; border: 1px solid rgba(0,230,118,0.2);">
                            <div style="font-size: 11px; color: #9ca3af;">📦 首批30%股数 ($900限额)</div>
                            <div id="pBatch1Shares" style="font-size: 16px; font-weight: 800; color: #00e676; font-family: 'DIN Alternate', sans-serif;">0.00 股</div>
                            <div style="font-size: 10px; color: #6b7280;" id="pBatch1Val">约 $0.00 (3-3-4第一笔)</div>
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 11px; margin-bottom: 4px;">
                        <div style="background: rgba(0,0,0,0.25); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(255,255,255,0.05);">
                            <span style="color: #9ca3af;">日内 VWAP 均价：</span>
                            <b style="color: #38bdf8;" id="pVwap">--</b>
                        </div>
                        <div style="background: rgba(0,0,0,0.25); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(255,255,255,0.05);">
                            <span style="color: #9ca3af;">5m RSI / 量比：</span>
                            <b style="color: #f3f4f6;" id="pRsi5m">--</b>
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 11px; margin-bottom: 4px;">
                        <div style="background: rgba(0,0,0,0.25); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(255,255,255,0.05);">
                            <span style="color: #9ca3af;">20日超额RS：</span>
                            <b style="color: #00e676;" id="pRs20d">--</b>
                        </div>
                        <div style="background: rgba(0,0,0,0.25); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(255,255,255,0.05);">
                            <span style="color: #9ca3af;">财报避雷日历：</span>
                            <b style="color: #93c5fd;" id="pEarningsDate">--</b>
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 11px;">
                        <div style="background: rgba(0,0,0,0.25); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(255,255,255,0.05);">
                            <span style="color: #9ca3af;">防守止损线：</span>
                            <b style="color: #ff5252;" id="pEntryStopLoss">$0.00</b>
                        </div>
                        <div style="background: rgba(0,0,0,0.25); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(255,255,255,0.05);">
                            <span style="color: #9ca3af;">黄金坑支撑带：</span>
                            <b style="color: #93c5fd;" id="pEntryFibBand">$0 ~ $0</b>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 2.8 ⚡ 机构期权多空异动雷达 (PCR、主力权利金大单與 ATM 预期波动带) -->
            <div class="panel-box" id="optionsAnomalyBox" data-cat="options" style="background: linear-gradient(135deg, rgba(168, 85, 247, 0.06), #181b22); border-left: 4px solid #a855f7; border-radius: 6px; padding: 10px 12px; margin-bottom: 8px;">
                <div class="box-title" style="margin-bottom: 8px;">
                    <span style="color: #c084fc; font-weight: 800; display: flex; align-items: center; gap: 6px;">
                        <span>⚡ 机构期权多空异动雷达</span>
                    </span>
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span id="pPcrSentimentBadge" style="font-size: 11px; padding: 2px 7px; border-radius: 4px; background: rgba(168,85,247,0.18); color: #c084fc; font-weight: 700;">🟢 偏多看涨</span>
                        <span class="card-toggle-btn" onclick="toggleCard(this)">▲</span>
                    </div>
                </div>
                <div class="panel-box-content">
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 11px; margin-bottom: 6px;">
                        <div style="background: rgba(0,0,0,0.25); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(255,255,255,0.05);">
                            <span style="color: #9ca3af;">沽购比(张数/资金)：</span>
                            <b style="color: #f3f4f6;" id="pOptionsPcr">--</b>
                        </div>
                        <div style="background: rgba(0,0,0,0.25); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(255,255,255,0.05);">
                            <span style="color: #9ca3af;">期权多空张数：</span>
                            <b style="color: #93c5fd;" id="pOptionsVolRatio">--</b>
                        </div>
                        <div style="background: rgba(0,0,0,0.25); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(255,255,255,0.05);">
                            <span style="color: #9ca3af;">平值隐含波动(IV)：</span>
                            <b style="color: #fbbf24;" id="pOptionsIv">--</b>
                        </div>
                        <div style="background: rgba(0,0,0,0.25); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(255,255,255,0.05);">
                            <span style="color: #9ca3af;">期权定价波动带：</span>
                            <b style="color: #38bdf8;" id="pOptionsExpMove">--</b>
                        </div>
                    </div>
                    <div style="display: flex; flex-direction: column; gap: 4px; font-size: 11px;">
                        <div style="background: rgba(0,0,0,0.25); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(255,255,255,0.05); display: flex; justify-content: space-between;">
                            <span style="color: #00e676;">🔥 主力 Call 大单(阻力墙)：</span>
                            <b style="color: #f3f4f6;" id="pTopCallStrike">--</b>
                        </div>
                        <div style="background: rgba(0,0,0,0.25); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(255,255,255,0.05); display: flex; justify-content: space-between;">
                            <span style="color: #ff5252;">🛡️ 主力 Put 大单(支撑墙)：</span>
                            <b style="color: #f3f4f6;" id="pTopPutStrike">--</b>
                        </div>
                    </div>
                    <div id="pOptionsExpTip" style="font-size: 10px; color: #9ca3af; margin-top: 5px; text-align: right;">
                        *基于主力周期权利金大单与平值跨式(ATM Straddle)隐含波动测算 (30m本地缓存·0额度)
                    </div>
                </div>
            </div>

            <!-- 3. 量化战术研判与深度决策卡片 (多维成因诊断 + 操盘军规) -->
            <div class="panel-box tactical-decision-card" id="tacticalDecisionBox" data-cat="tactical" style="padding: 10px 12px; margin-bottom: 8px;">
                <div class="box-title" style="margin-bottom: 8px;">
                    <span style="color: #00e5ff;">🎯 量化战术研判与深度成因</span>
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span id="pActionTypeBadge" style="font-size: 11px; padding: 2px 7px; border-radius: 4px; background: rgba(0,229,255,0.15); color: #00e5ff; font-weight: 700;">分批建仓</span>
                        <span class="card-toggle-btn" onclick="toggleCard(this)">▲</span>
                    </div>
                </div>
                <div class="panel-box-content">
                    <div id="pDecisionTitle" style="font-size: 13px; font-weight: 800; color: #00e676; margin-bottom: 8px;">
                        🟢 黄金坑就位 · 绝佳低吸建仓窗口
                    </div>

                    <!-- 核心成因诊断 (四维全景透视：均线、量价、支撑、板块) -->
                    <div class="decision-reason-box" style="background: rgba(0,0,0,0.3); border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; font-size: 11px; line-height: 1.5; border: 1px solid rgba(255,255,255,0.05);">
                        <div style="font-weight: 700; color: #f3f4f6; margin-bottom: 4px;">
                            <span>🔍 核心成因与技术面诊断：</span>
                        </div>
                        <div style="display: flex; flex-direction: column; gap: 4px; color: #d1d5db;">
                            <div>• <b style="color:#9ca3af;">均线形态：</b><span id="pMaReason">--</span></div>
                            <div>• <b style="color:#9ca3af;">量价动能：</b><span id="pVolReason">--</span></div>
                            <div>• <b style="color:#9ca3af;">波段支撑：</b><span id="pFibReason">--</span></div>
                            <div>• <b style="color:#9ca3af;">板块共振：</b><span id="pMacroReason">--</span></div>
                        </div>
                    </div>

                    <!-- 操盘指引与配置建议 -->
                    <div class="action-directive-box" style="border-top: 1px solid rgba(255,255,255,0.08); padding-top: 8px;">
                        <div style="font-size: 11px; color: #9ca3af; margin-bottom: 4px;">💡 <b>实战操盘指引与行动指令：</b></div>
                        <div id="pActionDirective" style="font-size: 11px; line-height: 1.5; color: #f3f4f6; font-weight: 500; margin-bottom: 8px;">
                            --
                        </div>
                        <!-- 碎股资金建议区域 (根据状态智能变色提示) -->
                        <div id="pAllocationSection" style="background: rgba(255,255,255,0.04); border-radius: 6px; padding: 6px 10px; display: flex; align-items: center; justify-content: space-between; border: 1px solid rgba(255,255,255,0.05);">
                            <div>
                                <div style="font-size: 11px; color: #9ca3af;" id="pAllocLabel">单股配置参考 ($900上限)：</div>
                                <div style="font-size: 10px; color: #6b7280;" id="pAllocSub">金字塔分批吸筹标准</div>
                            </div>
                            <div class="fractional-badge" id="pFractional" style="margin: 0; font-size: 16px; color: #00e5ff;">-- 股</div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 4. 宏观气象迷你卡 -->
            <div class="panel-box weather-mini" data-cat="macro" style="padding: 10px 12px;">
                <div class="box-title" style="margin-bottom: 6px;">
                    <span style="color: #ffa726;">🏛️ 宏观司令塔气象</span>
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span id="pWeatherBadge" style="color: #29b6f6; font-weight: 800;">{weather_info.get('weather', '阴天防守')}</span>
                        <span class="card-toggle-btn" onclick="toggleCard(this)">▲</span>
                    </div>
                </div>
                <div class="panel-box-content">
                    <div style="font-size: 11px; color: #aaa; line-height: 1.4;" id="pWeatherDesc">
                        {weather_info.get('tactics', '严格控制总仓位，分批防守。')}
                    </div>
                </div>
            </div>
        </aside>
    </main>

    <!-- 📱 手机专属底部 5 栏导航栏 (<=768px 自动激活，富途牛牛 App 经典交互) -->
    <nav class="mobile-bottom-nav" id="mobileBottomNav">
        <div class="m-nav-tab active" data-tab="watchlist" onclick="switchMobileTab('watchlist')">
            <span class="m-nav-icon">📊</span>
            <span class="m-nav-label">行情</span>
        </div>
        <div class="m-nav-tab" data-tab="chart" onclick="switchMobileTab('chart')">
            <span class="m-nav-icon">📈</span>
            <span class="m-nav-label">K线</span>
        </div>
        <div class="m-nav-tab" data-tab="tactics" onclick="switchMobileTab('tactics')">
            <span class="m-nav-icon">🎯</span>
            <span class="m-nav-label">决策</span>
        </div>
        <div class="m-nav-tab" data-tab="options" onclick="switchMobileTab('options')">
            <span class="m-nav-icon">⚡</span>
            <span class="m-nav-label">期权</span>
        </div>
        <div class="m-nav-tab" data-tab="portfolio" onclick="switchMobileTab('portfolio')">
            <span class="m-nav-icon">💼</span>
            <span class="m-nav-label">持仓</span>
        </div>
    </nav>

    <!-- 底部状态与指标栏 -->
    <footer class="futu-footer">
        <div class="indicator-chips">
            <span>技术指标:</span>
            <span class="chip">MA</span>
            <span class="chip">EMA</span>
            <span class="chip">VWAP</span>
            <span class="chip">5m RSI</span>
            <span class="chip">5m RVOL</span>
            <span class="chip">RS相对强弱</span>
            <span class="chip">财报日历</span>
        </div>
        <div style="display: flex; align-items: center; gap: 14px;">
            <span style="color: #60a5fa; cursor: pointer; text-decoration: underline;" onclick="toggleFullscreen()" title="点击全屏或按F11">🖥️ 全屏模式(F11)</span>
            <span style="color: #9ca3af;">💡 按 <b>Ctrl + -</b> 可调小比例更清爽</span>
            <span>⏱️ 1分钟极速自动轮询 | 标的池: {len(stocks_data) + len(macro_data)}只 | 数据更新于: {update_time_str}</span>
        </div>
    </footer>

    <!-- 💼 实盘持仓独立管理弹窗 Modal -->
    <div class="modal-overlay" id="portfolioModalOverlay" onclick="if(event.target===this)closePortfolioModal()">
        <div class="modal-card">
            <div class="modal-header">
                <div class="modal-title">
                    <span>💼 实盘持仓管理 (Firstrade 联动)</span>
                </div>
                <button class="modal-close" onclick="closePortfolioModal()">✖</button>
            </div>
            <div class="modal-body">
                <div style="background: rgba(255,105,0,0.08); border: 1px solid rgba(255,105,0,0.25); border-radius: 6px; padding: 8px 12px; margin-bottom: 14px; font-size: 12px; color: #cbd5e1; line-height: 1.5;">
                    💡 <b>自主管理说明</b>：在此添加或修改持仓股数与成本，系统将保存至本地 <code style="color:#ff6900;">user_portfolio.json</code>。雷达与风控系统会自动读取并更新微信风控与看盘哨兵，<b>以后每次买卖增减仓都不用再告诉我了</b>！你也可以直接在 Windows 记事本中打开该文件编辑。
                </div>
                <div style="overflow-x: auto;">
                    <table class="portfolio-table" id="portfolioTable">
                        <thead>
                            <tr>
                                <th style="width: 85px;">代码</th>
                                <th style="width: 100px;">名称</th>
                                <th style="width: 80px;">持仓股数</th>
                                <th style="width: 85px;">成本单价($)</th>
                                <th style="width: 85px;">一减位($)</th>
                                <th style="width: 75px;">一减股数</th>
                                <th style="width: 85px;">止损价($)</th>
                                <th style="width: 85px;">解套目标($)</th>
                                <th style="width: 50px;">操作</th>
                            </tr>
                        </thead>
                        <tbody id="portfolioTableBody">
                            <!-- 由 JS 动态渲染持仓列表 -->
                        </tbody>
                    </table>
                </div>
                <div style="margin-top: 12px;">
                    <button class="btn-secondary" onclick="addPortfolioRow()" style="display: inline-flex; align-items: center; gap: 4px;">
                        <span>➕ 添加新持仓标的</span>
                    </button>
                </div>
            </div>
            <div class="modal-footer">
                <span style="font-size: 11px; color: #9ca3af;">*修改后点击保存立即写入本地 user_portfolio.json 生效</span>
                <div style="display: flex; gap: 8px;">
                    <button class="btn-secondary" onclick="closePortfolioModal()">取消</button>
                    <button class="btn-primary" onclick="savePortfolioModal()">💾 保存修改并生效</button>
                </div>
            </div>
        </div>
    </div>

    <!-- 提示框 Toast -->
    <div class="toast-box" id="toastBox">✅ 持仓配置已成功保存！</div>

    <!-- 🔔 预警通知中心侧边抽屉 -->
    <div class="notif-drawer-overlay" id="notifDrawerOverlay" onclick="if(event.target===this) toggleNotifDrawer()">
        <div class="notif-drawer">
            <div class="notif-drawer-header">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-size: 16px;">🔔</span>
                    <span style="font-size: 14px; font-weight: 800; color: #f3f4f6;">实盘预警通知中心</span>
                    <span id="notifTotalCount" style="font-size: 11px; background: rgba(255,105,0,0.18); color: #ff6900; padding: 1px 6px; border-radius: 10px; font-weight: 700;">0 条</span>
                </div>
                <div style="display: flex; align-items: center; gap: 8px;">
                    <button class="notif-btn-clear" onclick="clearAllNotifs()">清空通知</button>
                    <button class="notif-btn-close" onclick="toggleNotifDrawer()">✕</button>
                </div>
            </div>
            <!-- 预警声音控制与试听条 -->
            <div class="notif-sound-bar" style="background: #14171d; border-bottom: 1px solid #23272e; padding: 7px 12px; display: flex; align-items: center; justify-content: space-between; font-size: 11px;">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span id="drawerSoundLabel" style="font-weight: 700; color: #4ade80;">🔊 声音预警已开启</span>
                    <button onclick="toggleSoundAlerts()" style="background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.15); color: #d1d5db; padding: 2px 7px; border-radius: 4px; cursor: pointer; font-size: 11px;">切换静音</button>
                </div>
                <div style="display: flex; align-items: center; gap: 3px;">
                    <span style="color: #9ca3af; font-size: 10px;">试听:</span>
                    <button onclick="playAlertSound('5m_entry', true)" class="sound-test-chip" title="试听 5m买点音效 (清脆双上扬)">🎯5m</button>
                    <button onclick="playAlertSound('portfolio', true)" class="sound-test-chip" title="试听 持仓风控音效 (紧急双频警报)">💼风控</button>
                    <button onclick="playAlertSound('golden_pit', true)" class="sound-test-chip" title="试听 黄金坑音效 (舒缓和弦)">🟢黄金坑</button>
                    <button onclick="playAlertSound('stop_loss', true)" class="sound-test-chip" title="试听 破位音效 (低沉下坠)">🔴破位</button>
                    <button onclick="playAlertSound('take_profit', true)" class="sound-test-chip" title="试听 止盈音效 (金币连音)">🟣止盈</button>
                    <button onclick="playAlertSound('macro_weather', true)" class="sound-test-chip" title="试听 气象音效 (柔和马林巴)">⛅气象</button>
                </div>
            </div>
            <!-- 通知分类导航条 -->
            <div class="notif-category-bar">
                <button class="n-tab active" data-cat="all" onclick="filterNotifCategory('all')">全部 (<span id="catCount_all">0</span>)</button>
                <button class="n-tab" data-cat="5m_entry" onclick="filterNotifCategory('5m_entry')">🎯 5m买点 (<span id="catCount_5m_entry">0</span>)</button>
                <button class="n-tab" data-cat="portfolio" onclick="filterNotifCategory('portfolio')">💼 持仓风控 (<span id="catCount_portfolio">0</span>)</button>
                <button class="n-tab" data-cat="golden_pit" onclick="filterNotifCategory('golden_pit')">🟢 黄金坑 (<span id="catCount_golden_pit">0</span>)</button>
                <button class="n-tab" data-cat="stop_loss" onclick="filterNotifCategory('stop_loss')">🔴 破位预警 (<span id="catCount_stop_loss">0</span>)</button>
                <button class="n-tab" data-cat="take_profit" onclick="filterNotifCategory('take_profit')">🟣 止盈冲高 (<span id="catCount_take_profit">0</span>)</button>
                <button class="n-tab" data-cat="macro_weather" onclick="filterNotifCategory('macro_weather')">⛅ 市场气象 (<span id="catCount_macro_weather">0</span>)</button>
            </div>
            <!-- 通知列表容器 -->
            <div class="notif-drawer-body" id="notifListContainer">
                <!-- 动态由 JavaScript 渲染 -->
            </div>
            <div class="notif-drawer-footer">
                <span>💡 点击任意预警卡片，主看盘视窗与期权雷达将即刻自动联动跳转至该标的</span>
            </div>
        </div>
    </div>

    <!-- TradingView 官方嵌入脚本与前端交互引擎 -->
    <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
    <script>
        // 标的池数据注入
        const STOCKS_DATA = {stocks_json};
        const MACRO_DATA = {macro_json};
        const WEATHER_INFO = {weather_json};
        const INITIAL_WATCHLIST = {watchlist_json};
        const INITIAL_PORTFOLIO = {portfolio_json};
        const INITIAL_NOTIFICATIONS = {notifications_json};

        let currentFilter = "all";
        let targetWatchlist = Array.isArray(INITIAL_WATCHLIST) ? [...INITIAL_WATCHLIST] : [];
        let userPortfolio = (typeof INITIAL_PORTFOLIO === 'object' && INITIAL_PORTFOLIO !== null) ? {{...INITIAL_PORTFOLIO}} : {{}};
        let terminalNotifications = Array.isArray(INITIAL_NOTIFICATIONS) ? [...INITIAL_NOTIFICATIONS] : [];
        let currentNotifCat = "all";
        try {{
            localStorage.setItem("targetWatchlist", JSON.stringify(targetWatchlist));
        }} catch(e) {{}}

        // 动态计算本地/局域网 API 服务地址，支持手机端 Wi-Fi 直连与电脑端 file:// 无缝自适应
        const API_BASE = window.location.protocol.startsWith("http") ? window.location.origin : "http://127.0.0.1:8765";

        // 启动时主动向 8765 服务同步最新自选盯盘列表、用户持仓与预警通知
        fetch(`${{API_BASE}}/api/watchlist`)
            .then(r => r.json())
            .then(res => {{
                if (res && Array.isArray(res.watchlist)) {{
                    targetWatchlist = res.watchlist;
                    try {{
                        localStorage.setItem("targetWatchlist", JSON.stringify(targetWatchlist));
                    }} catch(e) {{}}
                    updateStarUI();
                    renderWatchlist(currentFilter);
                }}
            }})
            .catch(e => {{}});

        function syncPortfolioToRuntimeData(port) {{
            if (!port || typeof port !== "object") return;
            // 1. 先清零所有标的的旧持仓股数，确保删除的持仓彻底移除
            Object.keys(MACRO_DATA).forEach(k => {{
                MACRO_DATA[k].shares = 0;
                MACRO_DATA[k].trim1 = 0;
                MACRO_DATA[k].trim1_shares = 0;
                MACRO_DATA[k].trim2 = 0;
            }});
            STOCKS_DATA.forEach(st => {{
                st.shares = 0;
                st.trim1 = 0;
                st.trim1_shares = 0;
                st.trim2 = 0;
            }});
            // 2. 注入最新持仓
            Object.keys(port).forEach(t => {{
                const p = port[t];
                if (MACRO_DATA[t]) Object.assign(MACRO_DATA[t], p);
                const st = STOCKS_DATA.find(x => x.ticker === t);
                if (st) Object.assign(st, p);
            }});
        }}

        fetch(`${{API_BASE}}/api/portfolio`)
            .then(r => r.json())
            .then(res => {{
                if (res && res.portfolio) {{
                    userPortfolio = res.portfolio;
                    syncPortfolioToRuntimeData(userPortfolio);
                    renderWatchlist(currentFilter);
                    selectTicker(currentTicker);
                }}
            }})
            .catch(e => {{}});

        fetch(`${{API_BASE}}/api/notifications`)
            .then(r => r.json())
            .then(res => {{
                if (res && Array.isArray(res.notifications)) {{
                    handleIncomingNotifications(res.notifications);
                }}
            }})
            .catch(e => {{}});

        // TradingView 交易所标的代码映射 (全量 65 只)
        const SYMBOL_MAP = {{
            "QQQM": "NASDAQ:QQQM",
            "SPY": "AMEX:SPY",
            "DIA": "AMEX:DIA",
            "IWM": "AMEX:IWM",
            "SMH": "NASDAQ:SMH",
            "SOXX": "NASDAQ:SOXX",
            "SOXL": "AMEX:SOXL",
            "KORU": "AMEX:KORU",
            "IGV": "AMEX:IGV",
            "TLT": "NASDAQ:TLT",
            "GLD": "AMEX:GLD",
            "QQQI": "NASDAQ:QQQI",
            "VIXY": "AMEX:VIXY",
            "SQQQ": "NASDAQ:SQQQ",
            "SOXS": "SOXS",
            "NVDA": "NASDAQ:NVDA",
            "TSM": "NYSE:TSM",
            "AMD": "NASDAQ:AMD",
            "AVGO": "NASDAQ:AVGO",
            "ASML": "NASDAQ:ASML",
            "AMAT": "NASDAQ:AMAT",
            "MSFT": "NASDAQ:MSFT",
            "GOOGL": "NASDAQ:GOOGL",
            "META": "NASDAQ:META",
            "AAPL": "NASDAQ:AAPL",
            "AMZN": "NASDAQ:AMZN",
            "PLTR": "NYSE:PLTR",
            "CRWV": "NASDAQ:CRWV",
            "NBIS": "NASDAQ:NBIS",
            "VST": "NYSE:VST",
            "BE": "NYSE:BE",
            "CRDO": "NASDAQ:CRDO",
            "COHR": "NYSE:COHR",
            "MRVL": "NASDAQ:MRVL",
            "AAOI": "NASDAQ:AAOI",
            "LITE": "NASDAQ:LITE",
            "SPCX": "AMEX:SPCX",
            "ASTS": "NASDAQ:ASTS",
            "RKLB": "NASDAQ:RKLB",
            "KO": "NYSE:KO",
            "AXP": "NYSE:AXP",
            "INTC": "NASDAQ:INTC",
            "TSLA": "NASDAQ:TSLA",
            "VRT": "NYSE:VRT",
            "DELL": "NYSE:DELL",
            "HPE": "NYSE:HPE",
            "MU": "NASDAQ:MU",
            "CEG": "NASDAQ:CEG",
            "OKLO": "NYSE:OKLO",
            "FPS": "NYSE:FPS",
            "CRWD": "NASDAQ:CRWD",
            "MSTR": "NASDAQ:MSTR",
            "WDC": "NASDAQ:WDC",
            "SNDK": "NASDAQ:SNDK",
            "SKHY": "NASDAQ:SKHY",
            "STX": "NASDAQ:STX",
            "ORCL": "NYSE:ORCL",
            "CCXI": "NASDAQ:CCXI",
            "LLY": "NYSE:LLY",
            "MRNA": "NASDAQ:MRNA",
            "MCD": "NYSE:MCD",
            "NKE": "NYSE:NKE",
            "V": "NYSE:V",
            "PEP": "NASDAQ:PEP",
            "IBIT": "NASDAQ:IBIT"
        }};

        let currentTicker = "INTC";
        let currentInterval = "D";
        let currentStyle = "1"; // 1=K线, 3=分时面积图

        // 初始化 TradingView 图表挂载 (默认开启 Pre/Post 盘前盘后延展时段)
        function loadTvWidget(ticker, interval, style) {{
            const container = document.getElementById("tv_chart_container");
            container.innerHTML = "";
            const tvSymbol = SYMBOL_MAP[ticker] || ("NASDAQ:" + ticker);
            const targetSession = "extended";

            try {{
                new TradingView.widget({{
                    "autosize": true,
                    "symbol": tvSymbol,
                    "interval": interval,
                    "timezone": "America/New_York",
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
                    "overrides": {{
                        "mainSeriesProperties.sessionId": targetSession
                    }},
                    "studies": [
                        "MASimple@tv-basicstudies",
                        "MACD@tv-basicstudies",
                        "RSI@tv-basicstudies"
                    ]
                }});
            }} catch(e) {{
                const iframeUrl = `https://s.tradingview.com/widgetembed/?frameElementId=tv_widget&symbol=${{encodeURIComponent(tvSymbol)}}&interval=${{interval}}&hidesidetoolbar=0&symboledit=1&saveimage=0&toolbarbg=14161b&theme=dark&style=${{style||'1'}}&timezone=America%2FNew_York&locale=zh_CN&extended_hours=1&sessionId=${{targetSession}}`;
                container.innerHTML = `<iframe src="${{iframeUrl}}" style="width:100%; height:100%; border:none;"></iframe>`;
            }}
        }}

        // 盯盘状态 UI 刷新函数
        function updateStarUI() {{
            const isStarred = targetWatchlist.includes(currentTicker);
            const starBtn = document.getElementById("starBtn");
            const starIcon = document.getElementById("starIcon");
            const starText = document.getElementById("starBtnText");
            const pStarBadge = document.getElementById("pStarStatusBadge");
            const countEl = document.getElementById("starredCount");
            if (countEl) countEl.innerText = targetWatchlist.length;
            
            if (starBtn) {{
                if (isStarred) {{
                    starBtn.style.background = "rgba(255,215,0,0.22)";
                    starBtn.style.borderColor = "#ffd700";
                    starBtn.style.color = "#ffd700";
                    if (starIcon) starIcon.innerText = "⭐";
                    if (starText) starText.innerText = "已设盯盘";
                }} else {{
                    starBtn.style.background = "rgba(255,255,255,0.06)";
                    starBtn.style.borderColor = "rgba(255,255,255,0.18)";
                    starBtn.style.color = "#9ca3af";
                    if (starIcon) starIcon.innerText = "☆";
                    if (starText) starText.innerText = "设为盯盘";
                }}
            }}

            if (pStarBadge) {{
                if (isStarred) {{
                    pStarBadge.innerText = "⭐ 已加入日内盯盘";
                    pStarBadge.style.background = "rgba(255,215,0,0.18)";
                    pStarBadge.style.color = "#ffd700";
                    pStarBadge.style.border = "1px solid rgba(255,215,0,0.3)";
                }} else {{
                    pStarBadge.innerText = "☆ 未加入盯盘 (点击上方⭐添加)";
                    pStarBadge.style.background = "rgba(255,255,255,0.06)";
                    pStarBadge.style.color = "#9ca3af";
                    pStarBadge.style.border = "1px solid rgba(255,255,255,0.1)";
                }}
            }}
        }}

        // 切换当前选中股票的盯盘标记
        function toggleCurrentStar() {{
            const idx = targetWatchlist.indexOf(currentTicker);
            if (idx >= 0) {{
                targetWatchlist.splice(idx, 1);
            }} else {{
                targetWatchlist.push(currentTicker);
            }}
            try {{
                localStorage.setItem("targetWatchlist", JSON.stringify(targetWatchlist));
            }} catch(e) {{}}

            // 异步无阻塞同步到本地后台守护 API (端口 8765)
            fetch(`${{API_BASE}}/api/toggle_star?ticker=${{currentTicker}}`)
                .then(r => r.json())
                .then(res => {{
                    if (res && Array.isArray(res.watchlist)) {{
                        targetWatchlist = res.watchlist;
                        try {{
                            localStorage.setItem("targetWatchlist", JSON.stringify(targetWatchlist));
                        }} catch(e) {{}}
                        updateStarUI();
                        renderWatchlist(currentFilter);
                    }}
                }})
                .catch(e => {{}});

            updateStarUI();
            renderWatchlist(currentFilter);
        }}

        // 渲染左侧自选股列表
        function renderWatchlist(filter = currentFilter) {{
            currentFilter = filter;
            const container = document.getElementById("watchlistContainer");
            container.innerHTML = "";

            const countEl = document.getElementById("starredCount");
            if (countEl) countEl.innerText = targetWatchlist.length;

            let items = [];
            // 个股注入与行业标签匹配
            STOCKS_DATA.forEach(s => {{
                let match = false;
                if (filter === "all" || filter === "stocks") match = true;
                else if (filter === "starred" && targetWatchlist.includes(s.ticker)) match = true;
                else if (filter === "pit" && s.is_pit) match = true;
                else if (filter === "take_profit" && (s.is_take_profit || (s.signal && (s.signal.includes("止盈") || s.signal.includes("冲顶"))))) match = true;
                else if (filter === "stop" && (s.is_stop || s.is_deep_pullback || (s.signal && (s.signal.includes("破位") || s.signal.includes("跌破") || s.signal.includes("回踩"))))) match = true;
                else if (filter === "芯片与半导体" && (s.cat === "芯片算力" || s.cat === "晶圆代工" || s.cat === "制造设备" || s.cat === "通信芯片" || s.cat === "芯片半导体")) match = true;
                else if (filter === "存储闪存" && (s.cat === "存储芯片" || s.cat === "闪存存储" || s.cat === "数据存储")) match = true;
                else if (filter === "云与软件" && (s.cat === "云与软件" || s.cat === "生态平台" || s.cat === "社交AI" || s.cat === "终端硬件" || s.cat === "云与电商" || s.cat === "企业AI" || s.cat === "算力租赁")) match = true;
                else if (filter === "液冷服务器" && (s.cat === "液冷散热" || s.cat === "服务器ODM")) match = true;
                else if (filter === "光通信" && (s.cat === "光通信" || s.cat === "高速互联")) match = true;
                else if (filter === "能源电力" && (s.cat === "能源电力" || s.cat === "核能电力")) match = true;
                else if (filter === "太空商业" && s.cat === "太空商业") match = true;
                else if (filter === "物理与安全" && (s.cat === "物理AI" || s.cat === "AI安全" || s.cat === "数字资产" || s.cat === "价值蓝筹" || s.cat === "金融蓝筹" || s.cat === "专属风控")) match = true;
                else if (filter === "生物医药" && (s.cat === "生物医药" || s.cat === "医药健康")) match = true;
                else if (filter === "价值消费" && (s.cat === "价值蓝筹" || s.cat === "可选消费" || s.cat === "金融科技" || s.cat === "金融蓝筹")) match = true;
                else if (s.cat === filter) match = true;

                if (match) {{
                    items.push({{ ...s, is_stock: true }});
                }}
            }});
            // ETF 注入与全量信号联动
            Object.keys(MACRO_DATA).forEach(t => {{
                const m = MACRO_DATA[t];
                let match = false;
                if (filter === "all" || filter === "macro") match = true;
                else if (filter === "starred" && targetWatchlist.includes(t)) match = true;
                else if (filter === "pit" && (m.is_pit || (m.signal && m.signal.includes("黄金坑")))) match = true;
                else if (filter === "take_profit" && (m.is_take_profit || (m.signal && (m.signal.includes("止盈") || m.signal.includes("超买") || m.signal.includes("冲顶"))))) match = true;
                else if (filter === "stop" && (m.is_stop || m.is_deep_pullback || (m.signal && (m.signal.includes("破位") || m.signal.includes("跌破") || m.signal.includes("回踩"))))) match = true;
                else if (filter === "芯片与半导体" && (t === "SMH" || t === "SOXX" || t === "SOXL" || t === "SOXS" || t === "KORU")) match = true;
                else if (filter === "云与软件" && (t === "QQQM" || t === "IGV" || t === "SQQQ")) match = true;
                else if (filter === "物理与安全" && (t === "IBIT")) match = true;

                if (match) {{
                    items.push({{
                        ticker: t,
                        name: m.name,
                        sub: m.sub || m.role,
                        cat: m.cat || m.category,
                        cost: m.cost || 0,
                        shares: m.shares || 0,
                        trim1: m.trim1 || 0,
                        trim1_shares: m.trim1_shares || 0,
                        trim2: m.trim2 || 0,
                        base_val: m.base_val || 0,
                        target: m.target || 0,
                        last: m.last,
                        pct_change: m.pct_change || 0,
                        active_price: m.active_price || m.last,
                        ext_price: m.ext_price || 0,
                        ext_pct: m.ext_pct || 0,
                        session_tag: m.session_tag || "",
                        ma50: m.ma50,
                        ema20: m.ema20 || m.ma50,
                        bias: m.bias,
                        atr: m.atr,
                        rsi: m.rsi,
                        fib_low: m.fib_low,
                        fib_high: m.fib_high,
                        dist_to_pit: m.dist_to_pit,
                        pit_status: m.pit_status,
                        fractional_shares: m.fractional_shares || 0,
                        signal: m.signal || m.status,
                        tp_reason: m.tp_reason || "",
                        is_pit: m.is_pit,
                        is_stop: m.is_stop,
                        is_deep_pullback: m.is_deep_pullback,
                        is_take_profit: m.is_take_profit,
                        optimal_limit: m.optimal_limit,
                        limit_note: m.limit_note,
                        entry_stop_loss: m.entry_stop_loss,
                        stop_loss_pct: m.stop_loss_pct,
                        intraday_entry_ready: m.intraday_entry_ready,
                        intraday_status_text: m.intraday_status_text,
                        intraday_reason: m.intraday_reason,
                        vwap: m.vwap,
                        rsi_5m: m.rsi_5m,
                        rvol_5m: m.rvol_5m,
                        rs_20d: m.rs_20d,
                        earnings_date: m.earnings_date,
                        days_to_earnings: m.days_to_earnings,
                        earnings_tag: m.earnings_tag,
                        is_earnings_risk: m.is_earnings_risk,
                        has_options: m.has_options || false,
                        options_exp: m.options_exp || "",
                        call_vol: m.call_vol || 0,
                        put_vol: m.put_vol || 0,
                        pcr: m.pcr || 0,
                        dollar_pcr: m.dollar_pcr || 0,
                        pcr_sentiment: m.pcr_sentiment || "",
                        pcr_color: m.pcr_color || "",
                        top_call_info: m.top_call_info || "--",
                        top_put_info: m.top_put_info || "--",
                        iv_atm: m.iv_atm || 0,
                        exp_move_pct: m.exp_move_pct || 0,
                        exp_low: m.exp_low || 0,
                        exp_high: m.exp_high || 0,
                        exp_move_str: m.exp_move_str || "--",
                        is_stock: false
                    }});
                }}
            }});

            items.forEach(item => {{
                const div = document.createElement("div");
                div.className = `stock-item ${{item.ticker === currentTicker ? 'active' : ''}}`;
                div.dataset.ticker = item.ticker;
                
                const pct = item.pct_change || 0;
                const isUp = pct >= 0;
                const pctColor = isUp ? "#00e676" : "#ff5252";
                const pctBg = isUp ? "rgba(0,230,118,0.15)" : "rgba(255,82,82,0.15)";
                const isTakeProfit = item.is_take_profit || (item.signal && (item.signal.includes("止盈") || item.signal.includes("超买") || item.signal.includes("冲顶")));
                const isStop = item.is_stop || (item.signal && (item.signal.includes("破位") || item.signal.includes("跌破")));
                const isPullback = item.is_deep_pullback || (item.signal && item.signal.includes("回踩"));
                const isStarred = targetWatchlist.includes(item.ticker);
                const isHeld = Boolean(item.shares && item.shares > 0);
                const isEntryReady = Boolean(item.intraday_entry_ready);
                const isAlphaLeader = Boolean(item.is_pit && item.rs_20d !== undefined && item.rs_20d >= 3.0);
                const isEarnRisk = Boolean(item.is_earnings_risk);
                const hasExt = item.session_tag && item.ext_price && item.ext_price > 0;
                const extPct = item.ext_pct || 0;
                const extColor = extPct >= 0 ? "#00e676" : "#ff5252";

                div.innerHTML = `
                    <div class="s-info">
                        <div class="s-ticker">
                            ${{item.ticker}}
                            ${{isHeld ? '<span style="background:rgba(56,189,248,0.22); color:#38bdf8; border:1px solid rgba(56,189,248,0.5); border-radius:3px; padding:0 3px; font-size:9px; margin-left:3px;" title="Firstrade实盘持仓">💼持仓</span>' : ''}}
                            ${{isStarred ? '<span style="color:#ffd700; font-size:10px; margin-left:3px;" title="已加入建仓盯盘">⭐</span>' : ''}}
                            ${{isEntryReady && isStarred ? '<span style="background:rgba(0,230,118,0.2); color:#00e676; border:1px solid rgba(0,230,118,0.5); border-radius:3px; padding:0 3px; font-size:9px; margin-left:3px;">🎯买点</span>' : ''}}
                            ${{item.is_pit ? '<span style="color:#00e676; font-size:10px; margin-left:3px;">🟢坑</span>' : ''}}
                            ${{isAlphaLeader ? '<span style="color:#ffd700; font-size:9px; margin-left:2px;" title="20日相对大盘超额抗跌王">👑抗跌</span>' : ''}}
                            ${{isEarnRisk ? `<span style="color:#ffa726; font-size:9px; margin-left:2px;" title="${{item.earnings_tag}}">⚠️财报</span>` : ''}}
                            ${{isTakeProfit ? '<span style="color:#d946ef; font-size:10px; margin-left:3px;">🟣盈</span>' : ''}}
                            ${{isStop ? '<span style="color:#ff5252; font-size:10px; margin-left:3px;">🔴破</span>' : ''}}
                            ${{isPullback ? '<span style="color:#ff9100; font-size:10px; margin-left:3px;">🟠回</span>' : ''}}
                        </div>
                        <div class="s-name">${{item.name}} · ${{item.cat || ''}}</div>
                    </div>
                    <div class="s-quotes">
                        <div class="s-price">$${{item.last.toFixed(2)}}</div>
                        <div class="s-pct" style="color:${{pctColor}}; background:${{pctBg}}; padding:1px 5px; border-radius:3px;">
                            ${{isUp ? '+' : ''}}${{pct.toFixed(2)}}%
                        </div>
                        ${{hasExt ? `<div style="font-size:10px; color:#93c5fd; margin-top:2px;">${{item.session_tag.includes("夜盘") ? '🌌' : (item.session_tag.includes("盘前") ? '🌅' : '🌙')}}${{item.session_tag}} $${{item.ext_price.toFixed(2)}} <span style="color:${{extColor}};">(${{extPct >= 0 ? '+' : ''}}${{extPct.toFixed(2)}}%)</span></div>` : ''}}
                    </div>
                `;

                div.onclick = () => selectTicker(item.ticker);
                container.appendChild(div);
            }});
        }}

        // 切换当前选中股票
        function selectTicker(ticker) {{
            currentTicker = ticker;
            try {{
                localStorage.setItem("lastSelectedTicker", ticker);
            }} catch(e) {{}}



            // 📱 手机端联动：若在行情列表视图下点击了标的，自动跳到 K线 视窗查看
            if (window.innerWidth <= 768 && currentMobileTab === "watchlist") {{
                switchMobileTab("chart");
            }}

            // 查找标的信息
            const stock = STOCKS_DATA.find(s => s.ticker === ticker);
            const macro = MACRO_DATA[ticker];
            const data = stock || macro || {{}};

            // 更新顶部 Header 标识
            document.getElementById("curSym").innerText = ticker;
            document.getElementById("curName").innerText = data.name || "";
            document.getElementById("curPrice").innerText = "$" + (data.last ? data.last.toFixed(2) : "--");
            
            const pct = data.pct_change || 0;
            const curChangeEl = document.getElementById("curChange");
            curChangeEl.innerText = (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%";
            curChangeEl.style.color = pct >= 0 ? "#00e676" : "#ff5252";
            curChangeEl.style.background = pct >= 0 ? "rgba(0,230,118,0.15)" : "rgba(255,82,82,0.15)";

            // 更新盘前/盘后/夜盘扩展报价徽章
            const extBadgeEl = document.getElementById("curExtBadge");
            if (extBadgeEl) {{
                if (data.session_tag && data.ext_price && data.ext_price > 0) {{
                    const extPct = data.ext_pct || 0;
                    const extSign = extPct >= 0 ? "+" : "";
                    const extClr = extPct >= 0 ? "#00e676" : "#ff5252";
                    extBadgeEl.style.display = "inline-block";
                    const sIcon = data.session_tag.includes("夜盘") ? "🌌" : (data.session_tag.includes("盘前") ? "🌅" : "🌙");
                    extBadgeEl.innerHTML = `${{sIcon}} ${{data.session_tag}}: <b>$${{data.ext_price.toFixed(2)}}</b> <span style="color:${{extClr}}; margin-left:3px;">(${{extSign}}${{extPct.toFixed(2)}}%)</span>`;
                }} else {{
                    extBadgeEl.style.display = "none";
                }}
            }}

            // 更新顶部财报避雷徽章
            const earnBadgeEl = document.getElementById("curEarningsBadge");
            if (earnBadgeEl) {{
                if (data.earnings_tag) {{
                    earnBadgeEl.style.display = "inline-block";
                    earnBadgeEl.innerText = data.earnings_tag;
                    if (data.is_earnings_risk) {{
                        earnBadgeEl.style.background = "rgba(255, 82, 82, 0.18)";
                        earnBadgeEl.style.color = "#ff5252";
                        earnBadgeEl.style.borderColor = "rgba(255, 82, 82, 0.45)";
                    }} else {{
                        earnBadgeEl.style.background = "rgba(255, 167, 38, 0.14)";
                        earnBadgeEl.style.color = "#ffa726";
                        earnBadgeEl.style.borderColor = "rgba(255, 167, 38, 0.35)";
                    }}
                }} else {{
                    earnBadgeEl.style.display = "none";
                }}
            }}

            // 1. 基础指标与六维量化矩阵
            const curLast = data.last || 0;
            const curActive = data.active_price || curLast;
            const curMa50 = data.ma50 || 0;
            const curEma20 = data.ema20 || curMa50;
            const curBias = data.bias !== undefined ? data.bias : (curMa50 > 0 ? ((curLast - curMa50)/curMa50*100) : 0);
            const curAtr = data.atr || 0;
            const curRsi = data.rsi !== undefined ? data.rsi : 50;
            const curVolRatio = data.vol_ratio !== undefined ? data.vol_ratio : 1.0;
            const curFibLow = data.fib_low || 0;
            const curFibHigh = data.fib_high || 0;
            const curDistPit = data.dist_to_pit !== undefined ? data.dist_to_pit : 0;
            const curSignal = data.signal || "";
            const curCat = data.cat || data.sub || "";

            document.getElementById("pSector").innerText = curCat || "科技龙头";
            document.getElementById("pMa50").innerText = curMa50 ? ("$" + curMa50.toFixed(2)) : "--";
            
            const biasEl = document.getElementById("pBias");
            if (data.bias !== undefined) {{
                biasEl.innerText = (curBias >= 0 ? "+" : "") + curBias.toFixed(2) + "%";
                biasEl.style.color = curBias >= 0 ? "#00e676" : "#ff5252";
            }}
            document.getElementById("pEma20").innerText = curEma20 ? ("$" + curEma20.toFixed(2)) : "--";
            
            const volEl = document.getElementById("pVolRatio");
            volEl.innerText = curVolRatio ? (curVolRatio.toFixed(2) + "x") : "--";
            volEl.style.color = curVolRatio >= 1.25 ? "#ffd600" : (curVolRatio <= 0.85 ? "#90caf9" : "#f3f4f6");
            
            const rsiEl = document.getElementById("pRsi");
            rsiEl.innerText = curRsi.toFixed(1);
            rsiEl.style.color = curRsi >= 70 ? "#d946ef" : (curRsi <= 35 ? "#00e676" : "#f3f4f6");

            document.getElementById("pAtr").innerText = curAtr ? ("$" + curAtr.toFixed(2)) : "--";

            // 2. 实盘持仓 (INTC 7股 / SOXL 12.22594股) 专属风控与自救解套哨 (支持盘前盘后夜盘实时价格穿透)
            const intcBox = document.getElementById("intcTacticalBox");
            if (data.shares && data.shares > 0) {{
                intcBox.style.display = "block";
                const curPriceVal = curActive || curLast;
                const cost = data.cost || 95.53429;
                const shares = data.shares || 7.0;
                const trim1 = data.trim1 || 91.80;
                const trim1Shares = data.trim1_shares || 5.0;
                const trim2 = data.trim2 || 0;
                const stopLoss = data.base_val || 88.00;
                const targetVal = data.target || cost;
                const totalCost = cost * shares;
                const totalVal = curPriceVal * shares;
                const pnlPct = ((curPriceVal - cost) / cost) * 100;
                const pnlAmount = totalVal - totalCost;
                const bufferStop = curPriceVal - stopLoss;
                const bufferPct = (bufferStop / stopLoss) * 100;
                const trim1Cash = Math.round(trim1 * trim1Shares);
                const shareStr = Number.isInteger(shares) ? shares.toFixed(0) : shares.toFixed(5);
                const trimShareStr = Number.isInteger(trim1Shares) ? trim1Shares.toFixed(0) : trim1Shares.toFixed(5);

                const titleEl = document.getElementById("intcCardTitle");
                if (titleEl) titleEl.innerText = `🛡️ ${{ticker}} 实盘底仓风控与解套哨`;
                document.getElementById("intcCostBase").innerText = `$${{cost.toFixed(2)}} (持仓 ${{shareStr}} 股)`;
                document.getElementById("intcTotalCost").innerText = `$${{totalCost.toFixed(2)}}`;
                document.getElementById("intcCurrentVal").innerText = `$${{totalVal.toFixed(2)}}`;
                document.getElementById("intcPnlRatio").innerText = `${{pnlPct >= 0 ? '+' : ''}}${{pnlPct.toFixed(2)}}% ($${{pnlAmount >= 0 ? '+' : ''}}${{pnlAmount.toFixed(2)}})`;
                document.getElementById("intcPnlRatio").style.color = pnlPct >= 0 ? "#00e676" : "#ff5252";
                const trim1El = document.getElementById("intcTrim1Val");
                if (trim1El) trim1El.innerText = `$${{trim1.toFixed(2)}} (卖 ${{trimShareStr}} 股回笼 ~$${{trim1Cash}})`;
                const targetEl = document.getElementById("intcTargetVal");
                if (targetEl) targetEl.innerText = trim2 > 0 ? `$${{trim2.toFixed(2)}} (二刀) / $${{targetVal.toFixed(2)}} (全解套)` : `$${{targetVal.toFixed(2)}} (全仓解套)`;
                document.getElementById("intcStopVal").innerText = ticker === "SOXL" ? `$${{stopLoss.toFixed(2)}} (破 $83.50 先砍半)` : `$${{stopLoss.toFixed(2)}}`;

                const descEl = document.getElementById("intcStatusDesc");
                const sessLabel = data.session_tag ? `[${{data.session_tag}}] ` : "";
                if (curPriceVal <= stopLoss) {{
                    descEl.innerHTML = `<span style="color:#ff1744; font-weight:bold;">🚨 ${{sessLabel}}触发硬止损：现价 $${{curPriceVal.toFixed(2)}} 已击穿 $${{stopLoss.toFixed(2)}} 警戒线，严格执行保命防守！</span>`;
                }} else if (bufferPct <= 3.0) {{
                    descEl.innerHTML = `<span style="color:#ffa726; font-weight:bold;">⚠️ ${{sessLabel}}紧迫预警：距 $${{stopLoss.toFixed(2)}} 止损线仅剩 $${{bufferStop.toFixed(2)}} (${{bufferPct.toFixed(1)}}%) 缓冲空间！</span>`;
                }} else if (curPriceVal >= targetVal) {{
                    descEl.innerHTML = `<span style="color:#00e676; font-weight:bold;">🎉 ${{sessLabel}}解套达成：现价 $${{curPriceVal.toFixed(2)}} 已触达 $${{targetVal.toFixed(2)}} 成本目标，建议分批止盈落袋！</span>`;
                }} else if (curPriceVal >= trim1) {{
                    const holdNote = curPriceVal >= curEma20 ? "站稳 EMA20 顺势持有" : "反抽均线压制位，依托客观指标防守";
                    descEl.innerHTML = `<span style="color:#38bdf8; font-weight:bold;">📈 ${{sessLabel}}持仓跟踪：现价 $${{curPriceVal.toFixed(2)}} 已越过参考减仓线 ($${{trim1.toFixed(2)}})，${{holdNote}}。</span>`;
                }} else {{
                    const isLevEtf = ["SOXL", "KORU", "SQQQ", "SOXS"].includes(ticker);
                    const extraTip = ticker === "INTC"
                        ? "⚠️10/29盘后财报窗口，重点防范业绩波动风险。"
                        : (isLevEtf ? "3倍杠杆高波动标的，严格遵循硬止损线与客观形态！" : "严格遵循底线止损纪律，动态跟踪趋势。");
                    descEl.innerHTML = `<span style="color:#93c5fd;">💼 ${{sessLabel}}实盘风控：距止损底线缓冲 +$${{bufferStop.toFixed(2)}} (${{bufferPct.toFixed(1)}}%)。${{extraTip}}</span>`;
                }}
            }} else {{
                intcBox.style.display = "none";
            }}

            // 3. 核心状态研判与四维深度成因分析
            const sig = data.signal || "";
            const isPit = data.is_pit || sig.includes("黄金坑就位");
            const isPitWatch = sig.includes("坑内观察") || sig.includes("待芯片ETF");
            const isStop = data.is_stop || sig.includes("破位") || sig.includes("跌破") || sig.includes("硬止损");
            const isDeepPullback = data.is_deep_pullback || sig.includes("深度回踩");
            const isTakeProfit = data.is_take_profit || sig.includes("冲顶") || sig.includes("止盈") || sig.includes("超买");
            const isBullTrend = sig.includes("多头");

            const bannerEl = document.getElementById("pSignalBanner");
            const sigTextEl = document.getElementById("pSignalText");
            const sigTagEl = document.getElementById("pSignalTag");
            const decBox = document.getElementById("tacticalDecisionBox");
            const decTitleEl = document.getElementById("pDecisionTitle");
            const actionBadgeEl = document.getElementById("pActionTypeBadge");

            let themeColor = "#29b6f6";
            let themeBg = "linear-gradient(135deg, rgba(41, 182, 246, 0.08), #181b22)";
            let themeBorder = "#29b6f6";
            let actionType = "观望评估";
            let mainVerdict = "中性整理 · 等待方向";
            let maReason = "";
            let volReason = "";
            let fibReason = "";
            let macroReason = "";
            let actionDirective = "";
            let allocLabel = "单股配置参考 ($900上限)：";
            let allocSub = "按纪律逢低分批吸筹";
            let allocVal = data.fractional_shares ? (data.fractional_shares.toFixed(2) + " 股") : "--";

            // 1. 均线形态诊断
            if (curBias > 15.0) {{
                maReason = `偏离 MA50 (+${{curBias.toFixed(1)}}%) 极度严重，均线乖离率进入历史极端警报区`;
            }} else if (curBias > 5.0) {{
                maReason = `站稳 MA50 线上 (+${{curBias.toFixed(1)}}%) 且位于 EMA20 ($${{curEma20.toFixed(2)}}) 上方，多头发散上行`;
            }} else if (curBias >= 0) {{
                maReason = `贴近 MA50 均线 (+${{curBias.toFixed(1)}}%)，多头生命线支撑有效，处于正常回踩蓄势`;
            }} else if (curBias >= -8.0) {{
                maReason = `跌破 MA50 (${{curBias.toFixed(1)}}%)，均线暂时承压，属于健康波段洗盘回调区间`;
            }} else {{
                maReason = `深度跌破 MA50 (${{curBias.toFixed(1)}}%)，均线系统呈空头反压排列，中期趋势受损`;
            }}

            // 2. 量价动能诊断
            if (curVolRatio >= 1.5) {{
                volReason = `量比 ${{curVolRatio.toFixed(2)}}x (放量明显)，${{curBias > 8.0 ? '高位大换手赶顶，警惕主力兑现出货' : (curBias < -10.0 ? '放量大跌，空头主动性抛压加剧' : '多空激烈博弈，量能显著放大')}}`;
            }} else if (curVolRatio >= 1.1) {{
                volReason = `量比 ${{curVolRatio.toFixed(2)}}x (成交适度活跃)，量价匹配处于合理区间`;
            }} else if (curVolRatio <= 0.85) {{
                volReason = `量比 ${{curVolRatio.toFixed(2)}}x (显著缩量)，${{curBias < 0 ? '缩量阴跌假摔，主力诱空洗盘特征明显，无恐慌大割肉' : '缩量休整，筹码结构锁定良好'}}`;
            }} else {{
                volReason = `量比 ${{curVolRatio.toFixed(2)}}x，成交量能处于常态水平`;
            }}

            // 3. 波段支撑 (斐波那契) 诊断
            if (curFibLow && curFibHigh) {{
                if (curLast >= curFibLow && curLast <= curFibHigh) {{
                    fibReason = `精准落入 60日斐波那契 0.5~0.618 黄金坑支撑带 ($${{curFibLow.toFixed(2)}} ~ $${{curFibHigh.toFixed(2)}})，处于战略低吸区`;
                }} else if (isPit && curDistPit >= -2.0) {{
                    fibReason = `处于黄金坑底极窄洗盘弹簧区 (${{curDistPit.toFixed(1)}}%，坑底线 $${{curFibLow.toFixed(2)}})，属主力诱空洗盘，未实质破位`;
                }} else if (curLast > curFibHigh) {{
                    fibReason = `运行于黄金坑上方 (+${{curDistPit.toFixed(1)}}%)，波段支撑带位于 $${{curFibLow.toFixed(2)}} ~ $${{curFibHigh.toFixed(2)}}`;
                }} else {{
                    fibReason = `击穿波段坑底支撑 -${{Math.abs(curDistPit).toFixed(1)}}% (坑底线 $${{curFibLow.toFixed(2)}})，斐波那契多头支撑已破位`;
                }}
            }} else {{
                fibReason = `基准支撑带平稳运行`;
            }}

            // 4. 板块与大盘共振诊断
            const isSemi = ["芯片算力", "晶圆代工", "制造设备", "存储芯片", "通信芯片", "半导体"].includes(curCat);
            const smhData = MACRO_DATA["SMH"];
            const smhBias = smhData ? smhData.bias : 0;
            if (isSemi && smhBias < -3.0) {{
                macroReason = `⚠️ 核心半导体 ETF (SMH) 偏离 MA50 ${{smhBias.toFixed(1)}}% 破位承压，芯片赛道受行业贝塔压制`;
            }} else {{
                macroReason = `大盘气象评级为【${{WEATHER_INFO.weather || '防守'}}】，全市场环境需保持仓位纪律`;
            }}

            // 5. 综合战术分支判定与操盘指令输出
            if (isPitWatch) {{
                themeColor = "#f57f17";
                themeBg = "linear-gradient(135deg, rgba(245, 127, 23, 0.08), #181b22)";
                themeBorder = "#f57f17";
                actionType = "暂停接刀 / 保持观望";
                mainVerdict = "🟡 坑内观察 · 严禁抢跑左侧接刀";
                actionDirective = `【受板块拖累暂缓买入】个股估值与价位虽已落入黄金坑防守带，但核心芯片 ETF (SMH) 偏离 MA50 达 ${{smhBias.toFixed(1)}}% 处于破位弱势，存在板块系统性补跌风险。必须等待半导体板块收出右侧止跌企稳阳线后再行启动碎股定投！`;
                allocLabel = "仓位指令：";
                allocSub = "等待板块企稳确认";
                allocVal = "暂缓加仓";
            }} else if (isPit) {{
                themeColor = "#00e676";
                themeBg = "linear-gradient(135deg, rgba(0, 230, 118, 0.08), #181b22)";
                themeBorder = "#00e676";
                actionType = "分批低吸建仓";
                mainVerdict = "🟢 黄金坑就位 · 绝佳低吸建仓窗口";
                actionDirective = `【分批建仓窗口】标的波段回调幅度充分 (RSI ${{curRsi.toFixed(1)}} 超卖)，已进入 0.5~0.618 强力防守带。建议严格执行单股 $900 限额，以金字塔式逢低吸筹 ${{data.fractional_shares ? data.fractional_shares.toFixed(2) : '--'}} 股，严禁情绪化一次性重仓！`;
                allocLabel = "单股配置建议 ($900上限)：";
                allocSub = "金字塔分批建仓";
                allocVal = data.fractional_shares ? (data.fractional_shares.toFixed(2) + " 股") : "--";
            }} else if (isStop) {{
                themeColor = "#ff1744";
                themeBg = "linear-gradient(135deg, rgba(255, 23, 68, 0.08), #181b22)";
                themeBorder = "#ff1744";
                actionType = "破位风控 / 严禁抄底";
                mainVerdict = "🔴 放量破位杀跌 · 严禁逆势接飞刀";
                actionDirective = `【铁血风控指令】标的放量击穿关键斐波那契坑底防线 (跌破 ${{Math.abs(curDistPit).toFixed(1)}}%)，空头动能处于主导释放期。持仓者若跌破底线必须坚决离场止损；空仓观察者坚决管住手，切勿盲目猜底！`;
                allocLabel = "风控红线：";
                allocSub = "禁止任何左侧买入";
                allocVal = "严禁接刀";
            }} else if (isDeepPullback) {{
                themeColor = "#ff9100";
                themeBg = "linear-gradient(135deg, rgba(255, 145, 0, 0.08), #181b22)";
                themeBorder = "#ff9100";
                actionType = "深度回踩 / 严控追高";
                mainVerdict = "🟠 深度回踩 · 等待右侧量价共振";
                if (curVolRatio <= 0.85) {{
                    actionDirective = `【多看少动耐心观望】标的虽已跌破斐波那契坑底，但日线量比仅 ${{curVolRatio.toFixed(2)}}x 呈现显著缩量假摔特征。虽无主力大抛压，但亦无大资金承接，切忌盲目加仓摊平，等待右侧放量企稳信号。`;
                }} else if (pct >= 0) {{
                    actionDirective = `【超跌反抽·莫追高】标的处于黄金坑底 ($${{curFibLow.toFixed(2)}}) 下方深度回踩通道，今日收阳反抽 (${{pct >= 0 ? '+' : ''}}${{pct.toFixed(2)}}%，量比 ${{curVolRatio.toFixed(2)}}x)，空头宣泄暂缓；切勿在反抽高位追涨，等待回踩日内 VWAP 或上方 5m 量价共振信号。`;
                }} else {{
                    actionDirective = `【多看少动耐心观望】标的已跌破斐波那契坑底 ($${{curFibLow.toFixed(2)}})，当前日线量比 ${{curVolRatio.toFixed(2)}}x 未达恐慌放量破位阈值 (1.25x)，属弱势寻底阶段，等待 5m 站上 VWAP 且量比 ≥1.20x 共振确认。`;
                }}
                allocLabel = "观望策略：";
                allocSub = "等待右侧量价共振";
                allocVal = "持币等待";
            }} else if (isTakeProfit) {{
                themeColor = "#d946ef";
                themeBg = "linear-gradient(135deg, rgba(217, 70, 239, 0.08), #181b22)";
                themeBorder = "#d946ef";
                actionType = "高位减仓 / 锁定利润";
                mainVerdict = "🟣 冲顶减仓 · 启动分批锁定胜果";
                actionDirective = `【获利了结指引】技术面进入统计学极度超买高潮区 (RSI ${{curRsi.toFixed(1)}}，偏离 ${{curBias.toFixed(1)}}%)。持仓者建议分批卖出 1/3 ~ 1/2 锁定丰厚利润，剩余底仓以 EMA20 ($${{curEma20.toFixed(2)}}) 作为移动保护止损！`;
                allocLabel = "止盈指令：";
                allocSub = "落袋为安锁定胜果";
                allocVal = "分批减半";
            }} else if (isBullTrend) {{
                themeColor = "#29b6f6";
                themeBg = "linear-gradient(135deg, rgba(41, 182, 246, 0.08), #181b22)";
                themeBorder = "#29b6f6";
                actionType = "顺势持有 / 移动保护";
                mainVerdict = "🔵 强势多头推进 · 顺势持有待涨";
                actionDirective = `【顺势而为让利润奔跑】主升浪趋势健康，均线呈良性多头排列。依托 EMA20 ($${{curEma20.toFixed(2)}}) 作为趋势移动生命线，只要收盘不破 EMA20 安心持股待涨，不盲目追高。`;
                allocLabel = "持股策略：";
                allocSub = "沿EMA20移动保护";
                allocVal = "持股跟踪";
            }} else {{
                themeColor = "#94a3b8";
                themeBg = "linear-gradient(135deg, rgba(148, 163, 184, 0.08), #181b22)";
                themeBorder = "#64748b";
                actionType = "区间震荡 / 耐心蓄势";
                mainVerdict = "⚪ 震荡整理蓄势 · 多看少动中性仓位";
                actionDirective = `【箱体整理耐心中性】当前处于区间均线反复争夺阶段，未触发任何确定性波段信号。保持中性仓位，耐心等待股价回踩进入黄金坑或放量向上突破箱体。`;
                allocLabel = "操作纪律：";
                allocSub = "等待确定性突破";
                allocVal = "中性观望";
            }}

            // 6. 统一渲染注入 DOM
            bannerEl.style.color = themeColor;
            bannerEl.style.background = themeBg;
            bannerEl.style.borderColor = themeBorder;
            sigTextEl.innerText = curSignal || mainVerdict;
            sigTagEl.innerText = actionType;
            sigTagEl.style.color = themeColor;

            decBox.style.borderLeftColor = themeBorder;
            decBox.style.background = themeBg;
            decTitleEl.innerText = mainVerdict;
            decTitleEl.style.color = themeColor;
            actionBadgeEl.innerText = actionType;
            actionBadgeEl.style.color = themeColor;

            document.getElementById("pMaReason").innerText = maReason;
            document.getElementById("pVolReason").innerText = volReason;
            document.getElementById("pFibReason").innerText = fibReason;
            document.getElementById("pMacroReason").innerText = macroReason;
            document.getElementById("pActionDirective").innerHTML = actionDirective;
            document.getElementById("pAllocLabel").innerText = allocLabel;
            document.getElementById("pAllocSub").innerText = allocSub;
            document.getElementById("pFractional").innerText = allocVal;
            document.getElementById("pFractional").style.color = themeColor;

            // 7. 日内精准建仓挂单指南测算与盯盘 UI 同步
            let optimalLimit = data.optimal_limit || curActive;
            if (!data.optimal_limit && curFibHigh > curFibLow) {{
                const midAxis = curFibLow + 0.382 * (curFibHigh - curFibLow);
                optimalLimit = Math.round(midAxis * 100) / 100;
            }}
            const maxAlloc = data.max_alloc || 900.0;
            const totalFractional = data.fractional_shares || (curActive > 0 ? (maxAlloc / curActive) : 0);
            const b1Shares = Math.round(totalFractional * 0.30 * 100) / 100;
            const b1Val = Math.round(b1Shares * optimalLimit * 100) / 100;
            const rawPitStop = curFibLow > 0 ? curFibLow * 0.97 : (optimalLimit * 0.96);
            const fallbackStop = (rawPitStop >= optimalLimit * 0.90 && rawPitStop <= optimalLimit * 0.985) ? rawPitStop : (optimalLimit * 0.96);
            const stopLoss = data.entry_stop_loss || Math.round(fallbackStop * 100) / 100;
            const stopLossPct = data.stop_loss_pct !== undefined ? data.stop_loss_pct : (optimalLimit > 0 ? ((stopLoss - optimalLimit) / optimalLimit * 100) : -4.0);

            // 5分钟量化日内买点状态横幅渲染
            const intraBannerEl = document.getElementById("pIntradayBanner");
            const intraStatusEl = document.getElementById("pIntradayStatusText");
            const intraReasonEl = document.getElementById("pIntradayReason");
            const isEntryReady = Boolean(data.intraday_entry_ready);
            const intraStatusText = data.intraday_status_text || (isEntryReady ? "🎯 买点触发: 5m量化企稳" : "⏳ 蹲守挂单价");
            const intraReason = data.intraday_reason || "等待价格回踩至建议挂单价附近并出现 5m RSI 超卖拐头或 VWAP 突破信号。";

            if (intraBannerEl && intraStatusEl && intraReasonEl) {{
                intraStatusEl.innerText = intraStatusText;
                intraReasonEl.innerText = intraReason;
                if (isEntryReady) {{
                    intraBannerEl.style.background = "rgba(0, 230, 118, 0.16)";
                    intraBannerEl.style.borderColor = "#00e676";
                    intraStatusEl.style.color = "#00e676";
                }} else if (intraStatusText.includes("严禁") || intraStatusText.includes("超买") || intraStatusText.includes("跌破")) {{
                    intraBannerEl.style.background = "rgba(255, 82, 82, 0.14)";
                    intraBannerEl.style.borderColor = "rgba(255, 82, 82, 0.45)";
                    intraStatusEl.style.color = "#ff5252";
                }} else {{
                    intraBannerEl.style.background = "rgba(255, 215, 0, 0.10)";
                    intraBannerEl.style.borderColor = "rgba(255, 215, 0, 0.35)";
                    intraStatusEl.style.color = "#ffd700";
                }}
            }}

            const vwapEl = document.getElementById("pVwap");
            if (vwapEl) {{
                const vwapVal = data.vwap || curLast;
                const vwapDiff = vwapVal > 0 ? ((curActive - vwapVal) / vwapVal * 100) : 0;
                vwapEl.innerHTML = vwapVal > 0 ? `$${{vwapVal.toFixed(2)}} <span style="font-size:10px; color:${{vwapDiff >= 0 ? '#00e676' : '#ff5252'}};">(${{vwapDiff >= 0 ? '+' : ''}}${{vwapDiff.toFixed(2)}}%)</span>` : "--";
            }}
            const rsi5mEl = document.getElementById("pRsi5m");
            if (rsi5mEl) {{
                const r5 = data.rsi_5m !== undefined ? data.rsi_5m : curRsi;
                const rv5 = data.rvol_5m !== undefined ? data.rvol_5m : 1.0;
                rsi5mEl.innerHTML = r5 ? `${{r5.toFixed(1)}} <span style="font-size:10px; color:${{rv5 >= 1.25 ? '#ffd600' : '#9ca3af'}};">(${{rv5.toFixed(2)}}x)</span>` : "--";
                rsi5mEl.style.color = r5 <= 35 ? "#00e676" : (r5 >= 70 ? "#d946ef" : "#f3f4f6");
            }}
            const rs20dEl = document.getElementById("pRs20d");
            if (rs20dEl) {{
                const rsVal = data.rs_20d !== undefined ? data.rs_20d : 0;
                const rsSign = rsVal >= 0 ? "+" : "";
                const rsTag = rsVal >= 3.0 ? "👑抗跌" : (rsVal >= 0 ? "💪强" : "🥀弱");
                rs20dEl.innerText = `${{rsSign}}${{rsVal.toFixed(1)}}% (${{rsTag}})`;
                rs20dEl.style.color = rsVal >= 3.0 ? "#ffd700" : (rsVal >= 0 ? "#00e676" : "#ff5252");
            }}
            const earnEl = document.getElementById("pEarningsDate");
            if (earnEl) {{
                if (data.earnings_tag) {{
                    earnEl.innerText = data.earnings_tag;
                    earnEl.style.color = data.is_earnings_risk ? "#ff5252" : "#ffa726";
                }} else {{
                    earnEl.innerText = "🟢 近期无财报";
                    earnEl.style.color = "#9ca3af";
                }}
            }}

            const optEl = document.getElementById("pOptimalLimit");
            if (optEl) optEl.innerText = "$" + optimalLimit.toFixed(2);
            
            const diffPct = curActive > 0 ? ((optimalLimit - curActive) / curActive * 100) : 0;
            const noteEl = document.getElementById("pOptimalNote");
            if (noteEl) {{
                const basisText = data.limit_note || "黄金坑中下轴支撑";
                const hasPctInNote = basisText.includes("%");
                if (diffPct < -0.05) {{
                    noteEl.innerText = hasPctInNote ? basisText : `${{basisText}} (较现价折让 ${{diffPct.toFixed(1)}}%)`;
                    noteEl.style.color = "#ffd700";
                }} else {{
                    noteEl.innerText = hasPctInNote ? `${{basisText}} · 已达击球区` : `${{basisText}} (已达目标击球区)`;
                    noteEl.style.color = "#00e676";
                }}
            }}
            const b1El = document.getElementById("pBatch1Shares");
            if (b1El) b1El.innerText = b1Shares.toFixed(2) + " 股";
            const b1ValEl = document.getElementById("pBatch1Val");
            if (b1ValEl) b1ValEl.innerText = `约 $${{b1Val.toFixed(1)}} (3-3-4首笔30%)`;
            const stopEl = document.getElementById("pEntryStopLoss");
            if (stopEl) stopEl.innerHTML = stopLoss > 0 ? `$${{stopLoss.toFixed(2)}} <span style="font-size:10px; color:#f87171;">(${{stopLossPct.toFixed(1)}}%)</span>` : "--";
            const bandEl = document.getElementById("pEntryFibBand");
            if (bandEl) bandEl.innerText = (curFibLow && curFibHigh) ? (`$${{curFibLow.toFixed(2)}} ~ $${{curFibHigh.toFixed(2)}}`) : "--";

            // 2.8 机构期权多空异动雷达动态渲染
            const optBox = document.getElementById("optionsAnomalyBox");
            if (optBox) {{
                if (data.has_options) {{
                    optBox.style.display = "block";
                    const pcrBadge = document.getElementById("pPcrSentimentBadge");
                    if (pcrBadge) {{
                        pcrBadge.innerText = data.pcr_sentiment || "🟡 多空均衡";
                        pcrBadge.style.color = data.pcr_color || "#ffd700";
                        pcrBadge.style.background = (data.pcr_color || "#ffd700") + "22";
                        pcrBadge.style.border = "1px solid " + (data.pcr_color || "#ffd700") + "55";
                    }}
                    const pcrVal = document.getElementById("pOptionsPcr");
                    if (pcrVal) {{
                        const volPcrStr = data.pcr !== undefined ? data.pcr.toFixed(2) : "--";
                        const dlrPcrStr = data.dollar_pcr !== undefined ? data.dollar_pcr.toFixed(2) : volPcrStr;
                        pcrVal.innerText = `${{volPcrStr}} / 资金 ${{dlrPcrStr}}`;
                        pcrVal.style.color = data.pcr_color || "#f3f4f6";
                    }}
                    const volVal = document.getElementById("pOptionsVolRatio");
                    if (volVal) {{
                        const cv = data.call_vol ? (data.call_vol >= 1000 ? (data.call_vol / 1000).toFixed(1) + "k" : data.call_vol) : "0";
                        const pv = data.put_vol ? (data.put_vol >= 1000 ? (data.put_vol / 1000).toFixed(1) + "k" : data.put_vol) : "0";
                        volVal.innerHTML = `<span style="color:#00e676;">Call ${{cv}}</span> vs <span style="color:#ff5252;">Put ${{pv}}</span>`;
                    }}
                    const ivEl = document.getElementById("pOptionsIv");
                    if (ivEl) {{
                        ivEl.innerText = (data.iv_atm && data.iv_atm > 0) ? `${{data.iv_atm.toFixed(1)}}%` : "--";
                    }}
                    const expMoveEl = document.getElementById("pOptionsExpMove");
                    if (expMoveEl) {{
                        expMoveEl.innerText = data.exp_move_str || "--";
                    }}
                    const callStrike = document.getElementById("pTopCallStrike");
                    if (callStrike) callStrike.innerText = data.top_call_info || "--";
                    const putStrike = document.getElementById("pTopPutStrike");
                    if (putStrike) putStrike.innerText = data.top_put_info || "--";
                    const expTip = document.getElementById("pOptionsExpTip");
                    if (expTip) expTip.innerText = `*基于 ${{data.options_exp || '近期主力周'}} 权利金大单与 ATM Straddle 定价测算 (30m本地缓存·0额度)`;
                }} else {{
                    optBox.style.display = "none";
                }}
            }}

            updateStarUI();

            // 更新左侧高亮状态 (精确匹配 dataset.ticker，杜绝 BE/MU/KO 误匹配)
            document.querySelectorAll(".stock-item").forEach(el => {{
                el.classList.toggle("active", el.dataset.ticker === ticker);
            }});

            // 重新载入图表
            loadTvWidget(currentTicker, currentInterval, currentStyle);
        }}

        // 周期按钮点击监听绑定 (分时、5日、日K、周K、月K、季K、年K)
        document.querySelectorAll(".period-tab").forEach(tab => {{
            tab.addEventListener("click", function() {{
                document.querySelectorAll(".period-tab").forEach(t => t.classList.remove("active"));
                document.getElementById("minDropdownBtn").classList.remove("active");
                document.querySelectorAll(".dropdown-item").forEach(d => d.classList.remove("active"));
                
                this.classList.add("active");
                currentInterval = this.getAttribute("data-period");
                currentStyle = this.getAttribute("data-style") || "1";

                loadTvWidget(currentTicker, currentInterval, currentStyle);
            }});
        }});

        // 分钟下拉菜单交互控制
        const minBtn = document.getElementById("minDropdownBtn");
        const minMenu = document.getElementById("minDropdownMenu");

        minBtn.addEventListener("click", function(e) {{
            e.stopPropagation();
            minMenu.classList.toggle("show");
        }});

        document.querySelectorAll(".dropdown-item").forEach(item => {{
            item.addEventListener("click", function(e) {{
                e.stopPropagation();
                document.querySelectorAll(".period-tab").forEach(t => t.classList.remove("active"));
                document.querySelectorAll(".dropdown-item").forEach(d => d.classList.remove("active"));
                
                this.classList.add("active");
                minBtn.classList.add("active");

                const period = this.getAttribute("data-period");
                const label = this.getAttribute("data-label");
                document.getElementById("currentMinLabel").innerText = label;

                currentInterval = period;
                currentStyle = "1";
                minMenu.classList.remove("show");

                loadTvWidget(currentTicker, currentInterval, currentStyle);
            }});
        }});

        // 点击页面其他区域关闭下拉菜单
        window.addEventListener("click", function() {{
            minMenu.classList.remove("show");
        }});

        // 左侧自选分组切换
        document.querySelectorAll(".w-tab").forEach(tab => {{
            tab.addEventListener("click", function() {{
                document.querySelectorAll(".w-tab").forEach(t => t.classList.remove("active"));
                this.classList.add("active");
                renderWatchlist(this.getAttribute("data-filter"));
            }});
        }});

        // 📱 手机端专属 5 栏底部 Tab 导航切换引擎
        let currentMobileTab = "watchlist";
        function switchMobileTab(tab) {{
            currentMobileTab = tab;
            document.querySelectorAll(".m-nav-tab").forEach(t => {{
                t.classList.toggle("active", t.getAttribute("data-tab") === tab);
            }});

            const mainEl = document.getElementById("futuMain");
            if (mainEl) {{
                mainEl.classList.remove("m-view-watchlist", "m-view-chart", "m-view-tactics", "m-view-options", "m-view-portfolio");
                mainEl.classList.add(`m-view-${{tab}}`);
            }}

            if (tab === "tactics") {{
                filterRightCards("entry");
            }} else if (tab === "options") {{
                filterRightCards("options");
            }} else if (tab === "portfolio") {{
                filterRightCards("risk");
            }} else if (tab === "chart") {{
                setTimeout(() => window.dispatchEvent(new Event('resize')), 50);
            }}
        }}

        // 📱 手机端 K 线周期切换按钮事件
        document.querySelectorAll(".m-period-tab").forEach(tab => {{
            tab.addEventListener("click", function() {{
                document.querySelectorAll(".m-period-tab").forEach(t => t.classList.remove("active"));
                this.classList.add("active");
                currentInterval = this.getAttribute("data-period");
                currentStyle = this.getAttribute("data-style") || "1";
                loadTvWidget(currentTicker, currentInterval, currentStyle);
            }});
        }});

        // 侧边栏折叠切换
        function toggleSidebar() {{
            const sb = document.getElementById("sidebarLeft");
            const btn = document.getElementById("btnToggleSidebar");
            if (!sb) return;
            const isCol = sb.classList.toggle("collapsed");
            if (btn) {{
                btn.innerText = isCol ? "▶" : "◀";
                btn.classList.toggle("btn-is-collapsed", isCol);
                btn.title = isCol ? "展开标的列表" : "折叠标的列表";
            }}
        }}

        // 右侧面板卡片分类筛选 (解决拥挤，一键精简)
        function filterRightCards(cat) {{
            document.querySelectorAll(".p-filter-pill").forEach(p => {{
                p.classList.toggle("active", p.getAttribute("data-filter") === cat);
            }});
            // 若点击 💼持仓 且当前选中标的无持仓，自动切换至第一只实盘持仓标的，避免右侧空白
            if (cat === "risk") {{
                const curPort = userPortfolio[currentTicker];
                if (!curPort || !(curPort.shares > 0)) {{
                    const firstHeld = Object.keys(userPortfolio).find(k => userPortfolio[k] && userPortfolio[k].shares > 0);
                    if (firstHeld) {{
                        selectTicker(firstHeld);
                    }}
                }}
            }}
            const boxes = document.querySelectorAll("#sidebarRight .panel-box");
            boxes.forEach(box => {{
                const bCat = box.getAttribute("data-cat");
                if (cat === "all") {{
                    box.classList.remove("card-hidden");
                }} else {{
                    if (bCat === cat || bCat === "quotes") {{
                        box.classList.remove("card-hidden");
                        box.classList.remove("collapsed");
                        const toggleBtn = box.querySelector(".card-toggle-btn");
                        if (toggleBtn) toggleBtn.innerText = "▲";
                    }} else {{
                        box.classList.add("card-hidden");
                    }}
                }}
            }});
        }}

        // 单个卡片折叠/展开
        function toggleCard(btn) {{
            const box = btn.closest(".panel-box");
            if (!box) return;
            const isCol = box.classList.toggle("collapsed");
            btn.innerText = isCol ? "▼" : "▲";
        }}

        // 纯净全屏看盘切换
        function toggleFullscreen() {{
            if (!document.fullscreenElement) {{
                document.documentElement.requestFullscreen().catch(e => {{}});
            }} else {{
                if (document.exitFullscreen) document.exitFullscreen();
            }}
        }}

        // Toast 浮动提示
        function showToast(msg) {{
            const t = document.getElementById("toastBox");
            if (!t) return;
            t.innerText = msg;
            t.style.display = "block";
            setTimeout(() => {{ t.style.display = "none"; }}, 2800);
        }}

        // 持仓独立管理弹窗交互
        function openPortfolioModal() {{
            const modal = document.getElementById("portfolioModalOverlay");
            if (modal) modal.style.display = "flex";
            renderPortfolioModalTable();
        }}

        function closePortfolioModal() {{
            const modal = document.getElementById("portfolioModalOverlay");
            if (modal) modal.style.display = "none";
        }}

        function renderPortfolioModalTable() {{
            const tbody = document.getElementById("portfolioTableBody");
            if (!tbody) return;
            tbody.innerHTML = "";
            
            const tickers = Object.keys(userPortfolio);
            if (tickers.length === 0) {{
                tickers.push("INTC", "SOXL");
            }}
            
            tickers.forEach(t => {{
                const p = userPortfolio[t] || {{}};
                const tr = document.createElement("tr");
                tr.dataset.ticker = t;
                tr.innerHTML = `
                    <td><input class="p-input inp-ticker" value="${{t}}" style="font-weight:700; text-transform:uppercase;" /></td>
                    <td><input class="p-input inp-name" value="${{p.name || t}}" /></td>
                    <td><input class="p-input inp-shares" type="number" step="any" value="${{p.shares || 0}}" /></td>
                    <td><input class="p-input inp-cost" type="number" step="any" value="${{p.cost || 0}}" /></td>
                    <td><input class="p-input inp-trim1" type="number" step="any" value="${{p.trim1 || 0}}" /></td>
                    <td><input class="p-input inp-trim1s" type="number" step="any" value="${{p.trim1_shares || 0}}" /></td>
                    <td><input class="p-input inp-stop" type="number" step="any" value="${{p.base_val || p.stop_val || 0}}" /></td>
                    <td><input class="p-input inp-target" type="number" step="any" value="${{p.target || p.cost || 0}}" /></td>
                    <td style="text-align:center;"><button class="btn-del" onclick="removePortfolioRow(this)">删除</button></td>
                `;
                tbody.appendChild(tr);
            }});
        }}

        function addPortfolioRow() {{
            const tbody = document.getElementById("portfolioTableBody");
            if (!tbody) return;
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><input class="p-input inp-ticker" placeholder="如NVDA" style="font-weight:700; text-transform:uppercase;" /></td>
                <td><input class="p-input inp-name" placeholder="名称" /></td>
                <td><input class="p-input inp-shares" type="number" step="any" value="1.0" /></td>
                <td><input class="p-input inp-cost" type="number" step="any" value="100.0" /></td>
                <td><input class="p-input inp-trim1" type="number" step="any" value="0.0" /></td>
                <td><input class="p-input inp-trim1s" type="number" step="any" value="0.0" /></td>
                <td><input class="p-input inp-stop" type="number" step="any" value="0.0" /></td>
                <td><input class="p-input inp-target" type="number" step="any" value="0.0" /></td>
                <td style="text-align:center;"><button class="btn-del" onclick="removePortfolioRow(this)">删除</button></td>
            `;
            tbody.appendChild(tr);
        }}

        function removePortfolioRow(btn) {{
            const tr = btn.closest("tr");
            if (tr) tr.remove();
        }}

        function savePortfolioModal() {{
            const rows = document.querySelectorAll("#portfolioTableBody tr");
            const newPort = {{}};
            rows.forEach(r => {{
                const t = r.querySelector(".inp-ticker").value.trim().toUpperCase();
                if (!t) return;
                const name = r.querySelector(".inp-name").value.trim() || t;
                const shares = parseFloat(r.querySelector(".inp-shares").value) || 0.0;
                const cost = parseFloat(r.querySelector(".inp-cost").value) || 0.0;
                const trim1 = parseFloat(r.querySelector(".inp-trim1").value) || 0.0;
                const trim1_s = parseFloat(r.querySelector(".inp-trim1s").value) || 0.0;
                const stop_val = parseFloat(r.querySelector(".inp-stop").value) || 0.0;
                const target = parseFloat(r.querySelector(".inp-target").value) || cost;
                
                newPort[t] = {{
                    name: name,
                    shares: shares,
                    cost: cost,
                    trim1: trim1,
                    trim1_shares: trim1_s,
                    base_val: stop_val,
                    stop_val: stop_val,
                    target: target,
                    note: "自主管理实盘持仓"
                }};
            }});

            userPortfolio = newPort;

            // 1. 同步提交至 8765 守护服务写入 user_portfolio.json
            fetch(`${{API_BASE}}/api/portfolio`, {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify(newPort)
            }})
            .then(r => r.json())
            .then(res => {{
                showToast("✅ 持仓已成功保存至 user_portfolio.json！");
            }})
            .catch(e => {{
                const rawParam = encodeURIComponent(JSON.stringify(newPort));
                fetch(`${{API_BASE}}/api/save_portfolio?data=${{rawParam}}`)
                    .then(() => showToast("✅ 持仓已成功保存至 user_portfolio.json！"))
                    .catch(() => showToast("✅ 本地持仓已更新！"));
            }});

            // 2. 实时热更新前端数据源（含清理已删除持仓），无需刷新页面
            syncPortfolioToRuntimeData(newPort);

            renderWatchlist(currentFilter);
            selectTicker(currentTicker);
            closePortfolioModal();
        }}

        // 预警通知中心抽屉控制与交互
        function toggleNotifDrawer() {{
            const drawer = document.getElementById("notifDrawerOverlay");
            if (!drawer) return;
            const isVisible = drawer.style.display === "flex";
            drawer.style.display = isVisible ? "none" : "flex";
            if (!isVisible) {{
                renderNotifList(currentNotifCat);
            }}
        }}

        function filterNotifCategory(cat) {{
            currentNotifCat = cat;
            document.querySelectorAll(".n-tab").forEach(tab => {{
                if (tab.dataset.cat === cat) {{
                    tab.classList.add("active");
                }} else {{
                    tab.classList.remove("active");
                }}
            }});
            renderNotifList(cat);
        }}

        function renderNotifList(cat = "all") {{
            const container = document.getElementById("notifListContainer");
            const badge = document.getElementById("notifBadge");
            const totalCountSpan = document.getElementById("notifTotalCount");
            
            // 更新各分类计数
            const counts = {{
                all: terminalNotifications.length,
                "5m_entry": 0,
                "portfolio": 0,
                "golden_pit": 0,
                "stop_loss": 0,
                "take_profit": 0,
                "macro_weather": 0
            }};

            terminalNotifications.forEach(n => {{
                if (counts.hasOwnProperty(n.category)) {{
                    counts[n.category]++;
                }}
            }});

            Object.keys(counts).forEach(k => {{
                const el = document.getElementById("catCount_" + k);
                if (el) el.innerText = counts[k];
            }});

            if (badge) {{
                badge.innerText = terminalNotifications.length;
                badge.style.display = terminalNotifications.length > 0 ? "inline-block" : "none";
            }}
            if (totalCountSpan) {{
                totalCountSpan.innerText = terminalNotifications.length + " 条";
            }}

            if (!container) return;

            // 过滤列表
            const filtered = terminalNotifications.filter(n => {{
                if (cat === "all") return true;
                return n.category === cat;
            }});

            if (filtered.length === 0) {{
                container.innerHTML = `
                    <div style="text-align: center; color: #6b7280; padding: 60px 0; font-size: 13px;">
                        <div style="font-size: 32px; margin-bottom: 8px;">📭</div>
                        当前分类暂无新预警消息
                    </div>
                `;
                return;
            }}

            let html = "";
            filtered.forEach(n => {{
                let levelClass = "level-info";
                if (n.level === "danger") levelClass = "level-danger";
                else if (n.level === "success") levelClass = "level-success";
                else if (n.level === "warning") levelClass = "level-warning";
                else if (n.level === "purple") levelClass = "level-purple";

                const clickAction = n.ticker ? `onclick="jumpToTicker('${{n.ticker}}')"` : "";

                html += `
                    <div class="notif-card ${{levelClass}}" ${{clickAction}}>
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div style="display: flex; align-items: center; gap: 6px;">
                                <span style="font-weight: 800; font-size: 13px; color: #f3f4f6;">${{n.title || ""}}</span>
                                ${{n.ticker ? `<span style="font-size: 10px; background: rgba(255,105,0,0.15); color: #ff6900; padding: 1px 5px; border-radius: 3px; font-weight: 700;">${{n.ticker}}</span>` : ""}}
                            </div>
                            <span style="font-size: 10px; color: #6b7280; font-family: monospace;">${{n.time || ""}}</span>
                        </div>
                        <div style="font-size: 12px; color: #cbd5e1; line-height: 1.45;">
                            ${{n.desc || ""}}
                        </div>
                    </div>
                `;
            }});

            container.innerHTML = html;
        }}

        function jumpToTicker(ticker) {{
            if (!ticker) return;
            selectTicker(ticker);
            toggleNotifDrawer();
            showToast("🎯 已为您自动跳转至标的：" + ticker);
        }}

        function clearAllNotifs() {{
            terminalNotifications = [];
            renderNotifList("all");
            fetch(`${{API_BASE}}/api/clear_notifications`)
                .then(() => showToast("🗑️ 已清空全部预警通知"))
                .catch(() => showToast("🗑️ 已清空预警通知"));
        }}

        // ==========================================
        // 🔊 量化预警声音引擎 (Web Audio API + 本地 WAV 双轨架构)
        // ==========================================
        let isSoundEnabled = true;
        try {{
            const s = localStorage.getItem("terminalSoundEnabled");
            if (s !== null) isSoundEnabled = (s === "true");
        }} catch(e) {{}}

        let audioCtx = null;
        function getAudioContext() {{
            if (!audioCtx) {{
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                if (AudioContext) {{
                    audioCtx = new AudioContext();
                }}
            }}
            if (audioCtx && audioCtx.state === 'suspended') {{
                audioCtx.resume();
            }}
            return audioCtx;
        }}

        // 首次点击任意位置解锁音频引擎 (规避浏览器 Autoplay 限制)
        document.addEventListener("click", function unlockAudio() {{
            getAudioContext();
        }}, {{ once: true }});

        function toggleSoundAlerts() {{
            isSoundEnabled = !isSoundEnabled;
            try {{
                localStorage.setItem("terminalSoundEnabled", isSoundEnabled ? "true" : "false");
            }} catch(e) {{}}
            updateSoundUI();
            showToast(isSoundEnabled ? "🔊 声音预警已开启" : "🔇 声音预警已静音");
            if (isSoundEnabled) {{
                getAudioContext();
                playAlertSound("macro_weather", true);
            }}
        }}

        function updateSoundUI() {{
            const icon = document.getElementById("soundIcon");
            const label = document.getElementById("drawerSoundLabel");
            const btn = document.getElementById("btnSoundToggle");
            if (icon) icon.innerText = isSoundEnabled ? "🔊" : "🔇";
            if (btn) {{
                btn.title = isSoundEnabled ? "声音提示：已开启 (点击切换静音)" : "声音提示：已静音 (点击开启)";
                btn.style.color = isSoundEnabled ? "#4ade80" : "#9ca3af";
            }}
            if (label) {{
                label.innerText = isSoundEnabled ? "🔊 声音预警已开启" : "🔇 声音预警已静音";
                label.style.color = isSoundEnabled ? "#4ade80" : "#ef4444";
            }}
        }}

        const SOUND_FILE_MAP = {{
            "5m_entry": `${{API_BASE}}/sounds/5m_entry.wav`,
            "portfolio": `${{API_BASE}}/sounds/risk_alarm.wav`,
            "golden_pit": `${{API_BASE}}/sounds/golden_pit.wav`,
            "stop_loss": `${{API_BASE}}/sounds/breakdown.wav`,
            "take_profit": `${{API_BASE}}/sounds/take_profit.wav`,
            "macro_weather": `${{API_BASE}}/sounds/weather.wav`
        }};

        function playAlertSound(category, forcePlay = false) {{
            if (!isSoundEnabled && !forcePlay) return;

            // 优先尝试本地 sounds/*.wav 播放，失败则无缝由 Web Audio 电子合成器兜底
            const filePath = SOUND_FILE_MAP[category] || "sounds/5m_entry.wav";
            try {{
                const audio = new Audio(filePath);
                audio.volume = 0.85;
                const p = audio.play();
                if (p !== undefined) {{
                    p.catch(() => synthAlertSound(category));
                }}
            }} catch(e) {{
                synthAlertSound(category);
            }}
        }}

        function synthAlertSound(category) {{
            const ctx = getAudioContext();
            if (!ctx) return;
            const now = ctx.currentTime;

            if (category === "5m_entry") {{
                // 🎯 5m买点: 两个上扬清脆音 (659Hz -> 880Hz)
                playTone(ctx, 659.25, now, 0.10, "sine", 0.35);
                playTone(ctx, 880.00, now + 0.11, 0.16, "triangle", 0.45);
            }} else if (category === "portfolio") {{
                // 💼 持仓风控: 双声紧迫警报 (988Hz, 784Hz)
                playTone(ctx, 987.77, now, 0.12, "sawtooth", 0.40);
                playTone(ctx, 783.99, now + 0.16, 0.18, "sawtooth", 0.45);
            }} else if (category === "golden_pit") {{
                // 🟢 黄金坑就位: 舒缓温润和弦 (523Hz + 659Hz)
                playTone(ctx, 523.25, now, 0.35, "sine", 0.30);
                playTone(ctx, 659.25, now + 0.05, 0.35, "sine", 0.25);
            }} else if (category === "stop_loss") {{
                // 🔴 破位预警: 低沉下坠音 (400Hz -> 180Hz)
                playSweep(ctx, 400, 180, now, 0.28, "sawtooth", 0.40);
            }} else if (category === "take_profit") {{
                // 🟣 止盈冲高: 金币/收银机 4连音快速上扬
                playTone(ctx, 587.33, now, 0.08, "sine", 0.30);
                playTone(ctx, 739.99, now + 0.08, 0.08, "sine", 0.35);
                playTone(ctx, 880.00, now + 0.16, 0.08, "triangle", 0.40);
                playTone(ctx, 1174.66, now + 0.24, 0.18, "triangle", 0.45);
            }} else {{
                // ⛅ 市场气象: 柔和马林巴双音
                playTone(ctx, 440.00, now, 0.14, "sine", 0.30);
                playTone(ctx, 554.37, now + 0.15, 0.22, "sine", 0.35);
            }}
        }}

        function playTone(ctx, freq, startTime, duration, type, maxGain) {{
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = type || "sine";
            osc.frequency.setValueAtTime(freq, startTime);

            gain.gain.setValueAtTime(0.0001, startTime);
            gain.gain.exponentialRampToValueAtTime(maxGain, startTime + 0.01);
            gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);

            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(startTime);
            osc.stop(startTime + duration + 0.05);
        }}

        function playSweep(ctx, startFreq, endFreq, startTime, duration, type, maxGain) {{
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = type || "sawtooth";
            osc.frequency.setValueAtTime(startFreq, startTime);
            osc.frequency.exponentialRampToValueAtTime(Math.max(10, endFreq), startTime + duration);

            gain.gain.setValueAtTime(0.0001, startTime);
            gain.gain.exponentialRampToValueAtTime(maxGain, startTime + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);

            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(startTime);
            osc.stop(startTime + duration + 0.05);
        }}

        // 已播放过的通知 ID 集合，避免重复响铃轰炸（跨刷新持久化）
        let knownNotifIds = new Set();
        try {{
            const savedIds = JSON.parse(localStorage.getItem("terminalKnownNotifIds") || "[]");
            if (Array.isArray(savedIds)) savedIds.forEach(id => knownNotifIds.add(id));
        }} catch(e) {{}}
        terminalNotifications.forEach(n => {{
            if (n && n.id) knownNotifIds.add(n.id);
        }});
        function persistKnownNotifIds() {{
            try {{
                const arr = Array.from(knownNotifIds).slice(-120);
                localStorage.setItem("terminalKnownNotifIds", JSON.stringify(arr));
            }} catch(e) {{}}
        }}
        persistKnownNotifIds();

        function handleIncomingNotifications(newNotifs) {{
            if (!Array.isArray(newNotifs)) return;
            let freshItems = [];
            newNotifs.forEach(n => {{
                if (n && n.id && !knownNotifIds.has(n.id)) {{
                    freshItems.push(n);
                    knownNotifIds.add(n.id);
                }}
            }});
            if (freshItems.length > 0) persistKnownNotifIds();

            terminalNotifications = newNotifs;
            renderNotifList(currentNotifCat);

            // 若有全新预警，按最高优先级播放对应提示音
            if (freshItems.length > 0) {{
                const priorityOrder = ["portfolio", "5m_entry", "stop_loss", "take_profit", "golden_pit", "macro_weather"];
                let topCat = "5m_entry";
                for (let p of priorityOrder) {{
                    if (freshItems.some(x => x.category === p)) {{
                        topCat = p;
                        break;
                    }}
                }}
                playAlertSound(topCat);
                showToast(`🔔 收到 ${{freshItems.length}} 条全新实盘量化预警！`);
            }}
        }}

        function pollLatestNotifications() {{
            fetch(`${{API_BASE}}/api/notifications`)
                .then(r => r.json())
                .then(res => {{
                    if (res && Array.isArray(res.notifications)) {{
                        handleIncomingNotifications(res.notifications);
                    }}
                }})
                .catch(() => {{}});
        }}

        // 页面启动执行
        window.addEventListener("DOMContentLoaded", function() {{
            renderWatchlist("all");
            let initTicker = "INTC";
            try {{
                const savedT = localStorage.getItem("lastSelectedTicker");
                if (savedT && (STOCKS_DATA.some(s => s.ticker === savedT) || MACRO_DATA[savedT])) {{
                    initTicker = savedT;
                }}
            }} catch(e) {{}}
            selectTicker(initTicker);
            renderNotifList("all");
            updateSoundUI();
            setInterval(pollLatestNotifications, 20000);

            // 📱 手机端初始视图自适应：默认展开行情自选列表
            if (window.innerWidth <= 768) {{
                switchMobileTab("watchlist");
            }}
        }});
    </script>
</body>
</html>
"""
    return html

if __name__ == "__main__":
    print("Futu Pro Terminal Generator Ready.")
