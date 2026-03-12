import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ConfirmDialog } from "@/components/ConfirmDialog";

describe("ConfirmDialog", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <ConfirmDialog
        open={false}
        title="Test"
        message="Are you sure?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders title and message when open", () => {
    render(
      <ConfirmDialog
        open={true}
        title="Delete Item"
        message="This cannot be undone."
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(screen.getByText("Delete Item")).toBeInTheDocument();
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();
  });

  it("calls onConfirm when confirm button clicked", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open={true}
        title="Test"
        message="Sure?"
        confirmLabel="Delete"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />
    );
    fireEvent.click(screen.getByText("Delete"));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("calls onCancel when cancel button clicked", () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open={true}
        title="Test"
        message="Sure?"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />
    );
    fireEvent.click(screen.getByText("Cancel"));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("uses custom confirmLabel", () => {
    render(
      <ConfirmDialog
        open={true}
        title="Test"
        message="Sure?"
        confirmLabel="Remove"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(screen.getByText("Remove")).toBeInTheDocument();
  });
});
