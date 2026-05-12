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
  Loader2,
  Trash2,
  Users,
  UserPlus,
  KeyRound,
  FolderKanban,
  BarChart3,
  FlaskConical,
  Brain,
  HelpCircle,
  Database,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuthContext } from "@/components/AuthProvider";
import { usePermissions } from "@/hooks/usePermissions";
import { useChatContext } from "@/contexts/ChatContext";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { UserGuideDialog } from "@/components/UserGuideDialog";
import { NotificationBell } from "@/components/notifications/NotificationBell";
import { TenantMark } from "@/components/TenantMark";
import type { ConversationResponse } from "@/types";

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

type PermissionCodename =
  | "manage_users"
  | "manage_roles"
  | "assign_projects"
  | "view_all_projects"
  | "manage_projects"
  | "view_assigned_projects"
  | "query_rag"
  | "ingest_documents"
  | "view_audit_logs";

interface AdminSubLink {
  href: string;
  label: string;
  icon: typeof Users;
  adminOnly?: boolean;
  superadminOnly?: boolean;
  perm?: PermissionCodename;
}

const adminSubLinks: AdminSubLink[] = [
  { href: "/settings/sources", label: "Sources", icon: Database, adminOnly: true },
  { href: "/admin/users", label: "Users", icon: Users, superadminOnly: true },
  { href: "/admin/access-requests", label: "Access", icon: UserPlus, superadminOnly: true },
  { href: "/admin/roles", label: "Roles", icon: KeyRound, superadminOnly: true },
  { href: "/admin/project-assignments", label: "Projects", icon: FolderKanban, superadminOnly: true },
  { href: "/admin/usage", label: "Usage", icon: BarChart3, perm: "view_audit_logs" },
  { href: "/admin/benchmark", label: "Benchmark", icon: FlaskConical, perm: "view_audit_logs" },
  { href: "/admin/hindsight", label: "Memory", icon: Brain, perm: "view_audit_logs" },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuthContext();
  const { hasAnyAdminPermission, hasPermission, isSuperadmin } =
    usePermissions();
  const chat = useChatContext();

  const [expanded, setExpanded] = useLocalExpand();
  const [deleteTarget, setDeleteTarget] = useState<ConversationResponse | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);

  const isChatPage = pathname === "/";
  const isAdminSection = pathname.startsWith("/admin") || pathname === "/settings/sources";

  const visibleAdminLinks = adminSubLinks.filter((link) => {
    if (link.adminOnly) return hasAnyAdminPermission;
    if (link.superadminOnly) return isSuperadmin;
    if (link.perm) return hasPermission(link.perm);
    return false;
  });

  const conversations = chat.conversations;

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
      <aside className="w-14 border-r bg-card flex flex-col h-full transition-all duration-200">
        {/* Tenant mark */}
        <div className="px-2 py-3 flex items-center justify-center border-b border-border/60">
          <TenantMark iconOnly size="md" />
        </div>
        {/* Toggle expand */}
        <div className="p-2 flex items-center justify-center">
          <button
            onClick={() => setExpanded(true)}
            className="p-1.5 rounded-md hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            title="Expand sidebar"
          >
            <Menu className="h-4 w-4" />
          </button>
        </div>

        {/* Nav icons */}
        <nav className="flex-1 py-1 flex flex-col items-center gap-1">
          <button
            onClick={handleNewChat}
            title="New chat"
            className={cn(
              "flex items-center justify-center w-8 h-8 rounded-md transition-colors",
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
              className={cn(
                "flex items-center justify-center w-8 h-8 rounded-md transition-colors",
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
              href={visibleAdminLinks[0]?.href ?? "/admin/usage"}
              title="Admin"
              className={cn(
                "flex items-center justify-center w-8 h-8 rounded-md transition-colors",
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
            className="flex items-center justify-center w-8 h-8 rounded-md transition-colors text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <HelpCircle className="h-4 w-4" />
          </button>
        </nav>

        {/* Bottom: bell + logout */}
        <div className="p-2 border-t flex flex-col items-center gap-1">
          {user && (
            <>
              <NotificationBell />
              <button
                onClick={logout}
                className="p-1.5 text-muted-foreground hover:text-foreground"
                title="Sign out"
              >
                <LogOut className="h-3.5 w-3.5" />
              </button>
            </>
          )}
        </div>

        <UserGuideDialog open={guideOpen} onClose={() => setGuideOpen(false)} />
      </aside>
    );
  }

  // ─── Expanded sidebar ────────────────────────────────────────────
  return (
    <aside className="w-56 border-r bg-card flex flex-col h-full transition-all duration-200">
      {/* Tenant mark + collapse */}
      <div className="px-3 py-3 border-b border-border/60 flex items-center gap-2">
        <TenantMark size="md" className="flex-1 min-w-0" />
        <button
          onClick={() => setExpanded(false)}
          className="shrink-0 p-1.5 rounded-md hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
          title="Collapse sidebar"
        >
          <PanelLeftClose className="h-4 w-4" />
        </button>
      </div>

      {/* Nav items */}
      <nav className="px-2 py-1 space-y-0.5" data-tour="sidebar-nav">
        <button
          onClick={handleNewChat}
          className={cn(
            "flex items-center gap-2 w-full px-2.5 py-1.5 rounded-md text-sm transition-colors",
            isChatPage && !chat.activeConversationId
              ? "bg-secondary text-secondary-foreground font-medium"
              : "text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          <Plus className="h-4 w-4" />
          New Chat
        </button>
        {navItems.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-2 px-2.5 py-1.5 rounded-md text-sm transition-colors",
              pathname === href
                ? "bg-secondary text-secondary-foreground font-medium"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
          </Link>
        ))}
        {hasAnyAdminPermission && (
          <>
            <Link
              href={visibleAdminLinks[0]?.href ?? "/admin/usage"}
              className={cn(
                "flex items-center gap-2 px-2.5 py-1.5 rounded-md text-sm transition-colors",
                isAdminSection
                  ? "bg-secondary text-secondary-foreground font-medium"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Shield className="h-4 w-4" />
              Admin
            </Link>
            {isAdminSection &&
              visibleAdminLinks.length > 0 && (
                <div className="ml-4 space-y-0.5">
                  {visibleAdminLinks.map(({ href, label, icon: Icon }) => (
                    <Link
                      key={href}
                      href={href}
                      className={cn(
                        "flex items-center gap-2 px-2.5 py-1 rounded-md text-xs transition-colors",
                        pathname === href
                          ? "bg-secondary/70 text-secondary-foreground font-medium"
                          : "text-muted-foreground hover:bg-muted hover:text-foreground",
                      )}
                    >
                      <Icon className="h-3.5 w-3.5" />
                      {label}
                    </Link>
                  ))}
                </div>
              )}
          </>
        )}
        <button
          onClick={() => setGuideOpen(true)}
          className="flex items-center gap-2 px-2.5 py-1.5 rounded-md text-sm transition-colors text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <HelpCircle className="h-4 w-4" />
          User Guide
        </button>
      </nav>

      <div className="mx-3 my-2 border-t" />

      {/* Conversations */}
      <div className="flex-1 overflow-y-auto px-2">
        {chat.isLoading ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          </div>
        ) : conversations.length === 0 ? (
          <div className="text-center py-6 text-xs text-muted-foreground">
            No conversations yet.
          </div>
        ) : (
          conversations.map((conv) => (
            <ConversationRow
              key={conv.id}
              conversation={conv}
              isActive={conv.id === chat.activeConversationId}
              onSelect={() => handleSelectConversation(conv.id)}
              onDelete={() => setDeleteTarget(conv)}
            />
          ))
        )}
      </div>

      {/* Footer: user info + logout */}
      <div className="p-2 border-t">
        {user && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-muted-foreground truncate flex-1">
              {user.username}
            </span>
            <NotificationBell />
            <button
              onClick={logout}
              className="p-1 text-muted-foreground hover:text-foreground"
              title="Sign out"
            >
              <LogOut className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </div>

      <UserGuideDialog open={guideOpen} onClose={() => setGuideOpen(false)} />

      {/* Delete confirmation dialog */}
      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete Conversation"
        message={`Delete "${deleteTarget?.title || "New Chat"}"? All messages will be permanently removed.`}
        confirmLabel="Delete"
        onConfirm={() => {
          if (deleteTarget) {
            chat.deleteConversation(deleteTarget.id);
          }
          setDeleteTarget(null);
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </aside>
  );
}

// ─── Inline conversation row ───────────────────────────────────────
function ConversationRow({
  conversation,
  isActive,
  onSelect,
  onDelete,
}: {
  conversation: ConversationResponse;
  isActive: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  const title = conversation.title || "New Chat";

  return (
    <div
      className={cn(
        "group flex items-center gap-1 px-2 py-2 cursor-pointer transition-colors text-[13px] leading-tight rounded-lg",
        isActive
          ? "bg-muted text-foreground"
          : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
      )}
      onClick={onSelect}
    >
      <span className="flex-1 truncate">{title}</span>
      <button
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        className="shrink-0 p-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
        title="Delete"
      >
        <Trash2 className="h-3 w-3" />
      </button>
    </div>
  );
}
