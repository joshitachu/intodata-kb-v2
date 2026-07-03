import { ArrowDownUp, ChevronDown, ChevronUp, Eye, EyeOff, Layers, Maximize, Minimize2, Search, X } from "lucide-react";
import type { GroupColor } from "@/lib/graphTransform";

interface GraphToolbarProps {
  direction: "TB" | "LR";
  showConnections: boolean;
  stats: {
    jobCount: number;
    connectionCount: number;
    edgeCount: number;
    groupCount: number;
  };
  onToggleDirection: () => void;
  onToggleConnections: () => void;
  onFitView: () => void;
  groups: string[];
  groupColorMap: Record<string, GroupColor>;
  hiddenGroups: Set<string>;
  onToggleGroup: (group: string) => void;
  onShowAllGroups: () => void;
  onHideAllGroups: () => void;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  compactMode: boolean;
  onToggleCompact: () => void;
  collapsedMode: boolean;
  onCollapseAll: () => void;
  onExpandAll: () => void;
  expandedGroups: Set<string>;
}

function ToolbarButton({
  onClick,
  active,
  title,
  children,
}: {
  onClick: () => void;
  active?: boolean;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] font-medium rounded-lg border transition-all duration-150
        ${active
          ? "bg-slate-800 text-white border-slate-700 shadow-sm"
          : "bg-white text-slate-600 border-slate-200 hover:bg-slate-50 hover:border-slate-300"
        }`}
      title={title}
    >
      {children}
    </button>
  );
}

export function GraphToolbar({
  direction,
  showConnections,
  stats,
  onToggleDirection,
  onToggleConnections,
  onFitView,
  groups,
  groupColorMap,
  hiddenGroups,
  onToggleGroup,
  onShowAllGroups,
  onHideAllGroups,
  searchQuery,
  onSearchChange,
  compactMode,
  onToggleCompact,
  collapsedMode,
  onCollapseAll,
  onExpandAll,
  expandedGroups,
}: GraphToolbarProps) {
  return (
    <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-2 bg-white/80 backdrop-blur-sm flex-wrap">
      {/* View mode toggle */}
      <div className="flex rounded-lg border border-slate-200 overflow-hidden shadow-sm">
        <button
          onClick={onCollapseAll}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-semibold transition-all duration-150 ${
            collapsedMode
              ? "bg-slate-800 text-white"
              : "bg-white text-slate-500 hover:bg-slate-50"
          }`}
          title="Overview: show collapsed group nodes"
        >
          <Layers className="h-3.5 w-3.5" />
          Overview
        </button>
        <button
          onClick={onExpandAll}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-semibold border-l border-slate-200 transition-all duration-150 ${
            !collapsedMode
              ? "bg-slate-800 text-white"
              : "bg-white text-slate-500 hover:bg-slate-50"
          }`}
          title="Detail: show all individual component nodes"
        >
          Detail
        </button>
      </div>

      <div className="w-px h-5 bg-slate-200" />

      {/* Controls */}
      <div className="flex items-center gap-1.5">
        <ToolbarButton onClick={onToggleDirection} title={`Switch to ${direction === "TB" ? "left-right" : "top-down"} layout`}>
          <ArrowDownUp className="h-3.5 w-3.5" />
          {direction === "TB" ? "Top-Down" : "Left-Right"}
        </ToolbarButton>
        <ToolbarButton onClick={onToggleConnections} active={showConnections} title={showConnections ? "Hide connections" : "Show connections"}>
          {showConnections ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
          Conn
        </ToolbarButton>
        <ToolbarButton onClick={onToggleCompact} active={compactMode} title={compactMode ? "Switch to detailed view" : "Switch to compact view"}>
          <Minimize2 className="h-3.5 w-3.5" />
          {compactMode ? "Detailed" : "Compact"}
        </ToolbarButton>
        <ToolbarButton onClick={onFitView} title="Fit graph to view">
          <Maximize className="h-3.5 w-3.5" />
          Fit
        </ToolbarButton>
      </div>

      <div className="w-px h-5 bg-slate-200" />

      {/* Group pills */}
      {groups.length > 0 && (
        <div className="flex items-center gap-1 flex-wrap">
          {collapsedMode ? (
            <button
              onClick={onShowAllGroups}
              className="px-2 py-1 text-[10px] rounded-full border border-slate-200 bg-white hover:bg-slate-50 transition-colors font-semibold text-slate-500"
              title="Collapse all groups"
            >
              Reset
            </button>
          ) : (
            <>
              <button
                onClick={onShowAllGroups}
                className="px-2 py-1 text-[10px] rounded-full border border-slate-200 bg-white hover:bg-slate-50 transition-colors font-semibold text-slate-500"
              >
                All
              </button>
              <button
                onClick={onHideAllGroups}
                className="px-2 py-1 text-[10px] rounded-full border border-slate-200 bg-white hover:bg-slate-50 transition-colors font-semibold text-slate-500"
              >
                None
              </button>
            </>
          )}
          {groups.map((group) => {
            const color = groupColorMap[group];
            if (collapsedMode) {
              const isExpanded = expandedGroups.has(group);
              return (
                <button
                  key={group}
                  onClick={() => onToggleGroup(group)}
                  className="px-2 py-1 text-[10px] rounded-full border font-semibold transition-all duration-150 flex items-center gap-0.5"
                  style={{
                    background: isExpanded ? `linear-gradient(135deg, ${color?.bg}, white)` : "white",
                    borderColor: isExpanded ? color?.border : `${color?.border}40`,
                    color: color?.text || "#333",
                    opacity: isExpanded ? 1 : 0.55,
                    boxShadow: isExpanded ? `0 1px 3px ${color?.border}20` : "none",
                  }}
                  title={isExpanded ? `Collapse ${group}` : `Expand ${group}`}
                >
                  {isExpanded ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronUp className="h-3 w-3 shrink-0" />}
                  {group}
                </button>
              );
            }
            const isHidden = hiddenGroups.has(group);
            return (
              <button
                key={group}
                onClick={() => onToggleGroup(group)}
                className="px-2 py-1 text-[10px] rounded-full border font-semibold transition-all duration-150"
                style={{
                  background: isHidden ? "white" : `linear-gradient(135deg, ${color?.bg}, white)`,
                  borderColor: isHidden ? `${color?.border}30` : `${color?.border}60`,
                  color: color?.text || "#333",
                  opacity: isHidden ? 0.35 : 1,
                  boxShadow: isHidden ? "none" : `0 1px 3px ${color?.border}15`,
                }}
                title={isHidden ? `Show ${group}` : `Hide ${group}`}
              >
                {group}
              </button>
            );
          })}
        </div>
      )}

      <div className="w-px h-5 bg-slate-200" />

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-300 pointer-events-none" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Search nodes..."
          className="w-44 pl-8 pr-7 py-1.5 text-[11px] rounded-lg border border-slate-200 bg-white focus:outline-none focus:ring-2 focus:ring-slate-200 focus:border-slate-300 placeholder:text-slate-300 transition-all"
        />
        {searchQuery && (
          <button
            onClick={() => onSearchChange("")}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 transition-colors"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {/* Stats */}
      <div className="flex items-center gap-2.5 text-[11px] text-slate-400 ml-auto font-medium">
        <span>{stats.jobCount} <span className="text-slate-300">components</span></span>
        <span className="text-slate-200">|</span>
        <span>{stats.connectionCount} <span className="text-slate-300">conn</span></span>
        <span className="text-slate-200">|</span>
        <span>{stats.groupCount} <span className="text-slate-300">groups</span></span>
        {collapsedMode && expandedGroups.size > 0 && (
          <>
            <span className="text-slate-200">|</span>
            <span className="text-blue-500 font-semibold">
              {expandedGroups.size}/{stats.groupCount} expanded
            </span>
          </>
        )}
      </div>
    </div>
  );
}
