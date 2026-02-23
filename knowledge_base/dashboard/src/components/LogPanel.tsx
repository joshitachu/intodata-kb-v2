import { useEffect, useRef } from "react";
import type { LogEvent } from "../types";

interface Props {
  logs: LogEvent[];
  onClear: () => void;
}

const levelColors: Record<string, string> = {
  error: "text-red-400",
  warning: "text-yellow-400",
  info: "text-green-400",
};

export default function LogPanel({ logs, onClear }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs.length]);

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700">
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-700">
        <h3 className="text-sm font-medium text-gray-300">Live Logs</h3>
        <button
          className="text-xs text-gray-400 hover:text-white"
          onClick={onClear}
        >
          Clear
        </button>
      </div>
      <div className="h-64 overflow-y-auto p-3 font-mono text-xs">
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
      </div>
    </div>
  );
}
