"use client";

import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDeepDiveProjects } from "@/hooks/useDeepDiveProjects";

interface ProjectSelectorProps {
  selectedProject: string;
  onProjectChange: (projectId: string) => void;
}

export function ProjectSelector({
  selectedProject,
  onProjectChange,
}: ProjectSelectorProps) {
  const { data, isLoading, error } = useDeepDiveProjects();

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading projects...
      </div>
    );
  }

  if (error) {
    return (
      <p className="text-sm text-destructive">
        Failed to load projects: {error.message}
      </p>
    );
  }

  if (!data || data.projects.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No scanned projects available. Scan a project in the knowledge_base
        platform first.
      </p>
    );
  }

  return (
    <select
      value={selectedProject}
      onChange={(e) => onProjectChange(e.target.value)}
      className={cn(
        "w-full max-w-md rounded-lg border border-input bg-background px-3 py-2",
        "text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
      )}
    >
      <option value="">Select a project...</option>
      {data.projects.map((project) => (
        <option key={project.id} value={project.id}>
          {project.display_name} ({project.job_count} jobs, {project.platform})
        </option>
      ))}
    </select>
  );
}
