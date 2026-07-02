import AuthGuard from "@/components/AuthGuard";
import StudySidebar from "@/components/StudySidebar";
import ProgressContent from "@/components/ProgressContent";

export default function ProgressPage() {
  return (
    <AuthGuard>
      <div className="flex min-h-screen dark:bg-[#05060c] bg-slate-50 dark:text-white text-slate-900">
        <StudySidebar />
        <ProgressContent />
      </div>
    </AuthGuard>
  );
}

