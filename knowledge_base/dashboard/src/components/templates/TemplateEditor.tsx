import { useState, useEffect, useRef } from "react";
import { useVersion, useVersions, useCreateVersion, useTemplate } from "../../hooks/useTemplates";

interface Props {
  slug: string;
}

export default function TemplateEditor({ slug }: Props) {
  const { data: templateData } = useTemplate(slug);
  const isPolicy = templateData?.tier === "policy";

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
  const uploadRef = useRef<HTMLInputElement>(null);

  async function handleUploadFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    setContent(text);
    setEditing(true);
    if (uploadRef.current) uploadRef.current.value = "";
  }

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
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-mono text-slate-300">{slug}</h3>
            {templateData?.tier && (
              <span className={`text-[10px] px-1.5 py-0.5 border rounded font-medium ${
                templateData.tier === "policy"
                  ? "border-amber-500/30 bg-amber-500/10 text-amber-400"
                  : templateData.tier === "addon"
                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                    : "border-sky-500/30 bg-sky-500/10 text-sky-400"
              }`}>
                {templateData.tier}
              </span>
            )}
          </div>
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

          {isPolicy ? (
            <span className="flex items-center gap-1.5 text-xs text-amber-400/70 px-2 py-1">
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
              Managed via source control
            </span>
          ) : !editing ? (
            <>
              <button
                className="btn-ghost !text-xs"
                onClick={() => uploadRef.current?.click()}
              >
                Upload
              </button>
              <input
                ref={uploadRef}
                type="file"
                accept=".md,.txt,.json,.xml,.yaml,.yml"
                className="hidden"
                onChange={handleUploadFile}
              />
              <button
                className="btn-ghost !text-xs"
                onClick={() => setEditing(true)}
              >
                Edit
              </button>
            </>
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
