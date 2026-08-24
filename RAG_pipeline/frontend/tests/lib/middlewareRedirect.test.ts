import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "@/middleware";

describe("auth middleware login redirect", () => {
  it("preserves the query string of the blocked request", () => {
    // The GitHub App post-install return trip carries its whole payload in the
    // query string; dropping it sends the user back to a useless URL.
    const request = new NextRequest(
      "https://client.example.com/api/source/github/callback?installation_id=155193510&setup_action=install",
      { headers: { "x-forwarded-host": "client.example.com", "x-forwarded-proto": "https" } },
    );

    const response = middleware(request);
    const location = new URL(response.headers.get("location")!);

    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("redirect")).toBe(
      "/api/source/github/callback?installation_id=155193510&setup_action=install",
    );
  });

  it("builds the login URL from the forwarded host, not the container host", () => {
    const request = new NextRequest("http://0.0.0.0:3000/settings/sources", {
      headers: { "x-forwarded-host": "client.example.com", "x-forwarded-proto": "https" },
    });

    const location = new URL(middleware(request).headers.get("location")!);

    expect(location.origin).toBe("https://client.example.com");
  });
});
