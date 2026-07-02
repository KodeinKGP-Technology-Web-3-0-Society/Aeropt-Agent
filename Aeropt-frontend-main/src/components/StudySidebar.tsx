"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ComponentType, SVGProps } from "react";
import { useApp } from "@/components/AppContext";

type SidebarUser = {
  name: string;
  badge: string;
};

type NavItem = {
  label: string;
  href: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
};

const navItems: NavItem[] = [
  { label: "Dashboard", href: "/dashboard", icon: HomeIcon },
  { label: "Study Session", href: "/chat", icon: ChatIcon },
  { label: "Practice", href: "/pratice", icon: LayersIcon },
  { label: "Progress", href: "/progress", icon: ChartIcon },
  { label: "Settings", href: "/settings", icon: SettingsIcon },
];

function BrandIcon() {
  return (
    <span className="flex h-6 w-6 items-center justify-center rounded-md bg-[#6258ff] text-white shadow-[0_0_18px_rgba(98,88,255,0.35)]">
      <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4">
        <path
          d="M7 13.8a2.8 2.8 0 1 0 2.65 1.9h4.7A2.8 2.8 0 1 0 17 12.1V9.65A2.8 2.8 0 1 0 14.35 6H9.65A2.8 2.8 0 1 0 7 9.65v4.15Zm3-6.3h4m1.5 1.55v3.9m-6.2 3.25h5.4M7 9.65v4.15"
          fill="none"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.8"
        />
      </svg>
    </span>
  );
}

function HomeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" {...props}>
      <path d="m4 11 8-7 8 7v8.5a.5.5 0 0 1-.5.5H15v-5H9v5H4.5a.5.5 0 0 1-.5-.5V11Z" />
    </svg>
  );
}

function ChatIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" {...props}>
      <path d="M5 6.5h14v10H8.7L5 19.5v-13Z" />
    </svg>
  );
}

function LayersIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" {...props}>
      <path d="m12 4 8 4-8 4-8-4 8-4Zm-6 8 6 3 6-3m-12 4 6 3 6-3" />
    </svg>
  );
}

function ChartIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" {...props}>
      <path d="M4 19h16M7 16V9m5 7V5m5 11v-4" />
    </svg>
  );
}

function SettingsIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" {...props}>
      <path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Zm0-4v2m0 11v2m7.5-7.5h-2m-11 0h-2m12.8-5.3-1.4 1.4m-7.8 7.8-1.4 1.4m0-10.6 1.4 1.4m7.8 7.8 1.4 1.4" />
    </svg>
  );
}

function SidebarUserCard({ user, onLogout }: { user: SidebarUser; onLogout: () => void }) {
  return (
    <div className="group flex items-center justify-between gap-2 px-1 pb-4 border-t dark:border-[#1b1c2b] border-slate-200/50 pt-4 mt-2">
      <div className="flex items-center gap-2.5 min-w-0">
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-[#58d36f] to-[#1c6f32] relative shadow-[0_0_12px_rgba(88,211,111,0.2)]">
          <span className="h-1.5 w-1.5 rounded-full bg-[#0b3317]" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-xs font-semibold dark:text-white text-slate-900 tracking-wide">{user.name}</p>
          <p className="mt-0.5 inline-flex rounded border dark:border-[#3d3d91] border-indigo-200/50 dark:bg-[#11113a]/80 bg-indigo-50 px-1.5 py-0.5 text-[8.5px] font-semibold text-[#8584ff] uppercase tracking-wider">
            {user.badge}
          </p>
        </div>
      </div>
      <button
        onClick={onLogout}
        title="Sign Out"
        className="opacity-0 group-hover:opacity-100 p-1.5 rounded-md hover:bg-red-950/20 hover:text-red-400 dark:text-slate-500 text-slate-500 transition-all cursor-pointer"
      >
        <svg aria-hidden="true" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.2" className="h-3.5 w-3.5">
          <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15M12 9l-3 3m0 0l3 3m-3-3h12.75" />
        </svg>
      </button>
    </div>
  );
}

export default function StudySidebar() {
  const pathname = usePathname();
  const { data, logout } = useApp();
  const user = data.user;

  return (
    <aside className="study-sidebar flex min-h-screen w-[204px] flex-col px-3 py-5 dark:text-slate-100 text-slate-900 border-r dark:border-[#15162a] border-slate-200/80 select-none flex-none">
      <div className="mb-8 flex items-center gap-2.5 px-1.5">
        <BrandIcon />
        <span className="text-base font-bold tracking-tight dark:text-white text-slate-900">Prepwise</span>
      </div>

      <nav className="space-y-1">
        {navItems.map((item) => (
          <Link
            key={item.label}
            href={item.href}
            className={`sidebar-item flex items-center gap-2.5 h-[34px] px-2.5 text-xs font-semibold rounded-lg border-l-2 transition-all cursor-pointer ${
              pathname === item.href
                ? "border-[#6258ff] dark:bg-[#121126] bg-slate-100/60 text-[#8584ff] shadow-[inset_0_1px_1px_rgba(98,88,255,0.08)]"
                : "border-transparent dark:text-slate-400 text-slate-500 dark:hover:bg-[#101021] hover:bg-slate-100 dark:hover:text-slate-200 hover:text-slate-700"
            }`}
          >
            <item.icon className="h-[14px] w-[14px]" />
            <span>{item.label}</span>
          </Link>
        ))}
      </nav>

      <div className="mt-auto">
        <SidebarUserCard user={user} onLogout={logout} />
      </div>
    </aside>
  );
}

