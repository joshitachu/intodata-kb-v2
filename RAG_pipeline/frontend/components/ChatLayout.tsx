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

  useSyncUrlConversationId(chat.activeConversationId, urlState.isNew);

  useEffect(() => {
    if (chat.activeConversationId !== urlState.activeId) {
      chat.setActiveConversationId(urlState.activeId);
    }
  }, [urlState.activeId, chat]);

  const isListView =
    isMobile && urlState.activeId === null && !urlState.isNew;

  if (isListView) {
    return <MobileChatList />;
  }

  const title =
    chat.activeConversation?.title || (urlState.isNew ? "New Chat" : "Chat");

  return (
    <div className="flex flex-col h-full">
      {isMobile && (
        <MobileTopBar
          title={title}
          leftAction={
            <button
              onClick={() => {
                chat.setActiveConversationId(null);
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
