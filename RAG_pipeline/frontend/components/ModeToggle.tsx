"use client";

import { MessageSquare, Code } from "lucide-react";
import { cn } from "@/lib/utils";

export type ChatMode = "chat" | "deep-dive";

interface ModeToggleProps {
  mode: ChatMode;
  onModeChange: (mode: ChatMode) => void;
}

export function ModeToggle({ mode, onModeChange }: ModeToggleProps) {
  return (
    <div
      className="inline-flex items-center gap-0.5"
      data-tour="mode-toggle"
    >
      <button
        type="button"
        onClick={() => onModeChange("chat")}
        className={cn(
          "inline-flex items-center gap-1 text-xs font-medium transition-colors",
          "px-1.5 py-0.5 rounded",
          mode === "chat"
            ? "text-foreground"
            : "text-muted-foreground/70 hover:text-muted-foreground",
        )}
      >
        <MessageSquare className="h-3 w-3" />
        Chat
      </button>
      <button
        type="button"
        onClick={() => onModeChange("deep-dive")}
        className={cn(
          "inline-flex items-center gap-1 text-xs font-medium transition-colors",
          "px-1.5 py-0.5 rounded",
          mode === "deep-dive"
            ? "text-foreground"
            : "text-muted-foreground/70 hover:text-muted-foreground",
        )}
      >
        <Code className="h-3 w-3" />
        Deep Dive
      </button>
    </div>
  );
}
