"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useApp, Chapter, Concept } from "@/components/AppContext";

export default function DashboardContent() {
  const router = useRouter();
  const { data, setData, setQuizActive } = useApp();
  const [activeTab, setActiveTab] = useState("Laws of Motion");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchDashboard = useCallback(() => {
    setLoading(true);
    setError(null);

    const base =
      process.env.NEXT_PUBLIC_API_BASE ||
      "http://127.0.0.1:8000/api/v1";
    const url = `${base.replace(/\/+$/, "")}/analytics/dashboard/`;

    const token =
      localStorage.getItem("access_token") ||
      localStorage.getItem("token") ||
      localStorage.getItem("auth_token") ||
      "";
    const headers: Record<string, string> = {};
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }

    fetch(url, { headers })
      .then((res) => {
        if (!res.ok) throw new Error(`Server responded with ${res.status}`);
        return res.json();
      })
      .then((resData) => {
        setData((prev) => ({
          ...prev,
          dashboard: resData,
        }));
        setLoading(false);
      })
      .catch(() => {
        // Django analytics backend is not running — silently keep mock data
        setLoading(false);
      });
  }, [setData]);

  useEffect(() => {
    fetchDashboard();
  }, [fetchDashboard]);

  const { stats, conceptHealth } = data.dashboard;

  const currentChapter =
    conceptHealth.chapters.find((ch) => ch.name === activeTab) ||
    conceptHealth.chapters[0];

  const handleStartSession = () => {
    // Navigate to chat and activate diagnostic quiz
    setQuizActive(true);
    router.push("/chat");
  };

  const handleConceptClick = (concept: Concept) => {
    if (concept.tag === "WEAK") {
      setQuizActive(true);
      router.push("/chat");
    } else {
      router.push("/chat");
    }
  };

  // ── Loading Skeleton ─────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="flex-1 p-6 lg:p-8 overflow-y-auto max-h-screen animate-pulse">
        {/* Header skeleton */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
          <div>
            <div className="h-7 w-56 dark:bg-[#1b1c2b] bg-slate-200 rounded-lg" />
            <div className="h-4 w-72 dark:bg-[#1b1c2b] bg-slate-200/60 rounded mt-2" />
          </div>
          <div className="h-10 w-36 dark:bg-[#1b1c2b] bg-slate-200 rounded-lg" />
        </div>
        {/* Stats cards skeleton */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-8">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="dark:bg-[#0d0e16] bg-white border dark:border-[#1b1c2b] border-slate-200 rounded-xl p-5 shadow-lg h-[120px]"
            >
              <div className="h-3 w-28 dark:bg-[#1b1c2b] bg-slate-200 rounded mb-4" />
              <div className="h-8 w-16 dark:bg-[#1b1c2b] bg-slate-200 rounded mb-2" />
              <div className="h-3 w-40 dark:bg-[#1b1c2b] bg-slate-200/60 rounded" />
            </div>
          ))}
        </div>
        {/* Concept health skeleton */}
        <div className="dark:bg-[#0d0e16] bg-white border dark:border-[#1b1c2b] border-slate-200 rounded-2xl p-6 shadow-xl">
          <div className="flex justify-between items-center border-b dark:border-[#1b1c2b] border-slate-200 pb-4 mb-6">
            <div>
              <div className="h-4 w-48 dark:bg-[#1b1c2b] bg-slate-200 rounded mb-2" />
              <div className="h-3 w-64 dark:bg-[#1b1c2b] bg-slate-200/60 rounded" />
            </div>
            <div className="flex gap-2">
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="h-8 w-28 dark:bg-[#1b1c2b] bg-slate-200 rounded-md"
                />
              ))}
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <div
                key={i}
                className="p-4 border dark:border-[#1b1c2b] border-slate-200 rounded-xl dark:bg-[#080911]/50 bg-slate-50 h-[105px]"
              >
                <div className="h-4 w-32 dark:bg-[#1b1c2b] bg-slate-200 rounded mb-4" />
                <div className="flex justify-between items-end">
                  <div className="h-3 w-16 dark:bg-[#1b1c2b] bg-slate-200/60 rounded" />
                  <div className="h-3 w-10 dark:bg-[#1b1c2b] bg-slate-200 rounded" />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ── Error State ──────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="flex-1 p-6 lg:p-8 overflow-y-auto max-h-screen flex items-center justify-center">
        <div className="dark:bg-[#0d0e16] bg-white border border-red-900/30 rounded-2xl p-8 shadow-xl max-w-md w-full text-center">
          <div className="w-14 h-14 mx-auto mb-4 rounded-full bg-red-950/40 flex items-center justify-center">
            <svg
              className="h-7 w-7 text-red-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
              />
            </svg>
          </div>
          <h3 className="text-base font-bold dark:text-white text-slate-900 mb-2">
            Failed to Load Dashboard
          </h3>
          <p className="text-xs dark:text-slate-400 text-slate-500 mb-5 leading-relaxed">
            {error}. Make sure the backend server is running.
          </p>
          <button
            onClick={fetchDashboard}
            className="inline-flex items-center gap-2 bg-[#6258ff] hover:bg-[#5045ff] text-white font-semibold text-xs tracking-wider uppercase px-5 py-2.5 rounded-lg shadow-[0_4px_18px_rgba(98,88,255,0.25)] transition-all cursor-pointer active:scale-[0.98]"
          >
            <svg
              className="h-3.5 w-3.5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth="2.5"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182"
              />
            </svg>
            Retry
          </button>
        </div>
      </div>
    );
  }

  // ── Dashboard Content ────────────────────────────────────────────────
  return (
    <div className="flex-1 p-6 lg:p-8 overflow-y-auto max-h-screen">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="text-xl lg:text-2xl font-bold tracking-tight dark:text-white text-slate-900 select-none">
            Good morning, {data.user.name.split(" ")[0]}
          </h1>
          <p className="text-xs dark:text-slate-400 text-slate-500 font-medium mt-1">
            Let's cover your prerequisite gaps today.
          </p>
        </div>
        <button
          onClick={handleStartSession}
          className="inline-flex items-center justify-center gap-2 bg-[#6258ff] hover:bg-[#5045ff] active:scale-[0.98] text-white font-semibold text-xs tracking-wider uppercase px-5 py-2.5 rounded-lg shadow-[0_4px_18px_rgba(98,88,255,0.25)] transition-all cursor-pointer"
        >
          <svg
            aria-hidden="true"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth="2.5"
            className="h-3.5 w-3.5"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.348a1.125 1.125 0 010 1.971l-11.54 6.347a1.125 1.125 0 01-1.667-.985V5.653z" />
          </svg>
          Start Session
        </button>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-8">
        {stats.map((stat) => {
          if (stat.id === "proficiency") {
            return (
              <div
                key={stat.id}
                className="dark:bg-[#0d0e16] bg-white border dark:border-[#1b1c2b] border-slate-200 rounded-xl p-5 shadow-lg relative overflow-hidden"
              >
                <div className="flex justify-between items-start">
                  <div>
                    <span className="text-[11px] font-bold dark:text-slate-400 text-slate-500 uppercase tracking-wider">{stat.label}</span>
                    <h2 className="text-3xl font-extrabold dark:text-white text-slate-900 mt-2 mb-1">{stat.value}</h2>
                    <p className="text-xs dark:text-slate-500 text-slate-500 font-medium">{stat.subtext}</p>
                  </div>
                  <div className="h-14 w-14 rounded-full border-4 border-[#121126] border-t-[#6258ff] flex items-center justify-center text-[11px] font-bold dark:text-white text-slate-900 shadow-[0_0_15px_rgba(98,88,255,0.15)]">
                    {stat.value}
                  </div>
                </div>
              </div>
            );
          }

          if (stat.id === "weak_concepts") {
            return (
              <div
                key={stat.id}
                className="dark:bg-[#0d0e16] bg-white border dark:border-[#1b1c2b] border-slate-200 rounded-xl p-5 shadow-lg relative overflow-hidden"
              >
                <div>
                  <span className="text-[11px] font-bold dark:text-slate-400 text-slate-500 uppercase tracking-wider">{stat.label}</span>
                  <h2 className="text-3xl font-extrabold dark:text-white text-slate-900 mt-2 mb-2">{stat.value}</h2>
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {stat.list?.map((concept) => (
                      <span
                        key={concept}
                        onClick={() => router.push("/chat")}
                        className="cursor-pointer text-[9px] font-bold text-[#f87171] border border-[#f87171]/20 bg-[#f87171]/5 px-2 py-0.5 rounded hover:bg-[#f87171]/10 transition-colors"
                      >
                        {concept}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            );
          }

          // Streak Card — 7 dots per the spec (one per day of the week)
          const streakValue = parseInt(stat.value) || 0;
          return (
            <div
              key={stat.id}
              className="dark:bg-[#0d0e16] bg-white border dark:border-[#1b1c2b] border-slate-200 rounded-xl p-5 shadow-lg relative overflow-hidden"
            >
              <div className="flex justify-between items-start">
                <div>
                  <span className="text-[11px] font-bold dark:text-slate-400 text-slate-500 uppercase tracking-wider">{stat.label}</span>
                  <h2 className="text-3xl font-extrabold dark:text-white text-slate-900 mt-2 mb-1 flex items-center gap-1.5">
                    {stat.value}
                    <span className="text-orange-500 animate-pulse text-2xl">🔥</span>
                  </h2>
                  <p className="text-xs dark:text-slate-500 text-slate-500 font-medium">{stat.subtext}</p>
                </div>
                {/* Visual streak dots — 7 dots (one per day of the week) */}
                <div className="flex gap-1.5 mt-1.5">
                  {(stat.dots || [false, false, false, false, false, false, false]).map((active, idx) => {
                    const isActive = stat.dots ? active : (idx + 1) <= streakValue;
                    return (
                      <span
                        key={idx}
                        className={`h-2.5 w-2.5 rounded-full transition-all duration-300 ${
                          isActive
                            ? "bg-gradient-to-br from-orange-400 to-red-500 shadow-[0_0_8px_rgba(249,115,22,0.5)]"
                            : "dark:bg-[#1b1c2b] bg-slate-200 border border-[#2a2b3d]"
                        }`}
                      />
                    );
                  })}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Physics Concept Health */}
      <div className="dark:bg-[#0d0e16] bg-white border dark:border-[#1b1c2b] border-slate-200 rounded-2xl p-6 shadow-xl">
        <div className="flex flex-col md:flex-row md:items-center justify-between border-b dark:border-[#1b1c2b] border-slate-200 pb-4 mb-6 gap-3">
          <div>
            <h3 className="text-sm font-bold dark:text-white text-slate-900 uppercase tracking-wider">{conceptHealth.title}</h3>
            <div className="flex items-center gap-1.5 text-[11px] dark:text-slate-400 text-slate-500 mt-1 font-medium">
              <span>Chapters:</span>
              <span className="dark:text-slate-300 text-slate-700 font-semibold">{conceptHealth.tabs.join(" · ")}</span>
            </div>
          </div>
          <div className="flex dark:bg-[#07080d] bg-white p-1 border dark:border-[#1b1c2b] border-slate-200 rounded-lg">
            {conceptHealth.tabs.map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-all cursor-pointer ${
                  activeTab === tab
                    ? "dark:bg-[#121126] bg-slate-100 border dark:border-[#3d3d91] border-indigo-200/50 text-[#8584ff]"
                    : "dark:text-slate-400 text-slate-500 dark:hover:text-slate-200 hover:text-slate-700"
                }`}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>

        {/* Active Chapter Details */}
        {currentChapter && (
          <div className="mb-5 flex justify-between items-center dark:bg-[#07080d] bg-white/50 p-4 border dark:border-[#1b1c2b] border-slate-200/50 rounded-xl">
            <div className="min-w-0">
              <h4 className="text-sm font-bold dark:text-white text-slate-900 truncate">{currentChapter.name}</h4>
              <p className="text-xs dark:text-slate-500 text-slate-500 font-medium mt-0.5">{currentChapter.conceptCount} concepts inside</p>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs font-bold dark:text-slate-400 text-slate-500 uppercase tracking-wider">Chapter Proficiency</span>
              <div className="flex items-center gap-1.5">
                <span className={`text-base font-extrabold ${currentChapter.overallScore < 50 ? "text-red-400" : currentChapter.overallScore < 75 ? "text-yellow-400" : "text-emerald-400"}`}>
                  {currentChapter.overallScore}%
                </span>
                <div className="w-16 dark:bg-[#121126] bg-slate-100 h-1.5 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${currentChapter.overallScore < 50 ? "bg-red-400" : currentChapter.overallScore < 75 ? "bg-yellow-400" : "bg-emerald-400"}`}
                    style={{ width: `${currentChapter.overallScore}%` }}
                  />
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Concept Cards Grid */}
        {currentChapter && currentChapter.concepts.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {currentChapter.concepts.map((concept) => {
              const isWeak = concept.tag === "WEAK";
              const isStrong = concept.tag === "STRONG";
              return (
                <div
                  key={concept.name}
                  onClick={() => handleConceptClick(concept)}
                  className={`p-4 border rounded-xl dark:bg-[#080911]/50 bg-white dark:hover:bg-[#101021] hover:bg-slate-50 cursor-pointer transition-all hover:scale-[1.01] flex flex-col justify-between h-[105px] group ${
                    isWeak
                      ? "border-red-950/40 hover:border-red-900/60 shadow-[0_0_15px_rgba(239,68,68,0.03)]"
                      : isStrong
                      ? "border-emerald-950/40 hover:border-emerald-900/60"
                      : "dark:border-[#1b1c2b] border-slate-200 dark:hover:border-[#3d3d91] hover:border-indigo-200"
                  }`}
                >
                  <div className="flex justify-between items-start gap-2">
                    <span className="text-xs font-bold dark:text-slate-200 text-slate-700 tracking-wide dark:group-hover:text-white group-hover:text-slate-900 transition-colors truncate">
                      {concept.name}
                    </span>
                    {isWeak && (
                      <span className="text-[8px] font-extrabold text-red-400 border border-red-500/20 bg-red-950/40 px-1.5 py-0.5 rounded uppercase tracking-wider animate-pulse flex-none">
                        Weak
                      </span>
                    )}
                    {isStrong && (
                      <span className="text-[8px] font-extrabold text-emerald-400 border border-emerald-500/20 bg-emerald-950/40 px-1.5 py-0.5 rounded uppercase tracking-wider flex-none">
                        Strong
                      </span>
                    )}
                  </div>

                  <div className="flex justify-between items-end mt-4">
                    <div className="text-[10px] dark:text-slate-500 text-slate-500 font-semibold">
                      {concept.attempts} attempts
                    </div>
                    <div className="flex flex-col items-end">
                      <span className={`text-xs font-extrabold ${concept.score < 50 ? "text-red-400" : concept.score < 75 ? "text-yellow-400" : "text-emerald-400"}`}>
                        {concept.score}%
                      </span>
                      <div className="w-12 dark:bg-[#121126] bg-slate-100 h-1 rounded-full overflow-hidden mt-1">
                        <div
                          className={`h-full rounded-full transition-all duration-500 ${concept.score < 50 ? "bg-red-400" : concept.score < 75 ? "bg-yellow-400" : "bg-emerald-400"}`}
                          style={{ width: `${concept.score}%` }}
                        />
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="text-center py-12">
            <p className="text-sm dark:text-slate-500 text-slate-500 font-medium">No concept data yet. Start a study session to begin tracking your progress.</p>
          </div>
        )}
      </div>
    </div>
  );
}
