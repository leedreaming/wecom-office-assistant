# 注销「登录 Windows 后自动启动办公助手」计划任务
# 需要：管理员 PowerShell
$ErrorActionPreference = 'Stop'
$taskName = 'WeComOfficeAssistant-Autostart'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Host '!! 请以管理员身份运行本脚本。'; exit 1 }

& schtasks.exe /Delete /TN $taskName /F | Out-Host
if ($LASTEXITCODE -eq 0) { Write-Host "已删除计划任务：$taskName" }
else { Write-Host "任务不存在或删除失败（exit $LASTEXITCODE）" }
