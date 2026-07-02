"use client";

import React, { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useApp, ChatMessage } from "@/components/AppContext";

export default function ChatContent() {
  const router = useRouter();
  const {
    data,
    chatHistory,
    addChatMessage,
    isBotThinking,
    endSession,
    quizActive,
    setQuizActive,
    quizStep,
    setQuizStep,
  } = useApp();

  const [inputVal, setInputVal] = useState("");
  const [selectedOption, setSelectedOption] = useState<string | null>(null);
  const [quizSubmitted, setQuizSubmitted] = useState(false);
  const [quizFinished, setQuizFinished] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  const { header, sidebar, diagnosticQuiz } = data.chat;

  // Auto-scroll chat to bottom
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatHistory]);

  const handleSend = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputVal.trim()) return;
    addChatMessage(inputVal);
    setInputVal("");
  };

  const handleSuggestClick = (q: string) => {
    addChatMessage(q);
  };

  const cleanMathSymbols = (str: string) => {
    return str
      .replace(/\\tau/g, "τ")
      .replace(/\\theta/g, "θ")
      .replace(/\\alpha/g, "α")
      .replace(/\\times/g, "×")
      .replace(/\\cdot/g, "·")
      .replace(/\\sin/g, "sin")
      .replace(/\\cos/g, "cos")
      .replace(/\\text\{\s*m\}/g, " m")
      .replace(/\\text\{\s*N\}/g, " N")
      .replace(/\\text\{\s*kg\}/g, " kg")
      .replace(/\\frac\{(.*?)\}\{(.*?)\}/g, "($1/$2)")
      .replace(/\\\^/g, "^")
      .replace(/\^2/g, "²")
      .replace(/\^3/g, "³")
      .replace(/\\circ/g, "°");
  };

  const renderMath = (text: string) => {
    if (!text) return "";
    let formatted = text;

    // Replace block math $$...$$
    formatted = formatted.replace(/\$\$(.*?)\$\$/g, (_, match) => {
      let math = cleanMathSymbols(match);
      return `<div class="my-3 py-2 px-3.5 dark:bg-[#05060a] bg-slate-50 border dark:border-[#1b1c2b] border-slate-200 rounded-lg text-center font-mono text-sm text-[#8584ff] overflow-x-auto shadow-inner select-all">${math}</div>`;
    });

    // Replace inline math $...$
    formatted = formatted.replace(/\$(.*?)\$/g, (_, match) => {
      let math = cleanMathSymbols(match);
      return `<span class="px-1.5 py-0.5 dark:bg-[#0d0e16] bg-white border dark:border-[#212239] border-slate-300 rounded font-mono text-xs text-[#8584ff] select-all">${math}</span>`;
    });

    return <span dangerouslySetInnerHTML={{ __html: formatted }} />;
  };

  const handleQuizSubmit = () => {
    if (!selectedOption) return;
    setQuizSubmitted(true);
  };

  const handleQuizNext = () => {
    if (quizStep < 4) {
      setQuizStep(quizStep + 1);
      setSelectedOption(null);
      setQuizSubmitted(false);
    } else {
      setQuizFinished(true);
    }
  };

  const resetQuiz = () => {
    setQuizActive(false);
    setQuizStep(2);
    setSelectedOption(null);
    setQuizSubmitted(false);
    setQuizFinished(false);
  };

  return (
    <div className="flex-1 flex flex-col min-h-screen dark:bg-[#05060c] bg-slate-50 dark:text-white text-slate-900">
      {/* Session Top Nav Bar */}
      <header className="h-[56px] border-b dark:border-[#1b1c2b] border-slate-200 flex items-center justify-between px-6 dark:bg-[#07080d] bg-white select-none flex-none">
        <div className="flex items-center gap-3">
          <span className="text-xs font-bold dark:text-slate-400 text-slate-500 uppercase tracking-wider">{header.title}</span>
          <div className="flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-red-400 animate-pulse" />
            <span className="text-xs font-bold dark:text-white text-slate-900 border border-[#f87171]/20 bg-[#f87171]/5 px-2 py-0.5 rounded uppercase tracking-wider">
              {header.concept} (WEAK)
            </span>
            <span className="text-[10px] font-bold dark:text-slate-500 text-slate-500">·</span>
            <span className="text-xs font-bold dark:text-slate-400 text-slate-500 border dark:border-[#1b1c2b] border-slate-200 dark:bg-[#111124] bg-slate-100 px-2 py-0.5 rounded uppercase tracking-wider">
              {header.secondaryConcept}
            </span>
          </div>
        </div>
        <button
          onClick={async () => {
            setQuizActive(false);
            await endSession();
            router.push("/dashboard");
          }}
          className="text-xs font-semibold dark:text-slate-400 text-slate-500 hover:text-red-400 border dark:border-[#212239] border-slate-300 hover:border-red-950 px-3 py-1.5 rounded-lg transition-colors cursor-pointer"
        >
          End Session
        </button>
      </header>

      {/* Main Content Area split */}
      <div className="flex-1 flex flex-col lg:flex-row overflow-hidden min-h-0">
        
        {/* Left Side: Chat Workspace or Diagnostic Quiz */}
        <div className="flex-1 flex flex-col min-h-0 border-r dark:border-[#15162a] border-slate-200/80 dark:bg-[#06070e] bg-slate-50">
          
          {quizActive ? (
            /* DIAGNOSTIC QUIZ PANEL */
            <div className="flex-1 p-6 lg:p-10 overflow-y-auto flex flex-col justify-between max-w-[680px] mx-auto w-full">
              {!quizFinished ? (
                <>
                  {/* Active Quiz steps */}
                  <div>
                    <div className="flex justify-between items-center mb-6">
                      <span className="text-[11px] font-bold text-[#6258ff] uppercase tracking-widest">{diagnosticQuiz.title}</span>
                      <span className="text-xs font-bold dark:text-slate-400 text-slate-500 dark:bg-[#0d0e16] bg-white border dark:border-[#1b1c2b] border-slate-200 px-2.5 py-1 rounded-full">
                        Step {quizStep} of {diagnosticQuiz.totalSteps}
                      </span>
                    </div>

                    <div className="mb-2">
                      <h2 className="text-base font-extrabold dark:text-white text-slate-900 tracking-wide">{diagnosticQuiz.topic}</h2>
                      <p className="text-xs text-[#8584ff] font-semibold mt-1 uppercase tracking-wider">{diagnosticQuiz.subTitle}</p>
                    </div>

                    <div className="my-6 p-5 dark:bg-[#0d0e16] bg-white/80 border dark:border-[#1b1c2b] border-slate-200 rounded-2xl shadow-xl">
                      <p className="text-sm font-medium dark:text-slate-200 text-slate-800 leading-relaxed">
                        {diagnosticQuiz.questionText}
                      </p>
                    </div>

                    {/* Options */}
                    <div className="space-y-3">
                      {diagnosticQuiz.options.map((opt) => {
                        const isSelected = selectedOption === opt.label;
                        const isCorrect = opt.label === diagnosticQuiz.correctAnswer;
                        let optionClass = "dark:border-[#1b1c2b] border-slate-200 dark:bg-[#07080d] bg-white/40 dark:text-slate-300 text-slate-700 dark:hover:border-[#3d3d91] border-indigo-200/50 dark:hover:bg-[#101021] bg-slate-100/30";
                        
                        if (quizSubmitted) {
                          if (isCorrect) {
                            optionClass = "border-emerald-500/40 bg-emerald-950/20 text-emerald-300 shadow-[0_0_12px_rgba(16,185,129,0.08)]";
                          } else if (isSelected) {
                            optionClass = "border-red-500/40 bg-red-950/20 text-red-300 shadow-[0_0_12px_rgba(239,68,68,0.08)]";
                          }
                        } else if (isSelected) {
                          optionClass = "border-[#6258ff] dark:bg-[#121126] bg-slate-100 dark:text-white text-slate-900 shadow-[0_0_15px_rgba(98,88,255,0.1)]";
                        }

                        return (
                          <button
                            key={opt.label}
                            disabled={quizSubmitted}
                            onClick={() => setSelectedOption(opt.label)}
                            className={`w-full text-left p-4 border rounded-xl flex items-center gap-4 transition-all duration-200 font-sans cursor-pointer ${optionClass}`}
                          >
                            <span className={`h-6 w-6 rounded-lg flex items-center justify-center font-bold text-xs ${
                              isSelected 
                                ? "bg-[#6258ff] text-white" 
                                : "dark:bg-[#111124] bg-slate-100 dark:text-slate-500 text-slate-500 border dark:border-[#212239] border-slate-300"
                            }`}>
                              {opt.label}
                            </span>
                            <span className="text-xs font-semibold">{opt.text}</span>
                          </button>
                        );
                      })}
                    </div>

                    {/* Hint overlay */}
                    {quizSubmitted && selectedOption !== diagnosticQuiz.correctAnswer && (
                      <div className="mt-5 p-4 bg-orange-950/10 border border-orange-500/10 rounded-xl flex items-start gap-3">
                        <span className="text-orange-400 mt-0.5">💡</span>
                        <p className="text-[11px] font-semibold text-orange-300 leading-relaxed uppercase tracking-wider">
                          Hint: {diagnosticQuiz.hint}
                        </p>
                      </div>
                    )}
                  </div>

                  {/* Submission triggers */}
                  <div className="mt-8 pt-4 border-t dark:border-[#1b1c2b] border-slate-200/50">
                    {!quizSubmitted ? (
                      <button
                        onClick={handleQuizSubmit}
                        disabled={!selectedOption}
                        className="w-full bg-[#6258ff] hover:bg-[#5045ff] disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold text-xs uppercase tracking-wider py-3 rounded-xl transition-all shadow-[0_4px_15px_rgba(98,88,255,0.2)]"
                      >
                        Submit Answer
                      </button>
                    ) : (
                      <button
                        onClick={handleQuizNext}
                        className="w-full bg-[#1c6f32] hover:bg-[#155326] dark:text-white text-slate-900 font-semibold text-xs uppercase tracking-wider py-3 rounded-xl transition-all shadow-[0_4px_15px_rgba(28,111,50,0.2)]"
                      >
                        {quizStep < 4 ? "Next Step" : "Complete Quiz"}
                      </button>
                    )}
                  </div>
                </>
              ) : (
                /* QUIZ COMPLETED OVERLAY */
                <div className="flex-1 flex flex-col justify-center items-center text-center p-4">
                  <div className="h-14 w-14 rounded-full bg-emerald-950/40 border border-emerald-500/20 flex items-center justify-center text-emerald-400 mb-6 shadow-[0_0_20px_rgba(16,185,129,0.15)]">
                    <svg aria-hidden="true" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5" className="h-6 w-6">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                    </svg>
                  </div>
                  <h2 className="text-xl font-bold tracking-tight dark:text-white text-slate-900 mb-2">Quiz Complete!</h2>
                  
                  {/* Step dots */}
                  <div className="flex gap-2.5 my-4">
                    {[1, 2, 3, 4].map((s) => (
                      <div key={s} className="flex flex-col items-center gap-1">
                        <span className="h-6 w-6 rounded-full bg-[#1c6f32] dark:text-white text-slate-900 text-[10px] font-bold flex items-center justify-center border border-emerald-500/30">✓</span>
                        <span className="text-[9px] dark:text-slate-500 text-slate-500 font-semibold uppercase">Step {s}</span>
                      </div>
                    ))}
                  </div>

                  <div className="dark:bg-[#0d0e16] bg-white border dark:border-[#1b1c2b] border-slate-200 rounded-2xl p-5 my-6 max-w-[480px]">
                    <h3 className="text-xs font-bold text-red-400 uppercase tracking-widest mb-2">
                      Your weak point: {diagnosticQuiz.weakPoint.title}
                    </h3>
                    <p className="text-xs dark:text-slate-400 text-slate-500 leading-relaxed font-medium">
                      {diagnosticQuiz.weakPoint.description}
                    </p>
                  </div>

                  <div className="flex flex-col sm:flex-row gap-3.5 w-full max-w-[400px]">
                    <button
                      onClick={resetQuiz}
                      className="flex-1 dark:bg-[#121126] bg-slate-100 border dark:border-[#3d3d91] border-indigo-200/50 dark:text-white text-slate-900 font-semibold text-xs uppercase tracking-wider py-3 rounded-xl hover:bg-[#171635] transition-all cursor-pointer"
                    >
                      Review this concept
                    </button>
                    <button
                      onClick={() => {
                        setQuizActive(false);
                        router.push("/dashboard");
                      }}
                      className="flex-1 bg-[#6258ff] hover:bg-[#5045ff] text-white font-semibold text-xs uppercase tracking-wider py-3 rounded-xl transition-all shadow-[0_4px_15px_rgba(98,88,255,0.2)] cursor-pointer"
                    >
                      Back to Dashboard
                    </button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            /* CONVERSATION INTERACTIVE CHAT */
            <>
              {/* Chat Feed */}
              <div className="flex-1 p-5 overflow-y-auto space-y-5">
                {chatHistory.map((msg) => {
                  const isUser = msg.sender === "user";
                  return (
                    <div
                      key={msg.id}
                      className={`flex ${isUser ? "justify-end" : "justify-start"}`}
                    >
                      <div className={`max-w-[85%] sm:max-w-[75%] rounded-2xl p-4.5 shadow-lg transition-all ${
                        isUser
                          ? "bg-[#6258ff] text-white rounded-br-none"
                          : "dark:bg-[#0d0e16] bg-white border dark:border-[#1b1c2b] border-slate-200 dark:text-slate-200 text-slate-800 rounded-bl-none"
                      }`}>
                        
                        {/* Text component */}
                        <div className="text-xs sm:text-[13px] leading-relaxed font-medium">
                          {msg.text && renderMath(msg.text)}
                        </div>

                        {/* Special interactive AI components from PDF */}
                        {msg.isExplanation && (
                          <div className="space-y-4 mt-2">
                            {/* Gap filled notification */}
                            <div className="bg-[#eab308]/5 border border-[#eab308]/15 rounded-xl p-3.5 mt-3">
                              <h4 className="text-[10px] font-bold text-[#eab308] uppercase tracking-wider mb-1">
                                {msg.gapTitle}
                              </h4>
                              <p className="text-xs dark:text-slate-400 text-slate-500 leading-relaxed font-semibold">
                                {msg.gapDescription}
                              </p>
                            </div>

                            {/* Solution steps */}
                            <div className="space-y-3">
                              <h4 className="text-[11px] font-bold dark:text-white text-slate-900 uppercase tracking-widest mt-4">Solution</h4>
                              {msg.steps?.map((step, idx) => (
                                <div key={idx} className="flex gap-3 items-start text-xs sm:text-[13px] dark:text-slate-300 text-slate-700 font-medium">
                                  <span className="h-5 w-5 rounded-md dark:bg-[#121126] bg-slate-100 border dark:border-[#3d3d91] border-indigo-200/35 text-[#8584ff] text-[10px] font-bold flex items-center justify-center flex-none">
                                    {idx + 1}
                                  </span>
                                  <div className="flex-1 leading-relaxed mt-0.5">{renderMath(step)}</div>
                                </div>
                              ))}
                            </div>

                            {/* Key Insight */}
                            {msg.keyInsight && (
                              <div className="bg-[#6258ff]/5 border border-[#6258ff]/15 rounded-xl p-3.5 mt-4">
                                <h4 className="text-[10px] font-bold text-[#8584ff] uppercase tracking-wider mb-1.5 flex items-center gap-1.5">
                                  <span>💡</span> KEY INSIGHT
                                </h4>
                                <p className="text-xs dark:text-slate-400 text-slate-500 leading-relaxed font-semibold">
                                  {msg.keyInsight}
                                </p>
                              </div>
                            )}

                            {/* Try next suggested prompts */}
                            {msg.tryNext && msg.tryNext.length > 0 && (
                              <div className="pt-3 border-t dark:border-[#1b1c2b] border-slate-200/65 mt-4">
                                <p className="text-[10px] font-bold dark:text-slate-500 text-slate-500 uppercase tracking-widest mb-2.5">Try next —</p>
                                <div className="space-y-2">
                                  {msg.tryNext.map((q, idx) => (
                                    <button
                                      key={idx}
                                      onClick={() => handleSuggestClick(q)}
                                      className="w-full text-left px-3.5 py-2.5 dark:bg-[#07080d] bg-white dark:hover:bg-[#111124] bg-slate-100 border dark:border-[#1b1c2b] border-slate-200 dark:hover:border-[#3d3d91] border-indigo-200/50 text-xs font-semibold text-[#8584ff] rounded-lg transition-colors cursor-pointer flex items-center justify-between"
                                    >
                                      <span>{q}</span>
                                      <svg aria-hidden="true" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5" className="h-3.5 w-3.5 opacity-60">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                                      </svg>
                                    </button>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
                {/* Thinking indicator — shown while the agent is processing */}
                {isBotThinking && (
                  <div className="flex justify-start">
                    <div className="dark:bg-[#0d0e16] bg-white border dark:border-[#1b1c2b] border-slate-200 rounded-2xl rounded-bl-none px-5 py-3.5 shadow-lg flex items-center gap-2">
                      <span className="h-1.5 w-1.5 rounded-full bg-[#6258ff] animate-bounce [animation-delay:0ms]" />
                      <span className="h-1.5 w-1.5 rounded-full bg-[#6258ff] animate-bounce [animation-delay:150ms]" />
                      <span className="h-1.5 w-1.5 rounded-full bg-[#6258ff] animate-bounce [animation-delay:300ms]" />
                    </div>
                  </div>
                )}
                <div ref={chatEndRef} />
              </div>

              {/* Chat Form Area */}
              <form onSubmit={handleSend} className="p-4 border-t dark:border-[#1b1c2b] border-slate-200 dark:bg-[#07080d] bg-white select-none flex-none">
                <div className="relative flex items-center">
                  <input
                    type="text"
                    value={inputVal}
                    onChange={(e) => setInputVal(e.target.value)}
                    placeholder={isBotThinking ? "Tutor is thinking…" : "Ask anything from Laws of Motion, Work Energy & Power..."}
                    disabled={isBotThinking}
                    className="w-full dark:bg-[#050609] bg-slate-50 border dark:border-[#1b1c2b] border-slate-200 focus:border-[#6258ff]/80 text-xs sm:text-sm dark:text-white text-slate-900 placeholder-slate-600 rounded-xl pl-4 pr-[85px] py-3.5 outline-none transition-colors disabled:opacity-50"
                  />
                  <div className="absolute right-3 flex items-center gap-1.5">
                    <button
                      type="submit"
                      disabled={!inputVal.trim() || isBotThinking}
                      className="bg-[#6258ff] hover:bg-[#5045ff] disabled:opacity-30 p-2 rounded-lg transition-colors cursor-pointer text-white flex items-center justify-center"
                    >
                      <svg aria-hidden="true" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5" className="h-4 w-4">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
                      </svg>
                    </button>
                  </div>
                </div>
                <div className="mt-2.5 flex items-center justify-between text-[9.5px] font-bold dark:text-slate-600 text-slate-400 tracking-wider uppercase">
                  <span>KaTeX supported — type $F = ma$ for inline math</span>
                  <span>Press enter to send</span>
                </div>
              </form>
            </>
          )}
        </div>

        {/* Right Side: Concept Panel */}
        <aside className="w-full lg:w-[280px] p-6 dark:bg-[#07080d] bg-white flex flex-col gap-6 overflow-y-auto select-none flex-none">
          {/* Section 1: Concepts in Response */}
          <div>
            <h3 className="text-[10px] font-bold dark:text-slate-500 text-slate-500 uppercase tracking-widest mb-3.5">
              Concepts in this response
            </h3>
            <div className="space-y-2.5">
              {sidebar.concepts.map((concept) => {
                const isWeak = concept.status === "WEAK";
                return (
                  <div
                    key={concept.name}
                    className={`p-3 border rounded-xl flex items-center justify-between transition-colors ${
                      isWeak
                        ? "border-red-950 bg-red-950/10 hover:bg-red-950/20"
                        : "dark:border-[#1b1c2b] border-slate-200 dark:bg-[#0d0e16] bg-white/60 dark:hover:bg-[#101021] bg-slate-100/30"
                    }`}
                  >
                    <div className="min-w-0">
                      <p className="text-xs font-semibold dark:text-slate-200 text-slate-800 truncate">{concept.name}</p>
                      <span className="text-[8.5px] font-semibold dark:text-slate-500 text-slate-500 uppercase mt-0.5 block">State</span>
                    </div>
                    <div className="text-right">
                      <span className={`text-[9px] font-bold uppercase tracking-wider px-2 py-0.5 rounded border ${
                        isWeak
                          ? "border-red-500/20 bg-red-500/5 text-red-400"
                          : "border-yellow-500/20 bg-yellow-500/5 text-yellow-400"
                      }`}>
                        {concept.status}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Section 2: Prerequisite Chain Flow chart */}
          <div>
            <h3 className="text-[10px] font-bold dark:text-slate-500 text-slate-500 uppercase tracking-widest mb-4">
              Prerequisite chain
            </h3>
            <div className="relative pl-4 border-l dark:border-[#1b1c2b] border-slate-200 ml-1.5 space-y-7">
              {sidebar.prerequisites.map((prereq, index) => {
                const isWeak = prereq.status === "WEAK";
                const isStrong = prereq.status === "STRONG";
                return (
                  <div key={prereq.name} className="relative">
                    {/* Circle Node */}
                    <span className={`absolute left-[-21px] top-1 h-3 w-3 rounded-full border-2 ${
                      isWeak
                        ? "dark:bg-[#05060c] bg-slate-50 border-red-500 shadow-[0_0_8px_rgba(239,68,68,0.4)]"
                        : isStrong
                        ? "dark:bg-[#05060c] bg-slate-50 border-emerald-500"
                        : "dark:bg-[#05060c] bg-slate-50 border-yellow-500"
                    }`} />
                    
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 justify-between">
                        <span className="text-xs font-bold dark:text-slate-300 text-slate-700 truncate">{prereq.name}</span>
                        <span className={`text-[10px] font-extrabold ${
                          isWeak ? "text-red-400" : isStrong ? "text-emerald-400" : "text-yellow-400"
                        }`}>
                          {prereq.score}%
                        </span>
                      </div>
                      <span className="text-[8px] font-bold dark:text-slate-600 text-slate-400 uppercase tracking-wide block mt-0.5">
                        {isWeak ? "Weak Area" : "Verified Prereq"}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </aside>

      </div>
    </div>
  );
}
