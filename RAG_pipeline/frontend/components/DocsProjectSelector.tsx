"use client";

import { BookOpen, Loader2 } from "lucide-react";
import { SelectMenu, type SelectMenuOption } from "@/components/ui/select-menu";
import { useDocsProjects } from "@/hooks/useDocsProjects";
import { useChatContext } from "@/contexts/ChatContext";

export function DocsProjectSelector() {
  const { selectedProject, setSelectedProject } = useChatContext();
  const { data, isLoading, error } = useDocsProjects();

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
      <span className="px-2 text-xs text-muted-foreground">No projects available</span>
    );
  }

  const options: SelectMenuOption[] = data.projects.map((project) => ({
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
