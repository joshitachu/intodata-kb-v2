import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StylePackEditor } from "@/components/harness-packs/StylePackEditor";

describe("StylePackEditor", () => {
  it("creates a private structured presentation Pack without a manifest editor", async () => {
    const onCreatePrivate = vi.fn().mockResolvedValue(undefined);
    render(<StylePackEditor projectId="project-a" privatePacks={[]} projectPacks={[]} onCreatePrivate={onCreatePrivate} onSavePrivateVersion={vi.fn()} onCreateProject={vi.fn()} onSaveProjectVersion={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Executive Brief" } });
    fireEvent.change(screen.getByLabelText("Audience"), { target: { value: "executive" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Style Pack" }));

    await waitFor(() => expect(onCreatePrivate).toHaveBeenCalledWith(expect.objectContaining({
      id: "style.executive.brief", name: "Executive Brief", version: "1.0.0", presentation: expect.objectContaining({ audience: "executive" }),
    })));
    expect(screen.queryByLabelText(/manifest/i)).not.toBeInTheDocument();
  });

  it("limits generated logical slugs to the server canonical namespace budget", async () => {
    const onCreateProject = vi.fn().mockResolvedValue(undefined);
    render(<StylePackEditor projectId="project-a" privatePacks={[]} projectPacks={[]} onCreatePrivate={vi.fn()} onSavePrivateVersion={vi.fn()} onCreateProject={onCreateProject} onSaveProjectVersion={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Project scoped" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "A".repeat(120) } });
    fireEvent.change(screen.getByLabelText("Audience"), { target: { value: "engineering" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Style Pack" }));

    await waitFor(() => expect(onCreateProject).toHaveBeenCalledWith(expect.objectContaining({
      id: `style.${"a".repeat(48)}`,
    })));
  });
});
