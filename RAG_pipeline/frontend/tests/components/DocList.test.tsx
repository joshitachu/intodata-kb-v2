import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DocList } from "@/components/DocList";
import { useProjectDocs, useGeneratePdfs } from "@/hooks/useProjectDocs";
import type { DocFileInfo, DocListResponse } from "@/types";

vi.mock("@/hooks/useProjectDocs", () => ({
  useProjectDocs: vi.fn(),
  useGeneratePdfs: vi.fn(),
}));

function doc(name: string, subproject: string | null): DocFileInfo {
  return {
    name,
    subproject,
    type: "md",
    size_bytes: 1024,
    has_pdf: false,
    modified_at: "2026-08-27T09:00:00Z",
  };
}

// Every sub-project of a partitioned project emits the SAME document names --
// that duplication is what makes the sub-project part of a doc's identity.
const PARTITIONED: DocListResponse = {
  project_id: "werk",
  docs: [
    doc("00_AGENT_BRIEF.md", "Assetcheck"),
    doc("09_EXECUTIVE_SUMMARY.md", "Assetcheck"),
    doc("00_AGENT_BRIEF.md", "VQ_MQ2RHM"),
  ],
  total: 3,
  subprojects: ["Assetcheck", "VQ_MQ2RHM"],
};

const FLAT: DocListResponse = {
  project_id: "flatproj",
  docs: [doc("00_AGENT_BRIEF.md", null)],
  total: 1,
  subprojects: [],
};

function mockDocs(data: DocListResponse) {
  vi.mocked(useProjectDocs).mockReturnValue({
    data,
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useProjectDocs>);
}

function filterBar() {
  return screen.getByRole("group", { name: /filter by sub-project/i });
}

describe("DocList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useGeneratePdfs).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      data: undefined,
      error: null,
    } as unknown as ReturnType<typeof useGeneratePdfs>);
  });

  it("renders no sub-project filter for a flat project", () => {
    mockDocs(FLAT);

    render(<DocList projectId="flatproj" selectedDoc={null} onSelectDoc={vi.fn()} />);

    expect(
      screen.queryByRole("group", { name: /filter by sub-project/i }),
    ).not.toBeInTheDocument();
  });

  it("offers one filter per sub-project with its doc count", () => {
    mockDocs(PARTITIONED);

    render(<DocList projectId="werk" selectedDoc={null} onSelectDoc={vi.fn()} />);

    const bar = within(filterBar());
    expect(bar.getByRole("button", { name: "All (3)" })).toBeInTheDocument();
    expect(bar.getByRole("button", { name: "Assetcheck (2)" })).toBeInTheDocument();
    expect(bar.getByRole("button", { name: "VQ_MQ2RHM (1)" })).toBeInTheDocument();
  });

  it("shows every sub-project's docs before a filter is chosen", () => {
    mockDocs(PARTITIONED);

    render(<DocList projectId="werk" selectedDoc={null} onSelectDoc={vi.fn()} />);

    // Two docs share this name; both must be listed, not deduplicated away.
    expect(screen.getAllByText("00_AGENT_BRIEF.md")).toHaveLength(2);
  });

  it("narrows the list to the selected sub-project", async () => {
    mockDocs(PARTITIONED);
    render(<DocList projectId="werk" selectedDoc={null} onSelectDoc={vi.fn()} />);

    fireEvent.click(within(filterBar()).getByRole("button", { name: "VQ_MQ2RHM (1)" }));

    await waitFor(() => {
      expect(screen.getAllByText("00_AGENT_BRIEF.md")).toHaveLength(1);
    });
    expect(screen.queryByText("09_EXECUTIVE_SUMMARY.md")).not.toBeInTheDocument();
  });

  it("labels each row with its sub-project only while unfiltered", async () => {
    mockDocs(PARTITIONED);
    render(<DocList projectId="werk" selectedDoc={null} onSelectDoc={vi.fn()} />);

    // Unfiltered, the name alone is ambiguous, so the row carries the origin.
    expect(screen.getAllByText("Assetcheck", { selector: "span" })).toHaveLength(2);

    fireEvent.click(within(filterBar()).getByRole("button", { name: "Assetcheck (2)" }));

    await waitFor(() => {
      // Redundant once the filter states it; the chip stays, the row labels go.
      expect(
        screen.queryAllByText("Assetcheck", { selector: "span" }),
      ).toHaveLength(0);
    });
  });

  it("passes the whole doc to onSelectDoc so the sub-project travels with it", () => {
    mockDocs(PARTITIONED);
    const onSelectDoc = vi.fn();
    render(<DocList projectId="werk" selectedDoc={null} onSelectDoc={onSelectDoc} />);

    fireEvent.click(screen.getAllByText("00_AGENT_BRIEF.md")[1]);

    expect(onSelectDoc).toHaveBeenCalledWith(
      expect.objectContaining({ name: "00_AGENT_BRIEF.md", subproject: "VQ_MQ2RHM" }),
    );
  });

  it("puts the sub-project on download links", () => {
    mockDocs(PARTITIONED);

    render(<DocList projectId="werk" selectedDoc={null} onSelectDoc={vi.fn()} />);

    const links = screen.getAllByTitle("Download MD") as HTMLAnchorElement[];
    expect(links[2]).toHaveAttribute(
      "href",
      "/api/docs/werk/00_AGENT_BRIEF.md?subproject=VQ_MQ2RHM",
    );
  });

  it("omits the query string for a flat project", () => {
    mockDocs(FLAT);

    render(<DocList projectId="flatproj" selectedDoc={null} onSelectDoc={vi.fn()} />);

    expect(screen.getByTitle("Download MD")).toHaveAttribute(
      "href",
      "/api/docs/flatproj/00_AGENT_BRIEF.md",
    );
  });

  it("highlights only the selected sub-project's copy of a shared name", () => {
    mockDocs(PARTITIONED);

    const { container } = render(
      <DocList
        projectId="werk"
        selectedDoc={doc("00_AGENT_BRIEF.md", "VQ_MQ2RHM")}
        onSelectDoc={vi.fn()}
      />,
    );

    // Selecting by bare filename would light up both rows.
    expect(container.querySelectorAll(".bg-secondary")).toHaveLength(1);
  });

  it("falls back to All when a filter does not apply to the current project", async () => {
    mockDocs(PARTITIONED);
    const { rerender } = render(
      <DocList projectId="werk" selectedDoc={null} onSelectDoc={vi.fn()} />,
    );
    fireEvent.click(within(filterBar()).getByRole("button", { name: "Assetcheck (2)" }));
    await waitFor(() =>
      expect(screen.queryByText("09_EXECUTIVE_SUMMARY.md")).toBeInTheDocument(),
    );

    // The component is not remounted when the project changes; a stale filter
    // would otherwise hide every document with no visible cause.
    mockDocs(FLAT);
    rerender(<DocList projectId="flatproj" selectedDoc={null} onSelectDoc={vi.fn()} />);

    expect(screen.getByText("00_AGENT_BRIEF.md")).toBeInTheDocument();
  });
});
