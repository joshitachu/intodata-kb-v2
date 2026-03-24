import { useProfile, useTemplates, useVersions, useSetAssignment } from "../../hooks/useTemplates";
import { useState } from "react";

interface Props {
  profileId: number;
}

export default function ProfileDetail({ profileId }: Props) {
  const { data: profile, isLoading } = useProfile(profileId);
  const { data: templatesData } = useTemplates();
  const [addingSlug, setAddingSlug] = useState<string | null>(null);

  if (isLoading || !profile) {
    return <div className="p-4 text-sm text-slate-500">Loading profile...</div>;
  }

  const allTemplates = templatesData?.templates ?? [];
  const assignedSlugs = new Set(profile.assignments.map((a) => a.template_slug));
  const unassigned = allTemplates.filter((t) => !assignedSlugs.has(t.slug));

  return (
    <div className="card-dark">
      <div className="px-4 py-3 border-b border-white/[0.05] bg-surface-raised rounded-t">
        <h3 className="text-sm font-mono text-slate-300">{profile.name}</h3>
        <p className="text-xs text-slate-500 mt-0.5">
          {profile.assignments.length} template override{profile.assignments.length !== 1 ? "s" : ""}
          {profile.platform && ` -- ${profile.platform}`}
          {profile.is_default && " -- DEFAULT"}
        </p>
      </div>

      {/* Existing assignments */}
      {profile.assignments.length > 0 && (
        <div className="divide-y divide-white/[0.04]">
          {profile.assignments.map((a) => (
            <AssignmentRow
              key={a.id}
              profileId={profileId}
              slug={a.template_slug}
              displayName={a.template_display_name}
              currentVersionNumber={a.version_number}
              currentVersionId={a.version_id}
            />
          ))}
        </div>
      )}

      {/* Add override */}
      <div className="px-4 py-3 border-t border-white/[0.04]">
        {addingSlug ? (
          <AddAssignment
            profileId={profileId}
            slug={addingSlug}
            onDone={() => setAddingSlug(null)}
          />
        ) : (
          <div className="flex items-center gap-2">
            <select
              value=""
              onChange={(e) => setAddingSlug(e.target.value || null)}
              className="input-dark flex-1 text-xs"
            >
              <option value="">Add template override...</option>
              {unassigned.map((t) => (
                <option key={t.slug} value={t.slug}>
                  {t.display_name} ({t.slug})
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
    </div>
  );
}

function AssignmentRow({
  profileId,
  slug,
  displayName,
  currentVersionNumber,
  currentVersionId,
}: {
  profileId: number;
  slug: string;
  displayName: string;
  currentVersionNumber: number;
  currentVersionId: number;
}) {
  const { data: versionsData } = useVersions(slug);
  const setAssignment = useSetAssignment();
  const versions = versionsData?.versions ?? [];

  return (
    <div className="px-4 py-2.5 flex items-center justify-between">
      <div>
        <span className="text-sm text-slate-300">{displayName}</span>
        <span className="ml-2 text-xs font-mono text-slate-600">{slug}</span>
      </div>
      <select
        value={currentVersionId}
        onChange={(e) => {
          const versionId = Number(e.target.value);
          setAssignment.mutate({ profileId, slug, versionId });
        }}
        className="input-dark !w-auto text-xs"
      >
        {versions.map((v) => (
          <option key={v.id} value={v.id}>
            v{v.version_number} - {v.change_summary.slice(0, 30)}
          </option>
        ))}
        {versions.length === 0 && (
          <option value={currentVersionId}>v{currentVersionNumber}</option>
        )}
      </select>
    </div>
  );
}

function AddAssignment({
  profileId,
  slug,
  onDone,
}: {
  profileId: number;
  slug: string;
  onDone: () => void;
}) {
  const { data: versionsData, isLoading } = useVersions(slug);
  const setAssignment = useSetAssignment();
  const versions = versionsData?.versions ?? [];

  if (isLoading) return <span className="text-xs text-slate-500">Loading versions...</span>;
  if (versions.length === 0) {
    return (
      <div className="flex items-center gap-2 text-xs text-slate-500">
        No versions available for {slug}
        <button className="text-slate-400 hover:text-slate-200" onClick={onDone}>Cancel</button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-slate-400 font-mono">{slug}</span>
      <select
        defaultValue=""
        onChange={async (e) => {
          if (!e.target.value) return;
          await setAssignment.mutateAsync({
            profileId,
            slug,
            versionId: Number(e.target.value),
          });
          onDone();
        }}
        className="input-dark !w-auto text-xs"
      >
        <option value="">Select version...</option>
        {versions.map((v) => (
          <option key={v.id} value={v.id}>
            v{v.version_number} - {v.change_summary.slice(0, 30)}
          </option>
        ))}
      </select>
      <button className="text-xs text-slate-500 hover:text-slate-300" onClick={onDone}>
        Cancel
      </button>
    </div>
  );
}
