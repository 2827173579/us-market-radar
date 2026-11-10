Dim ws, fso, currentDir, cmd
Set ws = CreateObject("Wscript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

currentDir = fso.GetParentFolderName(Wscript.ScriptFullName)
ws.CurrentDirectory = currentDir

cmd = "cmd.exe /c python -u integrated_market_radar.py --loop-daemon --interval-seconds 60 --duration-minutes 0 > """ & currentDir & "\.radar_daemon.log"" 2>&1"
ws.Run cmd, 0, False

Wscript.Echo "✅ [美股量化雷达] 后台 60 秒轮询与 Discord 哨兵守护已成功在后台静默启动！" & vbCrLf & vbCrLf & "👉 本地看盘地址: http://127.0.0.1:8765" & vbCrLf & "👉 控制面板可随时双击 2-启动雷达控制面板.bat 打开管理。"
