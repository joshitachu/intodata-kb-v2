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
      className="inline-flex rounded-lg border border-input bg-background p-0.5"
      data-tour="mode-toggle"
    >
      <button
        type="button"
        onClick={() => onModeChange("chat")}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md text-xs font-medium transition-colors",
          "min-h-[36px] sm:min-h-0 px-3 sm:px-2.5 py-1.5 sm:py-1",
          mode === "chat"
            ? "bg-primary text-primary-foreground"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        <MessageSquare className="h-3.5 w-3.5" />
        Chat
      </button>
      <button
        type="button"
        onClick={() => onModeChange("deep-dive")}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md text-xs font-medium transition-colors",
          "min-h-[36px] sm:min-h-0 px-3 sm:px-2.5 py-1.5 sm:py-1",
          mode === "deep-dive"
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
