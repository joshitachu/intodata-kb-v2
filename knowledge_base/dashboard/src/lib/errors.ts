export function parseApiError(err: unknown): string {
  if (!(err instanceof Error)) return "An unexpected error occurred";
  const match = err.message.match(/^(\d{3}):/);
  if (match) return `Request failed (${match[1]})`;
  return "Request failed";
}
