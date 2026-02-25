import { useEffect, useRef, useState, useCallback } from "react";
import type { LogEvent } from "../types";

export function useLogStream(projectId: string | null) {
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [done, setDone] = useState(false);
  const [logFile, setLogFile] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const clear = useCallback(() => {
    setLogs([]);
    setDone(false);
    setLogFile(null);
  }, []);

  useEffect(() => {
    // Reset done state when project changes
    setDone(false);
    setLogFile(null);

    if (!projectId) return;

    const es = new EventSource(`/logs/stream?project_id=${projectId}`);
    esRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.level === "done") {
          setDone(true);
          setLogFile(data.log_file ?? null);
        } else {
          setLogs((prev) => [...prev, data as LogEvent]);
        }
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

  return { logs, clear, done, logFile };
}
