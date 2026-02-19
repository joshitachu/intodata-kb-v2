import { useEffect, useRef, useState, useCallback } from "react";
import type { LogEvent } from "../types";

export function useLogStream(projectId: string | null) {
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);

  const clear = useCallback(() => setLogs([]), []);

  useEffect(() => {
    if (!projectId) return;

    setLogs([]);
    const es = new EventSource(`/logs/stream?project_id=${projectId}`);
    esRef.current = es;

    es.onmessage = (event) => {
      try {
        const data: LogEvent = JSON.parse(event.data);
        setLogs((prev) => [...prev, data]);
      } catch {
        // ignore parse errors
      }
    };

    es.onerror = () => {
      // EventSource auto-reconnects
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [projectId]);

  return { logs, clear };
}
