import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SelectMenu } from "@/components/ui/select-menu";

const OPTIONS = [
  { value: "", label: "All projects" },
  { value: "a", label: "A very long project display name", hint: "42 designs · 12 jobs" },
  { value: "b", label: "Beta" },
];

describe("SelectMenu", () => {
  it("shows the selected label on the trigger, not the hint", () => {
    render(
      <SelectMenu value="a" onChange={vi.fn()} options={OPTIONS} ariaLabel="Project" />,
    );

    const trigger = screen.getByRole("button", { name: "Project" });
    expect(trigger).toHaveTextContent("A very long project display name");
    // The artifact breakdown belongs to the menu; on the trigger it would blow
    // the toolbar out of the composer.
    expect(trigger).not.toHaveTextContent("42 designs");
  });

  it("falls back to the placeholder when nothing is selected", () => {
    render(
      <SelectMenu
        value=""
        onChange={vi.fn()}
        options={OPTIONS.slice(1)}
        placeholder="Select a project"
        ariaLabel="Project"
      />,
    );

    expect(screen.getByRole("button", { name: "Project" })).toHaveTextContent("Select a project");
  });

  it("emits the option value on selection", async () => {
    const onChange = vi.fn();
    render(<SelectMenu value="" onChange={onChange} options={OPTIONS} ariaLabel="Project" />);

    // Radix opens on pointerdown/keyboard, not a synthetic click.
    const trigger = screen.getByRole("button", { name: "Project" });
    fireEvent.keyDown(trigger, { key: "ArrowDown" });

    const beta = await screen.findByRole("menuitem", { name: /Beta/ });
    fireEvent.keyDown(beta, { key: "Enter" });

    expect(onChange).toHaveBeenCalledWith("b");
  });
});
