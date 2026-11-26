#!/usr/bin/env node
'use strict';
/**
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 The WeCom Office Assistant contributors
 *
 * 非官方社区实现：不包含 DeepSeek Harness 的源码，只通过其本机 SDK stdio JSON-RPC 通信；
 * 协议未公开、未获官方支持，可能随 DSH 版本失效（验证基线：DSH 0.2.2-rc.5）。
 * 使用前请自行确认 DSH 的许可条款允许此类本机自动化集成。
 *
 * DeepSeek Harness SDK-stdio bridge (probe / minimal reusable sample).
 *
 * Spawns one long-lived `dsh <profile>` SDK JSON-RPC runtime (newline-delimited
 * JSON-RPC 2.0 on stdio) and exposes ask(sessionId, text) -> final assistant text.
 * Credentials come from the shared DSH_HOME managed credential store, so this
 * profile reuses the Desktop (GUI) sign-in.
 *
 * Transport facts (verified against DSH 0.2.2-rc.5):
 *   initialize            {cwd, provider, model, maxTokens?} -> {serverInfo}
 *   session/resume        {sessionId}  (required before reusing a persisted session in a new process)
 *   session/query         {sessionId, contentBlocks:[{type:'text',text}]}
 *                         -> {kind:'prompt', messageId} | {kind:'command', execution}
 *   shutdown              -> {}
 *   notifications         session.event (every log event, unfiltered),
 *                         session.status {status:'running'|'idle'},
 *                         session.query.completed {finalResponse} for commands.
 *
 * Completion rule used here: after a prompt is queued, wait for the next
 * `turn/end` session event and return the last non-empty assistant/message text
 * committed before it. Command queries wait for session.query.completed.
 *
 * Usage as a script:
 *   powershell.exe -NoProfile -ExecutionPolicy Bypass -File probe\run-bridge.ps1 "你好" [--session wechat-demo] [--timeout 180]
 * Usage as a library:
 *   const { DshBridge } = require('./dsh-bridge.js');
 */

const { spawn } = require('node:child_process');
const path = require('node:path');

const DEFAULT_OPTS = {
  profile: process.env.DSH_BRIDGE_PROFILE || 'wechat',
  provider: process.env.DSH_BRIDGE_PROVIDER || 'deepseek-account',
  model: process.env.DSH_BRIDGE_MODEL || 'deepseek-flash',
  maxTokens: Number(process.env.DSH_BRIDGE_MAX_TOKENS || 8192),
  cwd: process.env.DSH_BRIDGE_CWD || process.cwd(),
  requestTimeoutMs: 60000,
  askTimeoutMs: Number(process.env.DSH_BRIDGE_ASK_TIMEOUT_MS || 300000),
  log: () => {},
};

function nowMs() { return Date.now(); }

class DshBridge {
  /** @param {Partial<typeof DEFAULT_OPTS>} opts */
  constructor(opts = {}) {
    this.opts = { ...DEFAULT_OPTS, ...opts };
    this.child = null;
    this.buf = '';
    this.nextId = 1;
    this.pending = new Map(); // jsonrpc id -> {resolve, reject, method}
    this.knownSessions = new Set();
    this.queues = new Map(); // sessionId -> tail promise (one ask at a time per session)
    this.currentTurnEnd = new Map(); // sessionId -> highest ended turn number
    this.startPromise = null;
    this.closed = false;
  }

  log(...args) { this.opts.log(...args); }

  /** Spawn the runtime and complete the `initialize` handshake. Idempotent. */
  start() {
    if (this.startPromise) return this.startPromise;
    this.startPromise = this._start().catch((error) => {
      this.startPromise = null;
      throw error;
    });
    return this.startPromise;
  }

  async _start() {
    const exe = process.env.DSH_NODE_EXECUTABLE;
    const installDir = process.env.DSH_INSTALL_DIR;
    if (!exe || !installDir) throw new Error('DSH_NODE_EXECUTABLE / DSH_INSTALL_DIR are not set');
    const bin = path.join(installDir, 'lib', 'bin.js');
    const child = spawn(exe, [bin, this.opts.profile], {
      cwd: this.opts.cwd,
      env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    this.child = child;
    this.buf = '';
    this.stderrTail = '';
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => {
      this.stderrTail = (this.stderrTail + chunk).slice(-8000);
      this.log('stderr', chunk.trim());
    });
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk) => this._onData(chunk));
    child.on('exit', (code, signal) => this._onExit(code, signal));
    child.stdin.on('error', () => {});
    this.log('spawned pid', child.pid, 'profile', this.opts.profile);

    const result = await this.request('initialize', {
      cwd: this.opts.cwd,
      provider: this.opts.provider,
      model: this.opts.model,
      maxTokens: this.opts.maxTokens,
    });
    this.log('initialized', result && result.serverInfo);
    return this;
  }

  _onData(chunk) {
    this.buf += chunk;
    let index;
    while ((index = this.buf.indexOf('\n')) >= 0) {
      const line = this.buf.slice(0, index).trim();
      this.buf = this.buf.slice(index + 1);
      if (!line) continue;
      let frame;
      try { frame = JSON.parse(line); } catch (error) {
        this.log('non-JSON stdout line', line.slice(0, 300));
        continue;
      }
      this._onFrame(frame);
    }
  }

  _onFrame(frame) {
    if (frame.id !== undefined && frame.method === undefined) {
      const pending = this.pending.get(frame.id);
      if (!pending) return;
      this.pending.delete(frame.id);
      if (frame.error) {
        const error = new Error(frame.error.message || 'JSON-RPC error');
        error.code = frame.error.code;
        error.data = frame.error.data;
        pending.reject(error);
      } else {
        pending.resolve(frame.result);
      }
      return;
    }
    const method = frame.method;
    const params = frame.params || {};
    if (method === 'session.event') {
      const event = params.event || {};
      const sessionId = params.sessionId;
      if (event.type === 'turn/end' && event.data && typeof event.data.turn === 'number') {
        const previous = this.currentTurnEnd.get(sessionId) || 0;
        if (event.data.turn > previous) this.currentTurnEnd.set(sessionId, event.data.turn);
      }
      this._dispatch(sessionId, { kind: 'event', event });
      return;
    }
    if (method === 'session.status') {
      this._dispatch(params.sessionId, { kind: 'status', status: params.status });
      return;
    }
    if (method === 'session.query.completed') {
      this._dispatch(params.sessionId, { kind: 'query-completed', params });
      return;
    }
    if (frame.id !== undefined && method !== undefined) {
      // Server -> client request (approval/request, user-question/ask). This bridge
      // advertises neither capability, so answer with a JSON-RPC error instead of hanging.
      this.log('server->client request refused', method, JSON.stringify(params).slice(0, 300));
      this._send({ jsonrpc: '2.0', id: frame.id, error: { code: -32601, message: `bridge refuses ${method}` } });
    }
  }

  _dispatch(sessionId, item) {
    const waiter = this.waiters && this.waiters.get(sessionId);
    if (waiter) waiter.push(item);
  }

  _send(obj) {
    if (!this.child || this.child.killed) throw new Error('dsh runtime is not running');
    this.child.stdin.write(JSON.stringify(obj) + '\n');
  }

  request(method, params) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, method });
      const timer = setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error(`JSON-RPC ${method} timed out`));
      }, this.opts.requestTimeoutMs);
      const done = (fn) => (value) => { clearTimeout(timer); fn(value); };
      this.pending.set(id, { resolve: done(resolve), reject: done(reject), method });
      try { this._send({ jsonrpc: '2.0', id, method, params }); } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      }
    });
  }

  _onExit(code, signal) {
    this.log('runtime exited', code, signal);
    this.child = null;
    this.startPromise = null;
    const error = new Error(`dsh runtime exited (code=${code} signal=${signal}): ${this.stderrTail.trim().slice(-1200)}`);
    for (const [, pending] of this.pending) pending.reject(error);
    this.pending.clear();
    if (this.waiters) {
      for (const [, waiter] of this.waiters) waiter.fail(error);
      this.waiters.clear();
    }
  }

  /**
   * Ask one user message on one durable session and resolve with the final answer.
   * @param {string} sessionId durable session id (reuse it for conversation continuity)
   * @param {string} text user message text
   * @param {{timeoutMs?: number, create?: boolean}} [options]
   * @returns {Promise<{text: string, finishReason: string, turn: number|null, elapsedMs: number}>}
   */
  async ask(sessionId, text, options = {}) {
    if (!sessionId) throw new Error('sessionId is required');
    if (typeof text !== 'string' || text.trim() === '') throw new Error('text is required');
    const previous = this.queues.get(sessionId) || Promise.resolve();
    const run = previous.then(() => this._askOnce(sessionId, text, options));
    const tail = run.catch(() => {});
    this.queues.set(sessionId, tail);
    tail.then(() => { if (this.queues.get(sessionId) === tail) this.queues.delete(sessionId); });
    return run;
  }

  async _askOnce(sessionId, text, options) {
    await this.start();
    const timeoutMs = options.timeoutMs || this.opts.askTimeoutMs;
    const created = await this._ensureSession(sessionId, options.create !== false);
    const baselineTurn = this.currentTurnEnd.get(sessionId) || 0;

    const collector = this._collector(sessionId);
    this.waiters = this.waiters || new Map();
    this.waiters.set(sessionId, collector);

    const startedAt = nowMs();
    let timer;
    try {
      const query = await this.request('session/query', {
        sessionId,
        contentBlocks: [{ type: 'text', text }],
      });
      this.log('query receipt', JSON.stringify(query));
      const outcome = await Promise.race([
        this._waitForCompletion(sessionId, collector, query, baselineTurn, created),
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(Object.assign(new Error(`ask timed out after ${timeoutMs}ms`), { timeout: true })), timeoutMs);
        }),
      ]);
      return {
        text: outcome.text,
        finishReason: outcome.finishReason,
        turn: outcome.turn,
        elapsedMs: nowMs() - startedAt,
      };
    } catch (error) {
      if (error && error.timeout) await this._kill('ask timeout').catch(() => {});
      throw error;
    } finally {
      clearTimeout(timer);
      if (this.waiters && this.waiters.get(sessionId) === collector) this.waiters.delete(sessionId);
    }
  }

  async _ensureSession(sessionId, create) {
    if (this.knownSessions.has(sessionId)) return false;
    try {
      await this.request('session/resume', { sessionId });
      this.knownSessions.add(sessionId);
      this.log('resumed session', sessionId);
      return false;
    } catch (error) {
      if (!create) throw error;
      this.log('resume did not find a session, will create:', sessionId, '->', error.message);
      this.knownSessions.add(sessionId);
      return true;
    }
  }

  _collector(sessionId) {
    const items = [];
    return {
      items,
      push(item) { items.push(item); },
      fail(error) { items.push({ kind: 'failure', error }); },
    };
  }

  async _waitForCompletion(sessionId, collector, query, baselineTurn, isNewSession) {
    const isCommand = query && query.kind === 'command';
    const commandId = isCommand && query.execution ? query.execution.commandId : undefined;
    let lastText = '';
    let finishReason = 'unknown';
    let endTurn = null;
    let processTimer = null;
    try {
      for (;;) {
        await new Promise((resolve, reject) => {
          const tick = () => {
            // inspect buffered notifications
            while (collector.items.length > 0) {
              const item = collector.items.shift();
              if (item.kind === 'failure') { reject(item.error); return; }
              if (item.kind === 'event') {
                const event = item.event;
                if (event.type === 'assistant/message' && event.data && event.data.message) {
                  const text = assistantText(event.data.message);
                  if (text) lastText = text;
                } else if (event.type === 'turn/end') {
                  const turn = event.data ? event.data.turn : 0;
                  if (isCommand) continue;
                  if (turn > baselineTurn && event.data.reason) {
                    finishReason = event.data.reason.kind || 'unknown';
                    endTurn = turn;
                    resolve();
                    return;
                  }
                }
              } else if (item.kind === 'query-completed') {
                const params = item.params || {};
                if (!isCommand) continue;
                if (commandId !== undefined && String(params.commandId) !== String(commandId)) continue;
                lastText = typeof params.finalResponse === 'string' ? params.finalResponse : '';
                finishReason = params.finishReason || 'completed';
                resolve();
                return;
              }
            }
            processTimer = setTimeout(tick, 20);
          };
          tick();
        });
        break;
      }
    } finally {
      if (processTimer) clearTimeout(processTimer);
    }
    return { text: lastText, finishReason, turn: endTurn };
  }

  /** Send shutdown, then close pipes; resolves after the process exits. */
  async close() {
    if (this.closed) return;
    this.closed = true;
    const child = this.child;
    if (!child) return;
    try {
      await Promise.race([
        this.request('shutdown', undefined),
        new Promise((resolve) => setTimeout(resolve, 10000)),
      ]);
    } catch (error) {
      this.log('shutdown request failed', error.message);
    }
    await new Promise((resolve) => {
      if (!child || child.exitCode !== null) return resolve();
      const timer = setTimeout(() => { try { child.kill(); } catch {} resolve(); }, 10000);
      child.once('exit', () => { clearTimeout(timer); resolve(); });
    });
  }

  async _kill(reason) {
    const child = this.child;
    this.log('killing runtime:', reason);
    if (child) { try { child.kill(); } catch {} }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

function assistantText(message) {
  const content = message && message.content;
  if (!Array.isArray(content)) return '';
  let out = '';
  for (const block of content) {
    if (block && block.type === 'text' && typeof block.text === 'string') out += block.text;
  }
  return out;
}

async function main() {
  const argv = process.argv.slice(2);
  const args = { session: 'wechat-demo', timeout: undefined, message: null };
  for (let i = 0; i < argv.length; i++) {
    const value = argv[i];
    if (value === '--session') args.session = argv[++i];
    else if (value === '--timeout') args.timeout = Number(argv[++i]);
    else if (value === '--json') args.json = true;
    else args.message = args.message === null ? value : args.message + ' ' + value;
  }
  if (!args.message) {
    args.message = require('node:fs').readFileSync(0, 'utf8').trim();
  }
  const bridge = new DshBridge({ log: process.env.DSH_BRIDGE_DEBUG ? (...a) => console.error('[bridge]', ...a) : () => {} });
  try {
    const result = await bridge.ask(args.session, args.message, { timeoutMs: args.timeout });
    if (args.json) console.log(JSON.stringify({ sessionId: args.session, ...result }));
    else console.log(result.text);
    if (result.finishReason && result.finishReason !== 'completed') process.exitCode = 2;
  } finally {
    await bridge.close();
  }
}

module.exports = { DshBridge };

if (require.main === module) {
  main().catch((error) => {
    console.error('dsh-bridge error:', error && error.message ? error.message : error);
    process.exit(1);
  });
}
