@echo off
chcp 65001 >nul
rem 停止「企业微信办公助手」桥接
cd /d "%~dp0"
set FOUND=0
if exist "logs\bridge.pid" (
  for /f "usebackq delims=" %%p in ("logs\bridge.pid") do (
    taskkill /PID %%p /T /F >nul 2>&1
    if not errorlevel 1 (
      echo 已停止（PID %%p）
      set FOUND=1
    )
  )
  del /q "logs\bridge.pid" >nul 2>&1
)
if "%FOUND%"=="0" echo 没找到正在运行的助手（可能已经退出）。
timeout /t 2 >nul
