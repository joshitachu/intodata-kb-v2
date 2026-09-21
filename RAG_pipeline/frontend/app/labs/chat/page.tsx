"use client";

import { Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { ChatProvider, useChatContext } from "@/contexts/ChatContext";
import { ChatWindow } from "@/components/ChatWindow";
import {
  LabsHealthSchema,
  describeLaneDetail,
  laneVerdict,
  type LabsHealth,
  type LaneTone,
} from "@/components/labs/labsHealth";
import { LabsTurnPanel } from "@/components/labs/LabsTurnPanel";
import { useLabsTurnRecord } from "@/components/labs/useLabsTurnRecord";
import { Badge } from "@/components/ui/badge";
import { fetchJson } from "@/lib/authedFetch";
import { cn } from "@/lib/utils";

/**
 * Experimental chat lane.
 *
 * The point of this page is a fair side-by-side against production chat, so it
 * mounts the *same* `ChatWindow` (and therefore the same `MessageList`,
 * `MessageBubble`, citations, harness cards and progressive reveal) and only
 * swaps the backend lane, via `ChatProvider apiBase`. Nothing is forked: if the
 * rendering differed, the comparison would measure the renderer instead of the
 * agent.
 *
 * What is NOT duplicated from `components/ChatLayout.tsx` is deliberate.
 * ChatLayout owns home-page-only concerns -- `?c=` URL synchronisation, the
 * mobile conversation list, the pack workbench aside, and a `router.replace("/?c=new")`
 * recovery path that would navigate straight off this page. Only its ~15 lines
 * of context-to-`ChatWindow` prop wiring are repeated below.
 */
const LABS_API_BASE = "/api/labs";

const DOT_CLASS_BY_TONE: Record<LaneTone, string> = {
  ok: "bg-green-500",
  down: "bg-red-500",
  unknown: "bg-amber-500",
};

async function fetchLabsHealth(): Promise<LabsHealth> {
  return LabsHealthSchema.parse(
    await fetchJson<unknown>(`${LABS_API_BASE}/health`, {
      cache: "no-store",
      fallbackError: "Labs lane health unavailable",
    }),
  );
}

function useLabsHealth() {
  return useQuery({
    queryKey: ["labs", "health"],
    queryFn: fetchLabsHealth,
    staleTime: 15 * 1000,
    refetchInterval: 30 * 1000,
  });
}

function LabsHealthIndicator() {
  const { data, isLoading, isError, error } = useLabsHealth();

  let tone: LaneTone = "unknown";
  let label = "Lane status unknown";

  if (isLoading) {
    label = "Checking lane...";
  } else if (isError) {
    tone = "down";
    label = error instanceof Error ? error.message : "Lane unreachable";
  } else if (data) {
    ({ tone, label } = laneVerdict(data));
  }

  // The probe body is worth keeping and is not worth a line of chrome, so it
  // goes where a reader looks for detail: the hover text, after the verdict.
  const detail = data ? describeLaneDetail(data.detail) : null;

  return (
    <span
      className="inline-flex min-w-0 items-center gap-1.5 text-xs"
      title={detail ? `${label} -- ${detail}` : label}
      aria-live="polite"
    >
      <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", DOT_CLASS_BY_TONE[tone])} />
      <span className="truncate">{label}</span>
    </span>
  );
}

function LabsBanner() {
  return (
    <div
      role="note"
      className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-amber-300 bg-amber-50 px-4 py-2 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200"
    >
      <span className="flex items-center gap-1.5 text-sm font-semibold">
        <FlaskConical className="h-4 w-4 shrink-0" aria-hidden />
        Labs chat
      </span>
      <Badge variant="warning">Experimental &mdash; not production</Badge>
      <span className="text-xs">
        Answers come from the OpenCode lane ({LABS_API_BASE}), not the production harness.
      </span>
      <span className="ml-auto">
        <LabsHealthIndicator />
      </span>
    </div>
  );
}

/**
 * The same prop wiring `ChatLayout` gives `ChatWindow`, against the labs
 * provider. Keep the two in step: a divergence here is a divergence in the
 * comparison.
 *
 * `LabsTurnPanel` is the one addition, and it is deliberately *outside*
 * `ChatWindow`: the answer body stays the unforked production renderer, so a
 * difference in the answer is still a difference in the agent. The panel adds
 * only what the lane reports about the turn -- reasoning, tool calls, usage --
 * none of which the production bubble shows.
 */
function LabsChatSurface() {
  const chat = useChatContext();
  const turn = useLabsTurnRecord(chat.streamState);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <LabsTurnPanel record={turn} onStop={chat.cancelStream} />
      <ChatWindow
        conversation={chat.activeConversation}
        isLoading={chat.isLoadingConversation}
        isSending={chat.streamState.isStreaming}
        onSendMessage={chat.handleSendMessage}
        streamingMessage={
          chat.streamState.isStreaming || chat.streamState.content || chat.streamState.error
            ? chat.streamState
            : null
        }
        onRetryMessage={chat.retryFailedMessage}
        onWorkflowChanged={chat.refreshActiveConversation}
        pendingUserMessage={chat.pendingUserMessage}
        mode={chat.mode}
        onModeChange={chat.setMode}
        selectedProject={chat.selectedProject}
        deepDiveState={chat.deepDiveState}
        onCancelDeepDive={chat.onCancelDeepDive}
      />
    </div>
  );
}

export default function LabsChatPage() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <LabsBanner />
      <div className="flex min-h-0 flex-1 flex-col">
        <Suspense fallback={null}>
          {/* Shadows the app-wide provider mounted in `components/Layout.tsx`;
              descendants resolve `useChatContext()` to this one, so the turn is
              streamed from the labs lane while everything else is unchanged. */}
          <ChatProvider apiBase={LABS_API_BASE}>
            <LabsChatSurface />
          </ChatProvider>
        </Suspense>
      </div>
    </div>
  );
}
