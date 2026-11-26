@echo off
chcp 65001 >nul
rem 首次使用 / 换电脑后运行一次：初始化运行环境（默认在项目内 .venv）
rem 如果项目在 U 盘、读写很慢，建议把环境建到本地盘：
rem   双击 setup-local-venv.cmd   （等价于 setup.ps1 -VenvDir C:\wecom-venv）
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
echo.
pause
