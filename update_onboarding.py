import re

with open('/home/rohit/Downloads/KodeinKGP/aeropt/Aeropt-frontend/src/components/OnboardingFlow.tsx', 'r') as f:
    content = f.read()

# Replace StepDots
step_dots_old = """function StepDots({ step }: { step: number }) {
  return (
    <div className="flex items-center justify-center gap-1.5 mb-6">
      {Array.from({ length: TOTAL_STEPS }, (_, i) => (
        <span
          key={i}
          className={`h-1.5 rounded-full transition-all ${
            i + 1 === step ? "w-6 bg-[#6258ff]" : i + 1 < step ? "w-1.5 bg-[#6258ff]/50" : "w-1.5 bg-slate-600/40"
          }`}
        />
      ))}
    </div>
  );
}"""

step_dots_new = """function StepDots({ step }: { step: number }) {
  return (
    <div className="flex items-center gap-2 mb-8 justify-center">
      {Array.from({ length: TOTAL_STEPS }, (_, i) => (
        <span
          key={i}
          className={`h-2 rounded-full transition-all duration-300 ${
            i + 1 === step ? "w-8 bg-[#006bff]" : i + 1 < step ? "w-2 bg-[#006bff]/50" : "w-2 bg-[#d4e0ed]"
          }`}
        />
      ))}
    </div>
  );
}"""
content = content.replace(step_dots_old, step_dots_new)

# Replace SlideCard
slide_card_old = """function SlideCard({
  title,
  subtitle,
  children,
  onBack,
  onNext,
  nextLabel = "Continue",
  nextDisabled = false,
  loading = false,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  onBack?: () => void;
  onNext: () => void;
  nextLabel?: string;
  nextDisabled?: boolean;
  loading?: boolean;
}) {
  return (
    <div className="w-full max-w-lg mx-auto">
      <div className="dark:bg-[#0d0e16] bg-white/90 border dark:border-[#1b1c2b] border-slate-200 rounded-2xl p-6 shadow-[0_20px_50px_rgba(0,0,0,0.35)]">
        <h2 className="text-xl font-bold dark:text-white text-slate-900 mb-1">{title}</h2>
        {subtitle && (
          <p className="text-sm dark:text-slate-400 text-slate-500 mb-5">{subtitle}</p>
        )}
        <div className="mb-6">{children}</div>
        <div className="flex gap-3">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              disabled={loading}
              className="flex-1 py-2.5 rounded-lg border dark:border-[#1b1c2b] border-slate-200 text-sm font-medium dark:text-slate-300 text-slate-600 disabled:opacity-50"
            >
              Back
            </button>
          )}
          <button
            type="button"
            disabled={nextDisabled || loading}
            onClick={onNext}
            className="flex-1 py-2.5 rounded-lg bg-[#6258ff] hover:bg-[#5045ff] disabled:opacity-50 text-white text-sm font-semibold"
          >
            {loading ? "Setting up..." : nextLabel}
          </button>
        </div>
      </div>
    </div>
  );
}"""

slide_card_new = """function SlideCard({
  title,
  subtitle,
  children,
  onBack,
  onNext,
  nextLabel = "Continue",
  nextDisabled = false,
  loading = false,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  onBack?: () => void;
  onNext: () => void;
  nextLabel?: string;
  nextDisabled?: boolean;
  loading?: boolean;
}) {
  return (
    <div className="w-full max-w-[640px] mx-auto animate-in fade-in slide-in-from-bottom-4 duration-300">
      <div className="bg-white border border-[#d4e0ed] rounded-[24px] p-8 shadow-[0_8px_30px_rgb(0,107,255,0.05)]">
        <h2 className="text-[38px] md:text-[42px] leading-tight font-bold text-[#0b3558] mb-3">{title}</h2>
        {subtitle && (
          <p className="text-base text-[#476788] mb-8">{subtitle}</p>
        )}
        <div className="mb-10 space-y-4">{children}</div>
        <div className="flex gap-4">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              disabled={loading}
              className="flex-1 py-3.5 rounded-lg border border-[#d4e0ed] bg-white text-base font-medium text-[#0b3558] disabled:opacity-50 hover:bg-[#f8f9fb] transition-colors"
            >
              Back
            </button>
          )}
          <button
            type="button"
            disabled={nextDisabled || loading}
            onClick={onNext}
            className="flex-1 py-3.5 rounded-lg bg-[#006bff] hover:bg-[#0058d1] transition-colors disabled:opacity-50 text-white text-base font-semibold shadow-sm"
          >
            {loading ? "Setting up..." : nextLabel}
          </button>
        </div>
      </div>
    </div>
  );
}"""
content = content.replace(slide_card_old, slide_card_new)

# Replace Return statement header
ret_old = """  return (
    <div className="min-h-screen dark:bg-[#05060c] bg-slate-50 px-4 py-8 font-sans dark:text-slate-100 text-slate-900">
      <div className="absolute top-[-10%] left-[-10%] w-[50%] h-[50%] rounded-full bg-[#6258ff]/10 blur-[120px] pointer-events-none" />
      <div className="max-w-lg mx-auto relative">
        <div className="flex items-center justify-between mb-6 min-h-[32px]">
          {canSkipStep(step) ? (
            <button
              type="button"
              onClick={skipStep}
              disabled={saving}
              className="text-sm font-medium dark:text-slate-400 text-slate-500 hover:dark:text-slate-200 hover:text-slate-800 disabled:opacity-50"
            >
              Skip
            </button>
          ) : (
            <span className="w-10" />
          )}
          <div className="text-center">
            <p className="text-[11px] uppercase tracking-widest dark:text-slate-500 text-slate-400 font-semibold">
              Enter the Sandbox
            </p>
            <p className="text-xs dark:text-slate-500 text-slate-400 mt-0.5">
              Step {step} of {TOTAL_STEPS}
            </p>
          </div>
          <span className="w-10" />
        </div>"""

ret_new = """  return (
    <div className="min-h-screen bg-[#f8f9fb] px-4 py-12 md:py-16 font-sans text-[#0b3558]">
      <div className="max-w-3xl mx-auto relative">
        <div className="flex items-center justify-between mb-10 min-h-[32px]">
          {canSkipStep(step) ? (
            <button
              type="button"
              onClick={skipStep}
              disabled={saving}
              className="text-sm font-medium text-[#476788] hover:text-[#0b3558] disabled:opacity-50 transition-colors"
            >
              Skip
            </button>
          ) : (
            <span className="w-10" />
          )}
          <div className="text-center">
            <p className="text-[14px] uppercase tracking-widest text-[#006bff] font-semibold">
              Enter the Sandbox
            </p>
            <p className="text-sm text-[#476788] mt-1">
              Step {step} of {TOTAL_STEPS}
            </p>
          </div>
          <span className="w-10" />
        </div>"""
content = content.replace(ret_old, ret_new)

# General Regex Replacements for colors and classes
content = re.sub(r'dark:[a-zA-Z0-9_\[\]#/-]+', '', content) # Remove dark mode
content = content.replace('bg-slate-50', 'bg-[#f8f9fb]')
content = content.replace('bg-[#6258ff]/10', 'bg-[#006bff]/5')
content = content.replace('bg-[#6258ff]/15', 'bg-[#006bff]/5')
content = content.replace('bg-[#6258ff]/8', 'bg-[#006bff]/5')
content = content.replace('bg-[#6258ff]/20', 'bg-[#006bff]/10')
content = content.replace('border-[#6258ff]', 'border-[#006bff]')
content = content.replace('border-[#6258ff]/40', 'border-[#006bff]/40')
content = content.replace('border-[#6258ff]/60', 'border-[#006bff]/60')
content = content.replace('text-[#6258ff]', 'text-[#006bff]')
content = content.replace('accent-[#6258ff]', 'accent-[#006bff]')
content = content.replace('#6258ff', '#006bff')

content = content.replace('text-slate-900', 'text-[#0b3558]')
content = content.replace('text-slate-800', 'text-[#0b3558]')
content = content.replace('text-slate-600', 'text-[#476788]')
content = content.replace('text-slate-500', 'text-[#476788]')
content = content.replace('text-slate-400', 'text-[#476788]')

content = content.replace('border-slate-200', 'border-[#d4e0ed]')
content = content.replace('border-slate-300', 'border-[#d4e0ed]')

# Fix up multiple spaces introduced by dark mode removal
content = re.sub(r'\s{2,}', ' ', content)
# Fix up jsx class name spacing issues `className=" ..."`
content = re.sub(r'className="\s+', 'className="', content)
content = content.replace('  ', ' ')

# Loading state
load_old = """if (loading) {
  return (
   <div className="flex min-h-screen items-center justify-center bg-[#f8f9fb]">
    <p className="text-sm text-[#476788]">Setting up your classroom...</p>
   </div>
  );
 }"""
load_new = """if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#f8f9fb]">
        <p className="text-base text-[#476788]">Setting up your classroom...</p>
      </div>
    );
  }"""
content = content.replace(load_old, load_new) # It might not match exactly due to space replacement, let's just do it directly.

# Fix specific inputs
content = content.replace('rounded-lg px-3 py-2 text-sm', 'rounded-lg px-4 py-3 text-base focus:border-[#006bff] focus:ring-1 focus:ring-[#006bff] outline-none')

with open('/home/rohit/Downloads/KodeinKGP/aeropt/Aeropt-frontend/src/components/OnboardingFlow.tsx', 'w') as f:
    f.write(content)
