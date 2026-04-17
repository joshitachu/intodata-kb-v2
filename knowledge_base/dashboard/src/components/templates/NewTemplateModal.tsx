import { useState, useRef } from "react";
import { useCreateTemplate, useCreateVersion } from "../../hooks/useTemplates";

// Only non-policy categories are available for manual creation.
// Policy templates (analysis, orchestration, output) come from the seed script.
const CATEGORIES = [
  { value: "assessment", label: "Assessment" },
  { value: "pattern", label: "Pattern" },
  { value: "platform", label: "Platform" },
];

const ACCEPTED_EXTENSIONS = ".md,.txt,.json,.xml,.yaml,.yml";

function slugify(name: string, category: string): string {
  const kebab = name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${category}/${kebab}`;
}

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (slug: string) => void;
}

export default function NewTemplateModal({ open, onClose, onCreated }: Props) {
  const [mode, setMode] = useState<"file" | "paste">("file");
  const [file, setFile] = useState<File | null>(null);
  const [pastedContent, setPastedContent] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [category, setCategory] = useState("");
  const [platform, setPlatform] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const createTemplate = useCreateTemplate();
  const createVersion = useCreateVersion();
  const isPending = createTemplate.isPending || createVersion.isPending;

  function reset() {
    setMode("file");
    setFile(null);
    setPastedContent("");
    setDisplayName("");
    setCategory("");
    setPlatform("");
    setDescription("");
    setError("");
    if (fileRef.current) fileRef.current.value = "";
  }

  function handleClose() {
    reset();
    onClose();
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (!displayName.trim() || !category) {
      setError("Display name and category are required.");
      return;
    }

    if (mode === "file" && !file) {
      setError("Please select a file.");
      return;
    }

    if (mode === "paste" && !pastedContent.trim()) {
      setError("Please paste some content.");
      return;
    }

    const slug = slugify(displayName, category);

    try {
      const content = mode === "file" ? await file!.text() : pastedContent;

      await createTemplate.mutateAsync({
        slug,
        category,
        display_name: displayName.trim(),
        platform: platform || undefined,
        description: description || undefined,
      });

      await createVersion.mutateAsync({
        slug,
        content,
        changeSummary: mode === "file" ? "Initial version (uploaded)" : "Initial version (pasted)",
      });

      const createdSlug = slug;
      handleClose();
      onCreated(createdSlug);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <form
        onSubmit={handleSubmit}
        className="bg-surface-raised border border-white/[0.08] rounded-lg shadow-xl w-full max-w-md p-6 space-y-4"
      >
        <h2 className="text-sm font-semibold text-slate-200">New Template</h2>

        <div className="flex gap-1 bg-surface-deep rounded p-0.5">
          <button
            type="button"
            onClick={() => setMode("file")}
            className={`flex-1 text-xs py-1.5 rounded transition-colors ${
              mode === "file" ? "bg-white/[0.08] text-slate-200" : "text-slate-500 hover:text-slate-400"
            }`}
          >
            Upload File
          </button>
          <button
            type="button"
            onClick={() => setMode("paste")}
            className={`flex-1 text-xs py-1.5 rounded transition-colors ${
              mode === "paste" ? "bg-white/[0.08] text-slate-200" : "text-slate-500 hover:text-slate-400"
            }`}
          >
            Paste Text
          </button>
        </div>

        {mode === "file" ? (
          <div>
            <label className="block text-xs text-slate-400 mb-1">File *</label>
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPTED_EXTENSIONS}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="block w-full text-sm text-slate-400 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border file:border-white/[0.1] file:bg-surface-deep file:text-slate-300 file:text-xs file:cursor-pointer"
            />
          </div>
        ) : (
          <div>
            <label className="block text-xs text-slate-400 mb-1">Content *</label>
            <textarea
              value={pastedContent}
              onChange={(e) => setPastedContent(e.target.value)}
              placeholder="Paste template content here..."
              className="w-full h-40 bg-surface-deep border border-white/[0.08] rounded p-3 text-sm font-mono text-slate-300 focus:border-accent-primary/40 focus:outline-none resize-y"
              spellCheck={false}
            />
          </div>
        )}

        <div>
          <label className="block text-xs text-slate-400 mb-1">Display Name *</label>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="e.g. Executive Summary"
            className="input-dark w-full"
          />
          {displayName && category && (
            <p className="text-xs text-slate-600 mt-1 font-mono">
              slug: {slugify(displayName, category)}
            </p>
          )}
        </div>

        <div className="flex gap-3">
          <div className="flex-1">
            <label className="block text-xs text-slate-400 mb-1">Category *</label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="input-dark w-full"
            >
              <option value="">Select...</option>
              {CATEGORIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex-1">
            <label className="block text-xs text-slate-400 mb-1">Platform</label>
            <select
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
              className="input-dark w-full"
            >
              <option value="">Any</option>
              <option value="talend">Talend</option>
              <option value="workato">Workato</option>
              <option value="lobster">Lobster</option>
            </select>
          </div>
        </div>

        <div>
          <label className="block text-xs text-slate-400 mb-1">Description</label>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional description"
            className="input-dark w-full"
          />
        </div>

        {error && <p className="text-xs text-red-400">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={handleClose} className="btn-ghost !text-xs" disabled={isPending}>
            Cancel
          </button>
          <button type="submit" className="btn-ghost !text-xs" disabled={isPending}>
            {isPending ? "Creating..." : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}
