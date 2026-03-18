"use client";

import { useCallback, useRef, useState, type DragEvent } from "react";
import { Upload, Loader2, CheckCircle, AlertCircle } from "lucide-react";
import { useUploadDocument } from "@/hooks/useDocuments";
import { useDocsProjects } from "@/hooks/useDocsProjects";
import { cn } from "@/lib/utils";

const ALLOWED_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
];

const ALLOWED_EXTENSIONS = [".pdf", ".docx", ".pptx", ".xlsx"];

export function DocumentUpload() {
  const [dragOver, setDragOver] = useState(false);
  const [selectedProject, setSelectedProject] = useState("");
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
      upload.mutate({ file, project_id: selectedProject });
    },
    [upload, selectedProject],
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
      <div>
        <label
          htmlFor="project-select"
          className="block text-sm font-medium mb-1"
        >
          Project
        </label>
        <select
          id="project-select"
          value={selectedProject}
          onChange={(e) => setSelectedProject(e.target.value)}
          disabled={projectsLoading}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        >
          <option value="">
            {projectsLoading ? "Loading projects..." : "Select a project"}
          </option>
          {projectsData?.projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.display_name} ({p.platform})
            </option>
          ))}
        </select>
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
