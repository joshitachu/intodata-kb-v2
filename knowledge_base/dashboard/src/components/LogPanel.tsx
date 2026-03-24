import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useLogFiles } from "../hooks/useProjects";
import type { LogEvent } from "../types";

interface Props {
  logs: LogEvent[];
  onClear: () => void;
  projectId: string | null;
  selectedLogFile: string | null;
  onLogFileCleared: () => void;
}

const levelColors: Record<string, string> = {
  error: "text-red-400",
  warning: "text-yellow-400",
  info: "text-[#a0e06f]",
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

  useEffect(() => {
    if (mode === "live") {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs.length, mode]);

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
    <div className="bg-surface-deep rounded border border-white/[0.07]">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/[0.05]">
        <div className="flex items-center gap-3">
          <h3 className="text-xs font-mono uppercase tracking-widest text-slate-500">Logs</h3>
          <select
            className="bg-surface-raised text-slate-300 text-xs font-mono border border-white/[0.07] rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-accent-primary/50"
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
            className="text-xs text-slate-500 hover:text-slate-300 transition-colors font-mono"
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
              <p className="text-slate-600">
                No logs yet. Trigger an action to see output here.
              </p>
            )}
            {logs.map((log, i) => (
              <div key={i} className={levelColors[log.level] ?? "text-slate-400"}>
                {log.message}
              </div>
            ))}
            <div ref={bottomRef} />
          </>
        ) : loading ? (
          <p className="text-slate-600">Loading log file...</p>
        ) : (
          <pre className="text-slate-400 whitespace-pre-wrap">
            {historicalContent || "No content."}
          </pre>
        )}
      </div>
    </div>
  );
}
