# -*- coding: utf-8 -*-
"""
Helper module to build the Futu-Style Pro Terminal (futu_pro_terminal.html)
支持 55 只全量标的池 (12 ETF + 43 个股)，全周期切换与分钟下拉菜单
"""
import json
import math
import time

EXCLUDED_LEADER_TICKERS = frozenset({
    "SQQQ", "SOXS", "VIXY", "TLT", "GLD", "XAUUSD", "BTC-USD", "SHY", "IEF", "HYG", "UUP"
})
EXCLUDED_LEADER_CATS = frozenset({
    "大宗商品", "数字资产", "避险美债", "恐慌指数", "反向做空"
})


def compute_resilient_leaders_top5(stocks_data, macro_data=None, session_code="REGULAR", top_n=5):
    """
    恐慌退潮·抗跌领涨真龙头筛选器 (Top 5):
    严格排除反向 ETF (SQQQ/SOXS/VIXY)、国债/黄金/加密等防御或非权益杂项，
    优先筛选站稳当日 VWAP 上方（非美东常规盘中回退为正涨幅或强于 SPY）的标的，
    结合 20 日超额相对强弱 (RS) 与相对 SPY 超额表现综合排序取前 5 名。
    """
    sess = str(session_code or "REGULAR").strip().upper() or "REGULAR"
    unified = []
    seen = set()
    for s in (stocks_data or []):
        if isinstance(s, dict) and s.get("ticker"):
            tk = str(s["ticker"]).upper()
            if tk not in seen:
                seen.add(tk)
                unified.append({**s, "ticker": tk})
    if isinstance(macro_data, dict):
        for tk_m, m in macro_data.items():
            if isinstance(m, dict):
                tk = str(tk_m).upper()
                if tk not in seen:
                    seen.add(tk)
                    unified.append({**m, "ticker": tk})

    def _eff_pct(item):
        if sess == "REGULAR":
            val = item.get("pct_change")
        else:
            ext_p = item.get("ext_price") or 0
            if ext_p and float(ext_p) > 0 and item.get("ext_pct") is not None:
                val = item.get("ext_pct")
            else:
                val = item.get("pct_change")
        try:
            fv = float(val if val is not None else 0.0)
            return fv if math.isfinite(fv) else 0.0
        except Exception:
            return 0.0

    spy_pct = 0.0
    for it in unified:
        if it["ticker"] == "SPY":
            spy_pct = _eff_pct(it)
            break

    ranked = []
    for it in unified:
        tk = it["ticker"]
        cat = str(it.get("cat") or "")
        if tk in EXCLUDED_LEADER_TICKERS or cat in EXCLUDED_LEADER_CATS:
            continue
        if bool(it.get("is_inverse_hedge")) or bool(it.get("is_crash_halt")) or bool(it.get("is_cooling_halt")) or bool(it.get("is_ipo_pending")):
            continue
        try:
            active_px = float(it.get("active_price") or it.get("last") or 0.0)
        except Exception:
            active_px = 0.0
        if active_px <= 0 or not math.isfinite(active_px):
            continue

        tk_pct = _eff_pct(it)
        excess_vs_spy = round(tk_pct - spy_pct, 2)
        try:
            rs_20d = float(it.get("rs_20d") if it.get("rs_20d") is not None else 0.0)
            if not math.isfinite(rs_20d):
                rs_20d = 0.0
        except Exception:
            rs_20d = 0.0
        try:
            vwap_val = float(it.get("vwap") or 0.0)
            if not math.isfinite(vwap_val):
                vwap_val = 0.0
        except Exception:
            vwap_val = 0.0

        if sess == "REGULAR":
            above_vwap = bool(vwap_val > 0 and active_px >= vwap_val)
            vwap_tag = "✓VWAP" if above_vwap else "VWAP下"
        else:
            above_vwap = bool((tk_pct > 0) or (excess_vs_spy > 0.3))
            if excess_vs_spy > 0.3:
                vwap_tag = "✓强于SPY"
            elif tk_pct > 0:
                vwap_tag = "✓正涨幅"
            else:
                vwap_tag = "偏弱"

        rs_c = max(-30.0, min(40.0, rs_20d))
        exc_c = max(-15.0, min(20.0, excess_vs_spy))
        score = round((25.0 if above_vwap else 0.0) + rs_c * 1.2 + exc_c * 2.0, 2)
        ranked.append({
            "ticker": tk,
            "name": str(it.get("name") or tk),
            "rs_20d": round(rs_20d, 2),
            "pct": round(tk_pct, 2),
            "excess_vs_spy": excess_vs_spy,
            "above_vwap": above_vwap,
            "vwap_tag": vwap_tag,
            "leader_score": score,
        })

    ranked.sort(
        key=lambda x: (1 if x["above_vwap"] else 0, x["leader_score"], x["rs_20d"], x["excess_vs_spy"]),
        reverse=True,
    )
    return ranked[:max(1, int(top_n))]


def build_futu_terminal_html(stocks_data, macro_data, weather_info, update_time_str="", has_realtime=False, target_watchlist=None, terminal_notifications=None, session_code="REGULAR", session_name="常规盘中", sync_side_effects=False, sync_dir=None):
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

    defensive_alert_banner_html = ""
    defensive_alert_badge_html = ""
    try:
        import importlib
        import ml_predictor_5y
        importlib.reload(ml_predictor_5y)
        live_lookup_init = {}
        for s in (stocks_data or []):
            if isinstance(s, dict):
                tk_name = s.get("ticker") or s.get("symbol")
                if tk_name:
                    live_lookup_init[tk_name] = s
        for tk_m, m_item in (macro_data or {}).items():
            live_lookup_init[tk_m] = m_item
        existing_flight = weather_info.get("defensive_flight") if isinstance(weather_info, dict) else None
        if (
            isinstance(existing_flight, dict)
            and existing_flight.get("session") == session_code
            and existing_flight.get("score_source")
        ):
            df_alert = existing_flight
        else:
            df_alert = ml_predictor_5y.compute_defensive_flight_alert(
                live_lookup=live_lookup_init,
                session_code=session_code,
            )
        if df_alert.get('is_alert'):
            defensive_alert_badge_html = f'<span class="tool-link" style="color: #f85149; border: 1px solid rgba(248,81,73,0.6); background: rgba(248,81,73,0.22); padding: 4px 8px; font-weight: 800; font-size: 11.5px;" title="机构避险因子异常走强，警惕盘中去杠杆踩踏">🚨 防御因子起飞 · 警惕机构盘中去杠杆踩踏</span>'
            defensive_alert_banner_html = f'<div id="deleveragingAlertBanner" onclick="playAlertSound(\'defensive_flight\', true)" title="点击试听/重播防御因子起飞警报音" style="background: linear-gradient(90deg, rgba(239, 68, 68, 0.28), rgba(185, 28, 28, 0.18)); border-bottom: 1px solid rgba(239, 68, 68, 0.6); color: #fca5a5; padding: 7px 14px; font-weight: 800; font-size: 13px; display: flex; align-items: center; justify-content: center; gap: 10px; box-shadow: 0 2px 8px rgba(239, 68, 68, 0.2); cursor: pointer; user-select: none;"><span>🚨 防御因子起飞 · 警惕机构盘中去杠杆踩踏</span><span style="font-size: 11px; font-weight: 600; color: #f87171; background: rgba(0,0,0,0.3); padding: 2px 8px; border-radius: 4px;">超额背离 +{df_alert.get("flight_score", 0.0):.1f}%</span><span style="font-size: 10.5px; color: #fca5a5; opacity: 0.85; margin-left: 6px;">🔊 警报响铃 (点击重播)</span></div>'
        if weather_info and isinstance(weather_info, dict):
            weather_info['defensive_flight'] = df_alert
    except Exception:
        pass

    breadth_alert_badge_html = ""
    breadth_alert_banner_html = ""
    breadth_info = {}
    try:
        import ml_predictor_5y
        live_lookup_b = {}
        for s in (stocks_data or []):
            if isinstance(s, dict):
                tk_name = s.get("ticker") or s.get("symbol")
                if tk_name:
                    live_lookup_b[tk_name] = s
        for tk_m, m_item in (macro_data or {}).items():
            live_lookup_b[tk_m] = m_item
        existing_b = weather_info.get("cross_industry_breadth") if isinstance(weather_info, dict) else None
        if isinstance(existing_b, dict) and existing_b.get("regime"):
            breadth_info = existing_b
        else:
            breadth_info = ml_predictor_5y.compute_cross_industry_breadth_alert(
                live_lookup=live_lookup_b,
                session_code=session_code,
            )
            if weather_info and isinstance(weather_info, dict):
                weather_info["cross_industry_breadth"] = breadth_info
        if breadth_info.get("is_systemic_selloff"):
            b_down = int(breadth_info.get("trad_down_cnt", 0))
            b_tot = int(breadth_info.get("trad_total", 0))
            b_trad = float(breadth_info.get("trad_mean_chg", 0.0))
            b_tech = float(breadth_info.get("tech_mean_chg", 0.0))
            breadth_alert_badge_html = f'<span class="tool-link" id="breadthStatusBadge" style="color: #fca5a5; border: 1px solid rgba(248,113,113,0.65); background: rgba(220,38,38,0.25); padding: 4px 8px; font-weight: 800; font-size: 11.5px;" title="传统龙头与科技同步大面积下跌">🚨 全市场系统性普跌预警</span>'
            breadth_alert_banner_html = f'<div id="systemicSelloffBanner" style="background: linear-gradient(90deg, rgba(220, 38, 38, 0.34), rgba(153, 27, 27, 0.22)); border-bottom: 1px solid rgba(248, 113, 113, 0.65); color: #fecaca; padding: 7px 14px; font-weight: 800; font-size: 12.5px; display: flex; align-items: center; justify-content: center; gap: 10px; flex-wrap: wrap;"><span>🚨 全市场系统性普跌预警 · 传统与科技泥沙俱下（常规支撑 S1/S2 易失效，切勿盲目抄底）</span><span style="font-size: 11px; font-weight: 700; color: #fca5a5; background: rgba(0,0,0,0.35); padding: 2px 8px; border-radius: 4px;">传统 {b_down}/{b_tot} 跌 {b_trad:+.2f}% · 科技 {b_tech:+.2f}%</span></div>'
        elif breadth_info.get("is_structural_rotation"):
            b_steady = int(breadth_info.get("trad_steady_cnt", 0))
            b_tot = int(breadth_info.get("trad_total", 0))
            b_trad = float(breadth_info.get("trad_mean_chg", 0.0))
            breadth_alert_badge_html = f'<span class="tool-link" id="breadthStatusBadge" style="color: #38bdf8; border: 1px solid rgba(56,189,248,0.5); background: rgba(56,189,248,0.14); padding: 4px 8px; font-weight: 800; font-size: 11.5px;" title="科技回调但传统龙头多数稳健/翻红">🔄 科技结构性轮动（传统板块抗跌 {b_steady}/{b_tot} · {b_trad:+.2f}%）</span>'
    except Exception:
        breadth_info = {}

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

    ai_predictions = {}
    try:
        import os
        import numpy as np
        sc_dir = os.path.dirname(os.path.abspath(__file__))
        ai_file = os.path.join(sc_dir, "data_cache", "latest_ai_predictions.json")
        if os.path.exists(ai_file):
            with open(ai_file, "r", encoding="utf-8") as f:
                ai_predictions = json.load(f)

            # 将当前轮询周期的 Finnhub / 盘前盘后实时价格、ATR、RSI 与宏观气象注入 AI 预测快照并同步重绘 predict_radar_5y.html
            live_lookup = {}
            for s in (stocks_data or []):
                if s.get("ticker"):
                    live_lookup[s["ticker"]] = s
            for tk_m, m_item in (macro_data or {}).items():
                live_lookup[tk_m] = m_item

            w_name = weather_info.get("weather", "⛅ 阴天防守") if weather_info else "⛅ 阴天防守"
            w_icon = weather_info.get("icon", "") if weather_info else ""
            w_status = f"{w_icon} {w_name}".strip() if w_icon and w_icon not in w_name else w_name
            is_storm = ("🌧️" in w_status) or ("暴雨" in w_status)

            all_preds = ai_predictions.get("all_predictions", [])
            # 实盘价格兜底：优先使用 ml_panel_5y.parquet 最新真实收盘价，杜绝任何硬编码假价格
            _panel_last_close = {}
            try:
                import pandas as _pd
                _pq_path = os.path.join(sc_dir, "data_cache", "ml_panel_5y.parquet")
                if os.path.exists(_pq_path):
                    _pdf = _pd.read_parquet(_pq_path, columns=["Date", "Ticker", "Close"])
                    _pdf["Date"] = _pd.to_datetime(_pdf["Date"])
                    _panel_last_close = _pdf.sort_values("Date").groupby("Ticker")["Close"].last().to_dict()
            except Exception:
                _panel_last_close = {}
            pure_etf_set = {"QQQ", "SPY", "DIA", "IWM", "SMH", "SOXX", "SOXL", "KORU", "IGV", "TLT", "GLD", "VIXY", "SQQQ", "SOXS"}
            offensive_lev_etfs = {"SOXL", "KORU"}
            _exempt_regime_set = {"SQQQ", "SOXS", "VIXY", "TLT", "GLD", "XAUUSD"}
            _active_regime = str(
                (weather_info.get("market_regime") if isinstance(weather_info, dict) else None)
                or (ai_predictions.get("macro_weather") or {}).get("market_regime")
                or ai_predictions.get("market_regime")
                or "NEUTRAL_CHOP"
            )
            _active_regime_cn = str(
                (weather_info.get("market_regime_cn") if isinstance(weather_info, dict) else None)
                or (ai_predictions.get("macro_weather") or {}).get("market_regime_cn")
                or ai_predictions.get("market_regime_cn")
                or ("🔴 空头防守体制" if _active_regime == "BEAR_DEFENSE" else ("🟢 多头顺风体制" if _active_regime == "BULL_TREND" else "🟡 震荡分化体制"))
            )
            _active_breadth_ma20 = float(
                (weather_info.get("breadth_ma20_pct") if isinstance(weather_info, dict) and weather_info.get("breadth_ma20_pct") is not None else None)
                or (ai_predictions.get("macro_weather") or {}).get("breadth_ma20_pct")
                or ai_predictions.get("breadth_ma20_pct")
                or 50.0
            )
            _active_vix_regime = (
                (weather_info.get("vix_regime") if isinstance(weather_info, dict) else None)
                or ai_predictions.get("vix_regime")
                or (ai_predictions.get("macro_weather") or {}).get("vix_regime")
                or {}
            )
            _is_bear_regime = (_active_regime == "BEAR_DEFENSE" or bool(isinstance(_active_vix_regime, dict) and _active_vix_regime.get("vix_hard_brake")))
            _scorecard_obj = ai_predictions.get("live_scorecard") or {}
            _sc_path = os.path.join(sc_dir, "data_cache", "live_prediction_scorecard.json")
            if os.path.exists(_sc_path):
                try:
                    with open(_sc_path, "r", encoding="utf-8") as _scf:
                        _loaded_sc = json.load(_scf)
                    if isinstance(_loaded_sc, dict) and _loaded_sc:
                        _scorecard_obj = _loaded_sc
                except Exception:
                    pass
            ai_predictions["market_regime"] = _active_regime
            ai_predictions["market_regime_cn"] = _active_regime_cn
            ai_predictions["breadth_ma20_pct"] = round(_active_breadth_ma20, 1)
            ai_predictions["vix_regime"] = _active_vix_regime
            ai_predictions["live_scorecard"] = _scorecard_obj
            if isinstance(ai_predictions.get("macro_weather"), dict):
                ai_predictions["macro_weather"]["market_regime"] = _active_regime
                ai_predictions["macro_weather"]["market_regime_cn"] = _active_regime_cn
                ai_predictions["macro_weather"]["breadth_ma20_pct"] = round(_active_breadth_ma20, 1)
                ai_predictions["macro_weather"]["vix_regime"] = _active_vix_regime

            if all_preds and live_lookup:
                for item in all_preds:
                    tk = str(item.get("ticker", ""))
                    is_etf = bool(item.get("is_etf", tk in pure_etf_set))
                    item["is_etf"] = is_etf

                    # 始终按当前标的初始化局部行情变量，彻底杜绝跨标的变量污染 (Bug Fix #3)
                    lv = live_lookup.get(tk, {})
                    if tk in live_lookup:
                        item["is_crash_halt"] = bool(lv.get("is_crash_halt", False))
                        item["is_cooling_halt"] = bool(lv.get("is_cooling_halt", False))
                        if lv.get("has_options"):
                            for _opt_k in ("has_options", "pcr", "dollar_pcr", "pcr_sentiment", "pcr_color", "top_call_info", "top_put_info", "call_wall", "put_wall"):
                                if lv.get(_opt_k) is not None:
                                    item[_opt_k] = lv[_opt_k]
                    is_crash_halt = bool(item.get("is_crash_halt", False))
                    is_cooling_halt = bool(item.get("is_cooling_halt", False))

                    _live_quote_px = float(lv.get("active_price") or lv.get("last") or 0.0) if lv else 0.0
                    if _live_quote_px <= 0:
                        _panel_px = float(_panel_last_close.get(tk, 0.0) or 0.0)
                        if _panel_px > 0:
                            _live_quote_px = _panel_px
                        else:
                            _live_quote_px = float(item.get("latest_price") or item.get("current_price") or 0.0)
                    live_p = float(_live_quote_px)
                    if live_p <= 0:
                        live_p = float(item.get("latest_price") or item.get("current_price") or 100.0)
                    item["latest_price"] = round(live_p, 2)
                    item["current_price"] = round(live_p, 2)

                    live_atr = float(lv.get("atr", 0.0))
                    atr_pct = round((live_atr / live_p) * 100.0, 2) if (live_atr > 0 and live_p > 0) else float(item.get("atr_pct", 2.5))
                    item["atr_pct"] = atr_pct
                    raw_atr = max(0.012, atr_pct / 100.0)
                    eff_atr = (0.040 + 0.50 * (min(raw_atr, 0.100) - 0.040)) if raw_atr > 0.040 else raw_atr
                    u_mult = max(0.030, 1.35 * eff_atr)
                    if tk.upper() in ("XAUUSD", "GLD", "TLT"):
                        l_mult = float(np.clip(0.50 * eff_atr, 0.008, 0.013))
                    else:
                        l_mult = max(0.022, 1.00 * eff_atr)
                    item["target_profit_price"] = round(live_p * (1.0 + u_mult), 2)
                    item["stop_loss_price"] = round(live_p * (1.0 - l_mult), 2)
                    item["target_profit_pct"] = round(u_mult * 100.0, 1)
                    item["stop_loss_pct"] = round(l_mult * 100.0, 1)

                    item["short_target_profit_price"] = round(live_p * (1.0 - u_mult), 2)
                    item["short_stop_loss_price"] = round(live_p * (1.0 + l_mult), 2)
                    item["short_target_profit_pct"] = round(u_mult * 100.0, 1)
                    item["short_stop_loss_pct"] = round(l_mult * 100.0, 1)

                    live_bias = float(lv.get("bias", item.get("bias_ma50", 0.0)))
                    live_rsi = float(lv.get("rsi", item.get("rsi_14", 50.0)))
                    item["bias_ma50"] = round(live_bias, 2)
                    item["rsi_14"] = round(live_rsi, 1)

                    model_raw = float(item.get("model_raw_prob_pct", item.get("base_raw_prob_pct", item.get("predicted_win_rate_pct", 50.0))))
                    item["model_raw_prob_pct"] = model_raw
                    pct_chg = float(lv.get("ext_pct") if lv.get("ext_price", 0) > 0 else lv.get("pct_change", 0.0))
                    intra_adj = float(np.clip(pct_chg * 0.12, -1.5, 1.5))

                    # A/B 双轨分类引擎 (Track A 趋势动量 vs Track B Short-Covering Sniper 空头回补滑头引擎)
                    live_vwap = float(lv.get('vwap', 0.0)) if (lv.get('vwap') and float(lv.get('vwap', 0.0)) > 0) else None
                    try:
                        import ml_predictor_5y
                        prior_low_val = float(item.get('low_prev') or lv.get('prev_low') or 0.0) or None
                        vwap_val = live_vwap
                        live_bias_20 = float(lv.get('bias_ma20') or item.get('bias_ma20') or (live_bias * 0.75))
                        live_atr_val = live_atr if live_atr > 0 else (live_p * (atr_pct / 100.0))
                        track_res = ml_predictor_5y.classify_dual_track_candidate(
                            bias_ma50=live_bias,
                            rsi_14=live_rsi,
                            bias_ma20=live_bias_20,
                            price=live_p,
                            vwap=vwap_val,
                            prior_low=prior_low_val,
                            atr=live_atr_val,
                            is_halt=bool(is_crash_halt or is_cooling_halt),
                            is_crash_halt=is_crash_halt,
                            is_cooling_halt=is_cooling_halt
                        )
                        is_short_covering = bool(track_res['is_short_covering'])
                        is_broken = bool(track_res['is_broken']) if (not is_etf or tk in offensive_lev_etfs) else False
                        item['is_short_covering'] = is_short_covering
                        item['is_broken'] = is_broken
                        item['track'] = track_res['track']
                    except Exception:
                        is_broken = (not is_etf or tk in offensive_lev_etfs) and (live_bias < -12.0)
                        is_exhaustion = (live_rsi < 30.0 or live_bias < -15.0) and (pct_chg > 0 or live_p > (lv.get('low', live_p) + 0.5 * live_atr))
                        is_short_covering = is_broken and is_exhaustion and (not is_crash_halt)
                        item['is_short_covering'] = is_short_covering
                        item['is_broken'] = is_broken and (not is_short_covering)
                        item['track'] = 'Track B' if (is_broken or is_short_covering) else 'Track A'

                    # 4H 多周期共振状态与校准加成更新
                    if live_rsi < 32.0 and live_bias < -10.0:
                        item["four_h_div"] = "🟢 4H 底背离拐点 (见底信号)"
                        item["four_h_tag"] = "div-bull"
                        fh_bonus = 3.0
                    elif live_rsi > 68.0 and live_bias > 10.0:
                        item["four_h_div"] = "🔴 4H 顶背离滞涨 (防回踩)"
                        item["four_h_tag"] = "div-bear"
                        fh_bonus = -3.0
                    elif live_bias >= -4.0 and live_rsi >= 45.0:
                        item["four_h_div"] = "⚡ 4H 均线多头共振"
                        item["four_h_tag"] = "div-trend"
                        fh_bonus = 1.2
                    else:
                        item["four_h_div"] = item.get("four_h_div") or "➖ 4H 均线震荡纠缠"
                        item["four_h_tag"] = item.get("four_h_tag") or "div-neutral"
                        fh_bonus = 0.0

                    if is_short_covering:
                        new_raw = round(float(np.clip(model_raw + intra_adj, 15.0, 85.0)), 1)
                        new_cal = round(ml_predictor_5y.compute_track_b_empirical_win_rate(
                            bias_ma50=live_bias,
                            rsi_14=live_rsi,
                            bias_ma20=live_bias_20,
                            model_raw_prob=new_raw
                        ) * 100.0, 1)
                    elif is_broken:
                        new_raw = round(float(np.clip(model_raw + intra_adj, 15.0, 85.0)), 1)
                        breakdown_mult = float(np.clip(0.82 - max(0.0, (-12.0 - live_bias) * 0.006), 0.68, 0.82))
                        new_cal = round(float(np.clip(new_raw * breakdown_mult, 15.0, 49.5)), 1)
                    else:
                        if -6.5 <= live_bias <= 4.0 and 35.0 <= live_rsi <= 62.0:
                            struct_boost = 8.5
                        elif -12.0 <= live_bias < -6.5 and 30.0 <= live_rsi <= 65.0:
                            struct_boost = 6.0
                        elif 4.0 < live_bias <= 12.0 and 35.0 <= live_rsi <= 68.0:
                            struct_boost = 5.0
                        else:
                            struct_boost = 1.5
                        new_raw = round(float(np.clip(model_raw + struct_boost + intra_adj, 15.0, 88.0)), 1)
                        sec_pf = float(item.get("sector_pf", 1.30))
                        mult = float(np.clip(sec_pf / 1.30, 0.75, 1.15))
                        new_cal = round(float(np.clip(new_raw * mult + fh_bonus, 15.0, 95.0)), 1)

                    # 阶段一规则 3：次新股与短样本 (<756日) 贝叶斯强收缩 + <500日置信上限
                    _n_hist_bars = item.get("history_bars")
                    if _n_hist_bars is not None:
                        try:
                            import ml_predictor_5y
                            _shrink_res = ml_predictor_5y.apply_short_history_bayesian_shrinkage(
                                new_cal / 100.0, int(_n_hist_bars)
                            )
                            if _shrink_res.get("is_short_sample"):
                                new_cal = round(float(_shrink_res["shrunk_pct"]), 1)
                                if is_broken:
                                    new_cal = min(new_cal, 49.5)
                                item["is_short_sample"] = True
                                item["sample_shrinkage_tag"] = str(_shrink_res.get("sample_shrinkage_tag", ""))
                                item["win_rate_ci_pct"] = [_shrink_res.get("ci_low", 50.0), _shrink_res.get("ci_high", 50.0)]
                        except Exception:
                            pass

                    # 阶段一 + 真 VIX 恐慌中枢联动门禁：
                    # 状态 A (倒挂硬刹车/空头防守) 封顶 <=48.0%；状态 B (恐慌出清抄底窗) 对站稳 VWAP 非熔断优质标的平滑放宽至 <=58.0%
                    _is_regime_capped = False
                    try:
                        import ml_predictor_5y as _ml_gate
                        _gate_res = _ml_gate.apply_market_regime_winrate_gate(
                            cal_prob_pct=new_cal,
                            ticker=tk,
                            market_regime=_active_regime,
                            vix_regime=_active_vix_regime,
                            is_crash_halt=is_crash_halt,
                            is_cooling_halt=is_cooling_halt,
                            price=live_p,
                            vwap=live_vwap,
                            is_broken=is_broken,
                        )
                        _is_regime_capped = bool(_gate_res["is_regime_capped"])
                        new_cal = float(_gate_res["gated_prob_pct"])
                        if _is_regime_capped and (not _gate_res["vix_bottom_relaxed"]) and is_short_covering:
                            is_short_covering = False
                            is_broken = True
                            item["is_short_covering"] = False
                            item["is_broken"] = True
                            item["track"] = "Track B"
                    except Exception:
                        if _is_bear_regime and tk not in _exempt_regime_set:
                            if new_cal > 40.0:
                                new_cal = round(40.0 + (new_cal - 40.0) * 0.25, 1)
                            new_cal = round(float(np.clip(new_cal, 15.0, 48.0)), 1)
                            if is_short_covering:
                                is_short_covering = False
                                is_broken = True
                                item["is_short_covering"] = False
                                item["is_broken"] = True
                                item["track"] = "Track B"
                            _is_regime_capped = True
                    item["market_regime"] = _active_regime
                    item["is_regime_capped"] = _is_regime_capped

                    item["base_raw_prob_pct"] = new_raw
                    item["predicted_win_rate_pct"] = new_raw
                    item["calibrated_win_rate_pct"] = new_cal
                    item["macro_temp"] = w_status

                    is_effective_halt = bool(is_crash_halt or (is_cooling_halt and not is_short_covering))
                    raw_prob_pct = float(item.get("predicted_win_rate_pct", 50.0))
                    cal_prob_pct = float(item.get("calibrated_win_rate_pct", 50.0))
                    eval_prob_pct = max(raw_prob_pct, cal_prob_pct)

                    if is_effective_halt:
                        item["action"] = "🚨 暴跌熔断·严禁左侧接刀"
                    elif is_short_covering:
                        item["action"] = "🔥 恐慌衰竭·博弈空头回补 (压力位不过就跑)"
                    elif is_broken:
                        item["action"] = "🚨 破位飞刀·严禁左侧抄底"
                    elif _is_regime_capped:
                        item["action"] = "🛡️ 暴雨空仓·严禁左侧接刀" if is_storm else "🛡️ 空头体制封顶·暂停常规做多"
                    elif is_storm:
                        if raw_prob_pct >= 50.0 and cal_prob_pct >= 55.0:
                            item["action"] = "🛡️ 暴雨防守·仅极小仓试错"
                        else:
                            item["action"] = "🛡️ 暴雨空仓·严禁左侧接刀"
                    elif raw_prob_pct < 50.0:
                        item["action"] = "👀 原始胜率未过半·观望"
                    elif eval_prob_pct >= 65.0:
                        item["action"] = "🔥 极高确定性右侧做多"
                    elif eval_prob_pct >= 60.0:
                        item["action"] = "⚡ 逢低吸筹"
                    elif eval_prob_pct >= 55.0:
                        item["action"] = "📈 温和看多·右侧试错"
                    else:
                        item["action"] = "👀 观望等待"

                    if tk.upper() in ("SPY", "IWM", "DIA") and "SQQQ" in str(item.get("short_action", "")):
                        item["short_action"] = "🛡️ 建议买入反向ETF对冲"

                    # 计算多级支撑压力与推演剧本 (与 is_effective_halt 严格对齐，彻底杜绝熔断与剧本冲突)
                    try:
                        import ml_predictor_5y
                        pw_val = float(item.get('put_wall', 0.0)) if item.get('put_wall') else None
                        cw_val = float(item.get('call_wall', 0.0)) if item.get('call_wall') else None
                        if pw_val and live_p > 0 and (pw_val / live_p >= 1.67 or live_p / pw_val >= 1.67):
                            pw_val = None
                        if cw_val and live_p > 0 and (cw_val / live_p >= 1.67 or live_p / cw_val >= 1.67):
                            cw_val = None
                        sc_map = ml_predictor_5y.compute_multi_level_scenario_map(
                            ticker=tk,
                            current_price=live_p,
                            atr_pct=atr_pct,
                            bias_ma50=live_bias,
                            rsi_14=live_rsi,
                            action=item["action"],
                            is_short_covering=is_short_covering,
                            is_broken=is_broken,
                            put_wall=pw_val,
                            call_wall=cw_val,
                            is_halt=is_effective_halt
                        )
                        item.update(sc_map)
                        near_stop_px = sc_map.get('near_stop_price') if sc_map.get('near_stop_s1') else None
                        if is_short_covering:
                            item['target_profit_price'] = sc_map['r1']
                            item['target_profit_pct'] = round(((sc_map['r1'] - live_p) / live_p) * 100.0, 1)
                            if near_stop_px and 0 < float(near_stop_px) < live_p:
                                item['stop_loss_price'] = round(float(near_stop_px), 2)
                                item['stop_loss_pct'] = round(((live_p - float(near_stop_px)) / live_p) * 100.0, 1)
                            else:
                                max_swing_sl = min(0.085, max(0.045, 1.35 * eff_atr))
                                sc_stop_px = round(max(float(sc_map['s2']), live_p * (1.0 - max_swing_sl)), 2)
                                item['stop_loss_price'] = sc_stop_px
                                item['stop_loss_pct'] = round(((live_p - sc_stop_px) / live_p) * 100.0, 1)
                        elif near_stop_px and 0 < float(near_stop_px) < live_p:
                            item['stop_loss_price'] = round(float(near_stop_px), 2)
                            item['stop_loss_pct'] = round(((live_p - float(near_stop_px)) / live_p) * 100.0, 1)
                    except Exception:
                        pass

                # 空头排序并分配 short_rank
                short_candidates_sorted = sorted(
                    all_preds,
                    key=lambda x: (
                        1 if (x.get("is_crash_halt") or x.get("is_cooling_halt")) else 0,
                        -float(x.get("calibrated_short_win_rate_pct", 0.0))
                    )
                )
                for idx_s, item in enumerate(short_candidates_sorted, 1):
                    item["short_rank"] = idx_s

                def _get_futu_pred_tier(x):
                    if x.get("is_crash_halt") or (x.get("is_cooling_halt") and not x.get("is_short_covering")):
                        return 3
                    if x.get("is_etf"):
                        return 2 if x.get("is_short_covering") else 4
                    if x.get("is_short_covering"):
                        return 1
                    if x.get("is_broken"):
                        return 3
                    if float(x.get("calibrated_win_rate_pct", 0.0)) >= 50.0 and float(x.get("predicted_win_rate_pct", 0.0)) >= 50.0:
                        return 0
                    return 2

                all_preds.sort(
                    key=lambda x: (
                        _get_futu_pred_tier(x),
                        -float(x.get("calibrated_win_rate_pct", 0.0)),
                        -float(x.get("predicted_win_rate_pct", 0.0))
                    )
                )
                for idx_rk, item in enumerate(all_preds, 1):
                    item["rank"] = idx_rk

                top10_stocks = [p for p in all_preds if not p.get("is_etf")][:10] or all_preds[:10]
                top_short_stocks = [p for p in short_candidates_sorted if not (p.get("is_crash_halt") or p.get("is_cooling_halt"))][:10] or short_candidates_sorted[:10]
                ai_predictions["all_predictions"] = all_preds
                ai_predictions["table4"] = top10_stocks
                ai_predictions["top10"] = top10_stocks
                ai_predictions["top_long"] = top10_stocks
                ai_predictions["top_short"] = top_short_stocks
                ai_predictions["by_ticker"] = {item["ticker"]: item for item in all_preds}

                if "kpi" in ai_predictions:
                    ai_predictions["kpi"]["sync_time"] = update_time_str
                    ai_predictions["kpi"]["data_mode"] = f"Finnhub 秒级实时 ({session_name})" if has_realtime else "yfinance 最新收盘价底模"
                if "macro_weather" in ai_predictions and weather_info:
                    ai_predictions["macro_weather"]["status"] = w_status
                    q_bias = round(float(weather_info.get("qqqm_bias", 0.0)), 2)
                    s_bias = round(float(weather_info.get("smh_bias", 0.0)), 2)
                    ai_predictions["macro_weather"]["qqqm_bias"] = q_bias
                    ai_predictions["macro_weather"]["smh_bias"] = s_bias
                    ai_predictions["macro_weather"]["qqqm_bias_ma50"] = q_bias
                    ai_predictions["macro_weather"]["smh_bias_ma50"] = s_bias
                    raw_vixy = weather_info.get("vixy_pos_60", 50.0)
                    v_pos = round(float(raw_vixy) / 100.0 if float(raw_vixy) > 1.5 else float(raw_vixy), 2)
                    ai_predictions["macro_weather"]["vixy_pos"] = v_pos
                    ai_predictions["macro_weather"]["vixy_pos_60"] = v_pos

                _tc_proj = ai_predictions.get("time_cycle_projections", {})
                if isinstance(_tc_proj, dict) and ai_predictions.get("by_ticker"):
                    import ml_predictor_5y as _ml_tc
                    _tc_live_prices = {}
                    for _tk_c, _b_item in ai_predictions["by_ticker"].items():
                        if isinstance(_b_item, dict):
                            _cp_v = float(_b_item.get("latest_price") or _b_item.get("current_price") or 0.0)
                            if _cp_v > 0:
                                _tc_live_prices[_tk_c] = round(_cp_v, 2)
                    _ml_tc.align_time_cycle_projections_with_live(
                        tc_dict=_tc_proj,
                        live_prices=_tc_live_prices,
                        by_ticker=ai_predictions["by_ticker"],
                        session_code=session_code,
                    )
                    for _b_item_sync in ai_predictions["by_ticker"].values():
                        if isinstance(_b_item_sync, dict):
                            _ml_tc.sync_scenario_script_levels(_b_item_sync)
                _def_flight_obj = (weather_info.get("defensive_flight") if isinstance(weather_info, dict) else None) or ai_predictions.get("defensive_flight", {})
                ai_predictions["defensive_flight"] = _def_flight_obj
                _breadth_obj = (weather_info.get("cross_industry_breadth") if isinstance(weather_info, dict) else None) or breadth_info or ai_predictions.get("cross_industry_breadth", {})
                ai_predictions["cross_industry_breadth"] = _breadth_obj
                ai_predictions["market_regime"] = _active_regime
                ai_predictions["market_regime_cn"] = _active_regime_cn
                ai_predictions["breadth_ma20_pct"] = round(_active_breadth_ma20, 1)
                ai_predictions["vix_regime"] = _active_vix_regime
                ai_predictions["live_scorecard"] = _scorecard_obj
                if isinstance(ai_predictions.get("macro_weather"), dict):
                    ai_predictions["macro_weather"]["cross_industry_breadth"] = _breadth_obj
                    ai_predictions["macro_weather"]["market_regime"] = _active_regime
                    ai_predictions["macro_weather"]["market_regime_cn"] = _active_regime_cn
                    ai_predictions["macro_weather"]["breadth_ma20_pct"] = round(_active_breadth_ma20, 1)
                    ai_predictions["macro_weather"]["vix_regime"] = _active_vix_regime

                if sync_dir:
                    _sync_dir = sync_dir
                elif not sync_side_effects:
                    import tempfile as _tf
                    _sync_dir = _tf.mkdtemp(prefix="futu_terminal_test_")
                else:
                    _sync_dir = sc_dir
                os.makedirs(_sync_dir, exist_ok=True)
                ai_file = os.path.join(_sync_dir, "data_cache", "latest_ai_predictions.json")
                os.makedirs(os.path.dirname(ai_file), exist_ok=True)
                with open(ai_file, "w", encoding="utf-8") as wf:
                    json.dump(ai_predictions, wf, ensure_ascii=False, indent=2)

                # Maintain real-time byte-for-byte dual-path synchronization
                try:
                    import shutil
                    if "us-market-radar" in _sync_dir:
                        sister_ai_file = os.path.abspath(os.path.join(_sync_dir, "..", "data_cache", "latest_ai_predictions.json"))
                    else:
                        sister_ai_file = os.path.abspath(os.path.join(_sync_dir, "us-market-radar", "data_cache", "latest_ai_predictions.json"))
                    os.makedirs(os.path.dirname(sister_ai_file), exist_ok=True)
                    shutil.copyfile(ai_file, sister_ai_file)
                except Exception:
                    pass

                try:
                    import ml_predictor_5y
                    _tables_for_html = {
                        "kpi": ai_predictions.get("kpi", {}),
                        "macro_weather": ai_predictions.get("macro_weather", {}),
                        "vix_regime": _active_vix_regime,
                        "defensive_flight": _def_flight_obj,
                        "cross_industry_breadth": _breadth_obj,
                        "live_scorecard": _scorecard_obj,
                        "table1": ai_predictions.get("table1", []),
                        "table2": ai_predictions.get("sectors", ai_predictions.get("table2", [])),
                        "table3": ai_predictions.get("table3", []),
                        "table4": top10_stocks,
                        "top_long": top10_stocks,
                        "top_short": top_short_stocks,
                        "all_live_predictions": all_preds,
                        "time_cycle_projections": _tc_proj
                    }
                    if _tables_for_html["table1"] and _tables_for_html["table2"]:
                        _pred_html_path = os.path.join(_sync_dir, "predict_radar_5y.html")
                        ml_predictor_5y.render_html_dashboard(_tables_for_html, output_path=_pred_html_path)
                        import subprocess
                        _git_stage = ["predict_radar_5y.html", "data_cache/latest_ai_predictions.json"]
                        if os.path.exists(os.path.join(_sync_dir, "data_cache", "live_prediction_scorecard.json")):
                            _git_stage.append("data_cache/live_prediction_scorecard.json")
                        subprocess.run(["git", "add"] + _git_stage, cwd=_sync_dir, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception as _re:
                    print(f"[!] 同步重绘 predict_radar_5y.html 提示: {_re}")
    except Exception:
        ai_predictions = {}

    market_news = {}
    macro_calendar_banner_html = ""
    try:
        import os as _os_mn
        import jin10_news_sentinel as _jn_news
        _news_base = sync_dir or _os_mn.path.dirname(_os_mn.path.abspath(__file__))
        _news_cache_p = _os_mn.path.join(_news_base, "data_cache", "latest_market_news.json")
        if not _os_mn.path.exists(_news_cache_p):
            _news_cache_p = _os_mn.path.join(_os_mn.path.dirname(_os_mn.path.abspath(__file__)), "data_cache", "latest_market_news.json")
        market_news = _jn_news.load_latest_market_news_cache(_news_cache_p)
        macro_calendar_banner_html = _jn_news.render_macro_calendar_banner_html(market_news, variant="terminal")
    except Exception:
        market_news = {"macro_calendar": [], "macro_flashes": [], "ticker_news": {}, "calendar_banner": {}}
        macro_calendar_banner_html = (
            '<!-- MACRO_CALENDAR_BANNER_START -->\n'
            '<div id="macroCalendarBanner" data-level="idle" style="background: rgba(15,23,42,0.92); '
            'border-bottom: 1px solid rgba(56,189,248,0.28); padding: 5px 14px; font-size: 12px; color: #7dd3fc;">'
            '📡 宏观消息面监控中 · 今日暂无美股五星级经济数据大考</div>\n'
            '<!-- MACRO_CALENDAR_BANNER_END -->'
        )

    intraday_path_forecasts = {}
    try:
        _ip_base = sync_dir or os.path.dirname(os.path.abspath(__file__))
        _ip_p = os.path.join(_ip_base, "data_cache", "intraday_path_forecasts.json")
        if not os.path.exists(_ip_p):
            _ip_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_cache", "intraday_path_forecasts.json")
        if os.path.exists(_ip_p):
            with open(_ip_p, "r", encoding="utf-8") as _ipf:
                intraday_path_forecasts = json.load(_ipf) or {}
    except Exception:
        intraday_path_forecasts = {}

    # 将标的数据转为 JSON 供前端动态渲染
    resilient_leaders_top5 = compute_resilient_leaders_top5(
        stocks_data=stocks_data,
        macro_data=macro_data,
        session_code=session_code,
        top_n=5,
    )
    stocks_json = json.dumps(stocks_data, ensure_ascii=False)
    macro_json = json.dumps(macro_data, ensure_ascii=False)
    weather_json = json.dumps(weather_info, ensure_ascii=False)
    watchlist_json = json.dumps(target_watchlist, ensure_ascii=False)
    notifications_json = json.dumps(terminal_notifications, ensure_ascii=False)
    ai_predictions_json = json.dumps(ai_predictions, ensure_ascii=False)
    market_news_json = json.dumps(market_news, ensure_ascii=False)
    intraday_path_json = json.dumps(intraday_path_forecasts, ensure_ascii=False, separators=(",", ":"))
    leaders_json = json.dumps(resilient_leaders_top5, ensure_ascii=False)

    html = r"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="90">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <script>
        (function() {
            try {
                var savedTheme = localStorage.getItem("futu_theme");
                var t = (savedTheme === "light") ? "light" : "dark";
                document.documentElement.setAttribute("data-theme", t);
            } catch (e) {
                document.documentElement.setAttribute("data-theme", "dark");
            }
        })();
        // ⚡ 客户端智能防缓存自动刷新 (彻底突破 GitHub Pages / Fastly CDN 10分钟 max-age=600 强缓存)
        setTimeout(function() {
            try {
                var cleanPath = window.location.pathname;
                var searchParams = new URLSearchParams(window.location.search);
                searchParams.delete('symbol');
                searchParams.delete('ticker');
                searchParams.set('_t', Date.now());
                var wl = document.getElementById("watchlistContainer");
                if (wl) {
                    localStorage.setItem("futu_watchlist_scroll", wl.scrollTop);
                }
                window.location.replace(cleanPath + '?' + searchParams.toString() + window.location.hash);
            } catch(e) {
                window.location.reload();
            }
        }, 60000);
    </script>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="量化雷达">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="theme-color" id="metaThemeColor" content="#0B0F19">
    <link rel="manifest" href="manifest.json">
    <title>美股量化看盘终端 · Pro Terminal (__PY_TPL_TOTAL_COUNT__只标的)</title>
    <style>
        :root,
        [data-theme="dark"] {
            --bg-page: #0B0F19;
            --bg-card: #121826;
            --bg-elevated: #1A2234;
            --bg-input: #0F1523;
            --bg-hover: rgba(255, 255, 255, 0.05);
            --bg-active: rgba(56, 189, 248, 0.12);
            --border-subtle: rgba(255, 255, 255, 0.08);
            --border-strong: rgba(255, 255, 255, 0.16);
            --text-primary: #F3F4F6;
            --text-secondary: #94A3B8;
            --text-muted: #64748B;
            --color-up: #10B981;
            --color-up-bg: rgba(16, 185, 129, 0.14);
            --color-up-border: rgba(16, 185, 129, 0.34);
            --color-down: #EF4444;
            --color-down-bg: rgba(239, 68, 68, 0.14);
            --color-down-border: rgba(239, 68, 68, 0.34);
            --color-warn: #F59E0B;
            --color-warn-bg: rgba(245, 158, 11, 0.14);
            --color-warn-border: rgba(245, 158, 11, 0.34);
            --color-accent: #38BDF8;
            --color-accent-bg: rgba(56, 189, 248, 0.14);
            --color-accent-border: rgba(56, 189, 248, 0.34);
            --color-purple: #A78BFA;
            --color-purple-bg: rgba(167, 139, 250, 0.14);
            --color-purple-border: rgba(167, 139, 250, 0.34);
            --shadow-card: 0 1px 2px rgba(0, 0, 0, 0.32);
            --shadow-float: 0 12px 32px rgba(0, 0, 0, 0.55);
            --backdrop-overlay: rgba(3, 7, 18, 0.68);
        }

        [data-theme="light"] {
            --bg-page: #F4F6F9;
            --bg-card: #FFFFFF;
            --bg-elevated: #F1F5F9;
            --bg-input: #F8FAFC;
            --bg-hover: rgba(15, 23, 42, 0.04);
            --bg-active: rgba(2, 132, 199, 0.09);
            --border-subtle: #E2E8F0;
            --border-strong: #CBD5E1;
            --text-primary: #0F172A;
            --text-secondary: #64748B;
            --text-muted: #94A3B8;
            --color-up: #059669;
            --color-up-bg: rgba(5, 150, 105, 0.10);
            --color-up-border: rgba(5, 150, 105, 0.28);
            --color-down: #DC2626;
            --color-down-bg: rgba(220, 38, 38, 0.10);
            --color-down-border: rgba(220, 38, 38, 0.28);
            --color-warn: #D97706;
            --color-warn-bg: rgba(217, 119, 6, 0.10);
            --color-warn-border: rgba(217, 119, 6, 0.28);
            --color-accent: #0284C7;
            --color-accent-bg: rgba(2, 132, 199, 0.10);
            --color-accent-border: rgba(2, 132, 199, 0.28);
            --color-purple: #7C3AED;
            --color-purple-bg: rgba(124, 58, 237, 0.10);
            --color-purple-border: rgba(124, 58, 237, 0.28);
            --shadow-card: 0 1px 3px rgba(15, 23, 42, 0.06);
            --shadow-float: 0 12px 32px rgba(15, 23, 42, 0.14);
            --backdrop-overlay: rgba(15, 23, 42, 0.40);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        html, body {
            max-width: 100%;
            overflow-x: hidden;
        }

        body {
            background-color: var(--bg-page);
            color: var(--text-primary);
            font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
            height: 100vh;
            height: 100dvh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            -webkit-font-smoothing: antialiased;
            transition: background-color 0.2s ease, color 0.2s ease;
        }

        .tabular {
            font-variant-numeric: tabular-nums;
            font-feature-settings: "tnum" 1;
        }

        /* =========================================================
         * 1. 顶部清爽导航栏 (3 秒看懂大盘红绿灯 + 快捷搜索 + 主题切换)
         * ========================================================= */
        .futu-header {
            background: var(--bg-card);
            border-bottom: 1px solid var(--border-subtle);
            min-height: 54px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            padding: 6px 16px;
            flex-shrink: 0;
            z-index: 120;
        }

        .header-left {
            display: flex;
            align-items: center;
            gap: 8px;
            min-width: 0;
            flex: 1;
            flex-wrap: wrap;
        }

        .futu-logo {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            font-size: 14px;
            font-weight: 800;
            color: var(--text-primary);
            letter-spacing: -0.01em;
            white-space: nowrap;
            flex-shrink: 0;
        }

        .brand-mark {
            width: 24px;
            height: 24px;
            border-radius: 7px;
            background: linear-gradient(135deg, #38BDF8, #6366F1);
            color: #FFFFFF;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 13px;
            box-shadow: 0 2px 6px rgba(56, 189, 248, 0.25);
        }

        .futu-logo .brand-count {
            background: var(--bg-elevated);
            color: var(--text-secondary);
            border: 1px solid var(--border-subtle);
            font-size: 11px;
            padding: 2px 7px;
            border-radius: 999px;
            font-weight: 600;
        }

        .regime-pill {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 5px 11px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 700;
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            color: var(--text-primary);
            white-space: nowrap;
            flex-shrink: 0;
        }

        .scorecard-pill {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 11.5px;
            font-weight: 700;
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            color: var(--text-secondary);
            white-space: nowrap;
            flex-shrink: 0;
        }

        .header-status-badges {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            flex-shrink: 0;
        }

        .header-status-badges .tool-link {
            border-radius: 999px !important;
            padding: 3px 9px !important;
            font-size: 11px !important;
        }

        [data-theme="light"] .header-status-badges .tool-link {
            color: var(--text-primary) !important;
            background: var(--bg-elevated) !important;
            border-color: var(--border-strong) !important;
        }

        [data-theme="light"] #deleveragingAlertBanner,
        [data-theme="light"] #systemicSelloffBanner {
            background: #FEF2F2 !important;
            color: #991B1B !important;
            border-bottom-color: #FCA5A5 !important;
            box-shadow: none !important;
        }

        [data-theme="light"] #deleveragingAlertBanner span,
        [data-theme="light"] #systemicSelloffBanner span {
            color: #991B1B !important;
        }

        .top-disclaimer-pill {
            font-size: 11px;
            color: var(--color-warn);
            background: var(--color-warn-bg);
            border: 1px solid var(--color-warn-border);
            padding: 3px 8px;
            border-radius: 999px;
            white-space: nowrap;
            flex-shrink: 0;
        }

        @media (max-width: 1480px) {
            .top-disclaimer-pill {
                display: none;
            }
        }

        .header-right {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-shrink: 0;
        }

        /* 快捷搜索框 */
        .quick-search-wrap {
            position: relative;
            width: 176px;
            flex-shrink: 0;
        }

        .quick-search-input {
            width: 100%;
            height: 32px;
            padding: 0 10px 0 11px;
            border-radius: 8px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-elevated);
            color: var(--text-primary);
            font-size: 12px;
            outline: none;
            transition: border-color 0.15s, background-color 0.15s;
        }

        .quick-search-input::placeholder {
            color: var(--text-muted);
        }

        .quick-search-input:focus {
            border-color: var(--color-accent);
            background: var(--bg-card);
        }

        .quick-search-dropdown {
            display: none;
            position: absolute;
            top: calc(100% + 6px);
            left: 0;
            right: 0;
            min-width: 240px;
            max-height: 320px;
            overflow-y: auto;
            background: var(--bg-card);
            border: 1px solid var(--border-strong);
            border-radius: 12px;
            box-shadow: var(--shadow-float);
            padding: 4px;
            z-index: 600;
        }

        .quick-search-dropdown.show {
            display: block;
        }

        .qs-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            padding: 8px 10px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 12px;
            transition: background-color 0.12s;
        }

        .qs-item:hover {
            background: var(--bg-elevated);
        }

        .qs-left {
            display: flex;
            align-items: center;
            gap: 6px;
            min-width: 0;
        }

        .qs-sym {
            font-weight: 700;
            color: var(--text-primary);
        }

        .qs-name {
            color: var(--text-secondary);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .qs-right {
            font-weight: 600;
            white-space: nowrap;
        }

        /* 顶栏紧凑导航入口 */
        .mobile-quick-nav {
            display: flex;
            align-items: center;
            gap: 5px;
            flex-shrink: 0;
        }

        .tool-link {
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 12px;
            font-weight: 600;
            padding: 5px 9px;
            border-radius: 8px;
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            transition: all 0.15s ease;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            white-space: nowrap;
            flex-shrink: 0;
            cursor: pointer;
        }

        .tool-link:hover {
            color: var(--text-primary);
            border-color: var(--border-strong);
            background: var(--bg-hover);
        }

        .tool-link.nav-accent {
            background: var(--color-purple-bg);
            border-color: var(--color-purple-border);
            color: var(--color-purple);
            font-weight: 700;
        }

        .header-alert-controls {
            display: flex;
            align-items: center;
            gap: 5px;
            flex-shrink: 0;
        }

        .btn-notif-pill {
            background: var(--color-down-bg);
            border: 1px solid var(--color-down-border);
            color: var(--color-down);
            font-weight: 700;
            padding: 5px 9px;
            border-radius: 8px;
            font-size: 12px;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            cursor: pointer;
            white-space: nowrap;
        }

        .notif-count-badge {
            background: var(--color-down);
            color: #FFFFFF;
            font-size: 10px;
            padding: 1px 6px;
            border-radius: 999px;
            font-weight: 800;
        }

        .theme-toggle-btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 5px;
            height: 30px;
            padding: 0 10px;
            border-radius: 8px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-elevated);
            color: var(--text-primary);
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
            white-space: nowrap;
            flex-shrink: 0;
        }

        .theme-toggle-btn:hover {
            border-color: var(--color-accent);
            background: var(--bg-active);
        }

        /* =========================================================
         * 2. 克制的单行宏观快讯条 (默认单行截断，点击可展开)
         * ========================================================= */
        .macro-strip-wrap {
            position: relative;
            background: var(--bg-card);
            border-bottom: 1px solid var(--border-subtle);
            flex-shrink: 0;
        }

        .macro-strip-wrap #macroCalendarBanner {
            background: transparent !important;
            border-bottom: none !important;
            padding: 5px 16px !important;
            min-height: 30px;
            font-size: 11.5px !important;
            color: var(--text-secondary) !important;
            cursor: pointer;
        }

        .macro-strip-wrap #macroCalendarBanner > div:first-child {
            flex-wrap: nowrap !important;
            overflow: hidden;
        }

        .macro-strip-wrap.expanded #macroCalendarBanner > div:first-child {
            flex-wrap: wrap !important;
        }

        .macro-strip-wrap #macroCalHeadline {
            color: var(--text-primary) !important;
            font-weight: 700 !important;
            white-space: nowrap;
            flex-shrink: 0;
        }

        .macro-strip-wrap #macroCalDetail {
            color: var(--text-secondary) !important;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            min-width: 0;
        }

        .macro-strip-wrap.expanded #macroCalDetail {
            white-space: normal;
            overflow: visible;
        }

        .macro-strip-wrap #macroCalendarBanner button {
            background: var(--color-accent-bg) !important;
            color: var(--color-accent) !important;
            border: 1px solid var(--color-accent-border) !important;
            border-radius: 6px !important;
            padding: 2px 8px !important;
            font-size: 11px !important;
        }

        [data-theme="light"] .macro-strip-wrap #macroCalendarBanner[data-level="countdown_15m"] {
            background: rgba(220, 38, 38, 0.06) !important;
        }

        [data-theme="light"] .macro-strip-wrap #macroCalendarBanner[data-level="countdown_15m"] #macroCalHeadline {
            color: #B91C1C !important;
        }

        [data-theme="light"] .macro-strip-wrap #macroCalendarBanner span:first-child {
            color: var(--color-accent) !important;
            background: var(--color-accent-bg) !important;
        }

        /* =========================================================
         * 3. 三栏工作台主体
         * ========================================================= */
        .futu-main {
            display: flex;
            flex: 1;
            min-height: 0;
            gap: 12px;
            padding: 12px;
            overflow: hidden;
            position: relative;
        }

        /* 左栏：小白化自选列表 */
        .sidebar-left {
            width: 288px;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            box-shadow: var(--shadow-card);
            display: flex;
            flex-direction: column;
            flex-shrink: 0;
            overflow: hidden;
            transition: width 0.2s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease;
        }

        .sidebar-left.collapsed {
            width: 0 !important;
            min-width: 0 !important;
            opacity: 0;
            border: none;
            margin-right: -12px;
        }

        .sidebar-toggle-btn {
            position: absolute;
            top: 24px;
            left: 300px;
            z-index: 90;
            width: 16px;
            height: 44px;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 0 8px 8px 0;
            color: var(--text-secondary);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            transition: all 0.2s ease;
            box-shadow: var(--shadow-card);
        }

        .sidebar-toggle-btn.btn-is-collapsed {
            left: 12px !important;
            border-radius: 8px;
        }

        .sidebar-toggle-btn:hover {
            background: var(--color-accent);
            color: #FFFFFF;
            border-color: var(--color-accent);
        }

        .wl-filter-header {
            padding: 10px 10px 8px;
            border-bottom: 1px solid var(--border-subtle);
            display: flex;
            flex-direction: column;
            gap: 8px;
            flex-shrink: 0;
        }

        .wl-primary-filters {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 6px;
        }

        .wl-pill-btn {
            padding: 7px 8px;
            border-radius: 8px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-elevated);
            color: var(--text-secondary);
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            text-align: center;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            transition: all 0.15s ease;
        }

        .wl-pill-btn:hover {
            color: var(--text-primary);
            border-color: var(--border-strong);
        }

        .wl-pill-btn.active {
            background: var(--bg-active);
            border-color: var(--color-accent-border);
            color: var(--color-accent);
            font-weight: 700;
        }

        .wl-secondary-bar {
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .wl-sector-select {
            flex: 1;
            min-width: 0;
            height: 30px;
            padding: 0 8px;
            border-radius: 8px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-input);
            color: var(--text-secondary);
            font-size: 11.5px;
            font-weight: 600;
            outline: none;
            cursor: pointer;
        }

        .wl-sector-select:focus {
            border-color: var(--color-accent);
            color: var(--text-primary);
        }

        .wl-more-btn {
            height: 30px;
            padding: 0 9px;
            border-radius: 8px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-elevated);
            color: var(--text-secondary);
            font-size: 11.5px;
            font-weight: 600;
            cursor: pointer;
            white-space: nowrap;
            flex-shrink: 0;
        }

        .wl-more-btn:hover,
        .wl-more-btn.active {
            color: var(--text-primary);
            border-color: var(--border-strong);
        }

        .wl-leader-bar {
            display: flex;
            align-items: center;
        }

        .wl-leader-btn {
            width: 100%;
            padding: 6px 10px;
            border-radius: 8px;
            border: 1px solid rgba(16, 185, 129, 0.35);
            background: rgba(16, 185, 129, 0.10);
            color: var(--color-up);
            font-size: 11.5px;
            font-weight: 700;
            cursor: pointer;
            text-align: center;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            transition: all 0.15s ease;
        }

        .wl-leader-btn:hover {
            border-color: var(--color-up);
            background: rgba(16, 185, 129, 0.18);
        }

        .wl-leader-btn.active {
            background: var(--color-up);
            color: #FFFFFF;
            border-color: var(--color-up);
        }

        .resilient-leaders-strip {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 6px 12px;
            width: 100%;
            max-width: 100%;
            overflow-x: auto;
            border-bottom: 1px solid var(--border-subtle);
            background: var(--bg-elevated);
            flex-shrink: 0;
        }

        .leaders-strip-lbl {
            font-size: 11px;
            font-weight: 800;
            color: var(--color-up);
            white-space: nowrap;
            flex-shrink: 0;
        }

        .leader-chip {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 3px 8px;
            border-radius: 999px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-card);
            color: var(--text-primary);
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            white-space: nowrap;
            flex-shrink: 0;
            transition: border-color 0.15s ease, background-color 0.15s ease;
        }

        .leader-chip:hover,
        .leader-chip.active {
            border-color: var(--color-up);
            background: var(--color-up-bg, rgba(16, 185, 129, 0.14));
        }

        .leader-chip .lc-tag {
            font-size: 10px;
            color: var(--color-up);
            font-weight: 700;
        }

        .stale-ref-tag {
            font-size: 9.5px;
            font-weight: 500;
            color: var(--text-muted);
            margin-left: 3px;
        }

        .price-trio-bear-note {
            margin-top: 6px;
            padding: 5px 8px;
            border-radius: 7px;
            font-size: 11px;
            font-weight: 600;
            color: var(--color-warn);
            background: var(--color-warn-bg);
            border: 1px solid var(--color-warn-border);
            line-height: 1.35;
        }

        .watchlist-categories {
            display: none;
            flex-wrap: wrap;
            gap: 5px;
            padding: 8px 10px;
            background: var(--bg-elevated);
            border-bottom: 1px solid var(--border-subtle);
            max-height: 150px;
            overflow-y: auto;
            flex-shrink: 0;
        }

        .watchlist-categories.is-open {
            display: flex;
        }

        .w-tab {
            padding: 4px 8px;
            font-size: 11px;
            font-weight: 600;
            color: var(--text-secondary);
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.15s;
            white-space: nowrap;
            user-select: none;
        }

        .w-tab:hover {
            color: var(--text-primary);
            border-color: var(--border-strong);
        }

        .w-tab.active {
            color: var(--color-accent) !important;
            border-color: var(--color-accent-border) !important;
            background: var(--bg-active) !important;
            font-weight: 700;
        }

        .watchlist-container {
            flex: 1;
            overflow-y: auto;
            padding: 6px;
            display: flex;
            flex-direction: column;
            gap: 3px;
        }

        .stock-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            padding: 9px 10px;
            border-radius: 10px;
            border: 1px solid transparent;
            cursor: pointer;
            transition: background-color 0.14s, border-color 0.14s;
        }

        .stock-item:hover {
            background: var(--bg-elevated);
        }

        .stock-item.active {
            background: var(--bg-active);
            border-color: var(--color-accent-border);
        }

        .s-info {
            display: flex;
            flex-direction: column;
            gap: 3px;
            min-width: 0;
            flex: 1;
        }

        .s-ticker {
            font-size: 13.5px;
            font-weight: 700;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 6px;
            min-width: 0;
        }

        .s-ticker-code {
            letter-spacing: -0.01em;
            white-space: nowrap;
        }

        .s-status-pill {
            font-size: 10px;
            font-weight: 700;
            padding: 2px 6px;
            border-radius: 999px;
            white-space: nowrap;
            flex-shrink: 0;
            line-height: 1.25;
        }

        .s-name {
            font-size: 11.5px;
            color: var(--text-secondary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .s-quotes {
            text-align: right;
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 2px;
            flex-shrink: 0;
        }

        .s-price {
            font-size: 13.5px;
            font-weight: 700;
            color: var(--text-primary);
        }

        .s-pct {
            font-size: 11px;
            font-weight: 700;
            padding: 1px 6px;
            border-radius: 5px;
        }

        .s-ext-note {
            font-size: 10px;
            color: var(--text-muted);
            white-space: nowrap;
        }

        /* =========================================================
         * 4. 中栏：圆角卡片主图舞台 + Segmented Control 双 Tab
         * ========================================================= */
        .chart-center {
            flex: 1;
            min-width: 0;
            min-height: 0;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            box-shadow: var(--shadow-card);
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        .chart-stage-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            padding: 10px 14px;
            border-bottom: 1px solid var(--border-subtle);
            flex-shrink: 0;
            flex-wrap: wrap;
        }

        .current-stock-badge {
            display: flex;
            align-items: baseline;
            gap: 8px;
            min-width: 0;
            flex-wrap: wrap;
        }

        .cur-sym {
            font-size: 17px;
            font-weight: 800;
            color: var(--text-primary);
            letter-spacing: -0.02em;
        }

        .cur-name {
            font-size: 12.5px;
            color: var(--text-secondary);
            font-weight: 500;
        }

        .cur-price {
            font-size: 17px;
            font-weight: 800;
            color: var(--text-primary);
        }

        .cur-change {
            font-size: 12px;
            font-weight: 700;
            padding: 2px 7px;
            border-radius: 6px;
        }

        .cur-ext-badge,
        .cur-earn-badge {
            font-size: 11px;
            font-weight: 600;
            padding: 2px 7px;
            border-radius: 6px;
            white-space: nowrap;
        }

        /* 现代分段控件 (Segmented Control) */
        .center-view-tabs {
            display: inline-flex;
            align-items: center;
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            padding: 3px;
            border-radius: 10px;
            gap: 3px;
            flex-shrink: 0;
        }

        .cv-tab {
            padding: 5px 12px;
            border-radius: 7px;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-secondary);
            cursor: pointer;
            white-space: nowrap;
            user-select: none;
            transition: all 0.15s ease;
        }

        .cv-tab:hover {
            color: var(--text-primary);
        }

        .cv-tab.active {
            background: var(--bg-card);
            color: var(--color-accent);
            font-weight: 700;
            box-shadow: var(--shadow-card);
        }

        .chart-period-strip {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            padding: 0 14px;
            height: 38px;
            border-bottom: 1px solid var(--border-subtle);
            background: var(--bg-card);
            flex-shrink: 0;
        }

        .period-bar {
            display: flex;
            align-items: center;
            gap: 4px;
            height: 100%;
        }

        .period-tab {
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-secondary);
            cursor: pointer;
            white-space: nowrap;
            transition: all 0.15s ease;
        }

        .period-tab:hover {
            color: var(--text-primary);
            background: var(--bg-elevated);
        }

        .period-tab.active {
            color: var(--color-accent);
            background: var(--bg-active);
            font-weight: 700;
        }

        .dropdown-wrapper {
            position: relative;
            display: flex;
            align-items: center;
        }

        .period-dropdown-btn {
            display: flex;
            align-items: center;
            gap: 4px;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-secondary);
            cursor: pointer;
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
        }

        .period-dropdown-btn.active,
        .period-dropdown-btn:hover {
            color: var(--color-accent);
            border-color: var(--color-accent-border);
        }

        .dropdown-menu {
            display: none;
            position: absolute;
            top: calc(100% + 6px);
            left: 0;
            width: 136px;
            background: var(--bg-card);
            border: 1px solid var(--border-strong);
            border-radius: 10px;
            box-shadow: var(--shadow-float);
            padding: 5px;
            z-index: 500;
        }

        .dropdown-menu.show {
            display: block;
        }

        .dropdown-item {
            padding: 7px 10px;
            font-size: 12px;
            border-radius: 6px;
            color: var(--text-secondary);
            cursor: pointer;
        }

        .dropdown-item:hover {
            background: var(--bg-elevated);
            color: var(--text-primary);
        }

        .dropdown-item.active {
            color: var(--color-accent);
            background: var(--bg-active);
            font-weight: 700;
        }

        .dropdown-divider {
            height: 1px;
            background: var(--border-subtle);
            margin: 4px 0;
        }

        .center-view-meta {
            font-size: 11px;
            color: var(--text-secondary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 44%;
        }

        .tv-container {
            width: 100%;
            flex: 1;
            min-height: 0;
            position: relative;
        }

        .intraday-path-panel {
            flex: 1;
            min-height: 0;
            display: none;
            flex-direction: column;
            padding: 14px;
            gap: 10px;
            background: var(--bg-page);
            overflow-y: auto;
        }

        .chart-center.is-intraday-path .tv-container,
        .chart-center.is-intraday-path .chart-period-strip,
        .chart-center.is-intraday-path .mobile-period-bar {
            display: none !important;
        }

        .chart-center.is-intraday-path .intraday-path-panel {
            display: flex !important;
        }

        .ip-header-strip {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 8px 12px;
            padding: 12px 14px;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            box-shadow: var(--shadow-card);
        }

        .ip-summary-block {
            display: flex;
            flex-direction: column;
            gap: 4px;
            min-width: 200px;
            flex: 1;
        }

        .ip-summary-text {
            font-size: 13px;
            font-weight: 700;
            color: var(--text-primary);
            line-height: 1.45;
        }

        .ip-meta-tag {
            font-size: 11px;
            color: var(--text-secondary);
        }

        .ip-div-badge {
            font-size: 11.5px;
            font-weight: 700;
            padding: 5px 10px;
            border-radius: 8px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-elevated);
            color: var(--text-primary);
            white-space: nowrap;
        }

        .ip-levels-bar {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 8px 14px;
            font-size: 11.5px;
            color: var(--text-secondary);
            padding: 8px 12px;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
        }

        .ip-chart-wrap {
            position: relative;
            flex: 1;
            min-height: 290px;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            padding: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: var(--shadow-card);
        }

        #ipChartSvg {
            width: 100%;
            height: 100%;
            max-height: 500px;
            display: block;
        }

        .ip-tooltip {
            display: none;
            position: absolute;
            top: 12px;
            left: 14px;
            pointer-events: none;
            background: var(--bg-card);
            border: 1px solid var(--color-accent);
            border-radius: 8px;
            padding: 8px 11px;
            font-size: 11.5px;
            color: var(--text-primary);
            line-height: 1.5;
            z-index: 20;
            box-shadow: var(--shadow-float);
        }

        .ip-fallback-box {
            display: none;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            padding: 36px 20px;
            background: var(--bg-card);
            border: 1px dashed var(--border-strong);
            border-radius: 12px;
            color: var(--text-secondary);
            gap: 8px;
            min-height: 240px;
        }

        /* =========================================================
         * 5. 右栏：小白 3 秒极简决策卡 + 默认折叠专业量化抽屉
         * ========================================================= */
        .sidebar-right {
            width: 386px;
            display: flex;
            flex-direction: column;
            gap: 10px;
            overflow-y: auto;
            flex-shrink: 0;
            min-height: 0;
            padding-right: 2px;
        }

        .decision-card {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            padding: 14px;
            box-shadow: var(--shadow-card);
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .verdict-hero {
            padding: 12px 14px;
            border-radius: 10px;
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .verdict-eyebrow {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            font-size: 11px;
            font-weight: 600;
            color: var(--text-secondary);
        }

        .verdict-title {
            font-size: 16px;
            font-weight: 800;
            line-height: 1.35;
            color: var(--text-primary);
            letter-spacing: -0.01em;
        }

        .verdict-sub {
            font-size: 12px;
            line-height: 1.5;
            color: var(--text-secondary);
        }

        .verdict-chips-row {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 6px;
            margin-top: 2px;
        }

        .verdict-chip {
            font-size: 10.5px;
            font-weight: 700;
            padding: 2px 8px;
            border-radius: 999px;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            color: var(--text-secondary);
        }

        /* 三个直白价格格 */
        .price-trio-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 8px;
        }

        .price-tile {
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            padding: 10px 9px;
            display: flex;
            flex-direction: column;
            gap: 4px;
            min-width: 0;
        }

        .price-tile-lbl {
            font-size: 11px;
            font-weight: 600;
            color: var(--text-secondary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .price-tile-val {
            font-size: clamp(14px, 1.2vw, 17px);
            font-weight: 800;
            color: var(--text-primary);
            line-height: 1.2;
            word-break: break-word;
        }

        .price-tile-val.up { color: var(--color-up); }
        .price-tile-val.down { color: var(--color-down); }
        .price-tile-val.warn { color: var(--color-warn); }

        .price-tile-sub {
            font-size: 10.5px;
            color: var(--text-secondary);
            line-height: 1.3;
            word-break: break-word;
        }

        /* 多空趋势温度条 */
        .trend-thermo-card {
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            padding: 10px 12px;
            display: flex;
            flex-direction: column;
            gap: 7px;
        }

        .thermo-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 11.5px;
            color: var(--text-secondary);
            font-weight: 600;
        }

        .thermo-nums {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        }

        .thermo-side {
            display: flex;
            align-items: baseline;
            gap: 5px;
        }

        .thermo-lbl {
            font-size: 11.5px;
            color: var(--text-secondary);
            font-weight: 600;
        }

        .thermo-val {
            font-size: 16px;
            font-weight: 800;
        }

        .thermo-val.up { color: var(--color-up); }
        .thermo-val.down { color: var(--color-down); }

        .thermo-track {
            height: 8px;
            width: 100%;
            background: var(--bg-card);
            border-radius: 999px;
            overflow: hidden;
            display: flex;
        }

        .thermo-fill-up {
            height: 100%;
            width: 50%;
            background: var(--color-up);
            transition: width 0.28s ease;
        }

        .thermo-divider {
            width: 2px;
            height: 100%;
            background: var(--bg-card);
            flex-shrink: 0;
        }

        .thermo-fill-down {
            height: 100%;
            width: 50%;
            background: var(--color-down);
            transition: width 0.28s ease;
        }

        /* 展开专业量化数据按钮与折叠抽屉 */
        .pro-drawer-toggle {
            width: 100%;
            padding: 10px 14px;
            border-radius: 12px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-card);
            color: var(--text-primary);
            font-size: 12.5px;
            font-weight: 700;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            box-shadow: var(--shadow-card);
            transition: all 0.15s ease;
        }

        .pro-drawer-toggle:hover {
            border-color: var(--color-accent-border);
            color: var(--color-accent);
            background: var(--bg-elevated);
        }

        .pro-quant-drawer {
            display: none;
            flex-direction: column;
            gap: 10px;
        }

        .pro-quant-drawer.is-open {
            display: flex;
        }

        .panel-box {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            padding: 12px 14px;
            box-shadow: var(--shadow-card);
            display: flex;
            flex-direction: column;
            gap: 10px;
        }

        .panel-box.collapsed .panel-box-content {
            display: none !important;
        }

        .box-title {
            font-size: 12px;
            font-weight: 700;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        }

        .card-toggle-btn {
            font-size: 10px;
            color: var(--text-secondary);
            cursor: pointer;
            padding: 2px 7px;
            border-radius: 5px;
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            user-select: none;
        }

        .metrics-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px 12px;
            font-size: 11.5px;
        }

        .m-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 6px;
            padding: 6px 8px;
            border-radius: 8px;
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
        }

        .m-lbl { color: var(--text-secondary); white-space: nowrap; }
        .m-val { font-weight: 700; color: var(--text-primary); text-align: right; }

        .pro-sub-banner {
            padding: 8px 10px;
            border-radius: 8px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-elevated);
            font-size: 11.5px;
            line-height: 1.45;
        }

        .pro-targets-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 7px;
            font-size: 11.5px;
        }

        .pro-target-cell {
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            border-radius: 8px;
            padding: 7px 9px;
        }

        .pro-target-lbl {
            font-size: 10.5px;
            color: var(--text-secondary);
            margin-bottom: 2px;
        }

        .pro-scenario-box {
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            border-radius: 8px;
            padding: 9px 10px;
            font-size: 11.5px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .t-news-item {
            display: block;
            text-decoration: none;
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            border-radius: 8px;
            padding: 8px 10px;
            transition: border-color 0.15s ease;
        }

        .t-news-item:hover {
            border-color: var(--color-accent-border);
        }

        .t-news-title {
            color: var(--text-primary);
            font-weight: 600;
            line-height: 1.4;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .t-news-meta {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: 4px;
            font-size: 10.5px;
            color: var(--text-secondary);
        }

        .flash-kw-pill {
            display: inline-block;
            font-size: 10px;
            padding: 1px 6px;
            border-radius: 4px;
            background: var(--color-accent-bg);
            color: var(--color-accent);
            border: 1px solid var(--color-accent-border);
            font-weight: 700;
            margin-right: 4px;
        }

        /* =========================================================
         * 6. 底部栏与全站免责声明
         * ========================================================= */
        .futu-footer {
            background: var(--bg-card);
            border-top: 1px solid var(--border-subtle);
            display: flex;
            flex-direction: column;
            gap: 4px;
            padding: 6px 16px;
            font-size: 11px;
            color: var(--text-secondary);
            flex-shrink: 0;
        }

        .footer-meta-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            width: 100%;
            gap: 12px;
        }

        .indicator-chips {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }

        .chip {
            padding: 2px 7px;
            border-radius: 5px;
            background: var(--bg-elevated);
            color: var(--text-secondary);
            font-weight: 600;
        }

        .site-disclaimer-banner {
            width: 100%;
            background: var(--color-warn-bg);
            border: 1px solid var(--color-warn-border);
            border-radius: 6px;
            padding: 3px 10px;
            font-size: 11px;
            line-height: 1.35;
            color: var(--text-secondary);
            text-align: center;
        }

        .site-disclaimer-banner strong {
            color: var(--color-warn);
        }

        /* =========================================================
         * 7. 预警通知抽屉 & Toast
         * ========================================================= */
        .toast-box {
            position: fixed;
            bottom: 68px;
            left: 50%;
            transform: translateX(-50%);
            background: var(--color-up);
            color: #FFFFFF;
            padding: 9px 18px;
            border-radius: 999px;
            font-size: 12.5px;
            font-weight: 700;
            box-shadow: var(--shadow-float);
            z-index: 10000;
            display: none;
            white-space: nowrap;
        }

        .notif-drawer-overlay {
            position: fixed;
            inset: 0;
            background: var(--backdrop-overlay);
            backdrop-filter: blur(3px);
            z-index: 9998;
            display: none;
            justify-content: flex-end;
        }

        .notif-drawer {
            width: 460px;
            max-width: 100vw;
            height: 100vh;
            background: var(--bg-card);
            border-left: 1px solid var(--border-subtle);
            display: flex;
            flex-direction: column;
            box-shadow: var(--shadow-float);
        }

        .notif-drawer-header {
            padding: 12px 16px;
            background: var(--bg-card);
            border-bottom: 1px solid var(--border-subtle);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .notif-sound-bar {
            background: var(--bg-elevated);
            border-bottom: 1px solid var(--border-subtle);
            padding: 7px 12px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 11px;
            gap: 6px;
            flex-wrap: wrap;
        }

        .notif-category-wrap {
            display: flex;
            align-items: center;
            background: var(--bg-elevated);
            border-bottom: 1px solid var(--border-subtle);
            padding: 0 4px;
            gap: 2px;
        }

        .notif-scroll-btn {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            color: var(--text-secondary);
            width: 24px;
            height: 24px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 800;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }

        .notif-scroll-btn:hover {
            background: var(--color-accent);
            color: #FFFFFF;
            border-color: var(--color-accent);
        }

        .notif-category-bar {
            flex: 1;
            min-width: 0;
            display: flex;
            align-items: center;
            padding: 6px 4px;
            gap: 4px;
            overflow-x: auto;
            scrollbar-width: none;
            cursor: grab;
        }

        .notif-category-bar::-webkit-scrollbar { display: none; }
        .notif-category-bar.is-dragging,
        .notif-category-bar.is-dragging .n-tab { cursor: grabbing !important; }

        .n-tab {
            padding: 4px 8px;
            font-size: 11px;
            font-weight: 700;
            color: var(--text-secondary);
            background: transparent;
            border: 1px solid transparent;
            border-radius: 6px;
            cursor: grab;
            white-space: nowrap;
            flex-shrink: 0;
        }

        .n-tab:hover {
            color: var(--text-primary);
            background: var(--bg-card);
        }

        .n-tab.active {
            color: var(--color-accent);
            background: var(--bg-active);
            border-color: var(--color-accent-border);
        }

        .notif-drawer-body {
            flex: 1;
            overflow-y: auto;
            padding: 12px;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }

        .notif-card {
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            padding: 10px 12px;
            cursor: pointer;
            display: flex;
            flex-direction: column;
            gap: 5px;
            transition: border-color 0.15s ease;
        }

        .notif-card:hover {
            border-color: var(--color-accent-border);
        }

        .notif-card.level-success { border-left: 4px solid var(--color-up); }
        .notif-card.level-danger { border-left: 4px solid var(--color-down); }
        .notif-card.level-warning { border-left: 4px solid var(--color-warn); }
        .notif-card.level-purple { border-left: 4px solid var(--color-purple); }
        .notif-card.level-info { border-left: 4px solid var(--color-accent); }

        .notif-drawer-footer {
            padding: 10px 14px;
            background: var(--bg-card);
            border-top: 1px solid var(--border-subtle);
            font-size: 11px;
            color: var(--text-secondary);
            text-align: center;
        }

        .notif-btn-clear {
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            color: var(--text-secondary);
            padding: 3px 9px;
            border-radius: 6px;
            font-size: 11px;
            cursor: pointer;
        }

        .notif-btn-close {
            background: none;
            border: none;
            color: var(--text-secondary);
            font-size: 16px;
            cursor: pointer;
            padding: 2px 6px;
        }

        .sound-test-chip {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            color: var(--text-secondary);
            padding: 2px 6px;
            border-radius: 5px;
            font-size: 10px;
            cursor: pointer;
        }

        .sound-test-chip:hover {
            border-color: var(--color-accent);
            color: var(--color-accent);
        }

        /* 手机端专属底部栏 / 周期条 */
        .mobile-bottom-nav,
        .mobile-period-bar,
        .mobile-only {
            display: none;
        }

        /* =========================================================
         * 8. 移动端适配 (<= 768px)：3 栏底部切换 + 零横向溢出
         * ========================================================= */
        @media (max-width: 768px) {
            body {
                padding-bottom: 56px;
            }

            .futu-header {
                height: auto !important;
                padding: 8px 10px;
                gap: 6px;
                flex-wrap: wrap;
            }

            .header-left {
                order: 1;
                flex: 1 1 auto;
                min-width: 0;
                gap: 6px;
            }

            .futu-logo .brand-text,
            .futu-logo .brand-count,
            .header-status-badges,
            .top-disclaimer-pill,
            .theme-toggle-text {
                display: none !important;
            }

            .regime-pill {
                font-size: 11px;
                padding: 4px 8px;
                max-width: calc(100% - 34px);
                overflow: hidden;
                text-overflow: ellipsis;
            }

            .header-right {
                order: 2;
                display: contents !important;
            }

            .header-alert-controls {
                order: 2;
                flex-shrink: 0;
            }

            .btn-notif-pill {
                padding: 4px 7px;
                font-size: 11px;
            }

            .btn-notif-pill .notif-btn-label {
                display: none;
            }

            .theme-toggle-btn {
                order: 2;
                height: 28px;
                padding: 0 8px;
            }

            .scorecard-pill {
                order: 3;
                font-size: 10.5px;
                padding: 3px 8px;
            }

            .quick-search-wrap {
                order: 3;
                flex: 1;
                min-width: 120px;
                width: auto;
            }

            .quick-search-input {
                height: 28px;
                font-size: 11.5px;
            }

            .mobile-quick-nav {
                order: 4;
                width: 100%;
                flex: 0 0 100%;
                display: grid !important;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 5px;
                padding-top: 6px;
                border-top: 1px solid var(--border-subtle);
            }

            .mobile-quick-nav .tool-link {
                justify-content: center;
                padding: 5px 3px !important;
                font-size: 11px !important;
                border-radius: 7px;
            }

            .macro-strip-wrap #macroCalendarBanner {
                padding: 4px 10px !important;
                font-size: 11px !important;
            }

            .futu-main {
                padding: 8px;
                gap: 0;
                flex-direction: column;
            }

            .sidebar-toggle-btn {
                display: none !important;
            }

            .sidebar-left,
            .chart-center,
            .sidebar-right {
                width: 100% !important;
                max-width: 100% !important;
                min-width: 0 !important;
                height: 100% !important;
                display: none;
            }

            .futu-main.m-view-watchlist .sidebar-left {
                display: flex !important;
            }

            .futu-main.m-view-chart .chart-center {
                display: flex !important;
            }

            .futu-main.m-view-tactics .sidebar-right,
            .futu-main.m-view-options .sidebar-right,
            .futu-main.m-view-portfolio .sidebar-right {
                display: flex !important;
                padding-right: 0;
            }

            .chart-stage-header {
                flex-direction: column;
                align-items: stretch;
                padding: 8px 10px;
                gap: 8px;
            }

            .center-view-tabs {
                width: 100%;
                display: grid;
                grid-template-columns: 1fr 1fr;
            }

            .cv-tab {
                text-align: center;
                padding: 6px 6px;
                font-size: 12px;
            }

            .chart-period-strip {
                display: none !important;
            }

            .mobile-period-bar {
                display: flex !important;
                align-items: center;
                gap: 4px;
                padding: 4px 8px;
                border-bottom: 1px solid var(--border-subtle);
                overflow-x: auto;
                scrollbar-width: none;
                flex-shrink: 0;
            }

            .mobile-period-bar::-webkit-scrollbar { display: none; }

            .m-period-tab {
                padding: 5px 10px;
                font-size: 12px;
                font-weight: 600;
                color: var(--text-secondary);
                border-radius: 6px;
                cursor: pointer;
                white-space: nowrap;
            }

            .m-period-tab.active {
                color: var(--color-accent);
                background: var(--bg-active);
                font-weight: 700;
            }

            .intraday-path-panel {
                padding: 8px;
                gap: 8px;
            }

            .ip-div-badge {
                white-space: normal;
                font-size: 11px;
            }

            .ip-chart-wrap {
                min-height: 220px;
                padding: 4px;
            }

            .price-trio-grid {
                gap: 6px;
            }

            .price-tile {
                padding: 8px 7px;
            }

            .price-tile-val {
                font-size: 14px;
            }

            .futu-footer {
                padding: 4px 8px;
                gap: 0;
            }

            .footer-meta-row {
                display: none !important;
            }

            .site-disclaimer-banner {
                font-size: 9.5px;
                line-height: 1.25;
                padding: 3px 6px;
            }

            .mobile-bottom-nav {
                display: flex !important;
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                height: 56px;
                background: var(--bg-card);
                border-top: 1px solid var(--border-subtle);
                z-index: 999;
                padding-bottom: env(safe-area-inset-bottom, 0px);
            }

            .m-nav-tab {
                flex: 1;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                gap: 2px;
                cursor: pointer;
                color: var(--text-secondary);
                user-select: none;
            }

            .m-nav-tab.active {
                color: var(--color-accent);
            }

            .m-nav-icon {
                font-size: 17px;
                line-height: 1;
            }

            .m-nav-label {
                font-size: 11px;
                font-weight: 700;
            }

            .mobile-only {
                display: block !important;
            }

            .notif-drawer {
                width: 100vw !important;
            }
        }

        /* 全局真 VIX 恐慌中枢胶囊与浮层 + VIXY 现货提醒条 */
        .vix-pill-wrap {
            position: relative;
            display: inline-flex;
            align-items: center;
            flex-shrink: 0;
        }
        .vix-regime-pill {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 11.5px;
            font-weight: 700;
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            color: var(--text-primary);
            cursor: pointer;
            white-space: nowrap;
            font-family: inherit;
            transition: border-color 0.15s ease;
        }
        .vix-regime-pill:hover {
            border-color: var(--color-accent);
        }
        .vix-detail-popover {
            display: none;
            position: absolute;
            top: calc(100% + 8px);
            left: 0;
            width: 340px;
            max-width: calc(100vw - 24px);
            background: var(--bg-card);
            border: 1px solid var(--border-strong);
            border-radius: 12px;
            padding: 12px 14px;
            box-shadow: var(--shadow-float, var(--shadow-card));
            z-index: 950;
            text-align: left;
            color: var(--text-primary);
        }
        .vix-detail-popover.is-open,
        .vix-detail-popover.is-hover-open {
            display: block;
        }
        .vix-pop-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            padding-bottom: 8px;
            margin-bottom: 8px;
            border-bottom: 1px solid var(--border-subtle);
            font-size: 12.5px;
            font-weight: 800;
        }
        .vix-pop-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 6px;
            margin-bottom: 8px;
        }
        .vix-pop-cell {
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            border-radius: 8px;
            padding: 6px 8px;
        }
        .vix-pop-lbl {
            display: block;
            font-size: 10.5px;
            color: var(--text-muted);
            margin-bottom: 2px;
        }
        .vix-pop-val {
            font-size: 13px;
            font-weight: 800;
            font-variant-numeric: tabular-nums;
            color: var(--text-primary);
        }
        .vix-pop-tech {
            font-size: 11.5px;
            font-weight: 700;
            line-height: 1.4;
            color: var(--text-primary);
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            border-radius: 8px;
            padding: 6px 9px;
            margin-bottom: 6px;
        }
        .vix-pop-advice {
            font-size: 11.5px;
            line-height: 1.5;
            color: var(--text-secondary);
            background: var(--bg-elevated);
            border-radius: 8px;
            padding: 7px 9px;
        }
        .vixy-spot-hint {
            margin-top: 8px;
            padding: 8px 10px;
            border-radius: 8px;
            border: 1px solid var(--color-accent-border);
            background: var(--color-accent-bg);
            color: var(--text-primary);
            font-size: 11.5px;
            line-height: 1.45;
        }
        @media (max-width: 768px) {
            .vix-regime-pill {
                font-size: 10.5px;
                padding: 3px 8px;
                max-width: 100%;
            }
            .vix-detail-popover {
                position: fixed;
                left: 10px !important;
                right: 10px !important;
                top: 56px;
                width: auto;
                max-width: calc(100vw - 20px);
            }
        }
    </style>
</head>
<body>

    <!-- 1. 顶部清爽导航顶栏 -->
    <header class="futu-header">
        <div class="header-left">
            <div class="futu-logo">
                <span class="brand-mark">⚡</span>
                <span class="brand-text">量化雷达</span>
                <span class="brand-count">__PY_TPL_TOTAL_COUNT__标的</span>
            </div>
            <!-- 大盘红绿灯一句话胶囊 + 全局真VIX恐慌胶囊 + 实盘成绩单胶囊 -->
            <span id="pAiRegimePill" class="regime-pill" title="大盘体制状态">--</span>
            <div class="vix-pill-wrap" id="vixPillWrap">
                <button type="button" id="pVixRegimePill" class="vix-regime-pill" onclick="toggleVixPopover(event)" title="全局真 VIX 恐慌中枢" aria-expanded="false" aria-controls="vixDetailPopover">⚪ VIX --</button>
                <div id="vixDetailPopover" class="vix-detail-popover" role="dialog" aria-label="全局真 VIX 恐慌中枢详情"></div>
            </div>
            <span id="pAiScorecardPill" class="scorecard-pill tabular" title="AI 实盘近7日/30日滚动胜率">--</span>
            <div class="header-status-badges">
                __PY_TPL_SESSION_BADGE__
                __PY_TPL_DEF_BADGE__
                __PY_TPL_BREADTH_BADGE__
            </div>
            <span class="top-disclaimer-pill" title="本看板所有行情数据、量化指标与 AI 历史统计推演均为程序客观计算结果，仅作个人学习研究与看盘辅助参考，不构成任何投资或买卖建议。">⚠️ 数据仅供参考</span>
        </div>

        <div class="header-right">
            <!-- 快捷搜索框 -->
            <div class="quick-search-wrap">
                <input type="search" id="quickSearchInput" class="quick-search-input" placeholder="🔍 搜代码或名称..." autocomplete="off" aria-label="搜索股票代码或名称">
                <div id="quickSearchDropdown" class="quick-search-dropdown"></div>
            </div>

            <!-- 跨页导航紧凑入口 -->
            <nav class="mobile-quick-nav" id="mobileQuickNav" aria-label="快捷导航">
                <a href="predict_radar_5y.html" class="tool-link m-quick-link nav-accent" id="navLinkAiPredict" title="打开 AI 15日推演与5年多赛道预测战报">AI 15日推演</a>
                <a href="stocks_radar.html" class="tool-link m-quick-link" id="navLinkStocksRadar">个股雷达</a>
                <a href="macro_radar.html" class="tool-link m-quick-link" id="navLinkMacroRadar">宏观司令塔</a>
                <a href="backtest_report_5y.html" class="tool-link m-quick-link" id="navLinkBacktest5y">5Y回测</a>
            </nav>

            <!-- 预警中心 & 声音切换 -->
            <div class="header-alert-controls">
                <button type="button" id="btnNotifCenter" onclick="toggleNotifDrawer()" class="btn-notif-pill" title="打开预警通知中心">
                    <span>🔔</span>
                    <span class="notif-btn-label">预警中心</span>
                    <span id="notifBadge" class="notif-count-badge tabular">0</span>
                </button>
                <button type="button" id="btnSoundToggle" onclick="toggleSoundAlerts()" class="tool-link" title="声音提示：已开启 (点击切换静音)" style="padding: 5px 8px;">
                    <span id="soundIcon">🔊</span>
                </button>
            </div>

            <!-- 深色/浅色一键切换按钮 -->
            <button type="button" id="btnThemeToggle" class="theme-toggle-btn" onclick="toggleTheme()" title="切换浅色模式" aria-label="切换浅色或深色模式">
                <span id="themeToggleIcon">☀️</span>
                <span class="theme-toggle-text" id="themeToggleLabel">浅色</span>
            </button>
        </div>
    </header>

    __PY_TPL_DEF_BANNER__
    __PY_TPL_BREADTH_BANNER__

    <!-- 2. 克制的单行宏观快讯条 (点击文本可展开/收起详情) -->
    <div id="macroStripWrap" class="macro-strip-wrap" onclick="if(event.target.tagName !== 'BUTTON' && !event.target.closest('button')) toggleMacroStrip()" title="点击展开或收起宏观快讯详情">
        __PY_TPL_MACRO_BANNER__
    </div>

    <!-- 3. 主体三栏工作台 -->
    <main class="futu-main m-view-watchlist" id="futuMain">
        <button type="button" id="btnToggleSidebar" onclick="toggleSidebar()" title="折叠/展开左侧标的列表" class="sidebar-toggle-btn">◀</button>

        <!-- 左栏：小白化自选列表 -->
        <aside class="sidebar-left" id="sidebarLeft">
            <div class="wl-filter-header">
                <!-- 4 个核心高频小白筛选胶囊 -->
                <div class="wl-primary-filters" id="wlPrimaryFilters">
                    <button type="button" class="wl-pill-btn active" data-filter="all" onclick="setWatchlistFilter('all')">全部 (__PY_TPL_TOTAL_COUNT__)</button>
                    <button type="button" class="wl-pill-btn" data-filter="opportunity" onclick="setWatchlistFilter('opportunity')">🔥 今日机会</button>
                    <button type="button" class="wl-pill-btn" data-filter="risk" onclick="setWatchlistFilter('risk')">⚠️ 风险避坑</button>
                    <button type="button" class="wl-pill-btn" data-filter="gold_crypto" onclick="setWatchlistFilter('gold_crypto')">🥇 黄金·加密</button>
                </div>
                <div class="wl-leader-bar">
                    <button type="button" id="btnResilientTop5" class="wl-leader-btn" data-filter="resilient_top5" onclick="setWatchlistFilter('resilient_top5')" title="恐慌退潮真龙头：排除反向ETF，优先站稳VWAP + 20日超额RS + 相对SPY强弱">🛡️ 抗跌领涨 Top 5</button>
                </div>
                <!-- 二级细分赛道下拉 + 展开按钮 -->
                <div class="wl-secondary-bar">
                    <select id="sectorFilterSelect" class="wl-sector-select" onchange="onSectorSelectChange(this.value)" aria-label="按行业赛道或策略筛选">
                        <option value="">更多赛道 / 策略筛选...</option>
                        <option value="resilient_top5">🛡️ 抗跌领涨 Top 5</option>
                        <option value="ai_top">⚡ AI高胜率排序</option>
                        <option value="ai_short">🩸 AI做空榜 (Top 5)</option>
                        <option value="pit">🟢 黄金坑低吸</option>
                        <option value="take_profit">🟣 止盈减仓</option>
                        <option value="stop">🔴 破位/回踩</option>
                        <option value="stocks">仅看个股 (__PY_TPL_STOCKS_COUNT__)</option>
                        <option value="macro">仅看ETF (__PY_TPL_MACRO_COUNT__)</option>
                        <option value="存储闪存">存储与NAND</option>
                        <option value="芯片与半导体">芯片半导体</option>
                        <option value="液冷服务器">液冷与服务器</option>
                        <option value="云与软件">云巨头AI</option>
                        <option value="光通信">光通信</option>
                        <option value="能源电力">能源与核电</option>
                        <option value="太空商业">太空商业</option>
                        <option value="物理与安全">物理AI与安全</option>
                        <option value="数字资产">💎 数字资产 (BTC)</option>
                        <option value="大宗商品">🥇 黄金现货</option>
                        <option value="生物医药">生物医药</option>
                        <option value="价值消费">价值与传统龙头</option>
                    </select>
                    <button type="button" id="btnToggleMoreFilters" class="wl-more-btn" onclick="toggleMoreFilters()" title="展开或收起全部赛道标签">赛道 ▾</button>
                </div>
            </div>

            <!-- 默认折叠的细分赛道标签栏 -->
            <div class="watchlist-categories" id="watchlistCategories">
                <div class="w-tab active" data-filter="all">全部 (__PY_TPL_TOTAL_COUNT__)</div>
                <div class="w-tab" data-filter="resilient_top5" style="color: var(--color-up); font-weight: 700;">🛡️ 抗跌领涨 Top 5</div>
                <div class="w-tab" data-filter="ai_top" style="color: #a78bfa; font-weight: 700;">⚡ AI高胜率 (__PY_TPL_TOTAL_COUNT__)</div>
                <div class="w-tab" data-filter="ai_short" style="color: #ff5252; font-weight: 700; background: rgba(248, 81, 73, 0.14); border-color: rgba(248, 81, 73, 0.35);">🩸 AI做空榜 (Top 5)</div>
                <div class="w-tab" data-filter="pit">🟢 黄金坑</div>
                <div class="w-tab" data-filter="take_profit">🟣 止盈减仓</div>
                <div class="w-tab" data-filter="stop">🔴 破位/回踩</div>
                <div class="w-tab" data-filter="stocks">个股 (__PY_TPL_STOCKS_COUNT__)</div>
                <div class="w-tab" data-filter="macro">ETF (__PY_TPL_MACRO_COUNT__)</div>
                <div class="w-tab" data-filter="存储闪存">存储与NAND</div>
                <div class="w-tab" data-filter="芯片与半导体">芯片半导体</div>
                <div class="w-tab" data-filter="液冷服务器">液冷与服务器</div>
                <div class="w-tab" data-filter="云与软件">云巨头AI</div>
                <div class="w-tab" data-filter="光通信">光通信</div>
                <div class="w-tab" data-filter="能源电力">能源与核电</div>
                <div class="w-tab" data-filter="太空商业">太空商业</div>
                <div class="w-tab" data-filter="物理与安全">物理AI与安全</div>
                <div class="w-tab" data-filter="数字资产">💎 数字资产 (BTC)</div>
                <div class="w-tab" data-filter="大宗商品">🥇 黄金现货</div>
                <div class="w-tab" data-filter="生物医药">生物医药</div>
                <div class="w-tab" data-filter="价值消费">价值与传统龙头</div>
            </div>

            <div class="watchlist-container" id="watchlistContainer"></div>
        </aside>

        <!-- 中栏：TradingView 主图 + 今日盘中推演 Segmented Control -->
        <section class="chart-center" id="chartMiddle">
            <div id="resilientLeadersStrip" class="resilient-leaders-strip" style="display: none;"></div>
            <div class="chart-stage-header">
                <div class="current-stock-badge" id="headerStockBadge">
                    <span class="cur-sym" id="curSym">--</span>
                    <span class="cur-name" id="curName">加载中...</span>
                    <span class="cur-price tabular" id="curPrice">--</span>
                    <span class="cur-change tabular" id="curChange" style="color: var(--text-secondary); background: var(--bg-elevated);">--</span>
                    <span id="curExtBadge" class="cur-ext-badge tabular" style="display: none;">--</span>
                    <span id="curEarningsBadge" class="cur-earn-badge tabular" style="display: none;">--</span>
                </div>

                <div class="center-view-tabs" id="centerViewTabs">
                    <div class="cv-tab active" id="tabKlineView" data-view="kline" onclick="switchCenterChartView('kline')">📊 实时行情K线</div>
                    <div class="cv-tab" id="tabIntradayPathView" data-view="intraday_path" onclick="switchCenterChartView('intraday_path')">📈 今日盘中推演</div>
                </div>
            </div>

            <div class="chart-period-strip">
                <div class="period-bar" id="periodBar">
                    <div class="period-tab" data-period="1" data-style="3" data-type="min">分时</div>
                    <div class="period-tab" data-period="5" data-style="1" data-type="min">5日</div>
                    <div class="period-tab active" data-period="D" data-style="1" data-type="day">日K</div>
                    <div class="period-tab" data-period="W" data-style="1" data-type="day">周K</div>
                    <div class="period-tab" data-period="M" data-style="1" data-type="day">月K</div>
                    <div class="period-tab" data-period="3M" data-style="1" data-type="day">季K</div>
                    <div class="period-tab" data-period="12M" data-style="1" data-type="day">年K</div>
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
                <span class="center-view-meta" id="centerViewMeta">盘前走廊推演就绪</span>
            </div>

            <!-- 手机端 K 线周期条 -->
            <div class="mobile-period-bar" id="mobilePeriodBar">
                <div class="m-period-tab" data-period="1" data-style="3">分时</div>
                <div class="m-period-tab" data-period="5" data-style="1">5分</div>
                <div class="m-period-tab" data-period="15" data-style="1">15分</div>
                <div class="m-period-tab" data-period="60" data-style="1">1时</div>
                <div class="m-period-tab active" data-period="D" data-style="1">日K</div>
                <div class="m-period-tab" data-period="W" data-style="1">周K</div>
                <div class="m-period-tab" data-period="M" data-style="1">月K</div>
            </div>

            <div class="tv-container" id="tv_chart_container"></div>

            <div class="intraday-path-panel" id="intradayPathView">
                <div class="ip-header-strip" id="ipHeaderStrip">
                    <div class="ip-summary-block">
                        <div class="ip-summary-text" id="ipSummaryText">--</div>
                        <div class="ip-meta-tag" id="ipMetaTag">--</div>
                    </div>
                    <div class="ip-div-badge" id="ipDivergenceBadge">--</div>
                </div>
                <div class="ip-levels-bar" id="ipLevelsBar"></div>
                <div class="ip-chart-wrap" id="ipChartWrap">
                    <svg id="ipChartSvg" viewBox="0 0 920 420" preserveAspectRatio="xMidYMid meet"></svg>
                    <div class="ip-tooltip" id="ipTooltip"></div>
                </div>
                <div class="ip-fallback-box" id="ipFallbackBox"></div>
            </div>
        </section>

        <!-- 右栏：小白 3 秒极简决策卡 + 默认折叠专业量化数据抽屉 -->
        <aside class="sidebar-right" id="sidebarRight">
            <!-- 默认展示层：小白 3 秒极简决策卡 -->
            <section class="decision-card" id="beginnerDecisionCard">
                <!-- 1. 结论大卡片 -->
                <div class="verdict-hero" id="beginnerVerdictHero">
                    <div class="verdict-eyebrow">
                        <span>📌 3秒看懂 · 当前操作建议</span>
                        <span id="beginnerSectorTag" style="color: var(--color-accent);">--</span>
                    </div>
                    <div class="verdict-title" id="beginnerVerdictTitle">⏳ 正在分析当前标的...</div>
                    <div class="verdict-sub" id="beginnerVerdictSub">正在加载最新行情与模型判断...</div>
                    <div class="verdict-chips-row" id="pAiActionBanner">
                        <span class="verdict-chip" id="pAiActionText">--</span>
                        <span class="verdict-chip" id="pAiSectorStar">--</span>
                        <span class="verdict-chip" id="pAiShrinkBadge" style="display: none;"></span>
                        <span class="verdict-chip" id="pIpoNoticeBadge" style="display: none;">🆕 IPO 首日观察 · 满5日开启完整模型</span>
                    </div>
                    <div id="pVixySpotHint" class="vixy-spot-hint" style="display: none;"></div>
                </div>

                <!-- 2. 三个直白价格格 (三列大号数字，全站唯一口径) -->
                <div class="price-trio-grid">
                    <div class="price-tile">
                        <div class="price-tile-lbl" id="pTileBuyLbl">参考买点 / 观察价</div>
                        <div class="price-tile-val warn tabular" id="pOptimalLimit">--</div>
                        <div class="price-tile-sub tabular" id="pOptimalNote">--</div>
                    </div>
                    <div class="price-tile">
                        <div class="price-tile-lbl" id="pTileTpLbl">目标止盈</div>
                        <div class="price-tile-val up tabular" id="pBeginnerTakeProfit">--</div>
                        <div class="price-tile-sub tabular" id="pBeginnerTakeProfitPct">--</div>
                    </div>
                    <div class="price-tile">
                        <div class="price-tile-lbl" id="pTileStopLbl">跌破必砍 (短线止损)</div>
                        <div class="price-tile-val down tabular" id="pEntryStopLoss">--</div>
                        <div class="price-tile-sub tabular" id="pEntryStopDist">--</div>
                    </div>
                </div>
                <div id="pBearDefenseNote" class="price-trio-bear-note" style="display: none;">🧭 空头留意支撑区止盈 / 反抽压力位防守</div>

                <!-- 3. 多空趋势温度条 -->
                <div class="trend-thermo-card">
                    <div class="thermo-header">
                        <span>多空趋势温度</span>
                        <span id="beginnerTrendHint">--</span>
                    </div>
                    <div class="thermo-nums">
                        <div class="thermo-side">
                            <span class="thermo-lbl">看涨概率</span>
                            <span class="thermo-val up tabular" id="pAiLongWr">--%</span>
                        </div>
                        <div class="thermo-side">
                            <span class="thermo-lbl">看跌概率</span>
                            <span class="thermo-val down tabular" id="pAiShortWr">--%</span>
                        </div>
                    </div>
                    <div class="thermo-track">
                        <div id="pAiLongBar" class="thermo-fill-up"></div>
                        <div class="thermo-divider"></div>
                        <div id="pAiShortBar" class="thermo-fill-down"></div>
                    </div>
                </div>
            </section>

            <!-- 展开/收起专业量化数据抽屉按钮 -->
            <button type="button" id="btnProQuantToggle" class="pro-drawer-toggle" onclick="toggleProQuantDrawer()" aria-expanded="false" aria-controls="proQuantDrawer">
                <span id="proDrawerToggleLabel">🔍 展开专业量化数据 ▾</span>
            </button>

            <!-- 默认折叠的专业量化抽屉 (折叠状态下 DOM 仍存在且实时同步，0 JS 报错) -->
            <div class="pro-quant-drawer" id="proQuantDrawer">
                <!-- Group ①：日内盘口微结构 & 技术指标 -->
                <div class="panel-box" id="entryPlanBox" data-cat="entry">
                    <div class="box-title">
                        <span>① 日内盘口微结构与技术指标</span>
                        <div style="display: flex; align-items: center; gap: 6px;">
                            <span id="pSector" style="font-size: 11px; color: var(--color-accent); font-weight: 600;">--</span>
                            <span class="card-toggle-btn" onclick="toggleCard(this)">▲</span>
                        </div>
                    </div>
                    <div class="panel-box-content" style="display: flex; flex-direction: column; gap: 8px;">
                        <div id="pIntradayBanner" class="pro-sub-banner">
                            <div id="pIntradayStatusText" style="font-weight: 700; margin-bottom: 2px;">⏳ 加载中...</div>
                            <div id="pIntradayReason" style="color: var(--text-secondary);">正在同步实时量化挂单参数...</div>
                        </div>
                        <div id="pSignalBanner" class="pro-sub-banner" style="display: flex; align-items: center; justify-content: space-between; gap: 6px; flex-wrap: wrap;">
                            <span id="pSignalText" style="font-weight: 700;">⏳ 数据加载中...</span>
                            <span id="pSignalTag" style="font-size: 10.5px; padding: 2px 7px; border-radius: 5px; background: var(--bg-card); font-weight: 600;">--</span>
                        </div>
                        <div class="metrics-grid tabular">
                            <div class="m-item"><span class="m-lbl" id="pVwapLabel">日内 VWAP 均价</span><span class="m-val" id="pVwap">--</span></div>
                            <div class="m-item"><span class="m-lbl" id="pRsi5mLabel">5m RSI / 量比</span><span class="m-val" id="pRsi5m">--</span></div>
                            <div class="m-item"><span class="m-lbl" id="pRs20dLabel">20日超额RS</span><span class="m-val" id="pRs20d">--</span></div>
                            <div class="m-item"><span class="m-lbl">财报避雷日历</span><span class="m-val" id="pEarningsDate">--</span></div>
                            <div class="m-item" style="grid-column: span 2;"><span class="m-lbl">黄金坑支撑带</span><span class="m-val" id="pEntryFibBand">--</span></div>
                            <div class="m-item"><span class="m-lbl" id="pEntryPriceLabel">💡 参考挂单价</span><span class="m-val" id="pProEntryRef">--</span></div>
                            <div class="m-item"><span class="m-lbl" id="pEntryStopLabel">🛡️ 防守止损线 (日内短线)</span><span class="m-val" id="pProStopRef">--</span></div>
                        </div>
                        <div id="boxQuotes">
                            <div class="metrics-grid tabular">
                                <div class="m-item"><span class="m-lbl">50日均线</span><span class="m-val" id="pMa50">--</span></div>
                                <div class="m-item"><span class="m-lbl">MA50偏离</span><span class="m-val" id="pBias">--</span></div>
                                <div class="m-item"><span class="m-lbl">EMA20趋势</span><span class="m-val" id="pEma20">--</span></div>
                                <div class="m-item"><span class="m-lbl">成交量比</span><span class="m-val" id="pVolRatio">--</span></div>
                                <div class="m-item"><span class="m-lbl">14日 RSI</span><span class="m-val" id="pRsi">--</span></div>
                                <div class="m-item"><span class="m-lbl">14日 ATR</span><span class="m-val" id="pAtr">--</span></div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Group ②：机构期权与大单墙 -->
                <div class="panel-box" id="optionsGroupBox" data-cat="options">
                    <div class="box-title">
                        <span>② 机构期权与大单墙</span>
                        <div style="display: flex; align-items: center; gap: 6px;">
                            <span id="pPcrSentimentBadge" style="font-size: 10.5px; padding: 2px 7px; border-radius: 6px; font-weight: 700;">--</span>
                            <span class="card-toggle-btn" onclick="toggleCard(this)">▲</span>
                        </div>
                    </div>
                    <div class="panel-box-content" style="display: flex; flex-direction: column; gap: 8px;">
                        <div class="pro-sub-banner tabular" style="display: flex; justify-content: space-between; gap: 6px; flex-wrap: wrap; font-size: 11px;">
                            <span id="pAiLongRaw">原始多: --%</span>
                            <span id="pAiPcrSentiment">期权 PCR: --</span>
                            <span id="pAiShortRaw">原始空: --%</span>
                        </div>
                        <div id="optionsAnomalyBox" style="display: flex; flex-direction: column; gap: 7px;">
                            <div class="metrics-grid tabular">
                                <div class="m-item"><span class="m-lbl">沽购比(张数/资金)</span><span class="m-val" id="pOptionsPcr">--</span></div>
                                <div class="m-item"><span class="m-lbl">期权多空张数</span><span class="m-val" id="pOptionsVolRatio">--</span></div>
                                <div class="m-item"><span class="m-lbl">平值隐含波动(IV)</span><span class="m-val" id="pOptionsIv">--</span></div>
                                <div class="m-item"><span class="m-lbl">期权定价波动带</span><span class="m-val" id="pOptionsExpMove">--</span></div>
                            </div>
                            <div class="m-item tabular"><span class="m-lbl" style="color: var(--color-up);">🔥 主力 Call 大单(阻力墙)</span><span class="m-val" id="pTopCallStrike">--</span></div>
                            <div class="m-item tabular"><span class="m-lbl" style="color: var(--color-down);">🛡️ 主力 Put 大单(支撑墙)</span><span class="m-val" id="pTopPutStrike">--</span></div>
                            <div id="pOptionsExpTip" style="font-size: 10px; color: var(--text-muted); text-align: right;">*基于主力周期权利金大单与 ATM Straddle 隐含波动测算</div>
                        </div>
                        <div id="optionsUnavailableNote" style="display: none; font-size: 11.5px; color: var(--text-secondary); padding: 8px 10px; background: var(--bg-elevated); border-radius: 8px;">
                            ⚪ 当前标的暂无美股期权链数据（现货/加密/新股仅展示现货与均线量价指标）。
                        </div>
                    </div>
                </div>

                <!-- Group ③：4H 筹码与波段剧本 + 战术诊断 + 资讯/宏观 -->
                <div class="panel-box" id="aiPredictionBox" data-cat="ai">
                    <div class="box-title">
                        <span>③ 4H 筹码与波段剧本</span>
                        <div style="display: flex; align-items: center; gap: 5px; flex-wrap: wrap;">
                            <span id="pAiRankBadge" class="tabular" style="font-size: 10.5px; padding: 2px 7px; border-radius: 6px; background: var(--color-purple-bg); color: var(--color-purple); font-weight: 700;">多 #-- · 空 #--</span>
                            <a id="pAiTvLink" href="https://www.tradingview.com" target="_blank" style="font-size: 10.5px; padding: 2px 7px; border-radius: 6px; background: var(--color-accent-bg); color: var(--color-accent); text-decoration: none; font-weight: 700;">📈 TV ↗</a>
                            <a href="predict_radar_5y.html" target="_blank" style="font-size: 10.5px; padding: 2px 7px; border-radius: 6px; background: var(--bg-elevated); color: var(--text-primary); text-decoration: none; font-weight: 700;">⚡ 5年战报 ↗</a>
                            <span class="card-toggle-btn" onclick="toggleCard(this)">▲</span>
                        </div>
                    </div>
                    <div class="panel-box-content" id="pAiCardBody" style="display: flex; flex-direction: column; gap: 8px;">
                        <div class="pro-targets-grid tabular">
                            <div class="pro-target-cell">
                                <div class="pro-target-lbl">🎯 多头目标止盈</div>
                                <b style="color: var(--color-up);" id="pAiTargetPrice">--</b>
                            </div>
                            <div class="pro-target-cell">
                                <div class="pro-target-lbl">🛡️ 波段防守止损</div>
                                <b style="color: var(--color-down);" id="pAiStopPrice">--</b>
                            </div>
                            <div class="pro-target-cell">
                                <div class="pro-target-lbl">🩸 空头目标下杀</div>
                                <b style="color: var(--color-down);" id="pAiShortTargetPrice">--</b>
                            </div>
                            <div class="pro-target-cell">
                                <div class="pro-target-lbl">🛡️ 空头反抽防守</div>
                                <b style="color: var(--color-up);" id="pAiShortStopPrice">--</b>
                            </div>
                        </div>

                        <div id="pAiV2ScenarioBox" class="pro-scenario-box">
                            <div style="display: flex; justify-content: space-between; align-items: center; gap: 6px; flex-wrap: wrap;">
                                <span style="color: var(--text-secondary); font-size: 10.5px;">🧭 V2.0 筹码点位 &amp; 4H 共振</span>
                                <span id="pAiFourHDiv" style="font-size: 10.5px; font-weight: 700; color: var(--color-purple);">--</span>
                            </div>
                            <div id="pAiMultiLevels" class="tabular" style="font-size: 11px; color: var(--text-primary); display: flex; justify-content: space-between; gap: 6px; flex-wrap: wrap;">
                                <span>支撑 S1/S2: --</span>
                                <span>阻力 R1/R2: --</span>
                            </div>
                            <div id="pAiScenarioScript" style="font-size: 11px; color: var(--text-secondary); line-height: 1.45; border-left: 2px solid var(--color-accent); padding-left: 8px;">--</div>
                        </div>
                    </div>
                </div>

                <div class="panel-box" id="tacticalDecisionBox" data-cat="tactical">
                    <div class="box-title">
                        <span>🎯 量化战术研判与深度成因</span>
                        <div style="display: flex; align-items: center; gap: 6px;">
                            <span id="pActionTypeBadge" style="font-size: 10.5px; padding: 2px 7px; border-radius: 6px; background: var(--bg-elevated); font-weight: 700;">--</span>
                            <span class="card-toggle-btn" onclick="toggleCard(this)">▲</span>
                        </div>
                    </div>
                    <div class="panel-box-content" style="display: flex; flex-direction: column; gap: 8px;">
                        <div id="pDecisionTitle" style="font-size: 13px; font-weight: 800;">⏳ 正在解析量化信号...</div>
                        <div class="pro-sub-banner" style="display: flex; flex-direction: column; gap: 4px; font-size: 11px;">
                            <div>• <b>均线形态：</b><span id="pMaReason">--</span></div>
                            <div>• <b>量价动能：</b><span id="pVolReason">--</span></div>
                            <div>• <b>波段支撑：</b><span id="pFibReason">--</span></div>
                            <div>• <b>板块共振：</b><span id="pMacroReason">--</span></div>
                        </div>
                        <div id="pActionDirective" style="font-size: 11px; line-height: 1.5; color: var(--text-secondary);">--</div>
                    </div>
                </div>

                <div class="panel-box" id="tickerNewsBox" data-cat="news">
                    <div class="box-title">
                        <span>📰 个股消息面 &amp; 财报排雷</span>
                        <div style="display: flex; align-items: center; gap: 6px;">
                            <span id="pNewsTickerBadge" style="font-size: 10.5px; padding: 2px 7px; border-radius: 6px; background: var(--color-accent-bg); color: var(--color-accent); font-weight: 700;">--</span>
                            <span class="card-toggle-btn" onclick="toggleCard(this)">▲</span>
                        </div>
                    </div>
                    <div class="panel-box-content" style="display: flex; flex-direction: column; gap: 7px;">
                        <div id="pNewsEarningsStatus" class="pro-sub-banner" style="font-weight: 600;">🟢 近期无财报雷区</div>
                        <div id="pTickerNewsList" style="display: flex; flex-direction: column; gap: 6px; font-size: 11.5px;">
                            <div style="color: var(--text-muted); padding: 4px 0;">⏳ 正在加载关联资讯...</div>
                        </div>
                    </div>
                </div>

                <div class="panel-box weather-mini" data-cat="macro">
                    <div class="box-title">
                        <span>🏛️ 宏观司令塔气象</span>
                        <div style="display: flex; align-items: center; gap: 6px;">
                            <span id="pWeatherBadge" style="color: var(--color-accent); font-weight: 700;">__PY_TPL_WEATHER_NAME__</span>
                            <span class="card-toggle-btn" onclick="toggleCard(this)">▲</span>
                        </div>
                    </div>
                    <div class="panel-box-content" style="display: flex; flex-direction: column; gap: 6px; font-size: 11px; color: var(--text-secondary);">
                        <div id="pWeatherDesc" style="line-height: 1.45;">__PY_TPL_WEATHER_TACTICS__</div>
                        <div style="padding-top: 6px; border-top: 1px dashed var(--border-subtle);">
                            <span style="font-weight: 700; color: var(--text-primary);">🌐 跨行业广度：</span>
                            <span id="pBreadthStatus" style="color: __PY_TPL_BREADTH_COLOR__; font-weight: 700;">__PY_TPL_BREADTH_TAG__</span>
                        </div>
                        <div style="padding-top: 6px; border-top: 1px dashed var(--border-subtle);">
                            <span style="font-weight: 700; color: var(--text-primary);">🏦 闲置资金泊入指示：</span>
                            <span style="color: __PY_TPL_PARKING_COLOR__; font-weight: 700;">__PY_TPL_PARKING_ACTION__</span>
                            <div style="color: var(--text-muted); font-size: 10.5px; margin-top: 2px;">__PY_TPL_PARKING_DESC__</div>
                        </div>
                    </div>
                </div>
            </div>
        </aside>
    </main>

    <!-- 手机端底部 3 栏清爽切换 (选股 / 看图 / 决策) -->
    <nav class="mobile-bottom-nav" id="mobileBottomNav" aria-label="手机端主视图切换">
        <div class="m-nav-tab active" data-tab="watchlist" onclick="switchMobileTab('watchlist')">
            <span class="m-nav-icon">📋</span>
            <span class="m-nav-label">选股</span>
        </div>
        <div class="m-nav-tab" data-tab="chart" onclick="switchMobileTab('chart')">
            <span class="m-nav-icon">📈</span>
            <span class="m-nav-label">看图</span>
        </div>
        <div class="m-nav-tab" data-tab="tactics" onclick="switchMobileTab('tactics')">
            <span class="m-nav-icon">🎯</span>
            <span class="m-nav-label">决策</span>
        </div>
    </nav>

    <!-- 底部状态与全站免责声明 -->
    <footer class="futu-footer">
        <div class="footer-meta-row">
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
                <span style="color: var(--color-accent); cursor: pointer;" onclick="toggleFullscreen()" title="点击全屏或按F11">🖥️ 全屏模式(F11)</span>
                <span class="tabular">⏱️ 60秒自动轮询 | 标的池: __PY_TPL_TOTAL_COUNT__只 | 更新: __PY_TPL_UPDATE_TIME__</span>
            </div>
        </div>
        <div class="site-disclaimer-banner" id="siteDisclaimerBanner">
            ⚠️ <strong>免责声明</strong>：本看板所有行情数据、量化指标与 AI 历史统计推演均为程序客观计算结果，仅作个人学习研究与看盘辅助参考，<strong>不构成任何投资或买卖建议</strong>。美股及杠杆产品波动剧烈，请独立决策并严控风险。
        </div>
    </footer>

    <div class="toast-box" id="toastBox">✅ 操作成功</div>

    <!-- 预警通知中心侧边抽屉 -->
    <div class="notif-drawer-overlay" id="notifDrawerOverlay" onclick="if(event.target===this) toggleNotifDrawer()">
        <div class="notif-drawer">
            <div class="notif-drawer-header">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-size: 16px;">🔔</span>
                    <span style="font-size: 14px; font-weight: 800; color: var(--text-primary);">实盘预警通知中心</span>
                    <span id="notifTotalCount" class="tabular" style="font-size: 11px; background: var(--color-accent-bg); color: var(--color-accent); padding: 2px 7px; border-radius: 999px; font-weight: 700;">0 条</span>
                </div>
                <div style="display: flex; align-items: center; gap: 8px;">
                    <button type="button" class="notif-btn-clear" onclick="clearAllNotifs()">清空通知</button>
                    <button type="button" class="notif-btn-close" onclick="toggleNotifDrawer()">✕</button>
                </div>
            </div>
            <div class="notif-sound-bar">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span id="drawerSoundLabel" style="font-weight: 700; color: var(--color-up);">🔊 声音预警已开启</span>
                    <button type="button" onclick="toggleSoundAlerts()" class="notif-btn-clear">切换静音</button>
                </div>
                <div style="display: flex; align-items: center; gap: 3px; flex-wrap: wrap;">
                    <span style="color: var(--text-muted); font-size: 10px;">试听:</span>
                    <button type="button" onclick="playAlertSound('defensive_flight', true)" class="sound-test-chip">🚨防御起飞</button>
                    <button type="button" onclick="playAlertSound('5m_entry', true)" class="sound-test-chip">🎯5m</button>
                    <button type="button" onclick="playAlertSound('portfolio', true)" class="sound-test-chip">💼风控</button>
                    <button type="button" onclick="playAlertSound('golden_pit', true)" class="sound-test-chip">🟢黄金坑</button>
                    <button type="button" onclick="playAlertSound('stop_loss', true)" class="sound-test-chip">🔴破位</button>
                    <button type="button" onclick="playAlertSound('take_profit', true)" class="sound-test-chip">🟣止盈</button>
                    <button type="button" onclick="playAlertSound('macro_weather', true)" class="sound-test-chip">⛅气象</button>
                </div>
            </div>
            <div class="notif-category-wrap">
                <button type="button" class="notif-scroll-btn notif-scroll-prev" id="notifScrollLeftBtn" onclick="scrollNotifCategory(-150)" title="向左滚动分类">&lt;</button>
                <div class="notif-category-bar" id="notifCategoryBar">
                    <button type="button" class="n-tab active" data-cat="all" onclick="filterNotifCategory('all')">全部 (<span id="catCount_all">0</span>)</button>
                    <button type="button" class="n-tab" data-cat="macro_flash" onclick="filterNotifCategory('macro_flash')">⚡ 实时快讯 (<span id="catCount_macro_flash">0</span>)</button>
                    <button type="button" class="n-tab" data-cat="defensive_flight" onclick="filterNotifCategory('defensive_flight')">🚨 防御起飞 (<span id="catCount_defensive_flight">0</span>)</button>
                    <button type="button" class="n-tab" data-cat="5m_entry" onclick="filterNotifCategory('5m_entry')">🎯 5m买点 (<span id="catCount_5m_entry">0</span>)</button>
                    <button type="button" class="n-tab" data-cat="portfolio" onclick="filterNotifCategory('portfolio')">🛡️ 持仓风控 (<span id="catCount_portfolio">0</span>)</button>
                    <button type="button" class="n-tab" data-cat="golden_pit" onclick="filterNotifCategory('golden_pit')">🟢 黄金坑 (<span id="catCount_golden_pit">0</span>)</button>
                    <button type="button" class="n-tab" data-cat="stop_loss" onclick="filterNotifCategory('stop_loss')">🔴 破位预警 (<span id="catCount_stop_loss">0</span>)</button>
                    <button type="button" class="n-tab" data-cat="take_profit" onclick="filterNotifCategory('take_profit')">🟣 止盈冲高 (<span id="catCount_take_profit">0</span>)</button>
                    <button type="button" class="n-tab" data-cat="macro_weather" onclick="filterNotifCategory('macro_weather')">⛅ 市场气象 (<span id="catCount_macro_weather">0</span>)</button>
                </div>
                <button type="button" class="notif-scroll-btn notif-scroll-next" id="notifScrollRightBtn" onclick="scrollNotifCategory(150)" title="向右滚动分类">&gt;</button>
            </div>
            <div class="notif-drawer-body" id="notifListContainer"></div>
            <div class="notif-drawer-footer">
                <span>💡 点击任意预警卡片，看板将自动联动跳转至对应标的</span>
            </div>
        </div>
    </div>

    <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
    <script>
        const STOCKS_DATA = __PY_TPL_STOCKS_JSON__;
        const MACRO_DATA = __PY_TPL_MACRO_JSON__;
        const WEATHER_INFO = __PY_TPL_WEATHER_JSON__;
        const INITIAL_WATCHLIST = __PY_TPL_WATCHLIST_JSON__;
        const INITIAL_PORTFOLIO = {};
        const INITIAL_PORTFOLIO_TS = __PY_TPL_PORTFOLIO_TS__;
        const INITIAL_NOTIFICATIONS = __PY_TPL_NOTIFICATIONS_JSON__;
        const AI_PREDICTIONS = __PY_TPL_AI_PREDICTIONS_JSON__;
        const INITIAL_MARKET_NEWS = __PY_TPL_MARKET_NEWS_JSON__;
        const INTRADAY_PATH_FORECASTS = __PY_TPL_INTRADAY_PATH_JSON__;
        const SESSION_CODE = "__PY_TPL_SESSION_CODE__";
        const RESILIENT_LEADERS_INIT = __PY_TPL_LEADERS_JSON__;

        let currentFilter = "all";
        let currentCenterView = "kline";
        let targetWatchlist = Array.isArray(INITIAL_WATCHLIST) ? [...INITIAL_WATCHLIST] : [];
        let userPortfolio = (typeof INITIAL_PORTFOLIO === 'object' && INITIAL_PORTFOLIO !== null) ? {...INITIAL_PORTFOLIO} : {};
        let rawTradingNotifications = Array.isArray(INITIAL_NOTIFICATIONS) ? [...INITIAL_NOTIFICATIONS] : [];
        let latestMarketNews = (typeof INITIAL_MARKET_NEWS === 'object' && INITIAL_MARKET_NEWS !== null) ? INITIAL_MARKET_NEWS : { macro_calendar: [], macro_flashes: [], ticker_news: {} };
        let currentNotifCat = "all";
        let currentTicker = "NVDA";
        let currentInterval = "D";
        let currentStyle = "1";

        function getCurrentTheme() {
            const attr = document.documentElement.getAttribute("data-theme");
            if (attr === "light" || attr === "dark") return attr;
            try {
                return localStorage.getItem("futu_theme") === "light" ? "light" : "dark";
            } catch (e) {
                return "dark";
            }
        }

        function getThemePalette() {
            const isLight = getCurrentTheme() === "light";
            return isLight ? {
                isLight: true,
                pageBg: "#F4F6F9",
                cardBg: "#FFFFFF",
                elevatedBg: "#F1F5F9",
                border: "#E2E8F0",
                text: "#0F172A",
                sub: "#64748B",
                muted: "#94A3B8",
                up: "#059669",
                upBg: "rgba(5, 150, 105, 0.10)",
                upBorder: "rgba(5, 150, 105, 0.30)",
                down: "#DC2626",
                downBg: "rgba(220, 38, 38, 0.10)",
                downBorder: "rgba(220, 38, 38, 0.30)",
                warn: "#D97706",
                warnBg: "rgba(217, 119, 6, 0.10)",
                warnBorder: "rgba(217, 119, 6, 0.30)",
                accent: "#0284C7",
                accentBg: "rgba(2, 132, 199, 0.10)",
                accentBorder: "rgba(2, 132, 199, 0.30)",
                purple: "#7C3AED",
                purpleBg: "rgba(124, 58, 237, 0.10)",
                purpleBorder: "rgba(124, 58, 237, 0.30)",
                svgGrid: "rgba(15, 23, 42, 0.08)",
                svgAxis: "#475569",
                svgAxisMuted: "#64748B",
                svgCorridorFill: "rgba(2, 132, 199, 0.14)",
                svgCorridorStroke: "rgba(2, 132, 199, 0.42)",
                svgP50: "#7C3AED",
                svgActual: "#EA580C",
                svgHover: "rgba(15, 23, 42, 0.35)",
                svgDiv0945: "rgba(217, 119, 6, 0.55)",
                svgDiv0945Text: "#B45309",
                svgDotStroke: "#FFFFFF"
            } : {
                isLight: false,
                pageBg: "#0B0F19",
                cardBg: "#121826",
                elevatedBg: "#1A2234",
                border: "rgba(255, 255, 255, 0.08)",
                text: "#F3F4F6",
                sub: "#94A3B8",
                muted: "#64748B",
                up: "#10B981",
                upBg: "rgba(16, 185, 129, 0.14)",
                upBorder: "rgba(16, 185, 129, 0.34)",
                down: "#EF4444",
                downBg: "rgba(239, 68, 68, 0.14)",
                downBorder: "rgba(239, 68, 68, 0.34)",
                warn: "#F59E0B",
                warnBg: "rgba(245, 158, 11, 0.14)",
                warnBorder: "rgba(245, 158, 11, 0.34)",
                accent: "#38BDF8",
                accentBg: "rgba(56, 189, 248, 0.14)",
                accentBorder: "rgba(56, 189, 248, 0.34)",
                purple: "#A78BFA",
                purpleBg: "rgba(167, 139, 250, 0.14)",
                purpleBorder: "rgba(167, 139, 250, 0.34)",
                svgGrid: "rgba(148, 163, 184, 0.12)",
                svgAxis: "#94A3B8",
                svgAxisMuted: "#64748B",
                svgCorridorFill: "rgba(56, 189, 248, 0.16)",
                svgCorridorStroke: "rgba(56, 189, 248, 0.40)",
                svgP50: "#A78BFA",
                svgActual: "#F97316",
                svgHover: "rgba(226, 232, 240, 0.45)",
                svgDiv0945: "rgba(250, 204, 21, 0.45)",
                svgDiv0945Text: "#FACC15",
                svgDotStroke: "#FFFFFF"
            };
        }

        function updateThemeToggleUI(theme) {
            const isLight = theme === "light";
            const icon = document.getElementById("themeToggleIcon");
            const label = document.getElementById("themeToggleLabel");
            const btn = document.getElementById("btnThemeToggle");
            const meta = document.getElementById("metaThemeColor");
            if (icon) icon.innerText = isLight ? "🌙" : "☀️";
            if (label) label.innerText = isLight ? "深色" : "浅色";
            if (btn) btn.title = isLight ? "切换到深色模式" : "切换到浅色模式";
            if (meta) meta.setAttribute("content", isLight ? "#F4F6F9" : "#0B0F19");
        }

        function applyTheme(theme, skipReload = false) {
            const nextTheme = (theme === "light") ? "light" : "dark";
            document.documentElement.setAttribute("data-theme", nextTheme);
            try {
                localStorage.setItem("futu_theme", nextTheme);
            } catch (e) {}
            updateThemeToggleUI(nextTheme);
            updateTopRegimeAndScorecard();
            if (!skipReload) {
                renderWatchlist(currentFilter);
                selectTicker(currentTicker, false);
                renderNotifList(currentNotifCat);
            }
        }

        function toggleTheme() {
            const next = getCurrentTheme() === "dark" ? "light" : "dark";
            applyTheme(next, false);
            showToast(next === "light" ? "☀️ 已切换为浅色清爽模式" : "🌙 已切换为深色暗黑模式");
        }

        function toggleMacroStrip() {
            const wrap = document.getElementById("macroStripWrap");
            if (wrap) wrap.classList.toggle("expanded");
        }

        function toggleMoreFilters(forceState) {
            const bar = document.getElementById("watchlistCategories");
            const btn = document.getElementById("btnToggleMoreFilters");
            if (!bar) return;
            const willOpen = (typeof forceState === "boolean") ? forceState : !bar.classList.contains("is-open");
            bar.classList.toggle("is-open", willOpen);
            if (btn) {
                btn.classList.toggle("active", willOpen);
                btn.innerText = willOpen ? "赛道 ▴" : "赛道 ▾";
            }
        }

        function toggleProQuantDrawer(forceState) {
            const drawer = document.getElementById("proQuantDrawer");
            const btn = document.getElementById("btnProQuantToggle");
            const lbl = document.getElementById("proDrawerToggleLabel");
            if (!drawer) return;
            const willOpen = (typeof forceState === "boolean") ? forceState : !drawer.classList.contains("is-open");
            drawer.classList.toggle("is-open", willOpen);
            if (btn) btn.setAttribute("aria-expanded", willOpen ? "true" : "false");
            if (lbl) lbl.innerText = willOpen ? "🔼 收起专业量化数据 ▴" : "🔍 展开专业量化数据 ▾";
            try {
                localStorage.setItem("futu_pro_drawer_open", willOpen ? "true" : "false");
            } catch (e) {}
        }

        function updateTopRegimeAndScorecard() {
            const pal = getThemePalette();
            const regEl = document.getElementById("pAiRegimePill");
            if (regEl) {
                const regCode = (AI_PREDICTIONS && (AI_PREDICTIONS.market_regime || (AI_PREDICTIONS.macro_weather && AI_PREDICTIONS.macro_weather.market_regime))) || "NEUTRAL_CHOP";
                const regCn = (AI_PREDICTIONS && (AI_PREDICTIONS.market_regime_cn || (AI_PREDICTIONS.macro_weather && AI_PREDICTIONS.macro_weather.market_regime_cn))) || "🟡 震荡分化";
                const brPct = (AI_PREDICTIONS && AI_PREDICTIONS.breadth_ma20_pct !== undefined) ? Number(AI_PREDICTIONS.breadth_ma20_pct) : 50.0;
                if (regCode === "BEAR_DEFENSE") {
                    regEl.innerText = "🔴 市场偏弱 · 建议多看少动";
                    regEl.style.color = pal.down;
                    regEl.style.background = pal.downBg;
                    regEl.style.borderColor = pal.downBorder;
                } else if (regCode === "BULL_TREND") {
                    regEl.innerText = "🟢 多头顺风 · 顺势低吸";
                    regEl.style.color = pal.up;
                    regEl.style.background = pal.upBg;
                    regEl.style.borderColor = pal.upBorder;
                } else {
                    regEl.innerText = "🟡 震荡分化 · 轻仓精选";
                    regEl.style.color = pal.warn;
                    regEl.style.background = pal.warnBg;
                    regEl.style.borderColor = pal.warnBorder;
                }
                regEl.title = `${regCn} · 站上20日线比例 ${brPct.toFixed(0)}%`;
            }

            const scEl = document.getElementById("pAiScorecardPill");
            if (scEl) {
                const scObj = (AI_PREDICTIONS && AI_PREDICTIONS.live_scorecard) ? AI_PREDICTIONS.live_scorecard : null;
                if (scObj && scObj.sample_count_7d !== undefined) {
                    const wr7 = Number(scObj.win_rate_7d_pct || 0);
                    const wr30 = Number(scObj.win_rate_30d_pct || 0);
                    const isDeg = Boolean(scObj.is_degraded || (Number(scObj.sample_count_7d || 0) > 0 && wr7 < 45.0));
                    if (isDeg) {
                        scEl.innerText = `⚠️ 近7日胜率 ${wr7.toFixed(1)}% · 降权参考`;
                        scEl.style.color = pal.warn;
                        scEl.style.background = pal.warnBg;
                        scEl.style.borderColor = pal.warnBorder;
                    } else {
                        scEl.innerText = `近7日胜率 ${wr7.toFixed(1)}%`;
                        scEl.style.color = pal.up;
                        scEl.style.background = pal.upBg;
                        scEl.style.borderColor = pal.upBorder;
                    }
                    scEl.title = `AI 实盘成绩单 · 近7日:${wr7.toFixed(1)}% | 近30日:${wr30.toFixed(1)}%`;
                } else {
                    scEl.innerText = "近7日胜率 初始化中";
                    scEl.style.color = pal.sub;
                    scEl.style.background = pal.elevatedBg;
                    scEl.style.borderColor = pal.border;
                }
            }

            const vixPill = document.getElementById("pVixRegimePill");
            const vixPop = document.getElementById("vixDetailPopover");
            const vr = (WEATHER_INFO && WEATHER_INFO.vix_regime && WEATHER_INFO.vix_regime.vix_spot !== undefined)
                ? WEATHER_INFO.vix_regime
                : ((AI_PREDICTIONS && (AI_PREDICTIONS.vix_regime || (AI_PREDICTIONS.macro_weather && AI_PREDICTIONS.macro_weather.vix_regime))) || null);
            if (vixPill) {
                if (vr && vr.pill_text) {
                    vixPill.innerText = String(vr.pill_text);
                    const lvl = String(vr.pill_level || "neutral");
                    if (lvl === "danger") {
                        vixPill.style.color = pal.down;
                        vixPill.style.background = pal.downBg;
                        vixPill.style.borderColor = pal.downBorder;
                    } else if (lvl === "bottom") {
                        vixPill.style.color = pal.up;
                        vixPill.style.background = pal.upBg;
                        vixPill.style.borderColor = pal.upBorder;
                    } else if (lvl === "warn") {
                        vixPill.style.color = pal.warn;
                        vixPill.style.background = pal.warnBg;
                        vixPill.style.borderColor = pal.warnBorder;
                    } else {
                        vixPill.style.color = pal.sub;
                        vixPill.style.background = pal.elevatedBg;
                        vixPill.style.borderColor = pal.border;
                    }
                    vixPill.title = vr.summary_cn || "全局真 VIX 恐慌中枢";
                } else {
                    vixPill.innerText = "⚪ VIX 暂缺 · 常规门禁";
                    vixPill.style.color = pal.sub;
                    vixPill.style.background = pal.elevatedBg;
                    vixPill.style.borderColor = pal.border;
                }
            }
            if (vixPop) {
                const fmtNum = (val, dec, suf = "") => (val !== null && val !== undefined && !Number.isNaN(Number(val))) ? `${Number(val).toFixed(dec)}${suf}` : "--";
                const vrObj = vr || {};
                const ratioNum = (vrObj.vix_term_ratio !== null && vrObj.vix_term_ratio !== undefined) ? Number(vrObj.vix_term_ratio) : null;
                const ratioClr = (ratioNum !== null && ratioNum >= 1.0) ? pal.down : ((ratioNum !== null && ratioNum >= 0.92) ? pal.warn : pal.up);
                const spNum = (vrObj.vxn_vix_spread !== null && vrObj.vxn_vix_spread !== undefined && !Number.isNaN(Number(vrObj.vxn_vix_spread))) ? Number(vrObj.vxn_vix_spread) : null;
                const spStr = spNum !== null ? `${spNum >= 0 ? "+" : ""}${spNum.toFixed(2)}` : "--";
                const spClr = (spNum !== null && spNum >= 5.5) ? pal.down : ((spNum !== null && spNum >= 3.5) ? pal.warn : pal.text);
                const techLbl = escapeNewsHtml(vrObj.tech_fear_label || "⚪ 科技波动平稳");
                const termCn = escapeNewsHtml(vrObj.term_structure_cn || "远期监测中");
                const advCn = escapeNewsHtml(vrObj.advice_cn || "比值回落至 0.97 以下、恐慌自 10 日高点回撤 ≥5% 且当日 VIX 未反扑（≤+3%）时开启右侧抄底窗。");
                vixPop.innerHTML = `
                    <div class="vix-pop-head">
                        <span>⚡ 全局真 VIX 恐慌中枢</span>
                        <span style="font-size: 11px; color: ${ratioClr}; font-weight: 700;">${termCn}</span>
                    </div>
                    <div class="vix-pop-grid tabular">
                        <div class="vix-pop-cell"><span class="vix-pop-lbl">^VIX (标普恐慌)</span><span class="vix-pop-val">${fmtNum(vrObj.vix_spot, 2)}</span></div>
                        <div class="vix-pop-cell"><span class="vix-pop-lbl">^VIX3M (3M远期)</span><span class="vix-pop-val">${fmtNum(vrObj.vix3m_spot, 2)}</span></div>
                        <div class="vix-pop-cell"><span class="vix-pop-lbl">纳指恐慌 (^VXN)</span><span class="vix-pop-val">${fmtNum(vrObj.vxn_spot, 2)}</span></div>
                        <div class="vix-pop-cell"><span class="vix-pop-lbl">科技溢价 (VXN - VIX)</span><span class="vix-pop-val" style="color: ${spClr};">${spStr}</span></div>
                        <div class="vix-pop-cell"><span class="vix-pop-lbl">期限比值 (VIX/VIX3M)</span><span class="vix-pop-val" style="color: ${ratioClr};">${fmtNum(vrObj.vix_term_ratio, 3)}</span></div>
                        <div class="vix-pop-cell"><span class="vix-pop-lbl">^VVIX (波动的波动)</span><span class="vix-pop-val">${fmtNum(vrObj.vvix_spot, 2)}</span></div>
                        <div class="vix-pop-cell"><span class="vix-pop-lbl">10Y 美债 (^TNX)</span><span class="vix-pop-val">${fmtNum(vrObj.tnx_yield, 2, "%")}</span></div>
                        <div class="vix-pop-cell"><span class="vix-pop-lbl">10日峰值 / 回撤</span><span class="vix-pop-val">${fmtNum(vrObj.vix_10d_peak, 2)} / -${fmtNum(vrObj.vix_drawdown_from_peak_pct, 1, "%")}</span></div>
                    </div>
                    <div class="vix-pop-tech">${techLbl}</div>
                    <div class="vix-pop-advice">${advCn}</div>
                `;
            }
            renderResilientLeadersStrip();
        }

        const LEADER_EXCLUDED_TICKERS = new Set(["SQQQ", "SOXS", "VIXY", "TLT", "GLD", "XAUUSD", "BTC-USD", "SHY", "IEF", "HYG", "UUP"]);
        const LEADER_EXCLUDED_CATS = new Set(["大宗商品", "数字资产", "避险美债", "恐慌指数", "反向做空"]);

        function computeResilientLeadersTop5() {
            const unified = [];
            const seen = new Set();
            (Array.isArray(STOCKS_DATA) ? STOCKS_DATA : []).forEach(s => {
                if (s && s.ticker && !seen.has(s.ticker)) {
                    seen.add(s.ticker);
                    unified.push({ ...s, is_stock: true });
                }
            });
            Object.keys(MACRO_DATA || {}).forEach(t => {
                if (!seen.has(t) && MACRO_DATA[t]) {
                    seen.add(t);
                    unified.push(buildMacroListEntry(t, MACRO_DATA[t]));
                }
            });

            const effPct = (it) => {
                if (SESSION_CODE === "REGULAR") {
                    const v = Number(it.pct_change || 0);
                    return Number.isFinite(v) ? v : 0;
                }
                const ep = Number(it.ext_price || 0);
                if (ep > 0 && it.ext_pct !== undefined && it.ext_pct !== null) {
                    const ev = Number(it.ext_pct);
                    return Number.isFinite(ev) ? ev : 0;
                }
                const v = Number(it.pct_change || 0);
                return Number.isFinite(v) ? v : 0;
            };

            const spyObj = unified.find(u => u.ticker === "SPY");
            const spyPct = spyObj ? effPct(spyObj) : 0;

            const ranked = [];
            unified.forEach(it => {
                const tk = String(it.ticker || "").toUpperCase();
                const cat = String(it.cat || "");
                if (!tk || LEADER_EXCLUDED_TICKERS.has(tk) || LEADER_EXCLUDED_CATS.has(cat)) return;
                if (it.is_inverse_hedge || it.is_crash_halt || it.is_cooling_halt || it.is_ipo_pending) return;
                const activePx = Number(it.active_price || it.last || 0);
                if (!(activePx > 0)) return;

                const tkPct = effPct(it);
                const exc = tkPct - spyPct;
                const rsRaw = Number(it.rs_20d !== undefined && it.rs_20d !== null ? it.rs_20d : 0);
                const rs = Number.isFinite(rsRaw) ? rsRaw : 0;
                const vwapVal = Number(it.vwap || 0);

                let aboveVwap = false;
                let vwapTag = "";
                if (SESSION_CODE === "REGULAR") {
                    aboveVwap = (vwapVal > 0 && activePx >= vwapVal);
                    vwapTag = aboveVwap ? "✓VWAP" : "VWAP下";
                } else {
                    aboveVwap = (tkPct > 0) || (exc > 0.3);
                    vwapTag = (exc > 0.3) ? "✓强于SPY" : (tkPct > 0 ? "✓正涨幅" : "偏弱");
                }

                const rsC = Math.max(-30, Math.min(40, rs));
                const excC = Math.max(-15, Math.min(20, exc));
                const score = (aboveVwap ? 25 : 0) + rsC * 1.2 + excC * 2.0;
                ranked.push({
                    entry: it,
                    ticker: tk,
                    name: it.name || tk,
                    rs_20d: rs,
                    excess_vs_spy: exc,
                    above_vwap: aboveVwap,
                    vwap_tag: vwapTag,
                    leader_score: score,
                });
            });

            ranked.sort((a, b) => {
                if (a.above_vwap !== b.above_vwap) return a.above_vwap ? -1 : 1;
                if (b.leader_score !== a.leader_score) return b.leader_score - a.leader_score;
                if (b.rs_20d !== a.rs_20d) return b.rs_20d - a.rs_20d;
                return b.excess_vs_spy - a.excess_vs_spy;
            });
            return ranked.slice(0, 5);
        }

        function renderResilientLeadersStrip() {
            const strip = document.getElementById("resilientLeadersStrip");
            if (!strip) return;
            const vr = (WEATHER_INFO && WEATHER_INFO.vix_regime && WEATHER_INFO.vix_regime.vix_spot !== undefined)
                ? WEATHER_INFO.vix_regime
                : ((AI_PREDICTIONS && (AI_PREDICTIONS.vix_regime || (AI_PREDICTIONS.macro_weather && AI_PREDICTIONS.macro_weather.vix_regime))) || null);
            const vixState = String((vr && vr.vix_state) || "").toUpperCase();
            const showStates = new Set(["ELEVATED_CAUTION", "BACKWARDATION_BRAKE", "WASHOUT_BOTTOM_WINDOW", "BOTTOM_PREMARKET_PREVIEW"]);
            if (!showStates.has(vixState)) {
                strip.style.display = "none";
                strip.innerHTML = "";
                return;
            }
            const top5 = computeResilientLeadersTop5();
            if (!top5.length) {
                strip.style.display = "none";
                strip.innerHTML = "";
                return;
            }
            const pal = getThemePalette();
            const chipsHtml = top5.map(ld => {
                const isAct = (ld.ticker === currentTicker) ? " active" : "";
                const rsStr = `${ld.rs_20d >= 0 ? "+" : ""}${ld.rs_20d.toFixed(1)}%`;
                const tagClr = ld.above_vwap ? pal.up : pal.sub;
                return `<button type="button" class="leader-chip${isAct}" data-ticker="${escapeNewsHtml(ld.ticker)}" onclick="selectTicker('${escapeNewsHtml(ld.ticker)}', true)" title="${escapeNewsHtml(ld.name)} · 相对SPY ${ld.excess_vs_spy >= 0 ? '+' : ''}${ld.excess_vs_spy.toFixed(2)}%"><b>${escapeNewsHtml(ld.ticker)}</b><span>RS ${rsStr}</span><span class="lc-tag" style="color:${tagClr};">${escapeNewsHtml(ld.vwap_tag)}</span></button>`;
            }).join("");
            strip.innerHTML = `<span class="leaders-strip-lbl">🛡️ 抗跌领涨 Top 5</span>${chipsHtml}`;
            strip.style.display = "flex";
        }

        function toggleVixPopover(ev) {
            if (ev && ev.stopPropagation) ev.stopPropagation();
            const pop = document.getElementById("vixDetailPopover");
            const pill = document.getElementById("pVixRegimePill");
            if (!pop || !pill) return;
            const willOpen = !pop.classList.contains("is-open");
            pop.classList.toggle("is-open", willOpen);
            if (!willOpen) pop.classList.remove("is-hover-open");
            pill.setAttribute("aria-expanded", willOpen ? "true" : "false");
        }
        (function bindVixPillInteractions() {
            document.addEventListener("DOMContentLoaded", function() {
                const wrap = document.getElementById("vixPillWrap");
                const pop = document.getElementById("vixDetailPopover");
                if (wrap && pop && window.matchMedia && window.matchMedia("(hover: hover)").matches) {
                    wrap.addEventListener("mouseenter", function() { pop.classList.add("is-hover-open"); });
                    wrap.addEventListener("mouseleave", function() { pop.classList.remove("is-hover-open"); });
                }
            });
            document.addEventListener("click", function(e) {
                const wrap = document.getElementById("vixPillWrap");
                const pop = document.getElementById("vixDetailPopover");
                const pill = document.getElementById("pVixRegimePill");
                if (!wrap || !pop) return;
                if (!wrap.contains(e.target) && (pop.classList.contains("is-open") || pop.classList.contains("is-hover-open"))) {
                    pop.classList.remove("is-open");
                    pop.classList.remove("is-hover-open");
                    if (pill) pill.setAttribute("aria-expanded", "false");
                }
            });
            document.addEventListener("keydown", function(e) {
                if (e && e.key === "Escape") {
                    const pop = document.getElementById("vixDetailPopover");
                    const pill = document.getElementById("pVixRegimePill");
                    if (pop) { pop.classList.remove("is-open"); pop.classList.remove("is-hover-open"); }
                    if (pill) pill.setAttribute("aria-expanded", "false");
                }
            });
        })();

        function escapeNewsHtml(str) {
            if (str === null || str === undefined) return "";
            return String(str)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#39;");
        }

        function buildMacroFlashNotifs(newsObj) {
            const flashes = (newsObj && Array.isArray(newsObj.macro_flashes)) ? newsObj.macro_flashes : [];
            return flashes.map(f => ({
                id: "flash_" + String(f.id || ""),
                category: "macro_flash",
                level: "info",
                title: f.title || "⚡ 宏观行业快讯",
                desc: f.summary || "",
                time: f.time_display || f.time_et || f.time_bj || "",
                keywords: Array.isArray(f.keywords) ? f.keywords : [],
                is_flash: true,
                _safeText: true
            }));
        }

        function mergeNotificationsWithFlashes(tradingList, newsObj) {
            const tArr = Array.isArray(tradingList) ? tradingList : [];
            const fArr = buildMacroFlashNotifs(newsObj);
            return [...tArr, ...fArr];
        }

        let terminalNotifications = mergeNotificationsWithFlashes(rawTradingNotifications, latestMarketNews);

        function openMacroFlashDrawer() {
            const drawer = document.getElementById("notifDrawerOverlay");
            if (drawer && drawer.style.display !== "flex") {
                drawer.style.display = "flex";
            }
            filterNotifCategory("macro_flash");
        }

        function refreshMacroBannerDOM(newsObj) {
            if (!newsObj || !newsObj.calendar_banner) return;
            const hlEl = document.getElementById("macroCalHeadline");
            const dtEl = document.getElementById("macroCalDetail");
            const bnEl = document.getElementById("macroCalendarBanner");
            if (hlEl && newsObj.calendar_banner.headline) hlEl.innerText = newsObj.calendar_banner.headline;
            if (dtEl && newsObj.calendar_banner.detail !== undefined) dtEl.innerText = newsObj.calendar_banner.detail;
            if (bnEl && newsObj.calendar_banner.level) bnEl.setAttribute("data-level", newsObj.calendar_banner.level);
        }

        function renderTickerNewsBox(ticker, dataObj) {
            const pal = getThemePalette();
            const badgeEl = document.getElementById("pNewsTickerBadge");
            const earnEl = document.getElementById("pNewsEarningsStatus");
            const listEl = document.getElementById("pTickerNewsList");
            if (badgeEl) badgeEl.innerText = ticker || "--";

            const isVixy = (ticker === "VIXY");
            const isEtf = !!(typeof MACRO_DATA !== "undefined" && MACRO_DATA && MACRO_DATA[ticker]) || (dataObj && dataObj.cat === "ETF基准");
            const isMacroSpot = ["XAUUSD", "BTC-USD"].includes(ticker) || (dataObj && dataObj.cat === "大宗商品");
            if (earnEl) {
                if (isVixy) {
                    earnEl.innerText = "📉 波动率期货工具（含展期损耗·无财报）";
                    earnEl.style.color = pal.accent;
                    earnEl.style.borderColor = pal.border;
                    earnEl.style.background = pal.elevatedBg;
                } else if (dataObj && dataObj.earnings_tag) {
                    earnEl.innerText = dataObj.is_earnings_risk
                        ? `🚨 高危财报排雷：${dataObj.earnings_tag}（防财报跳空插针）`
                        : `📅 财报倒计时：${dataObj.earnings_tag}`;
                    earnEl.style.color = dataObj.is_earnings_risk ? pal.down : pal.warn;
                    earnEl.style.borderColor = dataObj.is_earnings_risk ? pal.downBorder : pal.warnBorder;
                    earnEl.style.background = dataObj.is_earnings_risk ? pal.downBg : pal.warnBg;
                } else {
                    earnEl.innerText = isMacroSpot ? "🏛️ 关注宏观 / 美联储 FOMC & CPI 日历（无公司财报）" : (isEtf ? "📊 宏观/行业指数 ETF（无单一财报日风险）" : "🟢 近期无财报雷区");
                    earnEl.style.color = (isEtf || isMacroSpot) ? pal.accent : pal.up;
                    earnEl.style.borderColor = pal.border;
                    earnEl.style.background = pal.elevatedBg;
                }
            }

            if (!listEl) return;
            const tNewsMap = (latestMarketNews && latestMarketNews.ticker_news) ? latestMarketNews.ticker_news : {};
            const items = Array.isArray(tNewsMap[ticker]) ? tNewsMap[ticker] : [];
            if (items.length > 0) {
                listEl.innerHTML = items.slice(0, 5).map(item => {
                    const safeUrl = (item.url && /^https?:/i.test(String(item.url).trim())) ? escapeNewsHtml(String(item.url).trim()) : "#";
                    const safeTitle = escapeNewsHtml(item.title || "最新资讯");
                    const safeSrc = escapeNewsHtml(item.source || "Market News");
                    const safeTime = escapeNewsHtml(item.time || "--");
                    return `
                        <a class="t-news-item" href="${safeUrl}" target="_blank" rel="noopener noreferrer">
                            <div class="t-news-title">${safeTitle} ↗</div>
                            <div class="t-news-meta">
                                <span>📰 ${safeSrc}</span>
                                <span class="tabular">${safeTime}</span>
                            </div>
                        </a>
                    `;
                }).join("");
                return;
            }

            const flashes = (latestMarketNews && Array.isArray(latestMarketNews.macro_flashes)) ? latestMarketNews.macro_flashes.slice(0, 3) : [];
            if (flashes.length > 0) {
                const fallbackHdr = `<div style="font-size: 10.5px; color: ${pal.accent}; margin-bottom: 2px;">⚡ ETF/暂无个股直连新闻 · 自动切换最近宏观快讯</div>`;
                const flashCards = flashes.map(f => {
                    const kws = (Array.isArray(f.keywords) ? f.keywords.slice(0, 3) : []).map(k => `<span class="flash-kw-pill">[${escapeNewsHtml(k)}]</span>`).join("");
                    return `
                        <div class="t-news-item" onclick="openMacroFlashDrawer()" style="cursor: pointer;">
                            <div style="margin-bottom: 3px;">${kws}</div>
                            <div class="t-news-title">${escapeNewsHtml(f.summary || f.title || "")}</div>
                            <div class="t-news-meta">
                                <span>⚡ 宏观快讯</span>
                                <span class="tabular">${escapeNewsHtml(f.time_display || "--")}</span>
                            </div>
                        </div>
                    `;
                }).join("");
                listEl.innerHTML = fallbackHdr + flashCards;
            } else {
                listEl.innerHTML = `<div style="color: ${pal.muted}; padding: 6px 0;">暂无 ${escapeNewsHtml(ticker)} 近期新闻与宏观快讯</div>`;
            }
        }

        try {
            localStorage.setItem("targetWatchlist", JSON.stringify(targetWatchlist));
        } catch(e) {}

        const API_BASE = window.location.protocol.startsWith("http") ? window.location.origin : "http://127.0.0.1:8765";

        function syncPortfolioToRuntimeData(port) {
            if (!port || typeof port !== "object") return;
            Object.keys(MACRO_DATA).forEach(k => {
                MACRO_DATA[k].shares = 0;
                MACRO_DATA[k].trim1 = 0;
                MACRO_DATA[k].trim1_shares = 0;
                MACRO_DATA[k].trim2 = 0;
            });
            STOCKS_DATA.forEach(st => {
                st.shares = 0;
                st.trim1 = 0;
                st.trim1_shares = 0;
                st.trim2 = 0;
            });
            Object.keys(port).forEach(t => {
                const p = port[t];
                if (MACRO_DATA[t]) Object.assign(MACRO_DATA[t], p);
                const st = STOCKS_DATA.find(x => x.ticker === t);
                if (st) Object.assign(st, p);
            });
        }

        fetch(`${API_BASE}/api/notifications`)
            .then(r => r.json())
            .then(res => {
                if (res && Array.isArray(res.notifications)) {
                    handleIncomingNotifications(res.notifications);
                }
            })
            .catch(() => {});

        // TradingView 交易所标的代码映射 (全量 79 只)
        const SYMBOL_MAP = {
            "QQQ": "NASDAQ:QQQ",
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
            "VIXY": "AMEX:VIXY",
            "SQQQ": "NASDAQ:SQQQ",
            "SOXS": "AMEX:SOXS",
            "NVDA": "NASDAQ:NVDA",
            "TSM": "NYSE:TSM",
            "AMD": "NASDAQ:AMD",
            "AVGO": "NASDAQ:AVGO",
            "QCOM": "NASDAQ:QCOM",
            "ASML": "NASDAQ:ASML",
            "AMAT": "NASDAQ:AMAT",
            "MSFT": "NASDAQ:MSFT",
            "GOOGL": "NASDAQ:GOOGL",
            "META": "NASDAQ:META",
            "AAPL": "NASDAQ:AAPL",
            "AMZN": "NASDAQ:AMZN",
            "PLTR": "NASDAQ:PLTR",
            "CRWV": "NASDAQ:CRWV",
            "NBIS": "NASDAQ:NBIS",
            "VST": "NYSE:VST",
            "BE": "NYSE:BE",
            "NOW": "NYSE:NOW",
            "APLD": "NASDAQ:APLD",
            "CRDO": "NASDAQ:CRDO",
            "COHR": "NYSE:COHR",
            "MRVL": "NASDAQ:MRVL",
            "AAOI": "NASDAQ:AAOI",
            "AXTI": "NASDAQ:AXTI",
            "LITE": "NASDAQ:LITE",
            "FN": "NYSE:FN",
            "GLW": "NYSE:GLW",
            "SPCX": "NASDAQ:SPCX",
            "ASTS": "NASDAQ:ASTS",
            "RKLB": "NASDAQ:RKLB",
            "LUNR": "NASDAQ:LUNR",
            "FLY": "NASDAQ:FLY",
            "NASA": "AMEX:NASA",
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
            "BTC-USD": "COINBASE:BTCUSD",
            "COIN": "NASDAQ:COIN",
            "WDC": "NASDAQ:WDC",
            "SNDK": "NASDAQ:SNDK",
            "SKHY": "NASDAQ:SKHY",
            "STX": "NASDAQ:STX",
            "ORCL": "NYSE:ORCL",
            "LLY": "NYSE:LLY",
            "MRNA": "NASDAQ:MRNA",
            "MCD": "NYSE:MCD",
            "NKE": "NYSE:NKE",
            "V": "NYSE:V",
            "PEP": "NASDAQ:PEP",
            "JPM": "NYSE:JPM",
            "XOM": "NYSE:XOM",
            "WMT": "NYSE:WMT",
            "CAT": "NYSE:CAT",
            "XAUUSD": "OANDA:XAUUSD",
            "ANT": "NASDAQ:ANT"
        };

        function loadTvWidget(ticker, interval, style) {
            const container = document.getElementById("tv_chart_container");
            if (!container) return;
            container.innerHTML = "";
            const tvSymbol = SYMBOL_MAP[ticker] || ("NASDAQ:" + ticker);
            const targetSession = "extended";
            const tvTheme = getCurrentTheme();
            const toolbarBg = tvTheme === "light" ? "#FFFFFF" : "#121826";
            container.dataset.tvTheme = tvTheme;

            try {
                new TradingView.widget({
                    "autosize": true,
                    "symbol": tvSymbol,
                    "interval": interval,
                    "timezone": "America/New_York",
                    "theme": tvTheme,
                    "style": style || "1",
                    "locale": "zh_CN",
                    "toolbar_bg": toolbarBg,
                    "enable_publishing": false,
                    "hide_side_toolbar": false,
                    "allow_symbol_change": true,
                    "save_image": false,
                    "extended_hours": true,
                    "container_id": "tv_chart_container",
                    "enabled_features": ["pre_post_market_sessions"],
                    "overrides": { "mainSeriesProperties.sessionId": targetSession },
                    "studies": ["MASimple@tv-basicstudies", "MACD@tv-basicstudies", "RSI@tv-basicstudies"]
                });
            } catch(e) {
                const tbHex = tvTheme === "light" ? "FFFFFF" : "121826";
                const iframeUrl = `https://s.tradingview.com/widgetembed/?frameElementId=tv_widget&symbol=${encodeURIComponent(tvSymbol)}&interval=${interval}&hidesidetoolbar=0&symboledit=1&saveimage=0&toolbarbg=${tbHex}&theme=${tvTheme}&style=${style || '1'}&timezone=America%2FNew_York&locale=zh_CN&extended_hours=1&sessionId=${targetSession}`;
                container.innerHTML = `<iframe src="${iframeUrl}" data-tv-theme="${tvTheme}" style="width:100%; height:100%; border:none;"></iframe>`;
            }
        }

        function _ipNum(v, fb = null) {
            const n = Number(v);
            return Number.isFinite(n) ? n : fb;
        }

        function updateCenterViewMeta(ticker) {
            const metaEl = document.getElementById("centerViewMeta");
            if (!metaEl) return;
            const pal = getThemePalette();
            const byTk = (INTRADAY_PATH_FORECASTS && INTRADAY_PATH_FORECASTS.by_ticker) ? INTRADAY_PATH_FORECASTS.by_ticker : {};
            const item = byTk[ticker];
            if (!item) {
                metaEl.innerText = "盘前走廊推演就绪";
                metaEl.style.color = pal.sub;
                return;
            }
            const divObj = item.divergence || {};
            metaEl.innerText = divObj.badge || item.summary_text || "盘前走廊推演就绪";
            metaEl.style.color = (pal.isLight && (!divObj.color || divObj.color === "#60a5fa")) ? pal.accent : (divObj.color || pal.sub);
        }

        function switchCenterChartView(view) {
            currentCenterView = (view === "intraday_path") ? "intraday_path" : "kline";
            const chartMid = document.getElementById("chartMiddle");
            const tabK = document.getElementById("tabKlineView");
            const tabI = document.getElementById("tabIntradayPathView");
            if (tabK) tabK.classList.toggle("active", currentCenterView === "kline");
            if (tabI) tabI.classList.toggle("active", currentCenterView === "intraday_path");
            if (chartMid) chartMid.classList.toggle("is-intraday-path", currentCenterView === "intraday_path");

            if (currentCenterView === "intraday_path") {
                renderIntradayPathChart(currentTicker);
            } else {
                loadTvWidget(currentTicker, currentInterval, currentStyle);
            }
        }

        function renderIntradayPathChart(ticker) {
            const pal = getThemePalette();
            const sumEl = document.getElementById("ipSummaryText");
            const metaEl = document.getElementById("ipMetaTag");
            const divEl = document.getElementById("ipDivergenceBadge");
            const lvBar = document.getElementById("ipLevelsBar");
            const wrapEl = document.getElementById("ipChartWrap");
            const svgEl = document.getElementById("ipChartSvg");
            const fbEl = document.getElementById("ipFallbackBox");
            const tipEl = document.getElementById("ipTooltip");
            if (!svgEl || !wrapEl || !fbEl) return;
            if (tipEl) tipEl.style.display = "none";

            const rootDoc = INTRADAY_PATH_FORECASTS || {};
            const byTk = rootDoc.by_ticker || {};
            const item = byTk[ticker];
            const timeSlots = Array.isArray(rootDoc.time_slots) && rootDoc.time_slots.length === 78
                ? rootDoc.time_slots
                : Array.from({ length: 78 }, (_, i) => {
                    const tot = 570 + i * 5;
                    return `${String(Math.floor(tot / 60)).padStart(2, "0")}:${String(tot % 60).padStart(2, "0")}`;
                });

            if (!item) {
                if (sumEl) sumEl.innerText = `${ticker} 暂无当日盘中推演数据`;
                if (metaEl) metaEl.innerText = "请等待每日盘前 09:25 ET 或后台增量重建完成";
                if (divEl) {
                    divEl.innerText = "⏳ 等待推演数据";
                    divEl.style.color = pal.sub;
                    divEl.style.borderColor = pal.border;
                    divEl.style.background = pal.elevatedBg;
                }
                if (lvBar) lvBar.innerHTML = "";
                wrapEl.style.display = "none";
                fbEl.style.display = "flex";
                fbEl.innerHTML = `<div style="font-size:24px;">📊</div><div style="font-size:14px; font-weight:700; color:${pal.text};">${escapeNewsHtml(ticker)} 盘中走廊待生成</div><div style="font-size:12px; color:${pal.sub};">下轮扫描将自动匹配近 60 日相似开盘走势</div>`;
                return;
            }

            const divObj = item.divergence || {};
            const divClr = divObj.color || pal.accent;
            if (sumEl) sumEl.innerText = item.summary_text || item.status_message || "--";
            if (metaEl) {
                const ao = _ipNum(item.anchor_open, 0);
                const pc = _ipNum(item.prev_close, 0);
                const lockStr = item.snapshot_locked ? "🔒 09:25盘前走廊已锁定" : "📝 盘前实时预览";
                metaEl.innerText = `目标日 ${item.forecast_date || rootDoc.forecast_date || "--"} · ${lockStr} · 开盘锚 $${ao > 0 ? ao.toFixed(2) : "--"} · 昨收 $${pc > 0 ? pc.toFixed(2) : "--"}`;
            }
            if (divEl) {
                divEl.innerText = divObj.badge || "--";
                if (pal.isLight) {
                    const st = divObj.status || "";
                    if (st === "BELOW_P25") {
                        divEl.style.color = pal.down;
                        divEl.style.borderColor = pal.downBorder;
                        divEl.style.background = pal.downBg;
                    } else if (st === "ABOVE_P75") {
                        divEl.style.color = pal.up;
                        divEl.style.borderColor = pal.upBorder;
                        divEl.style.background = pal.upBg;
                    } else {
                        divEl.style.color = pal.accent;
                        divEl.style.borderColor = pal.accentBorder;
                        divEl.style.background = pal.accentBg;
                    }
                } else {
                    divEl.style.color = divClr;
                    divEl.style.borderColor = divClr + "66";
                    divEl.style.background = divClr + "1f";
                }
            }

            if (item.status !== "ok" || !Array.isArray(item.p50_prices) || item.p50_prices.length < 10) {
                wrapEl.style.display = "none";
                fbEl.style.display = "flex";
                const iconStr = item.status === "continuous_24h" ? "🌐" : "🐣";
                const titleStr = item.status === "continuous_24h" ? "7×24 小时连续交易品种" : "次新上市·分时历史样本积累中";
                const msgStr = item.status_message || item.summary_text || "当前暂不绘制美股 RTH 盘前走廊";
                const stockInfo = STOCKS_DATA.find(s => s.ticker === ticker) || MACRO_DATA[ticker] || {};
                const curPx = _ipNum(stockInfo.active_price || stockInfo.last, _ipNum(item.anchor_open, 0));
                const curVwap = _ipNum(stockInfo.vwap, 0);
                if (lvBar) {
                    lvBar.innerHTML = `<span>📌 标的: <b style="color:${pal.text};">${escapeNewsHtml(ticker)}</b></span><span>现价: <b style="color:${pal.text};">$${curPx > 0 ? curPx.toFixed(2) : "--"}</b></span><span>日内VWAP: <b style="color:${pal.accent};">$${curVwap > 0 ? curVwap.toFixed(2) : "--"}</b></span>`;
                }
                fbEl.innerHTML = `<div style="font-size:28px;">${iconStr}</div><div style="font-size:15px; font-weight:700; color:${pal.text};">${escapeNewsHtml(ticker)} · ${titleStr}</div><div style="font-size:12.5px; color:${pal.sub}; max-width:460px;">${escapeNewsHtml(msgStr)}</div>`;
                return;
            }

            fbEl.style.display = "none";
            wrapEl.style.display = "flex";

            const p25 = item.p25_prices.map(v => _ipNum(v, null));
            const p50 = item.p50_prices.map(v => _ipNum(v, null));
            const p75 = item.p75_prices.map(v => _ipNum(v, null));
            const act = Array.isArray(item.actual_prices) ? item.actual_prices.map(v => _ipNum(v, null)) : new Array(78).fill(null);
            const prevClose = _ipNum(item.prev_close, null);
            const pmh = _ipNum(item.pmh, null);
            const pml = _ipNum(item.pml, null);
            const anchorOpen = _ipNum(item.anchor_open, p50[0] || 100);

            if (lvBar) {
                const fmt = (v) => (v !== null && v > 0) ? `$${v.toFixed(2)}` : "--";
                lvBar.innerHTML = `
                    <span style="display:inline-flex;align-items:center;gap:4px;"><span style="width:10px;height:10px;background:${pal.svgCorridorFill};border:1px solid ${pal.accent};display:inline-block;border-radius:2px;"></span>正常波动带 (P25~P75)</span>
                    <span style="color:${pal.svgP50}; font-weight:600;">┈ 预期中位线 (P50)</span>
                    <span style="color:${pal.svgActual}; font-weight:600;">━ 今日实际走势</span>
                    <span style="color:${pal.sub};">昨收 ${fmt(prevClose)}</span>
                    <span style="color:${pal.warn};">盘前高 ${fmt(pmh)}</span>
                    <span style="color:${pal.accent};">盘前低 ${fmt(pml)}</span>
                `;
            }

            const vals = [];
            [p25, p50, p75, act].forEach(arr => arr.forEach(v => { if (v !== null && v > 0) vals.push(v); }));
            [prevClose, pmh, pml, anchorOpen].forEach(v => { if (v !== null && v > 0) vals.push(v); });
            let minY = vals.length ? Math.min(...vals) : 95;
            let maxY = vals.length ? Math.max(...vals) : 105;
            if (!Number.isFinite(minY) || !Number.isFinite(maxY) || maxY <= minY) {
                const base = Number.isFinite(anchorOpen) && anchorOpen > 0 ? anchorOpen : 100;
                minY = base * 0.98;
                maxY = base * 1.02;
            }
            const spanY = maxY - minY;
            minY = minY - spanY * 0.08;
            maxY = maxY + spanY * 0.08;

            const W = 920, H = 420;
            const pad = { left: 46, right: 86, top: 26, bottom: 38 };
            const plotW = W - pad.left - pad.right;
            const plotH = H - pad.top - pad.bottom;
            const nSlots = Math.max(2, p50.length);
            const xAt = (idx) => pad.left + (idx / (nSlots - 1)) * plotW;
            const yAt = (px) => pad.top + ((maxY - px) / (maxY - minY)) * plotH;

            const parts = [];
            parts.push(`<rect x="0" y="0" width="${W}" height="${H}" fill="${pal.cardBg}" rx="8" />`);

            for (let g = 0; g <= 4; g++) {
                const frac = g / 4;
                const val = maxY - frac * (maxY - minY);
                const y = yAt(val);
                if (!Number.isFinite(y)) continue;
                const pctStr = anchorOpen > 0 ? `${((val - anchorOpen) / anchorOpen * 100 >= 0 ? "+" : "")}${((val - anchorOpen) / anchorOpen * 100).toFixed(1)}%` : "";
                parts.push(`<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="${pal.svgGrid}" stroke-width="1" />`);
                parts.push(`<text x="${W - pad.right + 6}" y="${(y + 3.5).toFixed(1)}" fill="${pal.svgAxis}" font-size="10.5" font-family="monospace">$${val.toFixed(2)}</text>`);
                if (pctStr) {
                    parts.push(`<text x="${pad.left - 5}" y="${(y + 3.5).toFixed(1)}" text-anchor="end" fill="${pal.svgAxisMuted}" font-size="9.5" font-family="monospace">${pctStr}</text>`);
                }
            }

            const xTickIdxs = [0, 6, 12, 18, 24, 30, 36, 42, 48, 54, 60, 66, 72, 77];
            xTickIdxs.forEach(idx => {
                if (idx >= nSlots) return;
                const x = xAt(idx);
                parts.push(`<line x1="${x.toFixed(1)}" y1="${pad.top}" x2="${x.toFixed(1)}" y2="${H - pad.bottom}" stroke="${pal.svgGrid}" stroke-width="1" />`);
                parts.push(`<text x="${x.toFixed(1)}" y="${H - pad.bottom + 15}" text-anchor="middle" fill="${pal.svgAxis}" font-size="10">${timeSlots[idx] || ""}</text>`);
            });
            const x0945 = xAt(3);
            if (Number.isFinite(x0945)) {
                parts.push(`<line x1="${x0945.toFixed(1)}" y1="${pad.top}" x2="${x0945.toFixed(1)}" y2="${H - pad.bottom}" stroke="${pal.svgDiv0945}" stroke-width="1.2" stroke-dasharray="3 3" />`);
                parts.push(`<text x="${(x0945 + 4).toFixed(1)}" y="${pad.top + 12}" fill="${pal.svgDiv0945Text}" font-size="10" font-weight="700">09:45 背离窗</text>`);
            }

            const refLines = [];
            if (prevClose !== null && prevClose > 0) refLines.push({ label: "昨收", val: prevClose, color: pal.svgAxis });
            if (pmh !== null && pmh > 0) refLines.push({ label: "PMH", val: pmh, color: pal.warn });
            if (pml !== null && pml > 0) refLines.push({ label: "PML", val: pml, color: pal.accent });
            refLines.sort((a, b) => yAt(a.val) - yAt(b.val));
            let lastLabelY = -999;
            refLines.forEach(rf => {
                const y = yAt(rf.val);
                if (!Number.isFinite(y)) return;
                const clampedY = Math.max(pad.top + 8, Math.min(H - pad.bottom - 8, y));
                let ly = clampedY;
                if (Math.abs(ly - lastLabelY) < 12) ly = lastLabelY + 12;
                lastLabelY = ly;
                parts.push(`<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="${rf.color}" stroke-width="1.2" stroke-dasharray="5 4" opacity="0.85" />`);
                parts.push(`<text x="${pad.left + 6}" y="${(ly - 3).toFixed(1)}" fill="${rf.color}" font-size="10" font-weight="700">${rf.label} $${rf.val.toFixed(2)}</text>`);
            });

            const upperPts = [];
            const lowerPts = [];
            for (let i = 0; i < nSlots; i++) {
                if (p75[i] !== null && p25[i] !== null) {
                    const x = xAt(i), y75 = yAt(p75[i]), y25 = yAt(p25[i]);
                    if (Number.isFinite(x) && Number.isFinite(y75) && Number.isFinite(y25)) {
                        upperPts.push(`${x.toFixed(1)},${y75.toFixed(1)}`);
                        lowerPts.unshift(`${x.toFixed(1)},${y25.toFixed(1)}`);
                    }
                }
            }
            if (upperPts.length >= 2) {
                parts.push(`<polygon id="ipCorridorPoly" points="${upperPts.concat(lowerPts).join(" ")}" fill="${pal.svgCorridorFill}" stroke="${pal.svgCorridorStroke}" stroke-width="1" />`);
            }

            const p50Pts = [];
            for (let i = 0; i < nSlots; i++) {
                if (p50[i] !== null) {
                    const x = xAt(i), y = yAt(p50[i]);
                    if (Number.isFinite(x) && Number.isFinite(y)) p50Pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
                }
            }
            if (p50Pts.length >= 2) {
                parts.push(`<polyline id="ipP50Line" fill="none" stroke="${pal.svgP50}" stroke-width="2.2" stroke-dasharray="6 4" points="${p50Pts.join(" ")}" />`);
            }

            const actPts = [];
            let lastAct = null;
            for (let i = 0; i < nSlots; i++) {
                if (act[i] !== null && act[i] > 0) {
                    const x = xAt(i), y = yAt(act[i]);
                    if (Number.isFinite(x) && Number.isFinite(y)) {
                        actPts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
                        lastAct = { idx: i, x, y, px: act[i] };
                    }
                }
            }
            if (actPts.length >= 2) {
                parts.push(`<polyline id="ipActualLine" fill="none" stroke="${pal.svgActual}" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round" points="${actPts.join(" ")}" />`);
            }
            if (lastAct !== null) {
                parts.push(`<circle id="ipActualDot" cx="${lastAct.x.toFixed(1)}" cy="${lastAct.y.toFixed(1)}" r="5" fill="${divClr}" stroke="${pal.svgDotStroke}" stroke-width="1.6" />`);
            }

            parts.push(`<line id="ipHoverLine" x1="0" y1="${pad.top}" x2="0" y2="${H - pad.bottom}" stroke="${pal.svgHover}" stroke-width="1" stroke-dasharray="2 2" style="display:none;" />`);
            svgEl.innerHTML = parts.join("");

            svgEl.onmousemove = function(evt) {
                const rect = svgEl.getBoundingClientRect();
                if (!rect || rect.width <= 0) return;
                const relX = ((evt.clientX - rect.left) / rect.width) * W;
                const slotF = ((relX - pad.left) / plotW) * (nSlots - 1);
                const si = Math.max(0, Math.min(nSlots - 1, Math.round(slotF)));
                const hx = xAt(si);
                const hLine = document.getElementById("ipHoverLine");
                if (hLine && Number.isFinite(hx)) {
                    hLine.setAttribute("x1", hx.toFixed(1));
                    hLine.setAttribute("x2", hx.toFixed(1));
                    hLine.style.display = "block";
                }
                if (tipEl) {
                    const f = (v) => (v !== null && Number.isFinite(v)) ? `$${v.toFixed(2)}` : "--";
                    tipEl.innerHTML = `<b>${escapeNewsHtml(ticker)} · ${timeSlots[si] || ""} ET</b><br>实际 5m: <b style="color:${pal.svgActual};">${f(act[si])}</b><br>P50 预期: <b style="color:${pal.svgP50};">${f(p50[si])}</b><br>走廊 [P25~P75]: <span style="color:${pal.accent};">${f(p25[si])} ~ ${f(p75[si])}</span>`;
                    tipEl.style.display = "block";
                }
            };
            svgEl.onmouseleave = function() {
                const hLine = document.getElementById("ipHoverLine");
                if (hLine) hLine.style.display = "none";
                if (tipEl) tipEl.style.display = "none";
            };
        }

        function updateStarUI() {}
        function toggleCurrentStar() {}

        function getPlainStatusBadge(item, pal) {
            const sig = String(item.signal || "");
            const isTakeProfit = Boolean(item.is_take_profit || sig.includes("止盈") || sig.includes("超买") || sig.includes("冲顶"));
            const isStop = Boolean(item.is_stop || sig.includes("破位") || sig.includes("跌破"));
            const isPullback = Boolean(item.is_deep_pullback || sig.includes("回踩"));
            const isIpo = Boolean(item.is_ipo_pending || sig.includes("IPO"));

            if (isIpo) return { label: "🆕 IPO", color: pal.accent, bg: pal.accentBg };
            if (item.intraday_entry_ready) return { label: "🎯买点", color: pal.up, bg: pal.upBg };
            if (item.is_crash_halt || item.is_cooling_halt) return { label: "🔴 别接刀", color: pal.down, bg: pal.downBg };
            if (isTakeProfit) return { label: "🔴 别追", color: pal.purple, bg: pal.purpleBg };
            if (isStop) return { label: "🔴 别追", color: pal.down, bg: pal.downBg };
            if (item.is_pit) return { label: "🟢 可低吸", color: pal.up, bg: pal.upBg };
            if (item.broken_rebound_ready) return { label: "🟡 短线反抽", color: pal.warn, bg: pal.warnBg };
            if (isPullback || item.broken_bearish) return { label: "🟡 谨防回踩", color: pal.warn, bg: pal.warnBg };
            if (sig.includes("多头")) return { label: "🟢 可关注", color: pal.up, bg: pal.upBg };
            return { label: "⚪ 观望", color: pal.sub, bg: pal.elevatedBg };
        }

        function syncFilterControlsUI(filter) {
            const primaryFilters = new Set(["all", "opportunity", "risk", "gold_crypto"]);
            document.querySelectorAll(".wl-pill-btn").forEach(btn => {
                btn.classList.toggle("active", btn.getAttribute("data-filter") === filter);
            });
            const leaderBtn = document.getElementById("btnResilientTop5");
            if (leaderBtn) {
                leaderBtn.classList.toggle("active", filter === "resilient_top5");
            }
            document.querySelectorAll(".w-tab").forEach(t => {
                t.classList.toggle("active", t.getAttribute("data-filter") === filter);
            });
            const sel = document.getElementById("sectorFilterSelect");
            if (sel) {
                sel.value = (primaryFilters.has(filter) || filter === "resilient_top5") ? "" : filter;
            }
        }

        function setWatchlistFilter(filter) {
            const f = filter || "all";
            try {
                localStorage.setItem("futu_watchlist_filter", f);
            } catch (e) {}
            syncFilterControlsUI(f);
            renderWatchlist(f);
        }

        function onSectorSelectChange(val) {
            setWatchlistFilter(val || "all");
        }

        function buildMacroListEntry(t, m) {
            return {
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
                is_crash_halt: Boolean(m.is_crash_halt),
                is_cooling_halt: Boolean(m.is_cooling_halt),
                is_inverse_hedge: Boolean(m.is_inverse_hedge || ["SQQQ", "SOXS", "VIXY"].includes(t)),
                rth_low: m.rth_low || m.low_price || 0,
                low_price: m.low_price || m.rth_low || 0,
                ema5: m.ema5 || 0,
                optimal_limit: m.optimal_limit,
                limit_note: m.limit_note,
                entry_stop_loss: m.entry_stop_loss,
                stop_loss_pct: m.stop_loss_pct,
                intraday_entry_ready: m.intraday_entry_ready,
                intraday_status_text: m.intraday_status_text,
                intraday_reason: m.intraday_reason,
                vwap: m.vwap,
                has_5m_intraday: m.has_5m_intraday,
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
            };
        }

        function renderWatchlist(filter = currentFilter) {
            currentFilter = filter;
            const pal = getThemePalette();
            const container = document.getElementById("watchlistContainer");
            if (!container) return;
            container.innerHTML = "";

            const aiByTicker = (AI_PREDICTIONS && AI_PREDICTIONS.by_ticker) ? AI_PREDICTIONS.by_ticker : {};
            let items = [];

            STOCKS_DATA.forEach(s => {
                let match = false;
                const aiRow = aiByTicker[s.ticker];
                const aiCal = aiRow ? Number(aiRow.calibrated_win_rate_pct || 0) : 0;
                const sigStr = String(s.signal || "");
                const isOpportunity = !s.is_crash_halt && !s.is_cooling_halt && !s.is_inverse_hedge && !s.is_stop && !s.is_deep_pullback && !s.is_take_profit && !s.broken_bearish && (
                    Boolean(s.intraday_entry_ready) || Boolean(s.is_pit) || Boolean(s.broken_rebound_ready) || (aiCal >= 55.0) || sigStr.includes("黄金坑") || sigStr.includes("多头")
                );
                const isRisk = Boolean(s.is_crash_halt || s.is_cooling_halt || s.is_stop || s.is_deep_pullback || s.is_take_profit || s.is_earnings_risk || s.broken_bearish);
                const isGoldCrypto = ["XAUUSD", "GLD", "BTC-USD", "MSTR", "COIN"].includes(s.ticker) || s.cat === "大宗商品" || s.cat === "数字资产";

                if (filter === "all" || filter === "stocks") match = true;
                else if (filter === "opportunity" && isOpportunity) match = true;
                else if (filter === "risk" && isRisk) match = true;
                else if (filter === "gold_crypto" && isGoldCrypto) match = true;
                else if ((filter === "ai_top" || filter === "ai_short") && Boolean(aiRow)) match = true;
                else if (filter === "pit" && !s.is_crash_halt && !s.is_cooling_halt && !s.is_inverse_hedge && (s.is_pit || (s.pit_status && (s.pit_status.includes("坑内") || s.pit_status.includes("黄金坑"))) || (s.signal && s.signal.includes("黄金坑")) || (s.dist_to_pit !== undefined && s.dist_to_pit <= 0.0 && s.dist_to_pit >= -8.0))) match = true;
                else if (filter === "take_profit" && (s.is_take_profit || (s.signal && (s.signal.includes("止盈") || s.signal.includes("冲顶"))))) match = true;
                else if (filter === "stop" && (s.is_stop || s.is_deep_pullback || (s.signal && (s.signal.includes("破位") || s.signal.includes("跌破") || s.signal.includes("回踩"))))) match = true;
                else if (filter === "芯片与半导体" && (s.cat === "芯片算力" || s.cat === "晶圆代工" || s.cat === "制造设备" || s.cat === "通信芯片" || s.cat === "芯片半导体")) match = true;
                else if (filter === "存储闪存" && (s.cat === "存储芯片" || s.cat === "闪存存储" || s.cat === "数据存储")) match = true;
                else if (filter === "云与软件" && (s.cat === "云与软件" || s.cat === "生态平台" || s.cat === "社交AI" || s.cat === "终端硬件" || s.cat === "云与电商" || s.cat === "企业AI" || s.cat === "算力租赁")) match = true;
                else if (filter === "液冷服务器" && (s.cat === "液冷散热" || s.cat === "服务器ODM")) match = true;
                else if (filter === "光通信" && (s.cat === "光通信" || s.cat === "高速互联")) match = true;
                else if (filter === "能源电力" && (s.cat === "能源电力" || s.cat === "核能电力" || s.cat === "传统能源")) match = true;
                else if (filter === "太空商业" && s.cat === "太空商业") match = true;
                else if (filter === "物理与安全" && (s.cat === "物理AI" || s.cat === "AI安全" || s.cat === "价值蓝筹" || s.cat === "金融蓝筹" || s.cat === "专属风控")) match = true;
                else if (filter === "数字资产" && (s.cat === "数字资产" || s.ticker === "MSTR" || s.ticker === "BTC-USD")) match = true;
                else if (filter === "大宗商品" && (s.cat === "大宗商品" || s.ticker === "XAUUSD" || s.ticker === "GLD")) match = true;
                else if (filter === "生物医药" && (s.cat === "生物医药" || s.cat === "医药健康")) match = true;
                else if (filter === "价值消费" && (s.cat === "价值蓝筹" || s.cat === "可选消费" || s.cat === "金融科技" || s.cat === "金融蓝筹" || s.cat === "传统金融" || s.cat === "传统能源" || s.cat === "传统消费" || s.cat === "传统工业")) match = true;
                else if (s.cat === filter) match = true;

                if (match) items.push({ ...s, is_stock: true });
            });

            Object.keys(MACRO_DATA).forEach(t => {
                const m = MACRO_DATA[t];
                let match = false;
                const aiRow = aiByTicker[t];
                const aiCal = aiRow ? Number(aiRow.calibrated_win_rate_pct || 0) : 0;
                const isInv = Boolean(m.is_inverse_hedge || ["SQQQ", "SOXS", "VIXY"].includes(t));
                const sigStr = String(m.signal || m.status || "");
                const isOpportunity = !m.is_crash_halt && !m.is_cooling_halt && !isInv && !m.is_stop && !m.is_deep_pullback && !m.is_take_profit && (
                    Boolean(m.intraday_entry_ready) || Boolean(m.is_pit) || (aiCal >= 55.0) || sigStr.includes("黄金坑") || sigStr.includes("多头")
                );
                const isRisk = Boolean(m.is_crash_halt || m.is_cooling_halt || m.is_stop || m.is_deep_pullback || m.is_take_profit || m.is_earnings_risk);
                const isGoldCrypto = ["XAUUSD", "GLD", "BTC-USD"].includes(t) || m.cat === "大宗商品" || m.cat === "数字资产";

                if (filter === "all" || filter === "macro") match = true;
                else if (filter === "opportunity" && isOpportunity) match = true;
                else if (filter === "risk" && isRisk) match = true;
                else if (filter === "gold_crypto" && isGoldCrypto) match = true;
                else if ((filter === "ai_top" || filter === "ai_short") && Boolean(aiRow)) match = true;
                else if (filter === "pit" && !m.is_crash_halt && !m.is_cooling_halt && !m.is_inverse_hedge && !["SQQQ", "SOXS", "VIXY"].includes(t) && (m.is_pit || (m.signal && m.signal.includes("黄金坑")) || (m.pit_status && (m.pit_status.includes("坑内") || m.pit_status.includes("黄金坑"))) || (m.dist_to_pit !== undefined && m.dist_to_pit <= 0.0 && m.dist_to_pit >= -8.0))) match = true;
                else if (filter === "take_profit" && (m.is_take_profit || (m.signal && (m.signal.includes("止盈") || m.signal.includes("超买") || m.signal.includes("冲顶"))))) match = true;
                else if (filter === "stop" && (m.is_stop || m.is_deep_pullback || (m.signal && (m.signal.includes("破位") || m.signal.includes("跌破") || m.signal.includes("回踩"))))) match = true;
                else if (filter === "芯片与半导体" && (t === "SMH" || t === "SOXX" || t === "SOXL" || t === "SOXS" || t === "KORU")) match = true;
                else if (filter === "云与软件" && (t === "QQQ" || t === "IGV" || t === "SQQQ")) match = true;
                else if (filter === "数字资产" && (t === "BTC-USD" || m.cat === "数字资产")) match = true;
                else if (filter === "大宗商品" && (t === "GLD" || m.cat === "大宗商品")) match = true;

                if (match) items.push(buildMacroListEntry(t, m));
            });

            if (filter === "resilient_top5") {
                items = computeResilientLeadersTop5().map(ld => ({
                    ...ld.entry,
                    _leader_rs: ld.rs_20d,
                    _leader_tag: ld.vwap_tag,
                }));
            } else if (filter === "ai_top" && aiByTicker) {
                items.sort((a, b) => ((aiByTicker[a.ticker] && aiByTicker[a.ticker].rank) || 999) - ((aiByTicker[b.ticker] && aiByTicker[b.ticker].rank) || 999));
            } else if (filter === "ai_short" && aiByTicker) {
                items.sort((a, b) => ((aiByTicker[a.ticker] && aiByTicker[a.ticker].short_rank) || 999) - ((aiByTicker[b.ticker] && aiByTicker[b.ticker].short_rank) || 999));
            } else if (filter === "opportunity") {
                items.sort((a, b) => {
                    const sa = (a.intraday_entry_ready ? 200 : 0) + (a.is_pit ? 100 : 0) + ((aiByTicker[a.ticker] && aiByTicker[a.ticker].calibrated_win_rate_pct) || 0);
                    const sb = (b.intraday_entry_ready ? 200 : 0) + (b.is_pit ? 100 : 0) + ((aiByTicker[b.ticker] && aiByTicker[b.ticker].calibrated_win_rate_pct) || 0);
                    return sb - sa;
                });
            }

            if (items.length === 0) {
                container.innerHTML = `<div style="text-align:center; padding: 36px 12px; color: ${pal.muted}; font-size: 12px;">当前筛选暂无符合条件的标的</div>`;
                return;
            }

            items.forEach(item => {
                const div = document.createElement("div");
                div.className = `stock-item ${item.ticker === currentTicker ? 'active' : ''}`;
                div.dataset.ticker = item.ticker;

                const pct = item.pct_change || 0;
                const isUp = pct >= 0;
                const pctColor = isUp ? pal.up : pal.down;
                const pctBg = isUp ? pal.upBg : pal.downBg;
                const badge = getPlainStatusBadge(item, pal);
                const hasExt = item.session_tag && item.ext_price && item.ext_price > 0;
                const extPct = item.ext_pct || 0;
                const extColor = extPct >= 0 ? pal.up : pal.down;

                div.innerHTML = `
                    <div class="s-info">
                        <div class="s-ticker">
                            <span class="s-ticker-code">${escapeNewsHtml(item.ticker)}</span>
                            <span class="s-status-pill" style="color: ${badge.color}; background: ${badge.bg};">${badge.label}</span>
                        </div>
                        <div class="s-name">${escapeNewsHtml(item.name || "")}</div>
                    </div>
                    <div class="s-quotes tabular">
                        <div class="s-price">${item.last && item.last > 0 ? ('$' + item.last.toFixed(2)) : '--'}</div>
                        <div class="s-pct" style="color:${pctColor}; background:${pctBg};">
                            ${isUp ? '+' : ''}${pct.toFixed(2)}%
                        </div>
                        ${hasExt ? `<div class="s-ext-note">${escapeNewsHtml(item.session_tag)} $${item.ext_price.toFixed(2)} <span style="color:${extColor};">(${extPct >= 0 ? '+' : ''}${extPct.toFixed(2)}%)</span></div>` : ''}
                    </div>
                `;
                div.onclick = () => selectTicker(item.ticker, true);
                container.appendChild(div);
            });
        }

        function buildBeginnerVerdict(ctx) {
            const pal = getThemePalette();
            if (ctx.isIpo) {
                return {
                    title: "🆕 IPO 新股观察期 · 暂无历史规律，先看少动",
                    sub: "上市未满 5 个交易日，先观察开盘换手与均价线承接，不急于重仓追高。",
                    color: pal.accent, bg: pal.accentBg, border: pal.accentBorder
                };
            }
            if (ctx.isCrashHalt) {
                return {
                    title: "🔴 暴跌风险期 · 严禁抄底接刀",
                    sub: "短线出现剧烈跳空或大跌，模型已自动冻结买点，耐心等待日线企稳。",
                    color: pal.down, bg: pal.downBg, border: pal.downBorder
                };
            }
            if (ctx.isCoolingHalt) {
                return {
                    title: "🔴 大跌冷却期 · 暂缓买入",
                    sub: "近 3 日刚经历剧烈下跌，必须等日线确认止跌后再考虑介入。",
                    color: pal.down, bg: pal.downBg, border: pal.downBorder
                };
            }
            if (ctx.isBrokenBearish) {
                return {
                    title: "🔴 空头占优 · 严禁左侧接刀",
                    sub: "股价仍在日内均价线与支撑带下方运行，没站稳前别急着抄底。",
                    color: pal.down, bg: pal.downBg, border: pal.downBorder
                };
            }
            if (ctx.isStop) {
                return {
                    title: "🔴 跌破支撑线 · 防守为主，别盲目补仓",
                    sub: "关键支撑位已破位，持仓者严格按止损线风控，空仓者先多看少动。",
                    color: pal.down, bg: pal.downBg, border: pal.downBorder
                };
            }
            if (ctx.isPitWatch) {
                return {
                    title: "🟡 接近低吸区但板块偏弱 · 先观望",
                    sub: "虽然回落到低吸观察区，但行业 ETF 仍偏弱，暂不抢跑接刀。",
                    color: pal.warn, bg: pal.warnBg, border: pal.warnBorder
                };
            }
            if (ctx.isPit) {
                return {
                    title: "🟢 低吸窗口打开 · 可按参考买点分批挂单",
                    sub: "已回踩至支撑区间且呈现止跌迹象，务必同步设好下方短线止损位。",
                    color: pal.up, bg: pal.upBg, border: pal.upBorder
                };
            }
            if (ctx.isBrokenRebound) {
                return {
                    title: "🟡 破位后短线反抽 · 快进快出",
                    sub: "中期趋势仍弱，仅限小仓位盯紧短线止损博弈反抽，遇压力位不过即走。",
                    color: pal.warn, bg: pal.warnBg, border: pal.warnBorder
                };
            }
            if (ctx.isTakeProfit) {
                return {
                    title: "🟣 涨幅偏大 / 高位震荡 · 别追高，注意分批止盈",
                    sub: "短期已积累较大涨幅或跌破短期均线，建议落袋保护利润，空仓勿追。",
                    color: pal.purple, bg: pal.purpleBg, border: pal.purpleBorder
                };
            }
            if (ctx.isDeepPullback) {
                return {
                    title: "⚪ 暂观望 · 未站稳均价线，别急着抄底",
                    sub: "处于回调消化期，等待日内放量站稳均价线再考虑低吸。",
                    color: pal.warn, bg: pal.warnBg, border: pal.warnBorder
                };
            }
            if (ctx.isInverseHedge) {
                return {
                    title: "🛡️ 反向避险工具 · 仅限短线对冲",
                    sub: "具有杠杆损耗特性，只适合大盘下跌时短线防守对冲，不可长期持有。",
                    color: pal.accent, bg: pal.accentBg, border: pal.accentBorder
                };
            }
            if (ctx.isBullTrend) {
                return {
                    title: "🟢 趋势偏强 · 顺势持有，回踩不破再低吸",
                    sub: "运行在中期均线上方，顺势持有为主，不建议盘中大涨时情绪追高。",
                    color: pal.up, bg: pal.upBg, border: pal.upBorder
                };
            }
            return {
                title: "⚪ 暂观望 · 震荡整理中，等待高胜率买点",
                sub: "多空暂时胶着，未触发明确低吸信号前建议控制仓位、耐心等待。",
                color: pal.sub, bg: pal.elevatedBg, border: pal.border
            };
        }

        function selectTicker(ticker, fromUserClick = false) {
            currentTicker = ticker;
            const pal = getThemePalette();
            try {
                localStorage.setItem("lastSelectedTicker", ticker);
            } catch(e) {}

            if (fromUserClick && window.innerWidth <= 768 && currentMobileTab === "watchlist") {
                switchMobileTab("chart");
            }

            const stock = STOCKS_DATA.find(s => s.ticker === ticker);
            const macro = MACRO_DATA[ticker];
            const data = stock || macro || {};

            // 1. 顶部 / 图表头部行情标识
            const curSymEl = document.getElementById("curSym");
            const curNameEl = document.getElementById("curName");
            const curPriceEl = document.getElementById("curPrice");
            const curChangeEl = document.getElementById("curChange");
            if (curSymEl) curSymEl.innerText = ticker;
            if (curNameEl) curNameEl.innerText = data.name || "";
            if (curPriceEl) curPriceEl.innerText = (data.last && data.last > 0) ? ("$" + data.last.toFixed(2)) : "--";

            const pct = data.pct_change || 0;
            if (curChangeEl) {
                curChangeEl.innerText = (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%";
                curChangeEl.style.color = pct >= 0 ? pal.up : pal.down;
                curChangeEl.style.background = pct >= 0 ? pal.upBg : pal.downBg;
            }

            const extBadgeEl = document.getElementById("curExtBadge");
            if (extBadgeEl) {
                if (data.session_tag && data.ext_price && data.ext_price > 0) {
                    const extPct = data.ext_pct || 0;
                    const extSign = extPct >= 0 ? "+" : "";
                    const extClr = extPct >= 0 ? pal.up : pal.down;
                    extBadgeEl.style.display = "inline-block";
                    extBadgeEl.style.background = pal.accentBg;
                    extBadgeEl.style.color = pal.accent;
                    const sIcon = data.session_tag.includes("夜盘") ? "🌌" : (data.session_tag.includes("盘前") ? "🌅" : "🌙");
                    extBadgeEl.innerHTML = `${sIcon} ${escapeNewsHtml(data.session_tag)}: <b>$${data.ext_price.toFixed(2)}</b> <span style="color:${extClr};">(${extSign}${extPct.toFixed(2)}%)</span>`;
                } else {
                    extBadgeEl.style.display = "none";
                }
            }

            const earnBadgeEl = document.getElementById("curEarningsBadge");
            if (earnBadgeEl) {
                if (data.earnings_tag) {
                    earnBadgeEl.style.display = "inline-block";
                    earnBadgeEl.innerText = data.earnings_tag;
                    earnBadgeEl.style.background = data.is_earnings_risk ? pal.downBg : pal.warnBg;
                    earnBadgeEl.style.color = data.is_earnings_risk ? pal.down : pal.warn;
                } else {
                    earnBadgeEl.style.display = "none";
                }
            }

            // 2. 核心行情数值计算
            const curLast = data.last || 0;
            const curActive = (data.ext_price && data.ext_price > 0) ? data.ext_price : (data.active_price || curLast);
            const curMa50 = data.ma50 || 0;
            const curEma20 = data.ema20 || curMa50;
            const curBias = data.bias !== undefined ? data.bias : (curMa50 > 0 ? ((curActive - curMa50) / curMa50 * 100) : 0);
            const curAtr = data.atr || 0;
            const curRsi = data.rsi !== undefined ? data.rsi : 50;
            const curVolRatio = data.vol_ratio !== undefined ? data.vol_ratio : 1.0;
            const curFibLow = data.fib_low || 0;
            const curFibHigh = data.fib_high || 0;
            const curDistPit = data.dist_to_pit !== undefined ? data.dist_to_pit : 0;
            const curSignal = data.signal || "";
            const isInverseHedge = Boolean(data.is_inverse_hedge || ["SQQQ", "SOXS", "VIXY"].includes(ticker));
            const isCrashHalt = !isInverseHedge && Boolean(data.is_crash_halt || (data.optimal_limit === 0 && data.limit_note && data.limit_note.includes("破位")));
            const isCoolingHalt = !isInverseHedge && Boolean(data.is_cooling_halt || (data.intraday_status_text && data.intraday_status_text.includes("冷却期")));
            let optimalLimit = data.optimal_limit !== undefined ? data.optimal_limit : curActive;
            const stopLoss = (isCrashHalt || isCoolingHalt) ? 0 : (data.entry_stop_loss || (curFibLow > 0 ? curFibLow * 0.97 : optimalLimit * 0.96));
            const cancelLine = Math.max(data.low_price || data.rth_low || 0, curLast * 0.985);
            const curCat = data.cat || data.sub || "综合赛道";

            const sig = data.signal || "";
            const isPit = Boolean(data.is_pit || sig.includes("黄金坑就位"));
            const isPitWatch = Boolean(sig.includes("坑内观察") || sig.includes("待芯片ETF"));
            const isStop = Boolean(data.is_stop || sig.includes("破位") || sig.includes("跌破") || sig.includes("硬止损"));
            const isDeepPullback = Boolean(data.is_deep_pullback || sig.includes("深度回踩"));
            const isBrokenRebound = Boolean(data.broken_rebound_ready);
            const isBrokenBearish = Boolean(data.broken_bearish);
            const isTakeProfit = Boolean(data.is_take_profit || sig.includes("冲顶") || sig.includes("止盈") || sig.includes("超买"));
            const isBullTrend = Boolean(sig.includes("多头"));
            const isIpo = Boolean(data.is_ipo_pending || sig.includes("IPO"));

            const aiMap = (AI_PREDICTIONS && AI_PREDICTIONS.by_ticker) ? AI_PREDICTIONS.by_ticker : {};
            const aiItem = aiMap[ticker] || null;
            const aiNowR1 = Number((aiItem && aiItem.r1) || 0);

            // 3. 更新专业抽屉 ①：基础技术面指标
            const secEl = document.getElementById("pSector");
            if (secEl) secEl.innerText = curCat;
            const bSecTag = document.getElementById("beginnerSectorTag");
            if (bSecTag) bSecTag.innerText = curCat;

            const ma50El = document.getElementById("pMa50");
            if (ma50El) ma50El.innerText = curMa50 ? ("$" + curMa50.toFixed(2)) : "--";

            const biasEl = document.getElementById("pBias");
            if (biasEl && (data.bias !== undefined || curMa50 > 0)) {
                const hasExt = Boolean(data.ext_price && data.ext_price > 0 && data.session_tag);
                const extNote = hasExt ? ` <span style="font-size:10px; color:${pal.sub};">(含${escapeNewsHtml(data.session_tag)})</span>` : "";
                biasEl.innerHTML = (curBias >= 0 ? "+" : "") + curBias.toFixed(2) + "%" + extNote;
                biasEl.style.color = curBias >= 0 ? pal.up : pal.down;
            }
            const ema20El = document.getElementById("pEma20");
            if (ema20El) {
                ema20El.innerText = curEma20 ? ("$" + curEma20.toFixed(2)) : "--";
                ema20El.style.color = pal.accent;
            }
            const volEl = document.getElementById("pVolRatio");
            if (volEl) {
                volEl.innerText = curVolRatio ? (curVolRatio.toFixed(2) + "x") : "--";
                volEl.style.color = curVolRatio >= 1.25 ? pal.warn : (curVolRatio <= 0.85 ? pal.accent : pal.text);
            }
            const rsiEl = document.getElementById("pRsi");
            if (rsiEl) {
                rsiEl.innerText = curRsi.toFixed(1);
                rsiEl.style.color = curRsi >= 70 ? pal.purple : (curRsi <= 35 ? pal.up : pal.text);
            }
            const atrEl = document.getElementById("pAtr");
            if (atrEl) atrEl.innerText = curAtr ? ("$" + curAtr.toFixed(2)) : "--";

            // 4. 战术诊断与四维成因 (Group ③)
            let themeColor = pal.accent;
            let themeBg = pal.accentBg;
            let themeBorder = pal.accentBorder;
            let actionType = "观望评估";
            let mainVerdict = "中性整理 · 等待方向";
            let maReason = "";
            let volReason = "";
            let fibReason = "";
            let macroReason = "";
            let actionDirective = "";

            if (curBias > 15.0) {
                maReason = `偏离 MA50 (+${curBias.toFixed(1)}%) 极度严重，均线乖离率进入历史极端警报区`;
            } else if (curBias > 5.0) {
                maReason = `站稳 MA50 线上 (+${curBias.toFixed(1)}%) 且位于 EMA20 ($${curEma20.toFixed(2)}) 上方，多头发散上行`;
            } else if (curBias >= 0) {
                maReason = `贴近 MA50 均线 (+${curBias.toFixed(1)}%)，多头生命线支撑有效，处于正常回踩蓄势`;
            } else if (curBias >= -8.0) {
                maReason = `跌破 MA50 (${curBias.toFixed(1)}%)，均线暂时承压，属于健康波段洗盘回调区间`;
            } else {
                maReason = `深度跌破 MA50 (${curBias.toFixed(1)}%)，均线系统呈空头反压排列，中期趋势受损`;
            }

            if (curVolRatio >= 1.5) {
                volReason = `量比 ${curVolRatio.toFixed(2)}x (放量明显)，${curBias > 8.0 ? '高位大换手赶顶，警惕主力兑现出货' : (curBias < -10.0 ? '放量大跌，空头主动性抛压加剧' : '多空激烈博弈，量能显著放大')}`;
            } else if (curVolRatio >= 1.1) {
                volReason = `量比 ${curVolRatio.toFixed(2)}x (成交适度活跃)，量价匹配处于合理区间`;
            } else if (curVolRatio < 0.8 && (data.pct_change || 0) > 0) {
                volReason = `量比 ${curVolRatio.toFixed(2)}x (缩量反抽)，抛压阶段性减轻，关注上方 R1 阻力位能否补量突破`;
            } else if (curVolRatio <= 0.85) {
                volReason = `量比 ${curVolRatio.toFixed(2)}x (显著缩量)，${curBias < 0 ? '缩量阴跌假摔，主力诱空洗盘特征明显，无恐慌大割肉' : '缩量休整，筹码结构锁定良好'}`;
            } else {
                volReason = `量比 ${curVolRatio.toFixed(2)}x，成交量能处于常态水平`;
            }

            if (curFibLow && curFibHigh) {
                if (curLast >= curFibLow && curLast <= curFibHigh) {
                    fibReason = `精准落入 60日斐波那契 0.5~0.618 黄金坑支撑带 ($${curFibLow.toFixed(2)} ~ $${curFibHigh.toFixed(2)})，处于战略低吸区`;
                } else if (isPit && curDistPit >= -2.0) {
                    fibReason = `处于黄金坑底极窄洗盘弹簧区 (${curDistPit.toFixed(1)}%，坑底线 $${curFibLow.toFixed(2)})，属主力诱空洗盘，未实质破位`;
                } else if (curLast > curFibHigh) {
                    fibReason = `运行于黄金坑上方 (+${curDistPit.toFixed(1)}%)，波段支撑带位于 $${curFibLow.toFixed(2)} ~ $${curFibHigh.toFixed(2)}`;
                } else {
                    fibReason = `击穿波段坑底支撑 -${Math.abs(curDistPit).toFixed(1)}% (坑底线 $${curFibLow.toFixed(2)})，斐波那契多头支撑已破位`;
                }
            } else {
                fibReason = "基准支撑带平稳运行";
            }

            const isSemi = ["芯片算力", "晶圆代工", "制造设备", "存储芯片", "通信芯片", "半导体"].includes(curCat);
            const smhData = MACRO_DATA["SMH"];
            const smhBias = smhData ? smhData.bias : 0;
            if (isSemi && smhBias < -3.0) {
                macroReason = `⚠️ 核心半导体 ETF (SMH) 偏离 MA50 ${smhBias.toFixed(1)}% 破位承压，芯片赛道受行业贝塔压制`;
            } else {
                macroReason = `大盘气象评级为【${WEATHER_INFO.weather || '防守'}】，全市场环境需保持仓位纪律`;
            }

            if (isPitWatch) {
                themeColor = pal.warn; themeBg = pal.warnBg; themeBorder = pal.warnBorder;
                actionType = "暂停接刀 / 保持观望";
                mainVerdict = "🟡 坑内观察 · 严禁抢跑左侧接刀";
                actionDirective = `【受板块拖累暂缓买入】个股估值与价位虽已落入黄金坑防守带，但核心芯片 ETF (SMH) 偏离 MA50 达 ${smhBias.toFixed(1)}% 处于破位弱势，存在板块系统性补跌风险。必须等待半导体板块收出右侧止跌企稳阳线后再行启动！`;
            } else if (isPit && !isCrashHalt && !isCoolingHalt) {
                themeColor = pal.up; themeBg = pal.upBg; themeBorder = pal.upBorder;
                actionType = "V3.1 黄金坑右侧确认";
                mainVerdict = "🟢 V3.1 黄金坑就位 (9:30开盘执行)";
                actionDirective = `【V3.1 开盘执行标准】标的已触发右侧止跌企稳！<br>1️⃣ 开盘买入参考上限: $${optimalLimit.toFixed(2)}<br>2️⃣ 隔夜低开作废底线: $${cancelLine.toFixed(2)}<br>3️⃣ 铁血结构止损: $${stopLoss.toFixed(2)}`;
            } else if (isCrashHalt || isCoolingHalt) {
                themeColor = pal.down; themeBg = pal.downBg; themeBorder = pal.downBorder;
                actionType = isCoolingHalt ? "暴跌冷却 / 严禁抢跑" : "暴跌熔断 / 暂停接刀";
                mainVerdict = isCoolingHalt ? "⛔ 暴跌冷却期 (3日内曾大跌·等待日线企稳确认)" : "⛔ 暂停挂单 (暴跌宣泄期 · 防接飞刀)";
                actionDirective = `【V3.2 熔断保护】${data.intraday_reason || "标的触发日内暴跌熔断或处于3日暴跌冷却期内，日线尚未确认右侧企稳，严禁盲目左侧挂单抄底！"}`;
            } else if (isBrokenRebound) {
                themeColor = pal.warn; themeBg = pal.warnBg; themeBorder = pal.warnBorder;
                actionType = "超跌右侧博反抽 / 短线快进快出";
                mainVerdict = "⚡ 超跌右侧博反抽 (破坑深调·快进快出)";
                actionDirective = `【破坑右侧反抽】日线仍处黄金坑 ($${curFibLow.toFixed(2)}) 下方，但日内已站稳 VWAP/支撑企稳；仅限短线博空头回补，压力位 R1 ($${Number(aiNowR1 || data.broken_r1 || curFibHigh || 0).toFixed(2)}) 不过即走，严禁中线死扛。`;
            } else if (isBrokenBearish) {
                themeColor = pal.down; themeBg = pal.downBg; themeBorder = pal.downBorder;
                actionType = "破坑观望 / 严禁左侧接刀";
                mainVerdict = data.intraday_status_text || "🔴 空头宣泄·严禁左侧接刀";
                actionDirective = data.intraday_reason || `【左侧禁接】股价跌穿黄金坑 ($${curFibLow.toFixed(2)}) 且日内压制于 VWAP ($${(data.vwap || curLast).toFixed(2)}) 下方，空头宣泄未止跌；请耐心等待日内重夺 VWAP 后再博右侧反抽。`;
            } else if (isStop) {
                themeColor = pal.down; themeBg = pal.downBg; themeBorder = pal.downBorder;
                actionType = "破位风控 / 严禁抄底";
                mainVerdict = "🔴 放量破位杀跌 · 严禁逆势接飞刀";
                actionDirective = `【铁血风控指令】标的击穿关键斐波那契坑底防线 (跌破 ${Math.abs(curDistPit).toFixed(1)}%)，空头动能处于释放期。持仓者若跌破底线必须坚决离场止损；空仓观察者坚决管住手。`;
            } else if (isDeepPullback) {
                themeColor = pal.warn; themeBg = pal.warnBg; themeBorder = pal.warnBorder;
                actionType = "深度回踩 / 严控追高";
                mainVerdict = "🟠 深度回踩 · 等待右侧量价共振";
                if (curVolRatio <= 0.85) {
                    actionDirective = `【多看少动耐心观望】标的虽已跌破斐波那契坑底，但日线量比仅 ${curVolRatio.toFixed(2)}x 呈现显著缩量。等待右侧放量企稳信号。`;
                } else if (pct >= 0) {
                    actionDirective = `【超跌反抽·莫追高】今日收阳反抽 (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%，量比 ${curVolRatio.toFixed(2)}x)，切勿在反抽高位追涨，等待回踩日内 VWAP 或 5m 量价共振信号。`;
                } else {
                    actionDirective = `【多看少动耐心观望】当前日线量比 ${curVolRatio.toFixed(2)}x 未达恐慌放量破位阈值，属弱势寻底阶段，等待 5m 站上 VWAP 共振确认。`;
                }
            } else if (isTakeProfit) {
                themeColor = pal.purple; themeBg = pal.purpleBg; themeBorder = pal.purpleBorder;
                actionType = "高位减仓 / 锁定利润";
                if (sig.includes("跌破EMA20")) {
                    mainVerdict = "🟣 跌破EMA20 · 移动止盈落袋";
                    actionDirective = `【移动止盈指引】现价 ($${curActive.toFixed(2)}) 跌破 20日均线 EMA20 ($${curEma20.toFixed(2)})，建议移动止盈保护浮盈。`;
                } else {
                    mainVerdict = "🟣 冲顶减仓 · 启动分批锁定胜果";
                    actionDirective = `【获利了结指引】技术面进入超买高潮区 (RSI ${curRsi.toFixed(1)}，偏离 ${curBias.toFixed(1)}%)。持仓者建议分批兑现利润，剩余仓位以 EMA20 ($${curEma20.toFixed(2)}) 作为移动保护。`;
                }
            } else if (isBullTrend) {
                themeColor = pal.accent; themeBg = pal.accentBg; themeBorder = pal.accentBorder;
                actionType = "顺势持有 / 移动保护";
                mainVerdict = "🔵 强势多头推进 · 顺势持有待涨";
                actionDirective = `【顺势而为】主升浪趋势健康，依托 EMA20 ($${curEma20.toFixed(2)}) 作为趋势生命线，收盘不破安心持股。`;
            } else if (isIpo) {
                themeColor = pal.accent; themeBg = pal.accentBg; themeBorder = pal.accentBorder;
                actionType = "新股上市观察";
                mainVerdict = data.is_ipo_pending ? `🆕 IPO 首日待开盘 · 发行基准价 $${Number(data.ipo_price || curLast || 83).toFixed(2)}` : `🆕 IPO 新股价格发现期 · 现价 $${curLast.toFixed(2)}`;
                actionDirective = "【IPO 首日风险提示】标的刚上市尚无 5 日历史日线与支撑底模，重点观察竞价换手与日内 VWAP 承接，满 5 个交易日后自动激活多周期量化模型。";
            } else {
                themeColor = pal.sub; themeBg = pal.elevatedBg; themeBorder = pal.border;
                actionType = "区间震荡 / 耐心蓄势";
                mainVerdict = "⚪ 震荡整理蓄势 · 多看少动中性仓位";
                actionDirective = "【箱体整理】当前处于均线反复争夺阶段，未触发确定性波段信号，耐心等待回踩黄金坑或放量突破。";
            }

            const bannerEl = document.getElementById("pSignalBanner");
            const sigTextEl = document.getElementById("pSignalText");
            const sigTagEl = document.getElementById("pSignalTag");
            const decTitleEl = document.getElementById("pDecisionTitle");
            const actionBadgeEl = document.getElementById("pActionTypeBadge");

            if (bannerEl) {
                bannerEl.style.color = themeColor;
                bannerEl.style.background = themeBg;
                bannerEl.style.borderColor = themeBorder;
            }
            if (sigTextEl) sigTextEl.innerText = curSignal || mainVerdict;
            if (sigTagEl) {
                sigTagEl.innerText = actionType;
                sigTagEl.style.color = themeColor;
            }
            if (decTitleEl) {
                decTitleEl.innerText = mainVerdict;
                decTitleEl.style.color = themeColor;
            }
            if (actionBadgeEl) {
                actionBadgeEl.innerText = actionType;
                actionBadgeEl.style.color = themeColor;
            }

            const maREl = document.getElementById("pMaReason");
            const volREl = document.getElementById("pVolReason");
            const fibREl = document.getElementById("pFibReason");
            const macREl = document.getElementById("pMacroReason");
            const dirEl = document.getElementById("pActionDirective");
            if (maREl) maREl.innerText = maReason;
            if (volREl) volREl.innerText = volReason;
            if (fibREl) fibREl.innerText = fibReason;
            if (macREl) macREl.innerText = macroReason;
            if (dirEl) dirEl.innerHTML = actionDirective;

            // 5. 日内微结构数据 (Group ① 抽屉内实时同步)
            const intraBannerEl = document.getElementById("pIntradayBanner");
            const intraStatusEl = document.getElementById("pIntradayStatusText");
            const intraReasonEl = document.getElementById("pIntradayReason");
            const isEntryReady = Boolean(data.intraday_entry_ready);
            const intraStatusText = data.intraday_status_text || (isEntryReady ? "🎯 买点触发: 5m量化企稳" : "⏳ 蹲守挂单价");
            const intraReason = data.intraday_reason || "等待价格回踩至建议挂单价附近并出现 5m RSI 超卖拐头或 VWAP 突破信号。";

            if (intraBannerEl && intraStatusEl && intraReasonEl) {
                if (isInverseHedge) {
                    intraStatusEl.innerText = intraStatusText;
                    intraReasonEl.innerText = intraReason;
                    intraBannerEl.style.background = (isTakeProfit || intraStatusText.includes("收割")) ? pal.purpleBg : pal.accentBg;
                    intraBannerEl.style.borderColor = (isTakeProfit || intraStatusText.includes("收割")) ? pal.purpleBorder : pal.accentBorder;
                    intraStatusEl.style.color = (isTakeProfit || intraStatusText.includes("收割")) ? pal.purple : pal.accent;
                } else if (isCoolingHalt) {
                    intraBannerEl.style.background = pal.downBg;
                    intraBannerEl.style.borderColor = pal.downBorder;
                    intraStatusEl.style.color = pal.down;
                    intraStatusEl.innerText = "⛔ 暴跌冷却期 (3日内曾大跌·等待日线企稳确认)";
                    intraReasonEl.innerText = data.intraday_reason || "标的3日内曾发生单日大跌，目前处于冷却观察期，需等待日线右侧企稳确认。";
                } else if (isBrokenRebound) {
                    intraStatusEl.innerText = intraStatusText;
                    const _r1Shown = Number(aiNowR1 || data.broken_r1 || 0);
                    let _branchAReason = intraReason || "";
                    if (_r1Shown > 0) _branchAReason = _branchAReason.replace(/压力位\(R1 \$[\d.]+\)/, `压力位(R1 $${_r1Shown.toFixed(2)})`);
                    intraReasonEl.innerText = _branchAReason;
                    intraBannerEl.style.background = pal.warnBg;
                    intraBannerEl.style.borderColor = pal.warnBorder;
                    intraStatusEl.style.color = pal.warn;
                } else if (isBrokenBearish) {
                    intraStatusEl.innerText = intraStatusText;
                    intraReasonEl.innerText = intraReason;
                    intraBannerEl.style.background = pal.downBg;
                    intraBannerEl.style.borderColor = pal.downBorder;
                    intraStatusEl.style.color = pal.down;
                } else {
                    intraStatusEl.innerText = intraStatusText;
                    intraReasonEl.innerText = intraReason;
                    if (isEntryReady) {
                        intraBannerEl.style.background = pal.upBg;
                        intraBannerEl.style.borderColor = pal.upBorder;
                        intraStatusEl.style.color = pal.up;
                    } else if (intraStatusText.includes("IPO")) {
                        intraBannerEl.style.background = pal.accentBg;
                        intraBannerEl.style.borderColor = pal.accentBorder;
                        intraStatusEl.style.color = pal.accent;
                    } else if (intraStatusText.includes("严禁") || intraStatusText.includes("超买") || intraStatusText.includes("跌破") || intraStatusText.includes("冷却期")) {
                        intraBannerEl.style.background = pal.downBg;
                        intraBannerEl.style.borderColor = pal.downBorder;
                        intraStatusEl.style.color = pal.down;
                    } else {
                        intraBannerEl.style.background = pal.warnBg;
                        intraBannerEl.style.borderColor = pal.warnBorder;
                        intraStatusEl.style.color = pal.warn;
                    }
                }
            }

            const isNonRth = (SESSION_CODE !== "REGULAR");
            const vwapLblEl = document.getElementById("pVwapLabel");
            if (vwapLblEl) {
                vwapLblEl.innerHTML = isNonRth
                    ? '日内 VWAP 均价<span class="stale-ref-tag">(上日参考 · 09:30重置)</span>'
                    : '日内 VWAP 均价';
            }
            const rsi5LblEl = document.getElementById("pRsi5mLabel");
            if (rsi5LblEl) {
                rsi5LblEl.innerHTML = isNonRth
                    ? '5m RSI / 量比<span class="stale-ref-tag">(上日参考 · 09:30重置)</span>'
                    : '5m RSI / 量比';
            }
            const vwapEl = document.getElementById("pVwap");
            if (vwapEl) {
                const vwapVal = data.vwap || curLast;
                const vwapDiff = vwapVal > 0 ? ((curActive - vwapVal) / vwapVal * 100) : 0;
                vwapEl.innerHTML = vwapVal > 0 ? `$${vwapVal.toFixed(2)} <span style="font-size:10px; color:${vwapDiff >= 0 ? pal.up : pal.down};">(${vwapDiff >= 0 ? '+' : ''}${vwapDiff.toFixed(2)}%)</span>` : "--";
            }
            const rsi5mEl = document.getElementById("pRsi5m");
            if (rsi5mEl) {
                const has5m = (data.has_5m_intraday === true) || (data.has_5m_intraday !== false && data.rsi_5m !== undefined && data.rsi_5m !== null && data.rvol_5m !== undefined && data.rvol_5m !== null);
                if (!has5m || data.rsi_5m === null || data.rsi_5m === undefined || Number.isNaN(Number(data.rsi_5m))) {
                    rsi5mEl.innerText = "待开盘 / --";
                    rsi5mEl.style.color = pal.sub;
                } else {
                    const r5 = Number(data.rsi_5m);
                    const rv5 = (data.rvol_5m !== undefined && data.rvol_5m !== null && !Number.isNaN(Number(data.rvol_5m))) ? Number(data.rvol_5m) : null;
                    rsi5mEl.innerHTML = rv5 !== null
                        ? `${r5.toFixed(1)} <span style="font-size:10px; color:${rv5 >= 1.25 ? pal.warn : pal.sub};">(${rv5.toFixed(2)}x)</span>`
                        : `${r5.toFixed(1)}`;
                    rsi5mEl.style.color = r5 <= 35 ? pal.up : (r5 >= 70 ? pal.purple : pal.text);
                }
            }
            const rsLblEl = document.getElementById("pRs20dLabel");
            const rs20dEl = document.getElementById("pRs20d");
            const isVixySym = (ticker === "VIXY");
            if (rsLblEl) {
                rsLblEl.innerText = isVixySym ? "20日对冲脉冲" : "20日超额RS";
            }
            if (rs20dEl) {
                if (data.rs_20d === undefined || data.rs_20d === null || Number.isNaN(Number(data.rs_20d))) {
                    rs20dEl.innerText = "--";
                    rs20dEl.style.color = pal.sub;
                } else if (isVixySym) {
                    const rsVal = Number(data.rs_20d);
                    const rsSign = rsVal > 0 ? "+" : "";
                    let rsTag, rsColor;
                    if (rsVal >= 10.0) {
                        rsTag = "⚡避险飙升";
                        rsColor = pal.down;
                    } else if (rsVal >= 2.0) {
                        rsTag = "📈避险走强";
                        rsColor = pal.warn;
                    } else if (rsVal <= -5.0) {
                        rsTag = "📉恐慌回落";
                        rsColor = pal.up;
                    } else {
                        rsTag = "➖脉冲平稳";
                        rsColor = pal.sub;
                    }
                    rs20dEl.innerText = `${rsSign}${rsVal.toFixed(1)}% (${rsTag})`;
                    rs20dEl.style.color = rsColor;
                } else {
                    const rsVal = Number(data.rs_20d);
                    const rsSign = rsVal > 0 ? "+" : "";
                    let rsTag, rsColor;
                    if (rsVal >= 3.0) {
                        rsTag = "👑抗跌";
                        rsColor = pal.warn;
                    } else if (rsVal >= 0.5) {
                        rsTag = "💪强";
                        rsColor = pal.up;
                    } else if (rsVal <= -0.5) {
                        rsTag = "🥀弱";
                        rsColor = pal.down;
                    } else {
                        rsTag = "➖中性";
                        rsColor = pal.sub;
                    }
                    rs20dEl.innerText = `${rsSign}${rsVal.toFixed(1)}% (${rsTag})`;
                    rs20dEl.style.color = rsColor;
                }
            }
            const earnEl = document.getElementById("pEarningsDate");
            if (earnEl) {
                if (isVixySym) {
                    earnEl.innerText = "📉 波动率期货工具（含展期损耗·无财报）";
                    earnEl.style.color = pal.accent;
                } else if (data.earnings_tag) {
                    earnEl.innerText = data.earnings_tag;
                    earnEl.style.color = data.is_earnings_risk ? pal.down : pal.warn;
                } else if (["XAUUSD", "BTC-USD"].includes(ticker) || curCat === "大宗商品") {
                    earnEl.innerText = "🏛️ 宏观/FOMC日历";
                    earnEl.style.color = pal.accent;
                } else {
                    earnEl.innerText = "🟢 近期无财报";
                    earnEl.style.color = pal.sub;
                }
            }

            const vixHintEl = document.getElementById("pVixySpotHint");
            if (vixHintEl) {
                if (isVixySym) {
                    const vr = (WEATHER_INFO && WEATHER_INFO.vix_regime && WEATHER_INFO.vix_regime.vix_spot !== undefined)
                        ? WEATHER_INFO.vix_regime
                        : ((AI_PREDICTIONS && (AI_PREDICTIONS.vix_regime || (AI_PREDICTIONS.macro_weather && AI_PREDICTIONS.macro_weather.vix_regime))) || {});
                    const vSpot = (vr.vix_spot !== null && vr.vix_spot !== undefined) ? Number(vr.vix_spot).toFixed(2) : "--";
                    const v3m = (vr.vix3m_spot !== null && vr.vix3m_spot !== undefined) ? Number(vr.vix3m_spot).toFixed(2) : "--";
                    const vRatio = (vr.vix_term_ratio !== null && vr.vix_term_ratio !== undefined) ? Number(vr.vix_term_ratio).toFixed(3) : "--";
                    const vTermCn = escapeNewsHtml(vr.term_structure_cn || "期限监测中");
                    vixHintEl.innerHTML = `<b>⚡ 真 ^VIX 现货水位：</b>${vSpot} ｜ <b>^VIX3M：</b>${v3m} ｜ <b>期限比值 (VIX/VIX3M)：</b>${vRatio}（${vTermCn}）<br><span style="color: var(--text-secondary);">注：VIXY 跟踪短期波动率期货，存在展期损耗与多次合股历史，请以真实 ^VIX 现货水位与期限结构为准，勿被绝对股价误导。</span>`;
                    vixHintEl.style.display = "block";
                } else {
                    vixHintEl.style.display = "none";
                }
            }

            const bandEl = document.getElementById("pEntryFibBand");
            if (bandEl) {
                bandEl.innerText = isInverseHedge ? "🛡️ 反向衰减对冲(不计黄金坑)" : ((curFibLow && curFibHigh) ? (`$${curFibLow.toFixed(2)} ~ $${curFibHigh.toFixed(2)}`) : "--");
            }

            // 6. Layer A：小白 3 秒决策卡渲染 (结论 + 三列直白价格 + 多空温度条)
            const vHero = document.getElementById("beginnerVerdictHero");
            const vTitle = document.getElementById("beginnerVerdictTitle");
            const vSub = document.getElementById("beginnerVerdictSub");
            const ipoNotice = document.getElementById("pIpoNoticeBadge");
            const verdict = buildBeginnerVerdict({
                isIpo, isCrashHalt, isCoolingHalt, isBrokenBearish, isStop,
                isPitWatch, isPit, isBrokenRebound, isTakeProfit, isDeepPullback,
                isInverseHedge, isBullTrend
            });
            const regCodeNow = (AI_PREDICTIONS && (AI_PREDICTIONS.market_regime || (AI_PREDICTIONS.macro_weather && AI_PREDICTIONS.macro_weather.market_regime))) || "NEUTRAL_CHOP";
            const isExempt = ["SQQQ", "SOXS", "VIXY", "TLT", "GLD", "XAUUSD"].includes(ticker);
            if (vHero) {
                vHero.style.background = verdict.bg;
                vHero.style.borderColor = verdict.border;
            }
            if (vTitle) {
                vTitle.innerText = verdict.title;
                vTitle.style.color = verdict.color;
            }
            if (vSub) {
                const regimeNote = (regCodeNow === "BEAR_DEFENSE" && !isExempt && !isIpo && !isCrashHalt && !isCoolingHalt)
                    ? " 当前大盘偏弱，建议控制仓位、切勿重仓。"
                    : "";
                vSub.innerText = verdict.sub + regimeNote;
            }
            if (ipoNotice) {
                ipoNotice.style.display = isIpo ? "inline-block" : "none";
                if (isIpo) {
                    ipoNotice.style.color = pal.accent;
                    ipoNotice.style.background = pal.cardBg;
                    ipoNotice.style.borderColor = pal.accentBorder;
                }
            }

            // 三列价格格：1.参考买点 / 观察价  2.目标止盈  3.跌破必砍 (短线止损)
            const optEl = document.getElementById("pOptimalLimit");
            const noteEl = document.getElementById("pOptimalNote");
            const tpBegEl = document.getElementById("pBeginnerTakeProfit");
            const tpPctEl = document.getElementById("pBeginnerTakeProfitPct");
            const stopEl = document.getElementById("pEntryStopLoss");
            const stopDistEl = document.getElementById("pEntryStopDist");
            const proEntryRef = document.getElementById("pProEntryRef");
            const proStopRef = document.getElementById("pProStopRef");
            const priceLabelEl = document.getElementById("pEntryPriceLabel");
            const stopLabelEl = document.getElementById("pEntryStopLabel");
            if (priceLabelEl) priceLabelEl.innerText = "💡 参考挂单价";
            if (stopLabelEl) stopLabelEl.innerText = "🛡️ 防守止损线 (日内短线)";

            let buyShown = "--", buySub = "--", buyClr = pal.warn;
            let stopShown = "--", stopSub = "--", stopClr = pal.down;

            if (isBrokenRebound) {
                if (priceLabelEl) priceLabelEl.innerText = "💡 反抽参考价";
                buyShown = (optimalLimit && optimalLimit > 0) ? ("$" + optimalLimit.toFixed(2)) : "--";
                const diffPct = (curActive > 0 && optimalLimit > 0) ? ((optimalLimit - curActive) / curActive * 100) : 0;
                buySub = `回踩低吸 · 距现价 ${diffPct >= 0 ? '+' : ''}${diffPct.toFixed(1)}%`;
                buyClr = pal.warn;
                stopShown = stopLoss > 0 ? ("$" + stopLoss.toFixed(2)) : "--";
                const slSpot = (curActive > 0 && stopLoss > 0) ? ((stopLoss - curActive) / curActive * 100) : null;
                stopSub = slSpot !== null ? `距现价 ${slSpot >= 0 ? '+' : ''}${slSpot.toFixed(1)}% · 短线收紧` : "距现价 --";
            } else if (isBrokenBearish) {
                if (priceLabelEl) priceLabelEl.innerText = "🚫 暂缓左侧接刀";
                if (stopLabelEl) stopLabelEl.innerText = "🧭 下方支撑观察 S1";
                buyShown = "暂缓";
                const _vwap = Number(data.vwap || curActive || 0);
                buySub = _vwap > 0 ? `等站上 $${_vwap.toFixed(2)} 再看` : "等站稳均价线再看";
                buyClr = pal.down;
                const brokenS1 = Number(data.broken_s1 || 0);
                stopShown = brokenS1 > 0 ? ("$" + brokenS1.toFixed(2)) : "--";
                const s1Spot = (curActive > 0 && brokenS1 > 0) ? ((brokenS1 - curActive) / curActive * 100) : null;
                stopSub = s1Spot !== null ? `距现价 ${s1Spot >= 0 ? '+' : ''}${s1Spot.toFixed(1)}% · 观察支撑` : "破坑后极限支撑";
                stopClr = pal.warn;
            } else if (isInverseHedge) {
                buyShown = isTakeProfit ? "逢高减仓" : "短线对冲";
                buySub = isTakeProfit ? "反向工具宜分批落袋" : "仅作大盘下跌避险";
                buyClr = isTakeProfit ? pal.purple : pal.accent;
                stopShown = stopLoss > 0 ? ("$" + stopLoss.toFixed(2)) : "破EMA5走";
                const slSpot = (curActive > 0 && stopLoss > 0) ? ((stopLoss - curActive) / curActive * 100) : null;
                stopSub = slSpot !== null ? `距现价 ${slSpot >= 0 ? '+' : ''}${slSpot.toFixed(1)}%` : "EMA5 防线";
            } else if (isCrashHalt) {
                buyShown = "暂缓买入";
                buySub = "暴跌熔断 · 严禁接刀";
                buyClr = pal.down;
                stopShown = "暂不挂单";
                stopSub = "禁止抄底 · 无短线单";
                stopClr = pal.sub;
            } else if (isCoolingHalt) {
                buyShown = "暂缓买入";
                buySub = "冷却观察 · 等日线企稳";
                buyClr = pal.down;
                stopShown = "暂不挂单";
                stopSub = "等待企稳 · 无短线单";
                stopClr = pal.sub;
            } else if (isIpo) {
                buyShown = (optimalLimit && optimalLimit > 0) ? ("$" + optimalLimit.toFixed(2)) : "观察中";
                const diffPct = (curActive > 0 && optimalLimit > 0) ? ((optimalLimit - curActive) / curActive * 100) : null;
                buySub = diffPct !== null ? `IPO基准 · 距现价 ${diffPct >= 0 ? '+' : ''}${diffPct.toFixed(1)}%` : "新股上市价格发现期";
                buyClr = pal.accent;
                stopShown = stopLoss > 0 ? ("$" + stopLoss.toFixed(2)) : "暂不设单";
                stopSub = stopLoss > 0 && curActive > 0 ? `距现价 ${((stopLoss - curActive) / curActive * 100).toFixed(1)}%` : "IPO观察期防剧烈波动";
                stopClr = stopLoss > 0 ? pal.down : pal.sub;
            } else {
                buyShown = (optimalLimit && optimalLimit > 0) ? ("$" + optimalLimit.toFixed(2)) : "--";
                const diffPct = (curActive > 0 && optimalLimit > 0) ? ((optimalLimit - curActive) / curActive * 100) : 0;
                if (optimalLimit > 0 && diffPct >= -0.05) {
                    buySub = "已达参考低吸区";
                    buyClr = pal.up;
                } else {
                    buySub = optimalLimit > 0 ? `较现价 ${diffPct.toFixed(1)}%` : "--";
                    buyClr = pal.warn;
                }
                stopShown = stopLoss > 0 ? ("$" + stopLoss.toFixed(2)) : "--";
                const slSpot = (curActive > 0 && stopLoss > 0) ? ((stopLoss - curActive) / curActive * 100) : null;
                stopSub = slSpot !== null ? `距现价 ${slSpot >= 0 ? '+' : ''}${slSpot.toFixed(1)}%` : "距现价 --";
            }

            if (optEl) { optEl.innerText = buyShown; optEl.style.color = buyClr; }
            if (noteEl) { noteEl.innerText = buySub; noteEl.style.color = pal.sub; }
            if (stopEl) { stopEl.innerText = stopShown; stopEl.style.color = stopClr; }
            if (stopDistEl) { stopDistEl.innerText = stopSub; stopDistEl.style.color = stopClr === pal.sub ? pal.sub : pal.down; }
            if (proEntryRef) proEntryRef.innerText = buyShown;
            if (proStopRef) proStopRef.innerText = stopShown;

            // 7. AI 模型胜率 / 温度条 / 目标止盈 / 专业组 ②③ 同步更新
            const aiBox = document.getElementById("aiPredictionBox");
            const actText = document.getElementById("pAiActionText");
            const secStar = document.getElementById("pAiSectorStar");
            const shEl = document.getElementById("pAiShrinkBadge");
            const lwrEl = document.getElementById("pAiLongWr");
            const swrEl = document.getElementById("pAiShortWr");
            const lBar = document.getElementById("pAiLongBar");
            const sBar = document.getElementById("pAiShortBar");
            const trendHint = document.getElementById("beginnerTrendHint");
            const lRawEl = document.getElementById("pAiLongRaw");
            const pcrEl = document.getElementById("pAiPcrSentiment");
            const sRawEl = document.getElementById("pAiShortRaw");

            if (aiItem) {
                if (aiBox) aiBox.style.display = "flex";
                const rkBadge = document.getElementById("pAiRankBadge");
                if (rkBadge) {
                    const longRank = aiItem.rank !== undefined ? aiItem.rank : "--";
                    const shortRank = aiItem.short_rank !== undefined ? aiItem.short_rank : "--";
                    rkBadge.innerText = `多 #${longRank} · 空 #${shortRank}`;
                }
                const tvSym = SYMBOL_MAP[ticker] || ("NASDAQ:" + ticker);
                const tvExtBtn = document.getElementById("pAiTvLink");
                if (tvExtBtn) tvExtBtn.href = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tvSym)}`;

                const longWr = aiItem.calibrated_win_rate_pct !== undefined ? Number(aiItem.calibrated_win_rate_pct) : 50.0;
                const shortWr = aiItem.calibrated_short_win_rate_pct !== undefined ? Number(aiItem.calibrated_short_win_rate_pct) : 50.0;

                if (actText) {
                    if (aiItem.is_short_covering && aiItem.action) {
                        actText.innerText = aiItem.action;
                    } else if (aiItem.short_action && (shortWr > longWr || (aiItem.short_action.includes("🩸") && !aiItem.action))) {
                        actText.innerText = aiItem.short_action;
                    } else {
                        actText.innerText = aiItem.action || aiItem.short_action || "--";
                    }
                    actText.style.color = pal.text;
                }
                if (secStar) {
                    secStar.innerText = `${aiItem.sector_cn || curCat} ${aiItem.sector_rating ? aiItem.sector_rating.slice(0, 5) : ''}`.trim();
                    secStar.style.color = pal.warn;
                }

                if (shEl) {
                    if (aiItem.is_short_sample && aiItem.sample_shrinkage_tag) {
                        shEl.innerText = String(aiItem.sample_shrinkage_tag);
                        shEl.style.display = "inline-block";
                        shEl.style.color = pal.warn;
                        shEl.style.background = pal.warnBg;
                        shEl.style.borderColor = pal.warnBorder;
                    } else {
                        shEl.style.display = "none";
                    }
                }

                // 目标止盈格 (Layer A 第 2 格)
                const tpPrice = Number(aiItem.target_profit_price || 0);
                if (tpBegEl && tpPctEl) {
                    if (tpPrice > 0) {
                        const tpSpotPct = curActive > 0 ? ((tpPrice - curActive) / curActive * 100) : Number(aiItem.target_profit_pct || 0);
                        tpBegEl.innerText = "$" + tpPrice.toFixed(2);
                        tpBegEl.style.color = pal.up;
                        tpPctEl.innerText = `距现价 ${tpSpotPct >= 0 ? '+' : ''}${tpSpotPct.toFixed(1)}%`;
                        tpPctEl.style.color = pal.up;
                    } else {
                        tpBegEl.innerText = "--";
                        tpPctEl.innerText = "暂无目标位";
                    }
                }

                // 温度条 (必须严格保留 pure `${longWr.toFixed(1)}%` 供既有断言解析!)
                if (lwrEl) lwrEl.innerText = `${longWr.toFixed(1)}%`;
                if (swrEl) swrEl.innerText = `${shortWr.toFixed(1)}%`;

                const totalWr = (longWr + shortWr) || 100.0;
                const longRatio = Math.max(10, Math.min(90, (longWr / totalWr) * 100));
                const shortRatio = 100 - longRatio;
                if (lBar) lBar.style.width = `${longRatio.toFixed(1)}%`;
                if (sBar) sBar.style.width = `${shortRatio.toFixed(1)}%`;
                if (trendHint) {
                    if (longWr - shortWr >= 10) { trendHint.innerText = "偏多占优"; trendHint.style.color = pal.up; }
                    else if (shortWr - longWr >= 10) { trendHint.innerText = "偏空占优"; trendHint.style.color = pal.down; }
                    else { trendHint.innerText = "多空拉锯"; trendHint.style.color = pal.warn; }
                }

                if (lRawEl) {
                    const rawL = aiItem.predicted_win_rate_pct !== undefined ? aiItem.predicted_win_rate_pct : (aiItem.model_raw_prob_pct || 50.0);
                    lRawEl.innerText = `原始多: ${Number(rawL).toFixed(1)}%`;
                }
                if (pcrEl) {
                    const noEquityOpts = ["XAUUSD", "BTC-USD"].includes(ticker) || (aiItem.has_options === false) || (data && data.has_options === false);
                    if (noEquityOpts) {
                        pcrEl.innerText = "⚪ 无美股期权";
                    } else if (aiItem.dollar_pcr !== undefined) {
                        pcrEl.innerText = `PCR: ${Number(aiItem.dollar_pcr).toFixed(2)} ${aiItem.pcr_sentiment || ''}`.trim();
                    } else {
                        pcrEl.innerText = aiItem.pcr_sentiment || "期权PCR: 正常";
                    }
                }
                if (sRawEl) {
                    const rawS = aiItem.model_raw_short_prob_pct !== undefined ? aiItem.model_raw_short_prob_pct : (aiItem.short_raw_prob_pct !== undefined ? aiItem.short_raw_prob_pct : (aiItem.short_model_raw_prob_pct || 50.0));
                    sRawEl.innerText = `原始空: ${Number(rawS).toFixed(1)}%`;
                }

                const tpEl = document.getElementById("pAiTargetPrice");
                if (tpEl) tpEl.innerHTML = `$${(aiItem.target_profit_price || 0).toFixed(2)} <span style="font-size:10px; opacity:0.85;">(+${(aiItem.target_profit_pct || 0).toFixed(1)}%)</span>`;
                const slEl = document.getElementById("pAiStopPrice");
                if (slEl) slEl.innerHTML = `$${(aiItem.stop_loss_price || 0).toFixed(2)} <span style="font-size:10px; opacity:0.85;">(-${(aiItem.stop_loss_pct || 0).toFixed(1)}%)</span>`;
                const stpEl = document.getElementById("pAiShortTargetPrice");
                if (stpEl) stpEl.innerHTML = `$${(aiItem.short_target_profit_price || 0).toFixed(2)} <span style="font-size:10px; opacity:0.85;">(-${(aiItem.short_target_profit_pct || 0).toFixed(1)}%)</span>`;
                const sslEl = document.getElementById("pAiShortStopPrice");
                if (sslEl) sslEl.innerHTML = `$${(aiItem.short_stop_loss_price || 0).toFixed(2)} <span style="font-size:10px; opacity:0.85;">(+${(aiItem.short_stop_loss_pct || 0).toFixed(1)}%)</span>`;

                const fhEl = document.getElementById("pAiFourHDiv");
                if (fhEl) fhEl.innerText = aiItem.four_h_div || "➖ 4H 均线震荡纠缠";
                const mlEl = document.getElementById("pAiMultiLevels");
                if (mlEl) {
                    const s1v = aiItem.s1 !== undefined ? `$${Number(aiItem.s1).toFixed(2)}` : "--";
                    const s2v = aiItem.s2 !== undefined ? `$${Number(aiItem.s2).toFixed(2)}` : "--";
                    const r1v = aiItem.r1 !== undefined ? `$${Number(aiItem.r1).toFixed(2)}` : "--";
                    const r2v = aiItem.r2 !== undefined ? `$${Number(aiItem.r2).toFixed(2)}` : "--";
                    mlEl.innerHTML = `<span style="color:${pal.up};">支撑 S1:${s1v} / S2:${s2v}</span><span style="color:${pal.down};">阻力 R1:${r1v} / R2:${r2v}</span>`;
                }
                const scScriptEl = document.getElementById("pAiScenarioScript");
                if (scScriptEl) scScriptEl.innerText = aiItem.scenario_script || "--";
            } else {
                if (aiBox) aiBox.style.display = "none";
                if (actText) actText.innerText = isIpo ? "🆕 IPO 新股观察期" : (curSignal || "--");
                if (secStar) secStar.innerText = curCat;
                if (shEl) shEl.style.display = "none";
                if (tpBegEl) { tpBegEl.innerText = "--"; tpBegEl.style.color = pal.sub; }
                if (tpPctEl) { tpPctEl.innerText = isIpo ? "需满5日生成目标" : "暂无目标位"; tpPctEl.style.color = pal.sub; }
                if (lwrEl) lwrEl.innerText = "--%";
                if (swrEl) swrEl.innerText = "--%";
                if (lBar) lBar.style.width = "50%";
                if (sBar) sBar.style.width = "50%";
                if (trendHint) { trendHint.innerText = isIpo ? "样本积累中" : "暂无统计"; trendHint.style.color = pal.sub; }
                if (lRawEl) lRawEl.innerText = "原始多: --%";
                if (sRawEl) sRawEl.innerText = "原始空: --%";
                if (pcrEl) pcrEl.innerText = "⚪ 无美股期权";
            }

            // 7.5 空头占优 / 建议买入反向ETF对冲：防看反切换（将目标止盈/跌破必砍切换为 上方压力(R1) / 下方支撑(S1)）
            const tileTpLbl = document.getElementById("pTileTpLbl");
            const tileStopLbl = document.getElementById("pTileStopLbl");
            const bearDefenseNote = document.getElementById("pBearDefenseNote");
            const actStrNow = actText ? String(actText.innerText || "") : "";
            const isBearDefenseCard = (!isInverseHedge && !isIpo) && (
                Boolean(isBrokenBearish)
                || actStrNow.includes("建议买入反向ETF对冲")
                || actStrNow.includes("空头占优")
                || actStrNow.includes("强力做空")
                || actStrNow.includes("逢高做空")
            );
            if (isBearDefenseCard) {
                if (tileTpLbl) tileTpLbl.innerText = "上方压力 (R1)";
                if (tileStopLbl) tileStopLbl.innerText = "下方支撑 (S1)";
                if (bearDefenseNote) bearDefenseNote.style.display = "block";
                const r1Px = Number((aiItem && aiItem.r1) || data.broken_r1 || (aiItem && aiItem.target_profit_price) || 0);
                if (tpBegEl && tpPctEl) {
                    if (r1Px > 0) {
                        const r1Diff = curActive > 0 ? ((r1Px - curActive) / curActive * 100) : 0;
                        tpBegEl.innerText = "$" + r1Px.toFixed(2);
                        tpBegEl.style.color = pal.down;
                        tpPctEl.innerText = `距现价 ${r1Diff >= 0 ? '+' : ''}${r1Diff.toFixed(1)}% · 反抽压力`;
                        tpPctEl.style.color = pal.down;
                    } else {
                        tpBegEl.innerText = "--";
                        tpBegEl.style.color = pal.sub;
                        tpPctEl.innerText = "上方压力观察";
                        tpPctEl.style.color = pal.sub;
                    }
                }
                const s1Px = Number((aiItem && aiItem.s1) || data.broken_s1 || (aiItem && aiItem.short_target_profit_price) || stopLoss || 0);
                if (stopEl && stopDistEl) {
                    if (s1Px > 0) {
                        const s1Diff = curActive > 0 ? ((s1Px - curActive) / curActive * 100) : 0;
                        stopEl.innerText = "$" + s1Px.toFixed(2);
                        stopEl.style.color = pal.warn;
                        stopDistEl.innerText = `距现价 ${s1Diff >= 0 ? '+' : ''}${s1Diff.toFixed(1)}% · 支撑关注`;
                        stopDistEl.style.color = pal.warn;
                    } else {
                        stopEl.innerText = "--";
                        stopEl.style.color = pal.sub;
                        stopDistEl.innerText = "下方支撑观察";
                        stopDistEl.style.color = pal.sub;
                    }
                }
            } else {
                if (tileTpLbl) tileTpLbl.innerText = "目标止盈";
                if (tileStopLbl) tileStopLbl.innerText = "跌破必砍 (短线止损)";
                if (bearDefenseNote) bearDefenseNote.style.display = "none";
            }
            renderResilientLeadersStrip();

            // 8. 机构期权雷达 (Group ②)
            const optBox = document.getElementById("optionsAnomalyBox");
            const optUnavail = document.getElementById("optionsUnavailableNote");
            const pcrBadge = document.getElementById("pPcrSentimentBadge");
            if (optBox) {
                if (data.has_options) {
                    optBox.style.display = "flex";
                    if (optUnavail) optUnavail.style.display = "none";
                    if (pcrBadge) {
                        if (curActive > 0 && data.exp_low && data.exp_low > 0 && curActive < data.exp_low) {
                            pcrBadge.innerText = "🔴 击穿期权下轨";
                            pcrBadge.style.color = pal.down;
                            pcrBadge.style.background = pal.downBg;
                        } else if (curActive > 0 && data.exp_high && data.exp_high > 0 && curActive > data.exp_high) {
                            pcrBadge.innerText = "🔥 突破期权上轨";
                            pcrBadge.style.color = pal.up;
                            pcrBadge.style.background = pal.upBg;
                        } else {
                            pcrBadge.innerText = data.pcr_sentiment || "🟡 多空均衡";
                            pcrBadge.style.color = pal.isLight ? pal.text : (data.pcr_color || pal.warn);
                            pcrBadge.style.background = pal.elevatedBg;
                        }
                    }
                    const pcrVal = document.getElementById("pOptionsPcr");
                    if (pcrVal) {
                        const volPcrStr = data.pcr !== undefined ? data.pcr.toFixed(2) : "--";
                        const dlrPcrStr = data.dollar_pcr !== undefined ? data.dollar_pcr.toFixed(2) : volPcrStr;
                        pcrVal.innerText = `${volPcrStr} / 资金 ${dlrPcrStr}`;
                    }
                    const volVal = document.getElementById("pOptionsVolRatio");
                    if (volVal) {
                        const cv = data.call_vol ? (data.call_vol >= 1000 ? (data.call_vol / 1000).toFixed(1) + "k" : data.call_vol) : "0";
                        const pv = data.put_vol ? (data.put_vol >= 1000 ? (data.put_vol / 1000).toFixed(1) + "k" : data.put_vol) : "0";
                        volVal.innerHTML = `<span style="color:${pal.up};">Call ${cv}</span> vs <span style="color:${pal.down};">Put ${pv}</span>`;
                    }
                    const ivEl = document.getElementById("pOptionsIv");
                    if (ivEl) ivEl.innerText = (data.iv_atm && data.iv_atm > 0) ? `${data.iv_atm.toFixed(1)}%` : "--";
                    const expMoveEl = document.getElementById("pOptionsExpMove");
                    if (expMoveEl) expMoveEl.innerText = data.exp_move_str || "--";
                    const callStrike = document.getElementById("pTopCallStrike");
                    if (callStrike) callStrike.innerText = data.top_call_info || "--";
                    const putStrike = document.getElementById("pTopPutStrike");
                    if (putStrike) putStrike.innerText = data.top_put_info || "--";
                    const expTip = document.getElementById("pOptionsExpTip");
                    if (expTip) expTip.innerText = `*基于 ${data.options_exp || '近期主力周'} 权利金大单与 ATM Straddle 定价测算 (30m本地缓存·0额度)`;
                } else {
                    optBox.style.display = "none";
                    if (optUnavail) optUnavail.style.display = "block";
                    if (pcrBadge) {
                        pcrBadge.innerText = "⚪ 无美股期权";
                        pcrBadge.style.color = pal.sub;
                        pcrBadge.style.background = pal.elevatedBg;
                    }
                }
            }

            renderTickerNewsBox(ticker, data);
            updateStarUI();

            document.querySelectorAll(".stock-item").forEach(el => {
                el.classList.toggle("active", el.dataset.ticker === ticker);
            });

            updateCenterViewMeta(ticker);
            if (currentCenterView === "intraday_path") {
                renderIntradayPathChart(ticker);
            } else {
                loadTvWidget(currentTicker, currentInterval, currentStyle);
            }
        }

        // 快捷搜索框交互
        function initQuickSearch() {
            const input = document.getElementById("quickSearchInput");
            const dd = document.getElementById("quickSearchDropdown");
            if (!input || !dd) return;

            const allEntries = [];
            STOCKS_DATA.forEach(s => allEntries.push({ ticker: s.ticker, name: s.name || "", last: s.last || 0, pct: s.pct_change || 0 }));
            Object.keys(MACRO_DATA).forEach(k => {
                const m = MACRO_DATA[k];
                allEntries.push({ ticker: k, name: m.name || "", last: m.last || 0, pct: m.pct_change || 0 });
            });

            const renderHits = (q) => {
                const pal = getThemePalette();
                const term = String(q || "").trim().toLowerCase();
                if (!term) {
                    dd.classList.remove("show");
                    dd.innerHTML = "";
                    return;
                }
                const hits = allEntries.filter(x => x.ticker.toLowerCase().includes(term) || x.name.toLowerCase().includes(term)).slice(0, 10);
                if (hits.length === 0) {
                    dd.innerHTML = `<div style="padding:10px; font-size:12px; color:${pal.muted}; text-align:center;">未找到匹配标的</div>`;
                } else {
                    dd.innerHTML = hits.map(h => {
                        const c = h.pct >= 0 ? pal.up : pal.down;
                        return `<div class="qs-item" data-ticker="${escapeNewsHtml(h.ticker)}">
                            <div class="qs-left"><span class="qs-sym">${escapeNewsHtml(h.ticker)}</span><span class="qs-name">${escapeNewsHtml(h.name)}</span></div>
                            <div class="qs-right tabular" style="color:${c};">${h.last > 0 ? '$' + h.last.toFixed(2) : '--'} (${h.pct >= 0 ? '+' : ''}${h.pct.toFixed(2)}%)</div>
                        </div>`;
                    }).join("");
                    dd.querySelectorAll(".qs-item").forEach(el => {
                        el.onclick = () => {
                            const tk = el.getAttribute("data-ticker");
                            if (tk) {
                                selectTicker(tk, true);
                                input.value = "";
                                dd.classList.remove("show");
                            }
                        };
                    });
                }
                dd.classList.add("show");
            };

            input.addEventListener("input", () => renderHits(input.value));
            input.addEventListener("focus", () => { if (input.value.trim()) renderHits(input.value); });
            input.addEventListener("keydown", (e) => {
                if (e.key === "Enter") {
                    const first = dd.querySelector(".qs-item");
                    if (first) {
                        const tk = first.getAttribute("data-ticker");
                        if (tk) selectTicker(tk, true);
                        input.value = "";
                        dd.classList.remove("show");
                        input.blur();
                    }
                } else if (e.key === "Escape") {
                    dd.classList.remove("show");
                    input.blur();
                }
            });
            document.addEventListener("click", (e) => {
                if (!e.target.closest(".quick-search-wrap")) {
                    dd.classList.remove("show");
                }
            });
        }

        // 周期按钮监听
        document.querySelectorAll(".period-tab").forEach(tab => {
            tab.addEventListener("click", function() {
                document.querySelectorAll(".period-tab").forEach(t => t.classList.remove("active"));
                const minBtn = document.getElementById("minDropdownBtn");
                if (minBtn) minBtn.classList.remove("active");
                document.querySelectorAll(".dropdown-item").forEach(d => d.classList.remove("active"));

                this.classList.add("active");
                currentInterval = this.getAttribute("data-period");
                currentStyle = this.getAttribute("data-style") || "1";
                try {
                    localStorage.setItem("futu_kline_period", currentInterval);
                    localStorage.setItem("futu_kline_style", currentStyle);
                } catch(e) {}
                loadTvWidget(currentTicker, currentInterval, currentStyle);
            });
        });

        const minBtn = document.getElementById("minDropdownBtn");
        const minMenu = document.getElementById("minDropdownMenu");
        if (minBtn && minMenu) {
            minBtn.addEventListener("click", function(e) {
                e.stopPropagation();
                minMenu.classList.toggle("show");
            });
        }
        document.querySelectorAll(".dropdown-item").forEach(item => {
            item.addEventListener("click", function(e) {
                e.stopPropagation();
                document.querySelectorAll(".period-tab").forEach(t => t.classList.remove("active"));
                document.querySelectorAll(".dropdown-item").forEach(d => d.classList.remove("active"));
                this.classList.add("active");
                if (minBtn) minBtn.classList.add("active");
                const period = this.getAttribute("data-period");
                const label = this.getAttribute("data-label");
                const lblEl = document.getElementById("currentMinLabel");
                if (lblEl) lblEl.innerText = label;
                currentInterval = period;
                currentStyle = "1";
                if (minMenu) minMenu.classList.remove("show");
                try {
                    localStorage.setItem("futu_kline_period", currentInterval);
                    localStorage.setItem("futu_kline_style", currentStyle);
                } catch(e) {}
                loadTvWidget(currentTicker, currentInterval, currentStyle);
            });
        });
        window.addEventListener("click", function() {
            if (minMenu) minMenu.classList.remove("show");
        });

        document.querySelectorAll(".w-tab").forEach(tab => {
            tab.addEventListener("click", function() {
                const f = this.getAttribute("data-filter");
                setWatchlistFilter(f);
            });
        });

        let currentMobileTab = "watchlist";
        function switchMobileTab(tab) {
            const normalized = (tab === "options" || tab === "portfolio") ? "tactics" : (["watchlist", "chart", "tactics"].includes(tab) ? tab : "watchlist");
            currentMobileTab = normalized;
            try {
                localStorage.setItem("futu_mobile_tab", normalized);
            } catch(e) {}
            document.querySelectorAll(".m-nav-tab").forEach(t => {
                t.classList.toggle("active", t.getAttribute("data-tab") === normalized);
            });
            const mainEl = document.getElementById("futuMain");
            if (mainEl) {
                mainEl.classList.remove("m-view-watchlist", "m-view-chart", "m-view-tactics", "m-view-options", "m-view-portfolio");
                mainEl.classList.add(`m-view-${normalized}`);
            }
            if (tab === "options") {
                toggleProQuantDrawer(true);
            } else if (normalized === "chart") {
                setTimeout(() => window.dispatchEvent(new Event('resize')), 50);
            }
        }

        document.querySelectorAll(".m-period-tab").forEach(tab => {
            tab.addEventListener("click", function() {
                document.querySelectorAll(".m-period-tab").forEach(t => t.classList.remove("active"));
                this.classList.add("active");
                currentInterval = this.getAttribute("data-period");
                currentStyle = this.getAttribute("data-style") || "1";
                try {
                    localStorage.setItem("futu_kline_period", currentInterval);
                    localStorage.setItem("futu_kline_style", currentStyle);
                } catch(e) {}
                loadTvWidget(currentTicker, currentInterval, currentStyle);
            });
        });

        function toggleSidebar() {
            const sb = document.getElementById("sidebarLeft");
            const btn = document.getElementById("btnToggleSidebar");
            if (!sb) return;
            const isCol = sb.classList.toggle("collapsed");
            if (btn) {
                btn.innerText = isCol ? "▶" : "◀";
                btn.classList.toggle("btn-is-collapsed", isCol);
                btn.title = isCol ? "展开标的列表" : "折叠标的列表";
            }
        }

        function filterRightCards(cat) {
            try {
                localStorage.setItem("futu_right_panel_tab", cat);
            } catch(e) {}
            if (cat && cat !== "all") {
                toggleProQuantDrawer(true);
            }
        }

        function toggleCard(btn) {
            const box = btn.closest(".panel-box");
            if (!box) return;
            const isCol = box.classList.toggle("collapsed");
            btn.innerText = isCol ? "▼" : "▲";
        }

        function toggleFullscreen() {
            if (!document.fullscreenElement) {
                document.documentElement.requestFullscreen().catch(() => {});
            } else if (document.exitFullscreen) {
                document.exitFullscreen();
            }
        }

        function showToast(msg) {
            const t = document.getElementById("toastBox");
            if (!t) return;
            t.innerText = msg;
            t.style.display = "block";
            setTimeout(() => { t.style.display = "none"; }, 2600);
        }

        function toggleNotifDrawer() {
            const drawer = document.getElementById("notifDrawerOverlay");
            if (!drawer) return;
            const isVisible = drawer.style.display === "flex";
            drawer.style.display = isVisible ? "none" : "flex";
            if (!isVisible) renderNotifList(currentNotifCat);
        }

        function filterNotifCategory(cat) {
            currentNotifCat = cat;
            document.querySelectorAll(".n-tab").forEach(tab => {
                tab.classList.toggle("active", tab.dataset.cat === cat);
            });
            renderNotifList(cat);
        }

        function renderNotifList(cat = "all") {
            const pal = getThemePalette();
            const container = document.getElementById("notifListContainer");
            const badge = document.getElementById("notifBadge");
            const totalCountSpan = document.getElementById("notifTotalCount");

            const counts = {
                all: terminalNotifications.length,
                "macro_flash": 0,
                "defensive_flight": 0,
                "5m_entry": 0,
                "portfolio": 0,
                "golden_pit": 0,
                "stop_loss": 0,
                "take_profit": 0,
                "macro_weather": 0
            };
            terminalNotifications.forEach(n => {
                if (Object.prototype.hasOwnProperty.call(counts, n.category)) counts[n.category]++;
            });
            Object.keys(counts).forEach(k => {
                const el = document.getElementById("catCount_" + k);
                if (el) el.innerText = counts[k];
            });
            if (badge) {
                badge.innerText = terminalNotifications.length;
                badge.style.display = terminalNotifications.length > 0 ? "inline-block" : "none";
            }
            if (totalCountSpan) totalCountSpan.innerText = terminalNotifications.length + " 条";
            if (!container) return;

            const filtered = terminalNotifications.filter(n => cat === "all" || n.category === cat);
            if (filtered.length === 0) {
                container.innerHTML = `<div style="text-align: center; color: ${pal.muted}; padding: 60px 0; font-size: 13px;"><div style="font-size: 32px; margin-bottom: 8px;">📭</div>当前分类暂无新预警消息</div>`;
                return;
            }

            let htmlOut = "";
            filtered.forEach(n => {
                let levelClass = "level-info";
                if (n.level === "danger") levelClass = "level-danger";
                else if (n.level === "success") levelClass = "level-success";
                else if (n.level === "warning") levelClass = "level-warning";
                else if (n.level === "purple") levelClass = "level-purple";

                const clickAction = n.ticker ? `onclick="jumpToTicker('${escapeNewsHtml(n.ticker)}')"` : "";
                const kwBadgesHtml = (Array.isArray(n.keywords) && n.keywords.length > 0)
                    ? `<div style="display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px;">` + n.keywords.slice(0, 4).map(k => `<span class="flash-kw-pill">[${escapeNewsHtml(k)}]</span>`).join("") + `</div>`
                    : "";
                const cardTitle = n._safeText ? escapeNewsHtml(n.title || "") : (n.title || "");
                const cardDesc = n._safeText ? escapeNewsHtml(n.desc || "") : (n.desc || "");

                htmlOut += `
                    <div class="notif-card ${levelClass}" ${clickAction}>
                        <div style="display: flex; justify-content: space-between; align-items: center; gap: 6px;">
                            <div style="display: flex; align-items: center; gap: 6px; min-width: 0;">
                                <span style="font-weight: 800; font-size: 13px; color: ${pal.text};">${cardTitle}</span>
                                ${n.ticker ? `<span style="font-size: 10px; background: ${pal.accentBg}; color: ${pal.accent}; padding: 1px 6px; border-radius: 4px; font-weight: 700;">${escapeNewsHtml(n.ticker)}</span>` : ""}
                            </div>
                            <span class="tabular" style="font-size: 10px; color: ${pal.muted}; white-space: nowrap;">${escapeNewsHtml(n.time || "")}</span>
                        </div>
                        ${kwBadgesHtml}
                        <div style="font-size: 12px; color: ${pal.sub}; line-height: 1.45;">
                            ${cardDesc}
                        </div>
                    </div>
                `;
            });
            container.innerHTML = htmlOut;
        }

        function jumpToTicker(ticker) {
            if (!ticker) return;
            selectTicker(ticker, true);
            toggleNotifDrawer();
            showToast("🎯 已跳转至标的：" + ticker);
        }

        function clearAllNotifs() {
            terminalNotifications = [];
            renderNotifList("all");
            fetch(`${API_BASE}/api/clear_notifications`)
                .then(() => showToast("🗑️ 已清空全部预警通知"))
                .catch(() => showToast("🗑️ 已清空预警通知"));
        }

        // 声音预警引擎
        let isSoundEnabled = true;
        try {
            const s = localStorage.getItem("terminalSoundEnabled");
            if (s !== null) isSoundEnabled = (s === "true");
        } catch(e) {}

        let audioCtx = null;
        function getAudioContext() {
            if (!audioCtx) {
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                if (AudioContext) audioCtx = new AudioContext();
            }
            if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
            return audioCtx;
        }

        let isAudioUnlocked = false;
        function unlockAudioEngine() {
            const ctx = getAudioContext();
            if (ctx && ctx.state === "suspended") {
                ctx.resume().then(() => {
                    isAudioUnlocked = true;
                    if (soundQueue.length > 0) processSoundQueue();
                }).catch(() => {});
            } else {
                isAudioUnlocked = true;
                if (soundQueue.length > 0) processSoundQueue();
            }
        }
        ["click", "touchstart", "keydown", "mousedown"].forEach(evt => {
            window.addEventListener(evt, unlockAudioEngine, { passive: true });
        });

        function toggleSoundAlerts() {
            isSoundEnabled = !isSoundEnabled;
            try {
                localStorage.setItem("terminalSoundEnabled", isSoundEnabled ? "true" : "false");
            } catch(e) {}
            updateSoundUI();
            showToast(isSoundEnabled ? "🔊 声音预警已开启" : "🔇 声音预警已静音");
            if (isSoundEnabled) {
                getAudioContext();
                playAlertSound("macro_weather", true);
            }
        }

        function updateSoundUI() {
            const pal = getThemePalette();
            const icon = document.getElementById("soundIcon");
            const label = document.getElementById("drawerSoundLabel");
            const btn = document.getElementById("btnSoundToggle");
            if (icon) icon.innerText = isSoundEnabled ? "🔊" : "🔇";
            if (btn) {
                btn.title = isSoundEnabled ? "声音提示：已开启 (点击切换静音)" : "声音提示：已静音 (点击开启)";
                btn.style.color = isSoundEnabled ? pal.up : pal.sub;
            }
            if (label) {
                label.innerText = isSoundEnabled ? "🔊 声音预警已开启" : "🔇 声音预警已静音";
                label.style.color = isSoundEnabled ? pal.up : pal.down;
            }
        }

        const SOUND_FILE_MAP = {
            "defensive_flight": "sounds/defensive_flight.wav",
            "5m_entry": "sounds/5m_entry.wav",
            "portfolio": "sounds/risk_alarm.wav",
            "golden_pit": "sounds/golden_pit.wav",
            "stop_loss": "sounds/breakdown.wav",
            "take_profit": "sounds/take_profit.wav",
            "macro_weather": "sounds/weather.wav"
        };

        const soundQueue = [];
        let isPlayingSoundQueue = false;

        function queueAlertSound(category, forcePlay = false) {
            if (!isSoundEnabled && !forcePlay) return;
            soundQueue.push({ category, forcePlay });
            processSoundQueue();
        }

        function processSoundQueue() {
            if (isPlayingSoundQueue || soundQueue.length === 0) return;
            if (!isSoundEnabled) {
                soundQueue.length = 0;
                return;
            }
            isPlayingSoundQueue = true;
            const item = soundQueue.shift();
            playAlertSound(item.category, item.forcePlay);
            setTimeout(() => {
                isPlayingSoundQueue = false;
                if (soundQueue.length > 0) processSoundQueue();
            }, 520);
        }

        function playAlertSound(category, forcePlay = false) {
            if (!isSoundEnabled && !forcePlay) return;
            const filePath = SOUND_FILE_MAP[category] || "sounds/5m_entry.wav";
            try {
                const audio = new Audio(filePath);
                audio.volume = 0.88;
                const p = audio.play();
                if (p !== undefined) {
                    p.then(() => { isAudioUnlocked = true; }).catch(err => {
                        synthAlertSound(category);
                        if (!isAudioUnlocked && err && err.name === "NotAllowedError") {
                            showToast("🔊 收到预警！点击页面任意处即可开启声音播放");
                        }
                    });
                }
            } catch(e) {
                synthAlertSound(category);
            }
        }

        function synthAlertSound(category) {
            const ctx = getAudioContext();
            if (!ctx) return;
            const now = ctx.currentTime;
            if (category === "defensive_flight") {
                playTone(ctx, 987.77, now + 0.00, 0.10, "sawtooth", 0.45);
                playTone(ctx, 739.99, now + 0.11, 0.10, "sawtooth", 0.45);
                playTone(ctx, 987.77, now + 0.22, 0.10, "sawtooth", 0.48);
                playTone(ctx, 739.99, now + 0.33, 0.16, "sawtooth", 0.50);
            } else if (category === "5m_entry") {
                playTone(ctx, 659.25, now, 0.10, "sine", 0.35);
                playTone(ctx, 880.00, now + 0.11, 0.16, "triangle", 0.45);
            } else if (category === "portfolio") {
                playTone(ctx, 987.77, now, 0.12, "sawtooth", 0.40);
                playTone(ctx, 783.99, now + 0.16, 0.18, "sawtooth", 0.45);
            } else if (category === "golden_pit") {
                playTone(ctx, 523.25, now, 0.35, "sine", 0.30);
                playTone(ctx, 659.25, now + 0.05, 0.35, "sine", 0.25);
            } else if (category === "stop_loss") {
                playSweep(ctx, 400, 180, now, 0.28, "sawtooth", 0.40);
            } else if (category === "take_profit") {
                playTone(ctx, 587.33, now, 0.08, "sine", 0.30);
                playTone(ctx, 739.99, now + 0.08, 0.08, "sine", 0.35);
                playTone(ctx, 880.00, now + 0.16, 0.08, "triangle", 0.40);
                playTone(ctx, 1174.66, now + 0.24, 0.18, "triangle", 0.45);
            } else {
                playTone(ctx, 440.00, now, 0.14, "sine", 0.30);
                playTone(ctx, 554.37, now + 0.15, 0.22, "sine", 0.35);
            }
        }

        function playTone(ctx, freq, startTime, duration, type, maxGain) {
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
        }

        function playSweep(ctx, startFreq, endFreq, startTime, duration, type, maxGain) {
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
        }

        let knownNotifIds = new Set();
        let isFirstEverVisit = false;
        try {
            const rawSaved = localStorage.getItem("terminalKnownNotifIds");
            if (rawSaved === null) {
                isFirstEverVisit = true;
            } else {
                const savedIds = JSON.parse(rawSaved);
                if (Array.isArray(savedIds)) savedIds.forEach(id => knownNotifIds.add(id));
            }
        } catch(e) {}

        function persistKnownNotifIds() {
            try {
                const arr = Array.from(knownNotifIds).slice(-200);
                localStorage.setItem("terminalKnownNotifIds", JSON.stringify(arr));
            } catch(e) {}
        }

        if (isFirstEverVisit) {
            terminalNotifications.forEach(n => { if (n && n.id) knownNotifIds.add(n.id); });
            persistKnownNotifIds();
        } else {
            const initialFresh = [];
            terminalNotifications.forEach(n => {
                if (n && n.id && !knownNotifIds.has(n.id)) {
                    knownNotifIds.add(n.id);
                    if (n.category !== "macro_flash") initialFresh.push(n);
                }
            });
            if (initialFresh.length > 0) {
                persistKnownNotifIds();
                setTimeout(() => {
                    initialFresh.slice(0, 6).forEach(item => queueAlertSound(item.category || "5m_entry"));
                    showToast(`🔔 收到 ${initialFresh.length} 条全新实盘量化预警！`);
                }, 800);
            }
        }

        function handleIncomingNotifications(newNotifs) {
            if (!Array.isArray(newNotifs)) return;
            rawTradingNotifications = newNotifs;
            const merged = mergeNotificationsWithFlashes(rawTradingNotifications, latestMarketNews);
            let freshItems = [];
            merged.forEach(n => {
                if (n && n.id && !knownNotifIds.has(n.id)) {
                    knownNotifIds.add(n.id);
                    if (n.category !== "macro_flash") freshItems.push(n);
                }
            });
            terminalNotifications = merged;
            renderNotifList(currentNotifCat);
            if (freshItems.length > 0) {
                persistKnownNotifIds();
                freshItems.slice(0, 6).forEach(item => queueAlertSound(item.category || "5m_entry"));
                showToast(`🔔 收到 ${freshItems.length} 条全新量化预警...`);
            }
        }

        function pollLatestNotifications() {
            const isLocal = window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost";
            const notifUrl = isLocal ? `${API_BASE}/api/notifications` : `.terminal_notifications.json?_t=${Date.now()}`;
            fetch(notifUrl)
                .then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
                .then(res => {
                    const list = Array.isArray(res) ? res : (res && Array.isArray(res.notifications) ? res.notifications : null);
                    if (list) handleIncomingNotifications(list);
                })
                .catch(() => {});

            if (window.location.protocol.startsWith("http")) {
                fetch(`data_cache/latest_market_news.json?_t=${Date.now()}`)
                    .then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
                    .then(newsObj => {
                        if (newsObj && typeof newsObj === "object") {
                            latestMarketNews = newsObj;
                            terminalNotifications = mergeNotificationsWithFlashes(rawTradingNotifications, latestMarketNews);
                            terminalNotifications.forEach(n => { if (n && n.id) knownNotifIds.add(n.id); });
                            refreshMacroBannerDOM(latestMarketNews);
                            renderNotifList(currentNotifCat);
                            renderTickerNewsBox(currentTicker, MACRO_DATA[currentTicker] || STOCKS_DATA.find(x => x.ticker === currentTicker));
                        }
                    })
                    .catch(() => {});
            }
        }

        function scrollNotifCategory(delta) {
            const bar = document.getElementById("notifCategoryBar") || document.querySelector(".notif-category-bar");
            if (!bar) return;
            try { bar.scrollBy({ left: delta, behavior: "smooth" }); } catch(e) { bar.scrollLeft += delta; }
        }

        function enableHorizontalDragScroll(el) {
            if (!el || el.dataset.dragScrollBound === "1") return;
            el.dataset.dragScrollBound = "1";
            el.style.cursor = "grab";
            el.style.userSelect = "none";

            let isDown = false, startX = 0, startScrollLeft = 0, hasDragged = false, suppressNextClick = false;
            const getEventX = (ev) => (typeof ev.clientX === "number" && (ev.clientX !== 0 || !ev.pageX)) ? ev.clientX : (typeof ev.pageX === "number" ? ev.pageX : (ev.screenX || 0));

            const onMouseDown = (e) => {
                if (e.button !== undefined && e.button !== 0) return;
                isDown = true; hasDragged = false; startX = getEventX(e); startScrollLeft = el.scrollLeft;
                el.classList.add("is-dragging"); el.style.cursor = "grabbing"; el.style.scrollBehavior = "auto";
            };
            const onMouseMove = (e) => {
                if (!isDown) return;
                const walk = getEventX(e) - startX;
                if (Math.abs(walk) > 5) hasDragged = true;
                if (hasDragged && e.cancelable) e.preventDefault();
                el.scrollLeft = startScrollLeft - walk;
            };
            const endDrag = () => {
                if (!isDown) return;
                isDown = false; el.classList.remove("is-dragging"); el.style.cursor = "grab";
                if (hasDragged) {
                    suppressNextClick = true;
                    setTimeout(() => { suppressNextClick = false; }, 120);
                }
                hasDragged = false;
            };
            el.addEventListener("mousedown", onMouseDown);
            el.addEventListener("mousemove", onMouseMove);
            el.addEventListener("mouseup", endDrag);
            el.addEventListener("mouseleave", endDrag);
            window.addEventListener("mousemove", onMouseMove);
            window.addEventListener("mouseup", endDrag);
            el.addEventListener("click", (e) => {
                if (suppressNextClick) {
                    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
                    suppressNextClick = false;
                }
            }, true);
            el.addEventListener("wheel", (e) => {
                if (e.deltaY !== 0 || e.deltaX !== 0) {
                    if (e.cancelable) e.preventDefault();
                    el.scrollLeft += (e.deltaY !== 0 ? e.deltaY : e.deltaX);
                }
            }, { passive: false });
        }

        function initAllHorizontalScrollBars() {
            document.querySelectorAll(".notif-category-bar, .mobile-period-bar").forEach(bar => enableHorizontalDragScroll(bar));
        }

        window.addEventListener("DOMContentLoaded", function() {
            initAllHorizontalScrollBars();
            initQuickSearch();

            // 1. 恢复主题 (默认深色 dark)
            let savedTheme = "dark";
            try {
                if (localStorage.getItem("futu_theme") === "light") savedTheme = "light";
            } catch(e) {}
            applyTheme(savedTheme, true);

            // 2. 还原专业量化抽屉展开状态 (默认严格折叠)
            let proOpen = false;
            try {
                proOpen = localStorage.getItem("futu_pro_drawer_open") === "true";
            } catch(e) {}
            toggleProQuantDrawer(proOpen);

            // 3. 还原左侧筛选状态
            let savedFilter = "all";
            try {
                savedFilter = localStorage.getItem("futu_watchlist_filter") || "all";
                if (savedFilter === "starred") savedFilter = "all";
            } catch(e) {}
            syncFilterControlsUI(savedFilter);
            renderWatchlist(savedFilter);

            // 4. 还原左侧滚动位置
            const wlContainer = document.getElementById("watchlistContainer");
            if (wlContainer) {
                wlContainer.addEventListener("scroll", function() {
                    try { localStorage.setItem("futu_watchlist_scroll", wlContainer.scrollTop); } catch(e) {}
                }, { passive: true });
                try {
                    const savedScroll = localStorage.getItem("futu_watchlist_scroll");
                    if (savedScroll) {
                        setTimeout(() => { wlContainer.scrollTop = parseInt(savedScroll, 10); }, 60);
                    }
                } catch(e) {}
            }

            // 5. 确定初始标的并清理 URL 参数
            let initTicker = "NVDA";
            try {
                const urlParams = new URLSearchParams(window.location.search);
                const urlSym = (urlParams.get("symbol") || urlParams.get("ticker") || "").trim().toUpperCase();
                const savedT = localStorage.getItem("lastSelectedTicker");
                if (urlSym && (STOCKS_DATA.some(s => s.ticker === urlSym) || MACRO_DATA[urlSym])) {
                    initTicker = urlSym;
                    localStorage.setItem("lastSelectedTicker", urlSym);
                    urlParams.delete("symbol");
                    urlParams.delete("ticker");
                    const newQuery = urlParams.toString();
                    window.history.replaceState(null, "", window.location.pathname + (newQuery ? "?" + newQuery : "") + window.location.hash);
                } else if (savedT && (STOCKS_DATA.some(s => s.ticker === savedT) || MACRO_DATA[savedT])) {
                    initTicker = savedT;
                }
            } catch(e) {}

            // 6. 还原 K 线周期
            try {
                const savedPeriod = localStorage.getItem("futu_kline_period");
                const savedStyle = localStorage.getItem("futu_kline_style");
                if (savedPeriod) {
                    currentInterval = savedPeriod;
                    currentStyle = savedStyle || "1";
                    document.querySelectorAll(".period-tab").forEach(t => t.classList.toggle("active", t.getAttribute("data-period") === currentInterval));
                    document.querySelectorAll(".m-period-tab").forEach(t => t.classList.toggle("active", t.getAttribute("data-period") === currentInterval));
                    const minItem = document.querySelector(`.dropdown-item[data-period="${currentInterval}"]`);
                    if (minItem) {
                        document.querySelectorAll(".dropdown-item").forEach(d => d.classList.remove("active"));
                        minItem.classList.add("active");
                        const mb = document.getElementById("minDropdownBtn");
                        if (mb) mb.classList.add("active");
                        const ml = document.getElementById("currentMinLabel");
                        if (ml) ml.innerText = minItem.getAttribute("data-label");
                    }
                }
            } catch(e) {}

            // 7. 选中初始标的 + 刷新顶部红绿灯/成绩单
            updateTopRegimeAndScorecard();
            selectTicker(initTicker, false);

            // 8. 手机端视图还原
            if (window.innerWidth <= 768) {
                let savedMobileTab = "watchlist";
                try { savedMobileTab = localStorage.getItem("futu_mobile_tab") || "watchlist"; } catch(e) {}
                switchMobileTab(savedMobileTab);
            }

            // 9. 防御因子起飞警报
            const dfBanner = document.getElementById("deleveragingAlertBanner");
            const isDfAlert = (typeof WEATHER_INFO !== "undefined" && WEATHER_INFO && WEATHER_INFO.defensive_flight && WEATHER_INFO.defensive_flight.is_alert) || !!dfBanner;
            if (isDfAlert) {
                setTimeout(() => {
                    queueAlertSound("defensive_flight");
                    showToast("🚨 【全网警报】防御因子起飞！机构盘中去杠杆踩踏风险高企！");
                }, 700);
            }

            renderNotifList("all");
            updateSoundUI();
            if (window.location.hash === "#open_flash") openMacroFlashDrawer();
            window.addEventListener("hashchange", () => {
                if (window.location.hash === "#open_flash") openMacroFlashDrawer();
            });
            setInterval(pollLatestNotifications, 20000);
        });
    </script>
</body>
</html>
"""
    replacements = {
        "__PY_TPL_TOTAL_COUNT__": str(len(stocks_data) + len(macro_data)),
        "__PY_TPL_STOCKS_COUNT__": str(len(stocks_data)),
        "__PY_TPL_MACRO_COUNT__": str(len(macro_data)),
        "__PY_TPL_SESSION_BADGE__": str(session_badge_html),
        "__PY_TPL_DEF_BADGE__": str(defensive_alert_badge_html),
        "__PY_TPL_BREADTH_BADGE__": str(breadth_alert_badge_html),
        "__PY_TPL_DEF_BANNER__": str(defensive_alert_banner_html),
        "__PY_TPL_BREADTH_BANNER__": str(breadth_alert_banner_html),
        "__PY_TPL_MACRO_BANNER__": str(macro_calendar_banner_html),
        "__PY_TPL_WEATHER_NAME__": str((weather_info or {}).get("weather", "阴天防守")),
        "__PY_TPL_WEATHER_TACTICS__": str((weather_info or {}).get("tactics", "严格控制总仓位，分批防守。")),
        "__PY_TPL_BREADTH_COLOR__": str((breadth_info or {}).get("color", "#9ca3af")),
        "__PY_TPL_BREADTH_TAG__": str((breadth_info or {}).get("status_tag", "🛡️ 跨行业广度均衡")),
        "__PY_TPL_PARKING_COLOR__": str((weather_info or {}).get("qqq_parking_color", "#ffd54f")),
        "__PY_TPL_PARKING_ACTION__": str((weather_info or {}).get("qqq_parking_action", "🟡 观望防守")),
        "__PY_TPL_PARKING_DESC__": str((weather_info or {}).get("qqq_parking_desc", "大盘处于震荡期，维持轻仓现金防守")),
        "__PY_TPL_UPDATE_TIME__": str(update_time_str),
        "__PY_TPL_STOCKS_JSON__": stocks_json,
        "__PY_TPL_MACRO_JSON__": macro_json,
        "__PY_TPL_WEATHER_JSON__": weather_json,
        "__PY_TPL_WATCHLIST_JSON__": watchlist_json,
        "__PY_TPL_PORTFOLIO_TS__": str(int(time.time() * 1000)),
        "__PY_TPL_NOTIFICATIONS_JSON__": notifications_json,
        "__PY_TPL_AI_PREDICTIONS_JSON__": ai_predictions_json,
        "__PY_TPL_MARKET_NEWS_JSON__": market_news_json,
        "__PY_TPL_INTRADAY_PATH_JSON__": intraday_path_json,
        "__PY_TPL_SESSION_CODE__": str(session_code or "REGULAR"),
        "__PY_TPL_LEADERS_JSON__": leaders_json,
    }
    for _tok, _val in replacements.items():
        html = html.replace(_tok, _val)
    return html

if __name__ == "__main__":
    print("Futu Pro Terminal Generator Ready.")
