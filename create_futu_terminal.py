# -*- coding: utf-8 -*-
"""
Helper module to build the Futu-Style Pro Terminal (futu_pro_terminal.html)
支持 55 只全量标的池 (12 ETF + 43 个股)，全周期切换与分钟下拉菜单
"""
import json
import time

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
        df_alert = ml_predictor_5y.compute_defensive_flight_alert(live_lookup=live_lookup_init)
        if df_alert.get('is_alert'):
            defensive_alert_badge_html = f'<span class="tool-link" style="color: #f85149; border: 1px solid rgba(248,81,73,0.6); background: rgba(248,81,73,0.22); padding: 4px 8px; font-weight: 800; font-size: 11.5px;" title="机构避险因子异常走强，警惕盘中去杠杆踩踏">🚨 防御因子起飞 · 警惕机构盘中去杠杆踩踏</span>'
            defensive_alert_banner_html = f'<div id="deleveragingAlertBanner" onclick="playAlertSound(\'defensive_flight\', true)" title="点击试听/重播防御因子起飞警报音" style="background: linear-gradient(90deg, rgba(239, 68, 68, 0.28), rgba(185, 28, 28, 0.18)); border-bottom: 1px solid rgba(239, 68, 68, 0.6); color: #fca5a5; padding: 7px 14px; font-weight: 800; font-size: 13px; display: flex; align-items: center; justify-content: center; gap: 10px; box-shadow: 0 2px 8px rgba(239, 68, 68, 0.2); cursor: pointer; user-select: none;"><span>🚨 防御因子起飞 · 警惕机构盘中去杠杆踩踏</span><span style="font-size: 11px; font-weight: 600; color: #f87171; background: rgba(0,0,0,0.3); padding: 2px 8px; border-radius: 4px;">超额背离 +{df_alert.get("flight_score", 0.0):.1f}%</span><span style="font-size: 10.5px; color: #fca5a5; opacity: 0.85; margin-left: 6px;">🔊 警报响铃 (点击重播)</span></div>'
        if weather_info and isinstance(weather_info, dict):
            weather_info['defensive_flight'] = df_alert
    except Exception:
        pass

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
            pure_etf_set = {"QQQ", "SPY", "DIA", "IWM", "SMH", "SOXX", "SOXL", "KORU", "IGV", "TLT", "GLD", "QQQI", "VIXY", "SQQQ", "SOXS", "IBIT"}
            offensive_lev_etfs = {"SOXL", "KORU"}
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
                    try:
                        import ml_predictor_5y
                        prior_low_val = float(item.get('low_prev') or lv.get('prev_low') or 0.0) or None
                        vwap_val = float(lv.get('vwap', 0.0)) if (lv.get('vwap') and float(lv.get('vwap', 0.0)) > 0) else None
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

                    # 计算多级支撑压力与推演剧本 (与 is_effective_halt 严格对齐，彻底杜绝熔断与剧本冲突)
                    try:
                        import ml_predictor_5y
                        pw_val = float(item.get('put_wall', 0.0)) if item.get('put_wall') else None
                        cw_val = float(item.get('call_wall', 0.0)) if item.get('call_wall') else None
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
                                item['stop_loss_price'] = sc_map['s2']
                                item['stop_loss_pct'] = round(((live_p - sc_map['s2']) / live_p) * 100.0, 1)
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
                    for _tk_c, _tc_item in _tc_proj.items():
                        _b_item = ai_predictions["by_ticker"].get(_tk_c)
                        if isinstance(_tc_item, dict) and isinstance(_b_item, dict):
                            _new_cp = float(_b_item.get("latest_price") or _b_item.get("current_price") or 0.0)
                            if _new_cp > 0:
                                # 盘中/盘后报价只写入现价。已收盘历史 K 线与 15 日推演路径保持收盘价，禁止按比例改写。
                                _tc_item["current_price"] = round(_new_cp, 2)
                            # 1. 同步常规防御梯队点位 (s1, s2, sl, r1, r2) — 统一纯数学驱动，零个股硬编码
                            for _k_lvl in ("s1", "s2", "sl", "r1", "r2"):
                                if _b_item.get(_k_lvl) is not None:
                                    _tc_item[_k_lvl] = _b_item[_k_lvl]

                            # 2. R2 极限阻力联动校准 (Issue 3 / R3):
                            # 当 15 日推演路径最高价 max_proj_p 突破静态 R2 时，动态拔高 R2 至 max(r2, round(max_proj_p * 1.015, 2))
                            proj_list = _tc_item.get("projection", [])
                            proj_prices = [float(p["price"]) for p in proj_list if isinstance(p, dict) and p.get("price") is not None]
                            if proj_prices:
                                max_proj_p = max(proj_prices)
                                _tc_item["r2"] = max(float(_tc_item.get("r2") or 0.0), round(max_proj_p * 1.015, 2))
                                if _tc_item.get("r1") is not None and float(_tc_item["r2"]) <= float(_tc_item["r1"]):
                                    _min_step = max(0.01, round(float(_tc_item.get("current_price") or 100.0) * 0.005, 2))
                                    _tc_item["r2"] = round(float(_tc_item["r1"]) + _min_step, 2)
                                _b_item["r2"] = _tc_item["r2"]

                            # 3. 动态重构变盘时间窗 (Re-invoke _build_cycle_windows_from_projections)
                            try:
                                import ml_predictor_5y
                                _tc_item["cycle_windows"] = ml_predictor_5y._build_cycle_windows_from_projections(
                                    projections=_tc_item.get("projection", []),
                                    current_price=_tc_item.get("current_price"),
                                    r1=_tc_item.get("r1"),
                                    r2=_tc_item.get("r2")
                                )
                            except Exception:
                                pass

                            # 4. 双重防线兜底校验: 严禁任何高于 R1/R2 的波谷标为周期底回踩
                            _r1 = float(_tc_item.get("r1") or 0.0)
                            _r2 = float(_tc_item.get("r2") or 0.0)
                            for _cw in _tc_item.get("cycle_windows", []):
                                if isinstance(_cw, dict) and _cw.get("price"):
                                    _cw_p = float(_cw["price"])
                                    if _cw.get("type") in ("bottom", "caution"):
                                        if (_r1 > 0 and _cw_p >= _r1) or (_r2 > 0 and _cw_p >= _r2):
                                            _cw["type"] = "caution"
                                            _cw["desc"] = "⚠️ 高位强势休整窗 (已超R1/R2阻力·勿追高)"
                                            _cw["tag"] = "⚠️ 高位强势休整窗（已超R1/R2阻力·勿追高）"
                                        else:
                                            _cw["type"] = "bottom"
                                            _cw["desc"] = "🔵 周期底回踩窗 (关注空头回补企稳)"
                                            _cw["tag"] = "周期底回踩窗 (企稳关注)"
                _def_flight_obj = (weather_info.get("defensive_flight") if isinstance(weather_info, dict) else None) or ai_predictions.get("defensive_flight", {})
                ai_predictions["defensive_flight"] = _def_flight_obj

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
                        "defensive_flight": _def_flight_obj,
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
                        subprocess.run(["git", "add", "predict_radar_5y.html", "data_cache/latest_ai_predictions.json"], cwd=_sync_dir, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception as _re:
                    print(f"[!] 同步重绘 predict_radar_5y.html 提示: {_re}")
    except Exception:
        ai_predictions = {}

    # 将标的数据转为 JSON 供前端动态渲染
    stocks_json = json.dumps(stocks_data, ensure_ascii=False)
    macro_json = json.dumps(macro_data, ensure_ascii=False)
    weather_json = json.dumps(weather_info, ensure_ascii=False)
    watchlist_json = json.dumps(target_watchlist, ensure_ascii=False)
    notifications_json = json.dumps(terminal_notifications, ensure_ascii=False)
    ai_predictions_json = json.dumps(ai_predictions, ensure_ascii=False)

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
                // 清理跳转残留，防止强制复位
                searchParams.delete('symbol');
                searchParams.delete('ticker');
                searchParams.set('_t', Date.now());

                // 刷新前即时保存左侧滚动条与当前各状态
                var wl = document.getElementById("watchlistContainer");
                if (wl) {{
                    localStorage.setItem("futu_watchlist_scroll", wl.scrollTop);
                }}
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
    <title>富途牛牛风格 · 专业全周期量化看盘终端 (Pro Terminal · 70标的全量版)</title>
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
            gap: 6px;
            flex-shrink: 0;
        }}
        .tool-link {{
            color: #9ca3af;
            text-decoration: none;
            font-size: 12px;
            padding: 4px 8px;
            border-radius: 4px;
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 4px;
            white-space: nowrap;
            flex-shrink: 0;
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
            <!-- 市场时段状态标签 (动态四元划分) -->
            {session_badge_html}
            {defensive_alert_badge_html}
            <!-- 外部看板精简按钮 -->
            <a href="predict_radar_5y.html" target="_blank" class="tool-link" style="background: linear-gradient(135deg, rgba(188, 140, 255, 0.22), rgba(88, 166, 255, 0.18)); border: 1px solid rgba(188, 140, 255, 0.55); color: #e2c5ff; font-weight: 800; padding: 4px 8px; box-shadow: 0 0 8px rgba(188, 140, 255, 0.2);" title="打开 AI 美股 5 年多赛道波段预测全景战报">⚡ AI预测</a>
            <a href="stocks_radar.html" target="_blank" class="tool-link" style="padding: 4px 7px;">🎯 个股雷达</a>
            <a href="macro_radar.html" target="_blank" class="tool-link" style="padding: 4px 7px;">🏛️ 司令塔</a>
            <a href="backtest_report_5y.html" target="_blank" class="tool-link" style="padding: 4px 7px;">🛡️ 5Y回测</a>
        </div>
    </header>
    {defensive_alert_banner_html}

    <!-- 主体区域 -->
    <main class="futu-main m-view-watchlist" id="futuMain" style="position: relative;">
        <!-- 左侧折叠切换浮动按钮 -->
        <button id="btnToggleSidebar" onclick="toggleSidebar()" title="折叠/展开左侧标的列表" class="sidebar-toggle-btn">◀</button>

        <!-- 左侧：自选股列表 -->
        <aside class="sidebar-left" id="sidebarLeft">
            <div class="watchlist-categories">
                <div class="w-tab active" data-filter="all">全部 ({len(stocks_data) + len(macro_data)})</div>
                <div class="w-tab" data-filter="ai_top" style="color: #d8b4fe; font-weight: 800; background: rgba(188, 140, 255, 0.14); border: 1px solid rgba(188, 140, 255, 0.35);">⚡ AI高胜率 ({len(stocks_data) + len(macro_data)})</div>
                <div class="w-tab" data-filter="ai_short" style="color: #ff5252; font-weight: 800; background: rgba(248, 81, 73, 0.14); border: 1px solid rgba(248, 81, 73, 0.35);">🩸 AI做空榜 (Top 5)</div>
                <div class="w-tab" data-filter="starred" style="color: #ffd700; font-weight: 700; background: rgba(255, 215, 0, 0.08);">⭐ 建仓盯盘 (<span id="starredCount">0</span>)</div>
                <div class="w-tab" data-filter="pit" style="color: #00e676; font-weight: 700; background: rgba(0, 230, 118, 0.08);">🟢 黄金坑</div>
                <div class="w-tab" data-filter="take_profit" style="color: #d946ef; font-weight: 700; background: rgba(217, 70, 239, 0.08);">🟣 止盈减仓</div>
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
                <div class="w-tab" data-filter="物理与安全">物理AI与安全</div>
                <div class="w-tab" data-filter="数字资产" style="color: #f59e0b; font-weight: 700;">🪙 数字资产 (BTC)</div>
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
                    <button onclick="toggleNotifDrawer()" class="btn-secondary" style="flex: 1; padding: 7px; font-size: 12px; display: flex; align-items: center; justify-content: center; gap: 4px; border-radius: 4px;">
                        <span>🔔 预警中心</span>
                    </button>
                </div>
            </div>
            <!-- 视图过滤切换栏 (解决拥挤，支持一键筛选卡片) -->
            <div class="panel-filter-bar">
                <div class="p-filter-pill active" data-filter="all" onclick="filterRightCards('all')">全景</div>
                <div class="p-filter-pill" data-filter="ai" onclick="filterRightCards('ai')" style="color: #d8b4fe; font-weight: 700;">🧠AI预测</div>
                <div class="p-filter-pill" data-filter="options" onclick="filterRightCards('options')">⚡期权</div>
                <div class="p-filter-pill" data-filter="entry" onclick="filterRightCards('entry')">🎯挂单</div>
                <div class="p-filter-pill" data-filter="tactical" onclick="filterRightCards('tactical')">📊战术</div>
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

            <!-- 2.1 🧠 AI 5年多赛道波段预测哨 (Qlib长表截面 + 赛道PF先验校准 + 分段软压缩ATR) -->
            <div class="panel-box" id="aiPredictionBox" data-cat="ai" style="background: linear-gradient(135deg, rgba(188, 140, 255, 0.08), #161922); border-left: 4px solid #bc8cff; border-radius: 6px; padding: 10px 12px; margin-bottom: 8px;">
                <div class="box-title" style="margin-bottom: 8px;">
                    <span style="color: #d8b4fe; font-weight: 800; display: flex; align-items: center; gap: 6px;">
                        <span>🧠 AI 5年多赛道波段预测哨</span>
                    </span>
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span id="pAiRankBadge" style="font-size: 11px; padding: 2px 7px; border-radius: 4px; background: rgba(188,140,255,0.18); color: #e2c5ff; font-weight: 700;">多 #-- · 空 #--</span>
                        <a id="pAiTvLink" href="https://www.tradingview.com" target="_blank" style="font-size: 11px; padding: 2px 7px; border-radius: 4px; background: rgba(41,98,255,0.18); border: 1px solid rgba(41,98,255,0.45); color: #2962ff; text-decoration: none; font-weight: 700;" title="在 TradingView 中打开独立图表">📈 TV ↗</a>
                        <a href="predict_radar_5y.html" target="_blank" style="font-size: 11px; padding: 2px 7px; border-radius: 4px; background: rgba(96,165,250,0.18); border: 1px solid rgba(96,165,250,0.45); color: #60a5fa; text-decoration: none; font-weight: 700;" title="打开 AI 5年多赛道预测全景战报">⚡ 5年战报 ↗</a>
                        <span class="card-toggle-btn" onclick="toggleCard(this)">▲</span>
                    </div>
                </div>
                <div class="panel-box-content" id="pAiCardBody">
                    <!-- 1. Dominant Decision Banner -->
                    <div id="pAiActionBanner" style="background: rgba(188, 140, 255, 0.12); border: 1px solid rgba(188, 140, 255, 0.35); border-radius: 5px; padding: 6px 9px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;">
                        <span id="pAiActionText" style="font-size: 12px; font-weight: 800; color: #e2c5ff;">--</span>
                        <span id="pAiSectorStar" style="font-size: 11px; color: #ffd54f; font-weight: 700;">--</span>
                    </div>

                    <!-- 2. Dual Comparison Tug-of-War Probability Module -->
                    <div style="background: rgba(0,0,0,0.35); border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; padding: 8px 10px; margin-bottom: 8px;">
                        <div style="display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 4px;">
                            <div>
                                <span style="font-size: 11px; color: #9ca3af;">🔥 做多校准胜率</span>
                                <span id="pAiLongWr" style="font-size: 16px; font-weight: 800; color: #3fb950; font-family: 'DIN Alternate', monospace; margin-left: 4px;">--%</span>
                            </div>
                            <div style="text-align: right;">
                                <span style="font-size: 11px; color: #9ca3af;">🩸 做空校准胜率</span>
                                <span id="pAiShortWr" style="font-size: 16px; font-weight: 800; color: #f85149; font-family: 'DIN Alternate', monospace; margin-left: 4px;">--%</span>
                            </div>
                        </div>

                        <!-- Bidirectional Energy Bar -->
                        <div style="height: 10px; width: 100%; background: rgba(255,255,255,0.06); border-radius: 5px; overflow: hidden; display: flex; position: relative;">
                            <div id="pAiLongBar" style="height: 100%; width: 50%; background: linear-gradient(90deg, #238636, #3fb950); transition: width 0.3s ease;"></div>
                            <div style="width: 2px; height: 100%; background: #ffffff; opacity: 0.5; z-index: 2;"></div>
                            <div id="pAiShortBar" style="height: 100%; width: 50%; background: linear-gradient(90deg, #f85149, #da3633); transition: width 0.3s ease;"></div>
                        </div>

                        <!-- Raw Prob & PCR Sub-text -->
                        <div style="display: flex; justify-content: space-between; margin-top: 4px; font-size: 10px; color: #8b949e;">
                            <span id="pAiLongRaw">原始多: --%</span>
                            <span id="pAiPcrSentiment">期权 PCR: --</span>
                            <span id="pAiShortRaw">原始空: --%</span>
                        </div>
                    </div>

                    <!-- 3. Dual-Track 4-Pill Dynamic Price Targets -->
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 11px;">
                        <div style="background: rgba(63,185,80,0.08); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(63,185,80,0.25);">
                            <div style="color: #9ca3af; font-size: 10px;">🎯 多头目标止盈</div>
                            <b style="color: #3fb950;" id="pAiTargetPrice">--</b>
                        </div>
                        <div style="background: rgba(248,81,73,0.08); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(248,81,73,0.25);">
                            <div style="color: #9ca3af; font-size: 10px;">🛡️ 多头防守止损</div>
                            <b style="color: #f85149;" id="pAiStopPrice">--</b>
                        </div>
                        <div style="background: rgba(248,81,73,0.08); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(248,81,73,0.25);">
                            <div style="color: #9ca3af; font-size: 10px;">🩸 空头目标下杀</div>
                            <b style="color: #f85149;" id="pAiShortTargetPrice">--</b>
                        </div>
                        <div style="background: rgba(63,185,80,0.08); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(63,185,80,0.25);">
                            <div style="color: #9ca3af; font-size: 10px;">🛡️ 空头反抽防守</div>
                            <b style="color: #3fb950;" id="pAiShortStopPrice">--</b>
                        </div>
                    </div>

                    <!-- 4. V2.0 Multi-Level Scenario Points & 4H Resonance -->
                    <div id="pAiV2ScenarioBox" style="margin-top: 7px; background: rgba(13, 17, 23, 0.78); border: 1px solid rgba(88, 166, 255, 0.25); border-radius: 5px; padding: 6px 8px; font-size: 11px;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                            <span style="color: #9ca3af; font-size: 10px;">🧭 V2.0 筹码点位 & 4H 共振</span>
                            <span id="pAiFourHDiv" style="font-size: 10px; font-weight: 700; color: #a371f7;">--</span>
                        </div>
                        <div id="pAiMultiLevels" style="font-family: 'DIN Alternate', monospace; font-size: 11px; color: #e5e7eb; margin-bottom: 4px; display: flex; justify-content: space-between; gap: 6px;">
                            <span style="color: #3fb950;">支撑 S1/S2: --</span>
                            <span style="color: #f85149;">阻力 R1/R2: --</span>
                        </div>
                        <div id="pAiScenarioScript" style="font-size: 10.5px; color: #cbd5e1; line-height: 1.35; border-left: 2px solid #58a6ff; padding-left: 6px;">--</div>
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
                        <div style="background: rgba(0,0,0,0.3); border-radius: 6px; padding: 6px 8px; border: 1px solid rgba(255,215,0,0.25);">
                            <div id="pEntryPriceLabel" style="font-size: 11px; color: #9ca3af;">💡 参考挂单价</div>
                            <div id="pOptimalLimit" style="font-size: 22px; font-weight: 800; color: #ffd700; font-family: \'DIN Alternate\', sans-serif;">$0.00</div>
                            <div style="font-size: 10px; color: #6b7280;" id="pOptimalNote">黄金坑中下轴支撑</div>
                        </div>
                        <div style="background: rgba(0,0,0,0.3); border-radius: 6px; padding: 6px 8px; border: 1px solid rgba(255,82,82,0.35);">
                            <div id="pEntryStopLabel" style="font-size: 11px; color: #9ca3af;">🛡️ 防守止损线</div>
                            <div id="pEntryStopLoss" style="font-size: 22px; font-weight: 800; color: #ff5252; font-family: \'DIN Alternate\', sans-serif;">$0.00</div>
                            <div style="font-size: 10px; color: #6b7280;" id="pEntryStopDist">距入场 --</div>
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
                    <div style="display: grid; grid-template-columns: 1fr; gap: 6px; font-size: 11px;">
                        <div style="background: rgba(0,0,0,0.25); border-radius: 4px; padding: 5px 7px; border: 1px solid rgba(255,255,255,0.05); display: flex; justify-content: space-between; align-items: center;">
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
                    <div style="font-size: 11px; margin-top: 6px; padding-top: 6px; border-top: 1px dashed rgba(255,255,255,0.1); line-height: 1.3;">
                        <span style="color: #60a5fa; font-weight: 700;">🏦 闲置资金泊入指示：</span>
                        <span style="color: {weather_info.get('qqq_parking_color', '#ffd54f')}; font-weight: 800;">{weather_info.get('qqq_parking_action', '🟡 观望防守')}</span>
                        <div style="color: #888; font-size: 10px; margin-top: 2px;">{weather_info.get('qqq_parking_desc', '大盘处于震荡期，维持轻仓现金防守')}</div>
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
                    <button onclick="playAlertSound('defensive_flight', true)" class="sound-test-chip" style="color: #f87171; border-color: rgba(248,113,113,0.4);" title="试听 防御因子起飞警报 (高急促空袭警报音)">🚨防御起飞</button>
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
                <button class="n-tab" data-cat="defensive_flight" onclick="filterNotifCategory('defensive_flight')" style="color: #f87171;">🚨 防御起飞 (<span id="catCount_defensive_flight">0</span>)</button>
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
        const INITIAL_PORTFOLIO = {{}};
        const INITIAL_PORTFOLIO_TS = {int(time.time() * 1000)};
        const INITIAL_NOTIFICATIONS = {notifications_json};
        const AI_PREDICTIONS = {ai_predictions_json};

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

        // 优先检查当前浏览器本地持久化缓存 (跨电脑刷新不丢失)
        // 优先检查当前浏览器本地持久化缓存 (远端修改时生效，但量化主机更新时以主机最新实盘为准)
        try {{
            const savedCustom = localStorage.getItem("user_custom_portfolio");
            const savedTs = parseInt(localStorage.getItem("user_custom_portfolio_ts") || "0");
            if (savedCustom && savedTs > INITIAL_PORTFOLIO_TS) {{
                const parsed = JSON.parse(savedCustom);
                if (parsed && typeof parsed === "object" && Object.keys(parsed).length > 0) {{
                    userPortfolio = parsed;
                    syncPortfolioToRuntimeData(userPortfolio);
                    renderWatchlist(currentFilter);
                    selectTicker(currentTicker);
                }}
            }}
        }} catch(e) {{}}

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

        // TradingView 交易所标的代码映射 (全量 71 只)
        const SYMBOL_MAP = {{
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
            "QQQI": "NASDAQ:QQQI",
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
            "SPCX": "NASDAQ:SPCX",
            "ASTS": "NASDAQ:ASTS",
            "RKLB": "NASDAQ:RKLB",
            "FLY": "NASDAQ:FLY",
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
            "COIN": "NASDAQ:COIN",
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
                const aiByTicker = (AI_PREDICTIONS && AI_PREDICTIONS.by_ticker) ? AI_PREDICTIONS.by_ticker : {{}};
                if (filter === "all" || filter === "stocks") match = true;
                else if ((filter === "ai_top" || filter === "ai_short") && Boolean(aiByTicker[s.ticker])) match = true;
                else if (filter === "starred" && targetWatchlist.includes(s.ticker)) match = true;
                else if (filter === "pit" && !s.is_crash_halt && !s.is_cooling_halt && !s.is_inverse_hedge && (s.is_pit || (s.pit_status && (s.pit_status.includes("坑内") || s.pit_status.includes("黄金坑"))) || (s.signal && s.signal.includes("黄金坑")) || (s.dist_to_pit !== undefined && s.dist_to_pit <= 0.0 && s.dist_to_pit >= -8.0))) match = true;
                else if (filter === "take_profit" && (s.is_take_profit || (s.signal && (s.signal.includes("止盈") || s.signal.includes("冲顶"))))) match = true;
                else if (filter === "stop" && (s.is_stop || s.is_deep_pullback || (s.signal && (s.signal.includes("破位") || s.signal.includes("跌破") || s.signal.includes("回踩"))))) match = true;
                else if (filter === "芯片与半导体" && (s.cat === "芯片算力" || s.cat === "晶圆代工" || s.cat === "制造设备" || s.cat === "通信芯片" || s.cat === "芯片半导体")) match = true;
                else if (filter === "存储闪存" && (s.cat === "存储芯片" || s.cat === "闪存存储" || s.cat === "数据存储")) match = true;
                else if (filter === "云与软件" && (s.cat === "云与软件" || s.cat === "生态平台" || s.cat === "社交AI" || s.cat === "终端硬件" || s.cat === "云与电商" || s.cat === "企业AI" || s.cat === "算力租赁")) match = true;
                else if (filter === "液冷服务器" && (s.cat === "液冷散热" || s.cat === "服务器ODM")) match = true;
                else if (filter === "光通信" && (s.cat === "光通信" || s.cat === "高速互联")) match = true;
                else if (filter === "能源电力" && (s.cat === "能源电力" || s.cat === "核能电力")) match = true;
                else if (filter === "太空商业" && s.cat === "太空商业") match = true;
                else if (filter === "物理与安全" && (s.cat === "物理AI" || s.cat === "AI安全" || s.cat === "价值蓝筹" || s.cat === "金融蓝筹" || s.cat === "专属风控")) match = true;
                else if (filter === "数字资产" && (s.cat === "数字资产" || t === "MSTR" || t === "IBIT")) match = true;
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
                else if ((filter === "ai_top" || filter === "ai_short") && Boolean(AI_PREDICTIONS && AI_PREDICTIONS.by_ticker && AI_PREDICTIONS.by_ticker[t])) match = true;
                else if (filter === "starred" && targetWatchlist.includes(t)) match = true;
                else if (filter === "pit" && !m.is_crash_halt && !m.is_cooling_halt && !m.is_inverse_hedge && !["SQQQ", "SOXS", "VIXY"].includes(t) && (m.is_pit || (m.signal && m.signal.includes("黄金坑")) || (m.pit_status && (m.pit_status.includes("坑内") || m.pit_status.includes("黄金坑"))) || (m.dist_to_pit !== undefined && m.dist_to_pit <= 0.0 && m.dist_to_pit >= -8.0))) match = true;
                else if (filter === "take_profit" && (m.is_take_profit || (m.signal && (m.signal.includes("止盈") || m.signal.includes("超买") || m.signal.includes("冲顶"))))) match = true;
                else if (filter === "stop" && (m.is_stop || m.is_deep_pullback || (m.signal && (m.signal.includes("破位") || m.signal.includes("跌破") || m.signal.includes("回踩"))))) match = true;
                else if (filter === "芯片与半导体" && (t === "SMH" || t === "SOXX" || t === "SOXL" || t === "SOXS" || t === "KORU")) match = true;
                else if (filter === "云与软件" && (t === "QQQ" || t === "IGV" || t === "SQQQ")) match = true;
                else if (filter === "物理与安全" && (t === "IBIT")) match = true;
                else if (filter === "数字资产" && (t === "IBIT" || m.cat === "数字资产")) match = true;

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

            if (filter === "ai_top" && AI_PREDICTIONS && AI_PREDICTIONS.by_ticker) {{
                items.sort((a, b) => {{
                    const ra = (AI_PREDICTIONS.by_ticker[a.ticker] && AI_PREDICTIONS.by_ticker[a.ticker].rank) || 999;
                    const rb = (AI_PREDICTIONS.by_ticker[b.ticker] && AI_PREDICTIONS.by_ticker[b.ticker].rank) || 999;
                    return ra - rb;
                }});
            }} else if (filter === "ai_short" && AI_PREDICTIONS && AI_PREDICTIONS.by_ticker) {{
                items.sort((a, b) => {{
                    const ra = (AI_PREDICTIONS.by_ticker[a.ticker] && AI_PREDICTIONS.by_ticker[a.ticker].short_rank) || 999;
                    const rb = (AI_PREDICTIONS.by_ticker[b.ticker] && AI_PREDICTIONS.by_ticker[b.ticker].short_rank) || 999;
                    return ra - rb;
                }});
            }}

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
                            
                            ${{isStarred ? '<span style="color:#ffd700; font-size:10px; margin-left:3px;" title="已加入建仓盯盘">⭐</span>' : ''}}
                            ${{isEntryReady && isStarred ? '<span style="background:rgba(0,230,118,0.2); color:#00e676; border:1px solid rgba(0,230,118,0.5); border-radius:3px; padding:0 3px; font-size:9px; margin-left:3px;">🎯买点</span>' : ''}}
                            ${{item.is_pit ? '<span style="color:#00e676; font-size:10px; margin-left:3px;">🟢坑</span>' : ''}}
                            ${{isAlphaLeader ? '<span style="color:#ffd700; font-size:9px; margin-left:2px;" title="20日相对大盘超额抗跌王">👑抗跌</span>' : ''}}
                            ${{isEarnRisk ? `<span style="color:#ffa726; font-size:9px; margin-left:2px;" title="${{item.earnings_tag}}">⚠️财报</span>` : ''}}
                            ${{isTakeProfit ? '<span style="color:#d946ef; font-size:10px; margin-left:3px;">🟣盈</span>' : ''}}
                            ${{isStop ? '<span style="color:#ff5252; font-size:10px; margin-left:3px;">🔴破</span>' : ''}}
                            ${{isPullback ? '<span style="color:#ff9100; font-size:10px; margin-left:3px;">🟠回</span>' : ''}}
                            ${{(AI_PREDICTIONS && AI_PREDICTIONS.by_ticker && AI_PREDICTIONS.by_ticker[item.ticker]) ? (filter === "ai_short" ? `<span style="background:rgba(248,81,73,0.18); color:#f85149; border:1px solid rgba(248,81,73,0.38); border-radius:3px; padding:0 3px; font-size:9px; margin-left:3px;" title="AI 5年做空胜率 (#${{AI_PREDICTIONS.by_ticker[item.ticker].short_rank || '--'}})">🩸空 ${{((AI_PREDICTIONS.by_ticker[item.ticker].calibrated_short_win_rate_pct || 50.0)).toFixed(0)}}%</span>` : `<span style="background:rgba(188,140,255,0.18); color:${{AI_PREDICTIONS.by_ticker[item.ticker].calibrated_win_rate_pct >= 58 ? '#4ade80' : '#d8b4fe'}}; border:1px solid rgba(188,140,255,0.38); border-radius:3px; padding:0 3px; font-size:9px; margin-left:3px;" title="AI 5年多赛道预测校准胜率 (#${{AI_PREDICTIONS.by_ticker[item.ticker].rank}})">⚡AI ${{AI_PREDICTIONS.by_ticker[item.ticker].calibrated_win_rate_pct.toFixed(0)}}%</span>`) : ''}}
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

                div.onclick = () => selectTicker(item.ticker, true);
                container.appendChild(div);
            }});
        }}

        // 切换当前选中股票
        function selectTicker(ticker, fromUserClick = false) {{
            currentTicker = ticker;
            try {{
                localStorage.setItem("lastSelectedTicker", ticker);
            }} catch(e) {{}}

            // 📱 手机端联动：仅当用户在行情列表视图下主动点击了标的时，才自动跳到 K线 视窗查看
            if (fromUserClick && window.innerWidth <= 768 && currentMobileTab === "watchlist") {{
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

            // 1. 基础指标与六维量化矩阵 (优先穿透盘前/盘后实时真实成交价)
            const curLast = data.last || 0;
            const curActive = (data.ext_price && data.ext_price > 0) ? data.ext_price : (data.active_price || curLast);
            const curMa50 = data.ma50 || 0;
            const curEma20 = data.ema20 || curMa50;
            const curBias = data.bias !== undefined ? data.bias : (curMa50 > 0 ? ((curActive - curMa50)/curMa50*100) : 0);
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
            const fractional = data.fractional_shares || 0;
            const stopLoss = (isCrashHalt || isCoolingHalt) ? 0 : (data.entry_stop_loss || (curFibLow > 0 ? curFibLow * 0.97 : optimalLimit * 0.96));
            const cancelLine = Math.max(data.low_price || data.rth_low || 0, curLast * 0.985);
            const curCat = data.cat || data.sub || "";

            document.getElementById("pSector").innerText = curCat || "科技龙头";
            document.getElementById("pMa50").innerText = curMa50 ? ("$" + curMa50.toFixed(2)) : "--";
            
            const biasEl = document.getElementById("pBias");
            if (data.bias !== undefined || curMa50 > 0) {{
                const hasExt = Boolean(data.ext_price && data.ext_price > 0 && data.session_tag);
                const extNote = hasExt ? ` <span style="font-size:10px; color:#9ca3af;">(含${{data.session_tag}})</span>` : "";
                biasEl.innerHTML = (curBias >= 0 ? "+" : "") + curBias.toFixed(2) + "%" + extNote;
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

            // 3. 核心状态研判与四维深度成因分析
            const sig = data.signal || "";
            const isPit = data.is_pit || sig.includes("黄金坑就位");
            const isPitWatch = sig.includes("坑内观察") || sig.includes("待芯片ETF");
            const isStop = data.is_stop || sig.includes("破位") || sig.includes("跌破") || sig.includes("硬止损");
            const isDeepPullback = data.is_deep_pullback || sig.includes("深度回踩");
            const isBrokenRebound = Boolean(data.broken_rebound_ready);
            const isBrokenBearish = Boolean(data.broken_bearish);
            const _aiNow = (AI_PREDICTIONS && AI_PREDICTIONS.by_ticker) ? (AI_PREDICTIONS.by_ticker[ticker] || {{}}) : {{}};
            const aiNowR1 = Number(_aiNow.r1 || 0);
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
            }} else if (curVolRatio < 0.8 && (data.pct_change || 0) > 0) {{
                volReason = `量比 ${{curVolRatio.toFixed(2)}}x (缩量反抽)，抛压阶段性减轻，关注上方 R1 阻力位能否补量突破`;
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
            }} else if (isPit && !isCrashHalt && !isCoolingHalt) {{
                themeColor = "#00e676";
                themeBg = "linear-gradient(135deg, rgba(0, 230, 118, 0.08), #181b22)";
                themeBorder = "#00e676";
                actionType = "V3.1 黄金坑右侧确认";
                mainVerdict = "🟢 V3.1 黄金坑就位 (9:30开盘执行)";
                actionDirective = `【V3.1 开盘执行标准】标的已触发右侧止跌企稳！9:30 开盘执行指令：<br>1️⃣ 9:30开盘买入上限: $${{optimalLimit.toFixed(2)}}<br>2️⃣ 隔夜低开作废底线: $${{cancelLine.toFixed(2)}}<br>3️⃣ GTC 铁血结构止损: $${{stopLoss.toFixed(2)}}`;
            }} else if (isCrashHalt || isCoolingHalt) {{
                themeColor = "#ff5252";
                themeBg = "linear-gradient(135deg, rgba(255, 82, 82, 0.10), #181b22)";
                themeBorder = "#ff5252";
                actionType = isCoolingHalt ? "暴跌冷却 / 严禁抢跑" : "暴跌熔断 / 暂停接刀";
                mainVerdict = isCoolingHalt ? "⛔ 暴跌冷却期 (3日内曾大跌·等待日线企稳确认)" : "⛔ 暂停挂单 (暴跌宣泄期 · 防接飞刀)";
                actionDirective = `【V3.2 熔断保护】${{data.intraday_reason || "标的触发日内暴跌熔断或处于3日暴跌冷却期内，日线尚未确认右侧企稳，严禁盲目左侧挂单抄底！"}}`;
            }} else if (isBrokenRebound) {{
                themeColor = "#ffa726";
                themeBg = "linear-gradient(135deg, rgba(255, 167, 38, 0.10), rgba(0, 229, 255, 0.05), #181b22)";
                themeBorder = "#ffa726";
                actionType = "超跌右侧博反抽 / 短线快进快出";
                mainVerdict = "⚡ 超跌右侧博反抽 (破坑深调·快进快出)";
                actionDirective = `【破坑右侧反抽】日线仍处黄金坑 ($${{curFibLow.toFixed(2)}}) 下方，但日内已站稳 VWAP/支撑企稳；仅限短线博空头回补，压力位 R1 ($${{Number(aiNowR1 || data.broken_r1 || curFibHigh || 0).toFixed(2)}}) 不过即走，严禁中线死扛。`;
            }} else if (isBrokenBearish) {{
                themeColor = "#ff1744";
                themeBg = "linear-gradient(135deg, rgba(255, 23, 68, 0.09), #181b22)";
                themeBorder = "#ff1744";
                actionType = "破坑观望 / 严禁左侧接刀";
                mainVerdict = data.intraday_status_text || "🔴 空头宣泄·严禁左侧接刀";
                actionDirective = data.intraday_reason || `【左侧禁接】股价跌穿黄金坑 ($${{curFibLow.toFixed(2)}}) 且日内压制于 VWAP ($${{(data.vwap || curLast).toFixed(2)}}) 下方，空头宣泄未止跌；请耐心等待日内重夺 VWAP 后再博右侧反抽，当前严禁左侧接刀。`;
            }} else if (isStop) {{
                themeColor = "#ff1744";
                mainVerdict = "🔴 放量破位杀跌 · 严禁逆势接飞刀";
                actionDirective = `【铁血风控指令】标的放量击穿关键斐波那契坑底防线 (跌破 ${{Math.abs(curDistPit).toFixed(1)}}%)，空头动能处于主导释放期。持仓者若跌破底线必须坚决离场止损；空仓观察者坚决管住手，切勿盲目猜底！`;
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
            }} else if (isTakeProfit) {{
                themeColor = "#d946ef";
                themeBg = "linear-gradient(135deg, rgba(217, 70, 239, 0.08), #181b22)";
                themeBorder = "#d946ef";
                actionType = "高位减仓 / 锁定利润";
                if (sig.includes("跌破EMA20")) {{
                    mainVerdict = "🟣 跌破EMA20 · 移动止盈落袋";
                    actionDirective = `【移动止盈指引】标的现价 ($${{curActive.toFixed(2)}}) 跌破 20日均线 EMA20 ($${{curEma20.toFixed(2)}})，主升浪推进节奏受阻。已持仓者建议执行移动止盈落袋为安，保护浮盈；未建仓者切勿在此盲目抄底接下坠飞刀。`;
                }} else {{
                    mainVerdict = "🟣 冲顶减仓 · 启动分批锁定胜果";
                    actionDirective = `【获利了结指引】技术面进入统计学极度超买高潮区 (RSI ${{curRsi.toFixed(1)}}，偏离 ${{curBias.toFixed(1)}}%)。持仓者建议分批卖出 1/3 ~ 1/2 锁定丰厚利润，剩余底仓以 EMA20 ($${{curEma20.toFixed(2)}}) 作为移动保护止损！`;
                }}
            }} else if (isBullTrend) {{
                themeColor = "#29b6f6";
                themeBg = "linear-gradient(135deg, rgba(41, 182, 246, 0.08), #181b22)";
                themeBorder = "#29b6f6";
                actionType = "顺势持有 / 移动保护";
                mainVerdict = "🔵 强势多头推进 · 顺势持有待涨";
                actionDirective = `【顺势而为让利润奔跑】主升浪趋势健康，均线呈良性多头排列。依托 EMA20 ($${{curEma20.toFixed(2)}}) 作为趋势移动生命线，只要收盘不破 EMA20 安心持股待涨，不盲目追高。`;
            }} else {{
                themeColor = "#94a3b8";
                themeBg = "linear-gradient(135deg, rgba(148, 163, 184, 0.08), #181b22)";
                themeBorder = "#64748b";
                actionType = "区间震荡 / 耐心蓄势";
                mainVerdict = "⚪ 震荡整理蓄势 · 多看少动中性仓位";
                actionDirective = `【箱体整理耐心中性】当前处于区间均线反复争夺阶段，未触发任何确定性波段信号。保持中性仓位，耐心等待股价回踩进入黄金坑或放量向上突破箱体。`;
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

            // 7. 日内精准建仓挂单指南测算与盯盘 UI 同步
            // 5分钟量化日内买点状态横幅渲染
            const intraBannerEl = document.getElementById("pIntradayBanner");
            const intraStatusEl = document.getElementById("pIntradayStatusText");
            const intraReasonEl = document.getElementById("pIntradayReason");
            const isEntryReady = Boolean(data.intraday_entry_ready);
            const intraStatusText = data.intraday_status_text || (isEntryReady ? "🎯 买点触发: 5m量化企稳" : "⏳ 蹲守挂单价");
            const intraReason = data.intraday_reason || "等待价格回踩至建议挂单价附近并出现 5m RSI 超卖拐头或 VWAP 突破信号。";

            if (intraBannerEl && intraStatusEl && intraReasonEl) {{
                if (isInverseHedge) {{
                    intraStatusEl.innerText = intraStatusText;
                    intraReasonEl.innerText = intraReason;
                    if (isTakeProfit || intraStatusText.includes("收割")) {{
                        intraBannerEl.style.background = "rgba(217, 70, 239, 0.14)";
                        intraBannerEl.style.borderColor = "rgba(217, 70, 239, 0.45)";
                        intraStatusEl.style.color = "#d946ef";
                    }} else {{
                        intraBannerEl.style.background = "rgba(56, 189, 248, 0.12)";
                        intraBannerEl.style.borderColor = "rgba(56, 189, 248, 0.35)";
                        intraStatusEl.style.color = "#38bdf8";
                    }}
                }} else if (isCoolingHalt) {{
                    intraBannerEl.style.background = "rgba(255, 82, 82, 0.14)";
                    intraBannerEl.style.borderColor = "#ff5252";
                    intraStatusEl.style.color = "#ff5252";
                    intraStatusEl.innerText = "⛔ 暴跌冷却期 (3日内曾大跌·等待日线企稳确认)";
                    intraReasonEl.innerText = data.intraday_reason || "标的3日内曾发生单日大跌，目前处于冷却观察期，需等待日线右侧企稳确认。";
                }} else if (isBrokenRebound) {{
                    intraStatusEl.innerText = intraStatusText;
                    const _r1Shown = Number(aiNowR1 || data.broken_r1 || 0);
                    let _branchAReason = intraReason || "";
                    if (_r1Shown > 0) _branchAReason = _branchAReason.replace(/压力位\(R1 \$[\d.]+\)/, `压力位(R1 $${{_r1Shown.toFixed(2)}})`);
                    intraReasonEl.innerText = _branchAReason;
                    intraBannerEl.style.background = "linear-gradient(90deg, rgba(255, 167, 38, 0.18), rgba(0, 229, 255, 0.10))";
                    intraBannerEl.style.borderColor = "#ffa726";
                    intraStatusEl.style.color = "#ffd54f";
                }} else if (isBrokenBearish) {{
                    intraStatusEl.innerText = intraStatusText;
                    intraReasonEl.innerText = intraReason;
                    intraBannerEl.style.background = "rgba(255, 82, 82, 0.14)";
                    intraBannerEl.style.borderColor = "#ff5252";
                    intraStatusEl.style.color = "#ff5252";
                }} else {{
                    intraStatusEl.innerText = intraStatusText;
                    intraReasonEl.innerText = intraReason;
                    if (isEntryReady) {{
                        intraBannerEl.style.background = "rgba(0, 230, 118, 0.16)";
                        intraBannerEl.style.borderColor = "#00e676";
                        intraStatusEl.style.color = "#00e676";
                    }} else if (intraStatusText.includes("严禁") || intraStatusText.includes("超买") || intraStatusText.includes("跌破") || intraStatusText.includes("冷却期")) {{
                        intraBannerEl.style.background = "rgba(255, 82, 82, 0.14)";
                        intraBannerEl.style.borderColor = "rgba(255, 82, 82, 0.45)";
                        intraStatusEl.style.color = "#ff5252";
                    }} else {{
                        intraBannerEl.style.background = "rgba(255, 215, 0, 0.10)";
                        intraBannerEl.style.borderColor = "rgba(255, 215, 0, 0.35)";
                        intraStatusEl.style.color = "#ffd700";
                    }}
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
            const noteEl = document.getElementById("pOptimalNote");
            const stopEl = document.getElementById("pEntryStopLoss");
            const stopDistEl = document.getElementById("pEntryStopDist");
            const priceLabelEl = document.getElementById("pEntryPriceLabel");
            const stopLabelEl = document.getElementById("pEntryStopLabel");
            if (priceLabelEl) priceLabelEl.innerText = "💡 参考挂单价";
            if (stopLabelEl) stopLabelEl.innerText = "🛡️ 防守止损线";

            if (isBrokenRebound) {{
                if (priceLabelEl) priceLabelEl.innerText = "💡 反抽参考价";
                if (stopLabelEl) stopLabelEl.innerText = "🛡️ 防守止损线";
                if (optEl) {{
                    optEl.innerText = (optimalLimit && optimalLimit > 0) ? ("$" + optimalLimit.toFixed(2)) : "--";
                    optEl.style.color = "#ffb74d";
                    optEl.style.fontSize = "22px";
                }}
                if (noteEl) {{
                    const _chaseNote = (curActive > (data.vwap || curActive) * 1.03) || Number(data.rsi_5m || 0) >= 68;
                    noteEl.innerText = "回踩VWAP/日内支撑短线单" + (_chaseNote ? " · 日内已冲高偏离VWAP，切勿追高现价，耐心等回踩低吸" : "");
                    noteEl.style.color = _chaseNote ? "#ffd54f" : "#ffb74d";
                }}
                if (stopEl) {{
                    stopEl.innerText = stopLoss > 0 ? ("$" + stopLoss.toFixed(2)) : "--";
                    stopEl.style.fontSize = "22px";
                }}
                if (stopDistEl) {{
                    const stopDist = (optimalLimit > 0 && stopLoss > 0) ? ((stopLoss - optimalLimit) / optimalLimit * 100) : null;
                    stopDistEl.innerText = stopDist !== null ? (`距入场 ${{stopDist.toFixed(1)}}% · 短线收紧`) : "距入场 --";
                    stopDistEl.style.color = "#f87171";
                }}
            }} else if (isBrokenBearish) {{
                if (priceLabelEl) priceLabelEl.innerText = "🚫 暂缓左侧接刀";
                if (stopLabelEl) stopLabelEl.innerText = "🧭 下方支撑观察 S1";
                if (optEl) {{
                    optEl.innerText = "暂缓";
                    optEl.style.color = "#ff5252";
                    optEl.style.fontSize = "18px";
                }}
                if (noteEl) {{
                    const _vwap = Number(data.vwap || curActive || 0);
                    noteEl.innerText = _vwap > 0 ? (`等站上VWAP($${{_vwap.toFixed(2)}})右侧确认`) : "等站上VWAP右侧确认";
                    noteEl.style.color = "#ff5252";
                }}
                const brokenS1 = Number(data.broken_s1 || 0);
                if (stopEl) {{
                    stopEl.innerText = brokenS1 > 0 ? ("$" + brokenS1.toFixed(2)) : "--";
                    stopEl.style.fontSize = "20px";
                }}
                if (stopDistEl) {{
                    stopDistEl.innerText = "破坑后极限支撑";
                    stopDistEl.style.color = "#ffd700";
                }}
            }} else if (isInverseHedge) {{
                if (optEl) {{
                    optEl.innerText = isTakeProfit ? "🟣 逢高分批收割" : "🛡️ 仅限短线对冲";
                    optEl.style.color = isTakeProfit ? "#d946ef" : "#38bdf8";
                    optEl.style.fontSize = "20px";
                }}
                if (noteEl) {{
                    noteEl.innerText = data.limit_note || "反向衰减对冲工具·不设黄金坑埋伏单";
                    noteEl.style.color = isTakeProfit ? "#d946ef" : "#38bdf8";
                }}
                if (stopEl) {{
                    stopEl.innerText = stopLoss > 0 ? ("$" + stopLoss.toFixed(2)) : "跌破 EMA5 离场";
                    stopEl.style.fontSize = stopLoss > 0 ? "20px" : "14px";
                }}
                if (stopDistEl) {{
                    stopDistEl.innerText = "EMA5 防线";
                    stopDistEl.style.color = "#f87171";
                }}
            }} else if (isCrashHalt) {{
                if (optEl) {{
                    optEl.innerText = "⛔ 暂停挂单 (暴跌宣泄期)";
                    optEl.style.color = "#ff5252";
                    optEl.style.fontSize = "15px";
                }}
                if (noteEl) {{
                    noteEl.innerText = data.limit_note || "单日/盘后剧烈跳空破位，严禁首日左侧挂单接飞刀";
                    noteEl.style.color = "#ff5252";
                }}
                if (stopEl) {{
                    stopEl.innerText = "⛔ 观望不设单";
                    stopEl.style.fontSize = "14px";
                }}
                if (stopDistEl) {{
                    stopDistEl.innerText = "暂停挂单";
                    stopDistEl.style.color = "#6b7280";
                }}
            }} else if (isCoolingHalt) {{
                if (optEl) {{
                    optEl.innerText = "⛔ 暂停挂单 (暴跌冷却期)";
                    optEl.style.color = "#ff5252";
                    optEl.style.fontSize = "15px";
                }}
                if (noteEl) {{
                    noteEl.innerText = data.limit_note || "3日内曾大跌·等待日线企稳确认";
                    noteEl.style.color = "#ff5252";
                }}
                if (stopEl) {{
                    stopEl.innerText = "⛔ 观望不设单";
                    stopEl.style.fontSize = "14px";
                }}
                if (stopDistEl) {{
                    stopDistEl.innerText = "等待日线企稳";
                    stopDistEl.style.color = "#6b7280";
                }}
            }} else {{
                if (optEl) {{
                    optEl.innerText = (optimalLimit && optimalLimit > 0) ? ("$" + optimalLimit.toFixed(2)) : "--";
                    optEl.style.color = "#ffd700";
                    optEl.style.fontSize = "22px";
                }}
                const diffPct = curActive > 0 ? ((optimalLimit - curActive) / curActive * 100) : 0;
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
                if (stopEl) {{
                    stopEl.innerText = stopLoss > 0 ? ("$" + stopLoss.toFixed(2)) : "--";
                    stopEl.style.fontSize = "22px";
                }}
                if (stopDistEl) {{
                    const stopDist = (optimalLimit > 0 && stopLoss > 0) ? ((stopLoss - optimalLimit) / optimalLimit * 100) : null;
                    if (stopDist !== null) {{
                        stopDistEl.innerText = `距入场 ${{stopDist >= 0 ? '+' : ''}}${{stopDist.toFixed(1)}}%`;
                        stopDistEl.style.color = "#f87171";
                    }} else {{
                        stopDistEl.innerText = "距入场 --";
                        stopDistEl.style.color = "#6b7280";
                    }}
                }}
            }}
            const bandEl = document.getElementById("pEntryFibBand");
            if (bandEl) bandEl.innerText = isInverseHedge ? "🛡️ 反向衰减对冲(不计黄金坑)" : ((curFibLow && curFibHigh) ? (`$${{curFibLow.toFixed(2)}} ~ $${{curFibHigh.toFixed(2)}}`) : "--");

            // 2.6 AI 5年多赛道波段预测哨动态渲染 (多空拔河对决)
            const aiBox = document.getElementById("aiPredictionBox");
            if (aiBox) {{
                const aiMap = (AI_PREDICTIONS && AI_PREDICTIONS.by_ticker) ? AI_PREDICTIONS.by_ticker : {{}};
                const aiItem = aiMap[ticker];
                if (aiItem) {{
                    aiBox.style.display = "block";

                    // 1. Header Rank Badge & TradingView Chart Link
                    const rkBadge = document.getElementById("pAiRankBadge");
                    if (rkBadge) {{
                        const longRank = aiItem.rank !== undefined ? aiItem.rank : "--";
                        const shortRank = aiItem.short_rank !== undefined ? aiItem.short_rank : "--";
                        rkBadge.innerText = `多 #${{longRank}} · 空 #${{shortRank}}`;
                    }}
                    const tvSym = (typeof SYMBOL_MAP !== "undefined" && SYMBOL_MAP[ticker]) ? SYMBOL_MAP[ticker] : ("NASDAQ:" + ticker);
                    const tvExtBtn = document.getElementById("pAiTvLink");
                    if (tvExtBtn) {{
                        tvExtBtn.href = `https://www.tradingview.com/chart/?symbol=${{encodeURIComponent(tvSym)}}`;
                    }}

                    // 2. Dominant Action Banner
                    const actBanner = document.getElementById("pAiActionBanner");
                    const actText = document.getElementById("pAiActionText");
                    const secStar = document.getElementById("pAiSectorStar");
                    const longWr = aiItem.calibrated_win_rate_pct !== undefined ? aiItem.calibrated_win_rate_pct : 50.0;
                    const shortWr = aiItem.calibrated_short_win_rate_pct !== undefined ? aiItem.calibrated_short_win_rate_pct : 50.0;
                    if (actText) {{
                        if (aiItem.is_short_covering && aiItem.action) {{
                            actText.innerText = aiItem.action;
                        }} else if (aiItem.short_action && (shortWr > longWr || (aiItem.short_action.includes("🩸") && !aiItem.action))) {{
                            actText.innerText = aiItem.short_action;
                        }} else {{
                            actText.innerText = aiItem.action || aiItem.short_action || "--";
                        }}
                    }}
                    if (secStar) {{
                        secStar.innerText = `${{aiItem.sector_cn || ''}} ${{aiItem.sector_rating ? aiItem.sector_rating.slice(0, 5) : ''}}`;
                    }}
                    if (actBanner && actText) {{
                        const curText = actText.innerText || "";
                        if (curText.includes("🔥")) {{
                            actBanner.style.background = "rgba(63, 185, 80, 0.16)";
                            actBanner.style.borderColor = "rgba(63, 185, 80, 0.45)";
                            actText.style.color = "#3fb950";
                        }} else if (curText.includes("⚡")) {{
                            actBanner.style.background = "rgba(88, 166, 255, 0.16)";
                            actBanner.style.borderColor = "rgba(88, 166, 255, 0.45)";
                            actText.style.color = "#58a6ff";
                        }} else if (curText.includes("🩸") || curText.includes("破位") || curText.includes("买Put") || curText.includes("做空")) {{
                            actBanner.style.background = "rgba(248, 81, 73, 0.16)";
                            actBanner.style.borderColor = "rgba(248, 81, 73, 0.45)";
                            actText.style.color = "#f87171";
                        }} else if (curText.includes("🛡️") || curText.includes("🚨")) {{
                            actBanner.style.background = "rgba(255, 152, 0, 0.14)";
                            actBanner.style.borderColor = "rgba(255, 152, 0, 0.42)";
                            actText.style.color = "#ffb74d";
                        }} else {{
                            actBanner.style.background = "rgba(255, 213, 79, 0.12)";
                            actBanner.style.borderColor = "rgba(255, 213, 79, 0.38)";
                            actText.style.color = "#ffd54f";
                        }}
                    }}

                    // 3. Dual Tug-of-War Probability Module
                    const lwrEl = document.getElementById("pAiLongWr");
                    if (lwrEl) lwrEl.innerText = `${{longWr.toFixed(1)}}%`;
                    const swrEl = document.getElementById("pAiShortWr");
                    if (swrEl) swrEl.innerText = `${{shortWr.toFixed(1)}}%`;

                    const totalWr = (longWr + shortWr) || 100.0;
                    const longRatio = Math.max(10, Math.min(90, (longWr / totalWr) * 100));
                    const shortRatio = 100 - longRatio;

                    const lBar = document.getElementById("pAiLongBar");
                    if (lBar) lBar.style.width = `${{longRatio.toFixed(1)}}%`;
                    const sBar = document.getElementById("pAiShortBar");
                    if (sBar) sBar.style.width = `${{shortRatio.toFixed(1)}}%`;

                    const lRawEl = document.getElementById("pAiLongRaw");
                    if (lRawEl) {{
                        const rawL = aiItem.predicted_win_rate_pct !== undefined ? aiItem.predicted_win_rate_pct : (aiItem.model_raw_prob_pct || 50.0);
                        lRawEl.innerText = `原始多: ${{rawL.toFixed(1)}}%`;
                    }}
                    const pcrEl = document.getElementById("pAiPcrSentiment");
                    if (pcrEl) {{
                        if (aiItem.dollar_pcr !== undefined) {{
                            pcrEl.innerText = `PCR: ${{aiItem.dollar_pcr.toFixed(2)}} ${{aiItem.pcr_sentiment || ''}}`;
                        }} else {{
                            pcrEl.innerText = aiItem.pcr_sentiment || "期权PCR: 正常";
                        }}
                    }}
                    const sRawEl = document.getElementById("pAiShortRaw");
                    if (sRawEl) {{
                        const rawS = aiItem.model_raw_short_prob_pct !== undefined ? aiItem.model_raw_short_prob_pct : (aiItem.short_raw_prob_pct !== undefined ? aiItem.short_raw_prob_pct : (aiItem.short_model_raw_prob_pct || 50.0));
                        sRawEl.innerText = `原始空: ${{rawS.toFixed(1)}}%`;
                    }}

                    // 4. Dual-Track 4-Pill Dynamic Price Targets
                    const tpEl = document.getElementById("pAiTargetPrice");
                    if (tpEl) {{
                        tpEl.innerHTML = `$${{(aiItem.target_profit_price || 0).toFixed(2)}} <span style="font-size:10px; opacity:0.85;">(+${{(aiItem.target_profit_pct || 0).toFixed(1)}}%)</span>`;
                    }}
                    const slEl = document.getElementById("pAiStopPrice");
                    if (slEl) {{
                        slEl.innerHTML = `$${{(aiItem.stop_loss_price || 0).toFixed(2)}} <span style="font-size:10px; opacity:0.85;">(-${{(aiItem.stop_loss_pct || 0).toFixed(1)}}%)</span>`;
                    }}
                    const stpEl = document.getElementById("pAiShortTargetPrice");
                    if (stpEl) {{
                        stpEl.innerHTML = `$${{(aiItem.short_target_profit_price || 0).toFixed(2)}} <span style="font-size:10px; opacity:0.85;">(-${{(aiItem.short_target_profit_pct || 0).toFixed(1)}}%)</span>`;
                    }}
                    const sslEl = document.getElementById("pAiShortStopPrice");
                    if (sslEl) {{
                        sslEl.innerHTML = `$${{(aiItem.short_stop_loss_price || 0).toFixed(2)}} <span style="font-size:10px; opacity:0.85;">(+${{(aiItem.short_stop_loss_pct || 0).toFixed(1)}}%)</span>`;
                    }}

                    // 5. V2.0 Multi-Level Scenario Points & 4H Resonance
                    const fhEl = document.getElementById("pAiFourHDiv");
                    if (fhEl) {{
                        fhEl.innerText = aiItem.four_h_div || "➖ 4H 均线震荡纠缠";
                    }}
                    const mlEl = document.getElementById("pAiMultiLevels");
                    if (mlEl) {{
                        const s1v = aiItem.s1 !== undefined ? `$${{Number(aiItem.s1).toFixed(2)}}` : "--";
                        const s2v = aiItem.s2 !== undefined ? `$${{Number(aiItem.s2).toFixed(2)}}` : "--";
                        const r1v = aiItem.r1 !== undefined ? `$${{Number(aiItem.r1).toFixed(2)}}` : "--";
                        const r2v = aiItem.r2 !== undefined ? `$${{Number(aiItem.r2).toFixed(2)}}` : "--";
                        mlEl.innerHTML = `<span style="color:#3fb950;">支撑 S1:${{s1v}} / S2:${{s2v}}</span><span style="color:#f85149;">阻力 R1:${{r1v}} / R2:${{r2v}}</span>`;
                    }}
                    const scScriptEl = document.getElementById("pAiScenarioScript");
                    if (scScriptEl) {{
                        scScriptEl.innerText = aiItem.scenario_script || "--";
                    }}
                }} else {{
                    aiBox.style.display = "none";
                }}
            }}

            // 2.8 机构期权多空异动雷达动态渲染
            const optBox = document.getElementById("optionsAnomalyBox");
            if (optBox) {{
                if (data.has_options) {{
                    optBox.style.display = "block";
                    const pcrBadge = document.getElementById("pPcrSentimentBadge");
                    if (pcrBadge) {{
                        if (curActive > 0 && data.exp_low && data.exp_low > 0 && curActive < data.exp_low) {{
                            pcrBadge.innerText = "🔴 击穿期权下轨 (多头被埋)";
                            pcrBadge.style.color = "#ff5252";
                            pcrBadge.style.background = "rgba(255, 82, 82, 0.22)";
                            pcrBadge.style.border = "1px solid #ff5252";
                        }} else if (curActive > 0 && data.exp_high && data.exp_high > 0 && curActive > data.exp_high) {{
                            pcrBadge.innerText = "🔥 突破期权上轨 (逼空加速)";
                            pcrBadge.style.color = "#00e676";
                            pcrBadge.style.background = "rgba(0, 230, 118, 0.22)";
                            pcrBadge.style.border = "1px solid #00e676";
                        }} else {{
                            pcrBadge.innerText = data.pcr_sentiment || "🟡 多空均衡";
                            pcrBadge.style.color = data.pcr_color || "#ffd700";
                            pcrBadge.style.background = (data.pcr_color || "#ffd700") + "22";
                            pcrBadge.style.border = "1px solid " + (data.pcr_color || "#ffd700") + "55";
                        }}
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

                try {{
                    localStorage.setItem("futu_kline_period", currentInterval);
                    localStorage.setItem("futu_kline_style", currentStyle);
                }} catch(e) {{}}

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

                try {{
                    localStorage.setItem("futu_kline_period", currentInterval);
                    localStorage.setItem("futu_kline_style", currentStyle);
                }} catch(e) {{}}

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
                const f = this.getAttribute("data-filter");
                try {{
                    localStorage.setItem("futu_watchlist_filter", f);
                }} catch(e) {{}}
                renderWatchlist(f);
            }});
        }});

        // 📱 手机端专属 5 栏底部 Tab 导航切换引擎
        let currentMobileTab = "watchlist";
        function switchMobileTab(tab) {{
            currentMobileTab = tab;
            try {{
                localStorage.setItem("futu_mobile_tab", tab);
            }} catch(e) {{}}
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

                try {{
                    localStorage.setItem("futu_kline_period", currentInterval);
                    localStorage.setItem("futu_kline_style", currentStyle);
                }} catch(e) {{}}

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
            try {{
                localStorage.setItem("futu_right_panel_tab", cat);
            }} catch(e) {{}}
            document.querySelectorAll(".p-filter-pill").forEach(p => {{
                p.classList.toggle("active", p.getAttribute("data-filter") === cat);
            }});
            // 若点击 🧠AI预测 且当前选中标的不在 AI 28 股池中，自动切换至 AI 榜首标的
            if (cat === "ai" && AI_PREDICTIONS && AI_PREDICTIONS.by_ticker) {{
                if (!AI_PREDICTIONS.by_ticker[currentTicker] && AI_PREDICTIONS.top10 && AI_PREDICTIONS.top10.length > 0) {{
                    selectTicker(AI_PREDICTIONS.top10[0].ticker);
                }}
            }}
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
                "defensive_flight": 0,
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

        // 🎵 全局手势无感解锁音频引擎 (规避现代浏览器 Autoplay 限制)
        let isAudioUnlocked = false;
        function unlockAudioEngine() {{
            const ctx = getAudioContext();
            if (ctx && ctx.state === "suspended") {{
                ctx.resume().then(() => {{
                    isAudioUnlocked = true;
                    // 若有挂起的声音队列，立即开始播放
                    if (soundQueue.length > 0) processSoundQueue();
                }}).catch(() => {{}});
            }} else {{
                isAudioUnlocked = true;
                if (soundQueue.length > 0) processSoundQueue();
            }}
        }}
        ["click", "touchstart", "keydown", "mousedown"].forEach(evt => {{
            window.addEventListener(evt, unlockAudioEngine, {{ passive: true }});
        }});

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
            "defensive_flight": "sounds/defensive_flight.wav",
            "5m_entry": "sounds/5m_entry.wav",
            "portfolio": "sounds/risk_alarm.wav",
            "golden_pit": "sounds/golden_pit.wav",
            "stop_loss": "sounds/breakdown.wav",
            "take_profit": "sounds/take_profit.wav",
            "macro_weather": "sounds/weather.wav"
        }};

        // 🎵 声音队列调度器 (实现多条通知按先后顺序依次播放，间隔 520ms)
        const soundQueue = [];
        let isPlayingSoundQueue = false;

        function queueAlertSound(category, forcePlay = false) {{
            if (!isSoundEnabled && !forcePlay) return;
            soundQueue.push({{ category, forcePlay }});
            processSoundQueue();
        }}

        function processSoundQueue() {{
            if (isPlayingSoundQueue || soundQueue.length === 0) return;
            if (!isSoundEnabled) {{
                soundQueue.length = 0;
                return;
            }}

            isPlayingSoundQueue = true;
            const item = soundQueue.shift();
            playAlertSound(item.category, item.forcePlay);

            // 每条提示音播放后，间隔 520ms 播放下一条，避免声音撞车混杂
            setTimeout(() => {{
                isPlayingSoundQueue = false;
                if (soundQueue.length > 0) {{
                    processSoundQueue();
                }}
            }}, 520);
        }}

        function playAlertSound(category, forcePlay = false) {{
            if (!isSoundEnabled && !forcePlay) return;

            const filePath = SOUND_FILE_MAP[category] || "sounds/5m_entry.wav";
            try {{
                const audio = new Audio(filePath);
                audio.volume = 0.88;
                const p = audio.play();
                if (p !== undefined) {{
                    p.then(() => {{
                        isAudioUnlocked = true;
                    }}).catch(err => {{
                        // 若被浏览器 Autoplay 拦截，启用 Web Audio 兜底合成
                        synthAlertSound(category);
                        if (!isAudioUnlocked && err && err.name === "NotAllowedError") {{
                            showToast("🔊 收到预警！点击页面任意处即可开启声音播放");
                        }}
                    }});
                }}
            }} catch(e) {{
                synthAlertSound(category);
            }}
        }}

        function synthAlertSound(category) {{
            const ctx = getAudioContext();
            if (!ctx) return;
            const now = ctx.currentTime;

            if (category === "defensive_flight") {{
                // 🚨 防御因子起飞: 极度紧迫的交替双频空袭警报音 (988Hz <-> 740Hz 快速4连发)
                playTone(ctx, 987.77, now + 0.00, 0.10, "sawtooth", 0.45);
                playTone(ctx, 739.99, now + 0.11, 0.10, "sawtooth", 0.45);
                playTone(ctx, 987.77, now + 0.22, 0.10, "sawtooth", 0.48);
                playTone(ctx, 739.99, now + 0.33, 0.16, "sawtooth", 0.50);
            }} else if (category === "5m_entry") {{
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

        // 已播放过的通知 ID 集合（跨刷新持久化）
        let knownNotifIds = new Set();
        let isFirstEverVisit = false;
        try {{
            const rawSaved = localStorage.getItem("terminalKnownNotifIds");
            if (rawSaved === null) {{
                isFirstEverVisit = true;
            }} else {{
                const savedIds = JSON.parse(rawSaved);
                if (Array.isArray(savedIds)) savedIds.forEach(id => knownNotifIds.add(id));
            }}
        }} catch(e) {{}}

        function persistKnownNotifIds() {{
            try {{
                const arr = Array.from(knownNotifIds).slice(-200);
                localStorage.setItem("terminalKnownNotifIds", JSON.stringify(arr));
            }} catch(e) {{}}
        }}

        // 首次打开终端时静默记录当前已有通知，避免初次载入发出几十声响铃
        if (isFirstEverVisit) {{
            terminalNotifications.forEach(n => {{
                if (n && n.id) knownNotifIds.add(n.id);
            }});
            persistKnownNotifIds();
        }} else {{
            // 若为常规刷新或更新，检查是否有新放入 INITIAL_NOTIFICATIONS 的全新预警
            const initialFresh = [];
            terminalNotifications.forEach(n => {{
                if (n && n.id && !knownNotifIds.has(n.id)) {{
                    initialFresh.push(n);
                    knownNotifIds.add(n.id);
                }}
            }});
            if (initialFresh.length > 0) {{
                persistKnownNotifIds();
                // 延迟 800ms 待 DOM 就绪后按先后顺序依次排队播报
                setTimeout(() => {{
                    initialFresh.slice(0, 6).forEach(item => {{
                        queueAlertSound(item.category || "5m_entry");
                    }});
                    showToast(`🔔 收到 ${{initialFresh.length}} 条全新实盘量化预警！`);
                }}, 800);
            }}
        }}

        function handleIncomingNotifications(newNotifs) {{
            if (!Array.isArray(newNotifs)) return;
            let freshItems = [];
            newNotifs.forEach(n => {{
                if (n && n.id && !knownNotifIds.has(n.id)) {{
                    freshItems.push(n);
                    knownNotifIds.add(n.id);
                }}
            }});

            terminalNotifications = newNotifs;
            renderNotifList(currentNotifCat);

            // 🎯 用户指令：每推送一个新消息都给提示音！多个消息按先后顺序挨个排队提示
            if (freshItems.length > 0) {{
                persistKnownNotifIds();
                // 限制单批最多排队 6 声，防止极端异常情况下过多轰炸
                const playList = freshItems.slice(0, 6);
                playList.forEach(item => {{
                    queueAlertSound(item.category || "5m_entry");
                }});
                showToast(`🔔 收到 ${{freshItems.length}} 条全新量化预警，正在播报提示音...`);
            }}
        }}

        // 智能轮询：本地环境调 API，GitHub Pages 静态环境无缝读 .terminal_notifications.json
        function pollLatestNotifications() {{
            const isLocal = window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost";
            const notifUrl = isLocal 
                ? `${{API_BASE}}/api/notifications` 
                : `.terminal_notifications.json?_t=${{Date.now()}}`;

            fetch(notifUrl)
                .then(r => {{
                    if (!r.ok) throw new Error("HTTP " + r.status);
                    return r.json();
                }})
                .then(res => {{
                    const list = Array.isArray(res) ? res : (res && Array.isArray(res.notifications) ? res.notifications : null);
                    if (list) {{
                        handleIncomingNotifications(list);
                    }}
                }})
                .catch(() => {{}});
        }}

        // 页面启动执行
        window.addEventListener("DOMContentLoaded", function() {{
            // 1. 还原左侧分类过滤 Tab
            let savedFilter = "all";
            try {{
                savedFilter = localStorage.getItem("futu_watchlist_filter") || "all";
            }} catch(e) {{}}
            document.querySelectorAll(".w-tab").forEach(t => {{
                t.classList.toggle("active", t.getAttribute("data-filter") === savedFilter);
            }});
            renderWatchlist(savedFilter);

            // 2. 还原左侧列表滚动位置
            const wlContainer = document.getElementById("watchlistContainer");
            if (wlContainer) {{
                wlContainer.addEventListener("scroll", function() {{
                    try {{
                        localStorage.setItem("futu_watchlist_scroll", wlContainer.scrollTop);
                    }} catch(e) {{}}
                }}, {{ passive: true }});
                
                try {{
                    const savedScroll = localStorage.getItem("futu_watchlist_scroll");
                    if (savedScroll) {{
                        setTimeout(() => {{
                            wlContainer.scrollTop = parseInt(savedScroll, 10);
                        }}, 60);
                    }}
                }} catch(e) {{}}
            }}

            // 3. 确定并还原当前选中标的，同时清理 URL 参数
            let initTicker = "INTC";
            try {{
                const urlParams = new URLSearchParams(window.location.search);
                const urlSym = (urlParams.get("symbol") || urlParams.get("ticker") || "").trim().toUpperCase();
                const savedT = localStorage.getItem("lastSelectedTicker");
                if (urlSym && (STOCKS_DATA.some(s => s.ticker === urlSym) || MACRO_DATA[urlSym])) {{
                    initTicker = urlSym;
                    localStorage.setItem("lastSelectedTicker", urlSym);
                    // 清理 URL 参数，防止 60 秒后强制回溯
                    urlParams.delete("symbol");
                    urlParams.delete("ticker");
                    const newQuery = urlParams.toString();
                    const cleanUrl = window.location.pathname + (newQuery ? "?" + newQuery : "") + window.location.hash;
                    window.history.replaceState(null, "", cleanUrl);
                }} else if (savedT && (STOCKS_DATA.some(s => s.ticker === savedT) || MACRO_DATA[savedT])) {{
                    initTicker = savedT;
                }}
            }} catch(e) {{}}

            // 4. 还原 K 线周期与样式
            try {{
                const savedPeriod = localStorage.getItem("futu_kline_period");
                const savedStyle = localStorage.getItem("futu_kline_style");
                if (savedPeriod) {{
                    currentInterval = savedPeriod;
                    currentStyle = savedStyle || "1";
                    document.querySelectorAll(".period-tab").forEach(t => {{
                        t.classList.toggle("active", t.getAttribute("data-period") === currentInterval);
                    }});
                    document.querySelectorAll(".m-period-tab").forEach(t => {{
                        t.classList.toggle("active", t.getAttribute("data-period") === currentInterval);
                    }});
                    const minItem = document.querySelector(`.dropdown-item[data-period="${{currentInterval}}"]`);
                    if (minItem) {{
                        document.querySelectorAll(".dropdown-item").forEach(d => d.classList.remove("active"));
                        minItem.classList.add("active");
                        const mb = document.getElementById("minDropdownBtn");
                        if (mb) mb.classList.add("active");
                        const ml = document.getElementById("currentMinLabel");
                        if (ml) ml.innerText = minItem.getAttribute("data-label");
                    }}
                }}
            }} catch(e) {{}}

            // 5. 选中标的 (fromUserClick=false，不破坏移动端当前视图)
            selectTicker(initTicker, false);

            // 6. 还原右侧功能面板 Tab
            try {{
                const savedRightTab = localStorage.getItem("futu_right_panel_tab");
                if (savedRightTab && savedRightTab !== "all") {{
                    filterRightCards(savedRightTab);
                }}
            }} catch(e) {{}}

            // 7. 📱 手机端初始视图自适应：还原记忆 Tab，杜绝强制跳回自选列表
            if (window.innerWidth <= 768) {{
                let savedMobileTab = "watchlist";
                try {{
                    savedMobileTab = localStorage.getItem("futu_mobile_tab") || "watchlist";
                }} catch(e) {{}}
                switchMobileTab(savedMobileTab);
            }}

            // 8. 🚨 防御因子起飞警报：每次页面刷新/载入，若检测到防御因子起飞，立即排队播报极紧迫警报提示音
            const dfBanner = document.getElementById("deleveragingAlertBanner");
            const isDfAlert = (typeof WEATHER_INFO !== "undefined" && WEATHER_INFO && WEATHER_INFO.defensive_flight && WEATHER_INFO.defensive_flight.is_alert) || !!dfBanner;
            if (isDfAlert) {{
                setTimeout(() => {{
                    queueAlertSound("defensive_flight");
                    showToast("🚨 【全网警报】防御因子起飞！机构盘中去杠杆踩踏风险高企！");
                }}, 700);
            }}

            renderNotifList("all");
            updateSoundUI();
            setInterval(pollLatestNotifications, 20000);
        }});
    </script>
</body>
</html>
"""
    return html

if __name__ == "__main__":
    print("Futu Pro Terminal Generator Ready.")
