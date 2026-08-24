import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AutoNavigationBanner } from "@/components/copilot/AutoNavigationBanner";

/**
 * The banner's one job is to describe what the canvas actually did.
 *
 * "Moved to AFAS" while the landscape is still on screen sends the user looking
 * for a view that changed, and finding the same view is what makes them stop
 * believing the banner at all. So the verb is asserted per movement kind, not
 * treated as decoration.
 */

function renderBanner(props: Partial<Parameters<typeof AutoNavigationBanner>[0]> = {}) {
  const onUndo = vi.fn();
  render(
    <AutoNavigationBanner label="AFAS → Maximo" onUndo={onUndo} {...props} />,
  );
  return { onUndo, banner: screen.getByTestId("auto-navigation-banner") };
}

describe("AutoNavigationBanner", () => {
  it("says the relationship was focused when the level did not change", () => {
    const { banner } = renderBanner({ movement: "focused", reason: "follow" });
    expect(banner).toHaveTextContent("Focused on AFAS → Maximo");
    expect(banner).toHaveTextContent("to follow the conversation");
    expect(banner).not.toHaveTextContent("Moved to");
  });

  it("says opened when the stack descended into the thing already in view", () => {
    const { banner } = renderBanner({ movement: "opened", reason: "follow" });
    expect(banner).toHaveTextContent("Opened AFAS → Maximo");
  });

  it("claims movement only for a real change of subject", () => {
    const { banner } = renderBanner({
      label: "afas_4ps_employee_orchestration",
      movement: "navigated",
      reason: "explicit",
    });
    expect(banner).toHaveTextContent("Moved to afas_4ps_employee_orchestration");
    expect(banner).toHaveTextContent("because you asked to be taken there");
  });

  it("defaults to the strongest claim, so an un-migrated caller cannot understate a jump", () => {
    const { banner } = renderBanner();
    expect(banner).toHaveTextContent("Moved to AFAS → Maximo");
    expect(banner.dataset.movement).toBe("navigated");
  });

  it("appends the level description only when it adds something", () => {
    const { banner } = renderBanner({ movement: "focused", detail: "Relationship" });
    expect(banner).toHaveTextContent("Focused on AFAS → Maximo · Relationship");
  });

  it("offers the previous view back in one action", () => {
    const { onUndo } = renderBanner({ movement: "focused" });
    fireEvent.click(screen.getByRole("button", { name: /undo/i }));
    expect(onUndo).toHaveBeenCalledTimes(1);
  });

  it("announces rather than interrupts", () => {
    const { banner } = renderBanner({ movement: "focused" });
    expect(banner).toHaveAttribute("role", "status");
  });
});
