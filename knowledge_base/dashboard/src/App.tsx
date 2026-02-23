import { useState } from "react";
import AddRepoDialog from "./components/AddRepoDialog";
import LogPanel from "./components/LogPanel";
import ProjectList from "./components/ProjectList";
import { useLogStream } from "./hooks/useLogStream";

export default function App() {
  const [showAddRepo, setShowAddRepo] = useState(false);
  const [activeProject, setActiveProject] = useState<string | null>(null);
  const [logOpen, setLogOpen] = useState(true);
  const { logs, clear } = useLogStream(activeProject);

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
      <main className="flex-1 p-6">
        <div className="bg-white rounded-lg shadow border">
          <ProjectList onAction={(id) => setActiveProject(id)} />
        </div>
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
        {logOpen && <LogPanel logs={logs} onClear={clear} />}
      </div>

      {/* Add repo modal */}
      <AddRepoDialog open={showAddRepo} onClose={() => setShowAddRepo(false)} />
    </div>
  );
}
