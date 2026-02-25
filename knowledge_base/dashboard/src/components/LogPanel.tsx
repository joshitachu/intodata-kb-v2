import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useLogFiles } from "../hooks/useProjects";
import type { LogEvent } from "../types";

interface Props {
  logs: LogEvent[];
  onClear: () => void;
  projectId: string | null;
  /** When set, auto-switch to this historical log file */
  selectedLogFile: string | null;
  onLogFileCleared: () => void;
}

const levelColors: Record<string, string> = {
  error: "text-red-400",
  warning: "text-yellow-400",
  info: "text-green-400",
};

export default function LogPanel({
  logs,
  onClear,
  projectId,
  selectedLogFile,
  onLogFileCleared,
}: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<"live" | "historical">("live");
  const [historicalContent, setHistoricalContent] = useState<string>("");
  const [activeFile, setActiveFile] = useState<string>("");
  const [loading, setLoading] = useState(false);

  const { data: logFilesData } = useLogFiles(projectId);
  const logFiles = logFilesData?.files ?? [];

  // Auto-scroll for live logs
  useEffect(() => {
    if (mode === "live") {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs.length, mode]);

  // Handle external "View Log" trigger
  useEffect(() => {
    if (selectedLogFile && projectId) {
      setMode("historical");
      setActiveFile(selectedLogFile);
      loadLogFile(projectId, selectedLogFile);
      onLogFileCleared();
    }
  }, [selectedLogFile]); // eslint-disable-line react-hooks/exhaustive-deps

  async function loadLogFile(pid: string, filename: string) {
    setLoading(true);
    try {
      const res = await api.getLogContent(pid, filename);
      setHistoricalContent(res.content);
    } catch {
      setHistoricalContent("Failed to load log file.");
    }
    setLoading(false);
  }

  function handleModeChange(value: string) {
    if (value === "live") {
      setMode("live");
      setActiveFile("");
      setHistoricalContent("");
    } else {
      setMode("historical");
      setActiveFile(value);
      if (projectId) loadLogFile(projectId, value);
    }
  }

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700">
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-700">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-medium text-gray-300">Logs</h3>
          <select
            className="bg-gray-800 text-gray-300 text-xs border border-gray-600 rounded px-2 py-1"
            value={mode === "live" ? "live" : activeFile}
            onChange={(e) => handleModeChange(e.target.value)}
          >
            <option value="live">Live</option>
            {logFiles.map((f) => (
              <option key={f.filename} value={f.filename}>
                {f.timestamp} ({Math.round(f.size_bytes / 1024)}KB)
              </option>
            ))}
          </select>
        </div>
        {mode === "live" && (
          <button
            className="text-xs text-gray-400 hover:text-white"
            onClick={onClear}
          >
            Clear
          </button>
        )}
      </div>
      <div className="h-64 overflow-y-auto p-3 font-mono text-xs">
        {mode === "live" ? (
          <>
            {logs.length === 0 && (
              <p className="text-gray-500 italic">
                No logs yet. Trigger an action to see output here.
              </p>
            )}
            {logs.map((log, i) => (
              <div key={i} className={levelColors[log.level] ?? "text-gray-300"}>
                {log.message}
              </div>
            ))}
            <div ref={bottomRef} />
          </>
        ) : loading ? (
          <p className="text-gray-500">Loading log file...</p>
        ) : (
          <pre className="text-gray-300 whitespace-pre-wrap">
            {historicalContent || "No content."}
          </pre>
        )}
      </div>
    </div>
  );
}
