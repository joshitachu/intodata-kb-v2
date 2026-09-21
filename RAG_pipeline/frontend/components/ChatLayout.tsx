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
import { ArrowLeft, Package, X } from "lucide-react";
import { PackWorkbench } from "./packs/PackWorkbench";
import { LabsTurnPanel } from "./labs/LabsTurnPanel";
import { useLabsTurnRecord } from "./labs/useLabsTurnRecord";
import { closePackWorkbenchHref } from "@/lib/packs";

export function ChatLayout() {
  const chat = useChatContext();
  // The V2 turn instrument -- plan, reasoning, tool calls, usage -- which used
  // to live on `/labs/chat`. It survives that page's removal because none of it
  // is rendered by the chat bubble, and it stays *outside* `ChatWindow` for the
  // reason it always did: the answer body is the unforked production renderer,
  // so a difference in the answer is a difference in the agent, not the UI.
  const labsTurn = useLabsTurnRecord(chat.streamState);
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
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {chat.activeLane === "v2" && (
            <LabsTurnPanel record={labsTurn} onStop={chat.cancelStream} />
          )}
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
          /> : null}
        </div>
        {packWorkspaceOpen && (
          <aside
            aria-label="Pack workbench"
            className={
              isMobile
                ? "absolute inset-0 z-20 flex h-full w-full flex-col bg-background"
                : "flex h-full w-[min(44vw,42rem)] shrink-0 flex-col border-l border-border bg-background"
            }
          >
            {/* Sticky header: the workbench body scrolls a long way, and the
                only way out used to be a bare "close" link at the very top. */}
            <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-4 py-3">
              <span className="flex min-w-0 items-center gap-2">
                <Package className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                <span className="truncate text-sm font-semibold text-foreground">
                  Pack workbench
                </span>
              </span>
              <button
                type="button"
                onClick={() =>
                  router.push(closePackWorkbenchHref(new URLSearchParams(searchParams.toString())))
                }
                aria-label="Close pack workbench"
                title="Close"
                className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              <PackWorkbench projectId={searchParams.get("project")} />
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
