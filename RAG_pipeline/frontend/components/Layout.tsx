"use client";

import { useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useAuthContext } from "@/components/AuthProvider";
import { cn } from "@/lib/utils";
import { Sidebar } from "./Sidebar";
import { ChatProvider } from "@/contexts/ChatContext";
import { OnboardingGuard } from "./onboarding/OnboardingGuard";
import { ProductTour } from "./tour/ProductTour";
import { useViewport } from "@/hooks/useViewport";
import { MobileTopBar } from "./mobile/MobileTopBar";
import { MobileDrawer } from "./mobile/MobileDrawer";
import { SidebarContent } from "./mobile/SidebarContent";

interface LayoutProps {
  children: ReactNode;
}

const NO_LAYOUT_PATHS = ["/login", "/onboarding", "/guide"];

export function Layout({ children }: LayoutProps) {
  const pathname = usePathname();
  const { user } = useAuthContext();
  const { isMobile } = useViewport();
  const [drawerOpen, setDrawerOpen] = useState(false);

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

  if (
    pathname === "/settings/profile" &&
    user &&
    !user.has_password &&
    !user.sso_provider
  ) {
    return (
      <main className="min-h-screen overflow-y-auto bg-slate-50">
        {children}
      </main>
    );
  }

  const isChatPage = pathname === "/";

  if (isMobile) {
    return (
      <ChatProvider>
        <OnboardingGuard>
          <div className="flex flex-col h-screen">
            <MobileTopBar
              showMenuButton
              onMenuClick={() => setDrawerOpen(true)}
            />
            <main className="flex-1 overflow-y-auto pb-safe">
              {children}
            </main>
          </div>
          <MobileDrawer
            open={drawerOpen}
            onClose={() => setDrawerOpen(false)}
            ariaLabel="Main navigation"
          >
            <div className="pt-safe h-full">
              <SidebarContent onNavigate={() => setDrawerOpen(false)} />
            </div>
          </MobileDrawer>
          <ProductTour />
        </OnboardingGuard>
      </ChatProvider>
    );
  }

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
