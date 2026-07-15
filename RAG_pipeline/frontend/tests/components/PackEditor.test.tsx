import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PackEditor } from "@/components/harness-packs/PackEditor";

describe("PackEditor", () => {
  it("keeps invalid JSON client-side", async () => {
    const onSave = vi.fn();
    render(<PackEditor onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("Policy Pack manifest"), { target: { value: "{" } });
    fireEvent.click(screen.getByRole("button", { name: "Create policy Pack" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter valid Pack JSON.");
    expect(onSave).not.toHaveBeenCalled();
  });

  it("sends parsed data to the isolated save contract", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PackEditor onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("Policy Pack manifest"), { target: { value: JSON.stringify({ id: "custom.example" }) } });
    fireEvent.click(screen.getByRole("button", { name: "Create policy Pack" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({ id: "custom.example" }, "custom.example"));
  });
});
