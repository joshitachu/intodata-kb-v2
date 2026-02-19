"use client";

import { Clock, Trash2 } from "lucide-react";
import type { QueryHistoryEntry } from "@/types";
import { cn } from "@/lib/utils";

interface QueryHistoryProps {
  entries: QueryHistoryEntry[];
  onSelect: (entry: QueryHistoryEntry) => void;
  onClear: () => void;
}

export function QueryHistory({ entries, onSelect, onClear }: QueryHistoryProps) {
  if (entries.length === 0) {
    return (
      <div className="text-sm text-muted-foreground px-3 py-4">
        No queries yet. Ask a question to get started.
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-3 py-2 border-b">
        <h3 className="text-sm font-medium">History</h3>
        <button
          onClick={onClear}
          className="text-muted-foreground hover:text-foreground p-1"
          title="Clear history"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {entries.map((entry) => (
          <button
            key={entry.id}
            onClick={() => onSelect(entry)}
            className={cn(
              "w-full text-left px-3 py-2 text-sm border-b",
              "hover:bg-muted/50 transition-colors",
            )}
          >
            <p className="truncate font-medium">{entry.query}</p>
            <p className="text-xs text-muted-foreground flex items-center gap-1 mt-1">
              <Clock className="h-3 w-3" />
              {entry.timestamp.toLocaleTimeString()}
              <span className="ml-auto">{entry.latency_ms}ms</span>
            </p>
          </button>
        ))}
      </div>
    </div>
  );
}
