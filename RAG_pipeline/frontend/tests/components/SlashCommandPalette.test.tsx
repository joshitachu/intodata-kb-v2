import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { SlashCommandPalette, useSlashCommandPalette } from "@/components/SlashCommandPalette";
import { filterSlashCommands } from "@/hooks/useSlashCommands";

const commands = [
  { name: "docs", description: "Generate project documentation.", argument_hint: "project name" },
  { name: "investigate", description: "Investigate a failure.", argument_hint: "incident" },
];

describe("SlashCommandPalette", () => {
  it("filters discovered commands and selects with keyboard navigation", () => {
    const onSelect = vi.fn();
    render(<SlashCommandPalette commands={commands} query="/" onSelect={onSelect} />);

    const palette = screen.getByRole("listbox");
    fireEvent.keyDown(palette, { key: "ArrowDown" });
    fireEvent.keyDown(palette, { key: "Enter" });

    expect(onSelect).toHaveBeenCalledWith(commands[1]);
  });

  it("can share keyboard state with the composing input", () => {
    const onSelect = vi.fn();
    function Composer() {
      const [query, setQuery] = useState("/");
      const palette = useSlashCommandPalette({ commands, query, onSelect });
      return (
        <>
          <input aria-label="Message" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={palette.onKeyDown} />
          <SlashCommandPalette commands={commands} query={query} onSelect={onSelect} state={palette} />
        </>
      );
    }

    render(<Composer />);
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Message" }), { key: "ArrowDown" });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Message" }), { key: "Enter" });

    expect(onSelect).toHaveBeenCalledWith(commands[1]);
  });

  it("exposes descriptions and argument hints for a filtered command", () => {
    render(<SlashCommandPalette commands={commands} query="/doc" onSelect={vi.fn()} />);

    expect(screen.getByRole("option", { name: /docs/i })).toBeInTheDocument();
    expect(screen.getByText("project name")).toBeInTheDocument();
    expect(screen.queryByText("Investigate a failure.")).not.toBeInTheDocument();
    expect(filterSlashCommands(commands, "/invest")).toEqual([commands[1]]);
  });
});
