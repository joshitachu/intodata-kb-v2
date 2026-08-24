"use client";

import { FolderOpen, Loader2 } from "lucide-react";
import { SelectMenu, type SelectMenuOption } from "@/components/ui/select-menu";
import { useUserProjects } from "@/hooks/useUserProjects";
import { useChatContext } from "@/contexts/ChatContext";

export function RagProjectSelector() {
  const { selectedProject, setSelectedProject } = useChatContext();
  const { data, isLoading, error } = useUserProjects();

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
    return null;
  }

  const options: SelectMenuOption[] = [
    { value: "", label: "All Projects" },
    ...data.projects.map((project) => ({
      value: project.id,
      label: project.display_name,
    })),
  ];

  return (
    <div data-tour="project-selector">
      <SelectMenu
        value={selectedProject}
        onChange={setSelectedProject}
        options={options}
        placeholder="All Projects"
        icon={FolderOpen}
        ariaLabel="Retrieval project scope"
        className="max-w-[12rem]"
      />
    </div>
  );
}
