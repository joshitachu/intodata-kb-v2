"use client";

import { BookOpen } from "lucide-react";
import { SelectMenu, type SelectMenuOption } from "@/components/ui/select-menu";
import { SelectorFailed, SelectorPending } from "@/components/ui/selector-status";
import { queryOutcome } from "@/lib/queryState";
import { useDocsProjects } from "@/hooks/useDocsProjects";
import { useChatContext } from "@/contexts/ChatContext";

export function DocsProjectSelector() {
  const { selectedProject, setSelectedProject } = useChatContext();
  const query = useDocsProjects();
  const outcome = queryOutcome(query, "Could not load your projects");

  // "No projects available" is a claim about the account, so it may only be
  // made from a successful response. Anything not-yet-known (pending, paused,
  // waiting on a retry backoff) renders the pending affordance; a failure
  // renders as a failure. Branching on `isLoading` instead of on the outcome
  // is what let a *paused* query — data undefined, error null, isLoading false
  // because a paused retryer is not fetching — fall through to the empty state
  // and tell the user they had no projects until they pressed F5.
  if (outcome.kind === "unknown") return <SelectorPending phase={outcome.phase} />;
  if (outcome.kind === "failed") {
    return <SelectorFailed message={outcome.message} onRetry={() => void query.refetch()} />;
  }

  if (outcome.data.projects.length === 0) {
    return (
      <span className="px-2 text-xs text-muted-foreground">No projects available</span>
    );
  }

  const options: SelectMenuOption[] = outcome.data.projects.map((project) => ({
    value: project.id,
    label: project.display_name,
    hint: `${project.doc_count} ${project.doc_count === 1 ? "doc" : "docs"}`,
  }));

  return (
    <SelectMenu
      value={selectedProject}
      onChange={setSelectedProject}
      options={options}
      placeholder="Select a project"
      icon={BookOpen}
      ariaLabel="Documentation project"
      className="max-w-[14rem]"
    />
  );
}
