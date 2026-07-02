"use client";

import React from "react";
import { useApp } from "@/components/AppContext";
import LoginScreen from "@/components/LoginScreen";

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isLoggedIn } = useApp();

  if (!isLoggedIn) {
    return <LoginScreen />;
  }

  return <>{children}</>;
}
