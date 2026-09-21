import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MarkdownContent } from "@/components/MarkdownContent";

/**
 * Mermaid renders through a working element it appends to `document.body` when no
 * container is passed, and on a parse failure it draws its "Syntax error in text" bomb
 * into that element and throws WITHOUT removing it. An answer streams in token by token,
 * so every mermaid block is invalid for most of its life -- that is how a column of
 * orphaned bombs ended up stacked below the app. These tests hold the component to the
 * two rules that prevent it: never call `render()` on a definition that does not parse,
 * and always clean up the working element afterwards.
 */

const mermaidMock = vi.hoisted(() => {
  // Stands in for mermaid's grammar: a `flowchart` header plus one complete edge. A
  // definition still streaming in ("flowchart TD\n A -->") fails it, as it does in mermaid.
  const valid = (text: string) => text.trim().startsWith("flowchart") && /-->\s*\S/.test(text);
  const parse = vi.fn(async (text: string) => valid(text));
  const render = vi.fn(async (id: string, text: string) => {
    // Reproduce mermaid's own side effect: a body-level working element per call.
    const working = document.createElement("div");
    working.id = `d${id}`;
    document.body.appendChild(working);
    if (!valid(text)) {
      working.innerHTML = "<svg><text>Syntax error in text</text></svg>";
      throw new Error("Parse error on line 1");
    }
    // mermaid's returned SVG carries the diagram id -- cleanup must not match on it.
    return { svg: `<svg id="${id}" data-testid="diagram"></svg>`, bindFunctions: undefined };
  });
  return { initialize: vi.fn(), parse, render };
});

vi.mock("mermaid", () => ({ default: mermaidMock }));

function bombCount() {
  return document.body.querySelectorAll("[id^='dnexus-mermaid-']").length;
}

afterEach(() => {
  mermaidMock.parse.mockClear();
  mermaidMock.render.mockClear();
});

describe("MarkdownContent mermaid blocks", () => {
  it("renders a valid diagram and leaves no working element behind", async () => {
    render(
      <MarkdownContent content={"```mermaid\nflowchart TD\n  A --> B\n```"} />,
    );

    await waitFor(() => expect(mermaidMock.render).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(document.querySelector("[data-testid='diagram']")).not.toBeNull());
    expect(bombCount()).toBe(0);
  });

  it("shows the source instead of rendering a definition that does not parse", async () => {
    render(
      <MarkdownContent content={"```mermaid\nflowchart TD\n  A --> \n```"} />,
    );

    await waitFor(() => expect(mermaidMock.parse).toHaveBeenCalled());
    // The half-written definition never reaches render(), so mermaid never draws a bomb.
    expect(mermaidMock.render).not.toHaveBeenCalled();
    expect(bombCount()).toBe(0);
    expect(screen.getByText(/A -->/)).toBeTruthy();
  });

  it("cleans up after a render that throws", async () => {
    mermaidMock.parse.mockResolvedValueOnce(true as never);

    render(
      <MarkdownContent content={"```mermaid\ngraph TD\n  A --> B\n```"} />,
    );

    await waitFor(() => expect(mermaidMock.render).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByText(/Diagram rendering failed/)).toBeTruthy(),
    );
    expect(bombCount()).toBe(0);
  });

  it("does not leak a working element per streamed chunk", async () => {
    const chunks = [
      "```mermaid\nflow",
      "```mermaid\nflowchart TD\n  A -->",
      "```mermaid\nflowchart TD\n  A --> B\n```",
    ];
    const { rerender } = render(<MarkdownContent content={chunks[0]} />);
    rerender(<MarkdownContent content={chunks[1]} />);
    rerender(<MarkdownContent content={chunks[2]} />);

    await waitFor(() => expect(document.querySelector("[data-testid='diagram']")).not.toBeNull());
    expect(bombCount()).toBe(0);
  });
});
