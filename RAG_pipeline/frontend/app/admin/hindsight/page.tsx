"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  ChevronRight,
  Loader2,
  Search,
} from "lucide-react";

// --- Types ---

type Tab = "pipeline" | "banks" | "reflect";

interface BankInfo {
  bank_id: string;
  type: "system" | "user" | "project";
  label: string;
  exists: boolean;
  total_nodes: number;
  nodes_by_fact_type: Record<string, number>;
  total_observations: number;
}

interface BanksResponse {
  banks: BankInfo[];
  hindsight_enabled: boolean;
  funnel: { raw_memories: number; observations: number; mental_models: number };
}

interface Memory {
  content: string;
  timestamp: string;
  context: string;
  metadata: Record<string, string>;
  document_id: string | null;
  entities: string[];
  tags: string[];
  bank_id?: string;
  bank_type?: string;
  bank_label?: string;
}

interface MemoriesResponse {
  memories: Memory[];
  bank_id: string;
  total: number;
}

function memoryShape(factType: string): { label: string; color: string } {
  if (factType === "mental_model")
    return { label: "World", color: "bg-amber-100 text-amber-800" };
  if (factType === "observation")
    return { label: "Experience", color: "bg-blue-100 text-blue-800" };
  if (factType === "preference" || factType === "correction")
    return { label: "Opinion", color: "bg-purple-100 text-purple-800" };
  return { label: "Signal", color: "bg-gray-100 text-gray-700" };
}

function bankTypeBadge(type: string) {
  if (type === "system") return "bg-blue-100 text-blue-800";
  if (type === "user") return "bg-green-100 text-green-800";
  if (type === "project") return "bg-purple-100 text-purple-800";
  return "bg-gray-100 text-gray-700";
}

function relativeTime(ts: string): string {
  if (!ts) return "";
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

const PAGE_SIZE = 50;

// --- Component ---

export default function HindsightDashboard() {
  const [tab, setTab] = useState<Tab>("pipeline");
  const [loading, setLoading] = useState(true);
  const [banks, setBanks] = useState<BankInfo[]>([]);
  const [hindsightEnabled, setHindsightEnabled] = useState(true);
  const [funnel, setFunnel] = useState({
    raw_memories: 0,
    observations: 0,
    mental_models: 0,
  });

  // Pipeline tab
  const [recentCaptures, setRecentCaptures] = useState<Memory[]>([]);
  const [recentLoading, setRecentLoading] = useState(false);

  // Banks tab
  const [selectedBank, setSelectedBank] = useState<BankInfo | null>(null);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [memoriesLoading, setMemoriesLoading] = useState(false);
  const [memoriesPage, setMemoriesPage] = useState(0);
  const [memoriesTotal, setMemoriesTotal] = useState(0);
  const [expandedMemory, setExpandedMemory] = useState<number | null>(null);

  // Reflect tab
  const [reflectBank, setReflectBank] = useState("");
  const [reflection, setReflection] = useState("");
  const [reflectLoading, setReflectLoading] = useState(false);

  // --- Fetch functions ---

  const fetchBanks = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/memory/banks");
      if (res.ok) {
        const data: BanksResponse = await res.json();
        setBanks(data.banks);
        setHindsightEnabled(data.hindsight_enabled);
        setFunnel(data.funnel);
      }
    } catch {
      /* ignore */
    }
  }, []);

  const fetchRecent = useCallback(async () => {
    setRecentLoading(true);
    try {
      const res = await fetch("/api/admin/memory/recent?limit=15");
      if (res.ok) {
        const data = await res.json();
        setRecentCaptures(data.captures ?? []);
      }
    } catch {
      /* ignore */
    } finally {
      setRecentLoading(false);
    }
  }, []);

  const fetchMemories = useCallback(
    async (bankId: string, page: number) => {
      setMemoriesLoading(true);
      try {
        const offset = page * PAGE_SIZE;
        const res = await fetch(
          `/api/admin/memory/banks/${encodeURIComponent(bankId)}/memories?limit=${PAGE_SIZE}&offset=${offset}`,
        );
        if (res.ok) {
          const data: MemoriesResponse = await res.json();
          setMemories(data.memories);
          setMemoriesTotal(data.total);
        }
      } catch {
        /* ignore */
      } finally {
        setMemoriesLoading(false);
      }
    },
    [],
  );

  const fetchReflection = useCallback(async (bankId: string) => {
    setReflectLoading(true);
    try {
      const res = await fetch(
        `/api/admin/memory/banks/${encodeURIComponent(bankId)}/reflect`,
      );
      if (res.ok) {
        const data = await res.json();
        setReflection(data.reflection ?? "");
      }
    } catch {
      setReflection("Failed to fetch reflection.");
    } finally {
      setReflectLoading(false);
    }
  }, []);

  // --- Effects ---

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchBanks(), fetchRecent()]).then(() => setLoading(false));
  }, [fetchBanks, fetchRecent]);

  // --- Handlers ---

  function handleSelectBank(bank: BankInfo) {
    setSelectedBank(bank);
    setMemoriesPage(0);
    setExpandedMemory(null);
    fetchMemories(bank.bank_id, 0);
  }

  function handleBackToBanks() {
    setSelectedBank(null);
    setMemories([]);
    setMemoriesTotal(0);
    setExpandedMemory(null);
  }

  function handleMemoriesPage(page: number) {
    if (!selectedBank) return;
    setMemoriesPage(page);
    setExpandedMemory(null);
    fetchMemories(selectedBank.bank_id, page);
  }

  function handleReflect() {
    if (!reflectBank) return;
    fetchReflection(reflectBank);
  }

  // --- Disabled state ---

  const disabledBanner = (
    <div className="flex items-center gap-3 p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
      <AlertTriangle className="h-5 w-5 text-yellow-600 shrink-0" />
      <div>
        <p className="font-medium text-yellow-800">Memory system is disabled</p>
        <p className="text-sm text-yellow-700">
          Set <code className="bg-yellow-100 px-1 rounded">HINDSIGHT_ENABLED=true</code> to enable
          automatic knowledge capture.
        </p>
      </div>
    </div>
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
      </div>
    );
  }

  // Computed values
  const existingBanks = banks.filter((b) => b.exists);
  const systemBanks = banks.filter((b) => b.type === "system");
  const userBanks = banks.filter((b) => b.type === "user");
  const projectBanks = banks.filter((b) => b.type === "project");
  const banksWithMemories = existingBanks.filter((b) => b.total_nodes > 0);

  const tabs: { key: Tab; label: string }[] = [
    { key: "pipeline", label: "Insight Pipeline" },
    { key: "banks", label: "Memory Banks" },
    { key: "reflect", label: "Reflect" },
  ];

  return (
    <div className="max-w-7xl mx-auto py-6 space-y-6">
      {/* Header */}
      <h1 className="text-2xl font-bold">Memory Dashboard</h1>

      {/* Tabs */}
      <div className="border-b">
        <nav className="flex gap-6">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`pb-2 text-sm font-medium border-b-2 transition-colors ${
                tab === t.key
                  ? "border-primary text-primary"
                  : "border-transparent text-gray-500 hover:text-gray-700"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab Content */}
      {!hindsightEnabled ? (
        disabledBanner
      ) : (
        <>
          {tab === "pipeline" && (
            <PipelineTab
              banks={banks}
              existingBanks={existingBanks}
              systemBanks={systemBanks}
              userBanks={userBanks}
              projectBanks={projectBanks}
              funnel={funnel}
              recentCaptures={recentCaptures}
              recentLoading={recentLoading}
            />
          )}
          {tab === "banks" && (
            <BanksTab
              banks={banks}
              selectedBank={selectedBank}
              memories={memories}
              memoriesLoading={memoriesLoading}
              memoriesPage={memoriesPage}
              memoriesTotal={memoriesTotal}
              expandedMemory={expandedMemory}
              onSelectBank={handleSelectBank}
              onBack={handleBackToBanks}
              onPageChange={handleMemoriesPage}
              onToggleExpand={(i) =>
                setExpandedMemory(expandedMemory === i ? null : i)
              }
            />
          )}
          {tab === "reflect" && (
            <ReflectTab
              banksWithMemories={banksWithMemories}
              reflectBank={reflectBank}
              reflection={reflection}
              reflectLoading={reflectLoading}
              onBankChange={setReflectBank}
              onReflect={handleReflect}
            />
          )}
        </>
      )}
    </div>
  );
}

// ===================== PIPELINE TAB =====================

function PipelineTab({
  banks,
  existingBanks,
  systemBanks,
  userBanks,
  projectBanks,
  funnel,
  recentCaptures,
  recentLoading,
}: {
  banks: BankInfo[];
  existingBanks: BankInfo[];
  systemBanks: BankInfo[];
  userBanks: BankInfo[];
  projectBanks: BankInfo[];
  funnel: { raw_memories: number; observations: number; mental_models: number };
  recentCaptures: Memory[];
  recentLoading: boolean;
}) {
  return (
    <div className="space-y-6">
      {/* Summary cards */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Total Banks", value: existingBanks.length },
          { label: "System", value: systemBanks.filter((b) => b.exists).length },
          { label: "User", value: userBanks.filter((b) => b.exists).length },
          { label: "Project", value: projectBanks.filter((b) => b.exists).length },
        ].map((card) => (
          <div key={card.label} className="bg-white border rounded-lg p-4">
            <p className="text-xs text-gray-500 uppercase tracking-wide">
              {card.label}
            </p>
            <p className="text-2xl font-bold mt-1">{card.value}</p>
          </div>
        ))}
      </div>

      {/* Abstraction Funnel */}
      <div className="bg-white border rounded-lg p-6">
        <h3 className="text-sm font-semibold text-gray-700 mb-4">
          Abstraction Funnel
        </h3>
        <div className="flex items-center gap-4">
          <div className="flex-1 bg-blue-50 border border-blue-200 rounded-lg p-4 text-center">
            <p className="text-3xl font-bold text-blue-700">
              {funnel.raw_memories}
            </p>
            <p className="text-sm text-blue-600 mt-1">Raw Memories</p>
          </div>
          <ArrowRight className="h-5 w-5 text-gray-400 shrink-0" />
          <div className="flex-1 bg-amber-50 border border-amber-200 rounded-lg p-4 text-center">
            <p className="text-3xl font-bold text-amber-700">
              {funnel.observations}
            </p>
            <p className="text-sm text-amber-600 mt-1">Observations</p>
          </div>
          <ArrowRight className="h-5 w-5 text-gray-400 shrink-0" />
          <div className="flex-1 bg-emerald-50 border border-emerald-200 rounded-lg p-4 text-center">
            <p className="text-3xl font-bold text-emerald-700">
              {funnel.mental_models}
            </p>
            <p className="text-sm text-emerald-600 mt-1">Mental Models</p>
          </div>
        </div>
      </div>

      {/* Recent Captures */}
      <div className="bg-white border rounded-lg p-4">
        <h3 className="text-sm font-semibold text-gray-700 mb-1">
          Recent Captures
        </h3>
        <p className="text-xs text-gray-500 mb-4">
          What the system learned from recent interactions
        </p>
        {recentLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-gray-400" />
          </div>
        ) : recentCaptures.length === 0 ? (
          <p className="text-sm text-gray-400 py-6 text-center">
            No memories captured yet. The system learns passively from chat
            interactions.
          </p>
        ) : (
          <div className="space-y-3">
            {recentCaptures.map((mem, i) => (
              <div
                key={i}
                className="flex items-start gap-3 py-2 border-b last:border-0"
              >
                <span
                  className={`text-xs px-2 py-0.5 rounded shrink-0 mt-0.5 ${bankTypeBadge(mem.bank_type ?? "")}`}
                >
                  {mem.bank_label ?? mem.bank_id}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-gray-700 truncate">
                    {(mem.content ?? "").slice(0, 150)}
                  </p>
                  {mem.tags && mem.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1">
                      {mem.tags.map((tag) => (
                        <span
                          key={tag}
                          className="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <span className="text-xs text-gray-400 whitespace-nowrap shrink-0">
                  {relativeTime(mem.timestamp)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ===================== BANKS TAB =====================

function BanksTab({
  banks,
  selectedBank,
  memories,
  memoriesLoading,
  memoriesPage,
  memoriesTotal,
  expandedMemory,
  onSelectBank,
  onBack,
  onPageChange,
  onToggleExpand,
}: {
  banks: BankInfo[];
  selectedBank: BankInfo | null;
  memories: Memory[];
  memoriesLoading: boolean;
  memoriesPage: number;
  memoriesTotal: number;
  expandedMemory: number | null;
  onSelectBank: (b: BankInfo) => void;
  onBack: () => void;
  onPageChange: (p: number) => void;
  onToggleExpand: (i: number) => void;
}) {
  if (selectedBank) {
    return (
      <BankDetail
        bank={selectedBank}
        memories={memories}
        loading={memoriesLoading}
        page={memoriesPage}
        total={memoriesTotal}
        expandedMemory={expandedMemory}
        onBack={onBack}
        onPageChange={onPageChange}
        onToggleExpand={onToggleExpand}
      />
    );
  }

  return (
    <div className="bg-white border rounded-lg p-4">
      <h3 className="text-sm font-semibold text-gray-700 mb-3">
        Memory Banks
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-4 py-2 font-medium text-gray-600">
                Bank ID
              </th>
              <th className="text-left px-4 py-2 font-medium text-gray-600">
                Type
              </th>
              <th className="text-left px-4 py-2 font-medium text-gray-600">
                Label
              </th>
              <th className="text-right px-4 py-2 font-medium text-gray-600">
                Memories
              </th>
              <th className="text-right px-4 py-2 font-medium text-gray-600">
                Observations
              </th>
              <th className="text-left px-4 py-2 font-medium text-gray-600">
                Status
              </th>
            </tr>
          </thead>
          <tbody>
            {banks.map((bank) => (
              <tr
                key={bank.bank_id}
                className={`border-b last:border-0 transition-colors ${
                  bank.exists
                    ? "hover:bg-muted cursor-pointer"
                    : "opacity-40"
                }`}
                onClick={() => bank.exists && onSelectBank(bank)}
              >
                <td className="px-4 py-2 font-mono text-xs">
                  {bank.bank_id}
                </td>
                <td className="px-4 py-2">
                  <span
                    className={`text-xs px-2 py-0.5 rounded ${bankTypeBadge(bank.type)}`}
                  >
                    {bank.type}
                  </span>
                </td>
                <td className="px-4 py-2">{bank.label}</td>
                <td className="px-4 py-2 text-right text-gray-600">
                  {bank.total_nodes}
                </td>
                <td className="px-4 py-2 text-right text-gray-600">
                  {bank.total_observations}
                </td>
                <td className="px-4 py-2">
                  <span
                    className={`text-xs px-2 py-0.5 rounded ${
                      bank.exists
                        ? "bg-green-100 text-green-800"
                        : "bg-gray-100 text-gray-500"
                    }`}
                  >
                    {bank.exists ? "Active" : "Empty"}
                  </span>
                </td>
              </tr>
            ))}
            {banks.length === 0 && (
              <tr>
                <td
                  colSpan={6}
                  className="px-4 py-6 text-center text-gray-400"
                >
                  No memory banks found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ===================== BANK DETAIL =====================

function BankDetail({
  bank,
  memories,
  loading,
  page,
  total,
  expandedMemory,
  onBack,
  onPageChange,
  onToggleExpand,
}: {
  bank: BankInfo;
  memories: Memory[];
  loading: boolean;
  page: number;
  total: number;
  expandedMemory: number | null;
  onBack: () => void;
  onPageChange: (p: number) => void;
  onToggleExpand: (i: number) => void;
}) {
  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="space-y-4">
      {/* Back button */}
      <button
        onClick={onBack}
        className="flex items-center gap-1 text-sm text-primary hover:text-primary/80"
      >
        <ArrowLeft size={16} />
        Back to Banks
      </button>

      {/* Bank info card */}
      <div className="bg-white border rounded-lg p-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold font-mono">{bank.bank_id}</h2>
            <p className="text-sm text-gray-500">{bank.label}</p>
          </div>
          <div className="flex gap-6 text-center">
            <div>
              <p className="text-xs text-gray-500 uppercase">Type</p>
              <span
                className={`text-xs px-2 py-0.5 rounded ${bankTypeBadge(bank.type)}`}
              >
                {bank.type}
              </span>
            </div>
            <div>
              <p className="text-xs text-gray-500 uppercase">Memories</p>
              <p className="text-lg font-semibold">{bank.total_nodes}</p>
            </div>
            <div>
              <p className="text-xs text-gray-500 uppercase">Observations</p>
              <p className="text-lg font-semibold">{bank.total_observations}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Memories table */}
      <div className="bg-white border rounded-lg p-4">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">
          Memories ({total})
        </h3>
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-gray-400" />
          </div>
        ) : memories.length === 0 ? (
          <p className="text-sm text-gray-400 py-6 text-center">
            No memories in this bank
          </p>
        ) : (
          <div className="space-y-1">
            {memories.map((mem, i) => {
              const isExpanded = expandedMemory === i;
              const factType =
                mem.context ?? mem.metadata?.fact_type ?? "unknown";
              const shape = memoryShape(factType);

              return (
                <div key={i} className="border rounded-lg">
                  <div
                    className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-gray-50 transition-colors"
                    onClick={() => onToggleExpand(i)}
                  >
                    {isExpanded ? (
                      <ChevronDown className="h-4 w-4 text-gray-400 shrink-0" />
                    ) : (
                      <ChevronRight className="h-4 w-4 text-gray-400 shrink-0" />
                    )}
                    <span
                      className={`text-xs px-2 py-0.5 rounded shrink-0 ${shape.color}`}
                    >
                      {shape.label}
                    </span>
                    <span className="flex-1 text-sm text-gray-700 truncate">
                      {(mem.content ?? "").slice(0, 200)}
                    </span>
                    {mem.tags && mem.tags.length > 0 && (
                      <div className="flex gap-1 shrink-0">
                        {mem.tags.slice(0, 3).map((tag) => (
                          <span
                            key={tag}
                            className="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                    {mem.document_id && (
                      <span className="text-xs text-gray-400 shrink-0">
                        {mem.document_id}
                      </span>
                    )}
                    <span className="text-xs text-gray-400 whitespace-nowrap shrink-0">
                      {relativeTime(mem.timestamp)}
                    </span>
                  </div>
                  {isExpanded && (
                    <div className="px-10 pb-3 space-y-2 border-t bg-gray-50">
                      <div className="pt-2">
                        <p className="text-xs font-medium text-gray-500 mb-1">
                          Full Content
                        </p>
                        <p className="text-sm text-gray-700 whitespace-pre-wrap">
                          {mem.content}
                        </p>
                      </div>
                      {mem.entities && mem.entities.length > 0 && (
                        <div>
                          <p className="text-xs font-medium text-gray-500 mb-1">
                            Entities
                          </p>
                          <div className="flex flex-wrap gap-1">
                            {mem.entities.map((e) => (
                              <span
                                key={e}
                                className="text-xs bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded"
                              >
                                {e}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                      {mem.metadata &&
                        Object.keys(mem.metadata).length > 0 && (
                          <div>
                            <p className="text-xs font-medium text-gray-500 mb-1">
                              Metadata
                            </p>
                            <pre className="text-xs text-gray-600 bg-white p-2 rounded border overflow-x-auto">
                              {JSON.stringify(mem.metadata, null, 2)}
                            </pre>
                          </div>
                        )}
                      <div className="text-xs text-gray-400">
                        Timestamp: {mem.timestamp} | Context: {mem.context} |
                        Document: {mem.document_id ?? "none"}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Pagination */}
        {total > PAGE_SIZE && (
          <div className="flex items-center justify-between mt-4">
            <p className="text-xs text-gray-500">
              Page {page + 1} of {totalPages} ({total} total)
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => onPageChange(page - 1)}
                disabled={page === 0 || loading}
                className="px-3 py-1 text-sm border rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Previous
              </button>
              <button
                onClick={() => onPageChange(page + 1)}
                disabled={page >= totalPages - 1 || loading}
                className="px-3 py-1 text-sm border rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ===================== REFLECT TAB =====================

function ReflectTab({
  banksWithMemories,
  reflectBank,
  reflection,
  reflectLoading,
  onBankChange,
  onReflect,
}: {
  banksWithMemories: BankInfo[];
  reflectBank: string;
  reflection: string;
  reflectLoading: boolean;
  onBankChange: (v: string) => void;
  onReflect: () => void;
}) {
  const selectedLabel =
    banksWithMemories.find((b) => b.bank_id === reflectBank)?.label ??
    reflectBank;

  return (
    <div className="space-y-6">
      {/* Bank selector + action */}
      <div className="bg-white border rounded-lg p-4">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">
          Pattern Analysis
        </h3>
        <div className="flex items-end gap-4">
          <div className="flex-1">
            <label className="block text-xs text-gray-500 mb-1">
              Memory Bank
            </label>
            <select
              value={reflectBank}
              onChange={(e) => onBankChange(e.target.value)}
              className="w-full px-3 py-2 border rounded-md text-sm"
            >
              <option value="">Select a bank...</option>
              {banksWithMemories.map((b) => (
                <option key={b.bank_id} value={b.bank_id}>
                  {b.label} ({b.bank_id}) - {b.total_nodes} memories
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={onReflect}
            disabled={!reflectBank || reflectLoading}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {reflectLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Search className="h-4 w-4" />
            )}
            Analyze Patterns
          </button>
        </div>
      </div>

      {/* Reflection output */}
      {reflection && (
        <div className="bg-white border rounded-lg p-6">
          <p className="text-sm text-gray-500 mb-3">
            Based on accumulated interactions in{" "}
            <strong>{selectedLabel}</strong>, here are patterns worth
            considering:
          </p>
          <div className="prose prose-sm max-w-none">
            <p className="whitespace-pre-wrap text-gray-700">{reflection}</p>
          </div>
        </div>
      )}

      {!reflection && !reflectLoading && (
        <p className="text-sm text-gray-400 text-center py-8">
          Select a bank and click "Analyze Patterns" to see synthesized insights
          from accumulated memories.
        </p>
      )}
    </div>
  );
}
