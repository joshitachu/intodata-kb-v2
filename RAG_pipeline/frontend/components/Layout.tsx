"use client";

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useAuthContext } from "@/components/AuthProvider";
import { cn } from "@/lib/utils";
import { Sidebar } from "./Sidebar";
import { ChatProvider } from "@/contexts/ChatContext";
import { OnboardingGuard } from "./onboarding/OnboardingGuard";
import { ProductTour } from "./tour/ProductTour";

interface LayoutProps {
  children: ReactNode;
}

const NO_LAYOUT_PATHS = ["/login", "/onboarding"];

export function Layout({ children }: LayoutProps) {
  const pathname = usePathname();
  const { user } = useAuthContext();

  if (NO_LAYOUT_PATHS.some((p) => pathname.startsWith(p))) {
    return <>{children}</>;
  }

  if (pathname === "/settings/security" && user && !user.mfa_enabled) {
    return (
      <main className="min-h-screen overflow-y-auto bg-slate-50">
        {children}
      </main>
    );
  }

  const isChatPage = pathname === "/";

  return (
    <ChatProvider>
      <OnboardingGuard>
        <div className="flex h-screen">
          <Sidebar />
          <main className={cn("flex-1 overflow-y-auto", !isChatPage && "p-6")}>
            {children}
          </main>
        </div>
        <ProductTour />
      </OnboardingGuard>
    </ChatProvider>
  );
}
