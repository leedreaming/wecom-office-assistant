@echo off
chcp 65001 >nul
rem 双击自检：检查「DSH 是否在线」和「DeepSeek API 是否可用」
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

set "PY="
if defined WECOM_VENV if exist "%WECOM_VENV%\Scripts\python.exe" set "PY=%WECOM_VENV%\Scripts\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "C:\wecom-venv\Scripts\python.exe" set "PY=C:\wecom-venv\Scripts\python.exe"
if not defined PY (
  echo [!] 还没初始化运行环境，请先双击 setup.cmd
  pause
  exit /b 1
)

"%PY%" "%~dp0wecom_bridge.py" --check-all
echo.
echo （检查结果同时写入 logs\bridge.log）
pause
