"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const USER_COLORS = [
  "#3b82f6",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#06b6d4",
  "#f97316",
  "#ec4899",
  "#14b8a6",
  "#6366f1",
];

interface DailyByUserEntry {
  date: string;
  [username: string]: string | number | { count: number; cost_usd: number; input_tokens: number; output_tokens: number };
}

interface Props {
  data: DailyByUserEntry[];
  users: string[];
  metric?: "count" | "cost_usd";
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export default function UserStackedChart({ data, users, metric = "count" }: Props) {
  if (!data.length || !users.length) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400 text-sm">
        No per-user usage data
      </div>
    );
  }

  // Flatten the nested structure for recharts
  const chartData = data.map((entry) => {
    const flat: Record<string, string | number> = { date: formatDate(entry.date) };
    for (const user of users) {
      const userData = entry[user];
      if (userData && typeof userData === "object") {
        flat[user] = (userData as { count: number; cost_usd: number })[metric];
      } else {
        flat[user] = 0;
      }
    }
    return flat;
  });

  const isCost = metric === "cost_usd";

  return (
    <ResponsiveContainer width="100%" height={320}>
      <AreaChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
        <defs>
          {users.map((user, i) => (
            <linearGradient key={user} id={`grad-user-${i}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={USER_COLORS[i % USER_COLORS.length]} stopOpacity={0.4} />
              <stop offset="95%" stopColor={USER_COLORS[i % USER_COLORS.length]} stopOpacity={0.05} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
        <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="#9ca3af" />
        <YAxis
          tick={{ fontSize: 11 }}
          stroke="#9ca3af"
          tickFormatter={isCost ? (v) => `$${Number(v).toFixed(2)}` : undefined}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "white",
            border: "1px solid #e5e7eb",
            borderRadius: "8px",
            fontSize: "12px",
          }}
          formatter={(value) =>
            isCost ? `$${Number(value).toFixed(4)}` : Number(value).toLocaleString()
          }
        />
        <Legend wrapperStyle={{ fontSize: "12px" }} />
        {users.map((user, i) => (
          <Area
            key={user}
            type="monotone"
            dataKey={user}
            stackId="1"
            stroke={USER_COLORS[i % USER_COLORS.length]}
            strokeWidth={1.5}
            fill={`url(#grad-user-${i})`}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}
