import { useProjects } from "../hooks/useProjects";
import type { Project } from "../types";
import ProjectCard from "./ProjectCard";

interface Props {
  onAction: (projectId: string) => void;
  activeProject?: string | null;
  onSelect: (projectId: string) => void;
  canManageProjects?: boolean;
}

export default function ProjectList({
  onAction,
  activeProject,
  onSelect,
  canManageProjects = true,
}: Props) {
  const { data, isLoading, error } = useProjects();

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className="card-dark h-32 animate-pulse"
          />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="card-dark p-6">
        <p className="text-red-400 text-sm font-mono">
          Failed to load projects: {(error as Error).message}
        </p>
      </div>
    );
  }

  const projects: Project[] = data?.projects ?? [];

  if (projects.length === 0) {
    return (
      <div className="card-dark p-8 text-center">
        <p className="text-slate-500 text-sm font-mono">
          No projects registered. Click "Add Repo" to get started.
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
      {projects.map((p) => (
        <ProjectCard
          key={p.id}
          project={p}
          isActive={activeProject === p.id}
          onSelect={onSelect}
          onAction={onAction}
          canManageProjects={canManageProjects}
        />
      ))}
    </div>
  );
}
