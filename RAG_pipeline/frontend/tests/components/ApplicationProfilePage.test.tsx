import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ApplicationProfilePage from "@/app/admin/application-profile/page";

vi.mock("@/components/harness-packs/PackManager", () => ({
  PackManager: () => <div>Governed Pack workspace</div>,
}));

describe("ApplicationProfilePage", () => {
  it("uses the governed Pack workspace instead of a custom policy editor", () => {
    render(<ApplicationProfilePage />);
    expect(screen.getByText("Governed Pack workspace")).toBeInTheDocument();
  });
});
