# 配置说明

配置文件：项目根目录 `config.json`（从 `config.example.json` 复制）。
**`config.json` 含密钥，已被 `.gitignore` 排除，禁止提交。**

- 读取编码：UTF-8（兼容 BOM）；
- 首次运行若缺少 `config.json`：自动从模板复制并提示填写；
- `bot_id` / `bot_secret` 为空时启动会退出（退出码 2）；
- **热加载**：只有 `api.*` 会在每条消息前按文件修改时间重新加载；`engine` / `dsh.*` / `reply.*` 改动需重启。

---

## 1. 最小配置

```json
{
  "bot_id": "你的 Bot ID",
  "bot_secret": "你的 Secret",
  "engine": "auto"
}
```

> API Key 也可以用环境变量 `DEEPSEEK_API_KEY` 提供，避免写进文件。

---

## 2. 顶层字段

| 字段 | 类型 | 默认 | 必填 | 说明 |
|---|---|---|---|---|
| `bot_id` | string | — | **是** | 企业微信智能机器人 Bot ID |
| `bot_secret` | string | — | **是** | 机器人 Secret（长连接身份校验） |
| `session_tag` | string | `""` | 否 | 会话命名空间。改它 = 所有聊天换新会话（排查/升级时可换） |
| `engine` | `auto` / `dsh` / `api` | `auto` | 否 | 执行端选择，非法值回落 `auto` |
| `dsh` | object | 见下 | 否 | DSH 执行端配置 |
| `api` | object | 见下 | 否 | DeepSeek API 兜底配置 |
| `reply` | object | 见下 | 否 | 回复/命令配置 |

---

## 3. `dsh.*`：DeepSeek Harness 执行端

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `mode` | `web` / `sdk` | `web` | `web`=驱动已运行的 DSH 桌面版（推荐）；`sdk`=自启独立 runtime（实验性，依赖未公开协议） |
| `web_url` | string | `auto` | DSH HTTP 地址；`auto`=自动发现（推荐） |
| `home` | string | `auto` | DSH 凭证目录；`auto`=`~/.dsh`（可用环境变量 `DSH_HOME`） |
| `cwd` | string | `workspace` | 遥控任务工作目录；相对路径按项目目录解析 |
| `timeout_ms` | number | `900000` | 单任务等待上限（15 分钟），下限 5000 |
| `probe_timeout_ms` | number | `4000` | 单次在线探测超时（毫秒），下限 500 |
| `probe_ttl_ok_s` | number | `60` | 在线状态缓存秒数 |
| `probe_ttl_fail_s` | number | `15` | 离线状态缓存秒数 |
| `ports_ttl_s` | number | `30` | 端口自动发现缓存秒数 |
| `auto_approve` | bool | `true` | 是否自动把 DSH 默认权限设为 `danger-full-access`（免审批，见安全说明） |

`sdk` 模式额外字段（一般不用）：

| 字段 | 默认 | 说明 |
|---|---|---|
| `node_executable` | 环境变量 `DSH_NODE_EXECUTABLE` | DSH 自带 node/Electron 可执行文件 |
| `install_dir` | 环境变量 `DSH_INSTALL_DIR` | 含 `lib/bin.js` 的 DSH 安装目录 |
| `profile` | `wechat` | DSH profile 名 |
| `provider` | `deepseek-account` | 模型提供方 |
| `model` | `deepseek-flash` | 模型名 |

> ⚠️ `dsh.auto_approve=true` 会调用 DSH 的 `settings/update` 修改全局默认权限（新会话免审批）。
> 不接受该风险请设为 `false`，任务会等待电脑上人工批准。

---

## 4. `api.*`：DeepSeek API 兜底（纯聊天）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 是否创建 API 执行端；`false`=完全禁用 |
| `base_url` | string | `https://api.deepseek.com` | OpenAI 兼容地址（自动拼 `/chat/completions`，也兼容其它兼容接口） |
| `api_key` | string | `""` | API Key（`sk-` 开头）；空则读环境变量 |
| `api_key_env` | string | `DEEPSEEK_API_KEY` | 环境变量名 |
| `model` | string | `deepseek-chat` | 模型名；含 `reasoner` 时自动省略 temperature |
| `temperature` | number | `1.0` | 采样温度 |
| `max_tokens` | number | `4096` | 单次最大输出 token |
| `timeout_ms` | number | `180000` | API 超时（毫秒） |
| `history_max_turns` | number | `20` | 保留上下文轮数（1 轮 = user + assistant） |
| `history_max_chars` | number | `50000` | 上下文总字符上限（双阈值裁剪） |
| `system_prompt` | string | 内置 | 覆盖系统提示；支持 `{mode_block}`、`{time}` 占位符 |
| `switch_note` | string | 内置 | DSH 降级到 API 时插入的提示（每种聊天一次） |

错误提示已中文化：401 → 提示填 Key；402 → 余额不足；429 → 请求太频繁；5xx → 服务不可用。

---

## 5. `reply.*`：回复与命令

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `thinking_text` | string | `已收到，正在处理…` | 收到消息后的第一帧 |
| `max_chunk` | number | `1800` | 最终回复分片字数（企业微信流式帧不宜过长） |
| `timeout_ms` | number | `900000` | 任务总超时（为空时用 `dsh.timeout_ms`） |
| `new_chat_words` | string[] | `/new`、`新对话`、`重新开始`、`清空上下文` | 开新对话口令 |
| `status_words` | string[] | `/status`、`/状态` | 查状态口令 |
| `help_words` | string[] | `/help`、`/帮助`、`/?` | 帮助口令 |
| `stop_words` | string[] | `/stop`、`/停止`、`停止任务`、`取消任务` | 停止口令（另有自然语言识别） |

> 命令匹配不区分大小写、忽略首尾标点；自然语言（语音转文字后）也能识别
> 「停下来吧」「现在什么状态」「告诉我一下版本号」等常用说法。

---

## 6. 环境变量

| 变量 | 作用 |
|---|---|
| `DEEPSEEK_API_KEY` | API Key（`api.api_key` 为空时使用） |
| `DSH_HOME` | DSH 凭证目录（默认 `~/.dsh`） |
| `WECOM_VENV` | 运行环境目录（`start.cmd`/`check.cmd` 优先使用；也可命令行指定） |
| `WECOM_PYTHON` | `setup.ps1` 优先使用的 Python 路径 |
| `WECOM_SELFTEST_DSH=1` | 运行自测时额外验证「本机 DSH 在线」 |
| `DSH_NODE_EXECUTABLE`、`DSH_INSTALL_DIR` | `dsh.mode=sdk` 时使用 |
| `PYTHONIOENCODING=utf-8` | 控制台/子进程编码（脚本已内置设置） |

---

## 7. 常见配置示例

### 7.1 个人电脑，DSH 常开（推荐）

```json
{
  "bot_id": "...",
  "bot_secret": "...",
  "engine": "auto",
  "dsh": { "auto_approve": true },
  "api": { "enabled": true, "api_key": "" }
}
```

配合环境变量 `DEEPSEEK_API_KEY`，DSH 临时没开也能聊天。

### 7.2 只允许真执行（不允许聊天兜底）

```json
{
  "engine": "dsh",
  "api": { "enabled": false }
}
```

DSH 不可用时手机端会收到明确报错，不会静默换成聊天。

### 7.3 云服务器 / 常开小主机（纯聊天）

```json
{
  "engine": "api",
  "dsh": { "mode": "web" },
  "api": {
    "enabled": true,
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_key": ""
  }
}
```

API Key 用环境变量注入；不要在该机器上同时跑 DSH。

### 7.4 安全敏感环境（人工审批）

```json
{
  "engine": "dsh",
  "dsh": { "auto_approve": false }
}
```

任务会在电脑上等待人工点「允许」；适合不放心远程全权限的场景。

### 7.5 项目放在慢速 U 盘

```json
{
  "dsh": { "cwd": "workspace" }
}
```

运行环境放到本地盘（`setup-local-venv.cmd` 或 `WECOM_VENV=C:\wecom-venv`），
项目本体仍可放 U 盘；日志写入已异步化，不会卡消息处理。

---

## 8. 修改配置后

| 改了什么 | 是否要重启 |
|---|---|
| `api.api_key` / `api.model` 等 `api.*` | 不用（下一条消息自动热加载） |
| `bot_id` / `bot_secret` | 要重启 |
| `engine` | 要重启 |
| `dsh.*` / `reply.*` | 要重启 |

重启方式：`stop.cmd` → `start.cmd`（或前台 `python wecom_bridge.py`）。
