"use client";

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Sidebar } from "./Sidebar";
import { ChatProvider } from "@/contexts/ChatContext";

interface LayoutProps {
  children: ReactNode;
}

const NO_LAYOUT_PATHS = ["/login"];

export function Layout({ children }: LayoutProps) {
  const pathname = usePathname();

  if (NO_LAYOUT_PATHS.some((p) => pathname.startsWith(p))) {
    return <>{children}</>;
  }

  const isChatPage = pathname === "/";

  return (
    <ChatProvider>
      <div className="flex h-screen">
        <Sidebar />
        <main className={cn("flex-1 overflow-y-auto", !isChatPage && "p-6")}>
          {children}
        </main>
      </div>
    </ChatProvider>
  );
}
