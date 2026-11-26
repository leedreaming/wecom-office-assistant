# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The WeCom Office Assistant contributors
#
# 非官方项目：与腾讯（企业微信）、深度求索（DeepSeek / DeepSeek Harness）无隶属或背书关系。
# DSH 集成依赖其本机 HTTP 接口与本地凭证文件，属个人自动化用途，可能随 DSH 版本变化。
"""企业微信「智能机器人」<-> 本机 DeepSeek Harness 桥接（带 DeepSeek API 兜底）。

手机「企业微信」App 里给机器人发消息
    -> 本程序（WebSocket 长连接，官方 API，不需要公网/不碰鼠标）
    -> 先检测本机 DSH 桌面版是否开着：
         · 开着   -> 交给 DSH 真执行任务（能操作电脑、发文件）
         · 没开   -> 自动改用 DeepSeek API 直连回复（纯聊天，不能操作电脑）
    -> 回复原路发回手机

执行端模式（config.json -> "engine"）：
    auto  自动检测（默认）：DSH 在线用 DSH，DSH 不在线用 DeepSeek API
    dsh   只用 DSH（不兜底）
    api   只用 DeepSeek API（纯聊天）

用法：
    .venv\\Scripts\\python.exe wecom_bridge.py         前台运行（Ctrl+C 退出）
    .venv\\Scripts\\python.exe wecom_bridge.py --check-all   自检 DSH / API 两个执行端
    start.cmd                                          后台运行（日志在 logs\\）
    stop.cmd                                           停止后台进程
    set-api-key.cmd                                    填写 DeepSeek API Key
    check.cmd                                          双击自检

配置：config.json（首次运行会从 config.example.json 复制一份，填 bot_id / bot_secret）
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
import uuid

from aibot import WSClient, WSClientOptions, generate_req_id

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
EXAMPLE_PATH = os.path.join(HERE, "config.example.json")
LOG_DIR = os.path.join(HERE, "logs")
PID_PATH = os.path.join(LOG_DIR, "bridge.pid")
LOG_PATH = os.path.join(LOG_DIR, "bridge.log")
WORKER_PATH = os.path.join(HERE, "dsh_worker.js")

BRIDGE_VERSION = "1.0.0（办公助手）"
API_MODE_DSH_OFF = (
    "重要背景：电脑端的 DeepSeek Harness 当前没有开启，所以你现在只能纯聊天："
    "不能执行电脑命令、不能读写电脑文件、不能把文件发到手机。")
API_MODE_FORCED = (
    "重要背景：用户当前强制使用 API 模式（config 里 engine=api），所以你现在只能纯聊天："
    "不能执行电脑命令、不能读写电脑文件、不能把文件发到手机。")
API_DEFAULT_SYSTEM = (
    "你是运行在手机「企业微信」里的 DeepSeek 助手，用户通过聊天客户端远程给你发消息。\n"
    "{mode_block}\n"
    "回答用中文，直接、实用；内容较长时用 Markdown（企业微信支持基本 Markdown）。\n"
    "如果用户要求「操作电脑 / 跑命令 / 发文件」这类事，先说明现在是 API 聊天模式，"
    "再给出他能自己在手机上或下次上机时做的替代建议，不要假装自己已经执行。\n"
    "当前时间：{time}（Asia/Shanghai）。"
)


def mask_key(key):
    """日志/状态里只显示 API Key 的头尾，避免泄露。"""
    key = (key or "").strip()
    if len(key) <= 10:
        return "***" if key else "（未配置）"
    return key[:5] + "…" + key[-4:]


def safe_int(value, default, minimum=None):
    """配置里的数字容错：类型不对/负数都退回默认值/下限，避免启动直接崩。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = int(default)
    if minimum is not None:
        n = max(int(minimum), n)
    return n


def safe_float(value, default, minimum=None):
    try:
        f = float(value)
    except (TypeError, ValueError):
        f = float(default)
    if minimum is not None:
        f = max(float(minimum), f)
    return f


# --------------------------------------------------------------------------- 执行端异常
class DshUnavailableError(RuntimeError):
    """DSH 不在线 / 连不上 / 没启动 / 鉴权失败：这类失败可以安全地改用 DeepSeek API 兜底。"""


class DshTaskError(RuntimeError):
    """DSH 在线且已受理任务，但执行出错：不静默切 API（避免误导用户、避免重复执行）。"""


class DshTimeoutError(RuntimeError):
    """DSH 已受理任务但等待超时：不重试、不兜底（任务可能还在电脑上跑）。"""


# --------------------------------------------------------------------------- 日志
# 日志写盘放后台线程：网吧 U 盘偶发写入卡顿 5~10 秒，不能让它卡住 asyncio 事件循环
_LOG_QUEUE = queue.Queue(maxsize=2000)
_LOG_WRITER = None
_LOG_WRITER_LOCK = threading.Lock()


def _log_writer_loop():
    while True:
        line = _LOG_QUEUE.get()
        if line is None:
            break
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass


def _ensure_log_writer():
    global _LOG_WRITER
    if _LOG_WRITER is not None and _LOG_WRITER.is_alive():
        return
    with _LOG_WRITER_LOCK:
        if _LOG_WRITER is None or not _LOG_WRITER.is_alive():
            _LOG_WRITER = threading.Thread(
                target=_log_writer_loop, name="bridge-log-writer", daemon=True)
            _LOG_WRITER.start()


def flush_logs(timeout=2.0):
    """退出前尽量把队列里的日志写完（U 盘慢也不无限等）。"""
    deadline = time.time() + timeout
    while not _LOG_QUEUE.empty() and time.time() < deadline:
        time.sleep(0.02)


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:  # 控制台编码打不出 emoji 时，退化成 ASCII 也要显示出来
        try:
            sys.stdout.write(line.encode("ascii", "replace").decode("ascii") + "\n")
            sys.stdout.flush()
        except Exception:
            pass
    _ensure_log_writer()
    try:
        _LOG_QUEUE.put_nowait(line)
    except queue.Full:
        pass


class SdkLogger:
    """把 SDK 内部日志接到我们的日志文件里（默认不刷屏：debug 丢弃）。"""

    def debug(self, message, *args):
        pass

    def info(self, message, *args):
        log("[sdk] " + (message % args if args else message))

    def warn(self, message, *args):
        log("[sdk][warn] " + (message % args if args else message))

    def error(self, message, *args):
        log("[sdk][error] " + (message % args if args else message))


# --------------------------------------------------------------------------- 配置
def resolve_path(value, default):
    """把配置里的路径解析成绝对路径：空 -> default；相对路径 -> 相对项目目录。"""
    if not value or (isinstance(value, str) and value.strip().lower() == "auto"):
        return default
    value = os.path.expanduser(str(value))
    if not os.path.isabs(value):
        return os.path.abspath(os.path.join(HERE, value))
    return value


def load_config(require_credentials=True):
    if not os.path.exists(CONFIG_PATH):
        if os.path.exists(EXAMPLE_PATH):
            shutil.copyfile(EXAMPLE_PATH, CONFIG_PATH)
            log("已生成 config.json（记得填 bot_id / bot_secret）")
        if not require_credentials and os.path.exists(EXAMPLE_PATH):
            with open(EXAMPLE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        raise SystemExit(2)
    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)
    if require_credentials and not (cfg.get("bot_id") and cfg.get("bot_secret")):
        log("config.json 里 bot_id / bot_secret 还是空的。")
        log("开通步骤见 README.md：企业微信管理后台 -> 应用管理 -> 智能机器人 -> API 模式。")
        raise SystemExit(2)
    return cfg


# --------------------------------------------------------------------------- DSH 执行端：直接驱动正在运行的桌面实例（推荐）
class WebApiWorker:
    """通过桌面版 DSH 自带的 HTTP API 对话。

    好处：任务跑在用户正在用的那个 DSH 实例里（会话会出现在界面列表里，能看到它干活），
    不额外起 runtime、不依赖 profile 文件写权限。要求：桌面版 DSH 正在运行。
    端口自动发现：DSH 每次启动端口可能不同，自动从本机监听端口里找。
    """

    def __init__(self, cfg):
        d = cfg.get("dsh") or {}
        configured = (d.get("web_url") or "").strip()
        self.configured_base = configured.rstrip("/") if configured and configured.lower() != "auto" else None
        self.base = self.configured_base or "http://127.0.0.1:57352"
        self.authority = self.base.split("://", 1)[-1]
        self.cwd = resolve_path(d.get("cwd") or "", os.path.join(HERE, "workspace"))
        home = (d.get("home") or "").strip()
        if not home or home.lower() == "auto":
            home = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
        self.home = home
        self.http = None
        self.sessions = set()
        self.lock = asyncio.Lock()
        self._secret_cache = None
        self._resolved_at = 0.0
        # ---- 在线探测（给「DSH 没开就切 API」用）----
        self.probe_timeout = safe_float(d.get("probe_timeout_ms"), 4000, 500) / 1000.0
        self.probe_ttl_ok = safe_float(d.get("probe_ttl_ok_s"), 60, 5)
        self.probe_ttl_fail = safe_float(d.get("probe_ttl_fail_s"), 15, 3)
        self._probe_ok = None
        self._probe_at = 0.0
        self._probe_lock = asyncio.Lock()
        self._probe_reason = ""
        self._ports_cache = []
        self._ports_at = 0.0
        self._ports_ttl = safe_float(d.get("ports_ttl_s"), 30, 5)
        self._last_good_base = None

    @staticmethod
    def _split_base(base):
        from urllib.parse import urlsplit
        u = urlsplit(base)
        return u.hostname or "127.0.0.1", u.port or 80

    # ---------------------------------------------------------- 端口发现
    @staticmethod
    def _dsh_listen_ports():
        import subprocess
        ports = []
        try:
            pids = set()
            task = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq DeepSeek Harness.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, encoding="gbk", errors="replace", timeout=3)
            for line in task.stdout.splitlines():
                parts = [p.strip().strip('"') for p in line.split('","')]
                if len(parts) >= 2 and "deepseek harness" in parts[0].lower():
                    try:
                        pids.add(int(parts[1]))
                    except ValueError:
                        pass
            if not pids:
                return ports
            ns = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                                encoding="gbk", errors="replace", timeout=3)
            for line in ns.stdout.splitlines():
                if "LISTENING" not in line.upper():
                    continue
                parts = line.split()
                if len(parts) < 5:
                    continue
                local, pid_text = parts[1], parts[4]
                if not (local.startswith("127.0.0.1:") or local.startswith("[::1]:")):
                    continue
                try:
                    port = int(local.rsplit(":", 1)[1])
                    pid = int(pid_text)
                except ValueError:
                    continue
                if pid in pids and port not in ports:
                    ports.append(port)
        except Exception:  # noqa: BLE001
            pass
        return ports

    async def _discover_ports(self, force=False):
        """端口发现要跑 tasklist/netstat（同步、且负载高时可能很慢），放线程池 + 缓存 + 限时。"""
        now = time.time()
        if not force and self._ports_cache and (now - self._ports_at) < self._ports_ttl:
            return list(self._ports_cache)
        try:
            ports = await asyncio.wait_for(asyncio.to_thread(self._dsh_listen_ports), timeout=5)
        except Exception:  # noqa: BLE001
            ports = list(self._ports_cache)  # 超时/失败就用上次的结果（没有就空）
        self._ports_cache = ports
        self._ports_at = now
        return list(ports)

    async def _candidate_bases(self, force_ports=False):
        """DSH 可能的 HTTP 地址：上次成功地址优先 -> 配置 -> 自动发现 -> 旧默认端口。"""
        candidates = []
        if self._last_good_base:
            candidates.append(self._last_good_base)
        if self.configured_base and self.configured_base not in candidates:
            candidates.append(self.configured_base)
        for port in await self._discover_ports(force=force_ports):
            base = "http://127.0.0.1:%d" % port
            if base not in candidates:
                candidates.append(base)
        if "http://127.0.0.1:57352" not in candidates:
            candidates.append("http://127.0.0.1:57352")
        return candidates

    async def _resolve_base(self):
        """找出当前桌面版 DSH 的 HTTP 地址（配置优先 + 自动发现），并验证鉴权。

        整体限时 10 秒、单个地址限时 4 秒：DSH 没开时必须尽快失败、交给 API 兜底，
        绝不能在这里无限重试（否则连企业微信长连接都建不起来）。
        """
        self._secret_cache = None
        try:
            self._secret(force=True)
        except Exception as e:  # noqa: BLE001
            raise DshUnavailableError("找不到 DSH 登录凭证：%s" % (e,)) from e
        errors = []
        deadline = time.time() + 10.0
        for base in await self._candidate_bases():
            remain = deadline - time.time()
            if remain <= 0:
                errors.append("解析超时")
                break
            try:
                self.base = base
                self.authority = base.split("://", 1)[-1]
                value = await asyncio.wait_for(
                    self._rpc("session/modelCatalog", {}), timeout=min(4.0, remain))
                if value:
                    self._resolved_at = time.time()
                    self._last_good_base = base
                    log("已连接桌面版 DSH：%s" % base)
                    return True
            except Exception as e:  # noqa: BLE001
                errors.append("%s -> %s" % (base, str(e)[:120]))
                continue
        raise DshUnavailableError(
            "找不到可用的桌面版 DSH（/api 鉴权失败或未启动）。尝试过：\n  " + "\n  ".join(errors[:5]))

    # ---------------------------------------------------------- 在线探测
    async def is_available(self, force=False):
        """桌面版 DSH 是否在线。

        带缓存：在线 60 秒内、离线 15 秒内不重复探测，避免每条消息都等。
        任何异常都按「不在线」处理，让上层走 DeepSeek API 兜底。
        """
        try:
            return await self._probe(force=force)
        except Exception as e:  # noqa: BLE001
            log("DSH 在线探测异常（按未开启处理）：%r" % (e,))
            self.mark_unavailable("探测异常：%s" % (str(e)[:100],))
            return False

    def mark_unavailable(self, reason=""):
        """执行失败时把探测状态回写成「离线」，避免下一条消息又白等 DSH。"""
        self._probe_ok = False
        self._probe_at = time.time()
        self._probe_reason = reason or "DSH 执行失败"

    async def _probe(self, force=False):
        async with self._probe_lock:
            now = time.time()
            if not force and self._probe_ok is not None:
                ttl = self.probe_ttl_ok if self._probe_ok else self.probe_ttl_fail
                if now - self._probe_at < ttl:
                    return self._probe_ok
            ok, reason = await self._probe_once()
            if not ok:
                # DSH 执行任务时会短暂繁忙，探测可能超时 → 最近 10 分钟连过就再试一次，避免误判离线
                recently_ok = (time.time() - (self._resolved_at or 0)) < 600
                if recently_ok:
                    await asyncio.sleep(1.5)
                    ok2, reason2 = await self._probe_once()
                    if ok2:
                        log("DSH 探测首次失败（%s），重试后恢复在线 ✅" % (reason,))
                        ok, reason = True, "在线"
                    else:
                        reason = reason2 or reason
            if ok != self._probe_ok:
                if ok:
                    log("检测到桌面版 DSH 已开启：%s ✅（任务会真在电脑上执行）" % self.base)
                else:
                    log("桌面版 DSH 未开启 / 连不上 ❌（%s；改用 DeepSeek API 直连兜底）" % reason)
            self._probe_ok = ok
            self._probe_at = time.time()
            self._probe_reason = reason
            if ok:
                self._resolved_at = self._probe_at
            return ok

    def _quick_bases(self):
        """不用 tasklist/netstat 就知道的候选地址：上次成功地址 + 配置 + 旧默认端口。"""
        bases = []
        if self._last_good_base:
            bases.append(self._last_good_base)
        if self.configured_base and self.configured_base not in bases:
            bases.append(self.configured_base)
        if "http://127.0.0.1:57352" not in bases:
            bases.append("http://127.0.0.1:57352")
        return bases

    async def _probe_once(self):
        """探测 DSH 是否在线，返回 (是否在线, 原因)。

        - 先用「上次成功 / 配置 / 默认端口」快速试（正常 1~2 秒出结果）；
        - 都不行再自动发现端口（tasklist/netstat 高负载时慢，已限时 5 秒）；
        - 每个地址先 1 秒 TCP 预检，再发 /api/session/modelCatalog（最多 3 次尝试）。
        """
        try:
            secret = self._secret(force=True)  # 每次重读凭证：DSH 重启换 secret 也能自愈
        except Exception as e:  # noqa: BLE001
            return False, "没有 DSH 登录凭证（%s）" % (str(e)[:80],)
        deadline = time.time() + max(10.0, self.probe_timeout * 3)
        quick = self._quick_bases()
        ok, reason, secret = await self._probe_base_list(quick, secret, deadline)
        if ok:
            return True, "在线"
        discovered = [base for base in (
            "http://127.0.0.1:%d" % port for port in await self._discover_ports())
            if base not in quick]
        if discovered:
            ok2, reason2, secret = await self._probe_base_list(discovered, secret, deadline)
            if ok2:
                return True, "在线"
            reason = reason2 or reason
        return False, reason

    async def _probe_base_list(self, bases, secret, deadline):
        import aiohttp
        reason = "没有可试的地址"
        live = []
        for base in bases:
            remain = deadline - time.time()
            if remain <= 0:
                return False, "探测超时", secret
            try:
                host, port = self._split_base(base)
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=min(1.0, max(0.3, remain)))
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass
                live.append(base)
            except Exception as e:  # noqa: BLE001
                reason = "端口未监听(%s)" % (type(e).__name__,)
        for base in live:
            for attempt in (0, 1, 2):
                remain = deadline - time.time()
                if remain <= 0:
                    return False, "探测超时", secret
                authority = base.split("://", 1)[-1]
                timeout = aiohttp.ClientTimeout(
                    total=min(max(2.0, self.probe_timeout), max(0.8, remain)))
                try:
                    payload = {
                        "type": "client-request",
                        "rpcId": "wecom-probe-" + uuid.uuid4().hex[:8],
                        "method": "session/modelCatalog",
                        "payload": {"args": {}},
                    }
                    headers = {"content-type": "application/json",
                               "cookie": self._cookie(secret, authority)}
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.post(base + "/api/session/modelCatalog",
                                                json=payload, headers=headers) as resp:
                            status = resp.status
                            body = await resp.json(content_type=None) if status == 200 else None
                    if status == 200 and ((body or {}).get("result") or {}).get("ok"):
                        self.base = base
                        self.authority = authority
                        self._secret_cache = secret
                        self._last_good_base = base
                        return True, "在线", secret
                    if status == 401:
                        # DSH 重启/重登录后 browser-session secret 会变 → 重读凭证立即重试
                        self._secret_cache = None
                        secret = self._secret(force=True)
                        reason = "鉴权失败(401)，凭证可能已轮换"
                    else:
                        reason = "HTTP %s" % status
                except Exception as e:  # noqa: BLE001
                    reason = "连接失败(%s: %s)" % (type(e).__name__, str(e)[:60])
                if attempt < 2:
                    await asyncio.sleep(0.4)
        return False, reason, secret

    # ---------------------------------------------------------- 鉴权 cookie
    def _secret(self, force=False):
        if force:
            self._secret_cache = None
        if self._secret_cache is not None:
            return self._secret_cache
        path = os.path.join(self.home, ".credentials.yaml")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"client-connection/browser-session:[\s\S]*?secret:\s*([A-Za-z0-9_-]+)", text)
        if not m:
            raise RuntimeError("没在 %s 里找到 browser-session secret" % path)
        raw = m.group(1)
        raw += "=" * (-len(raw) % 4)
        self._secret_cache = base64.urlsafe_b64decode(raw)
        return self._secret_cache

    @staticmethod
    def _b64url(data):
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    def _cookie(self, secret=None, authority=None):
        authority = authority or self.authority
        secret = secret if secret is not None else self._secret()
        now_ms = int(time.time() * 1000)
        payload = {
            "version": 1,
            "authority": authority,
            "issuedAt": now_ms - 1000,
            "expiresAt": now_ms + 3600 * 1000,
        }
        body = self._b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        sig = self._b64url(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
        name = "dsh-auth-" + self._b64url(hashlib.sha256(authority.encode("utf-8")).digest())
        return "%s=v1.%s.%s" % (name, body, sig)

    # ---------------------------------------------------------- RPC
    async def start(self):
        import aiohttp
        if self.http is None or self.http.closed:
            self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120))
        await self._resolve_base()

    async def stop(self):
        if self.http is not None:
            try:
                await self.http.close()
            except Exception:  # noqa: BLE001
                pass
            self.http = None

    async def _rpc(self, method, args):
        """调一次 DSH 的 /api RPC。

        连接类错误只对同一个地址重试一次；**绝不在这里递归重新解析地址**
        （DSH 没开时会无限递归、把桥接启动/回复全部卡死）。
        DSH 重启换了端口的话，由下一轮的在线探测重新发现。
        """
        import aiohttp
        last_error = None
        for attempt in range(2):
            try:
                if self.http is None or self.http.closed:
                    self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120))
                rpc_id = "wecom-" + uuid.uuid4().hex[:12]
                payload = {"type": "client-request", "rpcId": rpc_id, "method": method, "payload": {"args": args}}
                headers = {"content-type": "application/json", "cookie": self._cookie()}
                async with self.http.post("%s/api/%s" % (self.base, method), json=payload, headers=headers) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        if resp.status in (401, 403, 404, 502, 503, 504):
                            raise DshUnavailableError(
                                "DSH API %s HTTP %s: %s" % (method, resp.status, text[:300]))
                        raise RuntimeError("DSH API %s HTTP %s: %s" % (method, resp.status, text[:300]))
                    try:
                        body = json.loads(text)
                    except Exception as e:  # noqa: BLE001
                        raise RuntimeError("DSH API %s 返回非 JSON: %s" % (method, text[:300])) from e
                result = body.get("result") or {}
                if not result.get("ok"):
                    raise RuntimeError("DSH API %s 失败: %s" % (
                        method, json.dumps(result.get("error"), ensure_ascii=False)[:400]))
                return result.get("value")
            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
                last_error = e
                try:
                    if self.http is not None:
                        await self.http.close()
                except Exception:  # noqa: BLE001
                    pass
                self.http = None
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
            except DshUnavailableError:
                raise
            except RuntimeError as e:
                # 401 多半是 DSH 重启后 browser-session secret 变了 → 清缓存重读凭证
                if "401" in str(e) or "unauthorized" in str(e).lower():
                    self._secret_cache = None
                raise
        raise DshUnavailableError("DSH 接口连接失败：%s" % (last_error,))

    # ---------------------------------------------------------- 会话
    async def _ensure_session(self, session_id):
        """先查会话在不在，不在才创建（直接 create 一个已存在的会话会让 DSH 断开连接）。"""
        if session_id in self.sessions:
            return
        exists = False
        try:
            await self._rpc("session/state", {"request": {"sessionId": session_id}})
            exists = True
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "exist" in msg or "already" in msg:
                exists = True
        if not exists:
            try:
                await self._rpc("session/create", {"request": {"cwd": self.cwd, "sessionId": session_id}})
                log("已创建新会话 %s（工作目录 %s）" % (session_id, self.cwd))
            except Exception as e:  # noqa: BLE001
                msg = str(e).lower()
                if "exist" not in msg and "already" not in msg:
                    raise
        self.sessions.add(session_id)

    @staticmethod
    def _extract_text(value):
        records = ((value or {}).get("output") or {}).get("records") or []
        text = ""
        reason = None
        for record in records:
            event = (record or {}).get("event") or {}
            if event.get("type") == "assistant/message":
                content = ((event.get("data") or {}).get("message") or {}).get("content") or []
                chunk = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                if chunk:
                    text = chunk
            elif event.get("type") == "turn/end":
                reason = ((event.get("data") or {}).get("reason") or {}).get("kind")
        return text, reason

    async def ask(self, session_id, text, timeout_ms):
        async with self.lock:
            try:
                await self.start()
            except DshUnavailableError:
                raise
            except Exception as e:  # noqa: BLE001
                raise DshUnavailableError("DSH 启动失败：%s" % (e,)) from e
            try:
                return await self._ask_impl(session_id, text, timeout_ms)
            except (DshUnavailableError, DshTaskError, DshTimeoutError):
                raise
            except asyncio.TimeoutError as e:
                raise DshTimeoutError("DSH 等待超时：%s" % (e,)) from e
            except Exception as e:  # noqa: BLE001
                raise DshTaskError("DSH 执行失败：%s" % (e,)) from e

    async def _ask_impl(self, session_id, text, timeout_ms):
        await self._ensure_session(session_id)
        state = await self._rpc("session/state", {"request": {"sessionId": session_id}})
        cursor = (state or {}).get("cursor") or 0
        request_id = str(uuid.uuid4())
        await self._rpc("session/prompt", {"request": {
            "requestId": request_id,
            "sessionId": session_id,
            "mode": "queue",
            "content": [{"type": "text", "text": text}],
            "clientTimeZone": "Asia/Shanghai",
        }})
        deadline = time.time() + timeout_ms / 1000.0
        last_value = None
        last_reason = ""
        while time.time() < deadline:
            remain_ms = int(min(30000, max(1000, (deadline - time.time()) * 1000)))
            value = await self._rpc("session/wait", {"request": {
                "sessionId": session_id, "timeoutMs": remain_ms, "afterSeq": cursor,
            }})
            last_value = value or {}
            last_reason = last_value.get("reason") or ""
            if last_reason and last_reason != "timeout":
                break
            if not last_reason:
                await asyncio.sleep(0.3)  # DSH 偶发返回空值：继续等，绝不能误判成超时
        if last_reason == "input-required":
            # DSH 在等电脑上的「批准/允许」：手机端无法批准 → 自动取消本次任务并明确告知
            log("DSH 任务停在「等待批准」（input-required），自动取消本次任务")
            try:
                await self._rpc("session/cancel", {"request": {"sessionId": session_id}})
            except Exception as e:  # noqa: BLE001
                log("取消「等待批准」的会话失败：%r" % (e,))
            raise DshTaskError(
                "⚠️ 这个任务需要你在电脑上点「允许」批准（涉及电脑桌面/文件等敏感操作），"
                "手机端没法替你批准，已自动停止。\n"
                "请在电脑上批准后把这条指令再发一次；或者把 DSH 审批策略改成自动允许（跟我说一声即可）。")
        reply, reason = self._extract_text(last_value)
        if reply:
            return reply
        if reason == "error":
            raise DshTaskError("DSH 执行出错（turn/end reason=error）")
        if reason:
            return "（执行结束，但没有文字结果；reason=%s）" % reason
        raise DshTimeoutError("DSH 等待超时（%ss）" % (timeout_ms // 1000))


# --------------------------------------------------------------------------- DeepSeek API 直连执行端（DSH 没开时兜底）
class ApiError(RuntimeError):
    """DeepSeek API 调用失败（给用户看的消息已经在异常文本里）。"""


class DeepSeekApiWorker:
    """DSH 桌面版没开时，直接用 DeepSeek 官方 API 聊天（纯文本，不能操作电脑）。

    - 走 OpenAI 兼容接口：POST {base_url}/chat/completions
    - 每个会话（session_id）保留多轮上下文，超长自动裁剪
    - API Key 优先取 config.json -> api.api_key，其次环境变量（默认 DEEPSEEK_API_KEY）
    """

    def __init__(self, cfg):
        self.enabled = True
        self.base_url = "https://api.deepseek.com"
        self.api_key = ""
        self.key_source = "未配置"
        self.model = "deepseek-chat"
        self.temperature = 1.0
        self.max_tokens = 4096
        self.timeout_s = 180.0
        self.system_prompt = API_DEFAULT_SYSTEM
        self.history_max_turns = 20
        self.history_max_chars = 50000
        self._apply_api_cfg(cfg.get("api") or {})
        self.histories = {}
        self._session = None
        self._config_mtime = self._read_cfg_mtime()

    def _apply_api_cfg(self, api):
        """把 config.json 里 api 段的配置应用到当前对象（启动时 + 配置变更热加载时都走这里）。"""
        api = api or {}
        self.enabled = bool(api.get("enabled", self.enabled))
        env_name = api.get("api_key_env") or "DEEPSEEK_API_KEY"
        cfg_key = str(api.get("api_key") or "").strip()
        env_key = (os.environ.get(env_name) or "").strip()
        self.api_key = cfg_key or env_key
        self.key_source = "config.json" if cfg_key else ("环境变量 " + env_name if env_key else "未配置")
        self.base_url = (api.get("base_url") or self.base_url or "https://api.deepseek.com").rstrip("/")
        self.model = api.get("model") or self.model
        self.temperature = safe_float(api.get("temperature"), self.temperature)
        self.max_tokens = safe_int(api.get("max_tokens"), self.max_tokens, 1)
        self.timeout_s = max(10.0, safe_float(api.get("timeout_ms"), self.timeout_s * 1000, 10000) / 1000.0)
        self.system_prompt = api.get("system_prompt") or self.system_prompt
        self.history_max_turns = safe_int(api.get("history_max_turns"), self.history_max_turns, 1)
        self.history_max_chars = safe_int(api.get("history_max_chars"), self.history_max_chars, 1000)

    @staticmethod
    def _read_cfg_mtime():
        try:
            return os.path.getmtime(CONFIG_PATH)
        except OSError:
            return 0.0

    def reload_if_changed(self):
        """桥接运行中改了 config.json（比如刚用 set-api-key.cmd 填 Key）→ 自动热加载。"""
        mtime = self._read_cfg_mtime()
        if mtime <= 0 or mtime == self._config_mtime:
            return False
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                cfg = json.load(f)
        except Exception as e:  # noqa: BLE001
            log("config.json 读取失败（继续用旧配置）：%r" % (e,))
            self._config_mtime = mtime
            return False
        self._apply_api_cfg(cfg.get("api") or {})
        self._config_mtime = mtime
        log("检测到 config.json 变化，已重新加载 API 配置：%s" % self.describe())
        return True

    @property
    def configured(self):
        return bool(self.api_key)

    def describe(self):
        if not self.configured:
            return "未配置（api.api_key 为空）"
        return "已配置（模型 %s，Key %s，来源 %s）" % (self.model, mask_key(self.api_key), self.key_source)

    async def start(self):
        import aiohttp
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout_s))
        return True

    async def stop(self):
        if self._session is not None:
            try:
                await self._session.close()
            finally:
                self._session = None

    def reset(self, session_id):
        """清空某个会话的上下文（发 /new 时用）。"""
        self.histories.pop(session_id, None)

    def _system_text(self, dsh_offline=True):
        mode_block = API_MODE_DSH_OFF if dsh_offline else API_MODE_FORCED
        return (self.system_prompt
                .replace("{mode_block}", mode_block)
                .replace("{time}", time.strftime("%Y-%m-%d %H:%M:%S")))

    def _trim(self, history):
        if self.history_max_turns < 1 or self.history_max_chars < 1000:
            return
        while len(history) > self.history_max_turns * 2:
            del history[0:2]
        total = sum(len(m.get("content") or "") for m in history)
        while total > self.history_max_chars and len(history) >= 2:
            total -= len(history[0].get("content") or "") + len(history[1].get("content") or "")
            del history[0:2]

    @staticmethod
    def _friendly_error(status, text):
        detail = ""
        try:
            detail = ((json.loads(text or "{}").get("error") or {}).get("message") or "")[:200]
        except Exception:  # noqa: BLE001
            detail = (text or "")[:200]
        if status == 401:
            return "DeepSeek API Key 无效或已失效（HTTP 401）。在电脑上双击项目里的 set-api-key.cmd 重新填一下。"
        if status == 402:
            return "DeepSeek API 余额不足（HTTP 402）。请到 platform.deepseek.com 充值后再试。"
        if status == 429:
            return "DeepSeek API 请求太频繁（HTTP 429），过一会儿再发一次。"
        if status >= 500:
            return "DeepSeek API 服务暂时不可用（HTTP %d），稍后再试。" % status
        return "DeepSeek API 调用失败（HTTP %d）：%s" % (status, detail or "未知错误")

    async def ask(self, session_id, text, timeout_ms, dsh_offline=True):
        """纯聊天：返回模型回复文本。不经过 DSH，不能操作电脑。

        dsh_offline=True 表示是「DSH 没开」的兜底场景；False 表示用户强制 engine=api。
        """
        import aiohttp
        self.reload_if_changed()  # 运行中改了 config.json（如刚填 Key）也能立刻生效
        if not self.configured:
            raise ApiError(
                "电脑端 DSH 没开，而且还没有配置 DeepSeek API Key，没法回复。\n"
                "在电脑上双击项目里的 set-api-key.cmd 填一个 Key 就行（一次填好，之后都生效）。")
        await self.start()
        history = self.histories.setdefault(session_id, [])
        messages = [{"role": "system", "content": self._system_text(dsh_offline)}]
        messages.extend(history)
        messages.append({"role": "user", "content": text})
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if "reasoner" not in self.model.lower():  # reasoner 模型不支持 temperature 等采样参数
            payload["temperature"] = self.temperature
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        url = self.base_url + "/chat/completions"
        timeout = aiohttp.ClientTimeout(total=max(10.0, min(self.timeout_s, timeout_ms / 1000.0 + 30)))
        try:
            async with self._session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
                raw = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise ApiError("连不上 DeepSeek API（网络问题）：%r" % (e,)) from e
        if status != 200:
            raise ApiError(self._friendly_error(status, raw))
        try:
            data = json.loads(raw)
            message = ((data.get("choices") or [{}])[0].get("message") or {})
            content = (message.get("content") or "").strip()
            if not content:  # 兼容 reasoner 等把正文放在 reasoning_content 的情况
                content = (message.get("reasoning_content") or "").strip()
        except Exception as e:  # noqa: BLE001
            raise ApiError("DeepSeek API 返回内容解析失败：%r" % (e,)) from e
        if not content:
            raise ApiError("DeepSeek API 返回了空内容，换个说法再试一次。")
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": content})
        self._trim(history)
        return content


# --------------------------------------------------------------------------- DSH 执行端：独立常驻 runtime（备用）
class DshWorker:
    """把 Node 的 dsh_worker.js 包成 asyncio 一问一答客户端。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.proc = None
        self.lock = asyncio.Lock()
        self.seq = 0
        self._stderr_task = None

    def _env(self):
        d = self.cfg.get("dsh") or {}
        env = dict(os.environ)
        env["ELECTRON_RUN_AS_NODE"] = "1"
        env["DSH_NODE_EXECUTABLE"] = d.get("node_executable", env.get("DSH_NODE_EXECUTABLE", ""))
        env["DSH_INSTALL_DIR"] = d.get("install_dir", env.get("DSH_INSTALL_DIR", ""))
        env["DSH_HOME"] = d.get("home", env.get("DSH_HOME", ""))
        env["DSH_BRIDGE_PROFILE"] = d.get("profile", "wechat")
        env["DSH_BRIDGE_PROVIDER"] = d.get("provider", "deepseek-account")
        env["DSH_BRIDGE_MODEL"] = d.get("model", "deepseek-flash")
        env["DSH_BRIDGE_CWD"] = d.get("cwd", HERE)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return env

    async def is_available(self, force=False):
        """sdk 模式没有轻量探测手段，直接返回 True；
        真出问题时 ask() 会抛错，由上层自动兜底到 DeepSeek API。"""
        return True

    async def start(self):
        if self.proc and self.proc.returncode is None:
            return
        d = self.cfg.get("dsh") or {}
        node = d.get("node_executable") or os.environ.get("DSH_NODE_EXECUTABLE")
        if not node or not os.path.exists(node):
            raise RuntimeError("找不到 DSH 的 node 可执行文件（config.json -> dsh.node_executable）")
        if not os.path.exists(WORKER_PATH):
            raise RuntimeError("找不到 dsh_worker.js")
        cwd = d.get("cwd") or HERE
        log("启动 DSH worker：%s" % node)
        self.proc = await asyncio.create_subprocess_exec(
            node, WORKER_PATH,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=self._env(),
            limit=1024 * 1024,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        # 预热：让 DSH runtime 完成 initialize
        t0 = time.time()
        await self._request({"id": 0, "cmd": "ping"}, timeout=120)
        log("DSH worker 就绪（%.1fs）" % (time.time() - t0))

    async def _drain_stderr(self):
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip()
                if text and not text.startswith("[dsh-worker] initializing"):
                    log("[dsh] " + text[:500])
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            log("[dsh] stderr 读取结束: %r" % (e,))

    async def _readline(self, timeout):
        return await asyncio.wait_for(self.proc.stdout.readline(), timeout=timeout)

    async def _request(self, payload, timeout):
        if not self.proc or self.proc.returncode is not None:
            raise RuntimeError("DSH worker 未运行")
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        self.proc.stdin.write(raw)
        await self.proc.stdin.drain()
        deadline = time.time() + timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise asyncio.TimeoutError("DSH 回复超时")
            line = await self._readline(remain)
            if not line:
                raise RuntimeError("DSH worker 已退出")
            try:
                obj = json.loads(line.decode("utf-8", "replace").strip())
            except Exception:  # noqa: BLE001
                continue
            if obj.get("id") == payload.get("id"):
                return obj

    async def ask(self, session, text, timeout_ms):
        async with self.lock:
            if not self.proc or self.proc.returncode is not None:
                await self.start()
            self.seq += 1
            payload = {
                "id": self.seq,
                "session": session,
                "text": text,
                "timeoutMs": int(timeout_ms),
            }
            try:
                res = await self._request(payload, timeout=timeout_ms / 1000.0 + 60)
            except Exception as e:  # noqa: BLE001
                log("DSH 调用失败：%r，重启 worker" % (e,))
                await self.stop()
                raise
            if not res.get("ok"):
                raise RuntimeError(res.get("error") or "DSH 返回失败")
            return res.get("text") or ""

    async def stop(self):
        proc = self.proc
        self.proc = None
        if self._stderr_task:
            self._stderr_task.cancel()
            self._stderr_task = None
        if not proc:
            return
        try:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- 桥接主逻辑
def session_for(chat_key, extra=""):
    digest = hashlib.sha1((chat_key + "|" + (extra or "")).encode("utf-8")).hexdigest()[:16]
    return "wecom-" + digest


def split_chunks(text, size):
    text = text or ""
    if size <= 0:
        return [text]
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


class Bridge:
    # 每轮都带给远端 agent 的提示：让它知道自己在被手机遥控、以及怎么把文件发回手机
    AGENT_HINT = (
        "[系统提示：你正在被手机端的用户通过企业微信远程指挥，你的回复会直接发到手机聊天里（纯文本/Markdown）。"
        "如果需要把电脑上的文件发给用户，请在回复里另起一行写 [[SEND_FILE:文件的绝对路径]]（可写多个），"
        "桥接会自动上传并发送；不要在回复正文里解释这个标记。]\n\n"
    )
    FILE_MARK_RE = re.compile(r"\[\[SEND_FILE[:：]\s*(.+?)\s*\]\]")
    # ---- 自然语言指令识别（语音消息会先被企业微信转成文字，所以语音同样适用）----
    STOP_PHRASES = {
        "停止", "停下", "停下来", "停下来吧", "停一下", "先停", "停", "停掉",
        "停止当前任务", "停止任务", "取消任务", "取消当前任务", "中断任务", "中断当前任务",
        "别做了", "不用做了", "不要做了", "停了吧", "先停下来", "先停止",
        "算了别做了", "算了不做了", "别做了吧", "先不做了",
    }
    STATUS_PHRASES = {
        "状态", "现在状态", "当前状态", "查看状态", "你的状态", "现在的状态", "版本", "版本号",
        "在干嘛", "你在干嘛", "你在做什么", "你在干什么", "在忙什么", "状态怎么样",
    }
    _NORM_RE = re.compile(r"[\s，。！？、,.!?~～…;；:：\"'“”‘’()（）\[\]【】]+")

    def __init__(self, cfg):
        self.cfg = cfg
        dsh_cfg = cfg.get("dsh") or {}
        mode = (dsh_cfg.get("mode") or "web").lower()
        if mode == "sdk":
            log("执行端模式：独立 runtime（sdk）")
            self.worker = DshWorker(cfg)
            self.session_scope = (dsh_cfg.get("cwd") or "sdk")
        else:
            log("执行端模式：驱动桌面版 DSH（web api）")
            self.worker = WebApiWorker(cfg)
            # 工作目录也进会话 id：目录换了自动开新会话，避免老会话把文件写回旧位置
            self.session_scope = self.worker.cwd
        self.client = None
        self.chat_locks = {}
        self.sessions = {}
        self.session_tag = str(cfg.get("session_tag") or "")
        # 执行端：auto=先探 DSH，不在线就用 DeepSeek API；dsh / api=强制
        self.engine_mode = (cfg.get("engine") or "auto").lower()
        if self.engine_mode not in ("auto", "dsh", "api"):
            self.engine_mode = "auto"
        api_cfg = cfg.get("api") or {}
        self.api_worker = DeepSeekApiWorker(cfg) if api_cfg.get("enabled", True) else None
        self.api_timeout_ms = safe_int(api_cfg.get("timeout_ms"), 180000, 10000)
        self.api_switch_note = api_cfg.get("switch_note") or (
            "⚠️ 电脑端 DSH 没开，本条由 DeepSeek API 直接回复（只能聊天，不能操作电脑）。\n\n")
        self.last_engine = {}   # chat_key -> 上次用的引擎（dsh/api），用来只提示一次切换
        # ---- 运行中任务控制（/stop 中断）与长任务主动推送 ----
        self.active_tasks = {}      # chat_key -> asyncio.Task（正在跑的推理任务）
        self.active_engine = {}     # chat_key -> 当前任务实际用的引擎（dsh/api）
        self.cancel_requested = set()   # 已请求停止的 chat_key
        self.chat_targets = {}      # chat_key -> 主动消息目标（chatid 或 userid）
        self.stream_starts = {}     # chat_key -> 本条消息流式回复开始时间
        self.task_meta = {}         # chat_key -> 当前任务的元信息（开始时间、指令摘要），用于完成通知
        self._auto_approve_done = False   # 是否已把 DSH 默认权限设为「自动允许」
        self.started_at = time.time()
        self._prep_task = None
        log("引擎模式：%s；DeepSeek API：%s" % (
            self.engine_mode,
            self.api_worker.describe() if self.api_worker else "已禁用（api.enabled=false）"))
        reply_cfg = cfg.get("reply") or {}
        self.thinking_text = reply_cfg.get("thinking_text", "已收到，正在处理…")
        self.max_chunk = safe_int(reply_cfg.get("max_chunk"), 1800, 200)
        self.timeout_ms = safe_int(reply_cfg.get("timeout_ms") or dsh_cfg.get("timeout_ms"), 900000, 5000)
        self.new_chat_words = set(reply_cfg.get("new_chat_words") or ["/new"])
        self.new_chat_words_norm = {str(w).strip().lower() for w in self.new_chat_words}
        self.status_words = set(reply_cfg.get("status_words") or ["/status", "/状态"])
        self.help_words = set(reply_cfg.get("help_words") or ["/help", "/帮助", "/?"])
        # 停止命令：长任务跑着时也能立即生效（不排队）
        self.stop_words = set(reply_cfg.get("stop_words") or ["/stop", "/停止", "停止任务", "取消任务"])

    def _chat_lock(self, chat_key):
        lock = self.chat_locks.get(chat_key)
        if lock is None:
            lock = asyncio.Lock()
            self.chat_locks[chat_key] = lock
        return lock

    def _session(self, chat_key, reset=False):
        cur = self.sessions.get(chat_key)
        if reset or not cur:
            if reset and cur and self.api_worker:
                self.api_worker.reset(cur)            # 清掉 API 模式下的旧上下文
                self.api_worker.reset(cur + ":api")   # API 模式用独立 key，避免与 DSH 上下文混用
            extra = (self.session_tag + "|" if self.session_tag else "") + self.session_scope
            if reset:
                extra += "|" + str(time.time())
            cur = session_for(chat_key, extra)
            self.sessions[chat_key] = cur
            log("会话 %s -> %s（工作目录 %s）" % (chat_key, cur, self.session_scope))
        return cur

    # -------------------------------------------------------------- 执行端选择
    async def _ensure_auto_approve(self):
        """把 DSH 的默认权限预设设为 danger-full-access（= 新会话自动允许、不弹审批）。

        手机遥控时没人能点「允许」，所以桥接启动时（以及首次真正用 DSH 前）自动落实一次；
        config.json -> dsh.auto_approve=false 可关掉。调用幂等，失败不影响主流程。
        """
        if self._auto_approve_done:
            return
        dsh_cfg = self.cfg.get("dsh") or {}
        if dsh_cfg.get("auto_approve", True) is False:
            return
        rpc = getattr(self.worker, "_rpc", None)
        if not callable(rpc):
            return
        try:
            await rpc("settings/update", {"ns": "permission",
                                          "patch": {"defaultPreset": "danger-full-access"}})
            self._auto_approve_done = True
            log("已设置 DSH 默认权限：danger-full-access（新会话自动允许，不再弹审批）")
        except Exception as e:  # noqa: BLE001
            log("设置 DSH 默认权限失败（不影响聊天）：%r" % (e,))

    async def _sniff_engine(self):
        """返回本条消息该用的执行端：'dsh' 或 'api'。"""
        if self.engine_mode == "dsh":
            return "dsh"
        if self.engine_mode == "api":
            return "api"
        # auto：先探 DSH 是否在线（带缓存，不会每条都慢）
        try:
            dsh_ok = await self.worker.is_available()
        except Exception:  # noqa: BLE001
            dsh_ok = False
        return "dsh" if dsh_ok else "api"

    async def _dsh_ask(self, session, text):
        """交给 DSH 执行。

        注意：**不做自动重试**。session/prompt 一旦下发，任务可能已经被 DSH 受理，
        重试会把同一条指令执行两遍（删文件/发消息这类操作很危险）。
        连接类错误由 _ask_with_fallback 自动改用 DeepSeek API 兜底。
        """
        await self._ensure_auto_approve()
        try:
            return await self.worker.ask(session, self.AGENT_HINT + text, self.timeout_ms)
        except (DshUnavailableError, DshTaskError, DshTimeoutError):
            raise
        except Exception as e:  # sdk 模式等旧执行端的通用错误 → 按「不可用」处理
            raise DshUnavailableError("DSH 执行失败：%s" % (e,)) from e

    async def _ask_with_fallback(self, chat_key, session, text):
        """按当前引擎执行：DSH 在线就用 DSH；不在线/连不上才自动用 DeepSeek API 兜底。

        - DSH 在线但任务本身失败/超时：不静默切 API（会误导用户、也可能重复执行），直接报错。
        - API 模式的上下文用独立 session key（session + ":api"），不与 DSH 的会话混用。
        返回 (回复文本, 实际使用的引擎)。
        """
        engine = await self._sniff_engine()
        api_session = session + ":api"
        # 先热加载 API 配置：刚用 set-api-key.cmd 填的 Key 必须本条消息就生效。
        # 必须在下面的 configured 判断之前，否则会先报「还没配置 API Key」（2026-11-24 实测 bug）。
        if self.api_worker is not None:
            try:
                self.api_worker.reload_if_changed()
            except Exception:  # noqa: BLE001
                pass
        self.active_engine[chat_key] = engine
        if engine == "dsh":
            try:
                reply = await self._dsh_ask(session, text)
            except DshUnavailableError as dsh_err:
                log("DSH 不可用：%r" % (dsh_err,))
                mark = getattr(self.worker, "mark_unavailable", None)
                if callable(mark):
                    mark("执行失败：%s" % (str(dsh_err)[:100],))
                if self.api_worker and self.api_worker.configured and self.engine_mode == "auto":
                    log("→ 本条自动改用 DeepSeek API 直连兜底")
                    reply = await self.api_worker.ask(
                        api_session, text, self.api_timeout_ms, dsh_offline=True)
                    engine = "api"
                else:
                    raise
        else:
            if not (self.api_worker and self.api_worker.configured):
                raise ApiError(
                    "电脑端 DSH 未开启，而且还没有配置 DeepSeek API Key，没法回复。\n"
                    "在电脑上双击项目里的 set-api-key.cmd 填一个 Key 即可（或先打开 DSH 桌面版）。")
            reply = await self.api_worker.ask(
                api_session, text, self.api_timeout_ms, dsh_offline=(self.engine_mode != "api"))
        # 引擎切换时给用户一句提示（同一个聊天里每种切换只提示一次）
        last = self.last_engine.get(chat_key)
        if engine == "api" and last != "api":
            if self.engine_mode == "api":
                reply = "（当前是 DeepSeek API 聊天模式，不能操作电脑）\n\n" + reply
            else:
                reply = self.api_switch_note + reply
        elif engine == "dsh" and last == "api":
            reply = "✅ 电脑端已恢复，切换回 DSH 执行。\n\n" + reply
        self.last_engine[chat_key] = engine
        log("本条执行端：%s" % ("DSH（电脑端）" if engine == "dsh" else "DeepSeek API（直连聊天）"))
        return reply, engine

    # -------------------------------------------------------------- 状态 / 帮助
    def _uptime_text(self):
        sec = int(time.time() - self.started_at)
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        if h:
            return "%d 小时 %d 分" % (h, m)
        if m:
            return "%d 分 %d 秒" % (m, s)
        return "%d 秒" % s

    async def _status_text(self, session, chat_key=None):
        mode_desc = {"auto": "auto（自动检测）", "dsh": "dsh（强制用 DSH）",
                     "api": "api（强制纯聊天）"}.get(self.engine_mode, self.engine_mode)
        lines = ["【办公助手 · 状态】", "版本：%s" % BRIDGE_VERSION, "引擎模式：%s" % mode_desc]
        dsh_ok = False
        try:
            dsh_ok = await self.worker.is_available(force=True)
        except Exception:  # noqa: BLE001
            pass
        probe_reason = getattr(self.worker, "_probe_reason", "") or ""
        if dsh_ok:
            lines.append("电脑端 DSH：在线 ✅（任务会真在电脑上执行）")
            base = getattr(self.worker, "base", None)
            if base:
                lines.append("DSH 地址：%s" % base)
        else:
            lines.append("电脑端 DSH：未开启 ❌（%s）" % (probe_reason or "本机没有运行中的 DSH 桌面版"))
        if self.api_worker:
            try:
                self.api_worker.reload_if_changed()  # 填完 Key 不重启也能在 /status 里看到「已配置」
            except Exception:  # noqa: BLE001
                pass
            if self.api_worker.configured:
                lines.append("DeepSeek API：已配置 ✅（模型 %s，Key %s）" % (
                    self.api_worker.model, mask_key(self.api_worker.api_key)))
            else:
                lines.append("DeepSeek API：未配置 ❌（双击 set-api-key.cmd 填 Key）")
        else:
            lines.append("DeepSeek API：已禁用")
        if self.engine_mode == "auto":
            if dsh_ok:
                will = "DSH"
            elif self.api_worker and self.api_worker.configured:
                will = "DeepSeek API"
            else:
                will = "无可用执行端"
            lines.append("现在发消息会由：%s 回复" % will)
        if chat_key:
            inner = self.active_tasks.get(chat_key)
            if inner is not None and not inner.done():
                lines.append("当前任务：执行中（发 /stop 可停止）")
        lines.append("当前会话：%s" % session)
        lines.append("提示：直接说「告诉我状态 / 版本」就能查本信息；"
                     "说「停止 / 停下来吧」可中断正在跑的长任务。")
        lines.append("已运行：%s" % self._uptime_text())
        return "\n".join(lines)

    @staticmethod
    def _help_text():
        return ("【办公助手 · 用法】\n"
                "· 直接发文字 / 语音 = 下指令。\n"
                "  - DSH 桌面版开着：它真的在电脑上执行（跑命令、写文件），结果发回手机；\n"
                "  - DSH 没开：自动改用 DeepSeek API 纯聊天（也能正常回答，但不能操作电脑）。\n"
                "· /new = 开新对话（清空上下文）。\n"
                "· /status = 查看当前走的是 DSH 还是 DeepSeek API。\n"
                "· /stop = 停止正在执行的长任务（电脑上正在跑的命令会被中断）。\n"
                "· /help = 看这份说明。\n"
                "· 要让电脑发文件：直接说“把 XX 文件发我”（仅 DSH 模式可用）。\n"
                "· 不用记斜杠命令：说「帮我加载好办公助手」= 开机后自检就绪；"
                "说「告诉我状态 / 版本」= 查状态；说「停止 / 停下来吧 / 停止当前任务」= 中断正在跑的任务。\n"
                "· 超过 2 分钟的长任务完成时，会单独推送一条「任务完成」通知到手机（写明是哪条指令）。")

    # -------------------------------------------------------------- 自然语言识别
    @classmethod
    def _norm_text(cls, text):
        return cls._NORM_RE.sub("", (text or "")).strip().lower()

    def _has_active_task(self, chat_key):
        inner = self.active_tasks.get(chat_key)
        return inner is not None and not inner.done()

    def _is_stop_intent(self, chat_key, text):
        """自然语言停止：说「停止」「停下来吧」「别做了」等都算（语音转文字后同样有效）。"""
        norm = self._norm_text(text)
        if not norm:
            return False
        if norm in self.STOP_PHRASES:
            return True
        if not self._has_active_task(chat_key):
            return False   # 没有任务在跑时不当成停止，避免误伤正常指令
        return len(norm) <= 24 and any(
            k in norm for k in ("停止", "停下", "取消", "中断", "别做", "不用做", "不要做", "停掉"))

    def _is_status_intent(self, text):
        """自然语言查状态：说「告诉我一下版本号等相关信息」「现在什么状态」都算。"""
        norm = self._norm_text(text)
        if not norm:
            return False
        if norm in self.STATUS_PHRASES:
            return True
        if len(norm) > 30:
            return False
        task_words = ("文件", "文件夹", "桌面", "打开", "关闭", "安装", "下载", "生成", "写",
                      "发我", "帮我", "运行", "执行", "截图", "浏览器", "表格", "文档", "命令",
                      "重启", "关机", "清理", "删除")
        if any(w in norm for w in task_words):
            return False   # 看着像让电脑干活的任务，不拦截
        return any(h in norm for h in ("状态", "版本", "模型", "执行端", "dsh", "api", "status", "version",
                                       "你在干嘛", "你在做什么", "你在干什么", "在忙什么",
                                       "配置好", "连上", "在线"))

    def _is_notify_test_intent(self, text):
        """「测试一下通知」= 当场验证手机主动推送是否生效（不用等长任务）。"""
        norm = self._norm_text(text)
        if not norm or len(norm) > 30:
            return False
        has_test = any(k in norm for k in ("测试", "试一下", "试试", "检验"))
        has_notify = any(k in norm for k in ("通知", "推送", "消息通知"))
        return has_test and has_notify

    def _is_load_intent(self, text):
        norm = self._norm_text(text)
        if "办公助手" not in norm or len(norm) > 40:
            return False
        blocked = ("文件", "文件夹", "桌面", "安装", "下载", "生成", "发我", "运行",
                   "执行", "截图", "浏览器", "表格", "文档", "命令", "重启", "关机",
                   "清理", "删除", "整理", "卸载")
        return not any(w in norm for w in blocked)

    async def _load_text(self, session, chat_key):
        status = await self._status_text(session, chat_key)
        return ("🏢 办公助手已加载好 ✅ 现在可以直接在手机上发消息/语音指挥电脑了。\n"
                "（以后电脑开机后，先发一句「帮我加载好办公助手」就能确认就绪）\n\n" + status)

    # -------------------------------------------------------------- 收消息
    @staticmethod
    def _extract_text(body):
        """从回调 body 里取"要执行的话"。语音消息自带语音转文字结果。"""
        msgtype = body.get("msgtype")
        if msgtype == "text":
            return ((body.get("text") or {}).get("content") or "").strip()
        if msgtype == "voice":
            return ((body.get("voice") or {}).get("content") or "").strip()
        if msgtype == "mixed":
            parts = []
            for item in (((body.get("mixed") or {}).get("msg_item")) or []):
                if isinstance(item, dict) and item.get("msgtype") == "text":
                    parts.append((item.get("text") or {}).get("content") or "")
            return "\n".join(parts).strip()
        return ""

    async def on_message(self, frame):
        body = frame.get("body") or {}
        msgtype = body.get("msgtype") or "?"
        chat_key = self._chat_key_of(body)
        text = self._extract_text(body)
        if not text:
            log("收到暂不支持的消息：%s chat=%s" % (msgtype, chat_key))
            if msgtype == "voice":
                hint = "语音没识别出文字，麻烦再说一次或直接发文字。"
            else:
                hint = "目前只接文字和语音消息（图片/文件暂时还不行）。"
            await self._safe_reply(frame, generate_req_id("stream"), hint)
            return
        log("收到消息(%s) chat=%s len=%d: %s" % (msgtype, chat_key, len(text), text[:120].replace("\n", " ")))
        # 记录主动推送目标（流式消息超过 10 分钟会过期，届时要靠主动推送把结果发出去）
        self.chat_targets[chat_key] = body.get("chatid") or ((body.get("from") or {}).get("userid") or "")
        self.stream_starts[chat_key] = time.time()

        # 停止：/stop 或自然语言（「停止」「停下来吧」「别做了」等）。不排队，长任务跑着也能立即中断
        cmd = text.strip().lower()
        if cmd in self.stop_words or self._is_stop_intent(chat_key, text):
            await self._handle_stop(frame, chat_key)
            return

        # 「测试一下通知」= 当场验证手机主动推送是否生效（不用等长任务）
        if self._is_notify_test_intent(text):
            await self._push_note(chat_key, (
                "🔔 【办公助手】通知测试：如果你在手机上看到这条独立消息，"
                "说明「主动推送」已生效 ✅ 以后 ≥1 分钟的任务完成时，就会发这种通知并写明是哪条任务。"))
            await self._safe_reply(frame, generate_req_id("stream"),
                                   "已发送一条测试通知，请看手机是否收到（应该是单独的一条新消息）。")
            return

        # 「帮我加载好办公助手」= 开机后的一条就绪自检口令（以后电脑打开先发这一句）
        if self._is_load_intent(text):
            session = self._session(chat_key)
            try:
                reply = await self._load_text(session, chat_key)
            except Exception as e:  # noqa: BLE001
                reply = "办公助手自检失败：%r" % (e,)
            await self._reply_long(frame, generate_req_id("stream"), reply, chat_key)
            return

        # 状态 / 帮助：/status、/help，或自然语言（「告诉我一下版本号等相关信息」等）。不排队
        want_status = cmd in self.status_words or self._is_status_intent(text)
        want_help = cmd in self.help_words
        if want_status or want_help:
            session = self._session(chat_key)
            try:
                reply = await self._status_text(session, chat_key) if want_status else self._help_text()
            except Exception as e:  # noqa: BLE001
                reply = "查询状态失败：%r" % (e,)
            await self._reply_long(frame, generate_req_id("stream"), reply, chat_key)
            return

        lock = self._chat_lock(chat_key)
        if lock.locked():
            # 前一条还在跑：先告诉用户在排队，别以为消息没收到
            await self._safe_reply(
                frame, generate_req_id("stream"),
                "收到，上一条指令还在执行，这条已排队；处理完会回复你。发 /stop 可停止上一条。")
        async with lock:
            reset = text.strip().lower() in self.new_chat_words_norm
            session = self._session(chat_key, reset=reset)
            stream_id = generate_req_id("stream")
            try:
                await self.client.reply_stream(frame, stream_id, self.thinking_text, False)
            except Exception as e:  # noqa: BLE001
                log("发送『正在处理』失败：%r" % (e,))
                stream_id = generate_req_id("stream")

            engine_used = None
            if reset:
                reply = "好，开个新对话。你想聊什么？"
            else:
                self.task_meta[chat_key] = {"started": time.time(), "text": text}
                keepalive = asyncio.create_task(self._keepalive(frame, stream_id, chat_key))
                inner = asyncio.create_task(self._ask_with_fallback(chat_key, session, text))
                self.active_tasks[chat_key] = inner
                try:
                    reply, engine_used = await inner
                except asyncio.CancelledError:
                    # /stop 取消了桥接侧等待（API 模式）→ 给用户一个明确结束语
                    reply = "⏹ 已停止当前任务。"
                    engine_used = None
                except Exception as err:  # noqa: BLE001
                    self.task_meta.pop(chat_key, None)
                    if chat_key in self.cancel_requested:
                        self.cancel_requested.discard(chat_key)
                        log("任务已被用户停止（异常也按停止处理）：%r" % (err,))
                        await self._safe_reply(frame, stream_id, "⏹ 已停止当前任务。")
                        return
                    log("处理失败：%s" % (traceback.format_exc(limit=6),))
                    err_text = str(err)
                    if "批准" in err_text or "审批" in err_text:
                        # 「等待电脑上批准」是 DSH 的安全机制，不是执行端故障：原样告知用户
                        await self._safe_reply(frame, stream_id, err_text)
                        return
                    await self._safe_reply(
                        frame, stream_id,
                        "执行端出错了：%s\n（DSH 没开就先用 set-api-key.cmd 配好 DeepSeek API Key；"
                        "DSH 刚重启就等十几秒再发一次）" % (err,))
                    return
                finally:
                    self.active_tasks.pop(chat_key, None)
                    self.active_engine.pop(chat_key, None)
                    keepalive.cancel()
                    try:
                        await keepalive
                    except asyncio.CancelledError:
                        pass
                    except Exception:  # noqa: BLE001
                        pass
                if chat_key in self.cancel_requested:
                    # 已向 DSH 发过 session/cancel：结果统一以「已停止」为准
                    self.cancel_requested.discard(chat_key)
                    reply = "⏹ 已停止当前任务。"
                    engine_used = None

            # 只有 DSH 真的执行过任务，才允许回复里的「发文件」标记生效。
            # API 纯聊天模式绝不能凭聊天内容读本机文件（防止提示注入把文件发出去）。
            if engine_used == "dsh":
                file_paths = self.FILE_MARK_RE.findall(reply)
            else:
                file_paths = []
            clean_reply = self.FILE_MARK_RE.sub("", reply).strip()
            if file_paths and not clean_reply:
                clean_reply = "文件发给你了，请查收。"
            if not clean_reply.strip():
                clean_reply = "（没有生成内容，换个说法再试一次？）"
            log("回复 %d 字%s：%s" % (len(clean_reply), ("，附 %d 个文件" % len(file_paths)) if file_paths else "",
                                  clean_reply[:120].replace("\n", " ")))
            used_push = await self._reply_long(frame, stream_id, clean_reply, chat_key)
            for path in file_paths:
                try:
                    await self._send_file(frame, path)
                except Exception as e:  # noqa: BLE001
                    log("发送文件失败 %s：%r" % (path, e))
                    await self._safe_reply(frame, generate_req_id("stream"), "文件没发出去：%s" % (e,))
            # 任务（≥1 分钟）完成后，再主动推一条「哪个任务完成了」到手机
            # 注意：task_meta 必须在这里才清空，早了通知就发不出去（2026-11-24 17:34 修）
            meta = self.task_meta.pop(chat_key, None) or {}
            elapsed = time.time() - (meta.get("started") or time.time())
            if not used_push and elapsed >= 60 and meta:
                snippet = (meta.get("text") or "").strip().replace("\n", " ")[:40] or "（未记录指令）"
                await self._push_note(chat_key, (
                    "✅ 【办公助手】任务完成：%s\n耗时 %d 分 %d 秒。结果已发在上面，请查看。"
                    % (snippet, int(elapsed // 60), int(elapsed % 60))))

    async def _keepalive(self, frame, stream_id, chat_key):
        """长任务期间刷新「还在处理」，让手机上不像是卡死/掉线。

        企业微信流式消息超过 10 分钟就不能再更新（errcode 846608），
        所以 9 分钟后自动改用「主动推送」回报进度，避免刷一堆报错。
        """
        started = time.time()
        try:
            while True:
                await asyncio.sleep(45)
                elapsed = int(time.time() - started)
                target = self.chat_targets.get(chat_key)
                if elapsed >= 540 and target:
                    try:
                        await self.client.send_message(target, {
                            "msgtype": "markdown",
                            "markdown": {"content": "还在处理中（已 %d 分钟）… 完成后我会单独发一条结果给你。"
                                                    % (elapsed // 60)},
                        })
                    except Exception:  # noqa: BLE001
                        pass
                    await asyncio.sleep(75)
                    continue
                try:
                    await self.client.reply_stream(frame, stream_id, "还在处理中（已 %d 秒）…" % elapsed, False)
                except Exception:  # noqa: BLE001
                    pass
        except asyncio.CancelledError:
            return

    async def on_enter_chat(self, frame):
        chat_key = self._chat_key_of(frame.get("body") or {})
        log("进入会话：%s" % chat_key)
        try:
            await self.client.reply_welcome(frame, {
                "msgtype": "text",
                "text": {"content": "你好，我是办公助手。电脑端 DSH 开着时，我会真的在电脑上执行任务、"
                                    "把结果发回手机；DSH 没开时我会自动改用 DeepSeek API 陪你聊天"
                                    "（能正常回答，但没法操作电脑）。\n"
                                    "发文字或语音都行，不用记斜杠命令：直接说「帮我加载好办公助手」= 开机自检就绪；"
                                    "说「告诉我一下状态/版本」查状态；说「停止 / 停下来吧 / 停止当前任务」中断正在跑的任务；"
                                    "说 /new 开新对话。"},
            })
        except Exception as e:  # noqa: BLE001
            log("欢迎语发送失败：%r" % (e,))

    # -------------------------------------------------------------- 回复
    async def _safe_reply(self, frame, stream_id, text):
        try:
            await self.client.reply_stream(frame, stream_id, text, True)
        except Exception as e:  # noqa: BLE001
            log("回复失败：%r" % (e,))

    async def _reply_long(self, frame, stream_id, text, chat_key=None):
        """发送最终回复；返回 True 表示走了「主动推送」路径（流式已过期）。"""
        started = self.stream_starts.get(chat_key) if chat_key else None
        elapsed = (time.time() - started) if started else 0.0
        if chat_key and elapsed > 570:
            # 企业微信流式消息 10 分钟后不能再更新 → 改用主动推送，保证结果必达
            await self._push_long(chat_key, text)
            return True
        chunks = split_chunks(text, self.max_chunk)
        for i, chunk in enumerate(chunks):
            sid = stream_id if i == 0 else generate_req_id("stream")
            try:
                await self.client.reply_stream(frame, sid, chunk, True)
            except Exception as e:  # noqa: BLE001
                log("第 %d 段回复失败：%r" % (i + 1, e))
                if chat_key and ("846608" in str(e) or "expired" in str(e).lower()):
                    log("流式消息已过期，改用主动推送补发结果")
                    await self._push_long(chat_key, text)
                    return True
            if i + 1 < len(chunks):
                await asyncio.sleep(0.2)
        return False

    async def _push_long(self, chat_key, text):
        """超过 10 分钟流式上限后，用「主动推送」把结果发到手机（不依赖回调帧）。"""
        target = self.chat_targets.get(chat_key)
        if not target:
            log("长任务结果无法主动推送：没有记录会话目标（chat_key=%s）" % chat_key)
            return
        chunks = split_chunks(text, min(self.max_chunk, 4000))
        for i, chunk in enumerate(chunks):
            try:
                await self.client.send_message(target, {
                    "msgtype": "markdown",
                    "markdown": {"content": chunk},
                })
                log("长任务结果已主动推送到手机（第 %d/%d 段）" % (i + 1, len(chunks)))
            except Exception as e:  # noqa: BLE001
                log("主动推送失败（第 %d 段）：%r" % (i + 1, e))
            if i + 1 < len(chunks):
                await asyncio.sleep(0.3)

    async def _push_note(self, chat_key, text):
        """主动推送一条简短通知到手机（不依赖回调帧，用于「任务完成」提醒）。"""
        target = self.chat_targets.get(chat_key)
        if not target:
            log("通知无法主动推送：没有记录会话目标（chat_key=%s）" % chat_key)
            return
        try:
            await self.client.send_message(target, {
                "msgtype": "markdown",
                "markdown": {"content": text},
            })
            log("已主动推送通知到手机：%s" % text[:60].replace("\n", " "))
        except Exception as e:  # noqa: BLE001
            log("主动推送通知失败：%r" % (e,))

    async def _handle_stop(self, frame, chat_key):
        """手机发 /stop：中断当前真正在跑的任务。

        - DSH 模式：调 DSH 的 session/cancel（实测 accepted=true，正在执行的任务会被中断）；
        - API 模式：直接取消桥接侧等待中的协程。
        """
        inner = self.active_tasks.get(chat_key)
        session = self.sessions.get(chat_key)
        if inner is None or inner.done():
            await self._safe_reply(frame, generate_req_id("stream"), "当前没有正在执行的任务。")
            return
        engine = self.active_engine.get(chat_key) or ""
        self.cancel_requested.add(chat_key)
        cancelled = False
        if engine != "api" and session and hasattr(self.worker, "_rpc"):
            try:
                res = await self.worker._rpc("session/cancel", {"request": {"sessionId": session}})
                log("已向桌面版 DSH 发送停止请求（session=%s）：%s" % (session, str(res)[:120]))
                cancelled = True
            except Exception as e:  # noqa: BLE001
                log("DSH 停止请求失败：%r" % (e,))
        if not cancelled:
            inner.cancel()
            log("已取消桥接侧等待（engine=%s）" % (engine or "?",))
        else:
            # DSH 已受理中断；再兜底：若 3 秒内等待还没结束，直接取消等待协程
            asyncio.create_task(self._cancel_task_later(inner, 3.0))
        await self._safe_reply(frame, generate_req_id("stream"), "⏹ 已请求停止当前任务，正在中断…")

    @staticmethod
    async def _cancel_task_later(task, delay):
        """停止兜底：DSH 受理 cancel 后若等待协程还没结束，延时强制取消，避免一直挂着。"""
        try:
            await asyncio.sleep(delay)
            if task is not None and not task.done():
                task.cancel()
                log("停止兜底：已取消等待协程")
        except Exception:  # noqa: BLE001
            pass

    # -------------------------------------------------------------- 发文件
    async def _send_file(self, frame, path):
        """把电脑上的文件通过企业微信发回手机（三步分片上传 → media_id → 回包）。"""
        import hashlib
        raw = str(path).strip().strip('"').strip("'").strip()
        full = os.path.expanduser(raw)
        if not os.path.isabs(full):
            full = os.path.abspath(os.path.join(getattr(self.worker, "cwd", HERE), full))
        if not os.path.isfile(full):
            raise RuntimeError("电脑上找不到这个文件：%s" % full)
        with open(full, "rb") as f:
            data = f.read()
        size = len(data)
        if size == 0:
            raise RuntimeError("文件是空的：%s" % os.path.basename(full))
        if size > 50 * 1024 * 1024:
            raise RuntimeError("文件超过 50MB 上限：%s" % os.path.basename(full))
        ext = os.path.splitext(full)[1].lower()
        image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
        media_type = "image" if ext in image_exts else "file"
        ws = self.client._ws_manager
        chunk_size = 512 * 1024
        total = max(1, (size + chunk_size - 1) // chunk_size)
        md5 = hashlib.md5(data).hexdigest()
        log("上传文件 %s（%.2f MB，%d 片，%s）…" % (os.path.basename(full), size / 1024 / 1024, total, media_type))
        init = await ws.send_reply(
            generate_req_id("aibot_upload_media_init"),
            {"type": media_type, "filename": os.path.basename(full),
             "total_size": size, "total_chunks": total, "md5": md5},
            "aibot_upload_media_init")
        upload_id = ((init or {}).get("body") or {}).get("upload_id")
        if not upload_id:
            raise RuntimeError("上传初始化失败：%r" % (init,))
        for i in range(total):
            chunk = data[i * chunk_size:(i + 1) * chunk_size]
            await ws.send_reply(
                generate_req_id("aibot_upload_media_chunk"),
                {"upload_id": upload_id, "chunk_index": i,
                 "base64_data": base64.b64encode(chunk).decode("ascii")},
                "aibot_upload_media_chunk")
        fin = await ws.send_reply(
            generate_req_id("aibot_upload_media_finish"),
            {"upload_id": upload_id}, "aibot_upload_media_finish")
        media_id = ((fin or {}).get("body") or {}).get("media_id")
        if not media_id:
            raise RuntimeError("上传完成失败：%r" % (fin,))
        await self.client.reply(frame, {"msgtype": media_type, media_type: {"media_id": media_id}})
        log("文件已发到手机：%s（media_id=%s）" % (os.path.basename(full), media_id))
        return media_id

    # -------------------------------------------------------------- 工具
    @staticmethod
    def _chat_key_of(body):
        chatid = body.get("chatid") or ""
        from_user = ((body.get("from") or {}).get("userid")) or body.get("userid") or ""
        return chatid or from_user or "unknown"

    # -------------------------------------------------------------- 生命周期
    async def _prepare_worker_safe(self):
        """后台准备 DSH 的包裹：异常只记日志，不产生未处理任务异常。"""
        try:
            await self._prepare_worker()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log("准备 DSH 时异常：\n" + traceback.format_exc(limit=5))

    async def _prepare_worker(self):
        """启动前准备 DSH：先探是否在线；不在线就跳过启动（绝不卡住企业微信连接）。

        这是「DSH 没开时也能用 API 回复」的关键：以前无条件 await worker.start()，
        DSH 没开时会长时间重试，机器人连企业微信长连接都建不起来。
        """
        if self.engine_mode == "api":
            log("引擎模式为 api：跳过 DSH 启动，直接用 DeepSeek API")
            return
        try:
            dsh_ok = await asyncio.wait_for(self.worker.is_available(force=True), timeout=12)
        except Exception as e:  # noqa: BLE001
            log("DSH 在线探测失败（按未开启处理）：%r" % (e,))
            dsh_ok = False
        if not dsh_ok and not isinstance(self.worker, DshWorker):
            # 网吧机器负载高时探测可能瞬时不稳：启动时多给一次机会
            await asyncio.sleep(0.8)
            try:
                dsh_ok = await asyncio.wait_for(self.worker.is_available(force=True), timeout=12)
            except Exception:  # noqa: BLE001
                dsh_ok = False
        if not dsh_ok:
            mark = getattr(self.worker, "mark_unavailable", None)
            if callable(mark):
                mark("启动时探测不在线")
            if isinstance(self.worker, DshWorker):
                log("sdk 模式执行端未启动（收到消息时会再试）")
            elif self.engine_mode == "auto":
                if self.api_worker and self.api_worker.configured:
                    log("桌面版 DSH 未开启：跳过启动，自动走 DeepSeek API 直连兜底")
                else:
                    log("桌面版 DSH 未开启，且 API Key 未配置：机器人能连上，但发消息会提示先配置")
            else:
                log("桌面版 DSH 未开启（engine=dsh 强制模式，发消息会报错）")
            return
        try:
            timeout = 60 if isinstance(self.worker, DshWorker) else 15
            await asyncio.wait_for(self.worker.start(), timeout=timeout)
            log("DSH 已就绪 ✅")
            await self._ensure_auto_approve()
        except Exception as e:  # noqa: BLE001
            log("DSH 启动失败/超时（按未开启处理）：%r" % (e,))
            mark = getattr(self.worker, "mark_unavailable", None)
            if callable(mark):
                mark("启动失败")

    async def run(self):
        log("启动桥接（DSH 在后台准备，不阻塞企业微信连接）…")
        self._prep_task = asyncio.create_task(self._prepare_worker_safe())
        if self.api_worker:
            try:
                await self.api_worker.start()
            except Exception as e:  # noqa: BLE001
                log("DeepSeek API 初始化失败：%r" % (e,))

        options = WSClientOptions(
            bot_id=self.cfg["bot_id"],
            secret=self.cfg["bot_secret"],
            reconnect_interval=1000,
            max_reconnect_attempts=-1,
            heartbeat_interval=30000,
            logger=SdkLogger(),
        )
        self.client = WSClient(options)

        @self.client.on("connected")
        def _connected():
            log("WebSocket 已连接")

        @self.client.on("authenticated")
        def _authed():
            log("认证成功，机器人已在线 ✅（在手机企业微信里给它发消息即可）")

        @self.client.on("disconnected")
        def _disconnected(reason=None):
            log("连接断开：%r，SDK 会自动重连" % (reason,))

        @self.client.on("reconnecting")
        def _reconnecting(attempt=None):
            log("正在重连（第 %s 次）" % (attempt,))

        @self.client.on("error")
        def _error(error=None):
            log("连接错误：%r" % (error,))

        self.client.on("message", self.on_message)
        self.client.on("event.enter_chat", self.on_enter_chat)

        await self.client.connect()
        await asyncio.Event().wait()

    async def shutdown(self):
        try:
            if self._prep_task and not self._prep_task.done():
                self._prep_task.cancel()
                try:
                    await self._prep_task
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.client:
                self.client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        await self.worker.stop()
        if self.api_worker:
            await self.api_worker.stop()
        try:
            if os.path.exists(PID_PATH):
                os.remove(PID_PATH)
        except Exception:  # noqa: BLE001
            pass


def write_pid():
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


async def async_main():
    cfg = load_config()
    bridge = Bridge(cfg)
    write_pid()
    try:
        await bridge.run()
    finally:
        await bridge.shutdown()
        flush_logs(3.0)


async def async_check():
    """不带企业微信凭证的自检：只验证 DSH 端到端能不能正常问答（有限时，不会挂死）。"""
    cfg = load_config(require_credentials=False)
    with open(EXAMPLE_PATH, "r", encoding="utf-8") as f:
        fallback = json.load(f)
    if not cfg.get("dsh"):
        cfg["dsh"] = fallback.get("dsh") or {}
    log("[自检] 启动执行端…")
    dsh_cfg = cfg.get("dsh") or {}
    worker = DshWorker(cfg) if (dsh_cfg.get("mode") or "web").lower() == "sdk" else WebApiWorker(cfg)
    try:
        try:
            await asyncio.wait_for(worker.start(), timeout=30)
        except asyncio.TimeoutError:
            log("[自检] DSH 启动超时（30 秒）。")
            return
        except DshUnavailableError as e:
            log("[自检] DSH 未开启 / 不可用：%s" % (e,))
            log("[自检] 注意：这不是故障 —— 桥接会自动改用 DeepSeek API 兜底；"
                "用 check.cmd 可看两个执行端的完整状态。")
            return
        t0 = time.time()
        text = await asyncio.wait_for(
            worker.ask("wecom-selftest", "只回答两个字：在线", 120000), timeout=180)
        log("[自检] DSH 回复（%.2fs）：%s" % (time.time() - t0, text))
        if "在线" in text:
            log("[自检] 通过 ✅  DSH 链路正常。填好 bot_id/bot_secret 后运行 start.cmd 即可。")
        else:
            log("[自检] DSH 有回复但内容不符预期，请检查模型配置。")
    except asyncio.TimeoutError:
        log("[自检] 超时：DSH 没有在限定时间内回复。")
    finally:
        try:
            await worker.stop()
        except Exception:  # noqa: BLE001
            pass


async def async_check_all():
    """双执行端自检：检查 DSH 是否在线、DeepSeek API 是否可用，并说明现在会自动用哪个。"""
    cfg = load_config(require_credentials=False)
    with open(EXAMPLE_PATH, "r", encoding="utf-8") as f:
        fallback = json.load(f)
    if not cfg.get("dsh"):
        cfg["dsh"] = fallback.get("dsh") or {}
    if not cfg.get("api"):
        cfg["api"] = fallback.get("api") or {}
    engine_mode = (cfg.get("engine") or "auto").lower()
    log("[自检] 引擎模式：%s" % engine_mode)

    log("[自检] 1/2 检查桌面版 DSH 是否开启…")
    dsh_cfg = cfg.get("dsh") or {}
    is_sdk = (dsh_cfg.get("mode") or "web").lower() == "sdk"
    dsh = DshWorker(cfg) if is_sdk else WebApiWorker(cfg)
    dsh_ok = False
    try:
        dsh_ok = await asyncio.wait_for(dsh.is_available(force=True), timeout=15)
        if dsh_ok:
            log("[自检] DSH：在线 ✅（%s）" % (getattr(dsh, "base", "sdk 模式"),))
        else:
            reason = getattr(dsh, "_probe_reason", "") or "未开启 / 不可达"
            log("[自检] DSH：未开启 ❌（%s）" % reason)
        if is_sdk:
            log("[自检] 提示：sdk 模式不支持轻量探测，上面的结果只代表「可以尝试启动」")
    except Exception as e:  # noqa: BLE001
        log("[自检] DSH：探测异常 ❌ %r" % (e,))
    finally:
        try:
            await dsh.stop()
        except Exception:  # noqa: BLE001
            pass

    log("[自检] 2/2 检查 DeepSeek API…")
    api = DeepSeekApiWorker(cfg)
    api_ok = False
    if not api.configured:
        log("[自检] DeepSeek API：未配置 ❌（config.json -> api.api_key 为空；双击 set-api-key.cmd 填 Key）")
    else:
        try:
            text = await api.ask("wecom-selftest", "只回答两个字：在线", 60000)
            log("[自检] DeepSeek API：正常 ✅ 模型 %s，回复：%s" % (api.model, text[:100].replace("\n", " ")))
            api_ok = True
        except Exception as e:  # noqa: BLE001
            log("[自检] DeepSeek API：调用失败 ❌ %s" % (e,))
        finally:
            await api.stop()

    if engine_mode == "dsh":
        who = "DSH（强制模式；DSH 不在线时会报错，不会走 API）"
    elif engine_mode == "api":
        who = "DeepSeek API（强制模式；纯聊天）"
    elif dsh_ok:
        who = "DSH（自动检测：在线，优先用 DSH）"
    elif api_ok:
        who = "DeepSeek API（自动兜底：DSH 未开）"
    else:
        who = "无可用执行端（先打开 DSH 桌面版，或配置 DeepSeek API Key）"
    log("[自检] 结论：本机现在发消息会由 —— %s" % who)
    if dsh_ok or api_ok or engine_mode == "dsh":
        log("[自检] 完成 ✅")
    else:
        log("[自检] 缺少可用执行端 ❌")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    log("=" * 60)
    if "--check-all" in argv:
        log("企业微信机器人桥接 - 双执行端自检（DSH / DeepSeek API）")
        try:
            asyncio.run(async_check_all())
        except SystemExit:
            raise
        except Exception:  # noqa: BLE001
            log("自检异常：\n" + traceback.format_exc())
        finally:
            flush_logs(3.0)
        return
    if "--check" in argv:
        log("企业微信机器人桥接 - 自检模式")
        try:
            asyncio.run(async_check())
        except SystemExit:
            raise
        except Exception:  # noqa: BLE001
            log("自检异常：\n" + traceback.format_exc())
        finally:
            flush_logs(3.0)
        return
    log("企业微信机器人桥接启动")
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        log("收到 Ctrl+C，退出")
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        log("异常退出：\n" + traceback.format_exc())
    finally:
        flush_logs(3.0)


if __name__ == "__main__":
    main()
