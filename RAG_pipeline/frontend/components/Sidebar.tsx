"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Search, MessageSquare, FileText, BookOpen, Settings, LogOut, Shield, Users, KeyRound, FolderKanban, BarChart3, FlaskConical } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuthContext } from "@/components/AuthProvider";
import { usePermissions } from "@/hooks/usePermissions";

const navItems = [
  { href: "/", label: "Search", icon: Search },
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/docs", label: "Project Docs", icon: BookOpen },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const { user, logout } = useAuthContext();
  const { hasAnyAdminPermission, hasPermission } = usePermissions();

  // Admin sub-links with their required permissions
  const adminSubLinks = [
    { href: "/admin/users", label: "Users", icon: Users, perm: "manage_users" as const },
    { href: "/admin/roles", label: "Roles", icon: KeyRound, perm: "manage_roles" as const },
    { href: "/admin/project-assignments", label: "Projects", icon: FolderKanban, perm: "assign_projects" as const },
    { href: "/admin/usage", label: "Usage", icon: BarChart3, perm: "manage_users" as const },
    { href: "/admin/benchmark", label: "Benchmark", icon: FlaskConical, perm: "manage_users" as const },
  ];

  const visibleAdminLinks = adminSubLinks.filter(
    (link) => hasPermission(link.perm),
  );

  return (
    <aside className="w-56 border-r bg-card flex flex-col h-full">
      <div className="p-4 border-b">
        <h1 className="font-bold text-lg">IntoData KB</h1>
        <p className="text-xs text-muted-foreground">Knowledge Base</p>
      </div>

      <nav className="flex-1 p-2 space-y-1">
        {navItems.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors",
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
              href={visibleAdminLinks[0]?.href ?? "/admin/users"}
              className={cn(
                "flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors",
                pathname.startsWith("/admin")
                  ? "bg-secondary text-secondary-foreground font-medium"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Shield className="h-4 w-4" />
              Admin
            </Link>
            {pathname.startsWith("/admin") && visibleAdminLinks.length > 0 && (
              <div className="ml-4 space-y-0.5">
                {visibleAdminLinks.map(({ href, label, icon: Icon }) => (
                  <Link
                    key={href}
                    href={href}
                    className={cn(
                      "flex items-center gap-2 px-3 py-1.5 rounded-md text-xs transition-colors",
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
      </nav>

      <div className="p-4 border-t space-y-2">
        {user && (
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground truncate">
              {user.username}
            </span>
            <button
              onClick={logout}
              className="text-muted-foreground hover:text-foreground"
              title="Sign out"
            >
              <LogOut className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
        <p className="text-xs text-muted-foreground">IntoData KB v0.1.0</p>
      </div>
    </aside>
  );
}
