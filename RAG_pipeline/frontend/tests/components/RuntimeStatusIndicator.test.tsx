import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const mockUseRuntimeStatus = vi.fn();
vi.mock("@/hooks/useRuntimeStatus", () => ({
  useRuntimeStatus: () => mockUseRuntimeStatus(),
}));

import { RuntimeStatusIndicator } from "@/components/RuntimeStatusIndicator";

describe("RuntimeStatusIndicator", () => {
  it("renders a loading message while checking", () => {
    mockUseRuntimeStatus.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    render(<RuntimeStatusIndicator />);
    expect(screen.getByText("Checking live runtime...")).toBeInTheDocument();
  });

  it("renders disabled message", () => {
    mockUseRuntimeStatus.mockReturnValue({
      data: {
        status: "disabled",
        provider_key: null,
        provider_label: "Live runtime",
        message: "Live runtime disabled",
        checked_at: "2026-06-16T00:00:00Z",
      },
      isLoading: false,
      isError: false,
    });
    render(<RuntimeStatusIndicator />);
    expect(screen.getByText("Live runtime disabled")).toBeInTheDocument();
  });

  it("renders unavailable message", () => {
    mockUseRuntimeStatus.mockReturnValue({
      data: {
        status: "unavailable",
        provider_key: "talend_tmc",
        provider_label: "Talend TMC",
        message: "Talend TMC unavailable",
        checked_at: "2026-06-16T00:00:00Z",
      },
      isLoading: false,
      isError: false,
    });
    render(<RuntimeStatusIndicator />);
    expect(screen.getByText("Talend TMC unavailable")).toBeInTheDocument();
  });

  it("renders connected message", () => {
    mockUseRuntimeStatus.mockReturnValue({
      data: {
        status: "connected",
        provider_key: "talend_tmc",
        provider_label: "Talend TMC",
        message: "Talend TMC connected",
        checked_at: "2026-06-16T00:00:00Z",
      },
      isLoading: false,
      isError: false,
    });
    render(<RuntimeStatusIndicator />);
    expect(screen.getByText("Talend TMC connected")).toBeInTheDocument();
  });

  it("degrades to an unknown message on error, without throwing", () => {
    mockUseRuntimeStatus.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    render(<RuntimeStatusIndicator />);
    expect(screen.getByText("Live runtime status unknown")).toBeInTheDocument();
  });
});
