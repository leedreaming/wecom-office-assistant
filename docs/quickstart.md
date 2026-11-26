# 快速开始（Windows，约 15 分钟）

> 目标：手机企业微信发一句话，电脑上的 DSH 真正执行并把结果发回手机。
> 完整流程与原理见 [workflow.md](workflow.md) / [architecture.md](architecture.md)。

## 准备清单

- [ ] Windows 10/11 电脑，能安装软件
- [ ] Python 3.10+（[python.org](https://www.python.org/downloads/)，安装时勾选 **Add python.exe to PATH**）
- [ ] 企业微信管理员权限（创建智能机器人）
- [ ] DeepSeek Harness（DSH）桌面版已安装并登录（想让它「操作电脑」才需要）
- [ ] （可选）DeepSeek API Key：<https://platform.deepseek.com/api_keys>

---

## 第 1 步：创建企业微信机器人，拿到 Bot ID / Secret

1. 浏览器打开 <https://work.weixin.qq.com/>，登录管理后台；
2. **应用管理 → 智能机器人 → 创建机器人**（填名称、头像、可见范围建议先选自己）；
3. 进入机器人详情页 → **开启「API 模式」→ 连接方式选「长连接」**；
4. 复制 **Bot ID** 与 **Secret**（Secret 只显示一次/可重置，保存好）。

> 入口名称随企业微信版本可能变化，官方文档：
> <https://developer.work.weixin.qq.com/document/path/101833>。
> 详细说明见 [wecom-bot-setup.md](wecom-bot-setup.md)。

✅ 检查点：机器人详情页显示「API 模式 / 长连接」，手头有 Bot ID 和 Secret。

---

## 第 2 步：下载代码

```powershell
git clone https://github.com/<你的用户名>/wecom-office-assistant.git
cd wecom-office-assistant
```

没有 Git：GitHub 页面 → **Code → Download ZIP** → 解压（路径不要含特殊权限限制，建议 `D:\wecom-office-assistant`）。

---

## 第 3 步：初始化运行环境

**双击 `setup.cmd`**（推荐），它会：

1. 找本机 Python 3.10+（也会尝试复用 DSH 自带 Python）；
2. 在项目内创建 `.venv`；
3. 优先用 `wheels\` 里的 Windows 离线依赖安装，失败再走阿里云/清华/PyPI 镜像；
4. 从 `config.example.json` 生成 `config.json`。

如果项目在**读写很慢的 U 盘**：双击 `setup-local-venv.cmd`，
把运行环境建到 `C:\wecom-venv`（更快、更稳），项目本体仍在 U 盘。

命令行等价操作：

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy config.example.json config.json
```

✅ 检查点：`.venv\Scripts\python.exe` 存在；`python -c "import aibot, aiohttp"` 无报错。

---

## 第 4 步：填配置

用记事本打开 `config.json`：

```json
{
  "bot_id": "粘贴 Bot ID",
  "bot_secret": "粘贴 Secret",
  "engine": "auto"
}
```

- `engine: auto`：DSH 开着就真执行，没开就 API 纯聊天（推荐）；
- 想强制真执行：`engine: dsh`；只想聊天：`engine: api`。

填 API Key（可选，二选一）：

```text
方式 A：双击 set-api-key.cmd，粘贴 sk- 开头的 Key（不回显，自动备份 config.json.bak）
方式 B：不写文件，用环境变量 DEEPSEEK_API_KEY
```

> ⚠️ **`config.json` 含密钥，不要提交 Git、不要发群、不要截图。**

✅ 检查点：`config.json` 里 `bot_id` / `bot_secret` 非空。

---

## 第 5 步：自检

**双击 `check.cmd`**，看到至少一个 ✅：

```text
[自检] 引擎模式：auto
[自检] DSH：在线 ✅（http://127.0.0.1:57312）
[自检] DeepSeek API：正常 ✅ 模型 deepseek-chat，回复：在线
[自检] 完成 ✅
```

| 你的情况 | 预期 |
|---|---|
| DSH 已开 + 配了 Key | 两个都 ✅ |
| 只有 DSH | DSH ✅，API 提示未配置（正常） |
| 只有 Key | DSH ❌（正常），API ✅ |
| 想真执行但 DSH ❌ | 打开并登录 DSH，等十几秒再自检一次 |

---

## 第 6 步：启动

**双击 `start.cmd`**，然后打开 `logs\bridge.log`，等 30~60 秒（慢机器/慢 U 盘更久），
看到下面这行即成功：

```text
认证成功，机器人已在线 ✅（在手机企业微信里给它发消息即可）
```

如果日志显示 DSH 相关，稍后会出现：

```text
检测到桌面版 DSH 已开启：http://127.0.0.1:<端口> ✅（任务会真在电脑上执行）
DSH 已就绪 ✅
```

✅ 检查点：日志出现「认证成功，机器人已在线 ✅」。

---

## 第 7 步：手机测试

1. 手机打开企业微信，找到刚创建的机器人；
2. 发一句：`/status` → 应返回版本、执行端、DSH 状态；
3. 发一句：`用 pwsh 报一下当前时间` → 若走 DSH，会真的在电脑上执行并回时间；
4. 发一句：`把桌面截图发我`（或任意电脑上的文件路径）→ 验证文件回传。

> 收不到回复？看 `logs\bridge.log` 最后 20 行，对照 [troubleshooting.md](troubleshooting.md)。

---

## 第 8 步（推荐）：开机自启

管理员 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File tools\register-autostart.ps1
```

之后每次登录 Windows，约 30~60 秒机器人自动上线；离开电脑前双击 `stop.cmd` 即可。
详见 [autostart.md](autostart.md)。

---

## 第 9 步（可选）：云端常驻

想让「电脑关机也能回复」？需要一台常开设备跑桥接（只能纯聊天）：
见 [cloud-deploy.md](cloud-deploy.md)。

---

## 新人最容易踩的 5 个坑

| 坑 | 现象 | 解决 |
|---|---|---|
| 密钥进了 Git | 提交历史泄露 Secret/Key | 立即重置 Secret/删除 Key；`git status` 确认 `config.json` 未被跟踪 |
| 同时开两份 | 手机端时好时坏、反复上线 | 一个机器人 ID 只允许一条长连接，`stop.cmd` + 单实例保护 |
| DSH 没登录 | 自检 DSH ❌ | 打开 DSH 并登录，再 `check.cmd` |
| 主动推送没权限 | 长任务结果/完成通知收不到 | 企业微信后台检查机器人主动推送权限 |
| `.ps1` 乱码/语法错误 | 中文脚本报「未结束的字符串」 | 本项目已强制 UTF-8 BOM；自改脚本请遵守 [CONTRIBUTING.md](../CONTRIBUTING.md) |
