"use client";

import { useCallback, useRef, useState, type DragEvent } from "react";
import { Upload, Loader2, CheckCircle, AlertCircle, ChevronDown } from "lucide-react";
import { useUploadDocument } from "@/hooks/useDocuments";
import { useDocsProjects } from "@/hooks/useDocsProjects";
import { useChatContext } from "@/contexts/ChatContext";
import { cn } from "@/lib/utils";
import { DOCUMENT_ROLES, type DocumentRole } from "@/types";

const ALLOWED_EXTENSIONS = [".pdf", ".docx", ".pptx", ".xlsx"];
const ROLE_LABELS: Record<DocumentRole, string> = {
  source: "Source",
  reference: "Reference",
  guidance: "Guidance",
  standard: "Standard",
  template: "Template",
  policy: "Policy",
};

export function DocumentUpload() {
  const { selectedProject, setSelectedProject } = useChatContext();
  const [documentRole, setDocumentRole] = useState<DocumentRole>("source");
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const upload = useUploadDocument();
  const { data: projectsData, isLoading: projectsLoading } = useDocsProjects();

  const handleFile = useCallback(
    (file: File) => {
      if (!selectedProject) return;
      const ext = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();
      if (!ALLOWED_EXTENSIONS.includes(ext)) {
        return;
      }
      upload.mutate({ file, project_id: selectedProject, document_role: documentRole });
    },
    [upload, selectedProject, documentRole],
  );

  function handleDrop(e: DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  }

  function handleDragOver(e: DragEvent) {
    e.preventDefault();
    setDragOver(true);
  }

  function handleDragLeave() {
    setDragOver(false);
  }

  const canUpload = !!selectedProject && !upload.isPending;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <label
            htmlFor="project-select"
            className="text-sm font-medium text-muted-foreground"
          >
            Project
          </label>
          <div className="relative inline-block">
            <select
              id="project-select"
              value={selectedProject}
              onChange={(e) => setSelectedProject(e.target.value)}
              disabled={projectsLoading}
              className={cn(
                "appearance-none cursor-pointer",
                "rounded-full border border-input bg-muted/40 pl-3 pr-7 py-1",
                "text-xs text-muted-foreground hover:text-foreground hover:bg-muted",
                "focus:outline-none focus:ring-1 focus:ring-ring",
                "transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
              )}
            >
              <option value="">
                {projectsLoading ? "Loading..." : "Select a project"}
              </option>
              {projectsData?.projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.display_name} ({p.platform})
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
          </div>
        </div>

        <div className="flex items-center gap-2">
          <label
            htmlFor="document-role-select"
            className="text-sm font-medium text-muted-foreground"
          >
            Role
          </label>
          <div className="relative inline-block">
            <select
              id="document-role-select"
              value={documentRole}
              onChange={(e) => setDocumentRole(e.target.value as DocumentRole)}
              className={cn(
                "appearance-none cursor-pointer",
                "rounded-full border border-input bg-muted/40 pl-3 pr-7 py-1",
                "text-xs text-muted-foreground hover:text-foreground hover:bg-muted",
                "focus:outline-none focus:ring-1 focus:ring-ring transition-colors",
              )}
            >
              {DOCUMENT_ROLES.map((role) => (
                <option key={role} value={role}>
                  {ROLE_LABELS[role]}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
          </div>
        </div>
      </div>

      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => canUpload && fileInputRef.current?.click()}
        className={cn(
          "border-2 border-dashed rounded-lg p-8 text-center transition-colors",
          canUpload ? "cursor-pointer" : "cursor-not-allowed opacity-60",
          dragOver ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:border-muted-foreground/50",
        )}
      >
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          accept={ALLOWED_EXTENSIONS.join(",")}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFile(file);
            e.target.value = "";
          }}
        />

        {upload.isPending ? (
          <div className="flex flex-col items-center gap-2">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Uploading and processing...</p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <Upload className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm font-medium">
              {selectedProject
                ? "Drop a file here or click to browse"
                : "Select a project first"}
            </p>
            <p className="text-xs text-muted-foreground">
              PDF, DOCX, PPTX, XLSX (max 50 MB)
            </p>
          </div>
        )}
      </div>

      {upload.isSuccess && (
        <div className="flex items-center gap-2 text-sm text-green-600">
          <CheckCircle className="h-4 w-4" />
          <span>{upload.data.message}</span>
        </div>
      )}

      {upload.isError && (
        <div className="flex items-center gap-2 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          <span>{upload.error.message}</span>
        </div>
      )}
    </div>
  );
}
