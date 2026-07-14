"use client";

import { useCallback, useEffect, useId, useMemo, useState, type KeyboardEvent } from "react";
import { CornerDownLeft } from "lucide-react";
import { cn } from "@/lib/utils";
import { filterSlashCommands } from "@/hooks/useSlashCommands";
import type { SlashCommand } from "@/types";

interface SlashCommandPaletteProps {
  commands: SlashCommand[];
  query: string;
  onSelect: (command: SlashCommand) => void;
  onDismiss?: () => void;
  className?: string;
  state?: SlashCommandPaletteState;
}

export interface SlashCommandPaletteState {
  matches: SlashCommand[];
  activeIndex: number;
  setActiveIndex: (index: number) => void;
  onKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
}

/**
 * Keyboard state for an input-mounted command palette. Attach ``onKeyDown``
 * to the composer and pass the returned state to ``SlashCommandPalette``.
 */
export function useSlashCommandPalette({
  commands,
  query,
  onSelect,
  onDismiss,
}: Omit<SlashCommandPaletteProps, "className" | "state">): SlashCommandPaletteState {
  const matches = useMemo(() => filterSlashCommands(commands, query), [commands, query]);
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => setActiveIndex(0), [query]);

  const onKeyDown = useCallback((event: KeyboardEvent<HTMLElement>) => {
    if (matches.length === 0) {
      if (event.key === "Escape") {
        event.preventDefault();
        onDismiss?.();
      }
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((value) => (value + 1) % matches.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((value) => (value - 1 + matches.length) % matches.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const command = matches[activeIndex];
      if (command) onSelect(command);
    } else if (event.key === "Escape") {
      event.preventDefault();
      onDismiss?.();
    }
  }, [activeIndex, matches, onDismiss, onSelect]);

  return { matches, activeIndex, setActiveIndex, onKeyDown };
}

/** A controlled command menu that can be mounted by ChatInput or another composer. */
export function SlashCommandPalette({
  commands,
  query,
  onSelect,
  onDismiss,
  className,
  state,
}: SlashCommandPaletteProps) {
  const listId = useId();
  const internalState = useSlashCommandPalette({ commands, query, onSelect, onDismiss });
  const palette = state ?? internalState;

  if (!query.trimStart().startsWith("/") || palette.matches.length === 0) return null;

  function choose(index: number) {
    const command = palette.matches[index];
    if (command) onSelect(command);
  }

  return (
    <div
      id={listId}
      role="listbox"
      aria-label="Slash commands"
      className={cn("overflow-hidden rounded-md border bg-popover text-popover-foreground shadow-lg", className)}
      onKeyDown={palette.onKeyDown}
      tabIndex={0}
    >
      {palette.matches.map((command, index) => (
        <button
          key={command.name}
          id={`${listId}-${command.name}`}
          type="button"
          role="option"
          aria-selected={palette.activeIndex === index}
          className={cn(
            "flex min-h-12 w-full items-center gap-3 px-3 py-2 text-left text-sm",
            palette.activeIndex === index ? "bg-accent text-accent-foreground" : "hover:bg-muted",
          )}
          onMouseEnter={() => palette.setActiveIndex(index)}
          onClick={() => choose(index)}
        >
          <span className="font-mono text-xs font-medium">/{command.name}</span>
          <span className="min-w-0 flex-1">
            <span className="block">{command.description}</span>
            {command.argument_hint && (
              <span className="block text-xs text-muted-foreground">{command.argument_hint}</span>
            )}
          </span>
          <CornerDownLeft className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        </button>
      ))}
    </div>
  );
}
