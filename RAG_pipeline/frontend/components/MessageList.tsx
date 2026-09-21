"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { AlertTriangle, ArrowDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { MessageBubble } from "./MessageBubble";
import { SUGGESTIONS } from "@/lib/constants";
import { STREAM_REVEAL_KEY, messageRevealKey } from "@/lib/answerReveal";
import type { MessageResponse } from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";

interface MessageListProps {
  messages: MessageResponse[];
  conversationId?: number | null;
  streamingMessage?: StreamState | null;
  onRetryMessage?: () => void;
  pendingUserMessage?: string | null;
  onSuggestionClick?: (suggestion: string) => void;
  /**
   * Render topology targets as links to `/topology` (see `MessageBubble`).
   * Set by the chat page, which has no canvas of its own; left off by the
   * docked copilot, which renders them as chips against the canvas beside it.
   */
  showTopologyLinks?: boolean;
  topologyProjectId?: string | null;
  /**
   * Progressive reveal state from the host (`hooks/useProgressiveReveal`).
   *
   * `key` names which bubble it applies to -- the live stream, or one persisted
   * message -- so a reveal survives the hand-off from the stream buffer to the
   * saved answer without this component needing to know a turn happened. Absent
   * means no pacing: every bubble renders its full text.
   */
  revealedAnswer?: { key: string; text: string; isRevealing: boolean } | null;
  /**
   * Assistant message ids known to have run in Deep Dive
   * (`ChatContext.deepDiveMessageIds`). Absent means "nothing is known", which
   * is not the same as "nothing ran in Deep Dive" -- a reloaded thread knows
   * nothing, because no turn's mode is persisted anywhere. Hosts that do not
   * offer the mode at all (the docked copilot) simply never pass it.
   */
  deepDiveMessageIds?: ReadonlySet<number>;
  /** True while the turn currently streaming is running in Deep Dive. */
  isStreamingDeepDive?: boolean;
}

export function MessageList({
  messages,
  conversationId,
  streamingMessage,
  onRetryMessage,
  pendingUserMessage,
  onSuggestionClick,
  showTopologyLinks,
  topologyProjectId,
  revealedAnswer,
  deepDiveMessageIds,
  isStreamingDeepDive,
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
    // The just-sent question renders before any message or delta exists, so
    // omitting it left the view parked on the previous turn: the question the
    // user had just asked, and then the whole answer, arrived off-screen below
    // with no indication anything had happened.
    pendingUserMessage,
    streamingMessage?.content,
    streamingMessage?.typedResponse,
    // Revealed text grows after the last delta has landed, so the thread has to
    // follow the reveal, not just the buffer.
    revealedAnswer?.text,
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

  const latestMessage = messages.at(-1);
  const liveErrorHasPersistedMessage =
    Boolean(streamingMessage?.error) && latestMessage?.status === "failed";
  const persistedStreamMessageId = streamingMessage?.messageId ?? null;

  // Empty conversation state
  if (messages.length === 0 && !pendingUserMessage && !streamingMessage) {
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
        {messages
          // During the stream-to-query hand-off, render the completed stream
          // until the host resets it. Hiding its persisted twin for this one
          // render prevents duplicate bubbles and lets progressive reveal carry
          // across the source swap without a blank frame.
          .filter((message) => message.id !== persistedStreamMessageId)
          .map((message) => (
          <MessageBubble
            key={message.id}
            message={message}
            conversationId={conversationId ?? null}
            revealedContent={
              revealedAnswer?.key === messageRevealKey(message.id)
                ? revealedAnswer.text
                : undefined
            }
            isRevealing={
              revealedAnswer?.key === messageRevealKey(message.id)
                ? revealedAnswer.isRevealing
                : undefined
            }
            ranInDeepDive={deepDiveMessageIds?.has(message.id) ?? false}
            harnessActivities={message.harness_activities}
            pendingAction={message.pending_action}
            workflowCards={message.workflow_cards}
            showTopologyLinks={showTopologyLinks}
            topologyProjectId={topologyProjectId}
            onRetry={
              liveErrorHasPersistedMessage && message.id === latestMessage?.id
                ? onRetryMessage
                : undefined
            }
          />
        ))}

        {/* Optimistic user message, shown until the turn's own record lands */}
        {pendingUserMessage && (
          <MessageBubble
            message={{
              id: -1,
              role: "user",
              content: pendingUserMessage,
              sources: null,
              latency_ms: null,
              status: "completed",
              created_at: new Date().toISOString(),
            }}
          />
        )}

        {/* Streaming assistant message */}
        {streamingMessage &&
          !liveErrorHasPersistedMessage &&
          (streamingMessage.isStreaming ||
            streamingMessage.content ||
            streamingMessage.typedResponse ||
            streamingMessage.error) && (
            <MessageBubble
              message={{
                id: -2,
                role: "assistant",
                content: streamingMessage.content,
                sources: null,
                latency_ms: streamingMessage.latencyMs,
                status: streamingMessage.error ? "failed" : "completed",
                error_message: streamingMessage.error,
                structured_answer: streamingMessage.typedResponse,
                workflow_cards: streamingMessage.typedResponse?.workflow_cards,
                created_at: new Date().toISOString(),
              }}
              ranInDeepDive={isStreamingDeepDive ?? false}
              isStreaming={streamingMessage.isStreaming}
              streamingStatus={streamingMessage.progress}
              streamProgress={streamingMessage.progressDetail ?? null}
              streamEvents={streamingMessage.agentEvents}
              revealedContent={
                revealedAnswer?.key === STREAM_REVEAL_KEY ? revealedAnswer.text : undefined
              }
              isRevealing={
                revealedAnswer?.key === STREAM_REVEAL_KEY
                  ? revealedAnswer.isRevealing
                  : undefined
              }
              workflowCards={streamingMessage.typedResponse?.workflow_cards}
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
