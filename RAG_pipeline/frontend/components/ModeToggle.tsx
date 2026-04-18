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
    <div className="inline-flex rounded-lg border border-input bg-background p-0.5" data-tour="mode-toggle">
      <button
        type="button"
        onClick={() => onModeChange("chat")}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
          mode === "chat"
            ? "bg-primary text-primary-foreground"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        <MessageSquare className="h-3 w-3" />
        Chat
      </button>
      <button
        type="button"
        onClick={() => onModeChange("deep-dive")}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
          mode === "deep-dive"
            ? "bg-primary text-primary-foreground"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        <Code className="h-3 w-3" />
        Deep Dive
      </button>
    </div>
  );
}
