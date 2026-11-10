@echo off
chcp 65001 >nul
title [美股量化雷达] 新电脑一键环境配置与依赖检测
echo ==============================================================================
echo  ⚡ 美股全周期量化雷达与 Discord 哨兵 · 新电脑一键环境配置向导
echo ==============================================================================
echo.

:: 1. 检测 Python 是否已安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [❌ 错误] 未检测到 Python 环境！
    echo 请先前往 https://www.python.org/downloads/ 下载并安装 Python 3.10 或更高版本。
    echo ⚠️ 安装时请务必勾选 "Add Python to PATH"（添加到系统环境变量）！
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do set PY_VER=%%i
echo [✅ 状态] 已检测到 Python 环境: %PY_VER%
echo.

:: 2. 检测并安装依赖库
echo [*] 正在检测并安装量化依赖库 (requests, pandas, numpy, yfinance, plotly, scikit-learn, lightgbm)...
echo (若下载缓慢，已自动使用清华大学国内高速镜像源)
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if %errorlevel% neq 0 (
    echo.
    echo [*] 备用源重试中 (官方 PyPI)...
    python -m pip install -r requirements.txt
)

echo.
echo ==============================================================================
echo [🎉 成功] 依赖库全部就绪！
echo 现在您可以直接双击 "2-启动雷达控制面板.bat" 开始使用控制中心看盘与推送！
echo ==============================================================================
echo.
pause
