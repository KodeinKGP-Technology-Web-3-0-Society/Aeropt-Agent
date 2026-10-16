// One vision call on a Cursor model, for local_ingest/exams (exam-paper extraction).
//
//   echo '{"model":"auto","prompt":"…","images":["/abs/page.png"]}' | node vision_once.mjs
//
// Prints one JSON line: {"text": "...", "ms": 1234} or {"error": "...", "ms": 1234}.
// The tutor gateway (server.mjs) is text-only; this is the one place images go to Cursor.
// Uses CURSOR_EXTRACT_API_KEY from .env, falling back to CURSOR_API_KEY.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Agent } from "@cursor/sdk";

const here = path.dirname(fileURLToPath(import.meta.url));
const env = {};
for (const line of fs.readFileSync(path.join(here, ".env"), "utf8").split("\n")) {
  const i = line.indexOf("=");
  if (i > 0 && !line.trimStart().startsWith("#")) env[line.slice(0, i).trim()] = line.slice(i + 1).trim().replace(/^["']|["']$/g, "");
}
const apiKey = process.env.CURSOR_EXTRACT_API_KEY || env.CURSOR_EXTRACT_API_KEY || env.CURSOR_API_KEY;

const job = JSON.parse(fs.readFileSync(0, "utf8"));
const started = Date.now();
const done = (body) => { process.stdout.write(JSON.stringify({ ...body, ms: Date.now() - started }) + "\n"); };

let agent;
try {
  const images = (job.images || []).map((p) => ({
    data: fs.readFileSync(p).toString("base64"),
    mimeType: p.endsWith(".jpg") || p.endsWith(".jpeg") ? "image/jpeg" : "image/png",
  }));
  agent = await Agent.create({ apiKey, model: { id: job.model || "auto" }, tools: [], local: { cwd: here } });
  const run = await agent.send({ text: job.prompt, images });
  let text = "";
  for await (const ev of run.stream()) {
    if (ev.type !== "assistant") continue;
    text += (ev.message?.content ?? []).map((b) => b.text ?? "").join("");
  }
  const res = await run.wait();
  if (res.status === "error") done({ error: String(res.error?.message || "cursor run failed") });
  else done({ text: text || String(res.result ?? "") });
} catch (err) {
  done({ error: String(err?.message || err) });
} finally {
  try {
    agent?.close();
  } catch {}
  process.exit(0);
}

