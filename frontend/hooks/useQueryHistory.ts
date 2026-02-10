"use client";

import { useCallback, useState } from "react";
import type { QueryHistoryEntry, QueryResponse } from "@/types";

export function useQueryHistory() {
  const [entries, setEntries] = useState<QueryHistoryEntry[]>([]);

  const addEntry = useCallback((query: string, response: QueryResponse) => {
    const entry: QueryHistoryEntry = {
      id: crypto.randomUUID(),
      query,
      answer: response.answer,
      sources: response.sources,
      latency_ms: response.latency_ms,
      timestamp: new Date(),
    };
    setEntries((prev) => [entry, ...prev]);
  }, []);

  const clearHistory = useCallback(() => {
    setEntries([]);
  }, []);

  return { entries, addEntry, clearHistory };
}
