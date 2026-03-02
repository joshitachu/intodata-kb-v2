"use client";

import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDocsProjects } from "@/hooks/useDocsProjects";

interface DocsProjectSelectorProps {
  selectedProject: string;
  onProjectChange: (projectId: string) => void;
}

export function DocsProjectSelector({
  selectedProject,
  onProjectChange,
}: DocsProjectSelectorProps) {
  const { data, isLoading, error } = useDocsProjects();

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
        No projects with documentation found. Generate docs in the knowledge
        base platform first.
      </p>
    );
  }

  return (
    <select
      value={selectedProject}
      onChange={(e) => onProjectChange(e.target.value)}
      className={cn(
        "w-full rounded-lg border border-input bg-background px-3 py-2",
        "text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
      )}
    >
      <option value="">Select a project...</option>
      {data.projects.map((project) => (
        <option key={project.id} value={project.id}>
          {project.display_name} ({project.doc_count} docs, {project.platform})
        </option>
      ))}
    </select>
  );
}
