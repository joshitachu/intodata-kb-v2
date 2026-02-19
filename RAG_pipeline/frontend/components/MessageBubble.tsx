"use client";

import { User, Bot } from "lucide-react";
import { cn } from "@/lib/utils";
import { SourceCitation } from "./SourceCitation";
import { MarkdownContent } from "./MarkdownContent";
import type { MessageResponse } from "@/types";

interface MessageBubbleProps {
  message: MessageResponse;
  isStreaming?: boolean;
}

export function MessageBubble({ message, isStreaming }: MessageBubbleProps) {
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

        {message.latency_ms && (
          <p className="text-xs text-muted-foreground">
            {message.latency_ms}ms
          </p>
        )}
      </div>
    </div>
  );
}
