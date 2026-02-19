"use client";

import { useState, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ConversationList } from "./ConversationList";
import { ChatWindow } from "./ChatWindow";
import {
  useConversations,
  useConversation,
  useCreateConversation,
  useDeleteConversation,
} from "@/hooks/useConversations";
import { useStreamMessage } from "@/hooks/useStreamMessage";
import type { ConversationMessageRequest } from "@/types";

export function ChatLayout() {
  const [activeConversationId, setActiveConversationId] = useState<
    number | null
  >(null);

  const queryClient = useQueryClient();
  const { data: conversationList, isLoading: isLoadingList } =
    useConversations();
  const { data: activeConversation, isLoading: isLoadingConversation } =
    useConversation(activeConversationId);
  const createMutation = useCreateConversation();
  const deleteMutation = useDeleteConversation();

  const { streamState, sendMessage: sendStreamMessage, reset } =
    useStreamMessage(activeConversationId);

  // When streaming is done (messageId set), invalidate caches and reset
  useEffect(() => {
    if (streamState.messageId !== null) {
      queryClient.invalidateQueries({
        queryKey: ["conversation", activeConversationId],
      });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      reset();
    }
  }, [streamState.messageId, activeConversationId, queryClient, reset]);

  // Select the first conversation if none is selected
  useEffect(() => {
    if (
      !activeConversationId &&
      conversationList?.conversations &&
      conversationList.conversations.length > 0
    ) {
      setActiveConversationId(conversationList.conversations[0].id);
    }
  }, [activeConversationId, conversationList]);

  // Clear active conversation if it was deleted
  useEffect(() => {
    if (
      activeConversationId &&
      conversationList?.conversations &&
      !conversationList.conversations.find((c) => c.id === activeConversationId)
    ) {
      setActiveConversationId(null);
    }
  }, [activeConversationId, conversationList]);

  // Track the optimistic user message
  const [pendingUserMessage, setPendingUserMessage] = useState<string | null>(
    null,
  );

  async function handleCreateConversation() {
    try {
      const newConversation = await createMutation.mutateAsync({});
      setActiveConversationId(newConversation.id);
    } catch (error) {
      console.error("Failed to create conversation:", error);
    }
  }

  async function handleDeleteConversation(id: number) {
    try {
      await deleteMutation.mutateAsync(id);
      if (activeConversationId === id) {
        setActiveConversationId(null);
      }
    } catch (error) {
      console.error("Failed to delete conversation:", error);
    }
  }

  async function handleSendMessage(request: ConversationMessageRequest) {
    if (!activeConversationId) return;
    setPendingUserMessage(request.query);
    try {
      await sendStreamMessage(request);
    } catch (error) {
      console.error("Failed to send message:", error);
    }
  }

  // Clear pending user message when stream is done
  useEffect(() => {
    if (!streamState.isStreaming && streamState.messageId !== null) {
      setPendingUserMessage(null);
    }
  }, [streamState.isStreaming, streamState.messageId]);

  return (
    <div className="flex h-full">
      <ConversationList
        conversations={conversationList?.conversations ?? []}
        activeId={activeConversationId}
        isLoading={isLoadingList}
        onSelect={setActiveConversationId}
        onCreate={handleCreateConversation}
        onDelete={handleDeleteConversation}
        isCreating={createMutation.isPending}
      />
      <ChatWindow
        conversation={activeConversation ?? null}
        isLoading={isLoadingConversation}
        isSending={streamState.isStreaming}
        onSendMessage={handleSendMessage}
        streamingMessage={
          streamState.isStreaming || streamState.content
            ? streamState
            : null
        }
        pendingUserMessage={pendingUserMessage}
      />
    </div>
  );
}
