import AuthGuard from "@/components/AuthGuard";
import StudySidebar from "@/components/StudySidebar";
import PracticeContent from "@/components/PracticeContent";

export default function PraticePage() {
  return (
    <AuthGuard>
      <div className="flex min-h-screen dark:bg-[#05060c] bg-slate-50 dark:text-white text-slate-900">
        <StudySidebar />
        <PracticeContent />
      </div>
    </AuthGuard>
  );
}

