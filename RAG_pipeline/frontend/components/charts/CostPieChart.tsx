"use client";

import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

const COLORS = [
  "#fe9543",
  "#6370b0",
  "#a0e06f",
  "#001e4d",
  "#ef4444",
  "#f59e0b",
  "#ec4899",
  "#14b8a6",
];

interface PieEntry {
  name: string;
  value: number;
}

interface Props {
  data: PieEntry[];
  title?: string;
}

export default function CostPieChart({ data, title }: Props) {
  const filtered = data.filter((d) => d.value > 0);

  if (!filtered.length) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400 text-sm">
        No cost data
      </div>
    );
  }

  return (
    <div>
      {title && <p className="text-sm font-medium text-gray-600 mb-2 text-center">{title}</p>}
      <ResponsiveContainer width="100%" height={280}>
        <PieChart>
          <Pie
            data={filtered}
            cx="50%"
            cy="50%"
            innerRadius={55}
            outerRadius={90}
            paddingAngle={2}
            dataKey="value"
            nameKey="name"
          >
            {filtered.map((_, i) => (
              <Cell key={i} fill={COLORS[i % COLORS.length]} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              backgroundColor: "white",
              border: "1px solid #e5e7eb",
              borderRadius: "8px",
              fontSize: "12px",
            }}
            formatter={(value) => [`$${Number(value).toFixed(4)}`, "Cost"]}
          />
          <Legend
            wrapperStyle={{ fontSize: "12px" }}
            formatter={(value: string) => <span className="text-gray-600">{value}</span>}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
