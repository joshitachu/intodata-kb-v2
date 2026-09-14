import { useState, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import AddRepoDialog from "./components/AddRepoDialog";
import DocPreview from "./components/DocPreview";
import KpiBar from "./components/KpiBar";
import LogPanel from "./components/LogPanel";
import ProjectList from "./components/ProjectList";
import ProjectSettings from "./components/ProjectSettings";
import QualityHistoryPanel from "./components/QualityHistoryPanel";
import RunHistory from "./components/RunHistory";
import ScanHistory from "./components/ScanHistory";
import ValidationReviewPanel from "./components/ValidationReviewPanel";
import ABComparison from "./components/ABComparison";
import HelpPanel from "./components/HelpPanel";
import FleetPage from "./components/FleetPage";
import ModelsPage from "./components/ModelsPage";
import ConnectorsPage from "./components/connectors/ConnectorsPage";
import { useLogStream } from "./hooks/useLogStream";
import { useProjects } from "./hooks/useProjects";
import { useAuth, getLoginUrl } from "./hooks/useAuth";
import { csrfHeaders } from "./api/client";
import { loadableFromQuery, mapLoadable } from "./lib/loadable";
import type { AuthUser } from "./hooks/useAuth";
import type { GenerateRun } from "./types";

const FLEET_ENABLED = import.meta.env.VITE_FLEET_ENABLED === "true";

type TopNav = "projects" | "docs" | "fleet" | "models" | "connectors";

export default function App() {
  const auth = useAuth();

  if (auth.loading) {
    return (
      <div className="min-h-screen bg-surface-bg flex items-center justify-center">
        <p className="text-slate-500 text-sm font-mono">Verifying...</p>
      </div>
    );
  }

  if (!auth.authenticated) {
    return null;
  }

  return <AppContent isAuthenticated={auth.authenticated} user={auth.user} />;
}

type DetailTab = "generate" | "scans" | "docs" | "compare" | "settings";

function AppContent({ isAuthenticated, user }: { isAuthenticated: boolean; user: AuthUser | null }) {
  const [showAddRepo, setShowAddRepo] = useState(false);
  const [activeProject, setActiveProject] = useState<string | null>(null);
  const [logOpen, setLogOpen] = useState(true);
  const [detailTab, setDetailTab] = useState<DetailTab>("generate");
  const [selectedLogFile, setSelectedLogFile] = useState<string | null>(null);
  const [compareRunA, setCompareRunA] = useState<number | null>(null);
  const [compareRunB, setCompareRunB] = useState<number | null>(null);
  const [topNav, setTopNav] = useState<TopNav>("projects");
  const { logs, clear, done, logFile } = useLogStream(activeProject);
  const projectsQuery = useProjects();
  const queryClient = useQueryClient();

  const activeProjectObj = projectsQuery.data?.projects.find((p) => p.id === activeProject) ?? null;
  // The real query state, not `?? []`. `KpiBar` aggregates over this list, and
  // an empty array would have it report a confident zero for a list nobody has
  // received yet. `ProjectList` reads the same shared query itself and already
  // renders its own skeleton/error states.
  const projects = mapLoadable(loadableFromQuery(projectsQuery), (data) => data.projects);
  const canViewConnectors = Boolean(user?.is_superadmin || user?.permissions.includes("view_connectors") || user?.permissions.includes("manage_connectors"));
  const canManageConnectors = Boolean(user?.is_superadmin || user?.permissions.includes("manage_connectors"));

  async function handleLogout() {
    await fetch("/auth/logout", { method: "POST", credentials: "include", headers: csrfHeaders() });
    queryClient.clear();
    window.location.href = getLoginUrl();
  }

  useEffect(() => {
    if (!done || !activeProject) return;
    if (logFile) {
      setSelectedLogFile(logFile);
      setLogOpen(true);
    }
    // Everything derived from a finished run is invalidated HERE, at the event
    // that changed it. This is what replaces the removed global 5s poll: it is
    // exact, it costs one request per affected panel instead of one every five
    // seconds forever, and the panels that were silently relying on that poll
    // for their post-run refresh (`docs`, `project-usage`, `quality-history`)
    // are now listed explicitly rather than left to a default that no longer
    // exists.
    queryClient.invalidateQueries({ queryKey: ["generate-runs", activeProject] });
    queryClient.invalidateQueries({ queryKey: ["scan-runs", activeProject] });
    queryClient.invalidateQueries({ queryKey: ["scan-status", activeProject] });
    queryClient.invalidateQueries({ queryKey: ["log-files", activeProject] });
    queryClient.invalidateQueries({ queryKey: ["docs", activeProject] });
    queryClient.invalidateQueries({ queryKey: ["project-usage", activeProject] });
    queryClient.invalidateQueries({ queryKey: ["quality-history", activeProject] });
    // The run's `_assessment/` evidence artifact only exists once the run
    // finishes; it is keyed by run id, so the whole prefix goes.
    queryClient.invalidateQueries({ queryKey: ["run-connector-evidence"] });
    queryClient.invalidateQueries({ queryKey: ["projects"] });
  }, [done, logFile, activeProject, queryClient]);

  function handleViewLog(run: GenerateRun) {
    if (run.log_file) {
      setSelectedLogFile(run.log_file);
      setLogOpen(true);
    }
  }

  function handleCompare(runAId: number, runBId: number) {
    setCompareRunA(runAId);
    setCompareRunB(runBId);
    setDetailTab("compare");
  }

  const tabs: { key: DetailTab; label: string }[] = [
    { key: "generate", label: "Generation History" },
    { key: "scans", label: "Scan History" },
    { key: "docs", label: "Documentation" },
    { key: "compare", label: "A/B Compare" },
    { key: "settings", label: "Settings" },
  ];

  return (
    <div className="min-h-screen flex flex-col bg-surface-bg">
      {/* Header */}
      <header className="sticky top-0 z-30 bg-surface-bg/80 backdrop-blur-md border-b border-white/[0.06]">
        <div className="max-w-7xl mx-auto px-6 py-3.5 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-2.5">
              <span className="text-sm font-semibold text-slate-200 tracking-wide">Nexus</span>
            </div>
            <nav className="flex items-center gap-1">
              {(["projects", "docs"] as TopNav[]).map((nav) => (
                <button
                  key={nav}
                  className={`px-3 py-1.5 text-sm rounded transition-all ${
                    topNav === nav
                      ? "text-slate-200 bg-white/[0.06]"
                      : "text-slate-500 hover:text-slate-300"
                  }`}
                  onClick={() => setTopNav(nav)}
                >
                  {nav.charAt(0).toUpperCase() + nav.slice(1)}
                </button>
              ))}
              {FLEET_ENABLED && user?.is_superadmin && (
                <button
                  className={`px-3 py-1.5 text-sm rounded transition-all ${
                    topNav === "fleet"
                      ? "text-slate-200 bg-white/[0.06]"
                      : "text-slate-500 hover:text-slate-300"
                  }`}
                  onClick={() => setTopNav("fleet")}
                >
                  Fleet
                </button>
              )}
              {user?.is_superadmin && (
                <button
                  className={`px-3 py-1.5 text-sm rounded transition-all ${
                    topNav === "models"
                      ? "text-slate-200 bg-white/[0.06]"
                      : "text-slate-500 hover:text-slate-300"
                  }`}
                  onClick={() => setTopNav("models")}
                >
                  Models
                </button>
              )}
              {canViewConnectors && (
                <button
                  className={`px-3 py-1.5 text-sm rounded transition-all ${
                    topNav === "connectors"
                      ? "text-slate-200 bg-white/[0.06]"
                      : "text-slate-500 hover:text-slate-300"
                  }`}
                  onClick={() => setTopNav("connectors")}
                >
                  Connectors
                </button>
              )}
            </nav>
          </div>
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
                className="btn-ghost"
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
          {/* Projects page */}
          {topNav === "projects" && (
            <>
              <KpiBar projects={projects} />

              <ProjectList
                onAction={(id) => setActiveProject(id)}
                onManageRuntimeEvidence={(id) => {
                  setActiveProject(id);
                  setTopNav("projects");
                  setDetailTab("settings");
                }}
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
                    <RunHistory
                      projectId={activeProject}
                      onViewLog={handleViewLog}
                      onCompare={handleCompare}
                    />
                  )}
                  {detailTab === "scans" && <ScanHistory projectId={activeProject} />}
                  {detailTab === "docs" && <DocPreview projectId={activeProject} />}
                  {detailTab === "compare" && (
                    <div className="space-y-4">
                      <ABComparison projectId={activeProject} preloadRunA={compareRunA} preloadRunB={compareRunB} />
                      <QualityHistoryPanel projectId={activeProject} />
                      <ValidationReviewPanel projectId={activeProject} />
                    </div>
                  )}
                  {detailTab === "settings" && (
                    <ProjectSettings
                      project={activeProjectObj}
                      onDeleted={() => setActiveProject(null)}
                    />
                  )}
                </div>
              )}
            </>
          )}

          {/* Help / reference panel */}
          {topNav === "docs" && <HelpPanel />}

          {/* Documentation Pack workspace; legacy registry editing is admin-only inside it. */}

          {/* Fleet / Mission Control — nexus operator server only */}
          {topNav === "fleet" && FLEET_ENABLED && user?.is_superadmin && <FleetPage />}

          {/* Models — per-VPS default model overrides (superadmin only) */}
          {topNav === "models" && user?.is_superadmin && <ModelsPage />}

          {topNav === "connectors" && canViewConnectors && <ConnectorsPage canManage={canManageConnectors} />}

        </div>
      </main>

      {/* Log panel (only on projects page) */}
      {topNav === "projects" && (
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
      )}

      {/* Add repo modal */}
      <AddRepoDialog open={showAddRepo} onClose={() => setShowAddRepo(false)} />
    </div>
  );
}
