"use client";

import { FileText, Loader2 } from "lucide-react";
import { useDocuments } from "@/hooks/useDocuments";
import { guidanceErrorMessage } from "@/hooks/useGuidance";
import type { DocumentResponse, DocumentRole } from "@/types";

interface ProjectMaterialSectionProps {
  projectId: string;
}

/**
 * What an uploaded file became.
 *
 * The role chosen at upload is a decision about how the assistant treats the
 * file, not a label on it: a standard or a policy is something the answer must
 * respect, guidance and templates are ways of working the assistant follows,
 * references are background, and sources are material it quotes. Read-only
 * here, because the only way to change any of it is to upload the file again
 * under a different role -- which is the panel next to this one.
 */
const ROLE_GROUP: Record<DocumentRole, "constraint" | "method" | "reference" | "evidence"> = {
  policy: "constraint",
  standard: "constraint",
  guidance: "method",
  template: "method",
  reference: "reference",
  source: "evidence",
};

const GROUPS = [
  {
    id: "constraint" as const,
    title: "Rules the assistant must follow",
    hint: "Standards and policies. Read in full before it recommends anything.",
  },
  {
    id: "method" as const,
    title: "Ways of working it copies",
    hint: "Guidance and templates. Picked up when that kind of work comes up.",
  },
  {
    id: "reference" as const,
    title: "Background it can look up",
    hint: "Opened when a question actually touches the subject.",
  },
  {
    id: "evidence" as const,
    title: "Material it searches and quotes",
    hint: "Sources. Quoted and credited, never treated as an instruction.",
  },
];

export function ProjectMaterialSection({ projectId }: ProjectMaterialSectionProps) {
  const { data, isLoading, error } = useDocuments();

  if (isLoading) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading the files uploaded to this project...
      </p>
    );
  }

  if (error) {
    return (
      <p className="text-sm text-destructive" role="alert">
        {guidanceErrorMessage(error, "Could not load the files for this project")}
      </p>
    );
  }

  const documents = (data?.documents ?? []).filter((doc) => doc.project_id === projectId);

  if (documents.length === 0) {
    return (
      <div
        data-testid="material-empty"
        className="rounded-md border border-dashed border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
      >
        <p>
          Nothing uploaded to this project yet. The role you pick when you upload
          decides what the file becomes.
        </p>
        <p className="mt-1.5 text-foreground">
          For example: an interface standard uploaded as a <em>Standard</em>
          {" "}becomes a rule the assistant must respect, while a functional design
          uploaded as a <em>Source</em> becomes material it can quote and credit.
        </p>
      </div>
    );
  }

  const byGroup = new Map<string, DocumentResponse[]>();
  for (const doc of documents) {
    const group = ROLE_GROUP[doc.document_role];
    byGroup.set(group, [...(byGroup.get(group) ?? []), doc]);
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Files you uploaded. Colleagues&apos; uploads are not listed here, but the
        assistant uses those too.
      </p>
      {GROUPS.map((group) => {
        const entries = byGroup.get(group.id) ?? [];
        if (entries.length === 0) return null;
        return (
          <div key={group.id}>
            <p className="text-xs font-medium text-foreground">{group.title}</p>
            <p className="text-[11px] text-muted-foreground">{group.hint}</p>
            <ul className="mt-1 space-y-1">
              {entries.map((doc) => (
                <li
                  key={doc.id}
                  className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-1.5 text-sm"
                >
                  <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate">{doc.filename}</span>
                  <span className="shrink-0 text-[11px] text-muted-foreground">
                    uploaded as {doc.document_role}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}
