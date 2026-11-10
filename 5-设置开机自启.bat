@echo off
chcp 65001 >nul
title [美股量化雷达] 设置开机自动启动
echo [*] 正在为当前电脑配置开机自启动...

set "CURRENT_DIR=%~dp0"
:: 去除尾部反斜杠
if "%CURRENT_DIR:~-1%"=="\" set "CURRENT_DIR=%CURRENT_DIR:~0,-1%"

set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET_VBS=%STARTUP_FOLDER%\TradeRadar_AutoStart.vbs"

(
echo Dim ws, fso, wmi, colProcesses, proc, isRunning
echo Set ws = CreateObject^("Wscript.Shell"^)
echo Set fso = CreateObject^("Scripting.FileSystemObject"^)
echo Set wmi = GetObject^("winmgmts:\\.\root\cimv2"^)
echo.
echo isRunning = False
echo Set colProcesses = wmi.ExecQuery^("Select CommandLine from Win32_Process Where Name = 'python.exe' or Name = 'pythonw.exe'"^)
echo For Each proc in colProcesses
echo     If Not IsNull^(proc.CommandLine^) Then
echo         If InStr^(proc.CommandLine, "integrated_market_radar.py"^) ^> 0 And InStr^(proc.CommandLine, "--loop-daemon"^) ^> 0 Then
echo             isRunning = True
echo             Exit For
echo         End If
echo     End If
echo Next
echo.
echo If Not isRunning Then
echo     Dim radarDir, logFile, cmd
echo     radarDir = "%CURRENT_DIR%"
echo     logFile = radarDir ^& "\.radar_daemon.log"
echo     ws.CurrentDirectory = radarDir
echo     cmd = "cmd.exe /c python -u integrated_market_radar.py --loop-daemon --interval-seconds 60 --duration-minutes 0 > """ ^& logFile ^& """ 2>&1"
echo     ws.Run cmd, 0, False
echo End If
) > "%TARGET_VBS%"

echo.
echo ==============================================================================
echo [🚀 成功] 开机自启动配置完成！
echo 脚本已自动写入当前电脑启动目录:
echo   %TARGET_VBS%
echo 今后只要电脑开机，系统便会在后台静默拉起 60 秒极速轮询与 Discord 哨兵。
echo ==============================================================================
echo.
pause
