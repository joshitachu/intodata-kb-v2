interface Props {
  status: string;
}

const chipColors: Record<string, string> = {
  ready: "border-accent-primary/40 text-accent-primary",
  registered: "border-slate-500/40 text-slate-400",
  scanning: "border-yellow-500/40 text-yellow-400",
  generating: "border-[#6370b0]/40 text-[#6370b0]",
  error: "border-red-400/40 text-red-400",
};

const labels: Record<string, string> = {
  ready: "READY",
  registered: "REGISTERED",
  scanning: "SCANNING",
  generating: "GENERATING",
  error: "ERROR",
};

export default function StatusBadge({ status }: Props) {
  const colors = chipColors[status] ?? "border-slate-500/40 text-slate-500";
  const label = labels[status] ?? status.toUpperCase();
  const showPulse = status === "ready";
  const showPing = status === "scanning" || status === "generating";

  return (
    <span className={`status-chip ${colors}`}>
      {showPulse && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="animate-pulse-dot absolute inline-flex h-full w-full rounded-full bg-current" />
          <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-current" />
        </span>
      )}
      {showPing && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-current opacity-75" />
          <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-current" />
        </span>
      )}
      {label}
    </span>
  );
}
