@echo off
chcp 65001 >nul
rem 前台运行桥接（调试用：日志直接显示在窗口里，Ctrl+C 退出）
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

set "PY="
if defined WECOM_VENV if exist "%WECOM_VENV%\Scripts\python.exe" set "PY=%WECOM_VENV%\Scripts\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "C:\wecom-venv\Scripts\python.exe" set "PY=C:\wecom-venv\Scripts\python.exe"
if not defined PY (
  echo [!] 还没有初始化运行环境，请先双击 setup.cmd
  pause
  exit /b 1
)

"%PY%" "%~dp0wecom_bridge.py"
echo.
pause
