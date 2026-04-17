"use client";

import { useState } from "react";
import {
  Loader2,
  Terminal,
  AlertCircle,
  XCircle,
  ChevronDown,
  ChevronRight,
  Brain,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { MarkdownContent } from "@/components/MarkdownContent";
import type { DeepDiveState } from "@/hooks/useDeepDive";

interface DeepDiveChatBubbleProps {
  state: DeepDiveState;
  onCancel: () => void;
}

export function DeepDiveChatBubble({
  state,
  onCancel,
}: DeepDiveChatBubbleProps) {
  const { isStreaming, content, thinking, toolCalls, error, usage } = state;
  const [showThinking, setShowThinking] = useState(false);

  // Nothing to display
  if (!isStreaming && !content && !error && !thinking && toolCalls.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-row">
      <div className="flex flex-col gap-2 items-start max-w-[85%]">
        {/* Thinking section (collapsible) */}
        {thinking && (
          <button
            type="button"
            onClick={() => setShowThinking(!showThinking)}
            className={cn(
              "flex items-center gap-1.5 text-xs font-medium text-muted-foreground",
              "hover:text-foreground transition-colors"
            )}
          >
            {showThinking ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            <Brain className="h-3 w-3" />
            Thinking
            {isStreaming && !content && (
              <Loader2 className="h-3 w-3 animate-spin ml-1" />
            )}
          </button>
        )}

        {showThinking && thinking && (
          <div className="rounded-lg border border-input bg-muted/30 p-3 text-xs text-muted-foreground max-h-60 overflow-y-auto w-full">
            <MarkdownContent
              content={thinking}
              className="prose-xs"
            />
          </div>
        )}

        {/* Tool calls progress */}
        {toolCalls.length > 0 && (
          <div className="rounded-lg border border-input bg-muted/50 p-3 w-full">
            <div className="flex items-center gap-2 mb-2 text-xs font-medium text-muted-foreground">
              <Terminal className="h-3 w-3" />
              Analysis Steps
            </div>
            <div className="space-y-1 max-h-32 overflow-y-auto">
              {toolCalls.map((call, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2 text-xs font-mono text-muted-foreground"
                >
                  <span className="text-primary shrink-0">
                    {i === toolCalls.length - 1 && isStreaming ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      ">"
                    )}
                  </span>
                  <span className="break-all">{call}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Streaming indicator when no content yet */}
        {isStreaming && !content && !thinking && toolCalls.length === 0 && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Starting deep dive analysis...
          </div>
        )}

        {/* Answer content */}
        {content && (
          <div className="py-1">
            <MarkdownContent content={content} />
            {isStreaming && (
              <span className="inline-block w-2 h-4 bg-foreground/70 animate-pulse rounded-sm ml-0.5 align-text-bottom" />
            )}
          </div>
        )}

        {/* Cancel button */}
        {isStreaming && (
          <button
            type="button"
            onClick={onCancel}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5",
              "text-xs font-medium text-destructive",
              "border border-destructive/30 hover:bg-destructive/10",
              "transition-colors"
            )}
          >
            <XCircle className="h-3 w-3" />
            Cancel
          </button>
        )}

        {/* Error display */}
        {error && (
          <div className="flex items-start gap-2 rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-xs text-destructive">
            <AlertCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {/* Usage stats (shown after completion) */}
        {!isStreaming && usage && (
          <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
            {usage.duration_ms != null && (
              <span>Duration: {(usage.duration_ms / 1000).toFixed(1)}s</span>
            )}
            {usage.cost_usd != null && (
              <span>Cost: ${usage.cost_usd.toFixed(4)}</span>
            )}
            {usage.input_tokens != null && (
              <span>Input: {usage.input_tokens.toLocaleString()} tokens</span>
            )}
            {usage.output_tokens != null && (
              <span>Output: {usage.output_tokens.toLocaleString()} tokens</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
