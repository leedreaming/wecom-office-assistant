# 初始化「企业微信办公助手」运行环境（首次使用 / 换电脑后运行一次）
#
# 用法：
#   setup.cmd                         在项目内创建 .venv（默认）
#   setup.ps1 -VenvDir C:\wecom-venv  把运行环境建到本地盘（项目在慢速 U 盘时推荐）
#
# 依赖：Python 3.10+。会优先使用本机 py/python，其次尝试 DSH（DeepSeek Harness）自带 Python。
# 安装依赖时优先用项目内 wheels\ 离线安装，失败再走在线镜像（阿里云 / 清华 / 官方 PyPI）。
param(
    [string]$VenvDir = ""
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
Write-Host "=== 初始化运行环境：$root"

# ---------- 1) 找 Python（3.10+） ----------
function Test-PythonExe {
    param([string]$Exe, [string[]]$PreArgs = @())
    try {
        $ver = (& $Exe @PreArgs --version 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $ver -match 'Python 3\.(\d+)') {
            if ([int]$Matches[1] -ge 10) { return $ver }
        }
    } catch {
        return $null
    }
    return $null
}

function Find-Python {
    $cands = New-Object System.Collections.ArrayList
    if ($env:WECOM_PYTHON) { [void]$cands.Add(@{ Exe = $env:WECOM_PYTHON; Args = @(); Label = 'WECOM_PYTHON' }) }
    foreach ($name in @('py', 'python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $pre = @()
        if ($name -eq 'py') { $pre = @('-3') }
        [void]$cands.Add(@{ Exe = $cmd.Source; Args = $pre; Label = $name })
    }
    # 若本机装了 DeepSeek Harness，它自带 Python，可直接复用
    $dshRun = Join-Path $env:USERPROFILE '.dsh\dsh-runtimes'
    if (Test-Path -LiteralPath $dshRun) {
        Get-ChildItem -LiteralPath $dshRun -Recurse -Filter 'python.exe' -ErrorAction SilentlyContinue |
            ForEach-Object { [void]$cands.Add(@{ Exe = $_.FullName; Args = @(); Label = 'dsh-runtime' }) }
    }
    foreach ($c in $cands) {
        if ($c.Exe -and (Test-Path -LiteralPath $c.Exe)) {
            $ver = Test-PythonExe -Exe $c.Exe -PreArgs $c.Args
            if ($ver) {
                Write-Host ("使用 Python：{0}（{1}，{2}）" -f $c.Exe, $ver, $c.Label)
                return $c
            }
        }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host ""
    Write-Host "!! 没找到 Python 3.10+。请先安装 Python（https://www.python.org/downloads/，安装时勾选 Add to PATH），"
    Write-Host "   或安装并登录 DeepSeek Harness（它自带 Python），然后再运行 setup.cmd。"
    exit 1
}

# ---------- 2) 决定运行环境目录 ----------
if (-not $VenvDir) {
    if ($env:WECOM_VENV) { $VenvDir = $env:WECOM_VENV } else { $VenvDir = Join-Path $root '.venv' }
}
$venvPy = Join-Path $VenvDir 'Scripts\python.exe'
Write-Host "运行环境目录：$VenvDir"

$needSetup = $true
if (Test-Path -LiteralPath $venvPy) {
    & $venvPy -c "import aibot, aiohttp" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $needSetup = $false
        Write-Host "已存在可用运行环境，跳过安装。"
    }
}

if ($needSetup) {
    if (Test-Path -LiteralPath $VenvDir) {
        $old = $VenvDir + '_old_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
        Move-Item -LiteralPath $VenvDir -Destination $old -Force -ErrorAction SilentlyContinue
        Write-Host "旧环境已改名留底：$old"
    }
    Write-Host "创建虚拟环境…"
    & $py.Exe @($py.Args + @('-m', 'venv', $VenvDir))
    if (-not (Test-Path -LiteralPath $venvPy)) {
        Write-Host "!! 创建虚拟环境失败（目录不可写？）。"
        exit 1
    }

    # ---------- 3) 安装依赖：离线 wheels 优先，失败再走在线镜像 ----------
    $wheelDir = Join-Path $root 'wheels'
    $reqFile = Join-Path $root 'requirements.txt'
    $installed = $false

    # wheels 里的二进制轮子是按 CPython 3.12 / Windows x64 下载的；
    # 其它 Python 版本直接走在线安装（或运行 scripts\download-wheels.ps1 重新生成对应版本）。
    $pyMinor = 0
    try { $pyMinor = [int](& $venvPy -c "import sys; print(sys.version_info[1])") } catch { }
    $wheelsMatch = (Test-Path -LiteralPath $wheelDir) -and ($pyMinor -eq 12)

    if ($wheelsMatch) {
        $whl = Get-ChildItem -LiteralPath $wheelDir -Filter '*.whl' -ErrorAction SilentlyContinue
        if ($whl) {
            Write-Host "从 wheels\ 离线安装依赖（共 $($whl.Count) 个 wheel，Python 3.12 / Windows x64）…"
            & $venvPy -m pip install --no-cache-dir --no-index --find-links $wheelDir -r $reqFile
            & $venvPy -c "import aibot, aiohttp" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { $installed = $true }
            else { Write-Host "离线安装未完成，改走在线镜像…" }
        }
    } elseif (Test-Path -LiteralPath $wheelDir) {
        Write-Host "wheels\ 面向 Python 3.12（当前 Python 3.$pyMinor），跳过离线包，走在线安装。"
        Write-Host "（想离线安装：用 Python 3.12，或运行 scripts\download-wheels.ps1 重新下载当前版本）"
    }

    if (-not $installed) {
        $mirrors = @(
            'https://mirrors.aliyun.com/pypi/simple',
            'https://pypi.tuna.tsinghua.edu.cn/simple',
            'https://pypi.org/simple'
        )
        foreach ($m in $mirrors) {
            Write-Host "在线安装依赖（镜像：$m）…"
            & $venvPy -m pip install --no-cache-dir -i $m -r $reqFile
            & $venvPy -c "import aibot, aiohttp" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { $installed = $true; break }
        }
    }

    if (-not $installed) {
        Write-Host "!! 依赖安装失败。请检查网络后重试 setup.cmd；也可手动执行："
        Write-Host "   `"$venvPy`" -m pip install -r requirements.txt"
        exit 1
    }
}

# ---------- 4) 收尾检查 ----------
& $venvPy -c "import aibot, aiohttp; print('环境 OK')"
$ws = Join-Path $root 'workspace'
if (-not (Test-Path -LiteralPath $ws)) { New-Item -ItemType Directory -Force -Path $ws | Out-Null }
$cfg = Join-Path $root 'config.json'
$example = Join-Path $root 'config.example.json'
if (-not (Test-Path -LiteralPath $cfg) -and (Test-Path -LiteralPath $example)) {
    Copy-Item -LiteralPath $example -Destination $cfg
    Write-Host "已从 config.example.json 生成 config.json（记得填 bot_id / bot_secret）"
}
Write-Host ""
Write-Host "初始化完成（运行环境：$VenvDir）。下一步："
Write-Host "  1) 编辑 config.json，填 bot_id / bot_secret（企业微信管理后台获取）；"
Write-Host "  2) 想让机器人能操作电脑：先启动并登录 DeepSeek Harness；只想聊天可跳过；"
Write-Host "  3) 双击 check.cmd 自检；双击 start.cmd 启动；手机企业微信发消息。"
Write-Host "  4) 需要开机自启：见 docs/autostart.md。"
