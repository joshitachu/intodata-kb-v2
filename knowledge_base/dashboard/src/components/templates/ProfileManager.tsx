import { useState } from "react";
import { usePlatforms } from "../../hooks/useDiscovery";
import { useProfiles, useCreateProfile, useCloneProfile, useSetDefaultProfile } from "../../hooks/useTemplates";
import type { TemplateProfile } from "../../types";

interface Props {
  onSelect: (profileId: number) => void;
  selectedId: number | null;
}

export default function ProfileManager({ onSelect, selectedId }: Props) {
  const { data, isLoading } = useProfiles();
  const createProfile = useCreateProfile();
  const cloneProfile = useCloneProfile();
  const setDefault = useSetDefaultProfile();
  const { data: platformsData } = usePlatforms();
  const platforms = platformsData?.platforms ?? [];

  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newPlatform, setNewPlatform] = useState("");
  const [newDescription, setNewDescription] = useState("");

  const profiles = data?.profiles ?? [];

  async function handleCreate() {
    if (!newName.trim()) return;
    await createProfile.mutateAsync({
      name: newName,
      platform: newPlatform || undefined,
      description: newDescription || undefined,
    });
    setShowCreate(false);
    setNewName("");
    setNewPlatform("");
    setNewDescription("");
  }

  async function handleClone(profile: TemplateProfile) {
    const name = prompt("Name for cloned profile:", `${profile.name} (Copy)`);
    if (!name) return;
    await cloneProfile.mutateAsync({ id: profile.id, newName: name });
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-mono text-slate-400">
          Profiles
          {profiles.length > 0 && (
            <span className="ml-2 text-xs text-slate-600">({profiles.length})</span>
          )}
        </h3>
        <button
          className="btn-ghost !text-xs"
          onClick={() => setShowCreate(!showCreate)}
        >
          {showCreate ? "Cancel" : "New Profile"}
        </button>
      </div>

      {/* Create form */}
      {showCreate && (
        <div className="card-dark p-4 space-y-3">
          <input
            type="text"
            placeholder="Profile name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            className="input-dark w-full"
          />
          <div className="flex gap-2">
            <select
              value={newPlatform}
              onChange={(e) => setNewPlatform(e.target.value)}
              className="input-dark w-auto"
            >
              <option value="">Any platform</option>
              {platforms.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
              {newPlatform && !platforms.some((p) => p.id === newPlatform) && (
                <option value={newPlatform}>{newPlatform}</option>
              )}
            </select>
            <input
              type="text"
              placeholder="Description (optional)"
              value={newDescription}
              onChange={(e) => setNewDescription(e.target.value)}
              className="input-dark flex-1"
            />
          </div>
          <button
            className="btn-ghost"
            disabled={!newName.trim() || createProfile.isPending}
            onClick={handleCreate}
          >
            Create
          </button>
        </div>
      )}

      {/* Profile list */}
      {isLoading ? (
        <div className="p-4 text-sm text-slate-500">Loading...</div>
      ) : profiles.length === 0 ? (
        <div className="p-4 text-sm text-slate-500 font-mono">
          No profiles yet. Create one to start customizing templates.
        </div>
      ) : (
        <div className="space-y-1">
          {profiles.map((p) => (
            <div
              key={p.id}
              className={`flex items-center justify-between px-3 py-2.5 rounded border cursor-pointer transition-all ${
                selectedId === p.id
                  ? "border-accent-primary/40 bg-accent-primary/5"
                  : "border-white/[0.06] hover:border-white/[0.12]"
              }`}
              onClick={() => onSelect(p.id)}
            >
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm text-slate-300">{p.name}</span>
                  {p.is_default && (
                    <span className="text-xs px-1.5 py-0.5 border border-[#a0e06f]/40 text-[#a0e06f] rounded-sm">
                      default
                    </span>
                  )}
                  {p.platform && (
                    <span className="text-xs px-1.5 py-0.5 border border-white/[0.1] text-slate-500 rounded-sm">
                      {p.platform}
                    </span>
                  )}
                </div>
                {p.description && (
                  <p className="text-xs text-slate-600 mt-0.5">{p.description}</p>
                )}
              </div>
              <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                {!p.is_default && (
                  <button
                    className="text-xs text-slate-500 hover:text-[#a0e06f] px-2 py-1"
                    title="Set as default"
                    onClick={() => setDefault.mutate(p.id)}
                  >
                    Set Default
                  </button>
                )}
                <button
                  className="text-xs text-slate-500 hover:text-accent-primary px-2 py-1"
                  title="Clone"
                  onClick={() => handleClone(p)}
                >
                  Clone
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
