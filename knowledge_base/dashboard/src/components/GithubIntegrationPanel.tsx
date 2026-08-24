import type { UseMutationResult } from "@tanstack/react-query";
import { parseApiError } from "../lib/errors";
import type { Project } from "../types";

interface Props {
  project: Project;
  newToken: string;
  onNewTokenChange: (value: string) => void;
  registerWebhook: UseMutationResult<unknown, Error, string, unknown>;
  updateGithubToken: UseMutationResult<unknown, Error, { projectId: string; token: string }, unknown>;
}

export default function GithubIntegrationPanel({
  project,
  newToken,
  onNewTokenChange,
  registerWebhook,
  updateGithubToken,
}: Props) {
  // App-connected projects authenticate via a GitHub App installation: no PAT to
  // store and webhooks are delivered at the App level, so the token/webhook
  // controls below do not apply.
  if (project.connection_kind === "app") {
    return (
      <div className="bg-surface-raised rounded border border-white/[0.07] p-3">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-emerald-400 flex-shrink-0" />
          <span className="text-sm text-emerald-400">
            Connected via GitHub App{project.github_account_login ? ` (${project.github_account_login})` : ""}
            {" "}— no token stored, access managed in GitHub
          </span>
        </div>
      </div>
    );
  }

  // Azure DevOps projects resolve their credential from an organization-level
  // connection, not from a per-project column. The controls below call the GitHub
  // API, so offering them here would only produce failures.
  if (project.connection_kind === "azure_devops") {
    return (
      <div className="bg-surface-raised rounded border border-white/[0.07] p-3">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-emerald-400 flex-shrink-0" />
          <span className="text-sm text-emerald-400">
            Connected via Azure DevOps{project.github_account_login ? ` (${project.github_account_login})` : ""}
            {" "}— token held on the organization connection, rotate it there
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-surface-raised rounded border border-white/[0.07] p-3 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {project.webhook_status === "active" ? (
            <>
              <span className="w-2 h-2 rounded-full bg-emerald-400 flex-shrink-0" />
              <span className="text-sm text-emerald-400">Webhook active — pushes trigger instant updates</span>
            </>
          ) : project.webhook_status === "not_registered" ? (
            <>
              <span className="w-2 h-2 rounded-full bg-amber-400 flex-shrink-0" />
              <span className="text-sm text-amber-400">Webhook not set up — using twice-daily poller</span>
            </>
          ) : (
            <>
              <span className="w-2 h-2 rounded-full bg-slate-500 flex-shrink-0" />
              <span className="text-sm text-slate-500">No webhook info</span>
            </>
          )}
        </div>
        {project.webhook_status === "not_registered" && project.has_github_token && (
          <button
            type="button"
            className="btn-dark text-xs px-2 py-1 flex-shrink-0"
            disabled={registerWebhook.isPending}
            onClick={() => registerWebhook.mutate(project.id)}
          >
            {registerWebhook.isPending ? "Registering..." : "Set up webhook"}
          </button>
        )}
      </div>
      <div className="flex items-end gap-2">
        <div className="flex-1 min-w-0">
          <label className="block text-xs text-slate-500 mb-1">
            {project.has_github_token ? "Update GitHub Token" : "Add GitHub Token"}
          </label>
          <input
            type="password"
            className="input-dark text-sm"
            placeholder={project.has_github_token ? "Enter new token to replace" : "ghp_..."}
            value={newToken}
            onChange={(e) => onNewTokenChange(e.target.value)}
            autoComplete="new-password"
          />
          <p className="text-xs text-slate-600 mt-1">Required scopes: <code className="font-mono">repo</code></p>
        </div>
        <button
          type="button"
          className="btn-dark text-xs px-3 py-2 flex-shrink-0 disabled:opacity-40"
          disabled={!newToken || updateGithubToken.isPending}
          onClick={() => {
            updateGithubToken.mutate({ projectId: project.id, token: newToken }, {
              onSuccess: () => onNewTokenChange(""),
            });
          }}
        >
          {updateGithubToken.isPending ? "Saving..." : "Save"}
        </button>
      </div>
      {(registerWebhook.isError || updateGithubToken.isError) && (
        <p className="text-xs text-red-400 font-mono">
          {parseApiError(registerWebhook.error ?? updateGithubToken.error)}
        </p>
      )}
    </div>
  );
}
