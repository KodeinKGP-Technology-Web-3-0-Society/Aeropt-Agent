"use client";

import React, { useState } from "react";
import { useApp } from "@/components/AppContext";

const API_BASE = (process.env.NEXT_PUBLIC_CHAT_API_BASE ?? "http://127.0.0.1:8001/api/v1").replace(/\/+$/, "");

function getStoredUserId(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("prepwise_user_id") ?? "";
}

export default function SettingsContent() {
  const { data, setData, logout, appTheme, setAppTheme } = useApp();
  
  const [name, setName] = useState(data.user.name);
  const [email, setEmail] = useState(data.user.email);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [alerts, setAlerts] = useState(true);
  const [strategy, setStrategy] = useState("advanced");

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaveError("");
    const userId = getStoredUserId();
    if (!userId) {
      setSaveError("No user ID found — please sign out and sign back in.");
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/auth/user/${userId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), email: email.trim() }),
      });
      if (res.status === 409) throw new Error("That email is already in use by another account.");
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error((d as { detail?: string }).detail ?? `Server error ${res.status}`);
      }
      // Persist locally so sidebar + header stay correct after this update
      if (name.trim())  localStorage.setItem("prepwise_user_name",  name.trim());
      if (email.trim()) localStorage.setItem("prepwise_user_email", email.trim());
      setData((prev) => ({ ...prev, user: { ...prev.user, name: name.trim(), email: email.trim() } }));
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed.");
    }
  };

  return (
    <div className="flex-1 p-6 lg:p-8 overflow-y-auto max-h-screen">
      {/* Header */}
      <div className="mb-8 select-none">
        <h1 className="text-xl lg:text-2xl font-bold tracking-tight dark:text-white text-slate-900">Settings</h1>
        <p className="text-xs dark:text-slate-400 text-slate-500 font-medium mt-1">Configure your JEE Copilot and account settings.</p>
      </div>

      <div className="max-w-[620px] space-y-6">
        {saved && (
          <div className="text-xs text-emerald-400 bg-emerald-950/20 border border-emerald-900/50 rounded-xl p-3 text-center font-bold tracking-wide">
            ✓ Profile updated successfully
          </div>
        )}
        {saveError && (
          <div className="text-xs text-red-400 bg-red-950/20 border border-red-900/50 rounded-xl p-3 text-center font-bold tracking-wide">
            {saveError}
          </div>
        )}

        {/* Profile Card */}
        <div className="dark:bg-[#0d0e16] bg-white border dark:border-[#1b1c2b] border-slate-200 rounded-2xl p-6 shadow-xl">
          <h3 className="text-xs font-bold dark:text-white text-slate-900 uppercase tracking-widest mb-4">Profile details</h3>
          <form onSubmit={handleSave} className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-[10px] font-bold dark:text-slate-500 text-slate-500 uppercase tracking-wider mb-2">
                  Full Name
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full dark:bg-[#07080d] bg-white border dark:border-[#1b1c2b] border-slate-200 focus:border-[#6258ff]/80 text-xs dark:text-white text-slate-900 rounded-lg p-3 outline-none transition-colors"
                />
              </div>
              <div>
                <label className="block text-[10px] font-bold dark:text-slate-500 text-slate-500 uppercase tracking-wider mb-2">
                  Target Exam
                </label>
                <div className="w-full dark:bg-[#07080d] bg-slate-50 border dark:border-[#1b1c2b] border-slate-200 text-xs dark:text-slate-400 text-slate-500 rounded-lg p-3 select-none">
                  {data.user.badge || "—"}
                </div>
              </div>
            </div>

            <div>
              <label className="block text-[10px] font-bold dark:text-slate-500 text-slate-500 uppercase tracking-wider mb-2">
                Registered Email
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full dark:bg-[#07080d] bg-white border dark:border-[#1b1c2b] border-slate-200 focus:border-[#6258ff]/80 text-xs dark:text-white text-slate-900 rounded-lg p-3 outline-none transition-colors"
              />
            </div>

            <button
              type="submit"
              className="bg-[#6258ff] hover:bg-[#5045ff] text-white font-semibold text-xs uppercase tracking-wider px-5 py-2.5 rounded-lg transition-all shadow-[0_4px_15px_rgba(98,88,255,0.25)] cursor-pointer mt-2"
            >
              Save Changes
            </button>
          </form>
        </div>

        {/* Study Preferences Card */}
        <div className="dark:bg-[#0d0e16] bg-white border dark:border-[#1b1c2b] border-slate-200 rounded-2xl p-6 shadow-xl space-y-4">
          <h3 className="text-xs font-bold dark:text-white text-slate-900 uppercase tracking-widest">Study Preferences</h3>
          
          <div className="flex items-center justify-between py-2 border-b dark:border-[#1b1c2b] border-slate-200/50">
            <div>
              <p className="text-xs font-bold dark:text-slate-300 text-slate-700">Daily Study Reminders</p>
              <p className="text-[10px] dark:text-slate-500 text-slate-500 font-semibold mt-0.5 uppercase tracking-wide">Get alerts when your daily streak is in danger</p>
            </div>
            <button
              onClick={() => setAlerts(!alerts)}
              className={`w-10 h-5.5 rounded-full p-0.5 transition-colors cursor-pointer flex items-center ${
                alerts ? "bg-[#1c6f32]" : "dark:bg-[#1b1c2b] bg-slate-200"
              }`}
            >
              <span
                className={`h-4.5 w-4.5 rounded-full bg-white transition-transform ${
                  alerts ? "translate-x-4.5" : "translate-x-0"
                }`}
              />
            </button>
          </div>

          <div className="flex items-center justify-between py-2 border-b dark:border-[#1b1c2b] border-slate-200/50">
            <div>
              <p className="text-xs font-bold dark:text-slate-300 text-slate-700">Target Level Strategy</p>
              <p className="text-[10px] dark:text-slate-500 text-slate-500 font-semibold mt-0.5 uppercase tracking-wide">Align recommendation algorithms with target</p>
            </div>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className="dark:bg-[#07080d] bg-white border dark:border-[#1b1c2b] border-slate-200 text-[11px] font-bold dark:text-white text-slate-900 rounded-lg px-3 py-1.5 outline-none cursor-pointer"
            >
              <option value="advanced">JEE Advanced (Conceptual focus)</option>
              <option value="main">JEE Main (Speed & Formula focus)</option>
              <option value="boards">Boards (Descriptive focus)</option>
            </select>
          </div>
          
          <div className="flex items-center justify-between py-2 border-b dark:border-[#1b1c2b] border-slate-200/50">
            <div>
              <p className="text-xs font-bold dark:text-slate-300 text-slate-700">Application Theme</p>
              <p className="text-[10px] dark:text-slate-500 text-slate-500 font-semibold mt-0.5 uppercase tracking-wide">Switch between Light and Dark mode</p>
            </div>
            <select
              value={appTheme}
              onChange={(e) => setAppTheme(e.target.value as "light" | "dark")}
              className="dark:bg-[#07080d] bg-white border dark:border-[#1b1c2b] border-slate-200 text-[11px] font-bold dark:text-white text-slate-900 rounded-lg px-3 py-1.5 outline-none cursor-pointer"
            >
              <option value="dark">Dark Mode</option>
              <option value="light">Light Mode</option>
            </select>
          </div>
        </div>

        {/* Danger Zone */}
        <div className="bg-red-950/10 border border-red-900/30 rounded-2xl p-6 shadow-xl space-y-4">
          <h3 className="text-xs font-bold text-red-400 uppercase tracking-widest">Danger Zone</h3>
          <p className="text-xs dark:text-slate-400 text-slate-500 font-medium">Log out from your current learning session on this device.</p>
          <button
            onClick={logout}
            className="bg-transparent hover:bg-red-950/25 border border-red-900/50 hover:border-red-600 text-xs font-bold text-red-400 uppercase tracking-wider px-5 py-2.5 rounded-lg transition-all cursor-pointer"
          >
            Sign Out Account
          </button>
        </div>
      </div>
    </div>
  );
}
