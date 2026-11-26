@echo off
chcp 65001 >nul
rem 运行环境建到本地盘（默认 C:\wecom-venv），适合项目放在读写很慢的 U 盘/移动硬盘时使用
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -VenvDir "C:\wecom-venv"
echo.
pause
