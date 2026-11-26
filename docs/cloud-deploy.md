# 云端部署：电脑关机也能收到回复（纯聊天）

> **先说清楚限制**：云端没有你电脑上的 DeepSeek Harness，所以只能 **DeepSeek API 纯聊天**：
> 不能操作电脑、不能发电脑上的文件。想「真执行电脑任务」，必须有一台开着 DSH 的电脑跑桥接。
>
> 另外：**同一个机器人 ID 只允许一条长连接**，云端与本地必须二选一（谁在线谁跑）。

---

## 1. 方案选择

| 方案 | 成本 | 优点 | 缺点 |
|---|---|---|---|
| **A. 云服务器**（轻量 2 核 2G） | ¥100–400/年 | 24h 在线、不受还原卡影响 | 有年费；只能纯聊天 |
| **B. 家里旧电脑 / 迷你主机 / NAS** | ¥0–800 一次性 | 零成本/可控、有屏幕好调试 | 要常开+网络，需设不休眠 |
| **C. 树莓派 / 旧安卓手机** | ¥100–400 | 便宜、功耗低 | 折腾、稳定性一般 |

推荐：有旧电脑用 B；没有就用 A（2 核 2G 足够，桥接只占几十 MB 内存）。

---

## 2. 方案 A：Linux 云服务器（以 Ubuntu 22.04 / Debian 12 为例）

### 2.1 前提

- 能 SSH 登录服务器；
- 服务器能出站访问 `openws.work.weixin.qq.com:443` 与 `api.deepseek.com:443`；
- **不需要开放任何入站端口**（桥接主动外连）。

### 2.2 安装 Python 环境

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
python3 --version   # 需要 3.10+
```

### 2.3 上传项目

在本地打包（只传程序与配置，不带 `.venv`/日志/私密数据）：

```powershell
# 本地执行（把路径、IP 换成你的）
scp -r D:\path\to\wecom-office-assistant root@<SERVER_IP>:/opt/wecom-office-assistant
```

或服务器直接拉取：

```bash
cd /opt
sudo git clone <你的仓库地址> wecom-office-assistant
sudo chown -R $USER:$USER wecom-office-assistant
```

### 2.4 建虚拟环境并安装依赖

```bash
cd /opt/wecom-office-assistant
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -c "import aibot, aiohttp; print('ok')"
```

> 国内服务器可用镜像：`.venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple -r requirements.txt`

### 2.5 配置为纯聊天模式

```bash
cp config.example.json config.json
nano config.json
chmod 600 config.json
```

```json
{
  "bot_id": "你的 Bot ID",
  "bot_secret": "你的 Secret",
  "engine": "api",
  "api": {
    "enabled": true,
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_key": ""
  }
}
```

API Key 推荐用环境变量注入（不要写文件）：

```bash
sudo tee /etc/wecom-bridge.env > /dev/null <<'EOF'
DEEPSEEK_API_KEY=sk-你的Key
EOF
sudo chmod 600 /etc/wecom-bridge.env
```

### 2.6 systemd 常驻（开机自启 + 崩溃自动拉起）

```bash
sudo cp deploy/wecom-bridge.service /etc/systemd/system/wecom-bridge.service
# 按实际路径修改 WorkingDirectory / ExecStart / 仓库地址
sudo nano /etc/systemd/system/wecom-bridge.service
```

在 `[Service]` 下增加环境变量文件：

```ini
EnvironmentFile=/etc/wecom-bridge.env
```

然后：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wecom-bridge
sudo systemctl status wecom-bridge --no-pager
journalctl -u wecom-bridge -n 50 --no-pager
```

看到日志 `认证成功，机器人已在线 ✅` 即成功。

### 2.7 手机验证

手机企业微信发：

- `/status` → 应显示引擎 `api`；
- 「你好」→ 应收到聊天回复（不带电脑操作能力）。

---

## 3. 方案 B：家里旧电脑 / 迷你主机（Windows）

1. 把项目拷到机器（`git clone` 或复制文件夹）；
2. 双击 `setup.cmd`（Windows）初始化环境；
3. `config.json` 设 `"engine": "api"`，填 API Key（或环境变量）；
4. 双击 `start.cmd` 启动；
5. 开机自启：按 [autostart.md](autostart.md) 注册计划任务；
6. **电源设置**：永不睡眠 / 合盖不睡眠 / 断网恢复后自动重连（桥接会无限重连）。

---

## 4. 云端与本机切换（重要）

```text
不在电脑前 / 电脑关机：
  云端：sudo systemctl start wecom-bridge     # 手机纯聊天
  本机：stop.cmd

到电脑前要真执行：
  云端：sudo systemctl stop wecom-bridge
  本机：start.cmd                              # 手机操作电脑

离开电脑：
  本机：stop.cmd
  云端：sudo systemctl start wecom-bridge
```

> 同时开会被互踢：表现为手机端时好时坏、消息丢失。切换务必先停另一方。

---

## 5. 可选：一键切换脚本

在本地电脑放两个 `.bat`（需要先把本机公钥配到服务器）：

```bat
:: 切到云端.bat
@echo off
ssh root@<SERVER_IP> "systemctl start wecom-bridge"
call "%~dp0stop.cmd"
```

```bat
:: 切到本机.bat
@echo off
ssh root@<SERVER_IP> "systemctl stop wecom-bridge"
call "%~dp0start.cmd"
```

---

## 6. 云端安全清单

- [ ] `config.json` 权限 600；API Key 用环境变量，不写进仓库；
- [ ] 不为桥接开放任何入站端口；
- [ ] 服务器只运行桥接，不装 DSH（避免误开全权限执行）；
- [ ] 机器人可见范围最小化；
- [ ] 定期 `sudo systemctl status`、看日志、升级系统与依赖；
- [ ] 备份 `config.json`（加密保存）。

---

## 7. 常见问题

**Q：云端能操作我电脑吗？**
不能。云端没有你电脑的桌面与文件；只能聊天。要真执行，必须电脑开着 DSH + 本地桥接。

**Q：能不能云端和本机同时跑（一个聊天一个执行）？**
不能，同一 Bot ID 只能一条长连接。需要两个机器人分别注册（成本/复杂度高，不推荐）。

**Q：低配 2 核 2G 够吗？**
够。桥接是单进程异步 Python，常驻内存几十 MB；主要成本是 DeepSeek API 调用费。

**Q：服务器重启后会自动恢复吗？**
`systemctl enable` 后会；服务 `Restart=always` 崩溃也会拉起。
