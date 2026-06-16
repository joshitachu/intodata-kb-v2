"use client";

import { AlertTriangle, Check, Loader2 } from "lucide-react";
import type { RuntimeToolProgress } from "@/types";

// Platform-agnostic, human-readable labels for known generic runtime tools.
// Unknown tool names fall back to a prettified version of the raw name.
const TOOL_LABELS: Record<string, string> = {
  list_executions: "List executions",
  get_execution_details: "Get execution details",
  detect_failures: "Detect failures",
  get_execution_logs: "Get execution logs",
};

export function runtimeToolLabel(toolName: string): string {
  const known = TOOL_LABELS[toolName];
  if (known) return known;
  const words = toolName.split(/[_\s]+/).filter(Boolean);
  if (words.length === 0) return "Runtime check";
  return words
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

interface RuntimeToolStatusProps {
  tools: RuntimeToolProgress[];
}

export function RuntimeToolStatus({ tools }: RuntimeToolStatusProps) {
  if (tools.length === 0) return null;

  return (
    <div className="flex flex-col gap-1 pb-1.5">
      {tools.map((tool, index) => {
        const label = runtimeToolLabel(tool.toolName);
        return (
          <div
            key={`${tool.toolName}-${index}`}
            className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground"
          >
            {tool.status === "running" && (
              <>
                <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
                <span>Checking runtime: {label}</span>
              </>
            )}
            {tool.status === "completed" && (
              <>
                <Check className="h-3 w-3 shrink-0 text-green-600" />
                <span>Runtime check complete: {label}</span>
                {tool.latencyMs !== null && (
                  <span className="text-muted-foreground/70">{tool.latencyMs}ms</span>
                )}
                {tool.truncated && (
                  <span className="text-muted-foreground/70">(truncated)</span>
                )}
              </>
            )}
            {tool.status === "failed" && (
              <>
                <AlertTriangle className="h-3 w-3 shrink-0 text-amber-600" />
                <span>
                  Runtime check unavailable: {label}
                  {tool.error?.code ? ` (${tool.error.code})` : ""}
                </span>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
