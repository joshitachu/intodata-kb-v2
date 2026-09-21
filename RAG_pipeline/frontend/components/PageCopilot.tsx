"use client";

import { useMemo } from "react";
import {
  ExternalLink,
  Loader2,
  MessageSquare,
  PanelRightClose,
  PanelRightOpen,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { answerRevealSource } from "@/lib/answerReveal";
import { useProgressiveReveal } from "@/hooks/useProgressiveReveal";
import { ChatInput } from "@/components/ChatInput";
import { MessageList } from "@/components/MessageList";
import { HarnessActivityStatus } from "@/components/HarnessActivityStatus";
import { ResizeHandle } from "@/components/copilot/ResizeHandle";
import { ScopeChip } from "@/components/copilot/ScopeChip";
import { CopilotNotices } from "@/components/copilot/CopilotNotices";
import { TargetChips } from "@/components/copilot/TargetChips";
import { AnswerSourceNote } from "@/components/copilot/AnswerSourceNote";
import { readEvidenceSummary } from "@/components/copilot/EvidenceSummaryNote";
import type {
  CopilotAnswerSource,
  CopilotNotice,
  CopilotScope,
  CopilotTarget,
} from "@/types/copilot";
import type { HarnessActivityEvent, MessageResponse } from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";

export { copilotNotice } from "@/components/copilot/CopilotNotices";

/**
 * The assistant docked beside the page it talks about.
 *
 * Page-agnostic on purpose: every domain fact arrives through a prop, so the
 * same panel mounts on the topology canvas, a document page or a runtime view
 * without change. It imports no graph component and no landscape type -- the
 * host resolves domain objects into `CopilotTarget`s (see
 * `lib/topologyTargets.ts` for the topology surface) before handing them over.
 *
 * Chat rendering is delegated to the existing `MessageList` / `MessageBubble` /
 * `ChatInput` / `HarnessActivityStatus` components rather than reimplemented,
 * so streaming, retry, citations and activity disclosure behave identically to
 * the full-page chat.
 *
 * The answer's progressive reveal is owned here (`useProgressiveReveal`), which
 * keeps its per-frame re-renders inside this panel: the canvas beside it is a
 * sibling and never re-renders for the animation. The reveal is presentation
 * only -- `targets`, `answerSource` and the host's auto-navigation all still
 * fire off the stream lifecycle, so nothing waits for the text to finish.
 */
export interface PageCopilotProps {
  /** What the user is looking at, rendered as the indicator chip. */
  scope: CopilotScope;
  /** Panel heading. Defaults to "Assistant". */
  title?: string;

  /** Conversation state, owned by the host. */
  messages: readonly MessageResponse[];
  conversationId?: number | null;
  streamState?: StreamState | null;
  /** The just-sent question, before the server echoes it back. */
  pendingUserMessage?: string | null;
  isSending: boolean;
  onSend: (query: string) => void;
  onRetry?: () => void;
  /** Promote the embedded thread into the ordinary chat list and open it. */
  onContinueInChat?: () => void;
  isContinuingInChat?: boolean;
  /** Host-level failure that is not part of the stream (e.g. send rejected). */
  errorMessage?: string | null;

  /** Tool-progress disclosure for the turn in progress. */
  activities?: readonly HarnessActivityEvent[];

  /** Starter prompts for the idle state. */
  suggestions?: readonly string[];
  onSuggestionClick?: (suggestion: string) => void;

  /** Clickable places the finished answer refers to. */
  targets?: readonly CopilotTarget[];
  /**
   * Targets discovered from tool events while the answer is still streaming.
   * Shown as provisional; the host highlights them on the page as they arrive
   * (see `useProgressiveTargets`).
   */
  liveTargets?: readonly CopilotTarget[];
  /** Hover re-highlights exactly one target; null on leave. */
  onTargetHover?: (target: CopilotTarget | null) => void;
  /**
   * Click-through for a chip. The panel never navigates on its own; the host
   * may, but only for a target the user's question explicitly asked to be
   * taken to (see the `navigate` intent in `types/topology.ts`).
   */
  onTargetClick?: (target: CopilotTarget) => void;

  /**
   * What backed the finished answer. Rendered in the footer so this panel's
   * page-flavoured chrome cannot imply the page's artifact vouched for an
   * answer that came from somewhere else.
   */
  answerSource?: CopilotAnswerSource | null;

  /** Degraded conditions, rendered above the thread. */
  notices?: readonly CopilotNotice[];

  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  /**
   * Width of the expanded column in pixels. When given together with
   * `onWidthChange` the panel grows a draggable left edge; when omitted it is a
   * fixed ~360px column, which is what a host that does not care about width
   * gets for free.
   */
  width?: number;
  onWidthChange?: (width: number) => void;
  /** Restore the default width. Bound to double-click and Home on the handle. */
  onWidthReset?: () => void;
  className?: string;
  placeholder?: string;
}

const DEFAULT_WIDTH = 360;

export function PageCopilot({
  scope,
  title = "Assistant",
  messages,
  conversationId,
  streamState,
  pendingUserMessage,
  isSending,
  onSend,
  onRetry,
  onContinueInChat,
  isContinuingInChat = false,
  errorMessage,
  activities,
  suggestions,
  onSuggestionClick,
  targets,
  liveTargets,
  onTargetHover,
  onTargetClick,
  answerSource,
  notices,
  collapsed,
  onCollapsedChange,
  width,
  onWidthChange,
  onWidthReset,
  className,
  placeholder = "Ask about what you see...",
}: PageCopilotProps) {
  const isStreaming = Boolean(streamState?.isStreaming);
  const isBusy = isSending || isStreaming;
  const latestAssistantHasEvidence = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message.role === "assistant") {
        return Boolean(readEvidenceSummary(message.structured_answer));
      }
    }
    return false;
  }, [messages]);

  // Which answer is being paced: the live buffer, or the persisted message it
  // becomes once the host refetches the thread.
  const revealSource = useMemo(
    () => answerRevealSource({ streamState, messages }),
    [streamState, messages],
  );
  const reveal = useProgressiveReveal({
    text: revealSource?.text ?? "",
    turnKey: revealSource?.key ?? null,
    isLive: revealSource?.isLive ?? false,
  });
  const revealedAnswer = revealSource
    ? { key: revealSource.key, text: reveal.text, isRevealing: reveal.isRevealing }
    : null;

  // A user action must never leave text mid-flight. Asking a new question and
  // jumping to a target are both "I am done reading this", so the previous
  // answer finishes instantly rather than animating under the new state.
  const handleSend = (query: string) => {
    reveal.complete();
    onSend(query);
  };
  const handleTargetClick = onTargetClick
    ? (target: CopilotTarget) => {
        reveal.complete();
        onTargetClick(target);
      }
    : undefined;
  // The thread takes over as soon as there is anything to show; the idle state
  // is only for a genuinely empty panel.
  const hasThread =
    messages.length > 0 || Boolean(pendingUserMessage) || Boolean(streamState);
  const scopeChip = (
    <ScopeChip parts={[scope.kindLabel, scope.name]} icon={Sparkles} label="Current scope" />
  );

  if (collapsed) {
    return (
      <aside
        aria-label={title}
        className={cn("flex h-full shrink-0 flex-col border-l bg-card", className)}
      >
        {conversationId && onContinueInChat && (
          <button
            type="button"
            onClick={onContinueInChat}
            disabled={isBusy || isContinuingInChat}
            aria-label="Continue this thread in main chat"
            title="Continue in main chat"
            className="flex w-11 items-center justify-center py-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isContinuingInChat ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <ExternalLink className="h-4 w-4" aria-hidden />
            )}
          </button>
        )}
        <button
          type="button"
          onClick={() => onCollapsedChange(false)}
          aria-expanded={false}
          aria-label={`Expand ${title}`}
          className="flex h-full w-11 flex-col items-center gap-3 py-3 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <PanelRightOpen className="h-4 w-4 shrink-0" aria-hidden />
          <span className="mt-1 text-[11px] font-medium [writing-mode:vertical-rl]">{title}</span>
          {isBusy && (
            <span
              className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-primary"
              aria-hidden
            />
          )}
        </button>
      </aside>
    );
  }

  // The handle is offered only when the host owns a width to change. A panel
  // rendered at a fixed width must not show a drag affordance that does nothing.
  const resizable = width !== undefined && onWidthChange !== undefined;

  return (
    <>
      {resizable && (
        <ResizeHandle
          width={width}
          onWidthChange={onWidthChange}
          onReset={onWidthReset ?? (() => onWidthChange(DEFAULT_WIDTH))}
          label={`Resize ${title}`}
        />
      )}
      <aside
        aria-label={title}
        // Inline width, not a Tailwind class: the value is continuous and
        // user-chosen, so there is no class to name it.
        style={{ width: width ?? DEFAULT_WIDTH }}
        className={cn(
          "flex h-full shrink-0 flex-col overflow-hidden border-l bg-card",
          className,
        )}
      >
      <header className="flex items-start gap-2 border-b px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-sm font-semibold">
            <MessageSquare className="h-3.5 w-3.5 text-primary/70" aria-hidden />
            <span className="truncate">{title}</span>
          </div>
          <div className="mt-1">{scopeChip}</div>
        </div>
        {conversationId && onContinueInChat && (
          <button
            type="button"
            onClick={onContinueInChat}
            disabled={isBusy || isContinuingInChat}
            aria-label="Continue this thread in main chat"
            title="Continue in main chat"
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isContinuingInChat ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <ExternalLink className="h-4 w-4" aria-hidden />
            )}
            <span>Continue in chat</span>
          </button>
        )}
        <button
          type="button"
          onClick={() => onCollapsedChange(true)}
          aria-expanded
          aria-label={`Collapse ${title}`}
          className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <PanelRightClose className="h-4 w-4" aria-hidden />
        </button>
      </header>

      <CopilotNotices notices={notices ?? []} />

      {errorMessage && (
        <div
          role="alert"
          className="mx-3 mt-2 rounded-md border border-destructive/50 bg-destructive/10 px-2.5 py-2 text-[11px] text-destructive"
        >
          <p>{errorMessage}</p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-1 rounded-md border border-destructive/30 px-2 py-0.5 font-medium hover:bg-destructive/10"
            >
              Retry
            </button>
          )}
        </div>
      )}

      {hasThread ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <MessageList
            messages={messages as MessageResponse[]}
            conversationId={conversationId ?? null}
            streamingMessage={streamState ?? null}
            pendingUserMessage={pendingUserMessage}
            onRetryMessage={onRetry}
            revealedAnswer={revealedAnswer}
          />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-3 py-3">
          <p className="text-xs leading-snug text-muted-foreground">
            Ask about what is on screen. Answers point back at it, so you can jump straight to
            the part being described.
          </p>
          {suggestions && suggestions.length > 0 && (
            <div className="flex flex-col gap-1.5" aria-label="Suggested prompts">
              {suggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => onSuggestionClick?.(suggestion)}
                  className="rounded-md border bg-background px-2.5 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:bg-muted/60 hover:text-foreground"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {isBusy && activities && activities.length > 0 && (
        <div className="border-t px-3 pt-2">
          <HarnessActivityStatus activities={activities as HarnessActivityEvent[]} />
        </div>
      )}

      {/* Attribution before chips: it qualifies the answer, and an answer that
          did NOT come from this page has no chips to sit above anyway. */}
      {!isBusy && answerSource && !latestAssistantHasEvidence && (
        <AnswerSourceNote source={answerSource} />
      )}

      {!isBusy && targets && targets.length > 0 && (
        <TargetChips
          targets={targets}
          heading="On this page"
          compact
          onTargetHover={onTargetHover}
          onTargetClick={handleTargetClick}
        />
      )}

      <div className="border-t">
        <ChatInput
          onSend={handleSend}
          isLoading={isBusy}
          hideImagePicker
          placeholder={placeholder}
        />
      </div>
      </aside>
    </>
  );
}
