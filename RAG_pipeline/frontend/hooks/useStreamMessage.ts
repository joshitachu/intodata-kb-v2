"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { z } from "zod";
import {
  type ConversationMessageRequest,
  type DocumentSource,
  type AgentStep,
  type HarnessActivityEvent,
  type HarnessPendingAction,
  type UsageStats,
  type RuntimeToolProgress,
  type StructuredAnswer,
  AgentStepSchema,
  DocumentSourceSchema,
  HarnessActivityEventSchema,
  HarnessPendingActionSchema,
  UsageStatsSchema,
  SseDoneSchema,
  RuntimeToolCallEventSchema,
  RuntimeToolResultEventSchema,
  StructuredAnswerSchema,
} from "@/types";

export interface StreamState {
  isStreaming: boolean;
  content: string;
  sources: DocumentSource[];
  error: string | null;
  messageId: number | null;
  latencyMs: number | null;
  usage: UsageStats | null;
  steps: AgentStep[];
  harnessActivities: HarnessActivityEvent[];
  pendingAction: HarnessPendingAction | null;
  runtimeTools: RuntimeToolProgress[];
  structuredAnswer: StructuredAnswer | null;
}

const INITIAL_STATE: StreamState = {
  isStreaming: false,
  content: "",
  sources: [],
  error: null,
  messageId: null,
  latencyMs: null,
  usage: null,
  steps: [],
  harnessActivities: [],
  pendingAction: null,
  runtimeTools: [],
  structuredAnswer: null,
};

export function useStreamMessage(conversationId: number | null) {
  const [streamState, setStreamState] = useState<StreamState>(INITIAL_STATE);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const reset = useCallback(() => {
    setStreamState(INITIAL_STATE);
  }, []);

  const sendMessage = useCallback(
    async (request: ConversationMessageRequest, overrideConversationId?: number) => {
      const targetId = overrideConversationId ?? conversationId;
      if (targetId === null) return;

      // Abort any in-flight stream
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setStreamState({
        isStreaming: true,
        content: "",
        sources: [],
        error: null,
        messageId: null,
        latencyMs: null,
        usage: null,
        steps: [],
        harnessActivities: [],
        pendingAction: null,
        runtimeTools: [],
        structuredAnswer: null,
      });

      try {
        const res = await fetch(
          `/api/conversations/${targetId}/messages/stream`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(request),
            signal: controller.signal,
          },
        );

        if (!res.ok) {
          const text = await res.text();
          let message = "Failed to send message";
          try {
            message = JSON.parse(text).error || message;
          } catch {
            // ignore parse error
          }
          setStreamState((prev) => ({
            ...prev,
            isStreaming: false,
            error: message,
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
          if (eventType === "sources") {
            try {
              const sources = z.array(DocumentSourceSchema).parse(JSON.parse(data));
              setStreamState((prev) => ({ ...prev, sources }));
            } catch {
              // ignore parse error
            }
          } else if (eventType === "token") {
            try {
              const token = z.string().parse(JSON.parse(data));
              setStreamState((prev) => ({
                ...prev,
                content: prev.content + token,
              }));
            } catch {
              // ignore parse error
            }
          } else if (eventType === "usage") {
            try {
              const usage = UsageStatsSchema.parse(JSON.parse(data));
              setStreamState((prev) => ({ ...prev, usage }));
            } catch {
              // ignore parse error
            }
          } else if (eventType === "step") {
            try {
              const step = AgentStepSchema.parse(JSON.parse(data));
              setStreamState((prev) => {
                const existingIndex = prev.steps.findIndex((s) => s.id === step.id);
                if (existingIndex === -1) {
                  return { ...prev, steps: [...prev.steps, step] };
                }
                const nextSteps = [...prev.steps];
                nextSteps[existingIndex] = step;
                return { ...prev, steps: nextSteps };
              });
            } catch {
              // ignore parse error
            }
          } else if (eventType === "runtime_tool_call") {
            try {
              const call = RuntimeToolCallEventSchema.parse(JSON.parse(data));
              setStreamState((prev) => ({
                ...prev,
                runtimeTools: [
                  ...prev.runtimeTools,
                  {
                    toolName: call.tool_name,
                    status: "running",
                    latencyMs: null,
                    truncated: false,
                    error: null,
                  },
                ],
              }));
            } catch {
              // ignore parse error
            }
          } else if (eventType === "runtime_tool_result") {
            try {
              const result = RuntimeToolResultEventSchema.parse(JSON.parse(data));
              setStreamState((prev) => {
                const next: RuntimeToolProgress = {
                  toolName: result.tool_name,
                  status: result.success ? "completed" : "failed",
                  latencyMs: result.latency_ms,
                  truncated: result.truncated,
                  error: result.error ?? null,
                };
                const matchIndex = prev.runtimeTools.findIndex(
                  (t) => t.toolName === result.tool_name && t.status === "running",
                );
                if (matchIndex === -1) {
                  return { ...prev, runtimeTools: [...prev.runtimeTools, next] };
                }
                const runtimeTools = [...prev.runtimeTools];
                runtimeTools[matchIndex] = next;
                return { ...prev, runtimeTools };
              });
            } catch {
              // ignore parse error
            }
          } else if (eventType === "harness_activity" || eventType === "activity") {
            try {
              const activity = HarnessActivityEventSchema.parse(JSON.parse(data));
              setStreamState((prev) => {
                // Event records are progressive snapshots. Keep their position
                // stable while an action moves from planned to complete.
                const activityKey = [
                  activity.workflow_run_id ?? "stream",
                  activity.event_type,
                  activity.label,
                ].join(":");
                const existingIndex = prev.harnessActivities.findIndex((item) =>
                  [item.workflow_run_id ?? "stream", item.event_type, item.label].join(":") === activityKey,
                );
                if (existingIndex === -1) {
                  return {
                    ...prev,
                    harnessActivities: [...prev.harnessActivities, activity],
                  };
                }
                const harnessActivities = [...prev.harnessActivities];
                harnessActivities[existingIndex] = {
                  ...harnessActivities[existingIndex],
                  ...activity,
                  safe_details: {
                    ...harnessActivities[existingIndex].safe_details,
                    ...activity.safe_details,
                  },
                };
                return { ...prev, harnessActivities };
              });
            } catch {
              // ignore parse error
            }
          } else if (eventType === "final_answer") {
            try {
              const structuredAnswer = StructuredAnswerSchema.parse(JSON.parse(data));
              setStreamState((prev) => ({ ...prev, structuredAnswer }));
            } catch {
              // Preserve token rendering when an older backend emits no typed answer.
            }
          } else if (eventType === "pending_action") {
            try {
              const pendingAction = HarnessPendingActionSchema.parse(JSON.parse(data));
              setStreamState((prev) => ({
                ...prev,
                isStreaming: false,
                pendingAction,
              }));
            } catch {
              // Ignore malformed or overbroad action payloads.
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
    [conversationId],
  );

  return { streamState, sendMessage, reset };
}
