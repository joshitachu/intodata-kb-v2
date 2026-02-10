"use client";

import { useState } from "react";
import { SearchBar } from "@/components/SearchBar";
import { ResponseDisplay } from "@/components/ResponseDisplay";
import { useSearch } from "@/hooks/useSearch";
import { useQueryHistory } from "@/hooks/useQueryHistory";
import type { QueryResponse } from "@/types";

export default function HomePage() {
  const [currentResponse, setCurrentResponse] = useState<QueryResponse | null>(null);
  const search = useSearch();
  const { addEntry } = useQueryHistory();

  function handleSearch(query: string) {
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

  return (
    <div className="max-w-4xl mx-auto py-8">
      <div className="text-center mb-8">
        <h1 className="text-2xl font-bold mb-2">Knowledge Base Search</h1>
        <p className="text-muted-foreground">
          Ask questions about your company documents
        </p>
      </div>

      <SearchBar onSearch={handleSearch} isLoading={search.isPending} />

      <ResponseDisplay
        response={currentResponse}
        isLoading={search.isPending}
        error={search.error}
      />
    </div>
  );
}
