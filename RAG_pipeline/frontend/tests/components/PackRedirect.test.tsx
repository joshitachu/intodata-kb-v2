import { describe, expect, it, vi } from "vitest";
import PackGovernancePage from "@/app/pack/page";

const mocks = vi.hoisted(() => ({ redirect: vi.fn() }));

vi.mock("next/navigation", () => ({ redirect: mocks.redirect }));

describe("legacy Pack route", () => {
  it("redirects legacy Pack links to the chat-side workbench", async () => {
    await PackGovernancePage({ searchParams: Promise.resolve({ project: "project-a", pack: "7" }) });
    expect(mocks.redirect).toHaveBeenCalledWith("/?workspace=pack&project=project-a&pack=7");
  });
});
