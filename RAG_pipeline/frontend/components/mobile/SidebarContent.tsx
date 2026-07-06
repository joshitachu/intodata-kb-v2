"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
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

export interface SidebarContentProps {
  onNavigate?: () => void;
}

export function SidebarContent({ onNavigate }: SidebarContentProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuthContext();
  const { hasAnyAdminPermission, hasPermission, isSuperadmin } = usePermissions();
  const chat = useChatContext();

  const [deleteTarget, setDeleteTarget] = useState<ConversationResponse | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);

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
    onNavigate?.();
  }

  function handleNewChat() {
    chat.setActiveConversationId(null);
    onNavigate?.();
  }

  function handleNav() {
    onNavigate?.();
  }

  return (
    <div className="flex flex-col h-full">
      {/* User header */}
      {user && (
        <div className="px-3 py-3 border-b border-border/60 flex items-center gap-2">
          <Link
            href="/settings/profile"
            onClick={handleNav}
            className="flex items-center gap-2 flex-1 min-w-0 hover:text-foreground transition-colors"
          >
            <div className="h-8 w-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
              <User className="h-4 w-4 text-primary" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium truncate">{user.username}</div>
              <div className="text-xs text-muted-foreground truncate">
                {user.is_superadmin ? "Superadmin" : user.roles?.[0] ?? "Member"}
              </div>
            </div>
          </Link>
          <button
            onClick={logout}
            className="shrink-0 p-2 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors min-h-[44px] min-w-[44px] flex items-center justify-center"
            title="Sign out"
            aria-label="Sign out"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* New chat */}
      <div className="px-2 py-2">
        <button
          onClick={handleNewChat}
          className={cn(
            "flex items-center gap-2 w-full px-3 py-2.5 rounded-md text-sm transition-colors min-h-[44px]",
            pathname === "/" && !chat.activeConversationId
              ? "bg-secondary text-secondary-foreground font-medium"
              : "text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          <Plus className="h-4 w-4" />
          New Chat
        </button>
      </div>

      {/* Nav items */}
      <nav className="px-2 space-y-0.5">
        {navItems.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            onClick={handleNav}
            className={cn(
              "flex items-center gap-2 px-3 py-2.5 rounded-md text-sm transition-colors min-h-[44px]",
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
              onClick={handleNav}
              className={cn(
                "flex items-center gap-2 px-3 py-2.5 rounded-md text-sm transition-colors min-h-[44px]",
                isAdminSection
                  ? "bg-secondary text-secondary-foreground font-medium"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Shield className="h-4 w-4" />
              Admin
            </Link>
            {isAdminSection && visibleAdminLinks.length > 0 && (
              <div className="ml-3 space-y-0.5 border-l border-border/60 pl-2">
                {visibleAdminLinks.map(({ href, label, icon: Icon }) => (
                  <Link
                    key={href}
                    href={href}
                    onClick={handleNav}
                    className={cn(
                      "flex items-center gap-2 px-3 py-2 rounded-md text-xs transition-colors min-h-[40px]",
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

        <Link
          href="/settings"
          onClick={handleNav}
          className={cn(
            "flex items-center gap-2 px-3 py-2.5 rounded-md text-sm transition-colors min-h-[44px]",
            pathname === "/settings" || pathname.startsWith("/settings/")
              ? "bg-secondary text-secondary-foreground font-medium"
              : "text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          <User className="h-4 w-4" />
          Settings
        </Link>

        <button
          onClick={() => setGuideOpen(true)}
          className="flex items-center gap-2 w-full px-3 py-2.5 rounded-md text-sm transition-colors min-h-[44px] text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <HelpCircle className="h-4 w-4" />
          User Guide
        </button>
      </nav>

      <div className="mx-3 my-2 border-t" />

      {/* Conversations */}
      <div className="flex-1 overflow-y-auto px-2 pb-2">
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

      <NotificationBell />

      <UserGuideDialog open={guideOpen} onClose={() => setGuideOpen(false)} />

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
    </div>
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
        "group flex items-center gap-1 px-3 py-2.5 cursor-pointer transition-colors text-sm leading-tight rounded-md min-h-[44px]",
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
        className="shrink-0 p-1.5 rounded text-muted-foreground hover:text-destructive transition-colors min-h-[36px] min-w-[36px] flex items-center justify-center"
        title="Delete"
        aria-label={`Delete conversation ${title}`}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
