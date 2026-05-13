"use client";

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useRef,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useConversations,
  useConversation,
  useCreateConversation,
  useDeleteConversation,
} from "@/hooks/useConversations";
import { useStreamMessage } from "@/hooks/useStreamMessage";
import { useDeepDive } from "@/hooks/useDeepDive";
import type { StreamState } from "@/hooks/useStreamMessage";
import type { DeepDiveState } from "@/hooks/useDeepDive";
import type {
  ConversationResponse,
  ConversationDetailResponse,
  ConversationMessageRequest,
} from "@/types";
import type { ChatMode } from "@/components/ModeToggle";

interface ChatContextValue {
  // Conversation list
  conversations: ConversationResponse[];
  isLoading: boolean;

  // Active conversation
  activeConversationId: number | null;
  setActiveConversationId: (id: number | null) => void;
  activeConversation: ConversationDetailResponse | null;
  isLoadingConversation: boolean;

  // Mode & project
  mode: ChatMode;
  setMode: (mode: ChatMode) => void;
  selectedProject: string;
  setSelectedProject: (project: string) => void;

  // Actions
  createConversation: () => Promise<void>;
  deleteConversation: (id: number) => Promise<void>;
  handleSendMessage: (request: ConversationMessageRequest) => Promise<void>;
  isCreating: boolean;

  // Stream state
  pendingUserMessage: string | null;
  streamState: StreamState;
  deepDiveState: DeepDiveState;
  onCancelDeepDive: () => void;
}

const ChatContext = createContext<ChatContextValue | null>(null);

export function useChatContext(): ChatContextValue {
  const ctx = useContext(ChatContext);
  if (!ctx) {
    throw new Error("useChatContext must be used within a ChatProvider");
  }
  return ctx;
}

export function useChatContextSafe(): ChatContextValue | null {
  return useContext(ChatContext);
}

export function ChatProvider({ children }: { children: ReactNode }) {
  const [activeConversationId, setActiveConversationId] = useState<
    number | null
  >(null);
  const [mode, setModeState] = useState<ChatMode>("chat");
  const [selectedProject, setSelectedProject] = useState("");
  const [pendingUserMessage, setPendingUserMessage] = useState<string | null>(
    null,
  );
  const justCreatedIdRef = useRef<number | null>(null);

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

  // Clear active conversation if it was deleted (skip for just-created ones)
  useEffect(() => {
    if (
      activeConversationId &&
      conversationList?.conversations &&
      !conversationList.conversations.find(
        (c) => c.id === activeConversationId,
      )
    ) {
      if (activeConversationId === justCreatedIdRef.current) {
        return; // List hasn't refetched yet — don't clear
      }
      setActiveConversationId(null);
    } else if (
      activeConversationId &&
      justCreatedIdRef.current === activeConversationId &&
      conversationList?.conversations?.find(
        (c) => c.id === activeConversationId,
      )
    ) {
      justCreatedIdRef.current = null; // List caught up, clear the ref
    }
  }, [activeConversationId, conversationList]);

  // Clear pending user message when stream is done
  useEffect(() => {
    if (
      mode === "chat" &&
      !streamState.isStreaming &&
      streamState.messageId !== null
    ) {
      setPendingUserMessage(null);
    }
  }, [mode, streamState.isStreaming, streamState.messageId]);

  async function createConversation() {
    try {
      const newConversation = await createMutation.mutateAsync({});
      setActiveConversationId(newConversation.id);
    } catch (error) {
      console.error("Failed to create conversation:", error);
    }
  }

  async function deleteConversation(id: number) {
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
    let conversationId = activeConversationId;
    if (!conversationId) {
      try {
        const newConversation = await createMutation.mutateAsync({});
        conversationId = newConversation.id;
        justCreatedIdRef.current = newConversation.id;
        setActiveConversationId(newConversation.id);
      } catch (error) {
        console.error("Failed to create conversation:", error);
        return;
      }
    }

    setPendingUserMessage(request.query);

    if (mode === "deep-dive") {
      try {
        const result = await deepDive.query({
          project_id: selectedProject,
          question: request.query,
          session_id: deepDive.state.sessionId ?? undefined,
        });
        if (result?.content && conversationId) {
          try {
            await fetch(`/api/conversations/${conversationId}/messages/save`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                user_message: request.query,
                assistant_message: result.content,
                latency_ms: result.latency_ms ?? null,
              }),
            });
          } catch (e) {
            console.error("Failed to save deep dive exchange:", e);
          }
          queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
          queryClient.invalidateQueries({ queryKey: ["conversations"] });
        }
      } catch (error) {
        console.error("Deep dive failed:", error);
      } finally {
        setPendingUserMessage(null);
      }
    } else {
      try {
        await sendStreamMessage(
          {
            ...request,
            project_id: request.project_id || selectedProject || undefined,
          },
          conversationId ?? undefined,
        );
      } catch (error) {
        console.error("Failed to send message:", error);
      }
    }
  }

  function setMode(newMode: ChatMode) {
    setModeState(newMode);
    if (newMode !== "deep-dive") {
      deepDive.reset();
      setPendingUserMessage(null);
    }
  }

  const conversations = conversationList?.conversations ?? [];

  return (
    <ChatContext.Provider
      value={{
        conversations,
        isLoading: isLoadingList,
        activeConversationId,
        setActiveConversationId,
        activeConversation: activeConversation ?? null,
        isLoadingConversation,
        mode,
        setMode,
        selectedProject,
        setSelectedProject,
        createConversation,
        deleteConversation,
        handleSendMessage,
        isCreating: createMutation.isPending,
        pendingUserMessage,
        streamState,
        deepDiveState: deepDive.state,
        onCancelDeepDive: deepDive.cancel,
      }}
    >
      {children}
    </ChatContext.Provider>
  );
}
