"use client";

import { Loader2, Network } from "lucide-react";
import { SelectMenu, type SelectMenuOption } from "@/components/ui/select-menu";
import { useDeepDiveProjects } from "@/hooks/useDeepDiveProjects";
import { useChatContext } from "@/contexts/ChatContext";

export function ProjectSelector() {
  const { selectedProject, setSelectedProject } = useChatContext();
  const { data, isLoading, error } = useDeepDiveProjects();

  if (isLoading) {
    return (
      <div className="flex h-7 items-center gap-1.5 px-2 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        <span>Projects...</span>
      </div>
    );
  }

  if (error) {
    return (
      <span className="px-2 text-xs text-destructive">Projects unavailable</span>
    );
  }

  if (!data || data.projects.length === 0) {
    return (
      <span className="px-2 text-xs text-muted-foreground">No scanned projects</span>
    );
  }

  // The artifact breakdown is detail for the menu, not the trigger — keeping it
  // out of the label is what stops the toolbar from being pushed out of the composer.
  const options: SelectMenuOption[] = data.projects.map((project) => {
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
