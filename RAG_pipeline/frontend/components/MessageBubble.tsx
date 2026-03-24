"use client";

import { User, Bot } from "lucide-react";
import { cn } from "@/lib/utils";
import { SourceCitation } from "./SourceCitation";
import { MarkdownContent } from "./MarkdownContent";
import type { MessageResponse, UsageStats } from "@/types";

interface MessageBubbleProps {
  message: MessageResponse;
  isStreaming?: boolean;
  usage?: UsageStats | null;
}

export function MessageBubble({ message, isStreaming, usage }: MessageBubbleProps) {
  const isUser = message.role === "user";

  return (
    <div
      className={cn("flex gap-3", isUser ? "flex-row-reverse" : "flex-row")}
    >
      <div
        className={cn(
          "flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted"
        )}
      >
        {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
      </div>

      <div
        className={cn(
          "flex flex-col gap-2 max-w-[80%]",
          isUser ? "items-end" : "items-start"
        )}
      >
        {isUser ? (
          <div className="rounded-lg px-4 py-2 bg-primary text-primary-foreground">
            <p className="text-sm whitespace-pre-wrap">{message.content}</p>
          </div>
        ) : (
          <div className="py-1">
            {message.content ? (
              <MarkdownContent content={message.content} />
            ) : isStreaming ? (
              <span className="inline-block w-2 h-4 bg-foreground/70 animate-pulse rounded-sm" />
            ) : null}
            {isStreaming && message.content && (
              <span className="inline-block w-2 h-4 bg-foreground/70 animate-pulse rounded-sm ml-0.5 align-text-bottom" />
            )}
          </div>
        )}

        {message.sources && message.sources.length > 0 && (
          <div className="w-full space-y-1">
            <p className="text-xs text-muted-foreground px-1">Sources:</p>
            {message.sources.map((source, index) => (
              <SourceCitation key={source.document_id} source={source} index={index} />
            ))}
          </div>
        )}

        {(message.latency_ms || usage) && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {message.latency_ms && <span>{message.latency_ms}ms</span>}
            {usage && usage.input_tokens > 0 && (
              <span className="bg-[#001e4d]/10 text-[#001e4d] px-1.5 py-0.5 rounded">
                {usage.input_tokens + usage.output_tokens} tokens
              </span>
            )}
            {usage && usage.cost_usd > 0 && (
              <span className="bg-[#a0e06f]/10 text-[#a0e06f] px-1.5 py-0.5 rounded">
                ${usage.cost_usd.toFixed(4)}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
