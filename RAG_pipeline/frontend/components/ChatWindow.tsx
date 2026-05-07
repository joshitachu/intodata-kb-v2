"use client";

import { FolderOpen, Sparkles } from "lucide-react";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";
import { ModeToggle } from "./ModeToggle";
import { ProjectSelector } from "./ProjectSelector";
import { RagProjectSelector } from "./RagProjectSelector";
import { useUserProjects } from "@/hooks/useUserProjects";
import type { ChatMode } from "./ModeToggle";
import type {
  ConversationDetailResponse,
  ConversationMessageRequest,
} from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";
import type { DeepDiveState } from "@/hooks/useDeepDive";

const SUGGESTIONS = [
  "What projects are available?",
  "Summarize the latest documentation",
  "How do the integrations connect?",
  "What are the key data flows?",
];

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
  deepDiveState,
  onCancelDeepDive,
}: ChatWindowProps) {
  const isDeepDive = mode === "deep-dive";
  const inputDisabled = isDeepDive && !selectedProject;

  const { data: userProjectsData } = useUserProjects();
  const selectedProjectName = !isDeepDive
    ? userProjectsData?.projects.find((p) => p.id === selectedProject)?.display_name
    : undefined;

  // Deep dive content exists independently of whether a conversation record loaded
  const hasDeepDiveContent =
    isDeepDive &&
    deepDiveState &&
    (deepDiveState.isStreaming ||
      deepDiveState.content ||
      deepDiveState.thinking ||
      deepDiveState.error);

  function handleSend(message: string, images?: string[]) {
    onSendMessage({ query: message, project_id: selectedProject || undefined, images });
  }

  function handleSuggestionClick(suggestion: string) {
    handleSend(suggestion);
  }

  const toolbarContent = (
    <>
      <ModeToggle mode={mode} onModeChange={onModeChange} />
      {isDeepDive ? <ProjectSelector /> : <RagProjectSelector />}
    </>
  );

  // Welcome screen - no conversation selected, and no active deep dive to show
  if (!conversation && !isLoading && !hasDeepDiveContent) {
    return (
      <div className="flex-1 flex flex-col h-full relative">
        {selectedProjectName && (
          <div className="absolute top-3 right-4 z-10 pointer-events-none">
            <div className="flex items-center gap-2 px-3.5 py-1.5 rounded-full border bg-background/80 backdrop-blur-sm shadow-sm text-sm text-muted-foreground whitespace-nowrap">
              <FolderOpen className="h-3.5 w-3.5 text-primary/70 shrink-0" />
              <span>{selectedProjectName}</span>
            </div>
          </div>
        )}
        <div className="flex-1 flex flex-col items-center justify-center px-4">
          <div className="max-w-md text-center space-y-4">
            <div>
              <h2 className="text-xl font-semibold">IntoData Knowledge Base</h2>
              <p className="text-sm text-muted-foreground mt-1">
                Ask questions about your documents, explore integrations, or deep-dive into project code.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2 pt-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => handleSuggestionClick(s)}
                  className="text-left text-xs px-3 py-2.5 rounded-xl border bg-card hover:bg-muted/60 transition-colors text-muted-foreground hover:text-foreground"
                >
                  <Sparkles className="h-3 w-3 mb-1 text-primary/60" />
                  {s}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="max-w-3xl mx-auto w-full">
          <ChatInput
            onSend={handleSend}
            isLoading={false}
            disabled={inputDisabled}
            hideImagePicker={isDeepDive}
            toolbarContent={toolbarContent}
            placeholder={
              isDeepDive
                ? selectedProject
                  ? "Ask a question about the project..."
                  : "Select a project to start deep dive..."
                : "Ask about your documents..."
            }
          />
          <p className="text-xs text-muted-foreground text-center pb-2">
            AI can make mistakes. Always review responses before acting on them.
          </p>
        </div>
      </div>
    );
  }

  // Loading state - but not when deep dive content should be shown
  if ((isLoading || !conversation) && !hasDeepDiveContent) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="h-6 w-6 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden h-full relative">
      {selectedProjectName && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10 pointer-events-none">
          <div className="flex items-center gap-1.5 px-3 py-1 rounded-full border bg-background/80 backdrop-blur-sm shadow-sm text-xs text-muted-foreground whitespace-nowrap">
            <FolderOpen className="h-3 w-3 text-primary/70 shrink-0" />
            <span>{selectedProjectName}</span>
          </div>
        </div>
      )}
      <div className="flex-1 overflow-y-auto">
        <MessageList
          messages={conversation?.messages ?? []}
          conversationId={conversation?.id ?? null}
          streamingMessage={isDeepDive ? null : streamingMessage}
          pendingUserMessage={pendingUserMessage}
          deepDiveState={isDeepDive ? deepDiveState : null}
          onCancelDeepDive={onCancelDeepDive}
          onSuggestionClick={handleSuggestionClick}
        />
      </div>

      <div className="max-w-3xl mx-auto w-full">
        <ChatInput
          onSend={handleSend}
          isLoading={isSending || (deepDiveState?.isStreaming ?? false)}
          disabled={inputDisabled}
          hideImagePicker={isDeepDive}
          toolbarContent={toolbarContent}
          placeholder={
            isDeepDive
              ? selectedProject
                ? "Ask a question about the project..."
                : "Select a project to start deep dive..."
              : "Ask a follow-up..."
          }
        />
        <p className="text-xs text-muted-foreground text-center pb-2">
          AI can make mistakes. Always review responses before acting on them.
        </p>
      </div>
    </div>
  );
}
