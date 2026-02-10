"use client";

import { useState, useEffect } from "react";
import { ConversationList } from "./ConversationList";
import { ChatWindow } from "./ChatWindow";
import {
  useConversations,
  useConversation,
  useCreateConversation,
  useDeleteConversation,
  useSendMessage,
} from "@/hooks/useConversations";
import type { ConversationMessageRequest } from "@/types";

export function ChatLayout() {
  const [activeConversationId, setActiveConversationId] = useState<
    number | null
  >(null);

  const { data: conversationList, isLoading: isLoadingList } =
    useConversations();
  const { data: activeConversation, isLoading: isLoadingConversation } =
    useConversation(activeConversationId);
  const createMutation = useCreateConversation();
  const deleteMutation = useDeleteConversation();

  // Use the useSendMessage hook when we have an active conversation
  const sendMutation = useSendMessage(activeConversationId ?? 0);

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
    try {
      await sendMutation.mutateAsync(request);
    } catch (error) {
      console.error("Failed to send message:", error);
    }
  }

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
        isSending={sendMutation.isPending}
        onSendMessage={handleSendMessage}
      />
    </div>
  );
}
