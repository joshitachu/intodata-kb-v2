"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface DailyEntry {
  date: string;
  count: number;
  cost_usd: number;
}

interface Props {
  data: DailyEntry[];
  metric?: "count" | "cost_usd";
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export default function DailyUsageChart({ data, metric = "count" }: Props) {
  if (!data.length) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400 text-sm">
        No usage data for this period
      </div>
    );
  }

  const chartData = data.map((d) => ({
    ...d,
    label: formatDate(d.date),
  }));

  const color = metric === "cost_usd" ? "#10b981" : "#3b82f6";
  const label = metric === "cost_usd" ? "Cost (USD)" : "Queries";

  return (
    <ResponsiveContainer width="100%" height={280}>
      <AreaChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
        <defs>
          <linearGradient id={`grad-${metric}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.3} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
        <XAxis dataKey="label" tick={{ fontSize: 11 }} stroke="#9ca3af" />
        <YAxis
          tick={{ fontSize: 11 }}
          stroke="#9ca3af"
          tickFormatter={metric === "cost_usd" ? (v) => `$${Number(v).toFixed(2)}` : undefined}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "white",
            border: "1px solid #e5e7eb",
            borderRadius: "8px",
            fontSize: "12px",
          }}
          formatter={(value) =>
            metric === "cost_usd" ? [`$${Number(value).toFixed(4)}`, label] : [Number(value).toLocaleString(), label]
          }
          labelFormatter={(l) => String(l)}
        />
        <Area
          type="monotone"
          dataKey={metric}
          stroke={color}
          strokeWidth={2}
          fill={`url(#grad-${metric})`}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
