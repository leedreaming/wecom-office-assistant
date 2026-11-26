#!/usr/bin/env node
'use strict';
/**
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 The WeCom Office Assistant contributors
 *
 * 非官方实现：使用 DeepSeek Harness 未公开的本地 SDK stdio 协议，仅用于个人自动化；
 * DSH 升级后协议可能变化（实测基线：DSH 0.2.2-rc.5），失败时请改用 dsh.mode=web。
 *
 * DSH 常驻桥 worker：从 stdin 读 NDJSON 请求，逐条交给 DSH agent，stdout 回 NDJSON 结果。
 *
 * 请求：{"id":1,"session":"wecom-xxx","text":"你好","timeoutMs":300000}
 * 回包：{"id":1,"ok":true,"text":"...","finishReason":"completed","elapsedMs":1234}
 * 保活：{"id":2,"cmd":"ping"} -> {"id":2,"ok":true,"pong":true}
 *
 * 底层复用 ../probe/dsh-bridge.js（SDK stdio JSON-RPC，profile=wechat，复用桌面版登录）。
 */
const path = require('node:path');
const readline = require('node:readline');

// 使用仓库内自带的 vendor 适配器（可整体搬动目录）
const vendor = path.join(__dirname, 'vendor', 'dsh-bridge.js');
const { DshBridge } = require(vendor);

const log = (...args) => {
  try {
    process.stderr.write('[dsh-worker] ' + args.map((a) => (typeof a === 'string' ? a : JSON.stringify(a))).join(' ') + '\n');
  } catch (_) { /* ignore */ }
};
const out = (obj) => {
  try { process.stdout.write(JSON.stringify(obj) + '\n'); } catch (_) { /* ignore */ }
};

const bridge = new DshBridge({ log });

let chain = Promise.resolve();
const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });

rl.on('line', (line) => {
  chain = chain.then(() => handle(line)).catch((e) => log('handler crashed:', e && e.message));
});

rl.on('close', async () => {
  log('stdin closed, shutting down');
  try { await bridge.close(); } catch (_) { /* ignore */ }
  process.exit(0);
});

process.on('SIGTERM', async () => {
  try { await bridge.close(); } catch (_) { /* ignore */ }
  process.exit(0);
});

async function handle(line) {
  const trimmed = String(line || '').trim();
  if (!trimmed) return;
  let req;
  try {
    req = JSON.parse(trimmed);
  } catch (e) {
    log('bad json:', trimmed.slice(0, 200));
    return;
  }
  if (req.cmd === 'ping') {
    out({ id: req.id, ok: true, pong: true });
    return;
  }
  const startedAt = Date.now();
  try {
    log('ask session=' + req.session + ' len=' + String(req.text || '').length);
    await bridge.start();
    const result = await bridge.ask(req.session, req.text, { timeoutMs: req.timeoutMs || 300000 });
    out({
      id: req.id,
      ok: result.finishReason !== 'error',
      text: result.text || '',
      finishReason: result.finishReason,
      turn: result.turn,
      elapsedMs: Date.now() - startedAt,
    });
  } catch (e) {
    log('ask failed:', e && e.message);
    out({ id: req.id, ok: false, error: String((e && e.message) || e), elapsedMs: Date.now() - startedAt });
  }
}
