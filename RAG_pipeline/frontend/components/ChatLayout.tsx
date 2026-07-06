"use client";

import { useEffect } from "react";
import { ChatWindow } from "./ChatWindow";
import { useChatContext } from "@/contexts/ChatContext";
import { useViewport } from "@/hooks/useViewport";
import { useVisualViewport } from "@/hooks/useVisualViewport";
import { useUrlConversationId, useSyncUrlConversationId } from "@/hooks/useUrlConversationId";
import { MobileChatList } from "./mobile/MobileChatList";
import { MobileTopBar } from "./mobile/MobileTopBar";
import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";

export function ChatLayout() {
  const chat = useChatContext();
  const { isMobile } = useViewport();
  const urlState = useUrlConversationId();
  const router = useRouter();
  const { keyboardHeight } = useVisualViewport();
  const {
    activeConversation,
    activeConversationId,
    conversations,
    isLoading,
    isLoadingConversation,
    setActiveConversationId,
  } = chat;

  useSyncUrlConversationId(activeConversationId, urlState.isNew);

  useEffect(() => {
    if (urlState.isNew) {
      if (activeConversationId !== null) {
        setActiveConversationId(null);
      }
      return;
    }

    if (urlState.activeId !== null) {
      const existsInList = conversations.some(
        (conversation) => conversation.id === urlState.activeId,
      );
      const selectedMissing =
        activeConversationId === urlState.activeId &&
        !isLoadingConversation &&
        activeConversation === null;

      if (!isLoading && !existsInList && (activeConversationId !== urlState.activeId || selectedMissing)) {
        if (activeConversationId !== null) {
          setActiveConversationId(null);
        }
        router.replace("/?c=new");
        return;
      }

      if (activeConversationId !== urlState.activeId) {
        setActiveConversationId(urlState.activeId);
      }
    }
  }, [
    activeConversation,
    activeConversationId,
    conversations,
    isLoading,
    isLoadingConversation,
    router,
    setActiveConversationId,
    urlState.activeId,
    urlState.isNew,
  ]);

  const isListView =
    isMobile && urlState.activeId === null && !urlState.isNew;

  if (isListView) {
    return <MobileChatList />;
  }

  const title =
    activeConversation?.title || (urlState.isNew ? "New Chat" : "Chat");

  return (
    <div className="flex flex-col h-full">
      {isMobile && (
        <MobileTopBar
          title={title}
          leftAction={
            <button
              onClick={() => {
                router.push("/");
              }}
              className="-ml-2 p-2 rounded-md text-foreground hover:bg-muted transition-colors min-h-[44px] min-w-[44px] flex items-center justify-center"
              aria-label="Back to conversations"
              title="Back"
            >
              <ArrowLeft className="h-5 w-5" />
            </button>
          }
        />
      )}
      <div
        className="flex-1 min-h-0"
        style={
          isMobile && keyboardHeight > 0
            ? { paddingBottom: keyboardHeight }
            : undefined
        }
      >
        <ChatWindow
          conversation={chat.activeConversation}
          isLoading={chat.isLoadingConversation}
          isSending={chat.streamState.isStreaming}
          onSendMessage={chat.handleSendMessage}
          streamingMessage={
            chat.streamState.isStreaming || chat.streamState.content || chat.streamState.error
              ? chat.streamState
              : null
          }
          onRetryMessage={chat.retryFailedMessage}
          pendingUserMessage={chat.pendingUserMessage}
          mode={chat.mode}
          onModeChange={chat.setMode}
          selectedProject={chat.selectedProject}
          deepDiveState={chat.deepDiveState}
          onCancelDeepDive={chat.onCancelDeepDive}
        />
      </div>
    </div>
  );
}
