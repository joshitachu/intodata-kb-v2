import { describe, expect, it } from "vitest";
import { parseApiError } from "../lib/errors";

describe("parseApiError", () => {
  it("shows an intentional FastAPI detail message", () => {
    expect(parseApiError(new Error('422: {"detail":"Elasticsearch endpoint is required"}')))
      .toBe("Elasticsearch endpoint is required");
  });

  it("shows a structured connector message", () => {
    expect(parseApiError(new Error('400: {"detail":{"code":"invalid_endpoint","message":"HTTPS is required"}}')))
      .toBe("HTTPS is required");
  });

  it("keeps non-JSON response bodies opaque", () => {
    expect(parseApiError(new Error("500: proxy failure"))).toBe("Request failed (500)");
  });
});
