import AuthGuard from "@/components/AuthGuard";
import StudySidebar from "@/components/StudySidebar";
import DashboardContent from "@/components/DashboardContent";

export default function DashboardPage() {
  return (
    <AuthGuard>
      <div className="flex min-h-screen dark:bg-[#05060c] bg-slate-50 dark:text-white text-slate-900">
        <StudySidebar />
        <DashboardContent />
      </div>
    </AuthGuard>
  );
}

