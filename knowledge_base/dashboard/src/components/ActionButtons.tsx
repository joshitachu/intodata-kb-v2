import { useScan, useGenerate, usePushToRag, useResetStatus } from "../hooks/useProjects";

interface Props {
  projectId: string;
  status: string;
  onAction: (projectId: string) => void;
}

export default function ActionButtons({ projectId, status, onAction }: Props) {
  const scan = useScan();
  const generate = useGenerate();
  const push = usePushToRag();
  const resetStatus = useResetStatus();

  const busy = status === "scanning" || status === "generating";

  return (
    <div className="flex gap-1.5 flex-wrap">
      <button
        className="px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
        disabled={busy || scan.isPending}
        onClick={() => {
          onAction(projectId);
          scan.mutate({ projectId, force: true });
        }}
      >
        Re-scan
      </button>
      <button
        className="px-2 py-1 text-xs bg-purple-600 text-white rounded hover:bg-purple-700 disabled:opacity-50"
        disabled={busy || generate.isPending}
        onClick={() => {
          onAction(projectId);
          generate.mutate(projectId);
        }}
      >
        Generate Docs
      </button>
      <button
        className="px-2 py-1 text-xs bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
        disabled={busy || push.isPending}
        onClick={() => {
          onAction(projectId);
          push.mutate(projectId);
        }}
      >
        Push to RAG
      </button>
      <button
        className="px-2 py-1 text-xs bg-gray-400 text-white rounded cursor-not-allowed"
        disabled
        title="Coming in Phase 4"
      >
        Generate IR
      </button>
      {busy && (
        <button
          className="px-2 py-1 text-xs bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50"
          disabled={resetStatus.isPending}
          onClick={() => resetStatus.mutate(projectId)}
        >
          Reset
        </button>
      )}
    </div>
  );
}
