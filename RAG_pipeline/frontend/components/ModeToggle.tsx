"use client";

import { MessageSquare, Network } from "lucide-react";
import { cn } from "@/lib/utils";

export type ChatMode = "chat" | "deep-dive";

interface ModeToggleProps {
  mode: ChatMode;
  onModeChange: (mode: ChatMode) => void;
}

const MODES = [
  { id: "chat" as const, label: "Chat", icon: MessageSquare },
  { id: "deep-dive" as const, label: "Deep Dive", icon: Network },
];

export function ModeToggle({ mode, onModeChange }: ModeToggleProps) {
  return (
    <div
      role="radiogroup"
      aria-label="Chat mode"
      className="inline-flex h-7 shrink-0 items-center gap-0.5 rounded-full border border-border bg-muted/40 p-0.5"
      data-tour="mode-toggle"
    >
      {MODES.map(({ id, label, icon: Icon }) => {
        const isActive = mode === id;
        return (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={isActive}
            onClick={() => onModeChange(id)}
            className={cn(
              "inline-flex h-6 items-center gap-1.5 whitespace-nowrap rounded-full px-2.5",
              "text-xs font-medium transition-colors",
              isActive
                ? "bg-background text-foreground shadow-elevation-xs"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden />
            {label}
          </button>
        );
      })}
    </div>
  );
}
