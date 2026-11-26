# 填写 / 更新 DeepSeek API Key 到 config.json（只保存在本目录，不随代码提交）
# 用法：双击 set-api-key.cmd（或右键用 PowerShell 运行本文件）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$cfgPath = Join-Path $root 'config.json'
$examplePath = Join-Path $root 'config.example.json'

Write-Host '=============================================='
Write-Host '  填写 DeepSeek API Key（用于 DSH 没开时兜底回复）'
Write-Host '=============================================='
Write-Host '怎么拿 Key：浏览器打开 https://platform.deepseek.com/api_keys'
Write-Host '           登录后点「创建 API Key」，复制以 sk- 开头的那一串。'
Write-Host '           也可以不写进文件，改用环境变量 DEEPSEEK_API_KEY。'
Write-Host ''

if (-not (Test-Path $cfgPath)) {
  if (Test-Path $examplePath) {
    Copy-Item $examplePath $cfgPath
    Write-Host "已从模板生成 config.json（Bot 凭证需要另填）"
  } else {
    Write-Host "!! 找不到 config.json，也不是首次运行，无法继续。"
    exit 1
  }
}

$secure = Read-Host '把 Key 粘贴到这里（输入不回显，回车确认）' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
$plain = ($plain -as [string]).Trim()
if (-not $plain) {
  Write-Host '没有输入内容，已取消（config.json 没改）。'
  exit 1
}
if ($plain -notlike 'sk-*') {
  Write-Host "提示：这个 Key 不是以 sk- 开头，请确认没粘错（DeepSeek 官方 Key 以 sk- 开头）。"
  $go = Read-Host '仍然保存？输入 y 继续，其它键取消'
  if ($go -ne 'y') { Write-Host '已取消。'; exit 1 }
}

$cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $cfg.PSObject.Properties['api']) {
  $cfg | Add-Member -NotePropertyName api -NotePropertyValue ([pscustomobject]@{}) -Force
}
if ($cfg.api.PSObject.Properties['api_key']) {
  $cfg.api.api_key = $plain
} else {
  $cfg.api | Add-Member -NotePropertyName api_key -NotePropertyValue $plain -Force
}
# 先备份旧配置（写坏/断电还能找回）
Copy-Item $cfgPath ($cfgPath + '.bak') -Force

$json = $cfg | ConvertTo-Json -Depth 20
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($cfgPath, $json, $utf8NoBom)

Write-Host ''
Write-Host ("已保存到 config.json（Key 只显示头尾：{0}…{1}）" -f $plain.Substring(0, [Math]::Min(5, $plain.Length)), $plain.Substring([Math]::Max(0, $plain.Length - 4)))
Write-Host '备份：config.json.bak'
Write-Host '如果机器人正在后台运行：不用重启，下次发消息会自动加载新 Key。'
Write-Host '验证一下：双击 check.cmd（会分别检查 DSH 和 DeepSeek API）。'
exit 0
