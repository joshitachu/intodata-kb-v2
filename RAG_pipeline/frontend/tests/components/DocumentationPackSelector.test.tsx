import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DocumentationPackSelector } from "@/components/harness-packs/DocumentationPackSelector";

describe("DocumentationPackSelector", () => {
  it("shows swap-pack metadata and selects its MCP plugin id", () => {
    const onChange = vi.fn();
    render(
      <DocumentationPackSelector
        packs={[{ id: 7, name: "Talend migration", version: "2.1.0", description: "Talend to Azure instructions." }]}
        selectedPluginPackId={null}
        onChange={onChange}
      />,
    );

    expect(screen.getByText("Talend migration")).toBeInTheDocument();
    expect(screen.getByText("Version 2.1.0")).toBeInTheDocument();
    expect(screen.queryByText("/private/plugin-packs")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Select" }));
    expect(onChange).toHaveBeenCalledWith(7);
  });
});
