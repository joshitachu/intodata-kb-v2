"use client";

import { FolderOpen } from "lucide-react";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";
import { ModeToggle } from "./ModeToggle";
import { ProjectSelector } from "./ProjectSelector";
import { RagProjectSelector } from "./RagProjectSelector";
import { RuntimeStatusIndicator } from "./RuntimeStatusIndicator";
import { useUserProjects } from "@/hooks/useUserProjects";
import { SUGGESTIONS } from "@/lib/constants";
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
  onRetryMessage?: () => void;
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
  onRetryMessage,
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
      <RuntimeStatusIndicator />
    </>
  );

  // Welcome screen - no conversation selected, and no active deep dive to show
  if (!conversation && !isLoading && !hasDeepDiveContent) {
    return (
      <div className="flex-1 flex flex-col h-full relative overflow-hidden">
        {/* Ambient brand glow */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-[65%] opacity-[0.07] dark:opacity-[0.14]"
          style={{
            background:
              "radial-gradient(60% 55% at 50% 0%, hsl(var(--primary)) 0%, transparent 72%)",
          }}
        />

        {selectedProjectName && (
          <div className="absolute top-3 right-4 z-10 pointer-events-none">
            <div className="flex items-center gap-2 px-3.5 py-1.5 rounded-full border bg-background/80 backdrop-blur-sm shadow-elevation-xs text-sm text-muted-foreground whitespace-nowrap">
              <FolderOpen className="h-3.5 w-3.5 text-primary/70 shrink-0" />
              <span>{selectedProjectName}</span>
            </div>
          </div>
        )}

        <div className="relative flex-1 flex flex-col items-center justify-center px-4 py-10 overflow-y-auto">
          <div className="w-full max-w-2xl mx-auto flex flex-col items-center text-center">
            {/* Brand */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/intodata-logo-hero.png"
              alt="Intodata"
              className="animate-hero-enter h-10 sm:h-12 w-auto dark:brightness-0 dark:invert"
            />

            {/* Headline */}
            <h2
              className="animate-hero-enter mt-6 bg-gradient-to-br from-foreground to-foreground/55 bg-clip-text text-transparent text-3xl sm:text-4xl font-semibold tracking-tight"
              style={{ animationDelay: "0.04s" }}
            >
              What would you like to know?
            </h2>
            <p
              className="animate-hero-enter mt-3 max-w-md text-sm sm:text-[15px] text-muted-foreground"
              style={{ animationDelay: "0.08s" }}
            >
              Ask questions about your documents, explore integrations, or
              deep-dive into project code.
            </p>

            {/* Centered input */}
            <div
              className="animate-hero-enter mt-7 w-full"
              style={{ animationDelay: "0.12s" }}
            >
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
            </div>

            {/* Suggestion pills */}
            <div className="flex flex-wrap items-center justify-center gap-2 px-6">
              {SUGGESTIONS.map((s, i) => {
                const Icon = s.icon;
                return (
                  <button
                    key={s.label}
                    onClick={() => handleSuggestionClick(s.label)}
                    className="animate-hero-enter group inline-flex items-center gap-2 rounded-full border bg-card/70 backdrop-blur-sm px-3.5 py-2 text-xs text-muted-foreground shadow-elevation-xs transition-all hover:-translate-y-px hover:border-primary/40 hover:text-foreground hover:shadow-elevation-sm"
                    style={{ animationDelay: `${0.16 + i * 0.05}s` }}
                  >
                    <Icon className="h-3.5 w-3.5 text-primary/70 transition-colors group-hover:text-primary" />
                    {s.label}
                  </button>
                );
              })}
            </div>

            <p
              className="animate-hero-enter mt-6 text-xs text-muted-foreground/80"
              style={{ animationDelay: "0.4s" }}
            >
              AI can make mistakes. Always review responses before acting on
              them.
            </p>
          </div>
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
          onRetryMessage={isDeepDive ? undefined : onRetryMessage}
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
