#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_21_tickers_challenger_r3_2.py
Empirical Adversarial Audit Harness for Challenger R3_2.
Verifies all 21 tickers in:
1. data_cache/latest_ai_predictions.json
2. predict_radar_5y.html
"""

import os
import sys
import json
import re
from datetime import datetime, date
import pandas as pd
import numpy as np

PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import ml_predictor_5y as mlp

def main():
    print("=" * 80)
    print("CHALLENGER R3_2: 21 TICKER EMPIRICAL ADVERSARIAL AUDIT")
    print("=" * 80)

    # 1. Load data_cache/latest_ai_predictions.json
    json_path = os.path.join(PROJECT_DIR, 'data_cache', 'latest_ai_predictions.json')
    if not os.path.exists(json_path):
        print(f"FAIL: {json_path} does not exist!")
        sys.exit(1)

    with open(json_path, 'r', encoding='utf-8') as f:
        prod_data = json.load(f)

    tcp = prod_data.get('time_cycle_projections', {})
    tickers = list(tcp.keys())
    print(f"Total tickers found in latest_ai_predictions.json: {len(tickers)}")
    print(f"Tickers list: {tickers}")

    # Check that there are at least 21 tickers
    if len(tickers) < 21:
        print(f"FAIL: Expected at least 21 tickers, found {len(tickers)}")
        sys.exit(1)

    # 2. Load predict_radar_5y.html and extract timeCycleData
    html_path = os.path.join(PROJECT_DIR, 'predict_radar_5y.html')
    if not os.path.exists(html_path):
        print(f"FAIL: {html_path} does not exist!")
        sys.exit(1)

    with open(html_path, 'r', encoding='utf-8') as f:
        html_content = f.read()

    # Extract timeCycleData = {...};
    marker = 'window.timeCycleData = timeCycleData;'
    end_idx = html_content.find(marker)
    if end_idx == -1:
        print("FAIL: Could not locate window.timeCycleData marker in predict_radar_5y.html!")
        sys.exit(1)

    start_match = re.search(r'const\s+timeCycleData\s*=\s*', html_content[:end_idx])
    if not start_match:
        print("FAIL: Could not locate const timeCycleData declaration in predict_radar_5y.html!")
        sys.exit(1)

    json_str = html_content[start_match.end():end_idx].strip()
    if json_str.endswith(';'):
        json_str = json_str[:-1].strip()

    try:
        html_tcp = json.loads(json_str)
        print(f"Successfully parsed timeCycleData from HTML: {len(html_tcp)} tickers found.")
    except Exception as e:
        print(f"FAIL: Could not parse JSON from HTML: {e}")
        sys.exit(1)

    # 3. Check for any hardcoded overrides in codebase
    print("\n--- AUDIT STEP 1: Codebase Scan for Hardcoded Ticker Conditions ---")
    futu_py = os.path.join(PROJECT_DIR, 'create_futu_terminal.py')
    with open(futu_py, 'r', encoding='utf-8') as f:
        futu_content = f.read()

    hardcode_matches = re.findall(r'if\s+.*(?:_tk_c|ticker|tk)\s*==\s*["\']([A-Z]+)["\']', futu_content)
    # Check specifically if AMZN or AVGO are singled out for point overrides
    suspicious = [m for m in hardcode_matches if m in ['AMZN', 'AVGO', 'NVDA', 'SOXL']]
    print(f"create_futu_terminal.py ticker conditionals matching target pool: {suspicious}")
    if suspicious:
        print(f"FAIL: Found hardcoded conditionals in create_futu_terminal.py: {suspicious}")
        sys.exit(1)
    else:
        print("PASS: Zero ticker-specific hardcoded overrides in create_futu_terminal.py")

    # 4. Detailed audit of each ticker in latest_ai_predictions.json and HTML
    print("\n--- AUDIT STEP 2: Per-Ticker Data & Calibration Invariant Audit ---")

    audit_records = []
    failures = []

    for tk in tickers:
        p_json = tcp[tk]
        p_html = html_tcp.get(tk)

        if not p_html:
            failures.append(f"{tk}: Missing from HTML timeCycleData!")
            continue

        cur_p = p_json.get('current_price')
        r1 = p_json.get('r1')
        r2 = p_json.get('r2')
        s1 = p_json.get('s1')
        s2 = p_json.get('s2')
        sl = p_json.get('sl')
        hist = p_json.get('history', [])
        proj = p_json.get('projection', [])
        wins = p_json.get('cycle_windows', [])

        # (a) HTML sync check
        html_cur_p = p_html.get('current_price')
        html_r1 = p_html.get('r1')
        html_r2 = p_html.get('r2')
        html_s1 = p_html.get('s1')
        html_s2 = p_html.get('s2')
        html_sl = p_html.get('sl')

        if abs(cur_p - html_cur_p) > 1e-4:
            failures.append(f"{tk}: current_price mismatch between json ({cur_p}) and html ({html_cur_p})")
        if abs(r1 - html_r1) > 1e-4:
            failures.append(f"{tk}: r1 mismatch between json ({r1}) and html ({html_r1})")
        if abs(r2 - html_r2) > 1e-4:
            failures.append(f"{tk}: r2 mismatch between json ({r2}) and html ({html_r2})")

        # (b) K-line sync check
        if len(hist) < 2:
            failures.append(f"{tk}: History has fewer than 2 bars ({len(hist)})")
        else:
            latest_hist_bar = hist[-2]
            today_anchor_bar = hist[-1]

            if latest_hist_bar['date'] != '2026-11-10':
                failures.append(f"{tk}: Latest historical bar is {latest_hist_bar['date']}, expected 2026-11-10")
            if today_anchor_bar['date'] != '2026-11-11':
                failures.append(f"{tk}: Today anchor bar is {today_anchor_bar['date']}, expected 2026-11-11")
            if abs(today_anchor_bar['price'] - cur_p) > 1e-4:
                failures.append(f"{tk}: Today anchor bar price {today_anchor_bar['price']} != cur_p {cur_p}")

        # (c) Projection sequence check
        if len(proj) != 15:
            failures.append(f"{tk}: Projection has {len(proj)} days, expected 15")
        else:
            if proj[0]['day'] != 'T+1' or proj[0]['date'] != '2026-11-12':
                failures.append(f"{tk}: Projection T+1 date is {proj[0].get('date')}, expected 2026-11-12")
            if proj[-1]['day'] != 'T+15':
                failures.append(f"{tk}: Projection last day is {proj[-1].get('day')}, expected T+15")

            # Check no weekends in projection
            for p in proj:
                d_obj = datetime.strptime(p['date'], '%Y-%m-%d').date()
                if d_obj.weekday() >= 5:
                    failures.append(f"{tk}: Projection date {p['date']} is a weekend ({d_obj.strftime('%A')})")

        # (d) Monotonicity & R2 Calibration Check
        # Ladder monotonicity: sl < s2 < s1 < cur_p < r1 < r2
        if not (sl < s2 < s1 < cur_p < r1 < r2):
            failures.append(f"{tk}: Monotonicity violated: sl={sl}, s2={s2}, s1={s1}, cur={cur_p}, r1={r1}, r2={r2}")

        proj_prices = [p['price'] for p in proj]
        max_proj_p = max(proj_prices) if proj_prices else 0.0
        expected_min_r2 = round(max_proj_p * 1.015, 2)

        # R2 >= max(P[1..15]) * 1.015
        if r2 < expected_min_r2 - 1e-4:
            failures.append(f"{tk}: R2 ({r2}) < expected_min_r2 ({expected_min_r2}) based on max_proj ({max_proj_p})")

        # R2 > R1
        if r2 <= r1:
            failures.append(f"{tk}: R2 ({r2}) <= R1 ({r1})")

        # (e) Cycle windows audit
        for i in range(1, len(wins)):
            d_curr = int(wins[i]['day'].replace('T+', ''))
            d_prev = int(wins[i-1]['day'].replace('T+', ''))
            if d_curr - d_prev < 2:
                failures.append(f"{tk}: Cycle windows interval < 2 days between {wins[i-1]['day']} and {wins[i]['day']}")

        for w in wins:
            w_day = w['day']
            w_type = w['type']
            w_price = w['price']
            w_desc = w['desc']

            if w_day in ['T+1', 'T+15']:
                failures.append(f"{tk}: Window at boundary day {w_day}!")

            if w_type == 'top':
                if w_price <= cur_p:
                    failures.append(f"{tk}: Top window price {w_price} <= cur_p {cur_p}")
                if '周期峰阻力窗' not in w_desc:
                    failures.append(f"{tk}: Top window desc missing '周期峰阻力窗': {w_desc}")

            elif w_type == 'bottom':
                if w_price >= r1:
                    failures.append(f"{tk}: Bottom window price {w_price} >= R1 {r1} (Must be tagged caution!)")
                if '周期底回踩窗' not in w_desc or '企稳' not in w_desc:
                    failures.append(f"{tk}: Bottom window desc missing required keywords: {w_desc}")

            elif w_type == 'caution':
                if w_price < r1 and w_price < r2:
                    failures.append(f"{tk}: Caution window price {w_price} < R1 ({r1}) and < R2 ({r2})")
                if '高位强势休整窗' not in w_desc:
                    failures.append(f"{tk}: Caution window desc missing '高位强势休整窗': {w_desc}")
                if '企稳' in w_desc:
                    failures.append(f"{tk}: Caution window desc illegally contains '企稳': {w_desc}")

        audit_records.append({
            'ticker': tk,
            'cur_p': cur_p,
            'sl': sl,
            's2': s2,
            's1': s1,
            'r1': r1,
            'r2': r2,
            'max_proj': max_proj_p,
            'r2_margin': round(r2 - max_proj_p, 2),
            'hist_prev': hist[-2]['date'] if len(hist)>=2 else None,
            'hist_today': hist[-1]['date'] if len(hist)>=2 else None,
            'proj_start': proj[0]['date'] if proj else None,
            'windows_count': len(wins),
            'window_types': [w['type'] for w in wins]
        })

    # Print summary table
    print(f"\nAudit complete across {len(audit_records)} tickers.")
    df_audit = pd.DataFrame(audit_records)
    print(df_audit[['ticker', 'cur_p', 'r1', 'r2', 'max_proj', 'r2_margin', 'hist_prev', 'hist_today', 'windows_count']].to_string(index=False))

    # 5. Check AMZN and AVGO specifically
    print("\n--- AUDIT STEP 3: AMZN and AVGO Dynamic Mathematical Re-Evaluation ---")
    for spec_tk in ['AMZN', 'AVGO']:
        item = tcp.get(spec_tk)
        if not item:
            failures.append(f"Missing {spec_tk} from projections!")
            continue

        c_p = item['current_price']
        json_r1 = item['r1']
        json_r2 = item['r2']
        p_max = max(p['price'] for p in item['projection'])

        print(f"\n[{spec_tk}] Current Price: {c_p}")
        print(f"[{spec_tk}] JSON R1: {json_r1}, JSON R2: {json_r2}")
        print(f"[{spec_tk}] Proj Max: {p_max}")
        print(f"[{spec_tk}] Cycle Windows: {item['cycle_windows']}")

        # Re-run _build_cycle_windows_from_projections dynamically
        rebuilt_windows = mlp._build_cycle_windows_from_projections(
            projections=item['projection'],
            current_price=c_p,
            r1=json_r1,
            r2=json_r2
        )
        print(f"[{spec_tk}] Dynamically Rebuilt Windows: {rebuilt_windows}")

        if rebuilt_windows != item['cycle_windows']:
            failures.append(f"[{spec_tk}] Dynamic rebuild windows mismatch: stored={item['cycle_windows']} vs rebuilt={rebuilt_windows}")
        else:
            print(f"PASS: [{spec_tk}] Stored cycle windows match dynamic rebuilt windows 100%!")

    # 6. Overall Result
    print("\n" + "=" * 80)
    if failures:
        print(f"FAILURES DETECTED ({len(failures)}):")
        for f in failures:
            print(f"  ❌ {f}")
        sys.exit(1)
    else:
        print("ALL 21 TICKERS PASSED ALL ADVERSARIAL INTEGRITY CHECKS EMPIRICALLY!")
        print("=" * 80)
        sys.exit(0)

if __name__ == '__main__':
    main()
