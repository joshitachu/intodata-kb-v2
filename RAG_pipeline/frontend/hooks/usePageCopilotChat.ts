"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useConversation,
  useCreateConversation,
  useUpdateConversation,
} from "@/hooks/useConversations";
import { useStreamMessage, type StreamState } from "@/hooks/useStreamMessage";
import type { ConversationMessageRequest, MessageResponse } from "@/types";

/**
 * Conversation state for a page-embedded copilot panel.
 *
 * Deliberately NOT `ChatContext`. That context owns the chat page's thread and
 * its mode switch, so reusing it would (a) interleave page questions with an
 * unrelated conversation, making both threads unreadable, and (b) route sends
 * through whatever mode the chat page was last left in -- a question asked on
 * the canvas would silently become a deep-dive query. The panel gets its own
 * thread instead; the wiring is small because `useStreamMessage` and the
 * conversation queries are already reusable on their own.
 */
export interface PageCopilotChat {
  conversationId: number | null;
  messages: readonly MessageResponse[];
  streamState: StreamState;
  /** The just-sent question, until the server echoes it back. */
  pendingUserMessage: string | null;
  isSending: boolean;
  /** Host-level failure outside the stream (e.g. the thread could not start). */
  errorMessage: string | null;
  send: (query: string) => void;
  retry: () => void;
  /** Explicitly expose this embedded thread in the ordinary chat list. */
  continueInMainChat: () => Promise<number | null>;
  isContinuingInMainChat: boolean;
  /** Abandon the thread and start fresh (e.g. the page switched project). */
  startNewThread: () => void;
  /**
   * Continue an existing thread instead of starting one -- e.g. a shared link
   * that carries both a level and the conversation it came from, so the answer
   * that produced the link is still on screen when the page opens.
   */
  adoptThread: (conversationId: number) => void;
}

/**
 * The SSE `done` event identifies the saved assistant message before React
 * Query has necessarily refetched it. Until that exact message is present in
 * the thread, starting another turn would clear the only visible copy held by
 * `useStreamMessage`.
 */
export function isCopilotTurnHandoffPending(
  messageId: number | null,
  messages: readonly MessageResponse[],
  conversationLoadFailed = false,
): boolean {
  return (
    messageId !== null &&
    !conversationLoadFailed &&
    !messages.some((message) => message.id === messageId)
  );
}

const THREAD_REFRESH_ERROR =
  "The answer was saved, but the thread history could not refresh. You can still continue.";

/**
 * @param buildRequest Turns a query into the outbound request, so the host owns
 *   every page-specific field (`project_id`, `page_scope`, ...). Read through a
 *   ref, so a host that rebuilds it every render does not destabilise `send`.
 */
export function usePageCopilotChat(
  buildRequest: (query: string) => ConversationMessageRequest,
): PageCopilotChat {
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [pendingUserMessage, setPendingUserMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const queryClient = useQueryClient();
  const createMutation = useCreateConversation();
  const updateMutation = useUpdateConversation();
  const { data: conversation, error: conversationLoadError } = useConversation(conversationId);
  const { streamState, sendMessage, reset } = useStreamMessage(conversationId);
  const messages = conversation?.messages ?? [];
  const isHandoffPending = isCopilotTurnHandoffPending(
    streamState.messageId,
    messages,
    Boolean(conversationLoadError),
  );

  const buildRequestRef = useRef(buildRequest);
  useEffect(() => {
    buildRequestRef.current = buildRequest;
  }, [buildRequest]);

  // The exact request of the last turn, replayed verbatim on retry -- the same
  // `client_turn_id` is what lets the backend resume that turn instead of
  // starting a second one.
  const lastTurnRef = useRef<{
    request: ConversationMessageRequest;
    conversationId: number;
  } | null>(null);

  // A finished turn is persisted server-side, so the refetched thread (not the
  // stream buffer) eventually becomes the source of truth -- including the
  // answer's `structured_answer.topology_targets`.
  //
  // Keep the completed stream visible until that exact persisted message is in
  // the query cache. Clearing it immediately after the SSE `done` event creates
  // a visible blank gap while the invalidated conversation is still refetching.
  useEffect(() => {
    if (streamState.messageId === null) return;
    const persisted = conversation?.messages.some(
      (message) => message.id === streamState.messageId,
    );
    if (!persisted) {
      void queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
      return;
    }
    queryClient.invalidateQueries({ queryKey: ["conversations"] });
    setPendingUserMessage(null);
    setErrorMessage((current) => current === THREAD_REFRESH_ERROR ? null : current);
    reset();
  }, [
    streamState.messageId,
    conversation?.messages,
    conversationId,
    queryClient,
    reset,
  ]);

  // Persistence is already complete when `messageId` is present. A malformed
  // or temporarily unavailable history response must therefore never leave
  // the composer locked forever: retain the streamed answer, disclose the
  // refresh failure, and let the user continue the saved thread.
  useEffect(() => {
    if (streamState.messageId === null || !conversationLoadError) return;
    setPendingUserMessage(null);
    setErrorMessage(THREAD_REFRESH_ERROR);
  }, [conversationLoadError, streamState.messageId]);

  // A failed turn still has the user's message saved server-side, so refetch
  // rather than leaving the question pinned as "pending" forever.
  useEffect(() => {
    if (!streamState.error) return;
    setPendingUserMessage(null);
    queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
  }, [streamState.error, conversationId, queryClient]);

  const send = useCallback(
    (query: string) => {
      const trimmed = query.trim();
      if (!trimmed || isSubmitting || streamState.isStreaming || isHandoffPending) return;
      setErrorMessage(null);
      setPendingUserMessage(trimmed);
      setIsSubmitting(true);
      void (async () => {
        try {
          let targetId = conversationId;
          if (targetId === null) {
            const created = await createMutation.mutateAsync({ surface: "topology" });
            targetId = created.id;
            setConversationId(created.id);
          }
          const request: ConversationMessageRequest = {
            ...buildRequestRef.current(trimmed),
            client_turn_id: globalThis.crypto.randomUUID(),
          };
          lastTurnRef.current = { request, conversationId: targetId };
          await sendMessage(request, targetId);
        } catch (error) {
          setPendingUserMessage(null);
          setErrorMessage(
            error instanceof Error ? error.message : "Could not send that question.",
          );
        } finally {
          setIsSubmitting(false);
        }
      })();
    },
    [
      conversationId,
      createMutation,
      isHandoffPending,
      isSubmitting,
      sendMessage,
      streamState.isStreaming,
    ],
  );

  const retry = useCallback(() => {
    const lastTurn = lastTurnRef.current;
    if (!lastTurn || isSubmitting || streamState.isStreaming || isHandoffPending) return;
    setErrorMessage(null);
    setPendingUserMessage(lastTurn.request.query);
    setIsSubmitting(true);
    void sendMessage(lastTurn.request, lastTurn.conversationId).finally(() =>
      setIsSubmitting(false),
    );
  }, [isHandoffPending, isSubmitting, sendMessage, streamState.isStreaming]);

  const continueInMainChat = useCallback(async (): Promise<number | null> => {
    if (conversationId === null || streamState.isStreaming || isHandoffPending) return null;
    setErrorMessage(null);
    try {
      await updateMutation.mutateAsync({
        id: conversationId,
        request: { visible_in_chat: true },
      });
      return conversationId;
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : "Could not continue this thread in chat.",
      );
      return null;
    }
  }, [conversationId, isHandoffPending, streamState.isStreaming, updateMutation]);

  const startNewThread = useCallback(() => {
    lastTurnRef.current = null;
    setConversationId(null);
    setPendingUserMessage(null);
    setErrorMessage(null);
    // Clears the visible stream state; an in-flight request is aborted by the
    // next `sendMessage` (or on unmount) rather than left to write into it.
    reset();
  }, [reset]);

  const adoptThread = useCallback(
    (id: number) => {
      // A non-positive id is what an unparseable URL degrades to; adopting it
      // would fetch conversation 0 and show an error for a link the user did
      // nothing wrong with.
      if (!Number.isInteger(id) || id <= 0) return;
      lastTurnRef.current = null;
      setConversationId(id);
      setPendingUserMessage(null);
      setErrorMessage(null);
      reset();
    },
    [reset],
  );

  return {
    conversationId,
    messages,
    streamState,
    pendingUserMessage,
    // Keep the composer locked for the very short stream-to-query hand-off.
    // This guarantees a new request cannot replace the completed turn's only
    // visible copy before the persisted thread has adopted it.
    isSending: isSubmitting || streamState.isStreaming || isHandoffPending,
    errorMessage,
    send,
    retry,
    continueInMainChat,
    isContinuingInMainChat: updateMutation.isPending,
    startNewThread,
    adoptThread,
  };
}
