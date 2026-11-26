# 重新下载当前 Python 版本可用的离线 wheel（Windows）
#
# 背景：仓库自带的 wheels/ 是按 CPython 3.12 / Windows x64 下载的。
#       如果你用其它 Python 版本，运行本脚本会在 wheels/ 重新下载一套匹配的轮子。
#
# 用法（在项目根目录）：
#   powershell -ExecutionPolicy Bypass -File scripts\download-wheels.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\download-wheels.ps1 -VenvDir C:\wecom-venv
param(
    [string]$VenvDir = ""
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$wheelDir = Join-Path $root 'wheels'
$req = Join-Path $root 'requirements.txt'

if (-not $VenvDir) {
    if (Test-Path -LiteralPath (Join-Path $root '.venv\Scripts\python.exe')) { $VenvDir = Join-Path $root '.venv' }
    elseif (Test-Path -LiteralPath 'C:\wecom-venv\Scripts\python.exe') { $VenvDir = 'C:\wecom-venv' }
}
$py = if ($VenvDir) { Join-Path $VenvDir 'Scripts\python.exe' } else { $null }
if (-not $py -or -not (Test-Path -LiteralPath $py)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { Write-Host '!! 找不到 Python，请先安装 Python 3.10+ 或先运行 setup.ps1'; exit 1 }
    $py = $cmd.Source
}
Write-Host "使用 Python：$py"
New-Item -ItemType Directory -Force -Path $wheelDir | Out-Null

& $py -m pip download --only-binary=:all: --dest $wheelDir -r $req
if ($LASTEXITCODE -ne 0) { Write-Host '!! 下载失败，请检查网络或 pip 源'; exit 1 }

Write-Host ''
Write-Host "完成：$wheelDir"
Get-ChildItem -LiteralPath $wheelDir -Filter '*.whl' | Select-Object Name, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 2) } } | Format-Table -AutoSize
