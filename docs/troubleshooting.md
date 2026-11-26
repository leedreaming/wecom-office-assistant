# 故障排查（Troubleshooting）

> 排障第一原则：**先看日志**。
> - `logs\bridge.log`：启动/执行端/回复/错误
> - `logs\autostart.log`：开机自启
> - 双击 `check.cmd`：双执行端自检
>
> 分享日志前请**脱敏**（删掉机器人 Secret、API Key、聊天内容、文件名、`chat=` 后的用户标识）。

---

## 1. 快速定位表

| 现象 | 最可能原因 | 先做什么 |
|---|---|---|
| 手机发消息没反应 | 桥接没启动 / 未认证 / 双开互踢 | 看 `bridge.log` 最后一行是否「认证成功」；`check.cmd`；确认只有一份实例 |
| 一直「已收到，正在处理…」 | 执行端慢 / DSH 卡审批 / 任务超时 | `/stop`；看日志；检查 DSH 是否弹审批 |
| 报「找不到可用的桌面版 DSH」 | DSH 没开 / 刚重启 / 端口变了 | 打开 DSH，等十几秒重发；看 `--check-all` |
| 报「还没配置 DeepSeek API Key」 | 没 Key 且 DSH 不可用 | `set-api-key.cmd` 或环境变量 |
| 长任务超 10 分钟后结果没发出 | 846608 流式过期 | 确认主动推送权限；代码应已自动改主动推送 |
| 主动推送/完成通知收不到 | 机器人无主动推送权限 | 企业微信后台检查权限 |
| 报「等待批准 / input-required」 | DSH 新会话审批策略是 ask | 电脑上点允许；或 `dsh.auto_approve=true` |
| 启动脚本乱码/语法错误 | `.ps1` 无 BOM / `.cmd` 是 LF / `.vbs` 含中文 | 见第 7 节，按编码规范改 |
| 开机后机器人不在线 | 环境被还原 / 计划任务失败 | `setup.cmd` → `start.cmd` → 查 `autostart.log` |
| 文件发不到手机 | 文件 >50MB / 路径不对 / 主动推送权限 | 看日志里的上传错误 |

---

## 2. 手机侧完全没反应

```text
① 电脑上是否有进程？任务管理器找 pythonw.exe / python.exe（命令行含 wecom_bridge）
② logs\bridge.pid 是否存在？文件里 PID 是否还活着
③ logs\bridge.log 最后 30 行
④ 是否同时开了两份桥接（本机 + 云主机）？
⑤ 手机企业微信里机器人是否在「可见范围」内？
```

常见日志与处理：

| 日志 | 含义 | 处理 |
|---|---|---|
| `认证成功，机器人已在线 ✅` | 正常 | 若仍无回复，看后续执行端日志 |
| `认证失败` / `401` / `unauthorized` | Bot ID/Secret 错、被重置、复制带空格 | 重新复制凭证；重置后更新 `config.json` 并重启 |
| 反复 `连接断开 / 重连` | 网络问题或同 ID 双开 | 只保留一份桥接；检查出站 443 |
| 没有任何新日志 | 进程根本没起来 | `start.cmd`；看是否提示「先运行 setup.cmd」 |

> 一个机器人 ID **只允许一条长连接**：本地与云端同时开 = 互相踢，手机端表现为时好时坏。

---

## 3. 执行端报错

### 3.1 `找不到可用的桌面版 DSH` / `DSH API session ... 401`

| 原因 | 处理 |
|---|---|
| DSH 没启动/没登录 | 打开 DSH 并登录，等十几秒 |
| DSH 刚重启，端口变了 | 等十几秒；桥接会自动重新发现端口（也可 `--check-all` 触发） |
| DSH 重启后 secret 轮换，旧 cookie 401 | 桥接会清缓存重读 `~/.dsh/.credentials.yaml`；仍失败就重启桥接 |
| 凭证文件格式变化（DSH 升级） | 升级兼容问题：先用 `engine=api` 兜底，反馈 Issue（附 DSH 版本） |
| 机器上装了多个 DSH / 进程名不同 | 在 `config.json` 配 `dsh.web_url` 指定地址（`--check-all` 会显示探测地址） |

### 3.2 `等待超时（900s）` 但 DSH 明明在跑

- 很可能任务卡在 **DSH 审批**（`input-required`）：桥接会识别并自动取消、回中文提示；
- 处理：在电脑上点「允许」，或把 `dsh.auto_approve` 设为 `true`（会修改 DSH 默认权限为
  `danger-full-access`，请确认接受风险）；
- 也可能是 DSH 在跑超长任务：`reply.timeout_ms` 默认 15 分钟，可调大。

### 3.3 回复「电脑端 DSH 没开，本条由 DeepSeek API 直接回复」

这是 `auto` 模式的正常降级：

- 想真执行 → 打开 DSH 并等十几秒；
- 不想看到降级 → 把 `engine` 设为 `dsh`（DSH 不可用会明确报错，而不是聊天）。

### 3.4 DS H 在线却「瞎答」（答非所问）

历史 bug：DSH 繁忙时探活超时被误判离线 → 消息交给 API 聊天模型，它会「猜」着回答。
现已通过「在线缓存 + 失败重试一次 + 失败原因写 `/status`」缓解。
遇到时：发 `/status` 看执行端与 DSH 地址；必要时重启桥接。

---

## 4. 长任务与消息

### 4.1 超过 10 分钟的结果

企业微信流式消息 10 分钟过期（`errcode 846608`）。代码已处理：

- 每 45 秒刷新「还在处理中」；
- ≥9.5 分钟或遇到 846608 → 结果改走 `send_message` **主动推送**；
- 任务 ≥60 秒完成 → 额外推送「任务完成」通知。

若仍收不到：检查机器人**主动推送权限**；看日志是否有 `send_message` 报错。

### 4.2 同一个聊天里任务排队

- 同一会话任务串行执行；上一条未完成时新消息会提示「已排队」；
- `/stop` 不排队，可立即中断当前任务（长任务占锁也能停）；
- 多用户/多会话共用一个 DSH 时，DSH 侧也是全局串行（一次跑一个任务）。

### 4.3 图片/文件消息没有响应

当前只处理**文字 / 语音 / 图文混排中的文字**；图片、文件消息会提示暂不支持。
语音依赖企业微信的语音转文字结果，识别为空时会让用户重发。

---

## 5. 文件回传失败

| 检查 | 说明 |
|---|---|
| 是否 `engine=dsh` | API 模式**故意禁止**发文件（安全设计），会剥离 `[[SEND_FILE]]` 标记 |
| 路径是否存在 | 模型输出的路径必须是电脑上真实存在的绝对路径 |
| 文件大小 | ≤50MB（分片 512KB，md5 校验） |
| 日志 | 搜 `upload_media` / `media_id` / `发送文件`；401/403 多为权限或素材接口变化 |
| SDK 版本 | 文件上传使用了 SDK 的部分未公开能力，SDK 升级可能破坏（见 Issue） |

---

## 6. 开机自启失败

```powershell
# 1) 任务是否存在/启用
schtasks /Query /TN "WeComOfficeAssistant-Autostart" /V /FO LIST

# 2) 手动触发
schtasks /Run /TN "WeComOfficeAssistant-Autostart"

# 3) 看自启日志
Get-Content .\logs\autostart.log -Tail 30
```

| autostart.log | 原因 | 处理 |
|---|---|---|
| `ERROR runtime not found` | 运行环境不存在（C 盘还原/换电脑） | 运行 `setup.cmd`，再 `start.cmd` |
| `ERROR start failed` | pythonw 路径/权限问题 | 检查 `.venv\Scripts\pythonw.exe` 是否存在 |
| 没有新日志 | 计划任务没执行 | 任务是否启用、触发器是否 ONLOGON、路径是否指向当前项目 |
| `already running ... skip` | 已有实例（正常单实例保护） | 无需处理 |

> 换盘符/移动目录后：重新运行 `tools\register-autostart.ps1`。

---

## 7. 脚本编码/乱码（Windows 特有）

| 文件类型 | 正确做法 | 错误症状 |
|---|---|---|
| `.ps1` | UTF-8 **with BOM** + CRLF | 中文乱码、`Unexpected token`、生成乱码目录名 |
| `.cmd` | CRLF | `The system cannot find the path specified.`、`'i' is not recognized...` |
| `.vbs` | **纯 ASCII** + CRLF | cscript 报「未结束的字符串常量」 |
| `.py` / `.json` / `.md` | UTF-8 无 BOM | JSON 解析失败、中文乱码 |
| 控制台 | `chcp 65001` + `PYTHONIOENCODING=utf-8` | 日志里 emoji/中文乱码 |

`config.json` 支持 BOM（代码用 `utf-8-sig` 读取），但建议保存为 UTF-8 无 BOM。

---

## 8. 性能 / U 盘 / 慢磁盘

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动很慢、回复延迟 | 项目/日志在慢速 U 盘；杀毒软件扫描 | 把运行环境放本地盘（`setup-local-venv.cmd`）；项目代码可留 U 盘 |
| U 盘写入卡顿导致日志延迟 | 慢盘 + 并发 I/O | 日志已异步化；避免同时大量读写 U 盘 |
| 长期运行日志过大 | 无自动轮转 | 定期 `stop.cmd` 后改名/清空 `logs\bridge.log` |
| 回复慢 | API 模式非流式（`stream:false`） | 长回答要等完整返回，属正常 |

---

## 9. 安全相关异常

| 现象 | 处理 |
|---|---|
| 发现 `config.json` 被提交/外发 | 立即重置机器人 Secret、删除 DeepSeek Key 重建；清理 Git 历史 |
| 手机端收到「API 模式不能发文件」 | 正常安全设计（防提示注入外发本机文件） |
| 不确定谁给机器人发过消息 | 日志含 `chat=` 标识（已建议脱敏）；缩小机器人可见范围 |
| 想让任务必须人工批准 | `dsh.auto_approve=false`（任务会等电脑上点允许） |

---

## 10. 提 Issue 时请附（全部脱敏）

1. 项目版本（`/status` 或 `wecom_bridge.py` 顶部 `BRIDGE_VERSION`）；
2. Windows 版本、Python 版本、是否 U 盘运行；
3. DSH 版本（若相关）；
4. `--check-all` 输出（脱敏）；
5. `logs\bridge.log` 相关片段（删掉 Secret/Key/聊天内容/文件名/chat 标识）；
6. 复现步骤与预期行为。

> 不要在 Issue 里贴完整 `config.json`、Secret、API Key、手机号、聊天记录原文。
