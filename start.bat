@echo off
chcp 65001 >nul
title 抖音弹幕悬浮窗
cd /d "%~dp0"
python --version >nul 2>&1
if errorlevel 1 (
    echo 没找到 Python。正在打开下载页面……
    echo 安装时一定要勾选 "Add python.exe to PATH"，装好后再双击 start.bat。
    start https://www.python.org/downloads/
    pause
    exit /b
)
python -c "import playwright, pystray, PIL" >nul 2>&1
if errorlevel 1 (
    echo 第一次运行，正在安装需要的组件，请稍等……
    python -m pip install -q playwright pystray pillow
    if errorlevel 1 (
        echo 安装失败，请检查网络后重试。
        pause
        exit /b
    )
)
if not exist "edge_profile" (
    echo 还没有登录过抖音。如果你的直播设了「部分人可见」，请先关掉本窗口，双击 login.bat 登录。
    echo 10 秒后继续启动……
    timeout /t 10 >nul
)
rem 用 pythonw 启动：不会有黑窗口，程序在右下角托盘里
start "" pythonw danmaku_overlay.py
exit /b
