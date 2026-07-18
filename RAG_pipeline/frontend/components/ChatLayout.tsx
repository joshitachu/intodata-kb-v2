"use client";

import { useEffect } from "react";
import { ChatWindow } from "./ChatWindow";
import { useChatContext } from "@/contexts/ChatContext";
import { useViewport } from "@/hooks/useViewport";
import { useVisualViewport } from "@/hooks/useVisualViewport";
import { useUrlConversationId, useSyncUrlConversationId } from "@/hooks/useUrlConversationId";
import { MobileChatList } from "./mobile/MobileChatList";
import { MobileTopBar } from "./mobile/MobileTopBar";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { PackWorkbench } from "./harness-packs/PackWorkbench";
import { closePackWorkspaceHref, packWorkspaceHref } from "@/lib/packWorkspace";

export function ChatLayout() {
  const chat = useChatContext();
  const { isMobile } = useViewport();
  const urlState = useUrlConversationId();
  const router = useRouter();
  const searchParams = useSearchParams();
  const packWorkspaceOpen = searchParams.get("workspace") === "pack";
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
      // Pack creation can finish creating its conversation before the `c=new`
      // URL is synchronized. Do not clear that conversation during the handoff.
      if (packWorkspaceOpen && activeConversationId !== null) return;
      if (
        activeConversationId !== null &&
        activeConversation !== null &&
        !isLoadingConversation
      ) {
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
    packWorkspaceOpen,
    router,
    setActiveConversationId,
    urlState.activeId,
    urlState.isNew,
  ]);

  useEffect(() => {
    if (
      chat.streamState.workspace !== "pack" ||
      packWorkspaceOpen ||
      activeConversationId === null
    ) return;
    router.replace(packWorkspaceHref({
      conversationId: activeConversationId,
      projectId: chat.selectedProject || undefined,
      packId: "active",
    }));
  }, [activeConversationId, chat.selectedProject, chat.streamState.workspace, packWorkspaceOpen, router]);

  const isListView =
    isMobile && urlState.activeId === null && !urlState.isNew && searchParams.get("workspace") !== "pack";

  if (isListView) {
    return <MobileChatList />;
  }

  const title =
    activeConversation?.title || (urlState.isNew ? "New Chat" : "Chat");

  return (
    <div className="flex flex-col h-full">
      {isMobile && !packWorkspaceOpen && (
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
        className="relative flex-1 min-h-0 flex"
        style={
          isMobile && keyboardHeight > 0
            ? { paddingBottom: keyboardHeight }
            : undefined
        }
      >
        <div className="min-w-0 flex-1">
          {!isMobile || !packWorkspaceOpen ? <ChatWindow
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
            onWorkflowChanged={chat.refreshActiveConversation}
            pendingUserMessage={chat.pendingUserMessage}
            mode={chat.mode}
            onModeChange={chat.setMode}
            selectedProject={chat.selectedProject}
            deepDiveState={chat.deepDiveState}
            onCancelDeepDive={chat.onCancelDeepDive}
          /> : null}
        </div>
        {packWorkspaceOpen && <div className={isMobile ? "absolute inset-0 z-20 h-full w-full" : "h-full w-[min(44vw,42rem)] shrink-0 border-l border-border"}>
          <PackWorkbench initialPackId={searchParams.get("pack")} initialProjectId={searchParams.get("project")} onClose={() => router.push(closePackWorkspaceHref(searchParams))} />
        </div>}
      </div>
    </div>
  );
}
