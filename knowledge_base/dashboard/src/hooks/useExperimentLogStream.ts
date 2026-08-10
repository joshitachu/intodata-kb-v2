import { useEffect, useRef, useState, useCallback } from "react";
import type { LogEvent } from "../types";

/**
 * Live log stream for one Grounded/OpenCode experiment. Mirrors useLogStream,
 * keyed by experiment_id instead of project_id -- the backend reuses the same
 * wire shape (LogEvent), so the `project_id` field here actually carries the
 * experiment id (see mcp_server.services.log_manager.experiment_log_manager).
 */
export function useExperimentLogStream(experimentId: string | null) {
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [done, setDone] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  const clear = useCallback(() => {
    setLogs([]);
    setDone(false);
  }, []);

  useEffect(() => {
    setLogs([]);
    setDone(false);
    esRef.current?.close();
    esRef.current = null;

    if (!experimentId) return;

    const es = new EventSource(
      `/mcp/agentic-experiments/${encodeURIComponent(experimentId)}/events/stream`,
    );
    esRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as Partial<LogEvent>;
        if (esRef.current !== es || data.project_id !== experimentId) {
          return;
        }
        if (data.level === "done") {
          setDone(true);
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
  }, [experimentId]);

  return { logs, clear, done };
}
