# 开机自启（Windows）

目标：登录 Windows 后自动把桥接拉起来，不用每次双击 `start.cmd`。

实现方式：**计划任务（ONLOGON）→ wscript 隐藏运行 `tools\autostart-hidden.vbs` → pythonw 启动 `wecom_bridge.py`**。
VBS 自带单实例保护（PID + WMI 校验命令行），不会重复开两份。

---

## 1. 注册

以**管理员身份**打开 PowerShell（开始菜单搜索 PowerShell → 右键「以管理员身份运行」）：

```powershell
cd <项目目录>
powershell -ExecutionPolicy Bypass -File tools\register-autostart.ps1
```

脚本会创建计划任务：

| 项目 | 值 |
|---|---|
| 任务名 | `WeComOfficeAssistant-Autostart` |
| 触发 | 当前用户登录时（ONLOGON） |
| 动作 | `wscript.exe //nologo <项目>\tools\autostart-hidden.vbs` |
| 权限 | LIMITED（当前用户交互式运行） |

## 2. 立即验证

```powershell
schtasks /Run /TN "WeComOfficeAssistant-Autostart"
Start-Sleep 5
Get-Content .\logs\autostart.log -Tail 20
Get-Content .\logs\bridge.log   -Tail 20
```

正常应看到：

```text
# autostart.log
=== autostart begin ===
script here=D:\path\to\project
pythonw=...\Scripts\pythonw.exe
started hidden: ...\pythonw.exe ...\wecom_bridge.py
=== autostart done ===
```

```text
# bridge.log
认证成功，机器人已在线 ✅（在手机企业微信里给它发消息即可）
DSH 已就绪 ✅
```

冷启动一般 **30~60 秒** 上线（慢机器/慢 U 盘更久）。期间手机发消息可能没回复，属正常。

## 3. 验证单实例（不会双开）

桥接已在运行时，再手动触发一次任务：

```powershell
schtasks /Run /TN "WeComOfficeAssistant-Autostart"
Get-Content .\logs\autostart.log -Tail 5
```

应看到：

```text
already running, pid=12345 - skip start (no duplicate)
```

这是设计行为：VBS 读 `logs\bridge.pid`，再用 WMI 校验该 PID 的命令行包含 `wecom_bridge`，
确认活着就退出。

## 4. 取消/删除

```powershell
# 方式 A：脚本（管理员）
powershell -ExecutionPolicy Bypass -File tools\unregister-autostart.ps1

# 方式 B：命令
schtasks /Delete /TN "WeComOfficeAssistant-Autostart" /F
```

> 取消自启只影响「登录自动启动」，不影响手动 `start.cmd`。

## 5. 换盘符 / 移动目录 / 换电脑

| 变更 | 要做的事 |
|---|---|
| 项目目录改名/移动 | 重新运行 `register-autostart.ps1`（任务里是绝对路径；VBS 自身按位置自适应） |
| U 盘盘符变化 | 同上，插好 U 盘后重新注册 |
| C 盘还原 / 换电脑 | `setup.cmd` → `start.cmd` → 重新注册自启 |
| 运行环境换位置 | VBS 会按 `WECOM_VENV` → `.venv` → `C:\wecom-venv` 顺序查找；改完注册一次 |

## 6. 自启失败排查

| autostart.log | 原因 | 处理 |
|---|---|---|
| `ERROR runtime not found` | 找不到 pythonw（环境未建/被还原） | 先 `setup.cmd`，再 `start.cmd` 验证 |
| `ERROR start failed: ...` | pythonw 路径/权限 | 检查 `.venv\Scripts\pythonw.exe` 是否存在、是否被杀软拦截 |
| 没有新行 | 任务没执行 | `schtasks /Query /TN ... /V /FO LIST` 看状态/上次结果；确认任务已启用 |
| `already running` | 已有实例 | 正常 |

额外检查：

```powershell
schtasks /Query /TN "WeComOfficeAssistant-Autostart" /V /FO LIST
Get-Content .\logs\autostart.log -Tail 50
```

## 7. 为什么不用「启动文件夹快捷方式」？

也可以，但计划任务更可控：

- 可设置仅在登录时触发、指定用户；
- 可以 `schtasks /Run` 手动验证；
- VBS 隐藏窗口 + 单实例保护，体验更接近「服务」。

如果只想简单：把 `start.cmd` 的快捷方式放进
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`，登录后会弹一个黑窗口并启动；

## 8. 设计说明（为什么没有看门狗）

- 本项目**故意不做**「定时检查并自动重启」：你执行 `stop.cmd` 后不希望它又被拉起来；
- 如果确实需要守护进程，可自行用「计划任务每 5 分钟运行 `start.cmd --autostart`」实现；
  `start.cmd` 有 PID 判重，重复运行不会双开。

## 9. 自启与休眠/睡眠

- Windows 睡眠时程序仍在内存，唤醒后长连接通常自动恢复；
- 若是「休眠/关机」，唤醒/开机后由计划任务负责重新启动；
- 笔记本合盖建议设置为「不睡眠」或「仅关闭屏幕」（电源选项），否则可能掉线。

## 10. Linux 服务器？用 systemd

云端常开设备不需要本页方案，见 [cloud-deploy.md](cloud-deploy.md)（`deploy/wecom-bridge.service`）。
