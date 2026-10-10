/**
 * Pure protocol layer: OpenAI/Groq chat messages  <->  one Cursor prompt, and a
 * model's reply  <->  OpenAI `content` / `tool_calls`.
 *
 * Nothing here touches the network, so all of it is unit-tested. The gateway
 * (server.mjs) is the only part that talks to Cursor.
 *
 * Tool calling is a plain JSON protocol rather than Cursor's native tool
 * machinery: native tools cost ~15k input tokens per turn (vs ~3.4k without) and
 * cannot be stopped after the first call without cancelling the run.
 */

/** OpenAI `content` is a string, null, or an array of typed parts. */
export function textOf(content) {
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => (typeof part === "string" ? part : part?.text ?? ""))
      .join("");
  }
  return String(content);
}

function argsAsObject(raw) {
  if (raw && typeof raw === "object") return raw;
  try {
    const parsed = JSON.parse(raw ?? "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function toolProtocol(tools) {
  const lines = tools.map((t) => {
    const fn = t.function ?? t;
    const schema = JSON.stringify(fn.parameters ?? { type: "object", properties: {} });
    return `- ${fn.name}: ${(fn.description ?? "").trim()}\n  parameters: ${schema}`;
  });
  return [
    "=== TOOLS ===",
    "You may call the tools below. To call one or more, reply with ONLY a JSON object",
    'and nothing else - no prose, no code fences: {"tool_calls":[{"name":"<tool>","arguments":{...}}]}',
    "To answer the user instead, reply in plain text with NO tool_calls object.",
    "Never invent a tool result: results arrive as [tool_result] lines on your next turn.",
    "Only the tools listed here exist. Arguments must match the parameters schema.",
    "",
    ...lines,
  ].join("\n");
}

/**
 * Flatten a chat into one prompt. Cursor's agent has no message-array API, so the
 * system instructions, history and tool results are laid out as labelled blocks.
 */
export function renderPrompt({ messages, tools = [] }) {
  const nameById = new Map();
  const system = [];
  const turns = [];

  for (const m of messages) {
    const role = m.role;
    if (role === "system" || role === "developer") {
      system.push(textOf(m.content));
    } else if (role === "user") {
      turns.push(`[user]\n${textOf(m.content)}`);
    } else if (role === "assistant") {
      const calls = m.tool_calls ?? [];
      const body = textOf(m.content);
      if (body) turns.push(`[assistant]\n${body}`);
      if (calls.length) {
        for (const c of calls) nameById.set(c.id, c.function?.name);
        const rendered = calls.map((c) => ({
          id: c.id,
          name: c.function?.name,
          arguments: argsAsObject(c.function?.arguments),
        }));
        turns.push(`[assistant called tools]\n${JSON.stringify({ tool_calls: rendered })}`);
      }
    } else if (role === "tool") {
      const name = m.name ?? nameById.get(m.tool_call_id) ?? "tool";
      turns.push(`[tool_result name=${name} id=${m.tool_call_id ?? ""}]\n${textOf(m.content)}`);
    }
  }

  const parts = [
    "You are the language model behind a tutoring application's API. This is NOT a coding",
    "task: do not read or write files, run commands, or use any tool except those declared",
    "below. Continue the conversation as the assistant and follow the system instructions",
    "exactly, including their formatting rules.",
    "",
    "=== SYSTEM INSTRUCTIONS ===",
    system.join("\n\n") || "(none)",
    "",
    "=== CONVERSATION ===",
    turns.join("\n\n") || "(empty)",
  ];
  if (tools.length) parts.push("", toolProtocol(tools));
  parts.push("", "=== YOUR TURN ===", "Reply as the assistant now.");
  return parts.join("\n");
}

/** First balanced {...} at or after `from`, honouring JSON strings/escapes. */
export function firstJsonObject(text, from = 0) {
  const start = text.indexOf("{", from);
  if (start < 0) return null;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === "{") depth++;
    else if (ch === "}" && --depth === 0) return text.slice(start, i + 1);
  }
  return null;
}

const TYPE_CHECKS = {
  string: (v) => typeof v === "string",
  number: (v) => typeof v === "number" && Number.isFinite(v),
  integer: (v) => Number.isInteger(v),
  boolean: (v) => typeof v === "boolean",
  array: (v) => Array.isArray(v),
  object: (v) => v !== null && typeof v === "object" && !Array.isArray(v),
  null: (v) => v === null,
};

/** Top-level check only: required keys, primitive types, enums. Returns an error or null. */
export function validateArgs(schema, args) {
  if (!schema || typeof schema !== "object") return null;
  for (const key of schema.required ?? []) {
    if (!(key in args)) return `missing required argument "${key}"`;
  }
  for (const [key, spec] of Object.entries(schema.properties ?? {})) {
    if (!(key in args) || !spec) continue;
    const types = spec.type ? [].concat(spec.type) : null;
    if (types && !types.some((t) => (TYPE_CHECKS[t] ?? (() => true))(args[key]))) {
      return `argument "${key}" must be ${types.join(" or ")}`;
    }
    if (Array.isArray(spec.enum) && !spec.enum.includes(args[key])) {
      return `argument "${key}" must be one of ${JSON.stringify(spec.enum)}`;
    }
  }
  return null;
}

function stripFences(text) {
  const m = text.trim().match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  return m ? m[1] : text.trim();
}

function normaliseCalls(obj, tools) {
  const list = Array.isArray(obj.tool_calls) ? obj.tool_calls : obj.name ? [obj] : null;
  if (!list || !list.length) return { kind: "invalid", reason: "tool_calls must be a non-empty array" };
  const byName = new Map(tools.map((t) => [(t.function ?? t).name, t.function ?? t]));
  const calls = [];
  for (const raw of list) {
    const name = raw?.name ?? raw?.function?.name;
    const spec = byName.get(name);
    if (!spec) return { kind: "invalid", reason: `unknown tool "${name}"` };
    let args = raw.arguments ?? raw.function?.arguments ?? {};
    if (typeof args === "string") {
      try { args = JSON.parse(args); } catch { return { kind: "invalid", reason: `arguments for "${name}" are not valid JSON` }; }
    }
    if (!TYPE_CHECKS.object(args)) return { kind: "invalid", reason: `arguments for "${name}" must be an object` };
    const problem = validateArgs(spec.parameters, args);
    if (problem) return { kind: "invalid", reason: `${name}: ${problem}` };
    calls.push({ name, arguments: args });
  }
  return { kind: "tools", calls };
}

/**
 * Classify a reply. Only a reply that is (or clearly embeds) a `tool_calls` object is
 * a tool call. LaTeX braces, JSON examples and ordinary prose all stay text, because
 * a tutor answer must never be misread as a tool call.
 */
export function parseAssistant(text, tools = []) {
  if (!tools.length) return { kind: "text", text };
  const body = stripFences(text);

  let candidate = null;
  if (body.startsWith("{")) candidate = firstJsonObject(body);
  else if (body.includes('"tool_calls"')) {
    const at = body.indexOf('{"tool_calls"');
    const spaced = at >= 0 ? at : body.search(/\{\s*"tool_calls"/);
    if (spaced >= 0) candidate = firstJsonObject(body, spaced);
  }
  if (!candidate) {
    // An unterminated or garbled attempt must go through repair, never reach a student as text.
    return body.startsWith("{") && body.includes('"tool_calls"')
      ? { kind: "invalid", reason: "the tool_calls object is incomplete or not valid JSON" }
      : { kind: "text", text };
  }

  let obj;
  try { obj = JSON.parse(candidate); } catch {
    return body.includes('"tool_calls"')
      ? { kind: "invalid", reason: "the tool_calls object is not valid JSON" }
      : { kind: "text", text };
  }
  if (!obj || (typeof obj !== "object")) return { kind: "text", text };
  if (!("tool_calls" in obj) && !("name" in obj && "arguments" in obj)) return { kind: "text", text };
  return normaliseCalls(obj, tools);
}

export const repairPrompt = (reason) =>
  `Your last reply could not be used as a tool call: ${reason}. ` +
  'Reply again with ONLY {"tool_calls":[{"name":"<tool>","arguments":{...}}]} using a listed tool ' +
  "and arguments matching its schema, or answer in plain text if no tool is needed.";

// ---- OpenAI response shapes -------------------------------------------------

const usageOf = (u) => ({
  prompt_tokens: u?.inputTokens ?? 0,
  completion_tokens: u?.outputTokens ?? 0,
  total_tokens: (u?.inputTokens ?? 0) + (u?.outputTokens ?? 0),
});

export function completion({ id, model, created, text, calls, usage }) {
  const message = calls?.length
    ? {
        role: "assistant",
        content: null,
        tool_calls: calls.map((c, i) => ({
          id: c.id ?? `call_${id.slice(-8)}_${i}`,
          type: "function",
          function: { name: c.name, arguments: JSON.stringify(c.arguments) },
        })),
      }
    : { role: "assistant", content: text };
  return {
    id,
    object: "chat.completion",
    created,
    model,
    choices: [{ index: 0, message, finish_reason: calls?.length ? "tool_calls" : "stop" }],
    usage: usageOf(usage),
  };
}

export function chunk({ id, model, created, delta = {}, finish = null, usage }) {
  const body = {
    id,
    object: "chat.completion.chunk",
    created,
    model,
    choices: [{ index: 0, delta, finish_reason: finish }],
  };
  if (usage) body.usage = usageOf(usage);
  return `data: ${JSON.stringify(body)}\n\n`;
}

export const SSE_DONE = "data: [DONE]\n\n";
