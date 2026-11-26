# 日常运维（Operations）

面向已经跑起来的用户：每天怎么检查、日志怎么管、怎么备份恢复、怎么升级。

---

## 1. 每次上机 30 秒检查

```text
1) 双击 check.cmd         → 至少一个执行端 ✅
2) 看 logs\bridge.log 尾部 → 是否「认证成功，机器人已在线」
3) 手机发 /status          → 有回复即链路正常
```

## 2. 启动 / 停止 / 重启

| 操作 | Windows | 说明 |
|---|---|---|
| 启动（后台） | `start.cmd` | 无窗口运行；日志写 `logs\bridge.log` |
| 启动（前台调试） | `run-foreground.cmd` | 日志直接显示在窗口；Ctrl+C 退出 |
| 停止 | `stop.cmd` | 按 PID 杀进程树；**拔 U 盘/关机前先执行** |
| 重启 | `stop.cmd` → `start.cmd` | 改 `bot_id` / `engine` / `dsh.*` 后需要重启 |
| 查看进程 | `Get-Content logs\bridge.pid` + 任务管理器 | venv `pythonw` 可能有「启动桩 + 子进程」两个 PID，属同一实例 |

> API Key（`api.*`）修改**不需要重启**：下一条消息自动热加载。

## 3. 日志管理

| 文件 | 内容 | 建议 |
|---|---|---|
| `logs\bridge.log` | 运行日志（约 120 字/消息摘要，含 `chat=` 标识） | 每周检查；分享前脱敏；过大时停止后改名/清空 |
| `logs\autostart.log` | 开机自启过程 | 只在自启异常时看 |
| `logs\bridge.pid` | 当前 PID | 不要手改 |

清理示例（先停止）：

```powershell
stop.cmd
Rename-Item .\logs\bridge.log ("bridge_" + (Get-Date -Format yyyyMMdd_HHmmss) + ".log")
# 或直接清空：Clear-Content .\logs\bridge.log
start.cmd
```

> 日志会记录消息前 120 字与 `chat=` 用户标识；**不要直接把原始日志贴到公开 Issue**。

## 4. 会话与上下文

| 操作 | 说明 |
|---|---|
| 同一聊天 | 上下文持续累积（DSH 会话 / API 历史） |
| `/new` 或「新对话」 | 换新会话，清空上下文 |
| 换工作目录 | `dsh.cwd` 改动后会话 ID 自动变化（旧上下文不再使用） |
| 上下文越来越长 | API 模式自动按轮数/字符裁剪；DSH 模式由 DSH 管理 |

## 5. 备份与恢复

**要备份：**

- `config.json`（**加密/离线保存**，含 Secret/Key）
- `workspace\` 下重要产出
- 自己改过的代码/文档（建议用 Git）

**不要备份：** `.venv\`、`logs\`、`__pycache__\`（可重建）。

**恢复顺序：**

```text
1) 恢复项目文件与 config.json
2) setup.cmd 重建环境（换电脑/C 盘还原）
3) check.cmd → start.cmd
4) 重新注册开机自启（换盘符/换机器时）
```

## 6. 密钥与权限运维

| 操作 | 步骤 |
|---|---|
| 轮换机器人 Secret | 企业微信后台重置 → 更新 `config.json` → 重启 |
| 轮换 DeepSeek Key | 平台删除旧 Key、建新 Key → `set-api-key.cmd`（无需重启） |
| 收紧 visible scope | 企业微信后台把机器人可见范围缩到必要成员 |
| 关闭自动审批 | `dsh.auto_approve=false` → 重启；任务需电脑上人工批准 |

## 7. 升级

```powershell
stop.cmd
git pull
.venv\Scripts\python.exe -m pip install -r requirements.txt
start.cmd
check.cmd
```

- 升级前备份 `config.json`；
- 跨大版本看 `CHANGELOG.md` 的「破坏性变更」；
- 升级后手机发 `/status` 确认版本。

## 8. 云端/本机切换（常开设备）

```text
去电脑前要真执行：
  云端：sudo systemctl stop wecom-bridge
  本地：start.cmd

离开电脑：
  本地：stop.cmd
  云端：sudo systemctl start wecom-bridge
```

> 同一机器人 ID 只允许一条长连接，**不能同时开**。

## 9. 卸载/清理

```powershell
stop.cmd
schtasks /Delete /TN "WeComOfficeAssistant-Autostart" /F
# 删除项目目录（注意先备份 config.json / workspace）
# 企业微信后台删除机器人；DeepSeek 平台删除 API Key
```

## 10. 值班巡检建议（可选）

如果你把它当生产工具用，建议：

- 每天看一次 `/status` 或日志是否有错误；
- 每周检查磁盘（日志/workspace）与依赖版本；
- 变更（改配置、升级、换机器）记录到自己的运维日志；
- 备份 `config.json` 与 workspace 到独立介质。
