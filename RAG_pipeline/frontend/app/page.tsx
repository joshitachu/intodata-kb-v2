"use client";

import { useState } from "react";
import { SearchBar } from "@/components/SearchBar";
import { ResponseDisplay } from "@/components/ResponseDisplay";
import { ModeToggle, type SearchMode } from "@/components/ModeToggle";
import { ProjectSelector } from "@/components/ProjectSelector";
import { DeepDiveDisplay } from "@/components/DeepDiveDisplay";
import { useSearch } from "@/hooks/useSearch";
import { useDeepDive } from "@/hooks/useDeepDive";
import { useQueryHistory } from "@/hooks/useQueryHistory";
import type { QueryResponse } from "@/types";

export default function HomePage() {
  const [currentResponse, setCurrentResponse] = useState<QueryResponse | null>(null);
  const [mode, setMode] = useState<SearchMode>("search");
  const [selectedProject, setSelectedProject] = useState("");
  const search = useSearch();
  const deepDive = useDeepDive();
  const { addEntry } = useQueryHistory();

  function handleSearch(query: string) {
    if (mode === "deep-dive") {
      deepDive.query({ project_id: selectedProject, question: query });
    } else {
      search.mutate(
        { query },
        {
          onSuccess: (response) => {
            setCurrentResponse(response);
            addEntry(query, response);
          },
        },
      );
    }
  }

  function handleModeChange(newMode: SearchMode) {
    setMode(newMode);
    // Reset states when switching modes
    if (newMode === "search") {
      deepDive.reset();
    } else {
      setCurrentResponse(null);
    }
  }

  const isDeepDive = mode === "deep-dive";
  const isLoading = isDeepDive ? deepDive.state.isStreaming : search.isPending;
  const searchDisabled = isDeepDive && !selectedProject;

  return (
    <div className="max-w-4xl mx-auto py-8">
      <div className="text-center mb-8">
        <h1 className="text-2xl font-bold mb-2">Knowledge Base Search</h1>
        <p className="text-muted-foreground">
          Ask questions about your company documents
        </p>
      </div>

      <div className="flex flex-col items-center gap-4 mb-4">
        <ModeToggle mode={mode} onModeChange={handleModeChange} />

        {isDeepDive && (
          <ProjectSelector
            selectedProject={selectedProject}
            onProjectChange={setSelectedProject}
          />
        )}
      </div>

      <SearchBar
        onSearch={handleSearch}
        isLoading={isLoading}
        disabled={searchDisabled}
        placeholder={
          isDeepDive
            ? selectedProject
              ? "Ask a detailed question about the project code..."
              : "Select a project first..."
            : undefined
        }
      />

      {isDeepDive ? (
        <DeepDiveDisplay state={deepDive.state} onCancel={deepDive.cancel} />
      ) : (
        <ResponseDisplay
          response={currentResponse}
          isLoading={search.isPending}
          error={search.error}
        />
      )}
    </div>
  );
}
