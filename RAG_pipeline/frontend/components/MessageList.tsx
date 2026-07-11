"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { AlertTriangle, ArrowDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { MessageBubble } from "./MessageBubble";
import { DeepDiveChatBubble } from "./DeepDiveChatBubble";
import { SUGGESTIONS } from "@/lib/constants";
import type { MessageResponse } from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";
import type { DeepDiveState } from "@/hooks/useDeepDive";

interface MessageListProps {
  messages: MessageResponse[];
  conversationId?: number | null;
  streamingMessage?: StreamState | null;
  onRetryMessage?: () => void;
  pendingUserMessage?: string | null;
  deepDiveState?: DeepDiveState | null;
  onCancelDeepDive?: () => void;
  onSuggestionClick?: (suggestion: string) => void;
}

export function MessageList({
  messages,
  conversationId,
  streamingMessage,
  onRetryMessage,
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
    streamingMessage?.harnessActivities,
    streamingMessage?.runtimeTools,
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

  const hasDeepDiveContent =
    deepDiveState &&
    (deepDiveState.isStreaming ||
      deepDiveState.content ||
      deepDiveState.thinking ||
      deepDiveState.error);
  const latestMessage = messages.at(-1);
  const liveErrorHasPersistedMessage =
    Boolean(streamingMessage?.error) && latestMessage?.status === "failed";

  // Empty conversation state
  if (
    messages.length === 0 &&
    !pendingUserMessage &&
    !hasDeepDiveContent &&
    !streamingMessage
  ) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center px-6 py-12">
        <div className="max-w-md text-center space-y-4">
          <div className="flex items-start gap-2 rounded-lg border border-amber-400/40 bg-amber-400/10 px-3 py-2.5 text-xs text-amber-700 dark:text-amber-400 text-left">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <span>AI responses may contain errors or outdated information. Always verify before acting.</span>
          </div>
          <p className="text-sm text-muted-foreground">
            Start a conversation by typing a message below, or try a suggestion:
          </p>
          <div className="flex flex-wrap justify-center gap-2">
            {SUGGESTIONS.map((s) => {
              const Icon = s.icon;
              return (
                <button
                  key={s.label}
                  onClick={() => onSuggestionClick?.(s.label)}
                  className="group inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border bg-card hover:bg-muted/60 transition-colors text-muted-foreground hover:text-foreground"
                >
                  <Icon className="h-3 w-3 text-primary/60 transition-colors group-hover:text-primary" />
                  {s.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div ref={scrollContainerRef} className="relative flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-6 space-y-6">
        {messages.map((message) => (
          <MessageBubble
            key={message.id}
            message={message}
            conversationId={conversationId ?? null}
            onRetry={
              liveErrorHasPersistedMessage && message.id === latestMessage?.id
                ? onRetryMessage
                : undefined
            }
          />
        ))}

        {/* Optimistic user message: prefer pendingUserMessage, fall back to stored deep dive question */}
        {(pendingUserMessage || (hasDeepDiveContent && deepDiveState?.question)) && (
          <MessageBubble
            message={{
              id: -1,
              role: "user",
              content: pendingUserMessage ?? deepDiveState!.question!,
              sources: null,
              latency_ms: null,
              status: "completed",
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
          !liveErrorHasPersistedMessage &&
          (streamingMessage.isStreaming ||
            streamingMessage.content ||
            streamingMessage.error) && (
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
                status: streamingMessage.error ? "failed" : "completed",
                error_message: streamingMessage.error,
                created_at: new Date().toISOString(),
              }}
              isStreaming={streamingMessage.isStreaming}
              hasSources={streamingMessage.sources.length > 0}
              usage={streamingMessage.usage}
              steps={streamingMessage.steps}
              harnessActivities={streamingMessage.harnessActivities}
              runtimeTools={streamingMessage.runtimeTools}
              onRetry={streamingMessage.error ? onRetryMessage : undefined}
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
