import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentUpload } from "@/components/DocumentUpload";

const mutate = vi.fn();
const setSelectedProject = vi.fn();

vi.mock("@/hooks/useDocuments", () => ({
  useUploadDocument: () => ({
    mutate,
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
    setSelectedProject.mockClear();
  });

  it("sends the selected document role with the uploaded file", () => {
    const { container } = render(<DocumentUpload />);
    const roleSelect = screen.getByLabelText("Role");
    fireEvent.change(roleSelect, { target: { value: "policy" } });

    const fileInput = container.querySelector('input[type="file"]');
    const file = new File(["content"], "policy.pdf", { type: "application/pdf" });

    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput!, { target: { files: [file] } });

    expect(mutate).toHaveBeenCalledWith({
      file,
      project_id: "proj",
      document_role: "policy",
    });
  });
});
