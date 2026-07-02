import AuthGuard from "@/components/AuthGuard";
import StudySidebar from "@/components/StudySidebar";
import SettingsContent from "@/components/SettingsContent";

export default function SettingsPage() {
  return (
    <AuthGuard>
      <div className="flex min-h-screen dark:bg-[#05060c] bg-slate-50 dark:text-white text-slate-900">
        <StudySidebar />
        <SettingsContent />
      </div>
    </AuthGuard>
  );
}

