@echo off
chcp 65001 >nul
title [美股量化雷达] 安全停止后台进程
echo [*] 正在安全停止所有后台雷达进程...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*integrated_market_radar.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo.
echo [🛑 成功] 所有后台雷达进程已全部终止，8765 端口已释放！
echo.
pause
