"use client";

import { useEffect, useRef } from "react";
import { MessageBubble } from "./MessageBubble";
import type { MessageResponse } from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";

interface MessageListProps {
  messages: MessageResponse[];
  streamingMessage?: StreamState | null;
  pendingUserMessage?: string | null;
}

export function MessageList({
  messages,
  streamingMessage,
  pendingUserMessage,
}: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingMessage?.content]);

  if (messages.length === 0 && !pendingUserMessage) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground">
        <p>Start a conversation by typing a message below.</p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-4">
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}

      {/* Optimistic user message while streaming */}
      {pendingUserMessage && (
        <MessageBubble
          message={{
            id: -1,
            role: "user",
            content: pendingUserMessage,
            sources: null,
            latency_ms: null,
            created_at: new Date().toISOString(),
          }}
        />
      )}

      {/* Streaming assistant message */}
      {streamingMessage && (streamingMessage.isStreaming || streamingMessage.content) && (
        <MessageBubble
          message={{
            id: -2,
            role: "assistant",
            content: streamingMessage.content,
            sources:
              streamingMessage.sources.length > 0
                ? streamingMessage.sources
                : null,
            latency_ms: streamingMessage.latencyMs,
            created_at: new Date().toISOString(),
          }}
          isStreaming={streamingMessage.isStreaming}
        />
      )}

      <div ref={bottomRef} />
    </div>
  );
}
