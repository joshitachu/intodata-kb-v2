"use client";

import { useCallback, useEffect, useState } from "react";
import { Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PackEditor } from "@/components/harness-packs/PackEditor";
import { PackSelector } from "@/components/harness-packs/PackSelector";
import { useUserProjects } from "@/hooks/useUserProjects";
import {
  createCustomPack,
  fetchPackCatalog,
  fetchPackRecommendations,
  savePackActivation,
} from "@/lib/harnessPacks";
import type { HarnessPackCatalog, HarnessPackRecommendation } from "@/types";

export default function ApplicationProfilePage() {
  const projects = useUserProjects();
  const [projectId, setProjectId] = useState("");
  const [catalog, setCatalog] = useState<HarnessPackCatalog | null>(null);
  const [recommendations, setRecommendations] = useState<HarnessPackRecommendation[]>([]);
  const [activePackIds, setActivePackIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId && projects.data?.projects.length) {
      setProjectId(projects.data.projects[0].id);
    }
  }, [projectId, projects.data]);

  const loadProfile = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const [nextCatalog, nextRecommendations] = await Promise.all([
        fetchPackCatalog(projectId),
        fetchPackRecommendations(projectId),
      ]);
      setCatalog(nextCatalog);
      setRecommendations(nextRecommendations);
      setActivePackIds(nextCatalog.active.active_pack_ids);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Application Profile could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void loadProfile();
  }, [loadProfile]);

  async function saveProfile() {
    if (!projectId) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      await savePackActivation(projectId, "default", activePackIds);
      setNotice("Application Profile saved.");
      await loadProfile();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Application Profile could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function createPolicyPack(manifest: unknown) {
    if (!projectId) throw new Error("Choose a project first.");
    await createCustomPack(projectId, manifest);
    setNotice("Policy Pack created. Select it to add it to the Application Profile.");
    await loadProfile();
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 py-6">
      <header className="flex items-center gap-2">
        <Settings2 className="h-6 w-6" aria-hidden />
        <h1 className="text-2xl font-semibold">Application Profiles</h1>
      </header>

      <div className="max-w-md space-y-2">
        <label htmlFor="application-profile-project" className="text-sm font-medium">Project</label>
        <select
          id="application-profile-project"
          className="h-10 w-full border border-input bg-background px-3 text-sm"
          value={projectId}
          onChange={(event) => {
            setProjectId(event.target.value);
            setNotice(null);
          }}
          disabled={projects.isLoading}
        >
          {!projects.data?.projects.length && <option value="">No projects available</option>}
          {projects.data?.projects.map((project) => (
            <option key={project.id} value={project.id}>{project.display_name}</option>
          ))}
        </select>
      </div>

      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {notice && <p role="status" className="text-sm text-emerald-700">{notice}</p>}

      <section className="space-y-4 border-t pt-5" aria-labelledby="profile-packs-heading">
        <div className="flex items-center justify-between gap-4">
          <h2 id="profile-packs-heading" className="text-base font-semibold">Capability policy</h2>
          <Button onClick={saveProfile} isLoading={saving} disabled={!projectId || loading || !catalog}>
            Save profile
          </Button>
        </div>
        {loading && <p className="text-sm text-muted-foreground">Loading profile...</p>}
        {catalog && (
          <PackSelector
            packs={catalog.packs}
            selectedPackIds={activePackIds}
            recommendations={recommendations}
            onChange={setActivePackIds}
          />
        )}
      </section>

      <section className="space-y-4 border-t pt-5" aria-labelledby="create-policy-pack-heading">
        <h2 id="create-policy-pack-heading" className="text-base font-semibold">Create policy Pack</h2>
        <PackEditor onSave={createPolicyPack} />
      </section>
    </div>
  );
}
