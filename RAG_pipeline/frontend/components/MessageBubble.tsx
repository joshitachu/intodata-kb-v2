"use client";

import { useState, useCallback } from "react";
import {
  Copy,
  Check,
  ThumbsUp,
  ThumbsDown,
  ChevronDown,
  ChevronRight,
  AlertCircle,
  RotateCcw,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { SourceCitation } from "./SourceCitation";
import { MarkdownContent } from "./MarkdownContent";
import { HarnessActivityStatus } from "./HarnessActivityStatus";
import { RuntimeToolStatus } from "./RuntimeToolStatus";
import { submitFeedback } from "@/lib/feedback";
import { useViewport } from "@/hooks/useViewport";
import type {
  AgentStep,
  HarnessActivityEvent,
  MessageResponse,
  RuntimeToolProgress,
  UsageStats,
} from "@/types";

interface MessageBubbleProps {
  message: MessageResponse;
  conversationId?: number | null;
  isStreaming?: boolean;
  hasSources?: boolean;
  usage?: UsageStats | null;
  steps?: AgentStep[];
  harnessActivities?: HarnessActivityEvent[];
  runtimeTools?: RuntimeToolProgress[];
  onRetry?: () => void;
}

export function MessageBubble({
  message,
  conversationId,
  isStreaming,
  hasSources,
  usage,
  steps,
  harnessActivities,
  runtimeTools,
  onRetry,
}: MessageBubbleProps) {
  const isUser = message.role === "user";
  const isFailed = !isUser && message.status === "failed";
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);

  // message.id < 1 marks optimistic / streaming messages that haven't been
  // persisted yet — feedback is disabled until the server assigns a real id.
  const canSubmitFeedback = !isUser && !isFailed && message.id > 0;

  function handleFeedback(next: "up" | "down") {
    if (!canSubmitFeedback) return;
    const toggledOff = feedback === next;
    const newState = toggledOff ? null : next;
    const prev = feedback;
    setFeedback(newState);  // optimistic
    void submitFeedback({
      kind: toggledOff ? "cleared" : next,
      conversation_id: conversationId ?? null,
      message_id: message.id,
      citations_snapshot: next === "down" ? message.sources ?? null : null,
    }).then((ok) => {
      if (!ok) setFeedback(prev);  // revert on failure
    });
  }
  const [sourcesExpanded, setSourcesExpanded] = useState(false);
  const [lightboxImage, setLightboxImage] = useState<string | null>(null);
  const sourceCount = message.sources?.length ?? 0;
  const imageList = message.image_data;
  const latestRunningStep = steps?.findLast((step) => step.status === "running");
  const latestStep = latestRunningStep ?? steps?.at(-1);

  const closeLightbox = useCallback(() => setLightboxImage(null), []);
  const { isMobile } = useViewport();

  function handleCopy() {
    navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="animate-message-enter group/msg">
      <div
        className={cn(
          "flex items-start",
          isUser ? "flex-row-reverse" : "flex-row",
        )}
      >
        {/* Content */}
        <div className={cn("max-w-[85%] min-w-0", isUser && "flex flex-col items-end")}>
          {isUser ? (
            /* User: pill bubble */
            <div className="rounded-2xl bg-muted/40 border px-4 py-2.5">
              <p className="text-sm whitespace-pre-wrap">{message.content}</p>
              {imageList && imageList.length > 0 && (
                <div className="flex flex-wrap gap-2 mt-2">
                  {imageList.map((img, i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => setLightboxImage(img)}
                      className="block"
                    >
                      <img
                        src={`data:image/jpeg;base64,${img}`}
                        alt={`Attachment ${i + 1}`}
                        className="h-20 w-20 rounded-lg object-cover border hover:ring-2 hover:ring-primary transition-shadow cursor-pointer"
                      />
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            /* Assistant: raw text on page background */
            <div>
              {harnessActivities && harnessActivities.length > 0 && (
                <HarnessActivityStatus activities={harnessActivities} />
              )}
              {runtimeTools && runtimeTools.length > 0 && (
                <RuntimeToolStatus tools={runtimeTools} />
              )}
              <div className="chat-prose">
                {isFailed ? (
                  <div className="flex flex-col items-start gap-2 rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-xs text-destructive">
                    <div className="flex items-start gap-2">
                      <AlertCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                      <span>
                        {message.error_message ||
                          "Chat response failed before an answer was saved."}
                      </span>
                    </div>
                    {onRetry && (
                      <button
                        type="button"
                        onClick={onRetry}
                        className="inline-flex items-center gap-1.5 rounded-md border border-destructive/30 px-2 py-1 font-medium hover:bg-destructive/10"
                      >
                        <RotateCcw className="h-3 w-3" />
                        Retry
                      </button>
                    )}
                  </div>
                ) : message.content ? (
                  <MarkdownContent content={message.content} />
                ) : isStreaming ? (
                  <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
                    <span className="inline-block h-3.5 w-3.5 rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground animate-spin" />
                    {latestStep?.label ?? (hasSources ? "Thinking..." : "Searching documents...")}
                  </span>
                ) : null}
                {isStreaming && message.content && (
                  <span className="inline-block w-0.5 h-4 bg-foreground/70 animate-cursor-blink ml-0.5 align-text-bottom" />
                )}
              </div>

              {/* Collapsible sources */}
              {sourceCount > 0 && !isStreaming && (
                <div className="pt-2">
                  <button
                    onClick={() => setSourcesExpanded(!sourcesExpanded)}
                    className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                  >
                    {sourcesExpanded ? (
                      <ChevronDown className="h-3 w-3" />
                    ) : (
                      <ChevronRight className="h-3 w-3" />
                    )}
                    {sourceCount} {sourceCount === 1 ? "source" : "sources"}
                  </button>
                  {sourcesExpanded && (
                    <div className="mt-1.5 space-y-1">
                      {message.sources!.map((source, index) => (
                        <SourceCitation
                          key={source.document_id}
                          source={source}
                          index={index}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Action buttons (assistant only) */}
              {!isStreaming && message.content && (
                <div
                  className={cn(
                    "flex items-center gap-1 pt-1.5 transition-opacity",
                    isMobile ? "opacity-100" : "opacity-0 group-hover/msg:opacity-100",
                  )}
                >
                  <button
                    onClick={handleCopy}
                    className={cn(
                      "rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors",
                      isMobile
                        ? "min-h-[40px] min-w-[40px] p-2"
                        : "p-1",
                    )}
                    aria-label="Copy message"
                    title="Copy"
                  >
                    {copied ? (
                      <Check className="h-4 w-4 sm:h-3.5 sm:w-3.5" />
                    ) : (
                      <Copy className="h-4 w-4 sm:h-3.5 sm:w-3.5" />
                    )}
                  </button>
                  <button
                    onClick={() => handleFeedback("up")}
                    disabled={!canSubmitFeedback}
                    className={cn(
                      "rounded-md transition-colors",
                      isMobile
                        ? "min-h-[40px] min-w-[40px] p-2"
                        : "p-1",
                      feedback === "up"
                        ? "text-primary bg-primary/10"
                        : "text-muted-foreground hover:text-foreground hover:bg-muted",
                      !canSubmitFeedback && "opacity-50 cursor-not-allowed",
                    )}
                    aria-label="Good response"
                    title="Good response"
                  >
                    <ThumbsUp className="h-4 w-4 sm:h-3.5 sm:w-3.5" />
                  </button>
                  <button
                    onClick={() => handleFeedback("down")}
                    disabled={!canSubmitFeedback}
                    className={cn(
                      "rounded-md transition-colors",
                      isMobile
                        ? "min-h-[40px] min-w-[40px] p-2"
                        : "p-1",
                      feedback === "down"
                        ? "text-destructive bg-destructive/10"
                        : "text-muted-foreground hover:text-foreground hover:bg-muted",
                      !canSubmitFeedback && "opacity-50 cursor-not-allowed",
                    )}
                    aria-label="Bad response"
                    title="Bad response"
                  >
                    <ThumbsDown className="h-4 w-4 sm:h-3.5 sm:w-3.5" />
                  </button>
                </div>
              )}

              {/* Usage stats */}
              {(message.latency_ms || usage) && !isStreaming && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground pt-1">
                  {message.latency_ms && <span>{message.latency_ms}ms</span>}
                  {usage && usage.input_tokens > 0 && (
                    <span className="bg-primary/10 text-primary px-1.5 py-0.5 rounded">
                      {usage.input_tokens + usage.output_tokens} tokens
                    </span>
                  )}
                  {usage && usage.cost_usd > 0 && (
                    <span className="bg-green-50 text-green-700 px-1.5 py-0.5 rounded">
                      ${usage.cost_usd.toFixed(4)}
                    </span>
                  )}
                  {usage && usage.model && (
                    <span
                      className="bg-slate-100 text-slate-700 px-1.5 py-0.5 rounded font-mono"
                      title={usage.provider ? `${usage.provider}/${usage.model}` : usage.model}
                    >
                      {usage.model}
                    </span>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      {/* Lightbox overlay */}
      {lightboxImage && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
          onClick={closeLightbox}
        >
          <button
            onClick={closeLightbox}
            className="absolute top-4 right-4 text-white/80 hover:text-white"
          >
            <X className="h-6 w-6" />
          </button>
          <img
            src={`data:image/jpeg;base64,${lightboxImage}`}
            alt="Full size"
            className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
}
