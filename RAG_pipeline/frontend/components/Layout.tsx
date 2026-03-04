"use client";

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "./Sidebar";

interface LayoutProps {
  children: ReactNode;
}

const NO_LAYOUT_PATHS = ["/login"];

export function Layout({ children }: LayoutProps) {
  const pathname = usePathname();

  if (NO_LAYOUT_PATHS.some((p) => pathname.startsWith(p))) {
    return <>{children}</>;
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <main className="flex-1 overflow-y-auto p-6">{children}</main>
    </div>
  );
}
