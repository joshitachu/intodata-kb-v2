import { useState, useEffect } from "react";
import { useVersion, useVersions, useCreateVersion } from "../../hooks/useTemplates";

interface Props {
  slug: string;
}

export default function TemplateEditor({ slug }: Props) {
  const { data: versionsData } = useVersions(slug);
  const versions = versionsData?.versions ?? [];
  const latestNum = versions.length > 0 ? versions[0].version_number : null;

  const [viewingVersion, setViewingVersion] = useState<number | null>(null);
  const activeVersion = viewingVersion ?? latestNum;

  const { data: versionData, isLoading } = useVersion(slug, activeVersion);
  const createVersion = useCreateVersion();

  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState("");
  const [commitMsg, setCommitMsg] = useState("");

  useEffect(() => {
    if (versionData?.content) {
      setContent(versionData.content);
    }
  }, [versionData]);

  // Reset when slug changes
  useEffect(() => {
    setEditing(false);
    setViewingVersion(null);
    setCommitMsg("");
  }, [slug]);

  async function handleSave() {
    if (!commitMsg.trim()) return;
    await createVersion.mutateAsync({
      slug,
      content,
      changeSummary: commitMsg,
    });
    setEditing(false);
    setCommitMsg("");
    setViewingVersion(null);
  }

  return (
    <div className="card-dark">
      {/* Header */}
      <div className="px-4 py-3 border-b border-white/[0.05] bg-surface-raised rounded-t flex items-center justify-between">
        <div>
          <h3 className="text-sm font-mono text-slate-300">{slug}</h3>
          {activeVersion != null && (
            <span className="text-xs text-slate-500">
              v{activeVersion}
              {versionData?.change_summary && ` -- ${versionData.change_summary}`}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* Version selector */}
          {versions.length > 0 && (
            <select
              value={activeVersion ?? ""}
              onChange={(e) => {
                setViewingVersion(Number(e.target.value));
                setEditing(false);
              }}
              className="input-dark !w-auto text-xs"
            >
              {versions.map((v) => (
                <option key={v.version_number} value={v.version_number}>
                  v{v.version_number} - {v.change_summary.slice(0, 40)}
                </option>
              ))}
            </select>
          )}

          {!editing ? (
            <button
              className="btn-ghost !text-xs"
              onClick={() => setEditing(true)}
            >
              Edit
            </button>
          ) : (
            <button
              className="text-xs text-slate-500 hover:text-slate-300 px-2 py-1"
              onClick={() => {
                setEditing(false);
                if (versionData?.content) setContent(versionData.content);
                setCommitMsg("");
              }}
            >
              Cancel
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="p-4 text-sm text-slate-500">Loading...</div>
      ) : (
        <div className="p-4">
          {editing ? (
            <div className="space-y-3">
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                className="w-full h-[500px] bg-surface-deep border border-white/[0.08] rounded p-3 text-sm font-mono text-slate-300 focus:border-accent-primary/40 focus:outline-none resize-y"
                spellCheck={false}
              />
              <div className="flex items-center gap-3">
                <input
                  type="text"
                  placeholder="Commit message (required)"
                  value={commitMsg}
                  onChange={(e) => setCommitMsg(e.target.value)}
                  className="input-dark flex-1"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && commitMsg.trim()) handleSave();
                  }}
                />
                <button
                  className="btn-ghost"
                  disabled={!commitMsg.trim() || createVersion.isPending}
                  onClick={handleSave}
                >
                  {createVersion.isPending ? "Saving..." : "Save Version"}
                </button>
              </div>
              {createVersion.isError && (
                <p className="text-xs text-red-400">
                  Error: {(createVersion.error as Error).message}
                </p>
              )}
            </div>
          ) : (
            <pre className="w-full h-[500px] overflow-auto bg-surface-deep border border-white/[0.06] rounded p-3 text-sm font-mono text-slate-400 whitespace-pre-wrap">
              {versionData?.content ?? "No content"}
            </pre>
          )}
        </div>
      )}

      {/* Version metadata */}
      {versionData && !editing && (
        <div className="px-4 pb-3 flex items-center gap-4 text-xs text-slate-600">
          {versionData.created_by && <span>by {versionData.created_by}</span>}
          {versionData.byte_size != null && (
            <span>{(versionData.byte_size / 1024).toFixed(1)}KB</span>
          )}
          {versionData.created_at && (
            <span>{new Date(versionData.created_at).toLocaleString()}</span>
          )}
          {versionData.content_hash && (
            <span className="font-mono">{versionData.content_hash.slice(0, 8)}</span>
          )}
        </div>
      )}
    </div>
  );
}
