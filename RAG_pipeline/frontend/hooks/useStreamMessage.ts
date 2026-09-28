"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import {
  type ConversationMessageRequest,
  type PublicAgentEvent,
  type TypedAgentResponse,
  SseDoneSchema,
  TypedAgentResponseSchema,
  PublicAgentEventSchema,
} from "@/types";
import { parseAgentProgress, type StreamProgress } from "@/lib/agentProgress";
import {
  licenceRefusalFromBody,
  notifyLicenceRefused,
  type LicenceRefusalDetail,
} from "@/lib/licence";

/**
 * Ceiling on retained `agentEvents`. Tool/plan/run events number in the tens
 * per turn, so this is unreachable in practice; it exists so a pathological run
 * cannot grow an unbounded array that every downstream `useMemo` re-walks.
 */
const MAX_AGENT_EVENTS = 1000;

/**
 * Event types that are never retained in `agentEvents`.
 *
 * `assistant.delta` carries prose already accumulated into `content`.
 * `assistant.progress` is a throttled liveness heartbeat emitted throughout a
 * long generation and carries no answer content at all. For both, only the
 * latest value matters (`content` / `progress` respectively), and there are
 * enough of them per turn that retaining them would make every derivation over
 * `agentEvents` grow with the turn's duration.
 */
const EPHEMERAL_EVENT_TYPES: ReadonlySet<string> = new Set([
  "assistant.delta",
  "assistant.progress",
  // Retracts provisional prose. Its effect is applied to `content` immediately;
  // the event itself describes a transition, not a fact about the finished turn,
  // so it is worth nothing to a host replaying `agentEvents`.
  "assistant.draft_reset",
  // Reasoning deltas, for the same reason `assistant.delta` is ephemeral: they
  // arrive token by token and are already accumulated into `reasoning`.
  "assistant.reasoning",
]);

/**
 * Frame types allowed to move the turn's *displayed* state -- the answer buffer,
 * the collapsed `progress` line and the streaming flag.
 *
 * This is the set the dispatcher used to gate on entirely: a frame whose type
 * was not listed here was parsed by nothing and discarded, sequence number and
 * all. Everything that validates as a `PublicAgentEvent` is now de-duplicated,
 * sequenced and retained regardless of type; this predicate only decides
 * whether the frame may also relabel the turn. An event the client does not
 * understand is not a statement about the phase, so it may not.
 */
function isDisplayEventType(eventType: string): boolean {
  return (
    eventType === "assistant.delta" ||
    eventType === "assistant.draft_reset" ||
    eventType === "assistant.progress" ||
    eventType === "runtime.update" ||
    eventType === "assistant.completed" ||
    eventType.startsWith("tool.") ||
    eventType.startsWith("workflow.") ||
    eventType.startsWith("plan.") ||
    eventType.startsWith("run.")
  );
}

/** Terminal frames: the turn is over and nothing more will arrive. */
const TERMINAL_EVENT_TYPES: ReadonlySet<string> = new Set([
  "run.completed",
  "run.failed",
  "run.interrupted",
]);

export interface StreamState {
  isStreaming: boolean;
  content: string;
  error: string | null;
  messageId: number | null;
  latencyMs: number | null;
  typedResponse?: TypedAgentResponse | null;
  runId?: string | null;
  progress?: string | null;
  /**
   * The latest `assistant.progress` heartbeat, kept whole rather than collapsed
   * into the `progress` one-liner.
   *
   * Before any answer text exists this and the `tool.*` events are the only
   * honest live signals there are, so the waiting state renders from it (see
   * `components/copilot/AssistantProgress.tsx`). Replaced, never accumulated:
   * heartbeats fire throughout a long generation and only the latest is a fact
   * about now.
   */
  progressDetail?: StreamProgress | null;
  lastSequence?: number;
  /**
   * The turn's validated public agent events, oldest first, so hosts can react
   * to tool results mid-stream instead of waiting for the finished answer (see
   * `components/copilot/useProgressiveTargets.ts`). `progress` remains the
   * collapsed one-line summary and is unaffected.
   *
   * `assistant.delta` is deliberately NOT retained: its only payload is prose
   * already accumulated into `content`, and there are thousands of deltas per
   * turn -- keeping them would make every derivation over this array quadratic
   * in the answer's length. Everything else that survives
   * `PublicAgentEventSchema` is kept verbatim; the schema is not widened here.
   *
   * Reset per turn alongside the rest of the state, and de-duplicated by
   * `event_id` (a resumed stream replays events it already delivered).
   *
   * Optional like the other additive fields on this state, because hosts and
   * tests construct `StreamState` literals directly; treat an absent value as
   * an empty list.
   */
  agentEvents?: readonly PublicAgentEvent[];
  /**
   * Which delta stream `content` currently holds.
   *
   * The trusted RAG answer is streamed live while it is generated
   * (`replayable: false`, provisional) and then emitted again after persistence
   * (`replayable: true`, authoritative). Appending both would duplicate the
   * answer, so the first authoritative delta RESETS `content` and the
   * authoritative run rebuilds it.
   *
   * This is a safety property, not just de-duplication: whatever the server
   * persisted always wins on screen, so if the live and stored text ever
   * diverged the display would self-correct rather than keep the provisional text.
   */
  deltaSource?: "live" | "authoritative" | null;
  /**
   * Accumulated `assistant.reasoning` text for the turn, oldest delta first.
   *
   * Kept strictly apart from `content`: reasoning is the model narrating to
   * itself, it is not the answer, and it is not validated or persisted. Hosts
   * that render it must keep it out of the answer body (`/labs/chat` shows it
   * in a collapsed panel). Lanes that never emit the event leave this empty,
   * which is why nothing downstream may treat an empty value as a failure.
   *
   * Optional like the other additive fields on this state, because hosts and
   * tests construct `StreamState` literals directly; treat an absent value as
   * an empty string.
   */
  reasoning?: string;
  /**
   * Set when the turn was refused by the licence (HTTP 402) before the stream
   * opened. `error` carries the same sentence so every host that already
   * reacts to a failed turn (clearing the optimistic message, re-reading the
   * thread) keeps doing so; hosts that know this field render it as a notice
   * instead of a broken turn with a retry, because retrying cannot succeed.
   *
   * Optional like the other additive fields on this state.
   */
  licenceRefusal?: LicenceRefusalDetail | null;
}

const INITIAL_STATE: StreamState = {
  isStreaming: false,
  content: "",
  error: null,
  messageId: null,
  latencyMs: null,
  typedResponse: null,
  runId: null,
  progress: null,
  progressDetail: null,
  lastSequence: 0,
  agentEvents: [],
  deltaSource: null,
  reasoning: "",
  licenceRefusal: null,
};

/**
 * `apiBase` selects the backend *lane* the turn is streamed from.
 *
 * Production is `/api` (the LangGraph harness) and is the default, so every
 * existing call site is byte-for-byte unchanged. `/api/labs` points the same
 * hook -- and therefore the same components, the same SSE parsing and the same
 * `StreamState` -- at the experimental OpenCode lane, which is the only way a
 * side-by-side comparison measures the agent rather than the renderer.
 */
export function useStreamMessage(
  conversationId: number | null,
  apiBase: string = "/api",
) {
  const [streamState, setStreamState] = useState<StreamState>(INITIAL_STATE);
  const abortRef = useRef<AbortController | null>(null);
  const lastEventIdRef = useRef<string | null>(null);
  const lastSequenceRef = useRef(0);
  const clientTurnRef = useRef<string | null>(null);
  const seenEventIdsRef = useRef(new Set<string>());

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const reset = useCallback(() => {
    lastEventIdRef.current = null;
    lastSequenceRef.current = 0;
    clientTurnRef.current = null;
    seenEventIdsRef.current.clear();
    setStreamState(INITIAL_STATE);
  }, []);

  /**
   * Stops *reading* the turn in progress.
   *
   * Aborting the fetch ends the client half of the stream, and that is all it
   * ends: neither lane exposes a cancel endpoint, and the Next proxy does not
   * forward the abort signal upstream, so the run may well continue and persist
   * its answer on the server. Callers must label this honestly -- "stop showing
   * me this", not "cancel the run".
   *
   * Whatever text already arrived is kept; it is what the server sent. No error
   * is recorded either, because the user asking to stop is not a failure.
   *
   * Idempotent, and a no-op when nothing is in flight.
   */
  const cancelStream = useCallback(() => {
    const controller = abortRef.current;
    if (!controller || controller.signal.aborted) return;
    controller.abort();
    abortRef.current = null;
    setStreamState((prev) =>
      prev.isStreaming ? { ...prev, isStreaming: false } : prev,
    );
  }, []);

  const sendMessage = useCallback(
    async (request: ConversationMessageRequest, overrideConversationId?: number) => {
      const targetId = overrideConversationId ?? conversationId;
      if (targetId === null) return;

      // Abort any in-flight stream
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      const sameTurn = Boolean(
        request.client_turn_id && request.client_turn_id === clientTurnRef.current,
      );
      if (!sameTurn) {
        lastEventIdRef.current = null;
        lastSequenceRef.current = 0;
        seenEventIdsRef.current.clear();
      }
      clientTurnRef.current = request.client_turn_id ?? null;

      setStreamState((prev) => ({
        isStreaming: true,
        content: sameTurn ? prev.content : "",
        error: null,
        messageId: sameTurn ? prev.messageId : null,
        latencyMs: sameTurn ? prev.latencyMs : null,
        typedResponse: sameTurn ? prev.typedResponse : null,
        runId: sameTurn ? prev.runId : null,
        progress: sameTurn ? prev.progress : null,
        progressDetail: sameTurn ? prev.progressDetail : null,
        deltaSource: sameTurn ? prev.deltaSource : null,
        reasoning: sameTurn ? prev.reasoning : "",
        licenceRefusal: null,
        lastSequence: lastSequenceRef.current,
        // A resumed turn keeps the events already delivered (the `seenEventIds`
        // set is preserved too, so the replay cannot double-append them).
        agentEvents: sameTurn ? prev.agentEvents : [],
      }));

      try {
        const res = await fetch(
          `${apiBase}/conversations/${targetId}/messages/stream?after_sequence=${lastSequenceRef.current}`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...(lastEventIdRef.current
                ? { "Last-Event-ID": lastEventIdRef.current }
                : {}),
            },
            body: JSON.stringify(request),
            signal: controller.signal,
          },
        );

        if (!res.ok) {
          const text = await res.text();
          let body: unknown = null;
          let message = "Failed to send message";
          try {
            body = JSON.parse(text);
            const candidate = (body as { error?: unknown } | null)?.error;
            if (typeof candidate === "string" && candidate.trim()) message = candidate;
          } catch {
            // ignore parse error
          }
          // A licence refusal is a verdict about the instance, not a failed
          // turn and never a logout: surface its sentence and tell the licence
          // banner to re-read the status.
          const refusal = licenceRefusalFromBody(res.status, body);
          if (refusal) notifyLicenceRefused();
          setStreamState((prev) => ({
            ...prev,
            isStreaming: false,
            error: refusal ? refusal.message : message,
            licenceRefusal: refusal ? refusal.detail : null,
          }));
          return;
        }

        const reader = res.body?.getReader();
        if (!reader) {
          setStreamState((prev) => ({
            ...prev,
            isStreaming: false,
            error: "No response body",
          }));
          return;
        }

        const decoder = new TextDecoder();
        let buffer = "";
        let currentEvent = "";
        let currentData: string[] = [];

        const dispatchEvent = (eventType: string, data: string) => {
          // Everything that is not one of the three transport frames below is a
          // public agent event. This used to be a whitelist of known types, so a
          // type the backend added -- `assistant.reasoning` was the first --
          // reached no parser at all and vanished with its sequence number.
          // Unrecognised frames now degrade instead: validated, de-duplicated
          // and retained like any other, they simply do not move the displayed
          // state (see `isDisplayEventType`). A frame that fails validation is
          // still ignored, which is what keeps a malformed one from breaking the
          // stream.
          if (
            eventType !== "agent_response" &&
            eventType !== "error" &&
            eventType !== "done"
          ) {
            try {
              const event = PublicAgentEventSchema.parse(JSON.parse(data));
              if (seenEventIdsRef.current.has(event.event_id)) return;
              seenEventIdsRef.current.add(event.event_id);
              lastEventIdRef.current = event.event_id;
              lastSequenceRef.current = Math.max(
                lastSequenceRef.current,
                event.sequence,
              );
              const payload = event.public_payload;
              const retainEvent =
                !EPHEMERAL_EVENT_TYPES.has(eventType) &&
                !EPHEMERAL_EVENT_TYPES.has(event.type);
              const isDelta = eventType === "assistant.delta";
              const isReset = eventType === "assistant.draft_reset";
              // The SSE frame name and the event's own `type` are the same
              // string in every emitter; both are checked for the two decisions
              // that are new here, so a frame is handled on either spelling.
              const isDisplayEvent =
                isDisplayEventType(eventType) || isDisplayEventType(event.type);
              const isReasoning =
                eventType === "assistant.reasoning" ||
                event.type === "assistant.reasoning";
              setStreamState((prev) => {
                // The server retracting a draft it already showed: the model
                // narrated before calling a tool, or the grounding validator
                // rejected the draft. Only PROVISIONAL text is ever dropped --
                // an authoritative buffer is what the server persisted, so it
                // outranks a retraction, which by then can only be a
                // late-arriving replay frame.
                const dropsDraft = isReset && prev.deltaSource === "live";
                return {
                  ...prev,
                  runId: event.run_id,
                  lastSequence: lastSequenceRef.current,
                  agentEvents:
                    retainEvent && (prev.agentEvents?.length ?? 0) < MAX_AGENT_EVENTS
                      ? [...(prev.agentEvents ?? []), event]
                      : prev.agentEvents,
                  deltaSource: isDelta
                    ? event.replayable
                      ? "authoritative"
                      : "live"
                    : dropsDraft
                      ? null
                      : prev.deltaSource,
                  content: dropsDraft
                    ? ""
                    : !isDelta
                      ? prev.content
                      : event.replayable && prev.deltaSource !== "authoritative"
                        ? String(payload.content ?? "")
                        : prev.content + String(payload.content ?? ""),
                  // Reasoning is the model narrating to itself: accumulated
                  // here, never appended to `content`, and never allowed to
                  // stand in for an answer.
                  reasoning: isReasoning
                    ? (prev.reasoning ?? "") + String(payload.content ?? "")
                    : prev.reasoning,
                  // Neither answer prose nor its retraction is a progress
                  // statement; both would overwrite the status line with
                  // something that is not a phase. Nor is a frame this client
                  // does not recognise -- labelling the turn with a raw type
                  // name would be the client inventing a phase.
                  progress:
                    !isDisplayEvent || isDelta || isReset
                      ? prev.progress
                      : String(payload.summary ?? payload.status ?? eventType),
                  // A heartbeat with nothing usable in it keeps the last known
                  // phase rather than blanking the waiting state.
                  progressDetail:
                    eventType === "assistant.progress"
                      ? parseAgentProgress(payload) ?? prev.progressDetail ?? null
                      : prev.progressDetail,
                  // Only a frame this client understands may end the turn --
                  // or, for every other display frame, confirm it is alive.
                  // An unrecognised type says nothing either way.
                  isStreaming: isDisplayEvent
                    ? !TERMINAL_EVENT_TYPES.has(eventType) &&
                      !TERMINAL_EVENT_TYPES.has(event.type)
                    : prev.isStreaming,
                };
              });
            } catch {
              // Ignore malformed or non-public events.
            }
          } else if (eventType === "agent_response") {
            try {
              const typedResponse = TypedAgentResponseSchema.parse(JSON.parse(data));
              setStreamState((prev) => ({
                ...prev,
                content: typedResponse.content,
                typedResponse,
              }));
            } catch {
              // Malformed typed responses never replace validated stream content.
            }
          } else if (eventType === "error") {
            let message = data;
            try {
              message = JSON.parse(data) as string;
            } catch {
              // Keep plain text SSE errors unchanged.
            }
            setStreamState((prev) => ({
              ...prev,
              isStreaming: false,
              error: message,
            }));
          } else if (eventType === "done") {
            try {
              const result = SseDoneSchema.parse(JSON.parse(data));
              if (result.message_id < 1) {
                setStreamState((prev) => ({
                  ...prev,
                  isStreaming: false,
                  error:
                    prev.error ??
                    "Chat stream ended before a response was saved.",
                }));
                return;
              }
              setStreamState((prev) => ({
                ...prev,
                isStreaming: false,
                messageId: result.message_id,
                latencyMs: result.latency_ms,
                runId: result.run_id ?? prev.runId,
              }));
            } catch {
              // ignore parse error
            }
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          // Process complete SSE lines
          const lines = buffer.split("\n");
          // Keep the last potentially incomplete line in the buffer
          buffer = lines.pop() ?? "";

          for (const rawLine of lines) {
            const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
            if (line.startsWith("event: ")) {
              currentEvent = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              currentData.push(line.slice(6));
            } else if (line === "") {
              if (currentEvent && currentData.length > 0) {
                dispatchEvent(currentEvent, currentData.join("\n"));
              }
              currentEvent = "";
              currentData = [];
            }
          }
        }

        if (currentEvent && currentData.length > 0) {
          dispatchEvent(currentEvent, currentData.join("\n"));
        }

        // If we get here without a done event, mark as not streaming
        setStreamState((prev) => {
          if (prev.isStreaming) {
            return { ...prev, isStreaming: false };
          }
          return prev;
        });
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          return; // Expected when aborting
        }
        setStreamState((prev) => ({
          ...prev,
          isStreaming: false,
          error: err instanceof Error ? err.message : "Stream failed",
        }));
      }
    },
    [apiBase, conversationId],
  );

  return { streamState, sendMessage, reset, cancelStream };
}
