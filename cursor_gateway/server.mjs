/**
 * JEEPath Cursor gateway.
 *
 * Speaks the Groq/OpenAI chat protocol on 127.0.0.1 so the tutor can run on Cursor
 * models with no change to its call sites: point GROQ_BASE_URL at this process.
 * Every request runs on a Cursor agent with ALL Cursor tools disabled and an empty
 * scratch directory, so the model can only produce text.
 *
 * Privacy: student messages leave this machine for Cursor's hosted models. This
 * process never logs message content, only model, timings, token counts, tool names.
 */
import http from "node:http";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Agent } from "@cursor/sdk";
import { renderPrompt, parseAssistant, repairPrompt, completion, chunk, SSE_DONE } from "./protocol.mjs";

const env = process.env;
const die = (msg) => { console.error(`[gateway] ${msg}`); process.exit(1); };

const CURSOR_API_KEY = env.CURSOR_API_KEY;
const GATEWAY_TOKEN = env.GATEWAY_TOKEN;
if (!CURSOR_API_KEY) die("CURSOR_API_KEY is not set.");
if (!GATEWAY_TOKEN || GATEWAY_TOKEN.length < 16) die("GATEWAY_TOKEN must be set to a secret of 16+ characters (use the same value as GROQ_API_KEY in the agent).");

const HOST = "127.0.0.1"; // loopback only: anyone who can reach this spends the Cursor plan
const PORT = Number(env.PORT || 8787);
const DEFAULT_MODEL = env.CURSOR_MODEL || "grok-4.6";
let MODEL_MAP = {};
try { MODEL_MAP = JSON.parse(env.CURSOR_MODEL_MAP || "{}"); } catch { die("CURSOR_MODEL_MAP is not valid JSON."); }
const MAX_CONCURRENCY = Math.max(1, Number(env.MAX_CONCURRENCY || 2)); // each agent costs RAM
const TIMEOUT_MS = Number(env.REQUEST_TIMEOUT_MS || 120000);
const WARMUP = env.CURSOR_WARMUP !== "0"; // the first Cursor call is ~20s slower than the rest
const MAX_BODY_BYTES = 8 * 1024 * 1024;

const WORKDIR = fs.mkdtempSync(path.join(os.tmpdir(), "cursor-gw-"));
const cleanup = () => { try { fs.rmSync(WORKDIR, { recursive: true, force: true }); } catch {} };

const redact = (s) => String(s ?? "").split(CURSOR_API_KEY).join("[redacted]");
const log = (line) => console.log(`${new Date().toISOString()} [gateway] ${redact(line)}`);

class GatewayError extends Error {
  constructor(status, message, code = "api_error", type = "api_error") {
    super(redact(message));
    this.status = status; this.code = code; this.type = type;
  }
}

/** Map a Cursor failure onto the strings utils/llm.py already reacts to. */
function classify(message) {
  const m = String(message ?? "").toLowerCase();
  if (/rate.?limit|quota|usage limit|too many|spend limit|out of (credits|requests)/.test(m))
    return new GatewayError(429, `Rate limit reached on the Cursor plan: ${message}`, "rate_limit_exceeded", "rate_limit_error");
  if (/cannot use this model|unknown model|model .*not found|not available/.test(m))
    return new GatewayError(404, `The model does not exist or you do not have access to it: ${message}`, "model_not_found", "invalid_request_error");
  if (/unauthori[sz]ed|invalid api key|authentication/.test(m))
    return new GatewayError(401, `Cursor rejected the API key: ${message}`, "invalid_api_key", "authentication_error");
  return new GatewayError(502, `Cursor run failed: ${message}`);
}

class Semaphore {
  constructor(n) { this.free = n; this.queue = []; }
  async acquire() { if (this.free > 0) { this.free--; return; } await new Promise((r) => this.queue.push(r)); }
  release() { const next = this.queue.shift(); if (next) next(); else this.free++; }
  get waiting() { return this.queue.length; }
}
const slots = new Semaphore(MAX_CONCURRENCY);

const addUsage = (a, b) => ({
  inputTokens: (a?.inputTokens ?? 0) + (b?.inputTokens ?? 0),
  outputTokens: (a?.outputTokens ?? 0) + (b?.outputTokens ?? 0),
});

/** One request's cancellation state: wall-clock timeout and client disconnect. */
function makeContext() {
  const ctx = { run: null, timedOut: false, closed: false };
  ctx.stop = () => { try { Promise.resolve(ctx.run?.cancel?.()).catch(() => {}); } catch {} };
  ctx.timer = setTimeout(() => { ctx.timedOut = true; ctx.stop(); }, TIMEOUT_MS);
  ctx.disconnect = () => { ctx.closed = true; ctx.stop(); };
  ctx.done = () => clearTimeout(ctx.timer);
  return ctx;
}

async function send(agent, prompt, ctx, onDelta) {
  const run = await agent.send(prompt);
  ctx.run = run;
  let text = "";
  for await (const ev of run.stream()) {
    if (ev.type !== "assistant") continue; // "thinking" is never forwarded
    const piece = (ev.message?.content ?? []).map((b) => b.text ?? "").join("");
    if (piece) { text += piece; onDelta?.(piece); }
  }
  const res = await run.wait();
  if (ctx.timedOut) throw new GatewayError(504, `Timed out after ${TIMEOUT_MS} ms waiting for Cursor.`, "timeout");
  if (ctx.closed) throw new GatewayError(499, "Client disconnected.", "client_closed");
  if (res.status === "error") throw classify(res.error?.message);
  return { text: text || String(res.result ?? ""), usage: res.usage };
}

async function withAgent(modelId, ctx, fn) {
  let agent;
  try {
    agent = await Agent.create({ apiKey: CURSOR_API_KEY, model: { id: modelId }, tools: [], local: { cwd: WORKDIR } });
  } catch (e) {
    throw classify(e?.message ?? e);
  }
  try { return await fn(agent); }
  finally { try { await agent[Symbol.asyncDispose]?.(); } catch {} }
}

// ---- HTTP ------------------------------------------------------------------

function sendJson(res, status, body) {
  if (res.headersSent) return res.end();
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}
const errorBody = (e) => ({ error: { message: e.message, type: e.type ?? "api_error", code: e.code ?? null } });

function authorised(req) {
  const given = Buffer.from((req.headers.authorization || "").replace(/^Bearer\s+/i, ""));
  const want = Buffer.from(GATEWAY_TOKEN);
  return given.length === want.length && crypto.timingSafeEqual(given, want);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const parts = []; let size = 0;
    req.on("data", (c) => {
      size += c.length;
      if (size > MAX_BODY_BYTES) { reject(new GatewayError(413, "Request body too large.")); req.destroy(); return; }
      parts.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(parts).toString("utf8")));
    req.on("error", reject);
  });
}

async function handleChat(req, res, body) {
  const { messages, tools = [], stream = false, model: requested } = body;
  if (!Array.isArray(messages) || !messages.length) throw new GatewayError(400, "`messages` must be a non-empty array.", "invalid_request", "invalid_request_error");
  const modelId = MODEL_MAP[requested] || DEFAULT_MODEL;
  const echoModel = requested || modelId;
  const id = `chatcmpl-${crypto.randomBytes(8).toString("hex")}`;
  const created = Math.floor(Date.now() / 1000);
  const prompt = renderPrompt({ messages, tools });
  const wantsTools = tools.length > 0;
  const started = Date.now();

  const ctx = makeContext();
  res.on("close", () => { if (!res.writableEnded) ctx.disconnect(); });

  await slots.acquire();
  let usage = {}; let toolNames = [];
  try {
    if (stream && !wantsTools) {
      // Real streaming: forward Cursor's assistant deltas as they arrive.
      let began = false;
      const begin = () => {
        if (began) return; began = true;
        res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
        res.write(chunk({ id, model: echoModel, created, delta: { role: "assistant", content: "" } }));
      };
      try {
        const out = await withAgent(modelId, ctx, (agent) =>
          send(agent, prompt, ctx, (piece) => { begin(); res.write(chunk({ id, model: echoModel, created, delta: { content: piece } })); }));
        usage = out.usage;
        begin();
        res.write(chunk({ id, model: echoModel, created, finish: "stop", usage }));
        res.write(SSE_DONE);
        res.end();
      } catch (e) {
        if (!began) throw e;
        res.write(`data: ${JSON.stringify(errorBody(e))}\n\n`); // mid-stream: the SDK raises on an error chunk
        res.end();
        throw e;
      }
    } else {
      const result = await withAgent(modelId, ctx, async (agent) => {
        let out = await send(agent, prompt, ctx);
        let total = out.usage;
        if (!wantsTools) return { kind: "text", text: out.text, usage: total };
        let parsed = parseAssistant(out.text, tools);
        if (parsed.kind === "invalid") {
          out = await send(agent, repairPrompt(parsed.reason), ctx); // one repair, on the same conversation
          total = addUsage(total, out.usage);
          parsed = parseAssistant(out.text, tools);
          if (parsed.kind === "invalid") throw new GatewayError(502, `Model produced an invalid tool call: ${parsed.reason}`, "invalid_tool_call");
        }
        return { ...parsed, usage: total };
      });
      usage = result.usage;
      const calls = result.kind === "tools" ? result.calls.map((c, i) => ({ ...c, id: `call_${id.slice(-8)}_${i}` })) : null;
      toolNames = calls ? calls.map((c) => c.name) : [];

      if (!stream) {
        sendJson(res, 200, completion({ id, model: echoModel, created, text: result.text, calls, usage }));
      } else { // stream=true with tools: buffered, then emitted as one chunk
        res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
        res.write(chunk({ id, model: echoModel, created, delta: { role: "assistant", content: calls ? null : "" } }));
        if (calls) {
          res.write(chunk({ id, model: echoModel, created, delta: { tool_calls: calls.map((c, i) => ({
            index: i, id: c.id, type: "function", function: { name: c.name, arguments: JSON.stringify(c.arguments) } })) } }));
        } else {
          res.write(chunk({ id, model: echoModel, created, delta: { content: result.text } }));
        }
        res.write(chunk({ id, model: echoModel, created, finish: calls ? "tool_calls" : "stop", usage }));
        res.write(SSE_DONE);
        res.end();
      }
    }
    log(`chat 200 model=${modelId} asked=${requested ?? "-"} ${Date.now() - started}ms in=${usage?.inputTokens ?? 0} out=${usage?.outputTokens ?? 0} stream=${stream} tools=[${toolNames.join(",")}]`);
  } catch (e) {
    log(`chat ${e.status ?? 500} model=${modelId} ${Date.now() - started}ms code=${e.code ?? "error"} ${e.message}`);
    throw e;
  } finally {
    ctx.done();
    slots.release();
  }
}

const server = http.createServer(async (req, res) => {
  try {
    const { pathname } = new URL(req.url, "http://localhost");
    if (req.method === "GET" && pathname === "/health") {
      return sendJson(res, 200, { ok: true, model: DEFAULT_MODEL, free_slots: slots.free, waiting: slots.waiting });
    }
    if (!authorised(req)) throw new GatewayError(401, "Invalid or missing bearer token.", "invalid_api_key", "authentication_error");

    if (req.method === "GET" && pathname === "/openai/v1/models") {
      const ids = [...new Set([DEFAULT_MODEL, ...Object.keys(MODEL_MAP), ...Object.values(MODEL_MAP)])];
      return sendJson(res, 200, { object: "list", data: ids.map((id) => ({ id, object: "model", owned_by: "cursor" })) });
    }
    if (req.method === "POST" && pathname === "/openai/v1/chat/completions") {
      let body;
      try { body = JSON.parse(await readBody(req)); } catch (e) {
        if (e instanceof GatewayError) throw e;
        throw new GatewayError(400, "Body is not valid JSON.", "invalid_request", "invalid_request_error");
      }
      return await handleChat(req, res, body);
    }
    throw new GatewayError(404, `No route for ${req.method} ${pathname}`, "not_found", "invalid_request_error");
  } catch (e) {
    const err = e instanceof GatewayError ? e : new GatewayError(500, e?.message ?? "Internal error");
    if (!res.headersSent) sendJson(res, err.status === 499 ? 499 : err.status, errorBody(err));
    else res.end();
  }
});

server.listen(PORT, HOST, () => {
  log(`listening on http://${HOST}:${PORT} model=${DEFAULT_MODEL} map=${JSON.stringify(MODEL_MAP)} concurrency=${MAX_CONCURRENCY} timeout=${TIMEOUT_MS}ms`);
  if (!WARMUP) return;
  (async () => {
    const ctx = makeContext(); await slots.acquire();
    try {
      await withAgent(DEFAULT_MODEL, ctx, (a) => send(a, "Reply with the single word: ready", ctx));
      log("warm-up complete");
    } catch (e) { log(`warm-up failed (${e.code ?? "error"}): ${e.message}`); }
    finally { ctx.done(); slots.release(); }
  })();
});

for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => { server.close(); cleanup(); process.exit(0); });
process.on("exit", cleanup);
process.on("unhandledRejection", (e) => log(`unhandled rejection: ${e?.message ?? e}`));
