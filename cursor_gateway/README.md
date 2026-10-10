# Cursor gateway — run the tutor on Cursor models instead of Groq

A small local server (Node, `127.0.0.1` only) that speaks the Groq/OpenAI chat protocol and runs
each request on a Cursor model (`grok-4.6`, `composer-2.5`, `auto`, …). The agent needs **no code
changes** at its ~20 LLM call sites: every one goes through the Groq SDK, which honours
`GROQ_BASE_URL`. Groq stays the default; this is opt-in.

```
agent (Groq SDK) ──GROQ_BASE_URL──▶ gateway :8787 ──@cursor/sdk, all Cursor tools off──▶ Cursor
```

## Use it

```bash
cd Aeropt-agent
cp cursor_gateway/.env.example cursor_gateway/.env     # set CURSOR_API_KEY (gitignored)
make cursor-gateway        # terminal 1  (first run: npm install, generates GATEWAY_TOKEN)
make run-cursor            # terminal 2  (the agent on :8001, wired to the gateway)
DRY_RUN=1 bash cursor_gateway/run.sh agent   # show the wiring with secrets masked
make cursor-gateway-test   # unit tests for the protocol layer (no network)
```

Requires Node ≥ 22.13. The agent process is never given the Cursor key.

## What it costs you — measured 2026-09-20, not estimated

| | Groq (`gpt-oss-120b`) | Cursor via this gateway |
|---|---|---|
| one text-only call | ~1.6 s | `grok-4.6` ~6–9 s, `composer-2.5` ~11 s, `auto` ~16 s (first call after start ~25–30 s) |
| one step with all 15 tool schemas | ~2 s | ~25 s |
| a tool-using turn (real loop, 4 tools) | ~5 s | ~34 s |
| input tokens per call | as sent | as sent **+ ~3,400** (Cursor's own scaffolding, injected regardless) |
| limit | 8,000 TPM, 1,000 RPD free | Cursor plan pool, no free tier |
| billed to | Groq account | **the account that owns the key** |

So: the free-tier TPM wall goes away, but turns are roughly **6–10× slower** and cost real plan usage.
Treat this as a way to unblock testing and to compare model quality, not as a latency upgrade.

## Things you must know

- **Privacy.** Student messages leave this machine for Cursor's hosted models, under that account's
  Privacy Mode settings. The gateway never logs message content — only model, duration, token counts and
  tool names.
- **Whose plan.** Usage bills to the key's owner. Check they expect it before running a real cohort.
- **Tool calling is a prompt protocol, not native function calling.** Native Cursor tools cost ~15k input
  tokens per turn and cannot be stopped after the first call. Instead the gateway asks the model for
  `{"tool_calls":[…]}`, validates names, required arguments, types and enums against the tool's schema,
  and re-asks **once** on a bad reply. A reply that mentions `tool_calls` but is still invalid is a 502,
  never shown to a student as text. Ordinary answers (LaTeX braces, JSON examples) are never mistaken for
  tool calls — see `test/protocol.test.mjs`.
- **`temperature` / `max_tokens` are ignored** — the SDK does not expose them.
- **`systemPrompt` is not used.** `@cursor/sdk` 1.0.31 documents it but its local runtime rejects
  `--system-prompt`, so system instructions are placed in the prompt instead.
- **Streaming is real** for text-only calls (deltas forwarded as they arrive). Calls that offer tools are
  buffered, because a reply cannot be classified until it is complete.
- **Timeouts.** The agent's turn budget defaults to 45 s; `run-cursor` raises it to 75 s
  (`LEARNING_AGENT_TIME_BUDGET_S`). The agent now sends an SSE `: keepalive` comment every 15 s so the
  browser's 90 s stall timer does not fire on a long silent turn.
- **Memory.** Each in-flight request holds a Cursor agent; `MAX_CONCURRENCY` (default 2) caps it.

## Models

`CURSOR_MODEL` (default `grok-4.6`) answers every request, whatever model name the agent asked for.
`CURSOR_MODEL_MAP='{"openai/gpt-oss-120b":"composer-2.5"}'` routes particular names elsewhere. Note the
SDK's ids differ from the REST `GET /v0/models` list (`grok-4.6`, not `cursor-grok-4.6-high-fast`);
an unknown id returns `model_not_found`, which the agent's fallback chain handles.

## Errors it produces

`429 rate_limit_exceeded` (plan usage exhausted) · `404 model_not_found` · `401 invalid_api_key`
(bad gateway token or rejected Cursor key) · `504 timeout` · `502 invalid_tool_call` / `502` other Cursor
failures. These use the strings `utils/llm.py` already reacts to for retry and fallback.
