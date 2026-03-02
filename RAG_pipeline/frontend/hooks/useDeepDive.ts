"use client";

import { useState, useCallback, useRef } from "react";
import type { DeepDiveRequest } from "@/types";

export interface DeepDiveState {
  isStreaming: boolean;
  content: string;
  thinking: string;
  toolCalls: string[];
  error: string | null;
  sessionId: string | null;
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
  toolCalls: [],
  error: null,
  sessionId: null,
  usage: null,
};

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

  const query = useCallback(async (request: DeepDiveRequest) => {
    // Abort any in-flight stream
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setState((prev) => ({
      isStreaming: true,
      content: "",
      thinking: "",
      toolCalls: [],
      error: null,
      // Preserve sessionId from previous state if the request has one
      sessionId: request.session_id ?? prev.sessionId,
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
        return;
      }

      const reader = res.body?.getReader();
      if (!reader) {
        setState((prev) => ({
          ...prev,
          isStreaming: false,
          error: "No response body",
        }));
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";
      let currentEvent = "";

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
            const data = line.slice(6);

            if (currentEvent === "token") {
              setState((prev) => ({
                ...prev,
                content: prev.content + data,
              }));
            } else if (currentEvent === "thinking") {
              setState((prev) => ({
                ...prev,
                thinking: prev.thinking + data,
              }));
            } else if (currentEvent === "thinking_start") {
              // Reset thinking for new thinking block
              setState((prev) => ({
                ...prev,
                thinking: "",
              }));
            } else if (currentEvent === "session") {
              setState((prev) => ({
                ...prev,
                sessionId: data,
              }));
            } else if (currentEvent === "tool_use") {
              setState((prev) => ({
                ...prev,
                toolCalls: [...prev.toolCalls, data],
              }));
            } else if (currentEvent === "usage") {
              try {
                const usageData = JSON.parse(data);
                setState((prev) => ({ ...prev, usage: usageData }));
              } catch {
                // ignore
              }
            } else if (currentEvent === "done") {
              try {
                const doneData = JSON.parse(data);
                setState((prev) => ({
                  ...prev,
                  isStreaming: false,
                  usage: { ...prev.usage, ...doneData },
                }));
              } catch {
                setState((prev) => ({ ...prev, isStreaming: false }));
              }
            } else if (currentEvent === "error") {
              setState((prev) => ({
                ...prev,
                isStreaming: false,
                error: data,
              }));
            } else if (currentEvent === "status") {
              setState((prev) => ({
                ...prev,
                toolCalls: [...prev.toolCalls, `[status] ${data}`],
              }));
            }

            currentEvent = "";
          }
        }
      }

      // If stream ended without a done event
      setState((prev) => {
        if (prev.isStreaming) {
          return { ...prev, isStreaming: false };
        }
        return prev;
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      setState((prev) => ({
        ...prev,
        isStreaming: false,
        error: err instanceof Error ? err.message : "Stream failed",
      }));
    }
  }, []);

  return { state, query, cancel, reset };
}
