"use client";

import { BookOpen, X } from "lucide-react";

interface UserGuideDialogProps {
  open: boolean;
  onClose: () => void;
}

export function UserGuideDialog({ open, onClose }: UserGuideDialogProps) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="flex h-[90vh] w-[90vw] max-w-6xl flex-col overflow-hidden rounded-2xl border bg-card shadow-elevation-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-primary text-primary-foreground shadow-elevation-xs">
              <BookOpen className="h-3.5 w-3.5" />
            </span>
            <h3 className="text-sm font-semibold text-foreground">User Guide</h3>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <iframe
          src="/guide/index.html"
          className="w-full flex-1 border-0 bg-white"
          title="Nexus User Guide"
        />
      </div>
    </div>
  );
}
