# -*- coding: utf-8 -*-
"""在桌面创建美股量化雷达快捷方式（动态定位当前项目目录，不依赖任何旧电脑绝对路径）。

用法：
    python create_desktop_shortcuts.py

生成内容：
    - 桌面两个文件夹（中文 / 英文），内含各看板 .url 快捷方式；
    - 每个文件夹内生成「一键刷新雷达数据.bat / 测试微信推送.bat」，
      其中引用的 integrated_market_radar.py 路径在生成时按当前项目目录动态解析。
    换电脑/换目录后重新运行本脚本即可，无需手改任何绝对路径。
"""
import os
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
desktop = os.path.join(os.path.expanduser("~"), "Desktop")

folders = [
    os.path.join(desktop, "美股量化雷达导航"),
    os.path.join(desktop, "TradeRadar_Navigation"),
]


def _uri(filename: str) -> str:
    """生成 file:// 协议 URI（自动处理盘符、空格与中文路径）。"""
    return Path(BASE_DIR, filename).as_uri()


shortcuts = [
    ("0-富途牛牛专业看盘终端.url", _uri("futu_pro_terminal.html")),
    ("1-宏观大盘司令塔.url", _uri("macro_radar.html")),
    ("2-核心个股碎股雷达.url", _uri("stocks_radar.html")),
    ("3-INTC作战防线图.url", _uri("stocks_radar.html")),  # INTC 防线已并入个股雷达
    ("4-AI美股5年预测看板.url", _uri("predict_radar_5y.html")),
    ("5-Antigravity手机遥控仪表板.url", "https://antigravity.google.com"),
]

shortcuts_en = [
    ("0-Futu_Pro_Terminal (富途看盘终端).url", _uri("futu_pro_terminal.html")),
    ("1-Macro_Radar (宏观大盘司令塔).url", _uri("macro_radar.html")),
    ("2-Stocks_Radar (核心个股碎股雷达).url", _uri("stocks_radar.html")),
    ("3-INTC_Defense (INTC作战防线图).url", _uri("stocks_radar.html")),
    ("4-AI_Predict_Radar_5Y (AI5年预测看板).url", _uri("predict_radar_5y.html")),
    ("5-Antigravity_Remote (手机遥控仪表板).url", "https://antigravity.google.com"),
]

RADAR_PY = os.path.join(BASE_DIR, "integrated_market_radar.py")

for folder in folders:
    os.makedirs(folder, exist_ok=True)
    sc_list = shortcuts if "美股" in folder else shortcuts_en
    for name, url in sc_list:
        p = os.path.join(folder, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"[InternetShortcut]\nURL={url}\n")

    bat_p = os.path.join(folder, "一键刷新雷达数据.bat" if "美股" in folder else "Refresh_Radar_Data.bat")
    bat_content = f'''@echo off
chcp 65001 >nul
echo [Trade Radar] 正在抓取全量 70 只标的美股实时行情并刷新看板...
python "{RADAR_PY}" --once
echo.
echo [提示] 刷新完成！后台守护进程倒计时已自动同步重置为 180 秒。
pause
'''
    with open(bat_p, "w", encoding="utf-8") as f:
        f.write(bat_content)

    test_wechat_bat_p = os.path.join(folder, "测试微信推送.bat" if "美股" in folder else "Test_WeChat_Alert.bat")
    test_wechat_content = f'''@echo off
chcp 65001 >nul
echo [Trade Radar] 正在测试向微信发送预警通知...
python "{RADAR_PY}" --test-wechat
echo.
pause
'''
    with open(test_wechat_bat_p, "w", encoding="utf-8") as f:
        f.write(test_wechat_content)

print("Desktop folders created successfully!")
print(f"Project dir: {BASE_DIR}")
