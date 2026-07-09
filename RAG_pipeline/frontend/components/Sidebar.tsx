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
  ScrollText,
  User,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuthContext } from "@/components/AuthProvider";
import { usePermissions } from "@/hooks/usePermissions";
import { useChatContext } from "@/contexts/ChatContext";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { UserGuideDialog } from "@/components/UserGuideDialog";
import { NotificationBell } from "@/components/notifications/NotificationBell";
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
  | "view_audit_logs"
  | "create_projects";

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
  { href: "/admin/project-assignments", label: "Projects", icon: FolderKanban, perm: "create_projects" },
  { href: "/admin/usage", label: "Usage", icon: BarChart3, perm: "view_audit_logs" },
  { href: "/admin/logs", label: "Logs", icon: ScrollText, perm: "view_audit_logs" },
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
    router.push(`/?c=${id}`);
  }

  function handleNewChat() {
    router.push("/?c=new");
  }

  if (!expanded) {
    return (
      <aside
        className="w-14 border-r bg-card flex flex-col h-full transition-all duration-200"
        data-tour="sidebar"
      >
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
              "flex items-center justify-center w-8 h-8 rounded-md transition-colors hover:bg-muted hover:text-foreground",
              isChatPage && !chat.activeConversationId
                ? "text-foreground"
                : "text-muted-foreground",
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
              aria-label="Admin"
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
            aria-label="User Guide"
            className="flex items-center justify-center w-8 h-8 rounded-md transition-colors text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <HelpCircle className="h-4 w-4" />
          </button>
        </nav>

        <div className="p-2 border-t flex flex-col items-center gap-1">
          {user && (
            <>
              <NotificationBell />
              <Link
                href="/settings/profile"
                className="p-1.5 text-muted-foreground hover:text-foreground"
                title="Profile"
                aria-label="Profile"
              >
                <User className="h-3.5 w-3.5" />
              </Link>
              <button
                onClick={logout}
                className="p-1.5 text-muted-foreground hover:text-foreground"
                title="Sign out"
                aria-label="Sign out"
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

      <nav className="px-2 py-1 space-y-0.5">
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

      <div className="p-2 border-t">
        {user && (
          <div className="flex items-center justify-between gap-2">
            <Link
              href="/settings/profile"
              className="text-xs text-muted-foreground truncate flex-1 hover:text-foreground transition-colors"
              title="View profile"
            >
              {user.username}
            </Link>
            <NotificationBell />
            <button
              onClick={logout}
              className="p-1 text-muted-foreground hover:text-foreground"
              title="Sign out"
              aria-label="Sign out"
            >
              <LogOut className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </div>

      <UserGuideDialog open={guideOpen} onClose={() => setGuideOpen(false)} />

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete Conversation"
        message={`Delete "${deleteTarget?.title || "New Chat"}"? All messages will be permanently removed.`}
        confirmLabel="Delete"
        onConfirm={() => {
          if (deleteTarget) {
            if (chat.activeConversationId === deleteTarget.id) {
              router.replace("/?c=new");
            }
            chat.deleteConversation(deleteTarget.id);
          }
          setDeleteTarget(null);
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </aside>
  );
}

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
        aria-label={`Delete conversation ${title}`}
      >
        <Trash2 className="h-3 w-3" />
      </button>
    </div>
  );
}
