import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentUpload } from "@/components/DocumentUpload";

const mutate = vi.fn();
const mutateAsync = vi.fn();
const setSelectedProject = vi.fn();

vi.mock("@/hooks/useDocuments", () => ({
  useUploadDocument: () => ({
    mutate,
    mutateAsync,
    isPending: false,
    isSuccess: false,
    isError: false,
  }),
}));

vi.mock("@/hooks/useDocsProjects", () => ({
  useDocsProjects: () => ({
    data: {
      projects: [
        { id: "proj", display_name: "Project", platform: "talend" },
      ],
    },
    isLoading: false,
  }),
}));

vi.mock("@/contexts/ChatContext", () => ({
  useChatContext: () => ({
    selectedProject: "proj",
    setSelectedProject,
  }),
}));

describe("DocumentUpload", () => {
  beforeEach(() => {
    mutate.mockClear();
    mutateAsync.mockReset();
    mutateAsync.mockResolvedValue({ message: "Ingested 1 chunks" });
    setSelectedProject.mockClear();
  });

  it("queues the selected document role with the uploaded file", async () => {
    const { container } = render(<DocumentUpload />);
    const roleSelect = screen.getByLabelText("Role");
    fireEvent.change(roleSelect, { target: { value: "policy" } });

    const fileInput = container.querySelector('input[type="file"]');
    const file = new File(["content"], "policy.pdf", { type: "application/pdf" });

    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput!, { target: { files: [file] } });

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({
      file,
      project_id: "proj",
      document_role: "policy",
    }));
  });

  it("queues every selected file and processes them one at a time", async () => {
    const { container } = render(<DocumentUpload />);
    const fileInput = container.querySelector('input[type="file"]');
    const first = new File(["first"], "first.pdf", { type: "application/pdf" });
    const second = new File(["# Second"], "second.md", { type: "text/markdown" });

    expect(fileInput).toHaveProperty("multiple", true);
    fireEvent.change(fileInput!, { target: { files: [first, second] } });

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2));
    expect(mutateAsync).toHaveBeenNthCalledWith(1, {
      file: first,
      project_id: "proj",
      document_role: "source",
    });
    expect(mutateAsync).toHaveBeenNthCalledWith(2, {
      file: second,
      project_id: "proj",
      document_role: "source",
    });
    expect(await screen.findAllByText(/Complete —/)).toHaveLength(2);
  });
});
