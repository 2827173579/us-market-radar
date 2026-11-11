# -*- coding: utf-8 -*-
import json,re,os,sys
import pandas as pd
repo=r"E:\wendang\deepseek-harness\default-workspace"; os.chdir(repo)
panel=pd.read_parquet("data_cache/ml_panel_5y.parquet",columns=["Date","Ticker","Close"])
panel["Date"]=pd.to_datetime(panel["Date"])
last=panel.sort_values("Date").groupby("Ticker")["Close"].last().to_dict()
html=open("predict_radar_5y.html",encoding="utf-8",errors="replace").read()
m=re.search(r'(?:const|let|var)\s+timeCycleData\s*=\s*(\{.*?\});\s*window\.timeCycleData',html,re.S)
tcd=json.loads(m.group(1)) if m else {}
ai=json.load(open("data_cache/latest_ai_predictions.json",encoding="utf-8")); by=ai.get("by_ticker",{})
fails=[]; checked=0
ap={str(x.get("ticker")):x for x in ai.get("all_predictions",[])}
for tk,px in last.items():
    for src,val in (("html",(tcd.get(tk) or {}).get("current_price")),("json",(by.get(tk) or {}).get("current_price")),("all",(ap.get(tk) or {}).get("current_price"))):
        if val is None: continue
        try: d=abs(float(val)-float(px))/float(px)*100
        except: continue
        checked+=1
        if d>15: fails.append((tk,src,round(float(val),2),round(float(px),2),round(d,1)))
print("checked",checked,"fail_count",len(fails))
for x in fails[:30]: print("FAIL",x)
print("AMD html", (tcd.get("AMD") or {}).get("current_price"), "json", (by.get("AMD") or {}).get("current_price"), "panel_close", last.get("AMD"))
print("140.0 occurrences in html", html.count("140.0"))
sys.exit(1 if fails else 0)
