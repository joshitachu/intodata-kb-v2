import type { UseMutationResult } from "@tanstack/react-query";
import { parseApiError } from "../lib/errors";

interface Props {
  projectId: string;
  confirmText: string;
  onConfirmTextChange: (value: string) => void;
  deleteProject: UseMutationResult<unknown, Error, string, unknown>;
  onDeleted?: () => void;
}

export default function DangerZone({
  projectId,
  confirmText,
  onConfirmTextChange,
  deleteProject,
  onDeleted,
}: Props) {
  return (
    <div className="border border-red-500/30 rounded-lg p-4 space-y-3">
      <h4 className="font-mono uppercase tracking-wider text-xs text-red-400">Danger Zone</h4>
      <p className="text-sm text-slate-400">
        Permanently delete this project and all associated data including scan results,
        generated docs, and RAG documents. This action cannot be undone.
      </p>
      <div className="flex flex-col sm:flex-row items-start sm:items-end gap-3">
        <div className="flex-1 w-full sm:w-auto">
          <label className="block text-xs text-slate-500 mb-1">
            Type <span className="font-mono text-red-400">{projectId}</span> to confirm
          </label>
          <input
            type="text"
            value={confirmText}
            onChange={(e) => onConfirmTextChange(e.target.value)}
            placeholder={projectId}
            className="input-dark w-full sm:w-64"
            disabled={deleteProject.isPending}
          />
        </div>
        <button
          onClick={() => {
            if (confirmText !== projectId) return;
            deleteProject.mutate(projectId, {
              onSuccess: () => {
                onConfirmTextChange("");
                onDeleted?.();
              },
            });
          }}
          disabled={confirmText !== projectId || deleteProject.isPending}
          className={`px-4 py-2 text-sm rounded font-medium transition-all whitespace-nowrap ${
            confirmText === projectId && !deleteProject.isPending
              ? "bg-red-600 text-white hover:bg-red-500"
              : "bg-white/[0.05] text-slate-600 cursor-not-allowed"
          }`}
        >
          {deleteProject.isPending ? "Deleting..." : "Permanently Delete"}
        </button>
      </div>
      {deleteProject.isError && (
        <p className="text-sm text-red-400">
          Failed: {parseApiError(deleteProject.error)}
        </p>
      )}
    </div>
  );
}
