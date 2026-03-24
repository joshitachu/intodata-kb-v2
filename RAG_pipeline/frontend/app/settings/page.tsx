"use client";

import { useEffect, useState } from "react";
import { CheckCircle, XCircle, Loader2 } from "lucide-react";
import type { HealthResponse } from "@/types";

export default function SettingsPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((res) => res.json())
      .then((data) => {
        setHealth(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  return (
    <div className="max-w-2xl mx-auto py-8">
      <h1 className="text-2xl font-bold mb-6">Settings</h1>

      <div className="space-y-6">
        <section>
          <h2 className="text-lg font-medium mb-4">System Health</h2>

          {loading && (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Checking services...
            </div>
          )}

          {error && (
            <div className="text-destructive text-sm">
              Failed to check health: {error}
            </div>
          )}

          {health && (
            <div className="border rounded-lg divide-y">
              <ServiceRow name="Database" status={health.database.status} />
              <ServiceRow name="Qdrant" status={health.qdrant.status} />
              <ServiceRow name="Ollama" status={health.ollama.status} />
            </div>
          )}
        </section>

        <section>
          <h2 className="text-lg font-medium mb-4">About</h2>
          <div className="text-sm text-muted-foreground space-y-1">
            <p>Nexus v0.1.0</p>
            <p>RAG-powered search over company documents</p>
            <p>Stack: FastAPI + Next.js + Qdrant + PostgreSQL + Ollama</p>
          </div>
        </section>
      </div>
    </div>
  );
}

function ServiceRow({ name, status }: { name: string; status: string }) {
  const isOk = status === "ok";
  return (
    <div className="flex items-center justify-between px-4 py-3">
      <span className="font-medium">{name}</span>
      <div className="flex items-center gap-2">
        {isOk ? (
          <CheckCircle className="h-4 w-4 text-[#a0e06f]" />
        ) : (
          <XCircle className="h-4 w-4 text-destructive" />
        )}
        <span className={isOk ? "text-[#a0e06f]" : "text-destructive"}>
          {status}
        </span>
      </div>
    </div>
  );
}
