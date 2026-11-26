@echo off
chcp 65001 >nul
rem 填写 / 更新 DeepSeek API Key（保存到本目录 config.json，不随代码提交）
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set-api-key.ps1"
echo.
pause
