"use client";

import React, { useState } from "react";
import { useApp } from "@/components/AppContext";

const API_BASE = (process.env.NEXT_PUBLIC_CHAT_API_BASE ?? "http://127.0.0.1:8001/api/v1").replace(/\/+$/, "");

export default function LoginScreen() {
  const { login } = useApp();
  const [activeTab, setActiveTab] = useState<"signin" | "signup">("signin");

  // Shared
  const [email, setEmail]       = useState("");
  const [password, setPassword] = useState("");
  const [error, setError]       = useState("");
  const [loading, setLoading]   = useState(false);

  // Create Account only
  const [name, setName]             = useState("");
  const [targetExam, setTargetExam] = useState("JEE_ADVANCED");

  // ── Sign In ────────────────────────────────────────────────────────────────
  const handleSignIn = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim())    { setError("Please enter your email.");    return; }
    if (!password)        { setError("Please enter your password."); return; }
    setError("");
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      if (res.status === 404) throw new Error("No account found with that email. Create an account first.");
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error((d as { detail?: string }).detail ?? `Server error ${res.status}`);
      }
      const data = await res.json() as { user_id: string; name: string; target_exam: string };
      login(email.trim(), data.user_id, data.name, data.target_exam);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed.");
    } finally {
      setLoading(false);
    }
  };

  // ── Create Account ─────────────────────────────────────────────────────────
  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim())         { setError("Please enter your name.");                      return; }
    if (!email.trim())        { setError("Please enter your email.");                     return; }
    if (password.length < 6)  { setError("Password must be at least 6 characters.");     return; }
    setError("");
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), email: email.trim(), password, target_exam: targetExam }),
      });
      if (res.status === 409) throw new Error("An account with this email already exists. Sign in instead.");
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error((d as { detail?: string }).detail ?? `Server error ${res.status}`);
      }
      const data = await res.json() as { user_id: string; name: string; target_exam: string };
      login(email.trim(), data.user_id, data.name, targetExam);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center dark:bg-[#05060c] bg-slate-50 px-4 font-sans dark:text-slate-100 text-slate-900 selection:bg-[#6258ff] selection:text-white relative overflow-hidden">
      <div className="absolute top-[-10%] left-[-10%] w-[50%] h-[50%] rounded-full bg-[#6258ff]/10 blur-[120px] pointer-events-none" />
      <div className="absolute bottom-[-10%] right-[-10%] w-[50%] h-[50%] rounded-full bg-[#1c6f32]/10 blur-[120px] pointer-events-none" />

      <div className="w-full max-w-[400px] flex flex-col items-center">
        {/* Brand */}
        <div className="mb-9 text-center">
          <div className="flex items-center justify-center gap-2.5 mb-2">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-[#776eff] to-[#5045ff] text-white shadow-[0_0_24px_rgba(98,88,255,0.4)]">
              <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4.5 w-4.5">
                <path d="M7 13.8a2.8 2.8 0 1 0 2.65 1.9h4.7A2.8 2.8 0 1 0 17 12.1V9.65A2.8 2.8 0 1 0 14.35 6H9.65A2.8 2.8 0 1 0 7 9.65v4.15Zm3-6.3h4m1.5 1.55v3.9m-6.2 3.25h5.4M7 9.65v4.15"
                  fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
              </svg>
            </span>
            <span className="text-xl font-bold tracking-tight dark:text-white text-slate-900">Prepwise</span>
          </div>
          <p className="text-[13px] dark:text-slate-400 text-slate-500 font-medium tracking-wide">
            Knows where you&apos;re stuck before you do.
          </p>
        </div>

        {/* Card */}
        <div className="w-full dark:bg-[#0d0e16] bg-white/80 border dark:border-[#1b1c2b] border-slate-200 rounded-2xl p-6.5 shadow-[0_20px_50px_rgba(0,0,0,0.4)] backdrop-blur-xl">

          {/* Tabs */}
          <div className="flex border-b dark:border-[#1b1c2b] border-slate-200 pb-4 mb-5">
            {(["signin", "signup"] as const).map((tab) => (
              <button key={tab} onClick={() => { setActiveTab(tab); setError(""); }}
                className={`flex-1 pb-2 text-[13px] font-semibold tracking-wide border-b-2 text-center transition-all ${
                  activeTab === tab
                    ? "border-[#6258ff] dark:text-white text-slate-900"
                    : "border-transparent dark:text-slate-500 text-slate-500 dark:hover:text-slate-300 text-slate-700"
                }`}>
                {tab === "signin" ? "Sign in" : "Create account"}
              </button>
            ))}
          </div>

          {error && (
            <div className="text-xs text-red-400 bg-red-950/20 border border-red-900/50 rounded-lg p-2.5 text-center mb-4">
              {error}
            </div>
          )}

          {/* ── SIGN IN ────────────────────────────────────────────────────── */}
          {activeTab === "signin" && (
            <form onSubmit={handleSignIn} className="space-y-4">
              <div>
                <label className="block text-[11px] font-semibold dark:text-slate-400 text-slate-500 uppercase tracking-wider mb-2">
                  Email
                </label>
                <input type="email" placeholder="you@example.com" value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full dark:bg-[#07080d] bg-white border dark:border-[#1b1c2b] border-slate-200 focus:border-[#6258ff]/80 focus:ring-1 focus:ring-[#6258ff]/30 dark:text-white text-slate-900 placeholder-slate-600 rounded-lg px-3.5 py-2.5 text-sm outline-none transition-all"
                />
              </div>

              <div>
                <label className="block text-[11px] font-semibold dark:text-slate-400 text-slate-500 uppercase tracking-wider mb-2">
                  Password
                </label>
                <input type="password" placeholder="••••••••" value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full dark:bg-[#07080d] bg-white border dark:border-[#1b1c2b] border-slate-200 focus:border-[#6258ff]/80 focus:ring-1 focus:ring-[#6258ff]/30 dark:text-white text-slate-900 placeholder-slate-600 rounded-lg px-3.5 py-2.5 text-sm outline-none transition-all"
                />
              </div>

              <button type="submit" disabled={loading}
                className="w-full bg-[#6258ff] hover:bg-[#5045ff] disabled:opacity-60 active:scale-[0.99] text-white font-semibold text-[13px] tracking-wide py-2.5 rounded-lg transition-all shadow-[0_4px_20px_rgba(98,88,255,0.25)] mt-2">
                {loading ? "Signing in…" : "Sign in"}
              </button>

              <button type="button" onClick={() => { setActiveTab("signup"); setError(""); }}
                className="w-full text-[12px] dark:text-slate-500 text-slate-500 hover:text-[#6258ff] transition-colors text-center pt-1">
                No account yet? Create one →
              </button>
            </form>
          )}

          {/* ── CREATE ACCOUNT ─────────────────────────────────────────────── */}
          {activeTab === "signup" && (
            <form onSubmit={handleRegister} className="space-y-4">
              <div>
                <label className="block text-[11px] font-semibold dark:text-slate-400 text-slate-500 uppercase tracking-wider mb-2">
                  Your Name
                </label>
                <input type="text" placeholder="e.g. Arjun Mehta" value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full dark:bg-[#07080d] bg-white border dark:border-[#1b1c2b] border-slate-200 focus:border-[#6258ff]/80 focus:ring-1 focus:ring-[#6258ff]/30 dark:text-white text-slate-900 placeholder-slate-600 rounded-lg px-3.5 py-2.5 text-sm outline-none transition-all"
                />
              </div>

              <div>
                <label className="block text-[11px] font-semibold dark:text-slate-400 text-slate-500 uppercase tracking-wider mb-2">
                  Email
                </label>
                <input type="email" placeholder="you@example.com" value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full dark:bg-[#07080d] bg-white border dark:border-[#1b1c2b] border-slate-200 focus:border-[#6258ff]/80 focus:ring-1 focus:ring-[#6258ff]/30 dark:text-white text-slate-900 placeholder-slate-600 rounded-lg px-3.5 py-2.5 text-sm outline-none transition-all"
                />
              </div>

              <div>
                <label className="block text-[11px] font-semibold dark:text-slate-400 text-slate-500 uppercase tracking-wider mb-2">
                  Password
                </label>
                <input type="password" placeholder="Min. 6 characters" value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full dark:bg-[#07080d] bg-white border dark:border-[#1b1c2b] border-slate-200 focus:border-[#6258ff]/80 focus:ring-1 focus:ring-[#6258ff]/30 dark:text-white text-slate-900 placeholder-slate-600 rounded-lg px-3.5 py-2.5 text-sm outline-none transition-all"
                />
              </div>

              <div>
                <label className="block text-[11px] font-semibold dark:text-slate-400 text-slate-500 uppercase tracking-wider mb-2">
                  Target Exam
                </label>
                <select value={targetExam} onChange={(e) => setTargetExam(e.target.value)}
                  className="w-full dark:bg-[#07080d] bg-white border dark:border-[#1b1c2b] border-slate-200 focus:border-[#6258ff]/80 dark:text-white text-slate-900 rounded-lg px-3.5 py-2.5 text-sm outline-none transition-all">
                  <option value="JEE_ADVANCED">JEE Advanced</option>
                  <option value="JEE_MAINS">JEE Mains</option>
                </select>
              </div>

              <button type="submit" disabled={loading}
                className="w-full bg-[#6258ff] hover:bg-[#5045ff] disabled:opacity-60 active:scale-[0.99] text-white font-semibold text-[13px] tracking-wide py-2.5 rounded-lg transition-all shadow-[0_4px_20px_rgba(98,88,255,0.25)] mt-2">
                {loading ? "Creating account…" : "Create account"}
              </button>

              <button type="button" onClick={() => { setActiveTab("signin"); setError(""); }}
                className="w-full text-[12px] dark:text-slate-500 text-slate-500 hover:text-[#6258ff] transition-colors text-center pt-1">
                Already have an account? Sign in →
              </button>
            </form>
          )}
        </div>

        <p className="mt-6 text-[10px] text-center dark:text-slate-600 text-slate-400 font-medium leading-relaxed tracking-wider uppercase">
          By continuing you agree to our{" "}
          <span className="text-[#6258ff] cursor-pointer hover:underline">Terms</span> &{" "}
          <span className="text-[#6258ff] cursor-pointer hover:underline">Privacy Policy</span>
        </p>
      </div>
    </div>
  );
}
