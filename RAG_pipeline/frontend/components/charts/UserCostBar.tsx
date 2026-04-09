"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const BAR_COLORS = [
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

interface UserCostEntry {
  username: string;
  cost_usd: number;
  count: number;
}

interface Props {
  data: UserCostEntry[];
}

export default function UserCostBar({ data }: Props) {
  if (!data.length) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400 text-sm">
        No user cost data
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={Math.max(200, data.length * 40 + 40)}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 5, right: 30, bottom: 5, left: 0 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" horizontal={false} />
        <XAxis
          type="number"
          tick={{ fontSize: 11 }}
          stroke="#9ca3af"
          tickFormatter={(v) => `$${Number(v).toFixed(2)}`}
        />
        <YAxis
          type="category"
          dataKey="username"
          tick={{ fontSize: 12 }}
          stroke="#9ca3af"
          width={100}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "white",
            border: "1px solid #e5e7eb",
            borderRadius: "8px",
            fontSize: "12px",
          }}
          formatter={(value) => [`$${Number(value).toFixed(4)}`, "Cost"]}
          labelFormatter={(label) => String(label)}
        />
        <Bar dataKey="cost_usd" radius={[0, 4, 4, 0]} barSize={24}>
          {data.map((_, i) => (
            <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
