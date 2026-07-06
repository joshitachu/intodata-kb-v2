"use client";

import { useRef, useState, type Dispatch, type SetStateAction } from "react";
import { Bell } from "lucide-react";
import { cn } from "@/lib/utils";
import { useUnreadCount } from "@/hooks/useNotifications";
import { useViewport } from "@/hooks/useViewport";
import { NotificationDropdown } from "./NotificationDropdown";
import { MobileBottomSheet } from "@/components/mobile/MobileBottomSheet";

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const { data } = useUnreadCount(true);
  const unread = data?.unread_count ?? 0;
  const { isMobile } = useViewport();

  if (isMobile) {
    return (
      <>
        <button
          onClick={() => setOpen(true)}
          className={cn(
            "relative p-2 rounded-md transition-colors min-h-[40px] min-w-[40px] flex items-center justify-center",
            "text-muted-foreground hover:text-foreground hover:bg-muted",
          )}
          title="Notifications"
          aria-label="Notifications"
        >
          <Bell className="h-4 w-4" />
          {unread > 0 && (
            <span className="absolute -top-0.5 -right-0.5 flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-red-500 text-white text-[10px] font-semibold">
              {unread > 99 ? "99+" : unread}
            </span>
          )}
        </button>
        <MobileBottomSheet
          open={open}
          onClose={() => setOpen(false)}
          title="Notifications"
          ariaLabel="Notifications"
        >
          <NotificationDropdown onClose={() => setOpen(false)} />
        </MobileBottomSheet>
      </>
    );
  }

  return <NotificationBellDesktop open={open} setOpen={setOpen} unread={unread} />;
}

function NotificationBellDesktop({
  open,
  setOpen,
  unread,
}: {
  open: boolean;
  setOpen: Dispatch<SetStateAction<boolean>>;
  unread: number;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);

  return (
    <div className="relative" ref={wrapperRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "relative p-1.5 rounded-md transition-colors",
          "text-muted-foreground hover:text-foreground hover:bg-muted",
        )}
        title="Notifications"
        aria-label="Notifications"
      >
        <Bell className="h-4 w-4" />
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-red-500 text-white text-[10px] font-semibold">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && <NotificationDropdown onClose={() => setOpen(false)} />}
    </div>
  );
}
