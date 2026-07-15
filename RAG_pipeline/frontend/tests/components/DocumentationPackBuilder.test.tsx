import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DocumentationPackBuilder } from "@/components/harness-packs/DocumentationPackBuilder";

describe("DocumentationPackBuilder", () => {
  it("creates a documentation Pack from document definitions", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(<DocumentationPackBuilder onCreate={onCreate} />);

    fireEvent.change(screen.getByLabelText("Pack name"), {
      target: { value: "Operations Handbook" },
    });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Operations docs" },
    });
    fireEvent.change(screen.getByLabelText("Writing instructions"), {
      target: { value: "Use source evidence." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save reusable Pack" }));

    await waitFor(() => expect(onCreate).toHaveBeenCalledWith({
      name: "Operations Handbook",
      description: "Operations docs",
      documents: [{
        name: "EXECUTIVE_SUMMARY",
        sections: ["Overview", "Architecture", "Operations", "Evidence Register"],
        instructions: "Use source evidence.",
      }],
    }));
  });
});
