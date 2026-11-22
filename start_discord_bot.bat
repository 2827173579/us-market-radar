@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [*] 正在启动美股量化雷达 Discord 双向交互机器人 (独立只读进程)...
python -u discord_radar_bot.py
pause
