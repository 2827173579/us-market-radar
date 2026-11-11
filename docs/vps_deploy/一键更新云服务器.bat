@echo off
chcp 65001 >nul
setlocal EnableExtensions
title us-market-radar · 一键热更新云服务器 (VPS)
REM ==============================================================================
REM  本地 Windows -> Ubuntu VPS 代码热更新（tar 打包 + scp 上传 + ssh 解压重启）
REM  - 只同步代码/静态资源/数据缓存，不会覆盖服务器上的 .env（密钥安全）
REM  - 不会 kill 进程：通过 systemd restart 让服务优雅重启
REM  使用前请修改下方 5 个配置变量，并确保 Windows 已启用 OpenSSH 客户端
REM ==============================================================================

set "VPS_HOST=YOUR_VPS_IP"
set "VPS_USER=ubuntu"
set "VPS_PORT=22"
set "APP_DIR=/opt/us-market-radar"
set "SERVICE_NAME=us-market-radar"
REM 可选：私钥路径；留空则使用密码或默认密钥交互认证
set "SSH_KEY="

REM 本脚本位于 docs/vps_deploy/，仓库根目录 = %~dp0..\..\
set "LOCAL_DIR=%~dp0..\..\"
set "PKG=%TEMP%\us-market-radar-update.tar.gz"

echo ==============================================================================
echo  us-market-radar · VPS 热更新
echo ==============================================================================
if "%VPS_HOST%"=="YOUR_VPS_IP" (
    echo [x] 请先用记事本打开本脚本，把 VPS_HOST / VPS_USER / VPS_PORT / APP_DIR / SERVICE_NAME 改成你的服务器配置。
    pause
    exit /b 1
)

echo [1/5] 检查本机命令 ...
where ssh >nul 2>&1 || (echo [x] 未找到 ssh，请在"设置-应用-可选功能"安装 OpenSSH 客户端 & pause & exit /b 1)
where scp >nul 2>&1 || (echo [x] 未找到 scp & pause & exit /b 1)
where tar >nul 2>&1 || (echo [x] 未找到 tar（Windows 10 1803+ 自带）& pause & exit /b 1)

echo [2/5] 打包本地代码（排除 .git / .env / 虚拟环境 / 研究目录）...
pushd "%LOCAL_DIR%"
if exist "%PKG%" del /f /q "%PKG%"
tar -czf "%PKG%" --exclude=.git --exclude=.venv --exclude=venv --exclude=__pycache__ --exclude=.env --exclude=.pytest_cache --exclude=daily_research --exclude=intraday_scalper --exclude=video_frames --exclude=.radar_daemon.log --exclude=*.pyc --exclude=*.log .
if errorlevel 1 (
    echo [x] 打包失败，请检查当前目录是否为项目根目录。
    popd
    pause
    exit /b 1
)
popd
for %%F in ("%PKG%") do echo     [OK] 打包完成: %%~nxF  (%%~zF bytes)

echo [3/5] 上传到 VPS (%VPS_USER%@%VPS_HOST%:%VPS_PORT%) ...
if defined SSH_KEY (
    scp -P %VPS_PORT% -i "%SSH_KEY%" "%PKG%" %VPS_USER%@%VPS_HOST%:/tmp/radar_update.tar.gz
) else (
    scp -P %VPS_PORT% "%PKG%" %VPS_USER%@%VPS_HOST%:/tmp/radar_update.tar.gz
)
if errorlevel 1 (
    echo [x] scp 上传失败：请检查 IP / 用户名 / 端口 / SSH 密钥或密码。
    pause
    exit /b 1
)

echo [4/5] 远端解压并重启 systemd 服务 ...
if defined SSH_KEY (
    ssh -p %VPS_PORT% -i "%SSH_KEY%" -t %VPS_USER%@%VPS_HOST% "tar -xzf /tmp/radar_update.tar.gz -C %APP_DIR% && rm -f /tmp/radar_update.tar.gz && sudo systemctl restart %SERVICE_NAME% && sleep 3 && sudo systemctl --no-pager --lines=15 status %SERVICE_NAME%"
) else (
    ssh -p %VPS_PORT% -t %VPS_USER%@%VPS_HOST% "tar -xzf /tmp/radar_update.tar.gz -C %APP_DIR% && rm -f /tmp/radar_update.tar.gz && sudo systemctl restart %SERVICE_NAME% && sleep 3 && sudo systemctl --no-pager --lines=15 status %SERVICE_NAME%"
)
if errorlevel 1 (
    echo [x] 远端更新失败：请登录服务器查看 journalctl -u %SERVICE_NAME% -n 80
    pause
    exit /b 1
)

echo [5/5] 更新完成！
echo     实时日志: ssh -p %VPS_PORT% %VPS_USER%@%VPS_HOST% "journalctl -u %SERVICE_NAME% -f"
echo     如遇到 sudo 需要密码，会在此步骤交互提示输入。
echo ==============================================================================
pause
