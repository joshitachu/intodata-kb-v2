"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Menu,
  PanelLeftClose,
  Plus,
  FileText,
  BookOpen,
  Network,
  Shield,
  LogOut,
  User,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuthContext } from "@/components/AuthProvider";
import { usePermissions } from "@/hooks/usePermissions";
import { useChatContext } from "@/contexts/ChatContext";
import { UserGuideDialog } from "@/components/UserGuideDialog";
import { SidebarContent } from "./mobile/SidebarContent";

const SIDEBAR_KEY = "sidebar-expanded";

function useLocalExpand(): [boolean, (v: boolean) => void] {
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem(SIDEBAR_KEY);
    if (stored === "true") setExpanded(true);
  }, []);

  function set(v: boolean) {
    setExpanded(v);
    localStorage.setItem(SIDEBAR_KEY, String(v));
  }

  return [expanded, set];
}

const navItems = [
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/docs", label: "Project Docs", icon: BookOpen },
  { href: "/topology", label: "Topology", icon: Network },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuthContext();
  const { hasAnyAdminPermission } = usePermissions();
  const chat = useChatContext();

  const [expanded, setExpanded] = useLocalExpand();
  const [guideOpen, setGuideOpen] = useState(false);

  const isChatPage = pathname === "/";
  const isAdminSection = pathname.startsWith("/admin") || pathname === "/settings/sources";

  function handleSelectConversation(id: number) {
    chat.setActiveConversationId(id);
    if (!isChatPage) {
      router.push("/");
    }
  }

  function handleNewChat() {
    chat.setActiveConversationId(null);
    if (!isChatPage) {
      router.push("/");
    }
  }

  // ─── Collapsed sidebar ───────────────────────────────────────────
  if (!expanded) {
    return (
      <aside
        className="w-14 border-r bg-card flex flex-col h-full transition-all duration-200"
        data-tour="sidebar"
      >
        <div className="px-2 py-3 flex items-center justify-center border-b border-border/60">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/favicon.svg"
            alt="Intodata"
            className="h-7 w-7 rounded-md shadow-elevation-xs"
          />
        </div>
        <div className="p-2 flex items-center justify-center">
          <button
            onClick={() => setExpanded(true)}
            className="p-1.5 rounded-md hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            title="Expand sidebar"
            aria-label="Expand sidebar"
          >
            <Menu className="h-4 w-4" />
          </button>
        </div>

        <nav className="flex-1 py-1 flex flex-col items-center gap-1">
          <button
            onClick={handleNewChat}
            title="New chat"
            aria-label="New chat"
            className={cn(
              "flex items-center justify-center w-10 h-10 rounded-md transition-colors",
              isChatPage && !chat.activeConversationId
                ? "bg-secondary text-secondary-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <Plus className="h-4 w-4" />
          </button>
          {navItems.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              title={label}
              aria-label={label}
              className={cn(
                "flex items-center justify-center w-10 h-10 rounded-md transition-colors",
                pathname === href
                  ? "bg-secondary text-secondary-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Icon className="h-4 w-4" />
            </Link>
          ))}
          {hasAnyAdminPermission && (
            <Link
              href="/admin/usage"
              title="Admin"
              aria-label="Admin"
              className={cn(
                "flex items-center justify-center w-10 h-10 rounded-md transition-colors",
                isAdminSection
                  ? "bg-secondary text-secondary-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Shield className="h-4 w-4" />
            </Link>
          )}
          <button
            onClick={() => setGuideOpen(true)}
            title="User Guide"
            aria-label="User Guide"
            className="flex items-center justify-center w-10 h-10 rounded-md transition-colors text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <User className="h-4 w-4" />
          </button>
        </nav>

        <div className="p-2 border-t flex flex-col items-center gap-1">
          {user && (
            <button
              onClick={logout}
              className="p-2 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors min-h-[40px] min-w-[40px] flex items-center justify-center"
              title="Sign out"
              aria-label="Sign out"
            >
              <LogOut className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        <UserGuideDialog open={guideOpen} onClose={() => setGuideOpen(false)} />
      </aside>
    );
  }

  // ─── Expanded sidebar ────────────────────────────────────────────
  return (
    <aside
      className="w-56 border-r bg-card flex flex-col h-full transition-all duration-200"
      data-tour="sidebar"
    >
      <div className="px-3 py-3 border-b border-border/60 flex items-center gap-2">
        <div className="flex-1 min-w-0">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/intodata-logo-hero.png"
            alt="Intodata"
            className="h-7 w-auto max-w-full object-contain object-left dark:brightness-0 dark:invert"
          />
        </div>
        <button
          onClick={() => setExpanded(false)}
          className="shrink-0 p-1.5 rounded-md hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
          title="Collapse sidebar"
          aria-label="Collapse sidebar"
        >
          <PanelLeftClose className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <SidebarContent
          onNavigate={() => {
            // Desktop sidebar: the shared content already sets the active
            // conversation. If we're not on the chat page, route there so
            // the new selection is actually visible. On / we can stay.
            if (!isChatPage) {
              router.push("/");
            }
          }}
        />
      </div>

      <UserGuideDialog open={guideOpen} onClose={() => setGuideOpen(false)} />
    </aside>
  );
}
