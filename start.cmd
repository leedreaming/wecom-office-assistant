@echo off
chcp 65001 >nul
rem 后台启动「企业微信办公助手」桥接（整个文件夹可以搬到 U 盘 / 任意目录）
rem 运行环境查找顺序：%WECOM_VENV% 环境变量 -> 项目内 .venv -> C:\wecom-venv（兼容旧版）
cd /d "%~dp0"

set "PYW="
if defined WECOM_VENV if exist "%WECOM_VENV%\Scripts\pythonw.exe" set "PYW=%WECOM_VENV%\Scripts\pythonw.exe"
if not defined PYW if exist ".venv\Scripts\pythonw.exe" set "PYW=.venv\Scripts\pythonw.exe"
if not defined PYW if exist "C:\wecom-venv\Scripts\pythonw.exe" set "PYW=C:\wecom-venv\Scripts\pythonw.exe"
if not defined PYW (
  echo [!] 还没有初始化运行环境，请先双击 setup.cmd
  if /i not "%~1"=="--autostart" pause
  exit /b 1
)

rem 单实例保护：bridge.pid 里的进程还活着就不再启动第二份
if exist "logs\bridge.pid" (
  for /f "usebackq delims=" %%p in ("logs\bridge.pid") do (
    tasklist /FI "PID eq %%p" 2>nul | find "%%p" >nul && (
      echo 已经在运行了（PID %%p）。日志：%~dp0logs\bridge.log
      exit /b 0
    )
  )
)

start "" "%PYW%" "%~dp0wecom_bridge.py"
echo 已后台启动（Python：%PYW%）
echo   日志：%~dp0logs\bridge.log
echo   成功标志：日志里出现「认证成功，机器人已在线」
echo   执行端：DSH 开着 = 真执行电脑任务；DSH 没开 = 自动用 DeepSeek API 纯聊天
echo            （API Key 用 set-api-key.cmd 填；自检用 check.cmd）
if /i not "%~1"=="--autostart" timeout /t 3 >nul
