@echo off
chcp 65001 >nul
title 登录抖音（只需要做一次）
cd /d "%~dp0"
python -c "import playwright, pystray, PIL" >nul 2>&1
if errorlevel 1 (
    echo 正在安装需要的组件，请稍等……
    python -m pip install -q playwright pystray pillow
)
echo 如果弹幕悬浮窗正在运行，请先在右下角托盘图标上右键 - 退出。
echo.
python danmaku_overlay.py --login
echo.
pause
