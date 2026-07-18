"use client";

import { useEffect, useState } from "react";
import { Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { StylePack, StylePackRequest } from "@/types";

interface StylePackEditorProps {
  projectId?: string;
  privatePacks: StylePack[];
  projectPacks: StylePack[];
  onCreatePrivate: (payload: StylePackRequest) => Promise<void>;
  onSavePrivateVersion: (packId: string, payload: StylePackRequest) => Promise<void>;
  onCreateProject: (payload: StylePackRequest) => Promise<void>;
  onSaveProjectVersion: (packId: string, payload: StylePackRequest) => Promise<void>;
}

type Scope = "private" | "project";
interface PresentationForm {
  audience: string | null;
  tone: string | null;
  technical_depth: string | null;
  output_organization: string | null;
  formatting: string[];
  communication: string[];
}
const emptyPresentation: PresentationForm = { audience: null, tone: null, technical_depth: null, output_organization: null, formatting: [], communication: [] };
const options = {
  formatting: [["markdown", "Markdown"], ["bulleted_lists", "Bulleted lists"], ["tables", "Tables"], ["code_blocks", "Code blocks"]],
  communication: [["qualify_uncertainty", "Qualify uncertainty"], ["action_oriented", "Action oriented"], ["include_sources", "Include sources"]],
} as const;
const MAX_STYLE_SLUG_LENGTH = 48;

function nextVersion(version: string) {
  const [major = "1", minor = "0", patch = "0"] = version.split(".");
  return `${major}.${minor}.${Number(patch.replace(/[^0-9].*$/, "")) + 1}`;
}

export function StylePackEditor(props: StylePackEditorProps) {
  const [scope, setScope] = useState<Scope>("private");
  const [selectedId, setSelectedId] = useState("");
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [presentation, setPresentation] = useState(emptyPresentation);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const packs = scope === "private" ? props.privatePacks : props.projectPacks;

  useEffect(() => {
    const selected = packs.find((pack) => pack.pack.id === selectedId);
    if (!selected) return;
    setName(selected.pack.name);
    setVersion(nextVersion(selected.pack.version));
    setPresentation({ ...emptyPresentation, ...selected.presentation, formatting: [...selected.presentation.formatting], communication: [...selected.presentation.communication] });
  }, [packs, selectedId]);

  function reset() {
    setSelectedId(""); setName(""); setVersion("1.0.0"); setPresentation(emptyPresentation); setError(null);
  }
  function setOption(field: "formatting" | "communication", value: string) {
    setPresentation((current) => ({ ...current, [field]: current[field].includes(value) ? current[field].filter((item) => item !== value) : [...current[field], value] }));
  }
  async function save() {
    if (!name.trim()) { setError("Name is required."); return; }
    if (!presentation.audience && !presentation.tone && !presentation.technical_depth && !presentation.output_organization && !presentation.formatting.length && !presentation.communication.length) { setError("Choose at least one presentation setting."); return; }
    const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, ".").replace(/^\.+|\.+$/g, "").slice(0, MAX_STYLE_SLUG_LENGTH);
    const id = selectedId || `style.${slug}`;
    if (!selectedId && !/^style\.[a-z][a-z0-9.-]{0,47}$/.test(id)) { setError("Use a name that produces a valid Style Pack identifier."); return; }
    const payload = { id, name: name.trim(), version, presentation } as StylePackRequest;
    setSaving(true); setError(null);
    try {
      if (scope === "private") {
        if (selectedId) await props.onSavePrivateVersion(selectedId, payload); else await props.onCreatePrivate(payload);
      } else {
        if (selectedId) await props.onSaveProjectVersion(selectedId, payload); else await props.onCreateProject(payload);
      }
      reset();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Style Pack could not be saved."); }
    finally { setSaving(false); }
  }

  return <section className="space-y-4" aria-labelledby="style-editor-heading">
    <div><h2 id="style-editor-heading" className="text-base font-semibold">Style / Presentation</h2><p className="mt-1 text-sm text-muted-foreground">Style Packs affect audience, tone, depth, organization, formatting, and communication only. They cannot change evidence, policy, permissions, semantics, or capabilities.</p></div>
    <div className="flex flex-wrap items-center gap-2" aria-label="Style Pack scope">
      <Button size="sm" variant={scope === "private" ? "primary" : "secondary"} onClick={() => { setScope("private"); reset(); }}>Private reusable</Button>
      <Button size="sm" variant={scope === "project" ? "primary" : "secondary"} disabled={!props.projectId} onClick={() => { setScope("project"); reset(); }}>Project scoped</Button>
      {scope === "project" && <span className="text-xs text-muted-foreground">Visible only to this project.</span>}
    </div>
    {packs.length > 0 && <div className="divide-y border-y border-border" aria-label={`${scope} Style Packs`}>
      {packs.map((pack) => <div key={pack.pack.id} className="flex flex-wrap items-center justify-between gap-x-5 gap-y-1 py-3 text-xs text-muted-foreground"><span><span className="text-sm font-medium text-foreground">{pack.pack.name}</span> v{pack.pack.version} · {pack.pack.owner} owner · {pack.pack.mutability}</span><span>Draft {pack.review_status === "draft" ? "" : `(${pack.review_status})`} · Quality {pack.quality_score === null ? "not scored" : `${Math.round(pack.quality_score * 100)}%`} · Evaluation {pack.evaluation_score === null ? "not scored" : `${Math.round(pack.evaluation_score * 100)}%`} · {pack.promotion_eligible ? "Promotion-eligible; PR review required" : "PR review required"}</span></div>)}
    </div>}
    <div className="grid gap-3 border-y border-border py-4 sm:grid-cols-2">
      <label className="text-sm font-medium">Existing Pack<select value={selectedId} onChange={(event) => setSelectedId(event.target.value)} className="mt-1 h-9 w-full border border-input bg-background px-2 text-sm"><option value="">Create new Style Pack</option>{packs.map((pack) => <option key={pack.pack.id} value={pack.pack.id}>{pack.pack.name} (v{pack.pack.version})</option>)}</select></label>
      <label className="text-sm font-medium">Name<input value={name} onChange={(event) => setName(event.target.value)} className="mt-1 h-9 w-full border border-input bg-background px-2 text-sm" maxLength={120} /></label>
      <label className="text-sm font-medium">Version<input value={version} onChange={(event) => setVersion(event.target.value)} className="mt-1 h-9 w-full border border-input bg-background px-2 text-sm" pattern="[0-9]+\\.[0-9]+\\.[0-9]+" /></label>
    </div>
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <SelectField label="Audience" value={presentation.audience ?? ""} onChange={(value) => setPresentation((current) => ({ ...current, audience: value || null }))} options={[["", "Not set"], ["executive", "Executive"], ["business", "Business"], ["engineering", "Engineering"], ["operations", "Operations"]]} />
      <SelectField label="Tone" value={presentation.tone ?? ""} onChange={(value) => setPresentation((current) => ({ ...current, tone: value || null }))} options={[["", "Not set"], ["neutral", "Neutral"], ["concise", "Concise"], ["explanatory", "Explanatory"]]} />
      <SelectField label="Technical depth" value={presentation.technical_depth ?? ""} onChange={(value) => setPresentation((current) => ({ ...current, technical_depth: value || null }))} options={[["", "Not set"], ["summary", "Summary"], ["standard", "Standard"], ["deep", "Deep"]]} />
      <SelectField label="Output organization" value={presentation.output_organization ?? ""} onChange={(value) => setPresentation((current) => ({ ...current, output_organization: value || null }))} options={[["", "Not set"], ["direct_answer", "Direct answer"], ["sectioned", "Sectioned"], ["incident_report", "Incident report"]]} />
    </div>
    <OptionGroup label="Formatting" field="formatting" values={presentation.formatting} onToggle={setOption} />
    <OptionGroup label="Communication" field="communication" values={presentation.communication} onToggle={setOption} />
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <div className="flex justify-end"><Button onClick={save} isLoading={saving}><Save className="h-3.5 w-3.5" aria-hidden />{selectedId ? "Save new version" : "Create Style Pack"}</Button></div>
  </section>;
}

function SelectField({ label, value, onChange, options: values }: { label: string; value: string; onChange: (value: string) => void; options: readonly (readonly [string, string])[] }) {
  return <label className="text-sm font-medium">{label}<select value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 h-9 w-full border border-input bg-background px-2 text-sm">{values.map(([optionValue, text]) => <option key={optionValue} value={optionValue}>{text}</option>)}</select></label>;
}

function OptionGroup({ label, field, values, onToggle }: { label: string; field: "formatting" | "communication"; values: string[]; onToggle: (field: "formatting" | "communication", value: string) => void }) {
  return <fieldset className="border-y border-border py-3"><legend className="text-sm font-medium">{label}</legend><div className="mt-2 flex flex-wrap gap-x-5 gap-y-2">{options[field].map(([value, text]) => <label key={value} className="flex items-center gap-2 text-sm text-muted-foreground"><input type="checkbox" checked={values.includes(value)} onChange={() => onToggle(field, value)} className="h-4 w-4 accent-primary" />{text}</label>)}</div></fieldset>;
}
