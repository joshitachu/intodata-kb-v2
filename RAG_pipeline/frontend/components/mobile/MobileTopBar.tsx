"use client";

import type { ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Menu } from "lucide-react";
import { cn } from "@/lib/utils";

export interface MobileTopBarProps {
  title?: string;
  onBack?: () => void;
  backHref?: string;
  leftAction?: ReactNode;
  rightAction?: ReactNode;
  onMenuClick?: () => void;
  showMenuButton?: boolean;
}

export function MobileTopBar({
  title,
  onBack,
  backHref,
  leftAction,
  rightAction,
  onMenuClick,
  showMenuButton,
}: MobileTopBarProps) {
  const router = useRouter();

  function handleBack() {
    if (onBack) {
      onBack();
      return;
    }
    if (typeof window !== "undefined" && window.history.length > 1) {
      router.back();
    } else if (backHref) {
      router.push(backHref);
    }
  }

  function renderLeft() {
    if (leftAction) return leftAction;
    if (showMenuButton) {
      return (
        <button
          onClick={onMenuClick}
          className="shrink-0 -ml-2 p-2 rounded-md text-foreground hover:bg-muted transition-colors min-h-[44px] min-w-[44px] flex items-center justify-center"
          aria-label="Open menu"
          title="Open menu"
        >
          <Menu className="h-5 w-5" />
        </button>
      );
    }
    if (onBack || backHref) {
      return (
        <button
          onClick={handleBack}
          className="shrink-0 -ml-2 p-2 rounded-md text-foreground hover:bg-muted transition-colors min-h-[44px] min-w-[44px] flex items-center justify-center"
          aria-label="Go back"
          title="Go back"
        >
          <ArrowLeft className="h-5 w-5" />
        </button>
      );
    }
    return null;
  }

  return (
    <header
      className={cn(
        "sticky top-0 z-30 pt-safe",
        "h-14 flex items-center gap-2 px-3",
        "bg-background/85 backdrop-blur supports-[backdrop-filter]:bg-background/70",
        "border-b border-border/60",
      )}
    >
      {renderLeft()}
      {title && (
        <h1 className="flex-1 min-w-0 text-base font-semibold truncate text-foreground">
          {title}
        </h1>
      )}
      {!title && <div className="flex-1" />}
      {rightAction && (
        <div className="shrink-0 flex items-center gap-1">{rightAction}</div>
      )}
    </header>
  );
}
