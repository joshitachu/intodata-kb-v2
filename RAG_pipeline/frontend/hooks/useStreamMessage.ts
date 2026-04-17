"use client";

import { useState, useCallback, useRef } from "react";
import type { ConversationMessageRequest, DocumentSource, UsageStats } from "@/types";

export interface StreamState {
  isStreaming: boolean;
  content: string;
  sources: DocumentSource[];
  error: string | null;
  messageId: number | null;
  latencyMs: number | null;
  usage: UsageStats | null;
}

const INITIAL_STATE: StreamState = {
  isStreaming: false,
  content: "",
  sources: [],
  error: null,
  messageId: null,
  latencyMs: null,
  usage: null,
};

export function useStreamMessage(conversationId: number | null) {
  const [streamState, setStreamState] = useState<StreamState>(INITIAL_STATE);
  const abortRef = useRef<AbortController | null>(null);

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

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          // Process complete SSE lines
          const lines = buffer.split("\n");
          // Keep the last potentially incomplete line in the buffer
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEvent = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              const data = line.slice(6);

              if (currentEvent === "sources") {
                try {
                  const sources = JSON.parse(data) as DocumentSource[];
                  setStreamState((prev) => ({ ...prev, sources }));
                } catch {
                  // ignore parse error
                }
              } else if (currentEvent === "token") {
                try {
                  const token = JSON.parse(data) as string;
                  setStreamState((prev) => ({
                    ...prev,
                    content: prev.content + token,
                  }));
                } catch {
                  // ignore parse error
                }
              } else if (currentEvent === "usage") {
                try {
                  const usage = JSON.parse(data) as UsageStats;
                  setStreamState((prev) => ({ ...prev, usage }));
                } catch {
                  // ignore parse error
                }
              } else if (currentEvent === "error") {
                setStreamState((prev) => ({
                  ...prev,
                  isStreaming: false,
                  error: data,
                }));
              } else if (currentEvent === "done") {
                try {
                  const result = JSON.parse(data) as {
                    message_id: number;
                    latency_ms: number;
                  };
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

              currentEvent = "";
            }
            // Empty lines are SSE delimiters — no action needed
          }
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
