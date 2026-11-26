# 注册「登录 Windows 后自动启动办公助手」计划任务
# 需要：以管理员身份运行（右键 PowerShell -> 以管理员身份运行）
# 用法：powershell -ExecutionPolicy Bypass -File tools\register-autostart.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$vbs = Join-Path $root 'tools\autostart-hidden.vbs'
$taskName = 'WeComOfficeAssistant-Autostart'

if (-not (Test-Path -LiteralPath $vbs)) { Write-Host "!! 找不到 $vbs"; exit 1 }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '!! 请以管理员身份运行本脚本（注册计划任务需要）。'
    exit 1
}

# ONLOGON：当前用户登录时触发；wscript 隐藏运行 VBS；VBS 内部会做单实例保护
$action = "wscript.exe //nologo `"$vbs`""
& schtasks.exe /Create /TN $taskName /SC ONLOGON /RL LIMITED /F /TR $action | Out-Host
if ($LASTEXITCODE -ne 0) { Write-Host "!! 注册失败（exit $LASTEXITCODE）"; exit 1 }

Write-Host ''
Write-Host "已注册计划任务：$taskName"
Write-Host "动作：$action"
Write-Host ''
& schtasks.exe /Query /TN $taskName /V /FO LIST | Out-Host
Write-Host ''
Write-Host '提示：'
Write-Host '  · 立即测试：schtasks /Run /TN "' + $taskName + '"'
Write-Host '  · 查看自启日志：项目\logs\autostart.log'
Write-Host '  · 换盘符/移动项目后需要重新运行本脚本'
Write-Host '  · 取消自启：tools\unregister-autostart.ps1'
