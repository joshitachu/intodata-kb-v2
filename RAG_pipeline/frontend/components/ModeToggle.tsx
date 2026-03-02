"use client";

import { Search, Code, MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";

export type SearchMode = "search" | "deep-dive";
export type ChatMode = "chat" | "deep-dive";

// Overloaded props: variant determines which modes are available
type ModeToggleProps =
  | {
      variant?: "search";
      mode: SearchMode;
      onModeChange: (mode: SearchMode) => void;
    }
  | {
      variant: "chat";
      mode: ChatMode;
      onModeChange: (mode: ChatMode) => void;
    };

export function ModeToggle(props: ModeToggleProps) {
  const variant = props.variant ?? "search";
  const primaryMode = variant === "chat" ? "chat" : "search";
  const primaryLabel = variant === "chat" ? "Chat" : "Search";
  const PrimaryIcon = variant === "chat" ? MessageSquare : Search;

  return (
    <div className="inline-flex rounded-lg border border-input bg-background p-1">
      <button
        type="button"
        onClick={() => (props.onModeChange as (m: string) => void)(primaryMode)}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
          props.mode === primaryMode
            ? "bg-primary text-primary-foreground"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        <PrimaryIcon className="h-3.5 w-3.5" />
        {primaryLabel}
      </button>
      <button
        type="button"
        onClick={() => (props.onModeChange as (m: string) => void)("deep-dive")}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
          props.mode === "deep-dive"
            ? "bg-primary text-primary-foreground"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        <Code className="h-3.5 w-3.5" />
        Deep Dive
      </button>
    </div>
  );
}
