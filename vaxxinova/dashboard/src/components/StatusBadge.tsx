interface Props {
  status: string;
}

const colors: Record<string, string> = {
  ready: "bg-green-100 text-green-800",
  registered: "bg-blue-100 text-blue-800",
  scanning: "bg-yellow-100 text-yellow-800",
  generating: "bg-purple-100 text-purple-800",
  error: "bg-red-100 text-red-800",
};

export default function StatusBadge({ status }: Props) {
  const cls = colors[status] ?? "bg-gray-100 text-gray-800";
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>
      {status}
    </span>
  );
}
