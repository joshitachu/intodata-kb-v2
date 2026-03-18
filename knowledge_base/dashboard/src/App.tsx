import { useState, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import AddRepoDialog from "./components/AddRepoDialog";
import DocPreview from "./components/DocPreview";
import KpiBar from "./components/KpiBar";
import LogPanel from "./components/LogPanel";
import ProjectList from "./components/ProjectList";
import ProjectSettings from "./components/ProjectSettings";
import RunHistory from "./components/RunHistory";
import ScanHistory from "./components/ScanHistory";
import { useLogStream } from "./hooks/useLogStream";
import { useProjects } from "./hooks/useProjects";
import { useAuth, getLoginUrl } from "./hooks/useAuth";
import type { AuthUser } from "./hooks/useAuth";
import type { GenerateRun } from "./types";

export default function App() {
  const auth = useAuth();

  if (auth.loading) {
    return (
      <div className="min-h-screen bg-[#0A0A0F] flex items-center justify-center">
        <p className="text-slate-500 text-sm font-mono">Verifying...</p>
      </div>
    );
  }

  if (!auth.authenticated) {
    return null;
  }

  return <AppContent isAuthenticated={auth.authenticated} user={auth.user} />;
}

type DetailTab = "generate" | "scans" | "docs" | "settings";

function AppContent({ isAuthenticated, user }: { isAuthenticated: boolean; user: AuthUser | null }) {
  const [showAddRepo, setShowAddRepo] = useState(false);
  const [activeProject, setActiveProject] = useState<string | null>(null);
  const [logOpen, setLogOpen] = useState(true);
  const [detailTab, setDetailTab] = useState<DetailTab>("generate");
  const [selectedLogFile, setSelectedLogFile] = useState<string | null>(null);
  const { logs, clear, done, logFile } = useLogStream(activeProject);
  const { data: projectsData } = useProjects();
  const queryClient = useQueryClient();

  const activeProjectObj = projectsData?.projects.find((p) => p.id === activeProject) ?? null;
  const projects = projectsData?.projects ?? [];

  async function handleLogout() {
    await fetch("/auth/logout", { method: "POST", credentials: "include" });
    window.location.href = getLoginUrl();
  }

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
    <div className="min-h-screen flex flex-col bg-[#0A0A0F]">
      {/* Header */}
      <header className="sticky top-0 z-30 bg-[#0A0A0F]/80 backdrop-blur-md border-b border-white/[0.06]">
        <div className="max-w-7xl mx-auto px-6 py-3.5 flex items-center justify-between">
          <h1 className="text-lg font-semibold tracking-tight text-slate-100">
            intodoc
          </h1>
          <div className="flex items-center gap-3">
            {user && (
              <span className="text-sm text-slate-500">
                {user.username}
                {user.is_superadmin && (
                  <span className="ml-1.5 px-1.5 py-0.5 text-xs border border-amber-500/40 text-amber-400 rounded-sm font-medium">
                    admin
                  </span>
                )}
              </span>
            )}
            {isAuthenticated && (
              <button
                className="btn-ghost-cyan"
                onClick={() => setShowAddRepo(true)}
              >
                Add Repo
              </button>
            )}
            <button
              className="px-3 py-1.5 text-sm text-slate-500 hover:text-slate-300 border border-white/[0.1] rounded hover:border-white/[0.2] transition-all"
              onClick={handleLogout}
            >
              Logout
            </button>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 py-6">
        <div className="max-w-7xl mx-auto px-6 space-y-6">
          <KpiBar projects={projects} />

          <ProjectList
            onAction={(id) => setActiveProject(id)}
            activeProject={activeProject}
            canManageProjects={isAuthenticated}
            onSelect={(id) => {
              setActiveProject(id);
              setSelectedLogFile(null);
              setLogOpen(true);
              setDetailTab("generate");
            }}
          />

          {/* Detail panels (tabbed) */}
          {activeProject && (
            <div>
              <div className="flex items-center gap-1 mb-3 border-b border-white/[0.06]">
                {tabs.map((tab) => (
                  <button
                    key={tab.key}
                    className={`tab-btn ${
                      detailTab === tab.key ? "tab-btn-active" : "tab-btn-inactive"
                    }`}
                    onClick={() => setDetailTab(tab.key)}
                  >
                    {tab.label}
                  </button>
                ))}
                <span className="ml-auto text-xs text-slate-600 pb-2 font-mono">{activeProject}</span>
              </div>

              {detailTab === "generate" && (
                <RunHistory projectId={activeProject} onViewLog={handleViewLog} />
              )}
              {detailTab === "scans" && <ScanHistory projectId={activeProject} />}
              {detailTab === "docs" && <DocPreview projectId={activeProject} />}
              {detailTab === "settings" && <ProjectSettings project={activeProjectObj} />}
            </div>
          )}
        </div>
      </main>

      {/* Log panel */}
      <div className="max-w-7xl mx-auto w-full px-6 pb-6">
        <div className="flex items-center justify-between mb-2">
          <button
            className="text-xs font-mono uppercase tracking-widest text-slate-500 hover:text-slate-300 transition-colors"
            onClick={() => setLogOpen(!logOpen)}
          >
            [ {logOpen ? "hide logs" : "show logs"} ]
            {activeProject && (
              <span className="ml-2 text-slate-600">({activeProject})</span>
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
