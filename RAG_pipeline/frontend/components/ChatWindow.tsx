"use client";

import { Loader2 } from "lucide-react";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";
import { ModeToggle } from "./ModeToggle";
import { ProjectSelector } from "./ProjectSelector";
import { RagProjectSelector } from "./RagProjectSelector";
import type { ChatMode } from "./ModeToggle";
import type {
  ConversationDetailResponse,
  ConversationMessageRequest,
} from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";
import type { DeepDiveState } from "@/hooks/useDeepDive";

interface ChatWindowProps {
  conversation: ConversationDetailResponse | null;
  isLoading: boolean;
  isSending: boolean;
  onSendMessage: (request: ConversationMessageRequest) => void;
  streamingMessage?: StreamState | null;
  pendingUserMessage?: string | null;
  mode: ChatMode;
  onModeChange: (mode: ChatMode) => void;
  selectedProject: string;
  onProjectChange: (projectId: string) => void;
  deepDiveState?: DeepDiveState | null;
  onCancelDeepDive?: () => void;
}

export function ChatWindow({
  conversation,
  isLoading,
  isSending,
  onSendMessage,
  streamingMessage,
  pendingUserMessage,
  mode,
  onModeChange,
  selectedProject,
  onProjectChange,
  deepDiveState,
  onCancelDeepDive,
}: ChatWindowProps) {
  if (!conversation) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground">
        <p>Select a conversation or create a new one to start chatting.</p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  function handleSend(message: string) {
    onSendMessage({ query: message });
  }

  const isDeepDive = mode === "deep-dive";
  const inputDisabled = isDeepDive && !selectedProject;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header with mode toggle */}
      <div className="border-b p-4 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <h2 className="font-semibold truncate">
            {conversation.title || `Conversation ${conversation.id}`}
          </h2>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          {isDeepDive ? (
            <ProjectSelector
              selectedProject={selectedProject}
              onProjectChange={onProjectChange}
            />
          ) : (
            <RagProjectSelector
              selectedProject={selectedProject}
              onProjectChange={onProjectChange}
            />
          )}
          <ModeToggle
            mode={mode}
            onModeChange={onModeChange}
            variant="chat"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <MessageList
          messages={conversation.messages}
          streamingMessage={isDeepDive ? null : streamingMessage}
          pendingUserMessage={pendingUserMessage}
          deepDiveState={isDeepDive ? deepDiveState : null}
          onCancelDeepDive={onCancelDeepDive}
        />
      </div>

      <ChatInput
        onSend={handleSend}
        isLoading={isSending || (deepDiveState?.isStreaming ?? false)}
        disabled={inputDisabled}
        placeholder={
          isDeepDive
            ? selectedProject
              ? "Ask a question about the project..."
              : "Select a project to start deep dive..."
            : undefined
        }
      />
    </div>
  );
}
