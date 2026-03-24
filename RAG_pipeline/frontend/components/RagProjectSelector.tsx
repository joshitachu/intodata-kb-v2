"use client";

import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useUserProjects } from "@/hooks/useUserProjects";

interface RagProjectSelectorProps {
  selectedProject: string;
  onProjectChange: (projectId: string) => void;
}

export function RagProjectSelector({
  selectedProject,
  onProjectChange,
}: RagProjectSelectorProps) {
  const { data, isLoading, error } = useUserProjects();

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>Projects...</span>
      </div>
    );
  }

  if (error) {
    return (
      <span className="text-xs text-destructive">Projects unavailable</span>
    );
  }

  if (!data || data.projects.length === 0) {
    return null;
  }

  return (
    <select
      value={selectedProject}
      onChange={(e) => onProjectChange(e.target.value)}
      className={cn(
        "rounded-lg border border-input bg-background px-3 py-1.5",
        "text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
      )}
    >
      <option value="">All Projects</option>
      {data.projects.map((project) => (
        <option key={project.id} value={project.id}>
          {project.display_name} ({project.platform})
        </option>
      ))}
    </select>
  );
}
