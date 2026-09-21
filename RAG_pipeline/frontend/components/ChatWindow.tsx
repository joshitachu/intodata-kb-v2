"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { FolderOpen, FileText, Network, Package } from "lucide-react";
import { MessageList } from "./MessageList";
import { AgentPlanCard } from "./AgentPlanCard";
import { ChatInput } from "./ChatInput";
import { ModeToggle } from "./ModeToggle";
import { ComposerLaneControls } from "./chat/LaneSwitch";
import { ProjectSelector } from "./ProjectSelector";
import { RagProjectSelector } from "./RagProjectSelector";
import { RuntimeStatusIndicator } from "./RuntimeStatusIndicator";
import { useUserProjects } from "@/hooks/useUserProjects";
import { useSlashCommands } from "@/hooks/useSlashCommands";
import { useProgressiveReveal } from "@/hooks/useProgressiveReveal";
import { answerRevealSource } from "@/lib/answerReveal";
import { packWorkbenchHref } from "@/lib/packs";
import { OnboardingLauncher } from "./onboarding/OnboardingLauncher";
import type { UploadedZipReceipt } from "@/lib/mcpClient";
import type { ChatMode } from "./ModeToggle";
import type {
  ConversationDetailResponse,
  ConversationMessageRequest,
} from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";

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
  onWorkflowChanged,
}: ChatWindowProps) {
  const isDeepDive = mode === "deep-dive";
  const inputDisabled = isDeepDive && !selectedProject;
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [packCommandError, setPackCommandError] = useState<string | null>(null);
  const { commands: slashCommands } = useSlashCommands();
  const router = useRouter();

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
  const selectedProjectName = !isDeepDive
    ? userProjectsData?.projects.find((project) => project.id === selectedProject)?.display_name
    : undefined;

  // Same progressive reveal as the docked copilot, over the same components
  // (`useProgressiveReveal`). Both modes stream through the same lane, so the
  // pacing is the same in both.
  const revealSource = useMemo(
    () =>
      answerRevealSource({
        streamState: streamingMessage ?? null,
        messages: conversation?.messages ?? [],
      }),
    [streamingMessage, conversation?.messages],
  );
  const reveal = useProgressiveReveal({
    text: revealSource?.text ?? "",
    turnKey: revealSource?.key ?? null,
    isLive: revealSource?.isLive ?? false,
  });
  const revealedAnswer = revealSource
    ? { key: revealSource.key, text: reveal.text, isRevealing: reveal.isRevealing }
    : null;

  function handleSend(message: string, images?: string[]) {
    // A new question means the previous answer is done being read.
    reveal.complete();
    onSendMessage({ query: message, project_id: selectedProject || undefined, images });
  }

  function handleSlashCommand(name: string, _argumentsText: string): boolean {
    if (name === "onboard" || name === "onboarding") {
      setPackCommandError(null);
      setShowOnboarding(true);
      return true;
    }
    if (name !== "pack" && name !== "packs") return false;
    // /pack opens the workbench. It used to parse a sub-command grammar and
    // pre-stage a draft; authoring now starts from the workbench's own AI
    // author, so the command only has to get the user there.
    setPackCommandError(null);
    setShowOnboarding(false);
    router.push(
      packWorkbenchHref({
        conversationId: conversation?.id,
        projectId: selectedProject || latestPendingAction?.project_id || undefined,
      }),
    );
    return true;
  }

  function handleSuggestionClick(suggestion: string) {
    handleSend(suggestion);
  }

  function handleIntroCtaClick(targetMode: ChatMode) {
    onModeChange(targetMode);
  }

  async function handleZipUploaded(receipt: UploadedZipReceipt) {
    if (!latestPendingAction || !zipUploadTarget || !latestPendingAction.workflow_run_id || !latestPendingAction.interrupt_id) {
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

  const planCard = conversation?.agent_plan ? <AgentPlanCard plan={conversation.agent_plan} /> : null;
  const toolbarContent = (
    <>
      <ModeToggle mode={mode} onModeChange={onModeChange} />
      {isDeepDive ? <ProjectSelector /> : <RagProjectSelector />}
      <RuntimeStatusIndicator />
      {!isDeepDive && (
        <button
          type="button"
          onClick={() => router.push(packWorkbenchHref({ conversationId: conversation?.id, projectId: selectedProject || undefined }))}
          aria-label="Open Pack workbench"
          title="Open Pack workbench"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Package className="h-4 w-4" />
        </button>
      )}
    </>
  );

  // Above the composer, not in its toolbar. The lane is a property of the
  // thread rather than of the turn, and it is immutable once the thread exists,
  // so it is presented once, plainly, where the user is about to commit to it.
  // Rendered in both modes for the same reason: a control that disappeared on
  // toggling to deep dive would be hiding a fact about the thread they are in.
  const composerHeader = <ComposerLaneControls />;

  // Welcome screen - no conversation selected
  if (!conversation && !isLoading) {
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
                headerContent={composerHeader}
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

            {packCommandError && <p role="alert" className="mt-3 w-full border-y border-amber-300 bg-amber-50 py-2 text-left text-sm text-amber-900">{packCommandError}</p>}

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

  // Loading state
  if (isLoading || !conversation) {
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
        {planCard}
        <MessageList
          messages={conversation?.messages ?? []}
          conversationId={conversation?.id ?? null}
          streamingMessage={streamingMessage}
          onRetryMessage={onRetryMessage}
          pendingUserMessage={pendingUserMessage}
          onSuggestionClick={handleSuggestionClick}
          revealedAnswer={revealedAnswer}
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
        <ChatInput
          onSend={handleSend}
          isLoading={isSending}
          disabled={inputDisabled}
          hideImagePicker={isDeepDive}
          toolbarContent={toolbarContent}
          headerContent={composerHeader}
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
        {packCommandError && <p role="alert" className="mx-4 mt-2 border-y border-amber-300 bg-amber-50 py-2 text-sm text-amber-900">{packCommandError}</p>}
        <p className="text-xs text-muted-foreground text-center pb-2">
          AI can make mistakes. Always review responses before acting on them.
        </p>
      </div>
    </div>
  );
}
