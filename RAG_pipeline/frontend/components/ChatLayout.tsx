"use client";

import { ChatWindow } from "./ChatWindow";
import { useChatContext } from "@/contexts/ChatContext";

export function ChatLayout() {
  const {
    activeConversation,
    isLoadingConversation,
    streamState,
    pendingUserMessage,
    mode,
    setMode,
    selectedProject,
    deepDiveState,
    onCancelDeepDive,
    handleSendMessage,
  } = useChatContext();

  return (
    <ChatWindow
      conversation={activeConversation}
      isLoading={isLoadingConversation}
      isSending={streamState.isStreaming}
      onSendMessage={handleSendMessage}
      streamingMessage={
        streamState.isStreaming || streamState.content ? streamState : null
      }
      pendingUserMessage={pendingUserMessage}
      mode={mode}
      onModeChange={setMode}
      selectedProject={selectedProject}
      deepDiveState={deepDiveState}
      onCancelDeepDive={onCancelDeepDive}
    />
  );
}
