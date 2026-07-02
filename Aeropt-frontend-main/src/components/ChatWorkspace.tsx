"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import StudySidebar from "@/components/StudySidebar";

// ── Types ─────────────────────────────────────────────────────────────────────

type FollowUpSuggestion = {
  content_id: string;
  content_type: string;
  difficulty: number | null;
  preview: string;
};

type QuizStep = {
  step_num: number;
  concept_id: string;
  question: string;
  hint: string;
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  agentUsed?: string;
  followUpSuggestions?: FollowUpSuggestion[];
  quizSteps?: QuizStep[];
  awaitingGradedResponse?: boolean;
};

// ── Constants ─────────────────────────────────────────────────────────────────

const API_BASE = (process.env.NEXT_PUBLIC_CHAT_API_BASE ?? "http://127.0.0.1:8001/api/v1").replace(/\/+$/, "");

const FALLBACK_PROMPTS = [
  "Explain Newton's second law with an example",
  "Give me a practice question on Laws of Motion",
];

// Read user_id from localStorage (set there by LoginScreen after register/sign-in)
function getStoredUserId(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("prepwise_user_id") ?? "";
}

// ── Icon components ───────────────────────────────────────────────────────────

function ArrowUpIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5">
      <path d="M12 19V5m0 0-6 6m6-6 6 6" />
    </svg>
  );
}

function PanelIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5">
      <path d="M5 5h14v14H5V5Zm10 0v14" />
    </svg>
  );
}

function XIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4">
      <path d="m6 6 12 12M18 6 6 18" />
    </svg>
  );
}

function TypingIndicator() {
  return (
    <div className="assistant-thinking">
      <span />
      <span />
      <span />
    </div>
  );
}

// ── Concept panel (kept for future data; empty for now) ───────────────────────

function ConceptPanel({
  collapsed,
  onToggle,
}: {
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <aside className={`concept-panel ${collapsed ? "concept-panel-collapsed" : ""}`}>
      <button type="button" className="concept-toggle" onClick={onToggle} aria-label="Toggle concepts panel">
        {collapsed ? <PanelIcon /> : <XIcon />}
      </button>

      {!collapsed && (
        <div className="concept-content">
          <h2>Concepts in this response</h2>
          <p className="concept-empty">Concept data will appear here as you study.</p>

          <h3>Prerequisite chain</h3>
          <p className="concept-empty">No prerequisite chain yet.</p>
        </div>
      )}
    </aside>
  );
}

// ── Assistant message renderer ────────────────────────────────────────────────

function AssistantResponse({ message }: { message: ChatMessage }) {
  return (
    <article className="lesson-response assistant-plain">
      {/* Agent label */}
      {message.agentUsed && (
        <p style={{ fontSize: "10px", opacity: 0.45, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: "6px" }}>
          {message.agentUsed}
        </p>
      )}

      {/* Main response text — rendered as Markdown with KaTeX math */}
      <div className="prose prose-sm prose-invert max-w-none">
        <ReactMarkdown
          remarkPlugins={[remarkMath]}
          rehypePlugins={[rehypeKatex]}
        >
          {message.content}
        </ReactMarkdown>
      </div>

      {/* Quiz steps */}
      {message.quizSteps && message.quizSteps.length > 0 && (
        <div style={{ marginTop: "16px", borderTop: "1px solid rgba(255,255,255,0.08)", paddingTop: "12px" }}>
          <p style={{ fontSize: "11px", fontWeight: 700, opacity: 0.5, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: "10px" }}>
            Diagnostic Quiz
          </p>
          {message.quizSteps.map((step) => (
            <div key={step.step_num} style={{ marginBottom: "12px" }}>
              <p style={{ fontSize: "12px", fontWeight: 700, opacity: 0.7 }}>Step {step.step_num}</p>
              <p style={{ fontSize: "13px", marginTop: "4px" }}>{step.question}</p>
              <p style={{ fontSize: "11px", opacity: 0.5, marginTop: "4px" }}>Hint: {step.hint}</p>
            </div>
          ))}
          <p style={{ fontSize: "11px", opacity: 0.45, marginTop: "8px" }}>
            Submit: <code>ANSWER::&lt;answer&gt;::&lt;correct|incorrect&gt;::&lt;error_type&gt;::&lt;seconds&gt;</code>
          </p>
        </div>
      )}

      {/* Awaiting graded response hint */}
      {message.awaitingGradedResponse && (
        <div style={{
          marginTop: "14px",
          padding: "10px 14px",
          background: "rgba(98,88,255,0.08)",
          border: "1px solid rgba(98,88,255,0.25)",
          borderRadius: "10px",
          fontSize: "12px",
          fontWeight: 600,
        }}>
          Reply <strong>1</strong> if your answer was correct, <strong>0</strong> if it was incorrect.
        </div>
      )}
    </article>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function ChatWorkspace() {
  const [messages, setMessages]           = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId]         = useState<string | null>(null);
  const [input, setInput]                 = useState("");
  const [isReplying, setIsReplying]       = useState(false);
  const [isEndingSession, setIsEndingSession] = useState(false);
  const [panelCollapsed, setPanelCollapsed] = useState(true);
  const [error, setError]                 = useState("");
  const formRef = useRef<HTMLFormElement>(null);

  // Auto-scroll to bottom on new messages
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isReplying]);

  // ── Quick prompts ─────────────────────────────────────────────────────────
  // Derive from the latest assistant message's follow_up_suggestions.
  // Each suggestion becomes a numbered button ("1", "2", "3") that sends that
  // number directly to the agent — matching what the agent's Follow-Up agent
  // expects.
  const latestAssistant = useMemo(
    () => [...messages].reverse().find((m) => m.role === "assistant") ?? null,
    [messages],
  );

  // While a practice/PYQ question is awaiting a 1/0 grade, do NOT show any
  // follow-up prompts or fallback suggestions — they would be confusing and
  // could trigger the wrong agent path if clicked.
  const isPracticeAwaitingGrade = Boolean(latestAssistant?.awaitingGradedResponse);

  const quickPrompts: { label: string; value: string }[] = useMemo(() => {
    // Suppress all prompts while a reply is loading (stale suggestions from the
    // previous turn must not show under the typing indicator) or while grading
    // is pending.
    if (isReplying || isPracticeAwaitingGrade) return [];
    const suggestions = latestAssistant?.followUpSuggestions;
    if (suggestions && suggestions.length > 0) {
      return suggestions.map((s, i) => ({
        label: `${i + 1}. [${(s.content_type ?? "").toUpperCase()} | ★${s.difficulty ?? "?"}] ${(s.preview ?? "").slice(0, 90)}…`,
        value: String(i + 1),   // sends "1", "2", or "3" to the agent
      }));
    }
    return FALLBACK_PROMPTS.map((p) => ({ label: p, value: p }));
  }, [latestAssistant, isPracticeAwaitingGrade, isReplying]);

  // ── Send a message ────────────────────────────────────────────────────────

  async function submitMessage(value: string) {
    const trimmed = value.trim();
    if (!trimmed || isReplying) return;

    const userId = getStoredUserId();
    if (!userId) {
      setError("No user ID found. Please sign in again.");
      return;
    }

    const userMsg: ChatMessage = { id: crypto.randomUUID(), role: "user", content: trimmed };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setError("");
    setIsReplying(true);

    try {
      const res = await fetch(`${API_BASE}/chat/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, session_id: sessionId, query: trimmed }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error((data as { detail?: string }).detail ?? `Server error ${res.status}`);
      }

      const payload = await res.json() as {
        session_id: string;
        response: string | null;
        agent_used: string | null;
        follow_up_suggestions: FollowUpSuggestion[] | null;
        quiz_steps: QuizStep[] | null;
        awaiting_graded_response: boolean;
        error: string | null;
      };

      // Persist session_id for subsequent turns
      if (payload.session_id) setSessionId(payload.session_id);

      const assistantMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: payload.error ? `⚠️ ${payload.error}` : (payload.response ?? ""),
        agentUsed: payload.agent_used ?? undefined,
        followUpSuggestions: payload.follow_up_suggestions ?? undefined,
        quizSteps: payload.quiz_steps ?? undefined,
        awaitingGradedResponse: payload.awaiting_graded_response,
      };

      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      // Remove optimistic user message so the student can retry
      setMessages((prev) => prev.filter((m) => m.id !== userMsg.id));
    } finally {
      setIsReplying(false);
    }
  }

  // ── End session ───────────────────────────────────────────────────────────

  async function endSession() {
    if (!sessionId || isEndingSession) return;
    const userId = getStoredUserId();
    setIsEndingSession(true);
    try {
      await fetch(`${API_BASE}/chat/end-session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, session_id: sessionId }),
      });
    } catch {
      // best-effort
    } finally {
      setIsEndingSession(false);
      setSessionId(null);
      setMessages([]);
      setError("");
    }
  }

  // ── New chat ──────────────────────────────────────────────────────────────

  function newChat() {
    // Flush current session if one exists (fire-and-forget)
    if (sessionId) {
      void endSession();
    } else {
      setMessages([]);
      setError("");
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitMessage(input);
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <main className="chat-page">
      <StudySidebar />

      <section className="chat-main">
        <header className="chat-header">
          <h1>{sessionId ? "Study Session" : "New Session"}</h1>
          <div className="header-actions">
            <button type="button" className="header-action-button" onClick={newChat} disabled={isReplying}>
              New chat
            </button>
            <button
              type="button"
              className="end-session"
              onClick={() => void endSession()}
              disabled={!sessionId || isEndingSession}
            >
              {isEndingSession ? "Ending…" : "End Session"}
            </button>
          </div>
        </header>

        <div className="chat-scroll">
          {/* Empty state */}
          {messages.length === 0 && !isReplying && (
            <p className="chat-error" style={{ opacity: 0.5 }}>
              Start by asking a Physics question below.
            </p>
          )}

          {/* Message feed */}
          {messages.map((message) =>
            message.role === "user" ? (
              <div className="user-bubble" key={message.id}>
                {message.content}
              </div>
            ) : (
              <AssistantResponse key={message.id} message={message} />
            ),
          )}

          {isReplying && <TypingIndicator />}
          {error && <p className="chat-error">{error}</p>}

          {/* Quick prompts / follow-up suggestions — hidden while a reply is
              loading and while grading is pending */}
          {!isReplying && !isPracticeAwaitingGrade && quickPrompts.length > 0 && (
            <div className="quick-prompts">
              <span>Try next</span>
              {quickPrompts.map((prompt) => (
                <button key={prompt.value} type="button" onClick={() => void submitMessage(prompt.value)}>
                  {prompt.label} {"->"}
                </button>
              ))}
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        <form ref={formRef} className="chat-input" onSubmit={onSubmit}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={
              isReplying
                ? "Tutor is thinking…"
                : latestAssistant?.awaitingGradedResponse
                ? "Type 1 (correct) or 0 (incorrect)…"
                : "Ask anything from Laws of Motion, Work Energy & Power, or Rotational Motion…"
            }
            rows={2}
            disabled={isReplying}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                formRef.current?.requestSubmit();
              }
            }}
          />
          <button type="submit" disabled={isReplying || !input.trim()} aria-label="Send message">
            <ArrowUpIcon />
          </button>
          <p>KaTeX supported — type $F = ma$ for inline math</p>
        </form>
      </section>

      <ConceptPanel
        collapsed={panelCollapsed}
        onToggle={() => setPanelCollapsed((v) => !v)}
      />
    </main>
  );
}
