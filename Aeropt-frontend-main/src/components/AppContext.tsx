"use client";

import React, { createContext, useContext, useState, useEffect } from "react";
import appDataJson from "@/data/appData.json";

// Types derived from JSON schema
export type User = {
  name: string;
  badge: string;
  email: string;
};

export type DashboardStat = {
  id: string;
  label: string;
  value: string;
  subtext: string;
  list?: string[];
  dots?: boolean[];
};

export type Concept = {
  name: string;
  score: number;
  attempts: number;
  tag: string | null;
};

export type Chapter = {
  name: string;
  conceptCount: number;
  overallScore: number;
  concepts: Concept[];
};

export type ChatMessage = {
  id: string;
  sender: "user" | "bot";
  text?: string;
  isExplanation?: boolean;
  gapTitle?: string;
  gapDescription?: string;
  steps?: string[];
  keyInsight?: string;
  tryNext?: string[];
};

export type QuizStep = {
  title: string;
  topic: string;
  totalSteps: number;
  currentStep: number;
  subTitle: string;
  questionText: string;
  options: { label: string; value: string; text: string }[];
  hint: string;
  correctAnswer: string;
  weakPoint: { title: string; description: string };
};

export type PracticeQuestion = {
  id: string;
  questionNumber: number;
  totalQuestions: number;
  difficulty: number;
  chapter: string;
  type: string;
  source: string;
  text: string;
  options: { label: string; text: string }[];
  correctAnswer: string;
  timeSpent: string;
  solution: string[];
  keyInsight: string;
};

export type AppDataState = {
  user: User;
  dashboard: {
    stats: DashboardStat[];
    conceptHealth: {
      title: string;
      tabs: string[];
      chapters: Chapter[];
    };
  };
  chat: {
    header: { title: string; concept: string; secondaryConcept: string };
    sidebar: {
      concepts: { name: string; score: number; status: string }[];
      prerequisites: { name: string; score: number; status: string }[];
    };
    conversation: ChatMessage[];
    diagnosticQuiz: QuizStep;
  };
  practice: {
    filters: { chapters: string[]; difficulties: string[]; types: string[] };
    questions: PracticeQuestion[];
  };
  progress: {
    subtitle: string;
    stats: DashboardStat[];
    mistakesDistribution: { type: string; count: number; percentage: number }[];
    proficiencyOverTime: {
      dates: string[];
      chapters: { name: string; data: number[] }[];
    };
    conceptBreakdownTable: {
      concept: string;
      chapter: string;
      score: number;
      attempts: number;
      accuracy: number;
      lastAttempted: string;
      trend: "up" | "down" | "flat";
    }[];
  };
};

// Read the active user_id from localStorage.  Set there by LoginScreen after
// a successful register or sign-in call to the FastAPI /auth endpoints.
function getStoredUserId(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("prepwise_user_id") ?? "";
}

function _examToBadge(exam: string): string {
  if (exam.toUpperCase().includes("ADVANCED")) return "JEE Advanced";
  if (exam.toUpperCase().includes("MAIN"))     return "JEE Mains";
  return exam;
}

type AppContextType = {
  isLoggedIn: boolean;
  login: (email: string, userId?: string, name?: string, targetExam?: string) => void;
  logout: () => void;
  data: AppDataState;
  setData: React.Dispatch<React.SetStateAction<AppDataState>>;
  // Chat page states
  chatHistory: ChatMessage[];
  addChatMessage: (text: string) => void;
  isBotThinking: boolean;
  sessionId: string | null;
  endSession: () => Promise<void>;
  quizActive: boolean;
  setQuizActive: (active: boolean) => void;
  quizStep: number; // 1 to 4
  setQuizStep: (step: number) => void;
  // Practice page states
  currentQuestionIndex: number;
  setCurrentQuestionIndex: (idx: number) => void;
  selectedPracticeChapter: string;
  setSelectedPracticeChapter: (ch: string) => void;
  selectedPracticeDifficulty: string;
  setSelectedPracticeDifficulty: (diff: string) => void;
  selectedPracticeType: string;
  setSelectedPracticeType: (type: string) => void;
  prioritizeWeak: boolean;
  setPrioritizeWeak: (val: boolean) => void;
  appTheme: "light" | "dark";
  setAppTheme: (theme: "light" | "dark") => void;
};

const AppContext = createContext<AppContextType | undefined>(undefined);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [isLoggedIn, setIsLoggedIn] = useState<boolean>(false);
  const [data, setData] = useState<AppDataState>(appDataJson as AppDataState);
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>(data.chat.conversation);
  const [quizActive, setQuizActive] = useState<boolean>(false);
  const [quizStep, setQuizStep] = useState<number>(2);

  // Practice state
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState<number>(0);
  const [selectedPracticeChapter, setSelectedPracticeChapter] = useState<string>("All");
  const [selectedPracticeDifficulty, setSelectedPracticeDifficulty] = useState<string>("All");
  const [selectedPracticeType, setSelectedPracticeType] = useState<string>("All");
  const [prioritizeWeak, setPrioritizeWeak] = useState<boolean>(false);

  const [appTheme, setAppTheme] = useState<"light" | "dark">("dark");

  // ── Real API session state ─────────────────────────────────────────────────
  // sessionId starts null; the first message creates a Supabase session via
  // the FastAPI server and stores the returned ID for all subsequent turns.
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isBotThinking, setIsBotThinking] = useState<boolean>(false);

  // Sync auth, user profile, and theme with localStorage on client load
  useEffect(() => {
    const authStatus = localStorage.getItem("prepwise_auth");
    if (authStatus === "true") {
      setIsLoggedIn(true);
      // Restore real user profile so mock data never leaks in
      const storedName  = localStorage.getItem("prepwise_user_name");
      const storedEmail = localStorage.getItem("prepwise_user_email");
      const storedExam  = localStorage.getItem("prepwise_user_target_exam");
      if (storedName || storedEmail) {
        setData((prev) => ({
          ...prev,
          user: {
            ...prev.user,
            name:  storedName  ?? prev.user.name,
            email: storedEmail ?? prev.user.email,
            badge: storedExam  ? _examToBadge(storedExam) : prev.user.badge,
          },
        }));
      }
    }
    const savedTheme = localStorage.getItem("prepwise_theme") as "light" | "dark" | null;
    if (savedTheme) {
      setAppTheme(savedTheme);
    }
  }, []);

  // Apply theme to DOM
  useEffect(() => {
    localStorage.setItem("prepwise_theme", appTheme);
    document.documentElement.setAttribute("data-theme", appTheme);
    if (appTheme === "dark") {
      document.documentElement.classList.add("dark");
      document.documentElement.classList.remove("light");
    } else {
      document.documentElement.classList.add("light");
      document.documentElement.classList.remove("dark");
    }
  }, [appTheme]);

  const login = (email: string, userId?: string, name?: string, targetExam?: string) => {
    setIsLoggedIn(true);
    localStorage.setItem("prepwise_auth", "true");
    if (userId)     localStorage.setItem("prepwise_user_id",          userId);
    if (email)      localStorage.setItem("prepwise_user_email",        email);
    if (name)       localStorage.setItem("prepwise_user_name",         name);
    if (targetExam) localStorage.setItem("prepwise_user_target_exam",  targetExam);
    setData((prev) => ({
      ...prev,
      user: {
        ...prev.user,
        name:  name       ?? prev.user.name,
        email: email      || prev.user.email,
        badge: targetExam ? _examToBadge(targetExam) : prev.user.badge,
      },
    }));
  };

  const logout = () => {
    setIsLoggedIn(false);
    localStorage.removeItem("prepwise_auth");
    localStorage.removeItem("prepwise_user_id");
    localStorage.removeItem("prepwise_user_name");
    localStorage.removeItem("prepwise_user_email");
    localStorage.removeItem("prepwise_user_target_exam");
  };

  const addChatMessage = async (text: string) => {
    // Add the user message immediately
    const userMsg: ChatMessage = {
      id: Math.random().toString(),
      sender: "user",
      text,
    };
    setChatHistory((prev) => [...prev, userMsg]);
    setIsBotThinking(true);

    try {
      const apiBase = process.env.NEXT_PUBLIC_CHAT_API_BASE ?? "http://127.0.0.1:8001/api/v1";
      const res = await fetch(`${apiBase}/chat/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: getStoredUserId(),
          session_id: sessionId,   // null on first turn → server creates session
          query: text,
        }),
      });

      if (!res.ok) {
        throw new Error(`Server returned ${res.status}`);
      }

      const payload = await res.json();

      // Persist session ID for all subsequent turns in this session
      if (payload.session_id) {
        setSessionId(payload.session_id);
      }

      // ── Build the bot message text ────────────────────────────────────────
      let botText: string = payload.error
        ? `⚠️ ${payload.error}`
        : (payload.response ?? "");

      // Append follow-up suggestions as a numbered list the student can reply to
      if (payload.follow_up_suggestions?.length > 0) {
        botText += "\n\n─── Follow-up suggestions ───";
        (payload.follow_up_suggestions as Array<{
          content_type?: string;
          difficulty?: number | string;
          preview?: string;
        }>).forEach((s, i) => {
          const ctype = (s.content_type ?? "").toUpperCase();
          const diff  = s.difficulty ?? "?";
          const prev  = (s.preview ?? "").slice(0, 120);
          botText += `\n  ${i + 1}. [${ctype} | difficulty=${diff}] ${prev}…`;
        });
        botText += "\n\nReply with **1**, **2**, or **3** to see that content, or **skip** to continue.";
      }

      // Append quiz steps when a diagnostic quiz has just been triggered
      if (payload.quiz_steps?.length > 0) {
        botText += "\n\n─── Diagnostic Quiz ───";
        (payload.quiz_steps as Array<{
          step_num?: number;
          concept_id?: string;
          question?: string;
          hint?: string;
        }>).forEach((step) => {
          botText += `\n\nStep ${step.step_num ?? "?"} [${step.concept_id ?? ""}]`;
          botText += `\n  Q: ${step.question ?? ""}`;
          botText += `\n  Hint: ${step.hint ?? ""}`;
        });
        botText += "\n\nSubmit your answer as:\n`ANSWER::<your answer>::<correct|incorrect>::<error_type>::<seconds>`";
      }

      const botMsg: ChatMessage = {
        id: Math.random().toString(),
        sender: "bot",
        text: botText,
      };
      setChatHistory((prev) => [...prev, botMsg]);

    } catch (err) {
      const errMsg: ChatMessage = {
        id: Math.random().toString(),
        sender: "bot",
        text: "⚠️ Could not reach the tutor server. Make sure it is running on port 8001 (`python -m uvicorn api_server:app --port 8001`).",
      };
      setChatHistory((prev) => [...prev, errMsg]);
    } finally {
      setIsBotThinking(false);
    }
  };

  // Flush the Supabase session summary and clear local session state.
  // Called when the student clicks "End Session".
  const endSession = async () => {
    if (!sessionId) return;
    try {
      const apiBase = process.env.NEXT_PUBLIC_CHAT_API_BASE ?? "http://127.0.0.1:8001/api/v1";
      await fetch(`${apiBase}/chat/end-session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: getStoredUserId(), session_id: sessionId }),
      });
    } catch {
      // best-effort — never block navigation on a summary flush failure
    }
    setSessionId(null);
    setChatHistory([]);
  };

  return (
    <AppContext.Provider
      value={{
        isLoggedIn,
        login,
        logout,
        data,
        setData,
        chatHistory,
        addChatMessage,
        isBotThinking,
        sessionId,
        endSession,
        quizActive,
        setQuizActive,
        quizStep,
        setQuizStep,
        currentQuestionIndex,
        setCurrentQuestionIndex,
        selectedPracticeChapter,
        setSelectedPracticeChapter,
        selectedPracticeDifficulty,
        setSelectedPracticeDifficulty,
        selectedPracticeType,
        setSelectedPracticeType,
        prioritizeWeak,
        setPrioritizeWeak,
        appTheme,
        setAppTheme,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useApp() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error("useApp must be used within an AppProvider");
  }
  return context;
}
