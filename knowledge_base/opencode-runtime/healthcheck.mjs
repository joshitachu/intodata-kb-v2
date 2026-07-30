const username = process.env.OPENCODE_SERVER_USERNAME || "opencode";
const password = process.env.OPENCODE_SERVER_PASSWORD;
const expectedVersion = process.env.NEXUS_EXPECTED_OPENCODE_VERSION || "1.18.4";

if (!password) {
  process.stderr.write("OpenCode health check is missing server authentication\n");
  process.exit(1);
}

const authorization = Buffer.from(`${username}:${password}`, "utf8").toString("base64");

try {
  const response = await fetch("http://127.0.0.1:4096/global/health", {
    headers: { Authorization: `Basic ${authorization}` },
    signal: AbortSignal.timeout(2000),
  });
  if (!response.ok) {
    throw new Error("health endpoint rejected the request");
  }
  const body = await response.json();
  if (body?.healthy !== true || body?.version !== expectedVersion) {
    throw new Error("health endpoint returned an unexpected runtime identity");
  }
} catch {
  process.stderr.write("OpenCode health check failed\n");
  process.exit(1);
}
