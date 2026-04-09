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
import { useDeepDive } from "@/hooks/useDeepDive";
import type { ConversationMessageRequest } from "@/types";
import type { ChatMode } from "./ModeToggle";

export function ChatLayout() {
  const [activeConversationId, setActiveConversationId] = useState<
    number | null
  >(null);
  const [mode, setMode] = useState<ChatMode>("chat");
  const [selectedProject, setSelectedProject] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const queryClient = useQueryClient();
  const { data: conversationList, isLoading: isLoadingList } =
    useConversations();
  const { data: activeConversation, isLoading: isLoadingConversation } =
    useConversation(activeConversationId);
  const createMutation = useCreateConversation();
  const deleteMutation = useDeleteConversation();

  const { streamState, sendMessage: sendStreamMessage, reset } =
    useStreamMessage(activeConversationId);

  const deepDive = useDeepDive();

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

    if (mode === "deep-dive") {
      // Route to deep dive
      try {
        await deepDive.query({
          project_id: selectedProject,
          question: request.query,
          session_id: deepDive.state.sessionId ?? undefined,
        });
      } catch (error) {
        console.error("Deep dive failed:", error);
      }
    } else {
      // Normal chat flow — pass project_id if set
      try {
        await sendStreamMessage({
          ...request,
          project_id: request.project_id || selectedProject || undefined,
        });
      } catch (error) {
        console.error("Failed to send message:", error);
      }
    }
  }

  // Clear pending user message when stream is done
  useEffect(() => {
    if (mode === "chat" && !streamState.isStreaming && streamState.messageId !== null) {
      setPendingUserMessage(null);
    }
  }, [mode, streamState.isStreaming, streamState.messageId]);

  // Clear pending user message when deep dive finishes
  useEffect(() => {
    if (mode === "deep-dive" && !deepDive.state.isStreaming && deepDive.state.content) {
      setPendingUserMessage(null);
    }
  }, [mode, deepDive.state.isStreaming, deepDive.state.content]);

  function handleModeChange(newMode: ChatMode) {
    setMode(newMode);
    // Reset deep dive state when switching away
    if (newMode !== "deep-dive") {
      deepDive.reset();
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
        isCollapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed((prev) => !prev)}
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
        mode={mode}
        onModeChange={handleModeChange}
        selectedProject={selectedProject}
        onProjectChange={setSelectedProject}
        deepDiveState={deepDive.state}
        onCancelDeepDive={deepDive.cancel}
      />
    </div>
  );
}
