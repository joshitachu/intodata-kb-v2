import type { Project } from "../types";
import { getPlatform } from "../lib/platform";
import { timeAgo, formatAbsoluteDate } from "../lib/timeago";
import ActionButtons from "./ActionButtons";
import StatusBadge from "./StatusBadge";

interface Props {
  project: Project;
  isActive: boolean;
  onSelect: (id: string) => void;
  onAction: (id: string) => void;
  canManageProjects: boolean;
}

export default function ProjectCard({
  project,
  isActive,
  onSelect,
  onAction,
  canManageProjects,
}: Props) {
  const platform = getPlatform(project.platform);

  return (
    <div
      className={`
        project-card relative bg-[#111118] rounded border border-white/[0.07] border-l-4
        ${platform.accentColor} cursor-pointer
        ${isActive ? "ring-1 ring-[#00D4FF]/30 shadow-card-hover" : "shadow-card"}
      `}
      onClick={() => onSelect(project.id)}
    >
      {/* Header */}
      <div className="px-4 pt-4 pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-slate-100 truncate">
              {project.display_name}
            </h3>
            <p className="text-xs font-mono text-slate-600 mt-0.5">{project.id}</p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <span className={platform.badgeClasses}>{platform.label}</span>
            <StatusBadge status={project.status} />
          </div>
        </div>

        {/* Stats row */}
        <div className="flex items-center gap-1.5 mt-3 text-xs text-slate-500 font-mono">
          <span className="text-slate-300">{project.job_count}</span>
          <span>{platform.termPlural}</span>
          <span className="text-slate-700">&middot;</span>
          <span className="text-slate-300">{project.doc_count}</span>
          <span>Docs</span>
          <span className="text-slate-700">&middot;</span>
          <span title={formatAbsoluteDate(project.last_scan_at)}>
            {timeAgo(project.last_scan_at)}
          </span>
        </div>

        {/* Error message */}
        {project.error_message && (
          <p
            className="text-xs text-red-400/80 font-mono mt-2 truncate"
            title={project.error_message}
          >
            {project.error_message}
          </p>
        )}
      </div>

      {/* Footer with actions */}
      {canManageProjects && (
        <div className="px-4 py-2.5">
          <ActionButtons
            projectId={project.id}
            status={project.status}
            onAction={onAction}
          />
        </div>
      )}
    </div>
  );
}
