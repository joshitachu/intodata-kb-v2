"use client";

import { useState, useCallback, useRef } from "react";
import type { DeepDiveRequest } from "@/types";

export interface DeepDiveState {
  isStreaming: boolean;
  content: string;
  thinking: string;
  activityCount: number;
  status: string;
  error: string | null;
  sessionId: string | null;
  question: string | null;
  usage: {
    duration_ms?: number;
    cost_usd?: number;
    input_tokens?: number;
    output_tokens?: number;
  } | null;
}

const INITIAL_STATE: DeepDiveState = {
  isStreaming: false,
  content: "",
  thinking: "",
  activityCount: 0,
  status: "",
  error: null,
  sessionId: null,
  question: null,
  usage: null,
};

function decodeSseData(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function isLeakedToolSyntax(text: string): boolean {
  const trimmed = text.trim();
  return (
    trimmed.startsWith("<function_calls>") ||
    /^<invoke\s/i.test(trimmed) ||
    /^[a-zA-Z_][\w]*\(\s*\{/.test(trimmed) ||
    trimmed.startsWith('{"tool"') ||
    trimmed.startsWith('{"tool_calls"') ||
    trimmed.includes("\nTOOL_RESULTS:")
  );
}

export function useDeepDive() {
  const [state, setState] = useState<DeepDiveState>(INITIAL_STATE);
  const abortRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    setState(INITIAL_STATE);
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setState((prev) => ({ ...prev, isStreaming: false }));
  }, []);

  const query = useCallback(async (request: DeepDiveRequest): Promise<{ content: string; sessionId: string | null; latency_ms: number | null } | null> => {
    // Abort any in-flight stream
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    let finalContent = "";
    let finalSessionId: string | null = request.session_id ?? null;
    let finalLatencyMs: number | null = null;

    setState((prev) => ({
      isStreaming: true,
      content: "",
      thinking: "",
      activityCount: 0,
      status: "Starting deep dive analysis...",
      error: null,
      // Preserve sessionId from previous state if the request has one
      sessionId: request.session_id ?? prev.sessionId,
      question: request.question,
      usage: null,
    }));

    try {
      const res = await fetch("/api/deep-dive/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
        signal: controller.signal,
      });

      if (!res.ok) {
        const text = await res.text();
        let message = "Deep dive failed";
        try {
          message = JSON.parse(text).error || message;
        } catch {
          // ignore
        }
        setState((prev) => ({ ...prev, isStreaming: false, error: message }));
        return null;
      }

      const reader = res.body?.getReader();
      if (!reader) {
        setState((prev) => ({
          ...prev,
          isStreaming: false,
          error: "No response body",
        }));
        return null;
      }

      const decoder = new TextDecoder();
      let buffer = "";
      let currentEvent = "";
      let acceptedAnswer = false;
      let receivedError = false;
      let receivedDone = false;
      let suppressedInternalOutput = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Process complete SSE lines
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            const data = decodeSseData(line.slice(6));
            const textData =
              typeof data === "string" ? data : JSON.stringify(data);

            if (currentEvent === "token") {
              if (!isLeakedToolSyntax(textData)) {
                acceptedAnswer = true;
                finalContent += textData;
                setState((prev) => ({
                  ...prev,
                  content: prev.content + textData,
                }));
              } else {
                suppressedInternalOutput = true;
              }
            } else if (currentEvent === "thinking") {
              setState((prev) => ({
                ...prev,
                thinking: prev.thinking + textData,
              }));
            } else if (currentEvent === "thinking_start") {
              // Reset thinking for new thinking block
              setState((prev) => ({
                ...prev,
                thinking: "",
              }));
            } else if (currentEvent === "session") {
              finalSessionId = textData;
              setState((prev) => ({
                ...prev,
                sessionId: textData,
              }));
            } else if (currentEvent === "tool_use") {
              setState((prev) => ({
                ...prev,
                activityCount: prev.activityCount + 1,
                status: "Inspecting repository...",
              }));
            } else if (currentEvent === "usage") {
              if (data && typeof data === "object") {
                const usageData = data as DeepDiveState["usage"];
                setState((prev) => ({ ...prev, usage: usageData }));
              }
            } else if (currentEvent === "done") {
              receivedDone = true;
              if (data && typeof data === "object") {
                const doneData = data as DeepDiveState["usage"];
                if (typeof doneData?.duration_ms === "number") {
                  finalLatencyMs = doneData.duration_ms;
                }
                setState((prev) => ({
                  ...prev,
                  isStreaming: false,
                  usage: { ...prev.usage, ...doneData },
                  error:
                    !acceptedAnswer && !receivedError
                      ? "Deep Dive ended without a final answer."
                      : prev.error,
                }));
              } else {
                setState((prev) => ({
                  ...prev,
                  isStreaming: false,
                  error:
                    !acceptedAnswer && !receivedError
                      ? "Deep Dive ended without a final answer."
                      : prev.error,
                }));
              }
            } else if (currentEvent === "error") {
              receivedError = true;
              setState((prev) => ({
                ...prev,
                isStreaming: false,
                error: textData,
              }));
            } else if (currentEvent === "status") {
              setState((prev) => ({
                ...prev,
                status: textData,
              }));
            }

            currentEvent = "";
          }
        }
      }

      // If stream ended without a done event
      setState((prev) => {
        if (prev.isStreaming) {
          return {
            ...prev,
            isStreaming: false,
            error:
              !acceptedAnswer && !receivedError
                ? suppressedInternalOutput
                  ? "Deep Dive returned internal tool output but no final answer."
                  : receivedDone
                    ? "Deep Dive ended without a final answer."
                    : "Deep Dive stream closed before a final answer."
                : prev.error,
          };
        }
        return prev;
      });

      return { content: finalContent, sessionId: finalSessionId, latency_ms: finalLatencyMs };
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return null;
      }
      setState((prev) => ({
        ...prev,
        isStreaming: false,
        error: err instanceof Error ? err.message : "Stream failed",
      }));
      return null;
    }
  }, []);

  return { state, query, cancel, reset };
}
