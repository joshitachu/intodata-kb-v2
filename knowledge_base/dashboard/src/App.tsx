import { useState, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import AddRepoDialog from "./components/AddRepoDialog";
import DocPreview from "./components/DocPreview";
import LogPanel from "./components/LogPanel";
import ProjectList from "./components/ProjectList";
import ProjectSettings from "./components/ProjectSettings";
import RunHistory from "./components/RunHistory";
import ScanHistory from "./components/ScanHistory";
import { useLogStream } from "./hooks/useLogStream";
import { useProjects } from "./hooks/useProjects";
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

  const canManageProjects =
    auth.user?.is_superadmin ||
    auth.user?.permissions?.includes("manage_projects") ||
    false;

  return <AppContent canManageProjects={canManageProjects} />;
}

type DetailTab = "generate" | "scans" | "docs" | "settings";

function AppContent({ canManageProjects }: { canManageProjects: boolean }) {
  const [showAddRepo, setShowAddRepo] = useState(false);
  const [activeProject, setActiveProject] = useState<string | null>(null);
  const [logOpen, setLogOpen] = useState(true);
  const [detailTab, setDetailTab] = useState<DetailTab>("generate");
  const [selectedLogFile, setSelectedLogFile] = useState<string | null>(null);
  const { logs, clear, done, logFile } = useLogStream(activeProject);
  const { data: projectsData } = useProjects();
  const queryClient = useQueryClient();

  const activeProjectObj = projectsData?.projects.find((p) => p.id === activeProject) ?? null;

  // When generation completes, switch to the historical log file and refresh queries
  useEffect(() => {
    if (!done || !activeProject) return;
    if (logFile) {
      setSelectedLogFile(logFile);
      setLogOpen(true);
    }
    queryClient.invalidateQueries({ queryKey: ["generate-runs", activeProject] });
    queryClient.invalidateQueries({ queryKey: ["scan-runs", activeProject] });
    queryClient.invalidateQueries({ queryKey: ["log-files", activeProject] });
    queryClient.invalidateQueries({ queryKey: ["projects"] });
  }, [done, logFile, activeProject, queryClient]);

  function handleViewLog(run: GenerateRun) {
    if (run.log_file) {
      setSelectedLogFile(run.log_file);
      setLogOpen(true);
    }
  }

  const tabs: { key: DetailTab; label: string }[] = [
    { key: "generate", label: "Generation History" },
    { key: "scans", label: "Scan History" },
    { key: "docs", label: "Documentation" },
    { key: "settings", label: "Settings" },
  ];

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="bg-white border-b px-6 py-4 flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-800">intodoc Control Panel</h1>
        {canManageProjects && (
          <button
            className="px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700"
            onClick={() => setShowAddRepo(true)}
          >
            Add Repo
          </button>
        )}
      </header>

      {/* Project table */}
      <main className="flex-1 p-6 space-y-4">
        <div className="bg-white rounded-lg shadow border">
          <ProjectList
            onAction={(id) => setActiveProject(id)}
            activeProject={activeProject}
            canManageProjects={canManageProjects}
            onSelect={(id) => {
              setActiveProject(id);
              setSelectedLogFile(null);
              setLogOpen(true);
              setDetailTab("generate");
            }}
          />
        </div>

        {/* Detail panels (tabbed) */}
        {activeProject && (
          <div>
            <div className="flex items-center gap-1 mb-3 border-b">
              {tabs.map((tab) => (
                <button
                  key={tab.key}
                  className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                    detailTab === tab.key
                      ? "border-blue-600 text-blue-600"
                      : "border-transparent text-gray-500 hover:text-gray-700"
                  }`}
                  onClick={() => setDetailTab(tab.key)}
                >
                  {tab.label}
                </button>
              ))}
              <span className="ml-auto text-xs text-gray-400 pb-2">{activeProject}</span>
            </div>

            {detailTab === "generate" && (
              <RunHistory projectId={activeProject} onViewLog={handleViewLog} />
            )}
            {detailTab === "scans" && (
              <ScanHistory projectId={activeProject} />
            )}
            {detailTab === "docs" && (
              <DocPreview projectId={activeProject} />
            )}
            {detailTab === "settings" && (
              <ProjectSettings project={activeProjectObj} />
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
