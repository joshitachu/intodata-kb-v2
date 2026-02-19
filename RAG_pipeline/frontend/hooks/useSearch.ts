"use client";

import { useMutation } from "@tanstack/react-query";
import type { QueryRequest, QueryResponse } from "@/types";

async function postQuery(request: QueryRequest): Promise<QueryResponse> {
  const res = await fetch("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.error || "Query failed");
  }

  return res.json();
}

export function useSearch() {
  return useMutation({
    mutationFn: postQuery,
  });
}
