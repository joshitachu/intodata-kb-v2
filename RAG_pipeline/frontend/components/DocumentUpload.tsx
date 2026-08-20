"use client";

import { useCallback, useRef, useState, type DragEvent } from "react";
import { Upload, Loader2, CheckCircle, AlertCircle, ChevronDown } from "lucide-react";
import { useUploadDocument } from "@/hooks/useDocuments";
import { useDocsProjects } from "@/hooks/useDocsProjects";
import { useChatContext } from "@/contexts/ChatContext";
import { cn } from "@/lib/utils";
import { DOCUMENT_ROLES, type DocumentRole } from "@/types";

const ALLOWED_EXTENSIONS = [".pdf", ".docx", ".pptx", ".xlsx", ".md"];
const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024;

type UploadQueueStatus = "queued" | "uploading" | "complete" | "failed";

interface UploadQueueItem {
  id: number;
  file: File;
  projectId: string;
  documentRole: DocumentRole;
  status: UploadQueueStatus;
  message?: string;
}
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
  const [uploadQueue, setUploadQueue] = useState<UploadQueueItem[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const queueRef = useRef<UploadQueueItem[]>([]);
  const queueIdRef = useRef(0);
  const isProcessingRef = useRef(false);
  const upload = useUploadDocument();
  const { data: projectsData, isLoading: projectsLoading } = useDocsProjects();

  const updateQueueItem = useCallback((id: number, changes: Partial<UploadQueueItem>) => {
    const next = queueRef.current.map((item) => (
      item.id === id ? { ...item, ...changes } : item
    ));
    queueRef.current = next;
    setUploadQueue(next);
  }, []);

  const processNext = useCallback(async () => {
    if (isProcessingRef.current) return;

    const next = queueRef.current.find((item) => item.status === "queued");
    if (!next) return;

    isProcessingRef.current = true;
    updateQueueItem(next.id, { status: "uploading" });
    try {
      const response = await upload.mutateAsync({
        file: next.file,
        project_id: next.projectId,
        document_role: next.documentRole,
      });
      updateQueueItem(next.id, { status: "complete", message: response.message });
    } catch (error) {
      updateQueueItem(next.id, {
        status: "failed",
        message: error instanceof Error ? error.message : "Upload failed",
      });
    } finally {
      isProcessingRef.current = false;
      void processNext();
    }
  }, [updateQueueItem, upload]);

  const queueFiles = useCallback((files: Iterable<File>) => {
    if (!selectedProject) return;

    const items = Array.from(files).map((file): UploadQueueItem => {
      const extension = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();
      const item: UploadQueueItem = {
        id: queueIdRef.current++,
        file,
        projectId: selectedProject,
        documentRole,
        status: "queued",
      };

      if (!ALLOWED_EXTENSIONS.includes(extension)) {
        return { ...item, status: "failed", message: "Unsupported file type" };
      }
      if (file.size > MAX_FILE_SIZE_BYTES) {
        return { ...item, status: "failed", message: "File exceeds the 50 MB limit" };
      }
      return item;
    });

    if (!items.length) return;
    queueRef.current = [...queueRef.current, ...items];
    setUploadQueue(queueRef.current);
    void processNext();
  }, [documentRole, processNext, selectedProject]);

  function handleDrop(e: DragEvent) {
    e.preventDefault();
    setDragOver(false);
    queueFiles(e.dataTransfer.files);
  }

  function handleDragOver(e: DragEvent) {
    e.preventDefault();
    setDragOver(true);
  }

  function handleDragLeave() {
    setDragOver(false);
  }

  const canUpload = !!selectedProject;

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
          multiple
          onChange={(e) => {
            if (e.target.files) queueFiles(e.target.files);
            e.target.value = "";
          }}
        />

        {uploadQueue.some((item) => item.status === "uploading") ? (
          <div className="flex flex-col items-center gap-2">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Uploading and processing...</p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <Upload className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm font-medium">
              {selectedProject
                ? "Drop files here or click to browse"
                : "Select a project first"}
            </p>
            <p className="text-xs text-muted-foreground">
              PDF, DOCX, PPTX, XLSX, MD (max 50 MB)
            </p>
          </div>
        )}
      </div>

      {uploadQueue.length > 0 && (
        <div className="divide-y rounded-md border" aria-live="polite">
          {uploadQueue.map((item) => (
            <div key={item.id} className="flex items-center gap-2 px-3 py-2 text-sm">
              {item.status === "uploading" && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" />}
              {item.status === "complete" && <CheckCircle className="h-4 w-4 shrink-0 text-green-600" />}
              {item.status === "failed" && <AlertCircle className="h-4 w-4 shrink-0 text-destructive" />}
              {item.status === "queued" && <Upload className="h-4 w-4 shrink-0 text-muted-foreground" />}
              <span className="min-w-0 flex-1 truncate">{item.file.name}</span>
              <span className={cn(
                "shrink-0 text-xs",
                item.status === "failed" ? "text-destructive" : "text-muted-foreground",
              )}>
                {item.status === "complete" && item.message
                  ? `Complete — ${item.message}`
                  : item.status === "failed" && item.message
                    ? `Failed — ${item.message}`
                    : item.status === "queued"
                      ? "Waiting"
                      : item.status === "uploading"
                        ? "Uploading and processing..."
                        : item.status === "complete"
                          ? "Complete"
                          : "Failed"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
