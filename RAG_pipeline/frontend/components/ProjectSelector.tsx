"use client";

import { Network } from "lucide-react";
import { SelectMenu, type SelectMenuOption } from "@/components/ui/select-menu";
import { SelectorFailed, SelectorPending } from "@/components/ui/selector-status";
import { queryOutcome } from "@/lib/queryState";
import { useDeepDiveProjects } from "@/hooks/useDeepDiveProjects";
import { useChatContext } from "@/contexts/ChatContext";

export function ProjectSelector() {
  const { selectedProject, setSelectedProject } = useChatContext();
  const query = useDeepDiveProjects();
  const outcome = queryOutcome(query, "Could not load your scanned projects");

  // Same correction as DocsProjectSelector: "No scanned projects" is a
  // statement about the account and may only be made from a real answer.
  if (outcome.kind === "unknown") return <SelectorPending phase={outcome.phase} />;
  if (outcome.kind === "failed") {
    return <SelectorFailed message={outcome.message} onRetry={() => void query.refetch()} />;
  }

  if (outcome.data.projects.length === 0) {
    return (
      <span className="px-2 text-xs text-muted-foreground">No scanned projects</span>
    );
  }

  // The artifact breakdown is detail for the menu, not the trigger — keeping it
  // out of the label is what stops the toolbar from being pushed out of the composer.
  const options: SelectMenuOption[] = outcome.data.projects.map((project) => {
    const counts = project.artifact_counts;
    const hint =
      project.platform.toLowerCase() === "talend" && counts
        ? `${counts.total_artifacts ?? project.job_count} designs · ${counts.job_count ?? 0} jobs · ${counts.route_count ?? 0} routes`
        : `${project.job_count} components`;
    return { value: project.id, label: project.display_name, hint };
  });

  return (
    <SelectMenu
      value={selectedProject}
      onChange={setSelectedProject}
      options={options}
      placeholder="Select a project"
      icon={Network}
      ariaLabel="Deep dive project"
      className="max-w-[12rem]"
    />
  );
}
