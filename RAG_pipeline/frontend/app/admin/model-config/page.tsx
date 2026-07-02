"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthContext } from "@/components/AuthProvider";

interface ModelConfig {
  provider: string | null;
  model: string | null;
  effective_chain: string[];
  allowed: Record<string, string[]>;
  defaults: Record<string, string>;
}

export default function AdminModelConfigPage() {
  const { user } = useAuthContext();
  const router = useRouter();

  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/model-config");
      if (!res.ok) {
        setError("Failed to load model configuration");
        return;
      }
      const data: ModelConfig = await res.json();
      setConfig(data);
      const initialProvider = data.provider ?? data.effective_chain[0] ?? "";
      setProvider(initialProvider);
      setModel(data.model ?? data.defaults[initialProvider] ?? "");
    } catch {
      setError("Network error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Superadmin-only surface.
  useEffect(() => {
    if (user && !user.is_superadmin) router.replace("/admin/usage");
  }, [user, router]);

  const modelOptions = useMemo(
    () => (config && provider ? config.allowed[provider] ?? [] : []),
    [config, provider],
  );

  // Keep the selected model valid for the chosen provider.
  useEffect(() => {
    if (modelOptions.length && !modelOptions.includes(model)) {
      setModel(modelOptions[0]);
    }
  }, [modelOptions, model]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSaved(false);
    setSaving(true);
    try {
      const res = await fetch("/api/admin/model-config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, model }),
      });
      if (!res.ok) {
        const data: { error?: string } | null = await res.json().catch(() => null);
        setError(data?.error || "Failed to save");
        return;
      }
      setSaved(true);
      await load();
    } catch {
      setError("Network error");
    } finally {
      setSaving(false);
    }
  }

  if (!user?.is_superadmin) {
    return (
      <p className="p-4 text-muted-foreground">Access denied. Superadmin only.</p>
    );
  }

  if (loading || !config) {
    return <p className="p-4 text-muted-foreground">Loading model configuration…</p>;
  }

  return (
    <div className="max-w-2xl mx-auto py-6">
      <div className="mb-2">
        <h1 className="text-2xl font-bold tracking-tight">Answer Model</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Choose the LLM used to generate answers. This standard applies to every
          user and admin. Embeddings and reranking are managed per deployment and
          are not configurable here.
        </p>
      </div>

      {error && (
        <div className="my-4 rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2.5 text-sm text-destructive">
          {error}
        </div>
      )}
      {saved && !error && (
        <div className="my-4 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2.5 text-sm text-emerald-700 dark:text-emerald-400">
          Saved. New answers will use this model.
        </div>
      )}

      <form
        onSubmit={handleSave}
        className="mt-4 space-y-4 rounded-2xl border bg-card p-6 shadow-elevation-sm"
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-foreground/80">
              Provider
            </label>
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              className="w-full rounded-lg border border-input bg-background px-3 py-2.5 text-sm"
            >
              {Object.keys(config.allowed).map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-foreground/80">
              Model
            </label>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="w-full rounded-lg border border-input bg-background px-3 py-2.5 text-sm"
            >
              {modelOptions.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
        </div>

        <button
          type="submit"
          disabled={saving || !provider || !model}
          className="rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-elevation-xs transition hover:bg-primary/90 disabled:opacity-50"
        >
          {saving ? "Saving…" : "Set as standard"}
        </button>
      </form>

      <div className="mt-6 rounded-2xl border bg-card p-6 shadow-elevation-sm">
        <h2 className="text-sm font-semibold text-foreground">Active configuration</h2>
        <dl className="mt-3 space-y-2 text-sm">
          <div className="flex gap-3">
            <dt className="w-40 shrink-0 text-muted-foreground">Standard</dt>
            <dd className="font-medium">
              {config.provider && config.model
                ? `${config.provider} / ${config.model}`
                : "Deployment default (no override set)"}
            </dd>
          </div>
          <div className="flex gap-3">
            <dt className="w-40 shrink-0 text-muted-foreground">Provider order</dt>
            <dd className="font-medium">{config.effective_chain.join(" → ")}</dd>
          </div>
          <div className="flex gap-3">
            <dt className="w-40 shrink-0 text-muted-foreground">Env defaults</dt>
            <dd className="text-muted-foreground">
              {Object.entries(config.defaults)
                .map(([p, m]) => `${p}: ${m}`)
                .join(", ")}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}
