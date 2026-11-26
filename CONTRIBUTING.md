# 贡献指南

感谢你愿意改进「企业微信办公助手」！这是一个偏实用的小项目，目标人群是
「想把手机变成电脑遥控器」的普通用户和小团队，所以**可读、可双击运行、可排障**比架构先进更重要。

## 一、你可以贡献什么

- 真实使用中踩到的坑与修复（企业微信 SDK、DSH 版本、Windows 环境差异等）。
- 文档改进：截图、步骤澄清、英文翻译。
- 新执行端适配：例如把 DSH 换成其它本机 Agent，或新增云端模型 API。
- 安全加固：权限最小化、密钥管理、提示注入防护。

## 二、本地开发

```powershell
# 1) 准备 Python 3.10+（推荐 3.12 / 3.13）
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2) 配置（不要提交 config.json！）
copy config.example.json config.json
# 填入 bot_id / bot_secret；API Key 也可留空，改用环境变量 DEEPSEEK_API_KEY

# 3) 语法检查 + 离线自测（不需要连企业微信）
python -m py_compile wecom_bridge.py selftest_engine.py
python selftest_engine.py
```

## 三、代码约定

- Python 3.10+，只用标准库 + `aibot` + `aiohttp`，不要引入重型依赖。
- 所有本机路径、端口、模型名、凭证都走 `config.json` 或环境变量，**禁止写死**。
- 日志中任何密钥必须走 `mask_key()` 掩码；不要打印完整消息正文以外的隐私内容。
- 企业微信消息回复尽量短；长任务要有进度与超时；出错要给「人话」提示 + 下一步。
- 文件编码（Windows 用户必看，项目已踩过坑）：
  - `.cmd` / `.bat` / `.ps1` / `.vbs`：**CRLF** 换行；
  - `.ps1`：**UTF-8 with BOM**（Windows PowerShell 5.1 才会正确识别中文）；
  - `.vbs`：**纯 ASCII**（cscript 按 ANSI 解析，中文注释会导致语法错误）；
  - `.py` / `.md` / `.json`：UTF-8（无 BOM）。

## 四、提交前检查清单

- [ ] `python -m py_compile wecom_bridge.py selftest_engine.py` 通过
- [ ] `python selftest_engine.py` 通过（或说明哪些项因环境跳过）
- [ ] 没有提交 `config.json`、日志、workspace、`.venv`、真实密钥/手机号/企业信息
- [ ] 新增配置项同步更新 `config.example.json` 与 `docs/`
- [ ] `.cmd/.ps1/.vbs` 的换行与编码符合上面的约定

## 五、Commit / PR

- 提交信息用中文或英文均可，建议格式：`类型: 简述`（如 `fix: DSH 探测超时误判`）。
- 一个 PR 只做一件事；涉及行为变更请同时更新 `CHANGELOG.md` 和文档。
- 不要在 Issue/PR 里贴完整 `config.json`、Secret、API Key、手机号或聊天记录原文。
