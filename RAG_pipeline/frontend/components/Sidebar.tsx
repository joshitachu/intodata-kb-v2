"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Search, MessageSquare, FileText, BookOpen, Settings, LogOut, Shield } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuthContext } from "@/components/AuthProvider";

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
        {user?.is_superadmin && (
          <Link
            href="/admin/users"
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
