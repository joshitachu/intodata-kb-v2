import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OnboardingLauncher } from "@/components/onboarding/OnboardingLauncher";
import { listPacks } from "@/lib/packs";

vi.mock("@/lib/packs", () => ({
  listPacks: vi.fn(),
}));

describe("OnboardingLauncher", () => {
  beforeEach(() => {
    vi.mocked(listPacks).mockResolvedValue([
      {
        id: 17,
        name: "Operations Handbook",
        manifest_name: "operations-handbook",
        version: "1.0.0",
        description: "Operations documentation",
        repo_url: null,
        repo_branch: null,
        last_commit_sha: null,
        auto_update: false,
        origin: "personal",
        certification_status: "personal",
        certification_provenance: null,
        personal: true,
        usable: true,
        mutable: true,
        blocked_reason: null,
      },
    ]);
  });

  it("starts agentic onboarding with the selected reusable Pack", async () => {
    const onStart = vi.fn();
    render(<OnboardingLauncher onStart={onStart} />);

    fireEvent.change(screen.getByLabelText("Project name"), {
      target: { value: "customer_orders" },
    });
    fireEvent.change(screen.getByLabelText("Repository URL"), {
      target: { value: "https://github.com/acme/customer-orders" },
    });
    fireEvent.change(screen.getByLabelText("Platform"), {
      target: { value: "workato" },
    });
    await waitFor(() => expect(screen.getByLabelText("Documentation Pack")).toHaveValue(""));
    fireEvent.change(screen.getByLabelText("Documentation Pack"), {
      target: { value: "17" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start onboarding" }));

    expect(onStart).toHaveBeenCalledWith(
      "Onboard a new project called customer_orders from Git repository "
      + "https://github.com/acme/customer-orders. The source type is git and the "
      + "platform is workato. Use documentation pack \"Operations Handbook\".",
    );
  });
});
