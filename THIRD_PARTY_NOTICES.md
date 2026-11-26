# 第三方组件说明

本项目自身以 MIT 许可证开源（见 `LICENSE`）。运行时依赖以下第三方组件，
其版权归各自作者所有，分发时请遵守对应许可证。

## 直接依赖

| 组件 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| [wecom-aibot-python-sdk](https://github.com/WecomTeam/wecom-aibot-python-sdk)（企业微信官方） | 1.0.2 | MIT | 企业微信智能机器人 WebSocket 长连接、消息收发、文件上传 |
| [aiohttp](https://github.com/aio-libs/aiohttp) | 3.14.5 | Apache-2.0 AND MIT | HTTP 客户端（DeepSeek API、DSH 本机 API） |

## 随仓库 `wheels/` 分发的传递依赖（Windows 离线安装用）

`wheels/` 中的 wheel 均来自 PyPI 官方发布，**未做任何修改**，面向
**CPython 3.12 / Windows x64**（其它 Python 版本会自动改走在线安装，或运行
`scripts/download-wheels.ps1` 重新下载）。各 wheel 的 `.dist-info` 目录内含各自的
LICENSE 文件，安装后也可在 `site-packages/*.dist-info/licenses` 查看。

| 组件 | 版本 | 许可证 |
|---|---|---|
| aiohappyeyeballs | 2.7.1 | PSF-2.0 |
| aiosignal | 1.4.0 | Apache-2.0 |
| attrs | 26.1.0 | MIT |
| certifi | 2026.11.20 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| frozenlist | 1.8.0 | Apache-2.0 |
| idna | 3.20 | BSD-3-Clause |
| multidict | 7.2.1 | Apache-2.0 |
| propcache | 0.5.4 | Apache-2.0 |
| pycparser | 3.12 | BSD-3-Clause |
| pyee | 16.1.0 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| websockets | 17.4 | BSD-3-Clause |
| yarl | 1.25.1 | Apache-2.0 |
| wecom-aibot-python-sdk | 1.0.2 | MIT |

> 所有依赖均为 MIT / BSD / Apache-2.0 / PSF / MPL-2.0 等宽松许可证，无 GPL/AGPL 传染风险。
> 若你再次分发本项目的二进制包，请保留以上组件的许可证信息（wheel 内已自带）。

## 外部服务与软件（不随本项目分发）

- **企业微信 / 企业微信智能机器人**：使用需遵守腾讯相关服务条款；
  机器人能力仅限本企业成员，凭证（Bot ID / Secret）由使用者在管理后台自行申请。
- **DeepSeek API**：可选执行端，调用 [DeepSeek 开放平台](https://platform.deepseek.com/)
  的 `chat/completions` 接口，需遵守其服务条款并自行承担调用费用。
- **DeepSeek Harness（DSH）**：可选的「真执行」执行端，属于独立的桌面软件，
  本仓库**不包含也不分发**其任何代码；桥接仅通过本机接口与之通信。
  本项集成是**非官方社区实现**，未获 DeepSeek 官方支持，请以 DSH 官方发布渠道与许可条款为准。

## 商标

「企业微信」「WeCom」「DeepSeek」「DeepSeek Harness」等名称与商标归各自权利人所有。
本项目与腾讯、深度求索公司无隶属或背书关系。
