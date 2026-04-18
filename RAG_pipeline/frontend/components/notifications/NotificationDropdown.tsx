"use client";

import { CheckCheck, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  useMarkAllRead,
  useMarkRead,
  useNotifications,
} from "@/hooks/useNotifications";
import type { Notification } from "@/types/onboarding";

interface Props {
  onClose: () => void;
}

export function NotificationDropdown({ onClose }: Props) {
  const { data, isLoading } = useNotifications();
  const markRead = useMarkRead();
  const markAllRead = useMarkAllRead();

  const notifs: Notification[] = data?.notifications ?? [];
  const unread = data?.unread_count ?? 0;

  return (
    <div className="absolute bottom-full left-0 mb-2 w-80 bg-card border border-border rounded-lg shadow-xl z-50">
      <div className="flex items-center justify-between px-3 py-2 border-b">
        <h3 className="text-sm font-semibold">Notifications</h3>
        {unread > 0 && (
          <button
            onClick={() => markAllRead.mutate()}
            className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
          >
            <CheckCheck className="h-3 w-3" />
            Mark all read
          </button>
        )}
      </div>

      <div className="max-h-80 overflow-y-auto">
        {isLoading ? (
          <div className="flex items-center justify-center py-8 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        ) : notifs.length === 0 ? (
          <div className="text-center text-sm text-muted-foreground py-8">
            No notifications yet.
          </div>
        ) : (
          <ul>
            {notifs.map((n) => (
              <li
                key={n.id}
                className={cn(
                  "px-3 py-2.5 border-b last:border-b-0 cursor-pointer transition-colors hover:bg-muted/40",
                  !n.is_read && "bg-primary/5",
                )}
                onClick={() => {
                  if (!n.is_read) markRead.mutate(n.id);
                }}
              >
                <div className="flex items-start gap-2">
                  {!n.is_read && (
                    <span className="shrink-0 mt-1.5 h-2 w-2 rounded-full bg-primary" />
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium leading-snug">
                      {n.title}
                    </p>
                    <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                      {n.message}
                    </p>
                    <p className="text-[10px] text-muted-foreground mt-1">
                      {new Date(n.created_at).toLocaleString()}
                    </p>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
