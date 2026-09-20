import test from "node:test";
import assert from "node:assert/strict";
import {
  textOf, renderPrompt, firstJsonObject, validateArgs, parseAssistant,
  completion, chunk, SSE_DONE, repairPrompt,
} from "../protocol.mjs";

const TOOLS = [
  { type: "function", function: { name: "resolve_concept", description: "Map a phrase to a concept id.",
      parameters: { type: "object", properties: { query: { type: "string" }, limit: { type: "integer" } }, required: ["query"] } } },
  { type: "function", function: { name: "calculate", description: "Evaluate arithmetic.",
      parameters: { type: "object", properties: { expression: { type: "string" }, mode: { type: "string", enum: ["exact", "float"] } }, required: ["expression"] } } },
];

test("textOf handles string, null and part arrays", () => {
  assert.equal(textOf("hi"), "hi");
  assert.equal(textOf(null), "");
  assert.equal(textOf([{ type: "text", text: "a" }, { type: "text", text: "b" }]), "ab");
});

test("renderPrompt keeps system, history and tool results attributed by tool name", () => {
  const p = renderPrompt({
    tools: TOOLS,
    messages: [
      { role: "system", content: "Be a tutor." },
      { role: "user", content: "explain torque" },
      { role: "assistant", content: null, tool_calls: [{ id: "call_1", type: "function", function: { name: "resolve_concept", arguments: '{"query":"torque"}' } }] },
      { role: "tool", tool_call_id: "call_1", content: '{"concept_id":"rot.torque"}' },
    ],
  });
  assert.match(p, /=== SYSTEM INSTRUCTIONS ===\nBe a tutor\./);
  assert.match(p, /\[user\]\nexplain torque/);
  assert.match(p, /\[assistant called tools\]\n\{"tool_calls":\[\{"id":"call_1","name":"resolve_concept","arguments":\{"query":"torque"\}\}\]\}/);
  assert.match(p, /\[tool_result name=resolve_concept id=call_1\]\n\{"concept_id":"rot\.torque"\}/);
  assert.match(p, /=== TOOLS ===/);
  assert.match(p, /- calculate: Evaluate arithmetic\./);
});

test("renderPrompt omits the tool block when no tools are offered (final pass)", () => {
  const p = renderPrompt({ messages: [{ role: "user", content: "hi" }], tools: [] });
  assert.doesNotMatch(p, /=== TOOLS ===/);
});

test("firstJsonObject respects braces inside strings", () => {
  assert.equal(firstJsonObject('x {"a":"}{","b":1} tail'), '{"a":"}{","b":1}');
  assert.equal(firstJsonObject("no json"), null);
  assert.equal(firstJsonObject('{"a":1'), null);
});

test("validateArgs: required, types, enums", () => {
  const s = TOOLS[1].function.parameters;
  assert.equal(validateArgs(s, { expression: "1+1" }), null);
  assert.match(validateArgs(s, {}), /missing required argument "expression"/);
  assert.match(validateArgs(s, { expression: 5 }), /must be string/);
  assert.match(validateArgs(s, { expression: "1", mode: "wild" }), /one of/);
});

test("a clean tool_calls object is a tool call", () => {
  const r = parseAssistant('{"tool_calls":[{"name":"resolve_concept","arguments":{"query":"torque"}}]}', TOOLS);
  assert.equal(r.kind, "tools");
  assert.deepEqual(r.calls, [{ name: "resolve_concept", arguments: { query: "torque" } }]);
});

test("fenced JSON and prose-before-JSON are still recognised", () => {
  const fenced = "```json\n" + '{"tool_calls":[{"name":"calculate","arguments":{"expression":"2*3"}}]}' + "\n```";
  assert.equal(parseAssistant(fenced, TOOLS).kind, "tools");
  const prose = 'Let me look that up.\n{"tool_calls":[{"name":"calculate","arguments":{"expression":"2*3"}}]}';
  assert.equal(parseAssistant(prose, TOOLS).kind, "tools");
});

test("several calls in one reply, arguments given as a JSON string", () => {
  const r = parseAssistant(JSON.stringify({ tool_calls: [
    { name: "resolve_concept", arguments: '{"query":"a"}' },
    { name: "calculate", arguments: { expression: "1" } },
  ] }), TOOLS);
  assert.equal(r.kind, "tools");
  assert.equal(r.calls.length, 2);
  assert.deepEqual(r.calls[0].arguments, { query: "a" });
});

test("braces inside argument strings do not break extraction", () => {
  const r = parseAssistant('{"tool_calls":[{"name":"calculate","arguments":{"expression":"f{x} + }"}}]}', TOOLS);
  assert.equal(r.kind, "tools");
  assert.equal(r.calls[0].arguments.expression, "f{x} + }");
});

test("unknown tool, bad args and malformed JSON are invalid, not silently text", () => {
  assert.match(parseAssistant('{"tool_calls":[{"name":"rm_rf","arguments":{}}]}', TOOLS).reason, /unknown tool "rm_rf"/);
  assert.match(parseAssistant('{"tool_calls":[{"name":"resolve_concept","arguments":{}}]}', TOOLS).reason, /missing required/);
  assert.match(parseAssistant('{"tool_calls":[{"name":"calculate","arguments":{"expression":1}}]}', TOOLS).reason, /must be string/);
  // A truncated attempt must be repaired, never shown to a student as if it were the answer.
  assert.equal(parseAssistant('{"tool_calls":[{"name":"calculate",', TOOLS).kind, "invalid");
  assert.equal(parseAssistant('{"tool_calls": [ oops ]}', TOOLS).kind, "invalid");
});

test("a tutor answer is never mistaken for a tool call", () => {
  const latex = "The torque is $\\tau = r F \\sin\\theta$, so $$\\frac{F}{m}$$ and {braces} everywhere.";
  assert.deepEqual(parseAssistant(latex, TOOLS), { kind: "text", text: latex });
  const jsonExample = 'You can write the dict as {"a": 1, "b": 2} in Python.';
  assert.equal(parseAssistant(jsonExample, TOOLS).kind, "text");
  const startsWithBrace = '{"answer": "42"}';
  assert.equal(parseAssistant(startsWithBrace, TOOLS).kind, "text");
});

test("with no tools offered a tool-shaped reply is just text", () => {
  const t = '{"tool_calls":[{"name":"calculate","arguments":{"expression":"1"}}]}';
  assert.equal(parseAssistant(t, []).kind, "text");
});

test("completion: tool_calls use OpenAI shape with string arguments", () => {
  const c = completion({ id: "chatcmpl-abcdef12", model: "m", created: 1, calls: [{ name: "calculate", arguments: { expression: "1" } }], usage: { inputTokens: 10, outputTokens: 4 } });
  const msg = c.choices[0].message;
  assert.equal(c.choices[0].finish_reason, "tool_calls");
  assert.equal(msg.content, null);
  assert.equal(msg.tool_calls[0].type, "function");
  assert.equal(msg.tool_calls[0].function.arguments, '{"expression":"1"}');
  assert.match(msg.tool_calls[0].id, /^call_/);
  assert.deepEqual(c.usage, { prompt_tokens: 10, completion_tokens: 4, total_tokens: 14 });
});

test("completion: plain text and SSE framing", () => {
  const c = completion({ id: "x", model: "m", created: 1, text: "hello", usage: {} });
  assert.equal(c.choices[0].message.content, "hello");
  assert.equal(c.choices[0].finish_reason, "stop");
  const s = chunk({ id: "x", model: "m", created: 1, delta: { content: "hi" } });
  assert.match(s, /^data: \{.*"delta":\{"content":"hi"\}.*\}\n\n$/);
  assert.equal(SSE_DONE, "data: [DONE]\n\n");
  assert.match(repairPrompt("unknown tool"), /unknown tool/);
});
