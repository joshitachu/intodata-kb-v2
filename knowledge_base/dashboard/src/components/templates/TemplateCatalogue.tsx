import { useState } from "react";
import { useTemplates } from "../../hooks/useTemplates";
import type { Template } from "../../types";

const CATEGORIES = [
  { value: "", label: "All" },
  { value: "analysis", label: "Analysis" },
  { value: "assessment", label: "Assessment" },
  { value: "output", label: "Output" },
  { value: "orchestration", label: "Orchestration" },
  { value: "pattern", label: "Pattern" },
  { value: "platform", label: "Platform" },
];

interface Props {
  onSelect: (slug: string) => void;
  selectedSlug: string | null;
}

export default function TemplateCatalogue({ onSelect, selectedSlug }: Props) {
  const [categoryFilter, setCategoryFilter] = useState("");
  const [platformFilter, setPlatformFilter] = useState("");
  const [search, setSearch] = useState("");

  const { data, isLoading } = useTemplates({
    category: categoryFilter || undefined,
    platform: platformFilter || undefined,
  });

  const templates = (data?.templates ?? []).filter((t) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      t.slug.toLowerCase().includes(q) ||
      t.display_name.toLowerCase().includes(q) ||
      (t.description ?? "").toLowerCase().includes(q)
    );
  });

  const grouped = templates.reduce<Record<string, Template[]>>((acc, t) => {
    (acc[t.category] ??= []).push(t);
    return acc;
  }, {});

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <input
          type="text"
          placeholder="Search templates..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="input-dark flex-1 min-w-[200px]"
        />
        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="input-dark w-auto"
        >
          {CATEGORIES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
        <select
          value={platformFilter}
          onChange={(e) => setPlatformFilter(e.target.value)}
          className="input-dark w-auto"
        >
          <option value="">All Platforms</option>
          <option value="talend">Talend</option>
          <option value="workato">Workato</option>
        </select>
      </div>

      {isLoading ? (
        <div className="p-6 text-sm text-slate-500">Loading templates...</div>
      ) : templates.length === 0 ? (
        <div className="p-6 text-sm text-slate-500 font-mono">
          No templates found. Run the seed script to populate.
        </div>
      ) : (
        <div className="space-y-4">
          {Object.entries(grouped)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([category, items]) => (
              <div key={category}>
                <h3 className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-2">
                  {category}
                  <span className="ml-2 text-slate-600">({items.length})</span>
                </h3>
                <div className="grid gap-1">
                  {items.map((t) => (
                    <button
                      key={t.slug}
                      onClick={() => onSelect(t.slug)}
                      className={`text-left px-3 py-2 rounded border transition-all ${
                        selectedSlug === t.slug
                          ? "border-accent-primary/40 bg-accent-primary/5 text-slate-200"
                          : "border-white/[0.06] hover:border-white/[0.12] text-slate-400 hover:text-slate-300"
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium">{t.display_name}</span>
                        <div className="flex items-center gap-2">
                          {t.platform && (
                            <span className="text-xs px-1.5 py-0.5 border border-white/[0.1] rounded text-slate-500">
                              {t.platform}
                            </span>
                          )}
                          {t.latest_version != null && (
                            <span className="text-xs text-slate-600 font-mono">
                              v{t.latest_version}
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="text-xs text-slate-600 font-mono mt-0.5">{t.slug}</div>
                    </button>
                  ))}
                </div>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
