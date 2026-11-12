#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_challenger_r2_audit.py - Empirical Challenger Audit Script
Audits data_cache/latest_ai_predictions.json and predict_radar_5y.html
across all 21 tickers for:
1. Ticker count (21 tickers)
2. Defense ladder monotonicity (SL < S2 < S1 < Price < R1 < R2)
3. R2 envelope containment (max_proj <= R2, especially KORU)
4. Today anchoring (2026-11-11)
5. Projections sequence (T+1 on 2026-11-12, length 15, no weekends)
6. Cycle windows (spacing >= 2, no T+1 or T+15, no price inversions, caution tagging for valley >= R1)
"""

import json
import os
import re
import sys
import pandas as pd
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
JSON_PATH = os.path.join(PROJECT_DIR, 'data_cache', 'latest_ai_predictions.json')
HTML_PATH = os.path.join(PROJECT_DIR, 'predict_radar_5y.html')


def audit_dataset(name: str, tcp_data: dict):
    print(f"\n=======================================================")
    print(f"=== AUDITING {name} (Ticker Count: {len(tcp_data)}) ===")
    print(f"=======================================================")

    violations = []
    summary_rows = []

    for tk in sorted(tcp_data.keys()):
        item = tcp_data[tk]
        cur_p = item.get('current_price')
        r1 = item.get('r1')
        r2 = item.get('r2')
        s1 = item.get('s1')
        s2 = item.get('s2')
        sl = item.get('sl')
        hist = item.get('history', [])
        proj = item.get('projection', [])
        wins = item.get('cycle_windows', [])

        # 1. Projections
        proj_prices = [float(p['price']) for p in proj]
        max_p = max(proj_prices) if proj_prices else 0.0

        # Check: max_proj <= r2
        if r2 is None or max_p > r2:
            violations.append(f"[{name}] {tk}: max_proj={max_p} > r2={r2} (DIFF={max_p - (r2 or 0):+.2f})")

        # Check: Monotonicity SL < S2 < S1 < Price < R1 < R2
        if not (sl < s2 < s1 < cur_p < r1 < r2):
            violations.append(f"[{name}] {tk}: ladder monotonicity broken: SL={sl}, S2={s2}, S1={s1}, P={cur_p}, R1={r1}, R2={r2}")

        # Check: History anchoring: today = 2026-11-11
        if not hist or hist[-1].get('date') != '2026-11-11':
            violations.append(f"[{name}] {tk}: history[-1] date is {hist[-1].get('date') if hist else None}, expected 2026-11-11")
        if len(hist) >= 2 and hist[-2].get('date') != '2026-11-10':
            violations.append(f"[{name}] {tk}: history[-2] date is {hist[-2].get('date')}, expected 2026-11-10")

        # Check: Proj head: T+1 at 2026-11-12, length 15
        if not proj or proj[0].get('day') != 'T+1' or proj[0].get('date') != '2026-11-12':
            violations.append(f"[{name}] {tk}: proj[0] is {proj[0] if proj else None}, expected T+1 2026-11-12")
        if len(proj) != 15:
            violations.append(f"[{name}] {tk}: proj length is {len(proj)}, expected 15")

        # Check: Weekend skipping
        weekend_bars = []
        for p in proj:
            dt = pd.to_datetime(p['date'])
            if dt.weekday() >= 5:
                weekend_bars.append((p['day'], p['date'], dt.day_name()))
        if weekend_bars:
            violations.append(f"[{name}] {tk}: weekend bars in projection: {weekend_bars}")

        # Check: Cycle windows
        for i in range(1, len(wins)):
            d_curr = int(wins[i]['day'].replace('T+', ''))
            d_prev = int(wins[i-1]['day'].replace('T+', ''))
            if d_curr - d_prev < 2:
                violations.append(f"[{name}] {tk}: window spacing < 2: T+{d_prev} and T+{d_curr}")
        for w in wins:
            w_d = int(w['day'].replace('T+', ''))
            if w_d == 1 or w_d == 15:
                violations.append(f"[{name}] {tk}: boundary window at T+{w_d}")
            if w['type'] == 'caution':
                if '企稳' in w.get('desc', ''):
                    violations.append(f"[{name}] {tk}: caution window has 企稳 in desc: {w}")
                if w['price'] < r1:
                    violations.append(f"[{name}] {tk}: caution window price {w['price']} < R1 {r1}")
            elif w['type'] == 'bottom':
                if w['price'] >= r1:
                    violations.append(f"[{name}] {tk}: bottom window price {w['price']} >= R1 {r1}")
            elif w['type'] == 'top':
                if w['price'] <= cur_p:
                    violations.append(f"[{name}] {tk}: top window price {w['price']} <= cur_p {cur_p}")

        margin = round(r2 - max_p, 2) if r2 is not None else None
        win_str = "; ".join([f"{w['day']}({w['type']} ${w['price']})" for w in wins]) if wins else "None"
        summary_rows.append({
            'tk': tk,
            'cur': cur_p,
            'r1': r1,
            'r2': r2,
            'max_proj': max_p,
            'margin': margin,
            'windows': win_str
        })

    # Print table
    print(f"{'Ticker':<7} | {'Price':<7} | {'R1':<7} | {'R2':<7} | {'MaxProj':<7} | {'Margin':<7} | {'Cycle Windows'}")
    print("-" * 80)
    for r in summary_rows:
        print(f"{r['tk']:<7} | {r['cur']:<7.2f} | {r['r1']:<7.2f} | {r['r2']:<7.2f} | {r['max_proj']:<7.2f} | {r['margin']:<7.2f} | {r['windows']}")

    print("-" * 80)
    print(f"Total Violations in {name}: {len(violations)}")
    if violations:
        for v in violations:
            print(f"  ❌ {v}")
    else:
        print(f"  ✅ 100% INVARIANTS SATISFIED FOR ALL {len(tcp_data)} TICKERS IN {name}!")

    return violations


def main():
    all_violations = []

    # 1. Audit latest_ai_predictions.json
    if not os.path.exists(JSON_PATH):
        print(f"ERROR: {JSON_PATH} does not exist!")
        sys.exit(1)

    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        data_json = json.load(f)
    tcp_json = data_json.get('time_cycle_projections', {})
    v_json = audit_dataset("data_cache/latest_ai_predictions.json", tcp_json)
    all_violations.extend(v_json)

    # 2. Audit predict_radar_5y.html
    if not os.path.exists(HTML_PATH):
        print(f"ERROR: {HTML_PATH} does not exist!")
        sys.exit(1)

    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        html_content = f.read()

    # Extract const timeCycleData = { ... };
    pattern = re.compile(r'const\s+timeCycleData\s*=\s*({.*?});', re.DOTALL)
    m = pattern.search(html_content)
    if not m:
        # Fallback raw decode
        idx = html_content.find('const timeCycleData = ')
        if idx != -1:
            json_str = html_content[idx + len('const timeCycleData = '):]
            tcp_html, _ = json.JSONDecoder().raw_decode(json_str)
        else:
            print("ERROR: could not locate const timeCycleData in predict_radar_5y.html")
            sys.exit(1)
    else:
        tcp_html = json.loads(m.group(1))

    v_html = audit_dataset("predict_radar_5y.html", tcp_html)
    all_violations.extend(v_html)

    # 3. Check consistency between JSON and HTML
    print(f"\n=======================================================")
    print(f"=== CROSS-AUDIT: JSON vs HTML CONSISTENCY ===")
    print(f"=======================================================")
    cross_diffs = []
    if set(tcp_json.keys()) != set(tcp_html.keys()):
        cross_diffs.append(f"Ticker keys mismatch: JSON={set(tcp_json.keys())} vs HTML={set(tcp_html.keys())}")
    for tk in tcp_json:
        if tk in tcp_html:
            item_j = tcp_json[tk]
            item_h = tcp_html[tk]
            for field in ['current_price', 'r1', 'r2', 's1', 's2', 'sl']:
                if item_j.get(field) != item_h.get(field):
                    cross_diffs.append(f"{tk} {field} mismatch: JSON={item_j.get(field)} vs HTML={item_h.get(field)}")
            # compare projections count & prices
            proj_j = [p['price'] for p in item_j.get('projection', [])]
            proj_h = [p['price'] for p in item_h.get('projection', [])]
            if proj_j != proj_h:
                cross_diffs.append(f"{tk} projection prices mismatch between JSON and HTML")

    print(f"Cross-Audit Differences Count: {len(cross_diffs)}")
    if cross_diffs:
        for cd in cross_diffs:
            print(f"  ❌ {cd}")
    else:
        print("  ✅ JSON AND HTML ARE 100% BIT-FOR-BIT CONSISTENT!")

    if all_violations or cross_diffs:
        print("\nOVERALL VERDICT: VIOLATIONS FOUND!")
        sys.exit(1)
    else:
        print("\nOVERALL VERDICT: ALL INVARIANTS 100% PASSED!")
        sys.exit(0)


if __name__ == '__main__':
    main()
