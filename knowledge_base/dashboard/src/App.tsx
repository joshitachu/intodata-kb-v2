import { useState, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import AddRepoDialog from "./components/AddRepoDialog";
import LogPanel from "./components/LogPanel";
import ProjectList from "./components/ProjectList";
import RunHistory from "./components/RunHistory";
import { useLogStream } from "./hooks/useLogStream";
import { useAuth } from "./hooks/useAuth";
import type { GenerateRun } from "./types";

export default function App() {
  const auth = useAuth();

  if (auth.loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-gray-500">Verifying authentication...</p>
      </div>
    );
  }

  if (!auth.authenticated) {
    return null; // Redirecting to login
  }

  return <AppContent />;
}

function AppContent() {
  const [showAddRepo, setShowAddRepo] = useState(false);
  const [activeProject, setActiveProject] = useState<string | null>(null);
  const [logOpen, setLogOpen] = useState(true);
  const [runHistoryOpen, setRunHistoryOpen] = useState(true);
  const [selectedLogFile, setSelectedLogFile] = useState<string | null>(null);
  const { logs, clear, done, logFile } = useLogStream(activeProject);
  const queryClient = useQueryClient();

  // When generation completes, switch to the historical log file and refresh queries
  useEffect(() => {
    if (!done || !activeProject) return;
    if (logFile) {
      setSelectedLogFile(logFile);
      setLogOpen(true);
    }
    queryClient.invalidateQueries({ queryKey: ["generate-runs", activeProject] });
    queryClient.invalidateQueries({ queryKey: ["log-files", activeProject] });
    queryClient.invalidateQueries({ queryKey: ["projects"] });
  }, [done, logFile, activeProject, queryClient]);

  function handleViewLog(run: GenerateRun) {
    if (run.log_file) {
      setSelectedLogFile(run.log_file);
      setLogOpen(true);
    }
  }

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="bg-white border-b px-6 py-4 flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-800">intodoc Control Panel</h1>
        <button
          className="px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700"
          onClick={() => setShowAddRepo(true)}
        >
          Add Repo
        </button>
      </header>

      {/* Project table */}
      <main className="flex-1 p-6 space-y-4">
        <div className="bg-white rounded-lg shadow border">
          <ProjectList
            onAction={(id) => setActiveProject(id)}
            activeProject={activeProject}
            onSelect={(id) => {
              setActiveProject(id);
              setSelectedLogFile(null);
              setLogOpen(true);
              setRunHistoryOpen(true);
            }}
          />
        </div>

        {/* Run history (collapsible) */}
        {activeProject && (
          <div>
            <button
              className="text-sm text-gray-500 hover:text-gray-700 mb-2"
              onClick={() => setRunHistoryOpen(!runHistoryOpen)}
            >
              {runHistoryOpen ? "Hide" : "Show"} Generation History
              <span className="ml-2 text-xs text-gray-400">({activeProject})</span>
            </button>
            {runHistoryOpen && (
              <RunHistory projectId={activeProject} onViewLog={handleViewLog} />
            )}
          </div>
        )}
      </main>

      {/* Log panel */}
      <div className="px-6 pb-6">
        <div className="flex items-center justify-between mb-2">
          <button
            className="text-sm text-gray-500 hover:text-gray-700"
            onClick={() => setLogOpen(!logOpen)}
          >
            {logOpen ? "Hide Logs" : "Show Logs"}
            {activeProject && (
              <span className="ml-2 text-xs text-gray-400">({activeProject})</span>
            )}
          </button>
        </div>
        {logOpen && (
          <LogPanel
            logs={logs}
            onClear={clear}
            projectId={activeProject}
            selectedLogFile={selectedLogFile}
            onLogFileCleared={() => setSelectedLogFile(null)}
          />
        )}
      </div>

      {/* Add repo modal */}
      <AddRepoDialog open={showAddRepo} onClose={() => setShowAddRepo(false)} />
    </div>
  );
}
