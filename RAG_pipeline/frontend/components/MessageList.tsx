"use client";

import { useEffect, useRef } from "react";
import { MessageBubble } from "./MessageBubble";
import { DeepDiveChatBubble } from "./DeepDiveChatBubble";
import type { MessageResponse } from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";
import type { DeepDiveState } from "@/hooks/useDeepDive";

interface MessageListProps {
  messages: MessageResponse[];
  streamingMessage?: StreamState | null;
  pendingUserMessage?: string | null;
  deepDiveState?: DeepDiveState | null;
  onCancelDeepDive?: () => void;
}

export function MessageList({
  messages,
  streamingMessage,
  pendingUserMessage,
  deepDiveState,
  onCancelDeepDive,
}: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingMessage?.content, deepDiveState?.content, deepDiveState?.thinking]);

  if (messages.length === 0 && !pendingUserMessage) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground">
        <p>Start a conversation by typing a message below.</p>
      </div>
    );
  }

  const hasDeepDiveContent =
    deepDiveState &&
    (deepDiveState.isStreaming ||
      deepDiveState.content ||
      deepDiveState.thinking ||
      deepDiveState.error ||
      deepDiveState.toolCalls.length > 0);

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

      {/* Deep dive response (replaces normal streaming bubble when in deep-dive mode) */}
      {hasDeepDiveContent && onCancelDeepDive && (
        <DeepDiveChatBubble
          state={deepDiveState}
          onCancel={onCancelDeepDive}
        />
      )}

      {/* Streaming assistant message (normal chat mode) */}
      {!hasDeepDiveContent &&
        streamingMessage &&
        (streamingMessage.isStreaming || streamingMessage.content) && (
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
