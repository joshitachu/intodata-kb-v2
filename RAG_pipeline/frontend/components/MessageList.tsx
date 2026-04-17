"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { ArrowDown, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { MessageBubble } from "./MessageBubble";
import { DeepDiveChatBubble } from "./DeepDiveChatBubble";
import type { MessageResponse } from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";
import type { DeepDiveState } from "@/hooks/useDeepDive";

const SUGGESTIONS = [
  "What projects are available?",
  "Summarize the latest documentation",
  "How do the integrations connect?",
  "What are the key data flows?",
];

interface MessageListProps {
  messages: MessageResponse[];
  streamingMessage?: StreamState | null;
  pendingUserMessage?: string | null;
  deepDiveState?: DeepDiveState | null;
  onCancelDeepDive?: () => void;
  onSuggestionClick?: (suggestion: string) => void;
}

export function MessageList({
  messages,
  streamingMessage,
  pendingUserMessage,
  deepDiveState,
  onCancelDeepDive,
  onSuggestionClick,
}: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  // Auto-scroll on new content
  useEffect(() => {
    scrollToBottom();
  }, [
    messages,
    streamingMessage?.content,
    deepDiveState?.content,
    deepDiveState?.thinking,
    scrollToBottom,
  ]);

  // Track scroll position for scroll-to-bottom button
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    function handleScroll() {
      if (!container) return;
      const distanceFromBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight;
      setShowScrollBtn(distanceFromBottom > 100);
    }

    container.addEventListener("scroll", handleScroll);
    return () => container.removeEventListener("scroll", handleScroll);
  }, []);

  // Empty conversation state
  if (messages.length === 0 && !pendingUserMessage) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center px-6 py-12">
        <div className="max-w-md text-center space-y-4">
          <p className="text-sm text-muted-foreground">
            Start a conversation by typing a message below, or try a suggestion:
          </p>
          <div className="flex flex-wrap justify-center gap-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => onSuggestionClick?.(s)}
                className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border bg-card hover:bg-muted/60 transition-colors text-muted-foreground hover:text-foreground"
              >
                <Sparkles className="h-3 w-3 text-primary/60" />
                {s}
              </button>
            ))}
          </div>
        </div>
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
    <div ref={scrollContainerRef} className="relative flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-6 space-y-6">
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

        {/* Deep dive response */}
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
              hasSources={streamingMessage.sources.length > 0}
              usage={streamingMessage.usage}
            />
          )}

        <div ref={bottomRef} />
      </div>

      {/* Scroll to bottom button */}
      {showScrollBtn && (
        <button
          onClick={scrollToBottom}
          className={cn(
            "absolute bottom-4 left-1/2 -translate-x-1/2",
            "flex items-center justify-center w-8 h-8 rounded-full",
            "bg-background border shadow-md",
            "hover:bg-muted transition-colors",
            "animate-message-enter",
          )}
        >
          <ArrowDown className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
