"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";

// Dynamic imports for chart components (no SSR)
const DailyUsageChart = dynamic(
  () => import("@/components/charts/DailyUsageChart"),
  { ssr: false },
);
const CostPieChart = dynamic(() => import("@/components/charts/CostPieChart"), {
  ssr: false,
});
const UserStackedChart = dynamic(
  () => import("@/components/charts/UserStackedChart"),
  { ssr: false },
);
const UserCostBar = dynamic(() => import("@/components/charts/UserCostBar"), {
  ssr: false,
});

// --- Types ---

interface InterfaceBreakdown {
  interface: string;
  count: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

interface ModelBreakdown {
  model: string;
  provider: string;
  count: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

interface DailyEntry {
  date: string;
  count: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

interface UsageSummary {
  days: number;
  by_interface: InterfaceBreakdown[];
  by_model: ModelBreakdown[];
  daily: DailyEntry[];
}

interface UserUsageEntry {
  user_id: number | null;
  username: string;
  email: string;
  count: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  avg_latency_ms: number;
}

interface ByUserResponse {
  days: number;
  by_user: UserUsageEntry[];
}

interface DailyByUserEntry {
  date: string;
  [username: string]:
    | string
    | number
    | {
        count: number;
        cost_usd: number;
        input_tokens: number;
        output_tokens: number;
      };
}

interface DailyByUserResponse {
  days: number;
  users: string[];
  daily: DailyByUserEntry[];
}

interface UserDetailUsage {
  user_id: number;
  username: string;
  email: string;
  days: number;
  totals: {
    count: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    avg_latency_ms: number;
  };
  daily: DailyEntry[];
  by_interface: InterfaceBreakdown[];
  by_model: ModelBreakdown[];
}

interface RecentThread {
  id: number;
  title: string | null;
  user_id: number | null;
  username: string | null;
  message_count: number;
  avg_session_seconds: number;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  last_activity: string | null;
}

// --- Helpers ---

type Tab = "overview" | "per-user" | "detail";
const PAGE_SIZE = 20;

function formatNumber(n: number): string {
  return n.toLocaleString();
}

function formatCost(n: number): string {
  return `$${n.toFixed(4)}`;
}

function formatDuration(seconds: number): string {
  if (!seconds || seconds <= 0) return "-";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  if (mins < 60) return secs ? `${mins}m ${secs}s` : `${mins}m`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return remMins ? `${hours}h ${remMins}m` : `${hours}h`;
}

// --- Component ---

export default function AdminUsagePage() {
  const [tab, setTab] = useState<Tab>("overview");
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);

  // Overview data
  const [summary, setSummary] = useState<UsageSummary | null>(null);

  // Per-user data
  const [byUser, setByUser] = useState<ByUserResponse | null>(null);
  const [dailyByUser, setDailyByUser] = useState<DailyByUserResponse | null>(
    null,
  );
  const [perUserMetric, setPerUserMetric] = useState<"count" | "cost_usd">(
    "count",
  );

  // User detail
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [userDetail, setUserDetail] = useState<UserDetailUsage | null>(null);
  const [userDetailLoading, setUserDetailLoading] = useState(false);
  const [userRecentThreads, setUserRecentThreads] = useState<RecentThread[]>(
    [],
  );
  const [userRecentPage, setUserRecentPage] = useState(0);

  // Recent threads (overview)
  const [recentThreads, setRecentThreads] = useState<RecentThread[]>([]);
  const [recentPage, setRecentPage] = useState(0);
  const [recentLoading, setRecentLoading] = useState(false);

  // --- Fetch functions ---

  const fetchSummary = useCallback(async (numDays: number) => {
    try {
      const res = await fetch(`/api/admin/usage/summary?days=${numDays}`);
      if (res.ok) setSummary(await res.json());
    } catch {
      /* ignore */
    }
  }, []);

  const fetchByUser = useCallback(async (numDays: number) => {
    try {
      const res = await fetch(`/api/admin/usage/by-user?days=${numDays}`);
      if (res.ok) setByUser(await res.json());
    } catch {
      /* ignore */
    }
  }, []);

  const fetchDailyByUser = useCallback(async (numDays: number) => {
    try {
      const res = await fetch(
        `/api/admin/usage/daily-by-user?days=${numDays}&top=10`,
      );
      if (res.ok) setDailyByUser(await res.json());
    } catch {
      /* ignore */
    }
  }, []);

  const fetchRecent = useCallback(
    async (page: number, userId?: number | null) => {
      setRecentLoading(true);
      try {
        const skip = page * PAGE_SIZE;
        let url = `/api/admin/usage/recent-threads?skip=${skip}&limit=${PAGE_SIZE}`;
        if (userId != null) url += `&user_id=${userId}`;
        const res = await fetch(url);
        if (res.ok) {
          const data: { entries: RecentThread[] } = await res.json();
          if (userId != null) {
            setUserRecentThreads(data.entries);
          } else {
            setRecentThreads(data.entries);
          }
        }
      } catch {
        /* ignore */
      } finally {
        setRecentLoading(false);
      }
    },
    [],
  );

  const fetchUserDetail = useCallback(
    async (userId: number, numDays: number) => {
      setUserDetailLoading(true);
      try {
        const res = await fetch(
          `/api/admin/usage/user/${userId}?days=${numDays}`,
        );
        if (res.ok) setUserDetail(await res.json());
      } catch {
        /* ignore */
      } finally {
        setUserDetailLoading(false);
      }
    },
    [],
  );

  // --- Effects ---

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetchSummary(days),
      fetchByUser(days),
      fetchDailyByUser(days),
      fetchRecent(0),
    ]).then(() => setLoading(false));
  }, [days, fetchSummary, fetchByUser, fetchDailyByUser, fetchRecent]);

  function handleDaysChange(newDays: number) {
    setDays(newDays);
    setRecentPage(0);
  }

  function handleSelectUser(userId: number | null) {
    if (userId == null) return;
    setSelectedUserId(userId);
    setTab("detail");
    setUserRecentPage(0);
    fetchUserDetail(userId, days);
    fetchRecent(0, userId);
  }

  function handleBackToPerUser() {
    setTab("per-user");
    setSelectedUserId(null);
    setUserDetail(null);
    setUserRecentThreads([]);
  }

  // Compute totals
  const totalQueries =
    summary?.by_interface.reduce((s, r) => s + r.count, 0) ?? 0;
  const totalInputTokens =
    summary?.by_interface.reduce((s, r) => s + r.input_tokens, 0) ?? 0;
  const totalOutputTokens =
    summary?.by_interface.reduce((s, r) => s + r.output_tokens, 0) ?? 0;
  const totalCost =
    summary?.by_interface.reduce((s, r) => s + r.cost_usd, 0) ?? 0;
  const activeUsers = byUser?.by_user.length ?? 0;

  if (loading) {
    return <p className="text-gray-400 p-4">Loading usage data...</p>;
  }

  // --- Tab rendering ---

  const tabs: { key: Tab; label: string }[] = [
    { key: "overview", label: "Overview" },
    { key: "per-user", label: "Per User" },
  ];
  if (tab === "detail") {
    tabs.push({ key: "detail", label: "User Detail" });
  }

  return (
    <div className="max-w-7xl mx-auto py-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Usage Dashboard</h1>
        <select
          value={days}
          onChange={(e) => handleDaysChange(parseInt(e.target.value, 10))}
          className="px-3 py-2 border rounded-md text-sm"
        >
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-5 gap-4">
        {[
          { label: "Total Queries", value: formatNumber(totalQueries) },
          { label: "Input Tokens", value: formatNumber(totalInputTokens) },
          { label: "Output Tokens", value: formatNumber(totalOutputTokens) },
          { label: "Total Cost", value: formatCost(totalCost) },
          { label: "Active Users", value: formatNumber(activeUsers) },
        ].map((card) => (
          <div key={card.label} className="bg-white border rounded-lg p-4">
            <p className="text-xs text-gray-500 uppercase tracking-wide">
              {card.label}
            </p>
            <p className="text-2xl font-bold mt-1">{card.value}</p>
          </div>
        ))}
      </div>

      {/* Tabs */}
      <div className="border-b">
        <nav className="flex gap-6">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => {
                if (t.key === "detail" && !selectedUserId) return;
                setTab(t.key);
              }}
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
      {tab === "overview" && (
        <OverviewTab
          summary={summary}
          recentThreads={recentThreads}
          recentPage={recentPage}
          recentLoading={recentLoading}
          onPageChange={(p) => {
            setRecentPage(p);
            fetchRecent(p);
          }}
        />
      )}
      {tab === "per-user" && (
        <PerUserTab
          byUser={byUser}
          dailyByUser={dailyByUser}
          metric={perUserMetric}
          onMetricChange={setPerUserMetric}
          onSelectUser={handleSelectUser}
        />
      )}
      {tab === "detail" && (
        <UserDetailTab
          detail={userDetail}
          loading={userDetailLoading}
          recentThreads={userRecentThreads}
          recentPage={userRecentPage}
          recentLoading={recentLoading}
          onBack={handleBackToPerUser}
          onPageChange={(p) => {
            setUserRecentPage(p);
            fetchRecent(p, selectedUserId);
          }}
        />
      )}
    </div>
  );
}

// ===================== OVERVIEW TAB =====================

function OverviewTab({
  summary,
  recentThreads,
  recentPage,
  recentLoading,
  onPageChange,
}: {
  summary: UsageSummary | null;
  recentThreads: RecentThread[];
  recentPage: number;
  recentLoading: boolean;
  onPageChange: (p: number) => void;
}) {
  const pieData = (summary?.by_interface ?? []).map((r) => ({
    name: r.interface,
    value: r.cost_usd,
  }));

  return (
    <div className="space-y-6">
      {/* Charts row */}
      <div className="grid grid-cols-2 gap-6">
        <div className="bg-white border rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">
            Daily Queries
          </h3>
          <DailyUsageChart data={summary?.daily ?? []} metric="count" />
        </div>
        <div className="bg-white border rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">
            Daily Cost
          </h3>
          <DailyUsageChart data={summary?.daily ?? []} metric="cost_usd" />
        </div>
      </div>

      {/* Pie + tables */}
      <div className="grid grid-cols-3 gap-6">
        <div className="bg-white border rounded-lg p-4">
          <CostPieChart data={pieData} title="Cost by Interface" />
        </div>
        <div className="bg-white border rounded-lg p-4 col-span-2">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">
            By Interface
          </h3>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-2 font-medium text-gray-600">
                  Interface
                </th>
                <th className="text-right px-4 py-2 font-medium text-gray-600">
                  Queries
                </th>
                <th className="text-right px-4 py-2 font-medium text-gray-600">
                  Input Tokens
                </th>
                <th className="text-right px-4 py-2 font-medium text-gray-600">
                  Output Tokens
                </th>
                <th className="text-right px-4 py-2 font-medium text-gray-600">
                  Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {summary?.by_interface.map((row) => (
                <tr key={row.interface} className="border-b last:border-0">
                  <td className="px-4 py-2 font-medium">{row.interface}</td>
                  <td className="px-4 py-2 text-right text-gray-600">
                    {formatNumber(row.count)}
                  </td>
                  <td className="px-4 py-2 text-right text-gray-600">
                    {formatNumber(row.input_tokens)}
                  </td>
                  <td className="px-4 py-2 text-right text-gray-600">
                    {formatNumber(row.output_tokens)}
                  </td>
                  <td className="px-4 py-2 text-right text-gray-600">
                    {formatCost(row.cost_usd)}
                  </td>
                </tr>
              ))}
              {(!summary || summary.by_interface.length === 0) && (
                <tr>
                  <td
                    colSpan={5}
                    className="px-4 py-6 text-center text-gray-400"
                  >
                    No usage data
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* By Model */}
      <div className="bg-white border rounded-lg p-4">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">By Model</h3>
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-4 py-2 font-medium text-gray-600">
                Model
              </th>
              <th className="text-left px-4 py-2 font-medium text-gray-600">
                Provider
              </th>
              <th className="text-right px-4 py-2 font-medium text-gray-600">
                Queries
              </th>
              <th className="text-right px-4 py-2 font-medium text-gray-600">
                Input Tokens
              </th>
              <th className="text-right px-4 py-2 font-medium text-gray-600">
                Output Tokens
              </th>
              <th className="text-right px-4 py-2 font-medium text-gray-600">
                Cost
              </th>
            </tr>
          </thead>
          <tbody>
            {summary?.by_model.map((row) => (
              <tr
                key={`${row.model}-${row.provider}`}
                className="border-b last:border-0"
              >
                <td className="px-4 py-2 font-medium">
                  {row.model || "(unknown)"}
                </td>
                <td className="px-4 py-2 text-gray-600">
                  {row.provider || "-"}
                </td>
                <td className="px-4 py-2 text-right text-gray-600">
                  {formatNumber(row.count)}
                </td>
                <td className="px-4 py-2 text-right text-gray-600">
                  {formatNumber(row.input_tokens)}
                </td>
                <td className="px-4 py-2 text-right text-gray-600">
                  {formatNumber(row.output_tokens)}
                </td>
                <td className="px-4 py-2 text-right text-gray-600">
                  {formatCost(row.cost_usd)}
                </td>
              </tr>
            ))}
            {(!summary || summary.by_model.length === 0) && (
              <tr>
                <td
                  colSpan={6}
                  className="px-4 py-6 text-center text-gray-400"
                >
                  No usage data
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Recent Threads */}
      <RecentThreadsTable
        entries={recentThreads}
        page={recentPage}
        loading={recentLoading}
        onPageChange={onPageChange}
      />
    </div>
  );
}

// ===================== PER USER TAB =====================

function PerUserTab({
  byUser,
  dailyByUser,
  metric,
  onMetricChange,
  onSelectUser,
}: {
  byUser: ByUserResponse | null;
  dailyByUser: DailyByUserResponse | null;
  metric: "count" | "cost_usd";
  onMetricChange: (m: "count" | "cost_usd") => void;
  onSelectUser: (userId: number | null) => void;
}) {
  const users = byUser?.by_user ?? [];
  const barData = users.map((u) => ({
    username: u.username,
    cost_usd: u.cost_usd,
    count: u.count,
  }));

  return (
    <div className="space-y-6">
      {/* Charts */}
      <div className="grid grid-cols-2 gap-6">
        <div className="bg-white border rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">
            Cost per User
          </h3>
          <UserCostBar data={barData} />
        </div>
        <div className="bg-white border rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-gray-700">
              Daily Usage by User
            </h3>
            <select
              value={metric}
              onChange={(e) =>
                onMetricChange(e.target.value as "count" | "cost_usd")
              }
              className="px-2 py-1 text-xs border rounded"
            >
              <option value="count">Queries</option>
              <option value="cost_usd">Cost</option>
            </select>
          </div>
          <UserStackedChart
            data={dailyByUser?.daily ?? []}
            users={dailyByUser?.users ?? []}
            metric={metric}
          />
        </div>
      </div>

      {/* User Leaderboard */}
      <div className="bg-white border rounded-lg p-4">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">
          User Leaderboard
        </h3>
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-4 py-2 font-medium text-gray-600">
                #
              </th>
              <th className="text-left px-4 py-2 font-medium text-gray-600">
                User
              </th>
              <th className="text-left px-4 py-2 font-medium text-gray-600">
                Email
              </th>
              <th className="text-right px-4 py-2 font-medium text-gray-600">
                Queries
              </th>
              <th className="text-right px-4 py-2 font-medium text-gray-600">
                Input Tokens
              </th>
              <th className="text-right px-4 py-2 font-medium text-gray-600">
                Output Tokens
              </th>
              <th className="text-right px-4 py-2 font-medium text-gray-600">
                Cost
              </th>
              <th className="text-right px-4 py-2 font-medium text-gray-600">
                Avg Latency
              </th>
            </tr>
          </thead>
          <tbody>
            {users.map((user, i) => (
              <tr
                key={user.user_id ?? "anon"}
                className="border-b last:border-0 hover:bg-muted cursor-pointer transition-colors"
                onClick={() => onSelectUser(user.user_id)}
              >
                <td className="px-4 py-2 text-gray-400">{i + 1}</td>
                <td className="px-4 py-2 font-medium">{user.username}</td>
                <td className="px-4 py-2 text-gray-500 text-xs">
                  {user.email || "-"}
                </td>
                <td className="px-4 py-2 text-right text-gray-600">
                  {formatNumber(user.count)}
                </td>
                <td className="px-4 py-2 text-right text-gray-600">
                  {formatNumber(user.input_tokens)}
                </td>
                <td className="px-4 py-2 text-right text-gray-600">
                  {formatNumber(user.output_tokens)}
                </td>
                <td className="px-4 py-2 text-right text-gray-600">
                  {formatCost(user.cost_usd)}
                </td>
                <td className="px-4 py-2 text-right text-gray-600">
                  {formatNumber(user.avg_latency_ms)}ms
                </td>
              </tr>
            ))}
            {users.length === 0 && (
              <tr>
                <td
                  colSpan={8}
                  className="px-4 py-6 text-center text-gray-400"
                >
                  No user usage data
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ===================== USER DETAIL TAB =====================

function UserDetailTab({
  detail,
  loading,
  recentThreads,
  recentPage,
  recentLoading,
  onBack,
  onPageChange,
}: {
  detail: UserDetailUsage | null;
  loading: boolean;
  recentThreads: RecentThread[];
  recentPage: number;
  recentLoading: boolean;
  onBack: () => void;
  onPageChange: (p: number) => void;
}) {
  if (loading) {
    return <p className="text-gray-400 p-4">Loading user detail...</p>;
  }
  if (!detail) {
    return <p className="text-gray-400 p-4">No user data available</p>;
  }

  const pieData = detail.by_interface.map((r) => ({
    name: r.interface,
    value: r.cost_usd,
  }));

  return (
    <div className="space-y-6">
      {/* Back + User header */}
      <div className="flex items-center gap-4">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-sm text-primary hover:text-primary/80"
        >
          <ArrowLeft size={16} />
          Back to Per User
        </button>
      </div>

      <div className="bg-white border rounded-lg p-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold">{detail.username}</h2>
            <p className="text-sm text-gray-500">{detail.email}</p>
          </div>
          <div className="flex gap-6 text-center">
            {[
              { label: "Queries", value: formatNumber(detail.totals.count) },
              {
                label: "Tokens (in/out)",
                value: `${formatNumber(detail.totals.input_tokens)} / ${formatNumber(detail.totals.output_tokens)}`,
              },
              { label: "Cost", value: formatCost(detail.totals.cost_usd) },
              {
                label: "Avg Latency",
                value: `${formatNumber(detail.totals.avg_latency_ms)}ms`,
              },
            ].map((stat) => (
              <div key={stat.label}>
                <p className="text-xs text-gray-500 uppercase">{stat.label}</p>
                <p className="text-lg font-semibold">{stat.value}</p>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-3 gap-6">
        <div className="bg-white border rounded-lg p-4 col-span-2">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">
            Daily Activity
          </h3>
          <DailyUsageChart data={detail.daily} metric="count" />
        </div>
        <div className="bg-white border rounded-lg p-4">
          <CostPieChart data={pieData} title="Cost by Interface" />
        </div>
      </div>

      {/* By Model */}
      {detail.by_model.length > 0 && (
        <div className="bg-white border rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">
            By Model
          </h3>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-2 font-medium text-gray-600">
                  Model
                </th>
                <th className="text-left px-4 py-2 font-medium text-gray-600">
                  Provider
                </th>
                <th className="text-right px-4 py-2 font-medium text-gray-600">
                  Queries
                </th>
                <th className="text-right px-4 py-2 font-medium text-gray-600">
                  Tokens (in/out)
                </th>
                <th className="text-right px-4 py-2 font-medium text-gray-600">
                  Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {detail.by_model.map((row) => (
                <tr
                  key={`${row.model}-${row.provider}`}
                  className="border-b last:border-0"
                >
                  <td className="px-4 py-2 font-medium">
                    {row.model || "(unknown)"}
                  </td>
                  <td className="px-4 py-2 text-gray-600">
                    {row.provider || "-"}
                  </td>
                  <td className="px-4 py-2 text-right text-gray-600">
                    {formatNumber(row.count)}
                  </td>
                  <td className="px-4 py-2 text-right text-gray-600">
                    {formatNumber(row.input_tokens)} /{" "}
                    {formatNumber(row.output_tokens)}
                  </td>
                  <td className="px-4 py-2 text-right text-gray-600">
                    {formatCost(row.cost_usd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Recent Threads for this user */}
      <RecentThreadsTable
        entries={recentThreads}
        page={recentPage}
        loading={recentLoading}
        onPageChange={onPageChange}
        title="Recent Threads (this user)"
      />
    </div>
  );
}

// ===================== SHARED COMPONENTS =====================

function RecentThreadsTable({
  entries,
  page,
  loading,
  onPageChange,
  title = "Recent Threads",
}: {
  entries: RecentThread[];
  page: number;
  loading: boolean;
  onPageChange: (p: number) => void;
  title?: string;
}) {
  return (
    <div className="bg-white border rounded-lg p-4">
      <h3 className="text-sm font-semibold text-gray-700 mb-3">{title}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-3 py-2 font-medium text-gray-600">
                ID
              </th>
              <th className="text-left px-3 py-2 font-medium text-gray-600">
                Thread
              </th>
              <th className="text-left px-3 py-2 font-medium text-gray-600">
                User
              </th>
              <th className="text-right px-3 py-2 font-medium text-gray-600">
                Messages
              </th>
              <th className="text-right px-3 py-2 font-medium text-gray-600">
                Avg Session
              </th>
              <th className="text-right px-3 py-2 font-medium text-gray-600">
                Tokens
              </th>
              <th className="text-right px-3 py-2 font-medium text-gray-600">
                Cost
              </th>
              <th className="text-left px-3 py-2 font-medium text-gray-600">
                Last Activity
              </th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td
                  colSpan={8}
                  className="px-3 py-6 text-center text-gray-400"
                >
                  Loading...
                </td>
              </tr>
            ) : (
              <>
                {entries.map((e) => (
                  <tr key={e.id} className="border-b last:border-0">
                    <td className="px-3 py-2 text-gray-500">{e.id}</td>
                    <td
                      className="px-3 py-2 text-gray-600 max-w-xs truncate"
                      title={e.title || undefined}
                    >
                      {e.title || "(untitled)"}
                    </td>
                    <td className="px-3 py-2 text-gray-600">
                      {e.username || (e.user_id != null ? `#${e.user_id}` : "-")}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-600">
                      {formatNumber(e.message_count)}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-600 text-xs">
                      {formatDuration(e.avg_session_seconds)}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-600 text-xs">
                      {e.input_tokens != null
                        ? formatNumber(e.input_tokens)
                        : "-"}
                      {" / "}
                      {e.output_tokens != null
                        ? formatNumber(e.output_tokens)
                        : "-"}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-600 text-xs">
                      {e.cost_usd != null ? formatCost(e.cost_usd) : "-"}
                    </td>
                    <td className="px-3 py-2 text-gray-500 text-xs whitespace-nowrap">
                      {e.last_activity
                        ? new Date(e.last_activity).toLocaleString()
                        : "-"}
                    </td>
                  </tr>
                ))}
                {entries.length === 0 && (
                  <tr>
                    <td
                      colSpan={8}
                      className="px-3 py-6 text-center text-gray-400"
                    >
                      No recent threads
                    </td>
                  </tr>
                )}
              </>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between mt-3">
        <p className="text-xs text-gray-500">
          Page {page + 1}
          {entries.length > 0 && ` (showing ${entries.length} entries)`}
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
            disabled={entries.length < PAGE_SIZE || loading}
            className="px-3 py-1 text-sm border rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
