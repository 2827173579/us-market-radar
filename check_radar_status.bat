@echo off
chcp 65001 >nul
echo ======================================================================
echo 🔍 [Trade Radar] 美股量化雷达 · 后台守护进程运行状态检查
echo ======================================================================
powershell -Command "$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*integrated_market_radar.py*--loop-daemon*' }; if ($procs) { Write-Host '🟢 守护进程状态: [正常运行中]' -ForegroundColor Green; foreach ($p in $procs) { Write-Host ('  • 进程 PID: ' + $p.ProcessId + ' | 启动时间: ' + $p.CreationDate); } } else { Write-Host '🔴 守护进程状态: [未在运行]' -ForegroundColor Red; }"
echo.
echo ----------------------------------------------------------------------
echo 📋 最新轮询日志摘要 (最后 15 行):
echo ----------------------------------------------------------------------
powershell -Command "$log = 'C:\Users\User\.gemini\antigravity-ide\scratch\us-market-radar\.radar_daemon.log'; if (Test-Path $log) { Get-Content $log -Tail 15 } else { echo '(暂无后台日志文件，当前可能在 IDE 终端会话中运行)' }"
echo.
echo ----------------------------------------------------------------------
echo 🌐 本地局域网看盘地址:
echo    👉 富途专业终端: http://192.168.45.136:8765
echo    👉 AI5年推演看板: http://192.168.45.136:8765/predict_radar_5y.html
echo ======================================================================
pause
