# -*- coding: utf-8 -*-
import os

desktop = os.path.join(os.path.expanduser('~'), 'Desktop')

folders = [
    os.path.join(desktop, '美股量化雷达导航'),
    os.path.join(desktop, 'TradeRadar_Navigation')
]

shortcuts = [
    ('0-富途牛牛专业看盘终端.url', 'file:///C:/Users/User/.gemini/antigravity-ide/scratch/futu_pro_terminal.html'),
    ('1-宏观大盘司令塔.url', 'file:///C:/Users/User/.gemini/antigravity-ide/scratch/macro_radar.html'),
    ('2-核心个股碎股雷达.url', 'file:///C:/Users/User/.gemini/antigravity-ide/scratch/stocks_radar.html'),
    ('3-INTC作战防线图.url', 'file:///C:/Users/User/.gemini/antigravity-ide/scratch/intc_radar.html'),
    ('4-AI美股5年预测看板.url', 'file:///C:/Users/User/.gemini/antigravity-ide/scratch/predict_radar_5y.html'),
    ('5-Antigravity手机遥控仪表板.url', 'https://antigravity.google.com')
]

shortcuts_en = [
    ('0-Futu_Pro_Terminal (富途看盘终端).url', 'file:///C:/Users/User/.gemini/antigravity-ide/scratch/futu_pro_terminal.html'),
    ('1-Macro_Radar (宏观大盘司令塔).url', 'file:///C:/Users/User/.gemini/antigravity-ide/scratch/macro_radar.html'),
    ('2-Stocks_Radar (核心个股碎股雷达).url', 'file:///C:/Users/User/.gemini/antigravity-ide/scratch/stocks_radar.html'),
    ('3-INTC_Defense (INTC作战防线图).url', 'file:///C:/Users/User/.gemini/antigravity-ide/scratch/intc_radar.html'),
    ('4-AI_Predict_Radar_5Y (AI5年预测看板).url', 'file:///C:/Users/User/.gemini/antigravity-ide/scratch/predict_radar_5y.html'),
    ('5-Antigravity_Remote (手机遥控仪表板).url', 'https://antigravity.google.com')
]

for folder in folders:
    os.makedirs(folder, exist_ok=True)
    sc_list = shortcuts if '美股' in folder else shortcuts_en
    for name, url in sc_list:
        p = os.path.join(folder, name)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(f"[InternetShortcut]\nURL={url}\n")

    bat_p = os.path.join(folder, '一键刷新雷达数据.bat' if '美股' in folder else 'Refresh_Radar_Data.bat')
    bat_content = '''@echo off
chcp 65001 >nul
echo [Trade Radar] 正在抓取全量 70 只标的美股实时行情并刷新看板...
python "C:\\Users\\User\\.gemini\\antigravity-ide\\scratch\\integrated_market_radar.py" --once
echo.
echo [提示] 刷新完成！后台守护进程倒计时已自动同步重置为 180 秒。
pause
'''
    with open(bat_p, 'w', encoding='ansi') as f:
        f.write(bat_content)

    test_wechat_bat_p = os.path.join(folder, '测试微信推送.bat' if '美股' in folder else 'Test_WeChat_Alert.bat')
    test_wechat_content = '''@echo off
chcp 65001 >nul
echo [Trade Radar] 正在测试向微信发送预警通知...
python "C:\\Users\\User\\.gemini\\antigravity-ide\\scratch\\integrated_market_radar.py" --test-wechat
echo.
pause
'''
    with open(test_wechat_bat_p, 'w', encoding='ansi') as f:
        f.write(test_wechat_content)

print("Desktop folders created successfully!")
