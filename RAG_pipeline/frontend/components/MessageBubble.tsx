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
  Network,
  RotateCcw,
  X,
  ExternalLink,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { SourceCitation } from "./SourceCitation";
import { MarkdownContent } from "./MarkdownContent";
import { HarnessActivityStatus } from "./HarnessActivityStatus";
import { HarnessPendingAction } from "./HarnessPendingAction";
import { WorkflowCard } from "./WorkflowCard";
import { PageScopeChip } from "./copilot/PageScopeChip";
import { TopologyTargetLinks } from "./copilot/TopologyTargetLinks";
import { AssistantProgress } from "./copilot/AssistantProgress";
import {
  EvidenceSummaryNote,
  readEvidenceSummary,
} from "./copilot/EvidenceSummaryNote";
import { readTopologyTargetsPayload } from "@/lib/topologyCopilot";
import type { StreamProgress } from "@/lib/agentProgress";
import { submitFeedback } from "@/lib/feedback";
import { useViewport } from "@/hooks/useViewport";
import type {
  HarnessActivityEvent,
  HarnessPendingAction as HarnessPendingActionValue,
  MessageResponse,
  PublicAgentEvent,
  TypedAgentResponse,
  UsageStats,
} from "@/types";

function isTypedAgentResponse(
  value: MessageResponse["structured_answer"],
): value is TypedAgentResponse {
  return Boolean(value && "response_type" in value && value.response_type === "agent_response_v1");
}

/**
 * Marks an answer that ran with the raw scan in the agent's tool menu.
 *
 * Only Deep Dive turns are marked. A matching "Chat" badge on every other
 * answer would be noise on the overwhelming majority of a thread and would say
 * nothing the absence of this one does not already say.
 */
function DeepDiveBadge() {
  return (
    <Badge
      variant="muted"
      data-testid="deep-dive-badge"
      aria-label="Answered in Deep Dive"
      title="This answer ran in Deep Dive: the raw scan was in the agent's tool menu."
    >
      <Network className="h-3 w-3 shrink-0" aria-hidden />
      Deep Dive
    </Badge>
  );
}

export function currentActivityLabel(args: {
  harnessActivities?: HarnessActivityEvent[];
  hasSources?: boolean;
}): string {
  const { hasSources } = args;
  return hasSources ? "Thinking..." : "Searching...";
}

interface MessageBubbleProps {
  message: MessageResponse;
  conversationId?: number | null;
  isStreaming?: boolean;
  streamingStatus?: string | null;
  hasSources?: boolean;
  usage?: UsageStats | null;
  harnessActivities?: HarnessActivityEvent[];
  pendingAction?: HarnessPendingActionValue | null;
  workflowCards?: MessageResponse["workflow_cards"];
  /**
   * Render the answer's topology targets as links to `/topology`. Off by
   * default: the docked copilot renders the same targets as navigable chips
   * against its own canvas, and showing both would be two ways to do one thing.
   */
  showTopologyLinks?: boolean;
  /** Project whose topology the links open, when the message names none. */
  topologyProjectId?: string | null;
  /**
   * Progressive reveal override for the answer text: the prefix of the answer
   * cleared for display so far (`hooks/useProgressiveReveal`). Presentation
   * only -- everything else on this bubble (sources, actions, latency) renders
   * off the message's real state, so nothing a finished turn earned is held
   * back behind the animation.
   */
  revealedContent?: string;
  /** True while that prefix is still growing; keeps the caret blinking. */
  isRevealing?: boolean;
  /**
   * This answer ran in Deep Dive. Provenance only, and only ever set from an
   * observed fact -- never inferred from the answer's shape or its tool calls,
   * because a badge that is wrong on a reloaded thread is worse than none.
   */
  ranInDeepDive?: boolean;
  /** Live phase for the waiting state, before any answer text exists. */
  streamProgress?: StreamProgress | null;
  /** The turn's agent events, summarised in the waiting state. */
  streamEvents?: readonly PublicAgentEvent[];
  onRetry?: () => void;
}

export function MessageBubble({
  message,
  conversationId,
  isStreaming,
  streamingStatus,
  hasSources,
  usage,
  harnessActivities,
  pendingAction,
  workflowCards,
  showTopologyLinks,
  topologyProjectId,
  revealedContent,
  isRevealing,
  ranInDeepDive,
  streamProgress,
  streamEvents,
  onRetry,
}: MessageBubbleProps) {
  const isUser = message.role === "user";
  // The mode is a property of the turn, but only the answer is worth marking:
  // the badge says "this is what produced that", and repeating it on the
  // question would double every Deep Dive turn's chrome for no extra fact.
  const showDeepDiveBadge = Boolean(ranInDeepDive) && !isUser;
  const typedResponse = isTypedAgentResponse(message.structured_answer)
    ? message.structured_answer
    : null;
  const evidenceSummary = readEvidenceSummary(message.structured_answer);
  // The reveal, when the host is pacing this bubble, is authoritative over both
  // text sources -- otherwise a typed response landing mid-reveal would paint
  // the whole answer at once, which is the block this exists to break up.
  const answerText =
    revealedContent !== undefined ? revealedContent : typedResponse?.content ?? message.content;
  const authoritativeCards = workflowCards ?? message.workflow_cards ?? typedResponse?.workflow_cards ?? [];
  const hasStructuredAnswer = Boolean(message.structured_answer);
  const transportFailed = !isUser && message.status === "failed";
  const showGenericFailure = transportFailed && !hasStructuredAnswer;
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);

  // message.id < 1 marks optimistic / streaming messages that haven't been
  // persisted yet — feedback is disabled until the server assigns a real id.
  const canSubmitFeedback = !isUser && !transportFailed && message.id > 0;

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

  const closeLightbox = useCallback(() => setLightboxImage(null), []);
  const { isMobile } = useViewport();

  function handleCopy() {
    const copyText = message.content || typedResponse?.content || "";
    navigator.clipboard.writeText(copyText);
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
          {/* Provenance for this turn: where the question was asked, and --
              on assistant turns only -- whether it ran in Deep Dive. Both are
              absent far more often than present, so the row disappears rather
              than reserving space. */}
          {(message.page_scope || showDeepDiveBadge) && (
            <div className="mb-1 flex flex-wrap items-center gap-1.5">
              {message.page_scope && <PageScopeChip scope={message.page_scope} />}
              {showDeepDiveBadge && <DeepDiveBadge />}
            </div>
          )}
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
              {pendingAction && (
                <HarnessPendingAction action={pendingAction} />
              )}
              <div className="chat-prose">
                {showGenericFailure ? (
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
                ) : answerText ? (
                  <MarkdownContent content={answerText} />
                ) : isStreaming ? (
                  <AssistantProgress
                    progress={streamProgress ?? null}
                    events={streamEvents}
                    fallbackLabel={streamingStatus || currentActivityLabel({ hasSources })}
                  />
                ) : null}
                {(isStreaming || isRevealing) && answerText && (
                  <span className="inline-block w-0.5 h-4 bg-foreground/70 animate-cursor-blink ml-0.5 align-text-bottom" />
                )}
              </div>

              {authoritativeCards.length > 0 && (
                <div className="space-y-2 pt-2">
                  {authoritativeCards.map((card) => (
                    <WorkflowCard key={card.workflow_run_id} card={card} />
                  ))}
                </div>
              )}

              {typedResponse && typedResponse.artifact_links.length > 0 && (
                <div className="flex flex-wrap gap-2 pt-2" aria-label="Artifacts">
                  {typedResponse.artifact_links.map((artifact) => (
                    <a
                      key={`${artifact.kind}:${artifact.url}`}
                      href={artifact.url}
                      className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium text-primary hover:bg-muted"
                    >
                      {artifact.label}
                      <ExternalLink className="h-3 w-3" aria-hidden />
                    </a>
                  ))}
                </div>
              )}

              {evidenceSummary && <EvidenceSummaryNote summary={evidenceSummary} />}

              {showTopologyLinks && (
                <TopologyTargetLinks
                  payload={readTopologyTargetsPayload(message.structured_answer)}
                  // The scope the question was asked from names its own
                  // project; it is more specific than whatever the page has
                  // selected now, which may have moved on since.
                  projectId={message.page_scope?.project_id ?? topologyProjectId}
                  conversationId={conversationId}
                />
              )}

              {harnessActivities && harnessActivities.length > 0 && (
                <div className="pt-2">
                  <HarnessActivityStatus activities={harnessActivities} />
                </div>
              )}

              {/* Collapsible sources */}
              {sourceCount > 0 && !isStreaming && (
                <div className="pt-2">
                  <button
                    type="button"
                    onClick={() => setSourcesExpanded(!sourcesExpanded)}
                    className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                    aria-expanded={sourcesExpanded}
                    aria-label={`${sourcesExpanded ? "Hide" : "Show"} ${sourceCount} ${sourceCount === 1 ? "source" : "sources"}`}
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
              {!isStreaming && (message.content || hasStructuredAnswer) && (
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
