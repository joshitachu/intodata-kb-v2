"use client";

import { ChevronDown, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDeepDiveProjects } from "@/hooks/useDeepDiveProjects";
import { useChatContext } from "@/contexts/ChatContext";

export function ProjectSelector() {
  const { selectedProject, setSelectedProject } = useChatContext();
  const { data, isLoading, error } = useDeepDiveProjects();

  if (isLoading) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground px-2 py-1">
        <Loader2 className="h-3 w-3 animate-spin" />
        <span>Projects...</span>
      </div>
    );
  }

  if (error) {
    return (
      <span className="text-xs text-destructive px-2">Projects unavailable</span>
    );
  }

  if (!data || data.projects.length === 0) {
    return (
      <span className="text-xs text-muted-foreground px-2">No scanned projects</span>
    );
  }

  return (
    <div className="relative">
      <select
        value={selectedProject}
        onChange={(e) => setSelectedProject(e.target.value)}
        className={cn(
          "appearance-none cursor-pointer",
          "rounded-full border border-input bg-muted/40 pl-3 pr-7 py-1",
          "text-xs text-muted-foreground hover:text-foreground hover:bg-muted",
          "focus:outline-none focus:ring-1 focus:ring-ring",
          "transition-colors",
        )}
      >
        <option value="">Select a project...</option>
        {data.projects.map((project) => (
          <option key={project.id} value={project.id}>
            {project.display_name} ({project.job_count} components)
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
    </div>
  );
}
