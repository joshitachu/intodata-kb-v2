const UNITS: [string, number][] = [
  ["year", 31536000],
  ["month", 2592000],
  ["week", 604800],
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
  ["second", 1],
];

export function timeAgo(dateStr: string | null | undefined): string {
  if (!dateStr) return "Never";
  const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (seconds < 5) return "Just now";
  for (const [unit, value] of UNITS) {
    const count = Math.floor(seconds / value);
    if (count >= 1) {
      return `${count} ${unit}${count !== 1 ? "s" : ""} ago`;
    }
  }
  return "Just now";
}

export function formatAbsoluteDate(dateStr: string | null | undefined): string {
  if (!dateStr) return "";
  return new Date(dateStr).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
