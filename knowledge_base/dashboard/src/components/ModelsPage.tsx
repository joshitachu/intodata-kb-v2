import { useMemo, useState } from "react";
import { useModels } from "../hooks/useDiscovery";
import {
  SETTING_SLOTS,
  useInstanceSettings,
  useUpdateInstanceSettings,
  type SettingSlot,
} from "../hooks/useInstanceSettings";
import { parseApiError } from "../lib/errors";

type DraftMap = Record<string, string>;

function buildDraft(overrides: Record<string, string>): DraftMap {
  const out: DraftMap = {};
  for (const slot of SETTING_SLOTS) {
    out[slot.key] = overrides[slot.key] ?? "";
  }
  return out;
}

export default function ModelsPage() {
  const { data: modelsData } = useModels();
  const { data: settingsData, isLoading, isError, error } = useInstanceSettings();
  const update = useUpdateInstanceSettings();
  const [draft, setDraft] = useState<DraftMap | null>(null);
  const [saved, setSaved] = useState(false);

  const overrides = settingsData?.settings ?? {};
  const currentDraft = draft ?? buildDraft(overrides);

  const allModels = modelsData?.models ?? [];
  const chatModels = useMemo(
    () => allModels.filter((m) => m.supports_analysis || m.supports_writing),
    [allModels]
  );
  const writingModels = useMemo(
    () => allModels.filter((m) => m.supports_writing),
    [allModels]
  );
  const modelsFor = (slot: SettingSlot) =>
    slot.key === "rag_embed_model"
      ? allModels
      : slot.key === "doc_gen_write_model"
        ? writingModels
        : chatModels;

  const isDirty =
    draft !== null &&
    SETTING_SLOTS.some((slot) => draft[slot.key] !== (overrides[slot.key] ?? ""));

  function handleChange(key: string, value: string) {
    setDraft((prev) => {
      const base = prev ?? buildDraft(overrides);
      return { ...base, [key]: value };
    });
    setSaved(false);
  }

  async function handleSave() {
    if (!draft) return;
    const payload: Record<string, string | null> = {};
    for (const slot of SETTING_SLOTS) {
      payload[slot.key] = draft[slot.key] === "" ? null : draft[slot.key];
    }
    await update.mutateAsync(payload);
    setDraft(null);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  function handleReset() {
    setDraft(null);
    setSaved(false);
  }

  return (
    <div className="card-dark">
      <div className="px-4 py-3 border-b border-white/[0.05] bg-surface-raised rounded-t">
        <h3 className="text-sm font-mono text-slate-400">Instance Models</h3>
        <p className="text-xs text-slate-600 mt-1">
          Per-VPS default models. Changes apply within 30 seconds without a restart.
          RAG keys ({'"rag_chat_model"'} and {'"rag_embed_model"'}) are validated by the
          RAG backend; MCP keys by the MCP server against the live model catalogue.
        </p>
      </div>
      <div className="p-4 space-y-4">
        {isLoading && (
          <p className="text-sm text-slate-500 font-mono">Loading…</p>
        )}
        {isError && (
          <p className="text-sm text-red-400">
            Failed to load settings: {parseApiError(error)}
          </p>
        )}
        {!isLoading && !isError && (
          <>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs font-mono uppercase tracking-wider text-slate-600 border-b border-white/[0.05]">
                  <th className="py-2 pr-4">Slot</th>
                  <th className="py-2 pr-4">Effective model</th>
                  <th className="py-2 pr-4">Default</th>
                </tr>
              </thead>
              <tbody>
                {SETTING_SLOTS.map((slot) => {
                  const options = modelsFor(slot);
                  const current = currentDraft[slot.key];
                  return (
                    <tr
                      key={slot.key}
                      className="border-b border-white/[0.04] align-top"
                    >
                      <td className="py-3 pr-4">
                        <div className="text-slate-200">{slot.label}</div>
                        <div className="text-xs text-slate-600 mt-1 max-w-md">
                          {slot.description}
                        </div>
                        <div className="text-[10px] font-mono text-slate-700 mt-1">
                          {slot.key}
                        </div>
                      </td>
                      <td className="py-3 pr-4">
                        <select
                          value={current}
                          onChange={(e) => handleChange(slot.key, e.target.value)}
                          className="input-dark w-full max-w-xs"
                        >
                          <option value="">(env / pydantic default)</option>
                          {options.map((m) => (
                            <option key={m.id} value={m.id}>
                              {m.label} ({m.id})
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="py-3 pr-4 text-xs text-slate-500 font-mono">
                        {current === "" ? "unset" : current}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="flex items-center gap-3">
              <button
                onClick={handleSave}
                disabled={!isDirty || update.isPending}
                className={`px-4 py-2 text-sm rounded font-medium transition-all ${
                  isDirty
                    ? "bg-accent-primary text-surface-bg hover:bg-accent-primary/90"
                    : "bg-white/[0.05] text-slate-600 cursor-not-allowed"
                }`}
              >
                {update.isPending ? "Saving…" : "Save Changes"}
              </button>
              <button
                onClick={handleReset}
                disabled={!isDirty || update.isPending}
                className="px-3 py-2 text-sm text-slate-500 hover:text-slate-300"
              >
                Reset
              </button>
              {saved && (
                <span className="text-sm text-[#a0e06f] font-medium">Saved!</span>
              )}
              {update.isError && (
                <span className="text-sm text-red-400">
                  Failed: {parseApiError(update.error)}
                </span>
              )}
            </div>
            <p className="text-xs text-slate-600">
              Empty value reverts the slot to its env-var default. Every save is
              written to the audit log with before/after per key.
            </p>
          </>
        )}
      </div>
    </div>
  );
}