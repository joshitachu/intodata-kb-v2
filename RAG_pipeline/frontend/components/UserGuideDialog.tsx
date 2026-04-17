"use client";

import { X } from "lucide-react";

interface UserGuideDialogProps {
  open: boolean;
  onClose: () => void;
}

export function UserGuideDialog({ open, onClose }: UserGuideDialogProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl w-[90vw] h-[90vh] max-w-6xl flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b bg-gray-50">
          <h3 className="text-sm font-semibold text-gray-700">User Guide</h3>
          <button
            onClick={onClose}
            className="p-1 rounded-md hover:bg-gray-200 text-gray-500 hover:text-gray-700 transition-colors"
            title="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <iframe
          src="/guide/index.html"
          className="flex-1 w-full border-0"
          title="Nexus User Guide"
        />
      </div>
    </div>
  );
}
