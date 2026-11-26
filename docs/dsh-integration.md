# 与 DeepSeek Harness（DSH）集成

本项目「真操作电脑」的能力来自本机的 **DeepSeek Harness 桌面版（DSH）**。
桥接不启动、不修改、不分发 DSH，只是通过它的本机接口「借用」其执行能力。

> DSH 是独立软件，需自行安装并登录；本仓库不包含其任何源码。
> 集成方式基于对 DSH 本机接口的观察，**非官方支持**，DSH 升级后可能变化。

---

## 1. 两种集成模式

`config.json → dsh.mode`：

| 模式 | 条件 | 原理 | 稳定性 |
|---|---|---|---|
| `web`（默认，推荐） | DSH 桌面版已运行并登录 | 发现本机监听端口 → 用本地凭证生成本地鉴权 → POST `/api/<rpc>` | 较好（接口相对稳定） |
| `sdk`（实验性） | 需要 node/Electron 路径与 DSH 安装目录 | `node dsh_worker.js` spawn DSH SDK stdio runtime，JSON-RPC 通信 | 较差（未公开协议，随 DSH 版本失效） |

普通用户只需 `web` 模式。

---

## 2. web 模式做了什么

### 2.1 端口自动发现

DSH 每次启动监听**随机端口**（实测出现过 `57352 / 65151 / 63232 / 57312` 等），所以桥接：

1. 用 `tasklist` 找到 `DeepSeek Harness.exe` 的 PID；
2. 用 `netstat -ano` 找到该 PID 在 `127.0.0.1` 上的 LISTENING 端口；
3. 缓存 30 秒（`dsh.ports_ttl_s`）；
4. 按顺序尝试：上次成功地址 → 配置的 `dsh.web_url` → 自动发现端口 → 默认 `57352`。

> 不需要手工填端口；想强制指定地址时把 `dsh.web_url` 设为 `http://127.0.0.1:<端口>`。

### 2.2 本地鉴权

DSH 桌面版在本机做了会话鉴权。桥接读取 `~/.dsh/.credentials.yaml`（可用 `DSH_HOME` 改目录）里的
browser-session 密钥，按它的规则为 `127.0.0.1:<端口>` 生成本地 cookie。
**凭证只在内存中使用，不打印、不落盘、不上传。** DSH 重启轮换凭证时，桥接遇到 401 会重新读取。

### 2.3 用到的本机接口（RPC）

| 方法 | 用途 |
|---|---|
| `session/modelCatalog` | 探活 + 验证鉴权（不关心返回内容） |
| `session/state` | 查询会话是否存在 / 取游标 |
| `session/create` | 创建会话（工作目录 `dsh.cwd`） |
| `session/prompt` | 下发任务（文本） |
| `session/wait` | 轮询等待结果（分片返回，超时上限约 30 秒/次） |
| `session/cancel` | `/stop` 中断任务 |
| `settings/update` | `auto_approve=true` 时把默认权限设为 `danger-full-access` |

### 2.4 超时与降级

| 场景 | 行为 |
|---|---|
| 探测超时/连接失败/401 | 视为「DSH 不可用」→ `auto` 模式改用 API 聊天兜底 |
| 任务已受理但执行失败 | **不重试、不降级**（避免重复执行） |
| 任务已受理但等待超时 | 不重试；提示可能还在电脑上跑 |
| `session/wait` 返回 `input-required` | 说明在等 DSH 审批：自动 cancel + 中文提示（或提前 `auto_approve`） |
| `session/wait` 偶发空值 | 继续等待，不误判超时 |

---

## 3. 权限与审批（重要）

DSH 默认会对敏感操作弹审批。无人值守的手机远程场景没人点「允许」，任务会卡住。

`dsh.auto_approve=true`（默认）时，桥接会调用 DSH 设置接口把**默认权限预设**改为
`danger-full-access`（新会话免审批）。这意味着：

- ✅ 手机发任务可直接执行，体验顺畅；
- ⚠️ 远程任务拥有你电脑的完整权限（读写文件、运行命令）；
- 关闭方式：`"auto_approve": false`，任务会等待电脑上人工批准；
- 该修改对**新会话**生效；桥接使用独立会话标签，避免旧会话策略残留。

---

## 4. sdk 模式（实验性，默认不用）

适合「不想开桌面版、只有 DSH 独立 runtime」的场景：

1. 配置 `dsh.mode = "sdk"`；
2. 设置环境变量（或写入 `dsh.*`）：
   - `DSH_NODE_EXECUTABLE`：DSH 自带的 node/Electron 可执行文件；
   - `DSH_INSTALL_DIR`：含 `lib/bin.js` 的 DSH 安装目录；
3. `dsh_worker.js` 会 spawn `dsh <profile>` 的 SDK JSON-RPC runtime（NDJSON over stdio），
   协议细节见 `vendor/dsh-bridge.js` 头部注释（验证基线 DSH `0.2.2-rc.5`）。

> ⚠️ 该协议未公开，DSH 升级很可能失效；优先使用 `web` 模式。

---

## 5. 兼容性

| 项目 | 说明 |
|---|---|
| 验证环境 | Windows 10/11 + DSH 桌面版（2026-11 版本） |
| 端口 | 自动发现，无需固定 |
| 凭证文件格式 | 依赖 `~/.dsh/.credentials.yaml` 的字段；若 DSH 改格式可能失效 |
| DSH 升级 | web 模式影响较小；sdk 模式影响大 |
| 多会话 | 桥接一次只跑一个 DSH 任务（全局串行），多聊天排队 |

遇到兼容问题：先用 `engine=api` 保证聊天可用，再提交 Issue（附 DSH 版本与日志）。

---

## 6. 安全提示

- 桥接读取 DSH 本地凭证 = 拥有与 DSH 相同的本机权限；
- `danger-full-access` 等于远程全权限，请只给可信成员使用机器人；
- 不要把 `~/.dsh/.credentials.yaml` 或 `config.json` 发给他人；
- 想「必须人工批准」：`auto_approve=false`。
