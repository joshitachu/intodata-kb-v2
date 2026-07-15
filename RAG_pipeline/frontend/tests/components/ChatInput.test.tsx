import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatInput } from "@/components/ChatInput";

describe("ChatInput", () => {
  it("sends an uploaded image without requiring typed text", async () => {
    const onSend = vi.fn();
    const { container } = render(<ChatInput onSend={onSend} isLoading={false} />);
    const fileInput = container.querySelector('input[type="file"]');
    const image = new File(["image-bytes"], "diagram.png", {
      type: "image/png",
    });

    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput!, { target: { files: [image] } });

    await waitFor(() => {
      expect(screen.getByRole("img", { name: "Attachment 1" })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(onSend).toHaveBeenCalledWith(
      "Analyze the attached image.",
      [expect.any(String)],
    );
  });

  it("validates ZIP sources before an upload can start", () => {
    const onZipUploaded = vi.fn();
    const { container } = render(
      <ChatInput onSend={vi.fn()} isLoading={false} onZipUploaded={onZipUploaded} />,
    );
    const inputs = container.querySelectorAll('input[type="file"]');
    const zipInput = inputs[1];

    fireEvent.change(zipInput, {
      target: { files: [new File(["not a zip"], "notes.txt", { type: "text/plain" })] },
    });

    expect(screen.getByText("Choose a ZIP archive")).toBeInTheDocument();
  });

  it("handles local documentation Pack library commands without sending a chat turn", () => {
    const onSend = vi.fn();
    const onSlashCommand = vi.fn().mockReturnValue(true);
    render(
      <ChatInput
        onSend={onSend}
        onSlashCommand={onSlashCommand}
        isLoading={false}
      />,
    );

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "/pack list" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(onSlashCommand).toHaveBeenCalledWith("pack", "list");
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue("");
  });
});
