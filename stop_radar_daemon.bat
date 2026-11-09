@echo off
chcp 65001 >nul
echo [Trade Radar] 正在停止后台轮询守护进程...
powershell -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*integrated_market_radar.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; echo ('[+] 已停止进程 PID: ' + $_.ProcessId) }"
echo.
echo [提示] 守护进程已停止。如果需要重新启动，可运行 Start_Radar_Daemon.vbs。
pause
