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
import { LicenceBanner } from "./LicenceBanner";
import { LicenceProvider } from "@/hooks/useLicence";

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

  // One licence status for the whole shell: the banner and every gated control
  // (chat composer, scan/generate) read it, so they cannot disagree. Only read
  // once a user is known; the status route requires a session.
  const licenceEnabled = Boolean(user);

  if (isMobile) {
    return (
      <LicenceProvider enabled={licenceEnabled}>
      <ChatProvider>
        <OnboardingGuard>
          <div className="flex flex-col h-screen">
            <MobileTopBar
              showMenuButton
              onMenuClick={() => setDrawerOpen(true)}
            />
            <LicenceBanner />
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
      </LicenceProvider>
    );
  }

  return (
    <LicenceProvider enabled={licenceEnabled}>
    <ChatProvider>
      <OnboardingGuard>
        <div className="flex h-screen">
          <Sidebar />
          {/* The banner heads the content column rather than the viewport, so it
              never covers the sidebar and scrolls with nothing. */}
          <div className="flex min-w-0 flex-1 flex-col">
            <LicenceBanner />
            <main className={cn("flex-1 overflow-y-auto", !isChatPage && "p-6")}>
              {children}
            </main>
          </div>
        </div>
        <ProductTour />
      </OnboardingGuard>
    </ChatProvider>
    </LicenceProvider>
  );
}
