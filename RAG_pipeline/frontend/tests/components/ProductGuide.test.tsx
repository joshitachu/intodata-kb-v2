import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProductGuide } from "@/components/guide/ProductGuide";

describe("ProductGuide", () => {
  it("renders as an interactive documentation page", () => {
    render(<ProductGuide />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Product documentation" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Nexus" })).toHaveAttribute("href", "/");
    expect(screen.getAllByRole("link", { name: /Open full size/ }).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByRole("searchbox", { name: "Search product documentation" }), {
      target: { value: "password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Open Create or change password" }));

    expect(window.location.hash).toBe("#first-password");
  });

  it("personalizes the quickstart and exposes real application links", () => {
    render(<ProductGuide />);

    fireEvent.click(screen.getByRole("button", { name: "Admin" }));

    expect(screen.getByRole("button", { name: "Admin" })).toHaveAttribute("aria-pressed", "true");
    expect(window.location.search).toContain("role=admin");
    expect(screen.getByRole("link", { name: "Open Access" })).toHaveAttribute(
      "href",
      "/admin/organization?tab=access",
    );
    expect(
      screen.getByRole("heading", { level: 2, name: "Users, access and projects" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Observe and diagnose" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Navigation reference" })).not.toBeInTheDocument();
    expect(screen.queryByText("Permission catalog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Retrieval/ }));
    expect(
      screen.getByRole("heading", { name: "Filter before ranking, then turn chunks into evidence" }),
    ).toBeInTheDocument();
  });
});
