Dim ws, fso, wmi, colProcesses, proc, isRunning
Set ws = CreateObject("Wscript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
Set wmi = GetObject("winmgmts:\\.\root\cimv2")

' 检查是否已有运行中的 integrated_market_radar.py 轮询进程，避免重复启动
isRunning = False
Set colProcesses = wmi.ExecQuery("Select CommandLine from Win32_Process Where Name = 'python.exe' or Name = 'pythonw.exe'")
For Each proc in colProcesses
    If Not IsNull(proc.CommandLine) Then
        If InStr(proc.CommandLine, "integrated_market_radar.py") > 0 And InStr(proc.CommandLine, "--loop-daemon") > 0 Then
            isRunning = True
            Exit For
        End If
    End If
Next

If Not isRunning Then
    Dim radarDir, logFile, cmd
    radarDir = fso.GetParentFolderName(Wscript.ScriptFullName)
    logFile = radarDir & "\.radar_daemon.log"
    ws.CurrentDirectory = radarDir
    cmd = "cmd.exe /c python -u integrated_market_radar.py --loop-daemon --interval-seconds 60 --duration-minutes 0 > """ & logFile & """ 2>&1"
    ws.Run cmd, 0, False
End If
