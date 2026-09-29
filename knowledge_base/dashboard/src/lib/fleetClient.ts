import type { ClientEnv } from "../api/fleet";

/**
 * Client-side mirrors of Mission Control's registry rules, so an operator sees
 * the problem before a round trip. The server stays authoritative: its 422
 * message is shown as well (see `describeMcDetail`).
 */

/** Same pattern as `deploy/deploy-client.sh --client-name` and Mission Control. */
export const CLIENT_SLUG_RE = /^[a-z0-9][a-z0-9-]{0,62}$/;

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

export function slugProblem(slug: string): string | null {
  if (!slug) return "Enter a slug.";
  if (!CLIENT_SLUG_RE.test(slug)) {
    return "Use 1-63 lowercase letters, digits or hyphens, starting with a letter or digit.";
  }
  return null;
}

/** Mirrors `mission_control.instance_client._checked_url`. */
export function instanceUrlProblem(raw: string): string | null {
  if (!raw) return "Enter the instance URL.";
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return "Not a valid URL.";
  }
  if ((url.protocol !== "https:" && url.protocol !== "http:") || !url.hostname) {
    return "Must be an absolute https:// URL with a host.";
  }
  if (url.search || url.hash) return "Must not carry a query or fragment.";
  if (url.username || url.password) return "Must not contain credentials (user:password@).";
  if (url.protocol !== "https:" && !LOOPBACK_HOSTS.has(url.hostname.toLowerCase())) {
    return "Must use https:// (plain http is allowed only for localhost).";
  }
  return null;
}

export const MISSING_TELEMETRY_URL_TEXT = "Set MC_PUBLIC_URL on this Mission Control to fill this in";

/** The env-file block for the client's server. A missing URL becomes a comment, never an empty value. */
export function clientEnvLines(env: ClientEnv): string {
  const urlLine =
    env.MCP_OPS_TELEMETRY_URL != null
      ? `MCP_OPS_TELEMETRY_URL=${env.MCP_OPS_TELEMETRY_URL}`
      : `# MCP_OPS_TELEMETRY_URL=  (${MISSING_TELEMETRY_URL_TEXT})`;
  return [
    urlLine,
    `MCP_OPS_TELEMETRY_API_KEY=${env.MCP_OPS_TELEMETRY_API_KEY}`,
    `MCP_OPS_CLIENT_SLUG=${env.MCP_OPS_CLIENT_SLUG}`,
  ].join("\n");
}

/** Where `deploy-client.sh --ops-telemetry-key-file` expects the heartbeat key on the client's server. */
export const OPS_TELEMETRY_KEY_FILE = "/etc/intodata-kb/ops-telemetry.key";

/** POSIX single-quoting, so a value is one literal shell word. */
function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

/**
 * The deploy commands for the client's server. The key is written to a
 * root-only file and passed by path, never as an argument: an argument lands
 * in `ps`, sudo logs and the deploy log. `printf` is a shell builtin and
 * `sudo tee` only sees the path, so the key is on no process command line and
 * in no sudo log. The first line is still in the operator's interactive shell
 * history unless it is run with a leading space (bash HISTCONTROL=ignorespace).
 */
export function deployClientCommands(env: ClientEnv): string {
  const url = env.MCP_OPS_TELEMETRY_URL ?? "<MC_PUBLIC_URL not set>";
  return [
    `printf '%s' ${shellQuote(env.MCP_OPS_TELEMETRY_API_KEY)} | sudo tee ${OPS_TELEMETRY_KEY_FILE} >/dev/null && sudo chmod 600 ${OPS_TELEMETRY_KEY_FILE}`,
    `deploy-client.sh --client-name ${env.MCP_OPS_CLIENT_SLUG} --ops-telemetry-url ${url} --ops-telemetry-key-file ${OPS_TELEMETRY_KEY_FILE}`,
  ].join("\n");
}

/**
 * The href for an instance URL, or null when it must be shown as plain text.
 * Only https (or http on a loopback host) becomes a link: heartbeat URLs are
 * client-controlled, so `javascript:`/`data:` and other schemes never reach
 * an `href`.
 */
export function safeInstanceHref(raw: string | null | undefined): string | null {
  if (!raw) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (!url.hostname || url.username || url.password) return null;
  if (url.protocol === "https:") return url.href;
  if (url.protocol === "http:" && LOOPBACK_HOSTS.has(url.hostname.toLowerCase())) return url.href;
  return null;
}
