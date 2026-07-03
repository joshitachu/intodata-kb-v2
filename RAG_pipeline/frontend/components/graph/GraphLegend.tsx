interface GroupColor {
  bg: string;
  border: string;
  text: string;
}

interface GraphLegendProps {
  groupColorMap: Record<string, GroupColor>;
  collapsedMode?: boolean;
}

export function GraphLegend({ groupColorMap, collapsedMode }: GraphLegendProps) {
  const groups = Object.entries(groupColorMap);

  return (
    <div className="absolute bottom-3 left-3 bg-white/90 backdrop-blur-md border border-slate-200 rounded-xl p-3.5 text-xs shadow-lg max-w-[240px] z-10">
      <div className="font-bold mb-2.5 text-slate-800 text-[11px] uppercase tracking-wider">Legend</div>

      {/* Edge types */}
      <div className="space-y-1.5 mb-2.5 pb-2.5 border-b border-slate-100">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-0 border-t-[2px] border-blue-500 relative">
            <div className="absolute -right-0.5 -top-[3px] w-0 h-0 border-l-[4px] border-l-blue-500 border-y-[3px] border-y-transparent" />
          </div>
          <span className="text-slate-500">Component orchestration</span>
        </div>
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-0 border-t-[1.5px] border-purple-400 border-dashed" />
          <span className="text-slate-500">Connection usage</span>
        </div>
        {collapsedMode && (
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-0 border-t-[2.5px] border-blue-500" />
            <span className="text-slate-500">Inter-group (aggregated)</span>
          </div>
        )}
      </div>

      {/* Node types */}
      <div className="space-y-1.5 mb-2.5 pb-2.5 border-b border-slate-100">
        <div className="flex items-center gap-2.5">
          <div className="w-4 h-3 rounded border-[1.5px] border-blue-400" style={{ background: "linear-gradient(135deg, #dbeafe, white)" }} />
          <span className="text-slate-500">Component</span>
        </div>
        <div className="flex items-center gap-2.5">
          <div className="w-4 h-3 rounded border border-purple-300" style={{ background: "linear-gradient(135deg, #faf5ff, white)" }} />
          <span className="text-slate-500">Connection</span>
        </div>
        {collapsedMode && (
          <div className="flex items-center gap-2.5">
            <div className="w-4 h-3 rounded border-[1.5px] border-indigo-300 bg-white overflow-hidden flex">
              <div className="w-1 h-full bg-indigo-400" />
            </div>
            <span className="text-slate-500">Group (click to expand)</span>
          </div>
        )}
      </div>

      {/* Group colors */}
      {groups.length > 0 && (
        <div>
          <div className="font-semibold mb-1.5 text-slate-400 text-[10px] uppercase tracking-wider">Groups</div>
          <div className="grid grid-cols-2 gap-1.5">
            {groups.map(([name, color]) => (
              <div key={name} className="flex items-center gap-1.5">
                <div
                  className="w-3 h-3 rounded-[3px] border shrink-0"
                  style={{
                    background: `linear-gradient(135deg, ${color.bg}, white)`,
                    borderColor: `${color.border}60`,
                  }}
                />
                <span className="text-slate-500 truncate" title={name}>
                  {name}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
