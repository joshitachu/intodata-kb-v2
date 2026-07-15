"use client";

import { useMemo, useState } from "react";
import { FolderOpen, FileText, Network, Package } from "lucide-react";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";
import { ModeToggle } from "./ModeToggle";
import { ProjectSelector } from "./ProjectSelector";
import { RagProjectSelector } from "./RagProjectSelector";
import { RuntimeStatusIndicator } from "./RuntimeStatusIndicator";
import { useUserProjects } from "@/hooks/useUserProjects";
import { useSlashCommands } from "@/hooks/useSlashCommands";
import { PackManager } from "./harness-packs/PackManager";
import { OnboardingLauncher } from "./onboarding/OnboardingLauncher";
import type { UploadedZipReceipt } from "@/lib/mcpClient";
import type { ChatMode } from "./ModeToggle";
import type {
  ConversationDetailResponse,
  ConversationMessageRequest,
} from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";
import type { DeepDiveState } from "@/hooks/useDeepDive";

// Capability categories shown on the empty-state chat. These are NOT
// pre-baked questions -- they describe what each chat mode is for, and
// clicking one switches the mode so the user can type their own query.
interface IntroCta {
  icon: typeof FileText;
  label: string;
  description: string;
  mode: ChatMode;
}

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
  onWorkflowChanged?: () => void;
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
  onWorkflowChanged,
}: ChatWindowProps) {
  const isDeepDive = mode === "deep-dive";
  const inputDisabled = isDeepDive && !selectedProject;
  const [showPacks, setShowPacks] = useState(false);
  const [packMode, setPackMode] = useState<"list" | "select" | "create">("list");
  const [packProjectOverride, setPackProjectOverride] = useState<string | null>(null);
  const [packInitialName, setPackInitialName] = useState<string | undefined>();
  const [packCommandError, setPackCommandError] = useState<string | null>(null);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const { commands: slashCommands } = useSlashCommands();

  const latestPendingAction = useMemo(
    () => [...(conversation?.messages ?? [])]
      .reverse()
      .find((message) => message.pending_action)?.pending_action ?? null,
    [conversation?.messages],
  );
  const zipUploadTarget =
    latestPendingAction?.required_fields.includes("upload_receipt") &&
    latestPendingAction.project_id
      ? {
          projectId: latestPendingAction.project_id,
          platform: latestPendingAction.platform ?? "generic",
        }
      : undefined;
  const { data: userProjectsData } = useUserProjects();
  const contextualPackProjectId = selectedProject || latestPendingAction?.project_id || "";
  const packProjectId = packProjectOverride === null ? contextualPackProjectId : packProjectOverride;
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

  function handleSlashCommand(name: string, argumentsText: string): boolean {
    if (name === "onboard" || name === "onboarding") {
      setShowPacks(false);
      setShowOnboarding(true);
      return true;
    }
    if (name !== "pack" && name !== "packs") return false;
    const parts = argumentsText.split(/\s+/).filter(Boolean);
    const action = parts[0]?.toLowerCase();
    if (action === "create") return false;
    if (!action || action === "list") {
      setPackProjectOverride("");
      setPackInitialName(undefined);
      setPackMode("list");
      setPackCommandError(null);
      setShowOnboarding(false);
      setShowPacks(true);
      return true;
    }
    const useMatch = argumentsText.match(/^use\s+(.+?)\s+for\s+(.+)$/i);
    const requestedPack = useMatch?.[1].trim().replace(/^["']|["']$/g, "");
    const projectQuery = (useMatch?.[2] ?? argumentsText).trim().toLowerCase();
    let resolvedProject = contextualPackProjectId;
    if (projectQuery) {
      const match = userProjectsData?.projects.find((project) => (
        project.id.toLowerCase() === projectQuery || project.display_name.toLowerCase() === projectQuery
      ));
      if (!match) {
        setPackCommandError(`Project “${projectQuery}” is not available in your current scope.`);
        setShowPacks(true);
        return true;
      }
      resolvedProject = match.id;
    } else if (!resolvedProject && userProjectsData?.projects.length === 1) {
      resolvedProject = userProjectsData.projects[0].id;
    }
    setPackProjectOverride(resolvedProject);
    setPackInitialName(requestedPack);
    setPackMode("select");
    setPackCommandError(resolvedProject ? null : "Choose a project before managing its documentation Pack.");
    setShowOnboarding(false);
    setShowPacks(true);
    return true;
  }

  function handleSuggestionClick(suggestion: string) {
    handleSend(suggestion);
  }

  function handleIntroCtaClick(targetMode: ChatMode) {
    onModeChange(targetMode);
  }

  async function handleZipUploaded(receipt: UploadedZipReceipt) {
    if (!latestPendingAction || !zipUploadTarget) {
      throw new Error("The onboarding workflow is no longer waiting for a ZIP source.");
    }
    const response = await fetch(
      `/api/harness/workflows/${encodeURIComponent(latestPendingAction.workflow_run_id)}/resume`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          interrupt_id: latestPendingAction.interrupt_id,
          expected_version: latestPendingAction.expected_version,
          response: receipt.receipt,
        }),
      },
    );
    if (!response.ok) {
      throw new Error("The ZIP was uploaded, but onboarding could not continue.");
    }
    onWorkflowChanged?.();
  }

  // Capability categories shown on the empty-state chat. Two cards only,
  // matching the two chat modes. Edit copy here.
  const INTRO_CTAS: IntroCta[] = [
    {
      icon: FileText,
      label: "Search your documents",
      description: "Ask anything across your uploaded files and project docs.",
      mode: "chat",
    },
    {
      icon: Network,
      label: "Deep-dive a project",
      description: "Inspect a scanned repo's code, integrations, and data flows.",
      mode: "deep-dive",
    },
  ];

  const toolbarContent = (
    <>
      <ModeToggle mode={mode} onModeChange={onModeChange} />
      {isDeepDive ? <ProjectSelector /> : <RagProjectSelector />}
      <RuntimeStatusIndicator />
      {!isDeepDive && (
        <button
          type="button"
          onClick={() => {
            setPackProjectOverride(null);
            setPackInitialName(undefined);
            setPackMode(contextualPackProjectId ? "select" : "list");
            setShowOnboarding(false);
            setShowPacks((value) => !value);
          }}
          aria-label="Documentation Pack"
          aria-expanded={showPacks}
          title="Documentation Pack"
          className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Package className="h-4 w-4" />
        </button>
      )}
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

        <div className="relative flex-1 flex flex-col items-center justify-center px-4 py-16 overflow-y-auto">
          <div className="w-full max-w-2xl mx-auto flex flex-col items-center text-center">
            {/* Brand wordmark */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/intodata-logo-hero.png"
              alt="Intodata"
              className="animate-hero-enter h-10 sm:h-12 w-auto dark:brightness-0 dark:invert"
            />

            {/* Centered input */}
            <div
              className="animate-hero-enter mt-10 w-full"
              style={{ animationDelay: "0.04s" }}
            >
              <ChatInput
                onSend={handleSend}
                isLoading={false}
                disabled={inputDisabled}
                hideImagePicker={isDeepDive}
                toolbarContent={toolbarContent}
                slashCommands={slashCommands}
                onSlashCommand={handleSlashCommand}
                zipUploadTarget={zipUploadTarget}
                onZipUploaded={zipUploadTarget ? handleZipUploaded : undefined}
                placeholder={
                  isDeepDive
                    ? selectedProject
                      ? "Ask a question about the project..."
                      : "Select a project to start deep dive..."
                    : "Ask about your documents..."
                }
              />
            </div>

            {showOnboarding && (
              <div className="mt-5 max-h-[50vh] w-full overflow-y-auto border-y py-4 text-left">
                <OnboardingLauncher
                  onStart={(message) => {
                    setShowOnboarding(false);
                    handleSend(message);
                  }}
                />
              </div>
            )}
            {showPacks && (
              <div className="mt-5 max-h-[45vh] w-full overflow-y-auto border-y py-4 text-left">
                {packCommandError && <p role="alert" className="text-sm text-destructive">{packCommandError}</p>}
                <PackManager
                  key={`${packProjectId}:${packMode}:${packInitialName ?? ""}`}
                  projectId={packProjectId || undefined}
                  initialMode={packMode}
                  initialPackName={packInitialName}
                />
              </div>
            )}

            {/* Two large CTA cards (Perplexity-style) -- capability categories,
                not pre-baked questions. Click switches chat mode. */}
            <div
              className="animate-hero-enter mt-6 grid grid-cols-1 sm:grid-cols-2 gap-3 w-full"
              style={{ animationDelay: "0.1s" }}
            >
              {INTRO_CTAS.map((cta) => {
                const Icon = cta.icon;
                const isActive = mode === cta.mode;
                return (
                  <button
                    key={cta.label}
                    onClick={() => handleIntroCtaClick(cta.mode)}
                    className={`group text-left rounded-2xl border px-5 py-4 transition-all hover:-translate-y-px hover:shadow-elevation-sm ${
                      isActive
                        ? "border-primary/50 bg-primary/[0.04]"
                        : "bg-card/60 hover:bg-card/80 hover:border-primary/40"
                    }`}
                  >
                    <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                      <Icon className="h-4 w-4 text-primary/70 transition-colors group-hover:text-primary" />
                      {cta.label}
                    </div>
                    <p className="mt-1.5 text-xs text-muted-foreground leading-snug">
                      {cta.description}
                    </p>
                  </button>
                );
              })}
            </div>
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
        {showOnboarding && (
          <div className="max-h-[55vh] overflow-y-auto border-t px-4 py-4">
            <OnboardingLauncher
              onStart={(message) => {
                setShowOnboarding(false);
                handleSend(message);
              }}
            />
          </div>
        )}
        {showPacks && (
          <div className="max-h-[45vh] overflow-y-auto border-t px-4 py-4">
            {packCommandError && <p role="alert" className="text-sm text-destructive">{packCommandError}</p>}
            <PackManager
              key={`${packProjectId}:${packMode}:${packInitialName ?? ""}`}
              projectId={packProjectId || undefined}
              initialMode={packMode}
              initialPackName={packInitialName}
            />
          </div>
        )}
        <ChatInput
          onSend={handleSend}
          isLoading={isSending || (deepDiveState?.isStreaming ?? false)}
          disabled={inputDisabled}
          hideImagePicker={isDeepDive}
          toolbarContent={toolbarContent}
          slashCommands={slashCommands}
          onSlashCommand={handleSlashCommand}
          zipUploadTarget={zipUploadTarget}
          onZipUploaded={zipUploadTarget ? handleZipUploaded : undefined}
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
