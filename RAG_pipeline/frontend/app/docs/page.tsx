"use client";

import { useState, useEffect } from "react";
import { BookOpen } from "lucide-react";
import { DocsProjectSelector } from "@/components/DocsProjectSelector";
import { DocList } from "@/components/DocList";
import { DocPreview } from "@/components/DocPreview";
import { MobileTopBar } from "@/components/mobile/MobileTopBar";
import { useChatContext } from "@/contexts/ChatContext";
import { useViewport } from "@/hooks/useViewport";
import { cn } from "@/lib/utils";
import type { DocFileInfo } from "@/types";

export default function DocsPage() {
  const { selectedProject: projectId } = useChatContext();
  // The whole DocFileInfo, not a filename: a partitioned project repeats every
  // document name across its sub-projects, so a name alone cannot identify
  // which one to preview.
  const [selectedDoc, setSelectedDoc] = useState<DocFileInfo | null>(null);
  const { isMobile } = useViewport();

  // Clear selected doc when project changes
  useEffect(() => {
    setSelectedDoc(null);
  }, [projectId]);

  return (
    <div className="flex flex-col h-full" data-tour="project-docs-page">
      {isMobile && <MobileTopBar title="Project Docs" backHref="/" />}

      {/* Page header */}
      <div className="p-3 sm:p-4 border-b">
        <div className="flex items-center gap-2 mb-3">
          <BookOpen className="h-5 w-5 text-primary" />
          <h1 className="hidden sm:block text-lg font-semibold">Project Documentation</h1>
        </div>
        <DocsProjectSelector />
      </div>

      {/* Content area */}
      {projectId ? (
        <div className="flex flex-col sm:flex-row flex-1 min-h-0">
          {/* Left panel: doc list */}
          <div className={cn(
            "flex flex-col min-h-0",
            isMobile
              ? selectedDoc
                ? "h-[35%] border-b"
                : "h-[50%] border-b"
              : "w-[40%] border-r",
          )}>
            <DocList
              projectId={projectId}
              selectedDoc={selectedDoc}
              onSelectDoc={setSelectedDoc}
            />
          </div>

          {/* Right panel: preview */}
          <div className={cn(
            "flex flex-col min-h-0",
            isMobile ? "flex-1" : "w-[60%]",
          )}>
            {selectedDoc ? (
              <DocPreview
                projectId={projectId}
                filename={selectedDoc.name}
                subproject={selectedDoc.subproject}
                hasPdf={selectedDoc.has_pdf}
                onClose={() => setSelectedDoc(null)}
              />
            ) : (
              <div className="flex items-center justify-center h-full text-muted-foreground text-sm p-4">
                Select a document to preview
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-center flex-1 text-muted-foreground text-sm">
          Select a project to browse its documentation
        </div>
      )}
    </div>
  );
}
