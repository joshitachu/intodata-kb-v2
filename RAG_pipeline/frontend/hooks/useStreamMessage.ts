"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import {
  type ConversationMessageRequest,
  type TypedAgentResponse,
  SseDoneSchema,
  TypedAgentResponseSchema,
} from "@/types";

export interface StreamState {
  isStreaming: boolean;
  content: string;
  error: string | null;
  messageId: number | null;
  latencyMs: number | null;
  typedResponse?: TypedAgentResponse | null;
}

const INITIAL_STATE: StreamState = {
  isStreaming: false,
  content: "",
  error: null,
  messageId: null,
  latencyMs: null,
  typedResponse: null,
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
        error: null,
        messageId: null,
        latencyMs: null,
        typedResponse: null,
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
          if (eventType === "agent_response") {
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
