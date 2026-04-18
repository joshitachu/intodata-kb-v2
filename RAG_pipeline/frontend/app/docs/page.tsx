"use client";

import { useState, useEffect } from "react";
import { BookOpen } from "lucide-react";
import { DocsProjectSelector } from "@/components/DocsProjectSelector";
import { DocList } from "@/components/DocList";
import { DocPreview } from "@/components/DocPreview";
import { useProjectDocs } from "@/hooks/useProjectDocs";
import { useChatContext } from "@/contexts/ChatContext";

export default function DocsPage() {
  const { selectedProject: projectId } = useChatContext();
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);
  const { data } = useProjectDocs(projectId);

  // Clear selected doc when project changes
  useEffect(() => {
    setSelectedDoc(null);
  }, [projectId]);

  const selectedDocInfo = data?.docs.find((d) => d.name === selectedDoc);

  return (
    <div className="flex flex-col h-full" data-tour="project-docs-page">
      {/* Page header */}
      <div className="p-4 border-b">
        <div className="flex items-center gap-2 mb-3">
          <BookOpen className="h-5 w-5 text-primary" />
          <h1 className="text-lg font-semibold">Project Documentation</h1>
        </div>
        <DocsProjectSelector />
      </div>

      {/* Content area */}
      {projectId ? (
        <div className="flex flex-1 min-h-0">
          {/* Left panel: doc list */}
          <div className="w-[40%] border-r flex flex-col min-h-0">
            <DocList
              projectId={projectId}
              selectedDoc={selectedDoc}
              onSelectDoc={setSelectedDoc}
            />
          </div>

          {/* Right panel: preview */}
          <div className="w-[60%] flex flex-col min-h-0">
            {selectedDoc ? (
              <DocPreview
                projectId={projectId}
                filename={selectedDoc}
                hasPdf={selectedDocInfo?.has_pdf ?? false}
                onClose={() => setSelectedDoc(null)}
              />
            ) : (
              <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
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
