import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LicenceBanner, LicenceBannerView } from "@/components/LicenceBanner";
import { LicenceContext } from "@/hooks/useLicence";
import type { LicenceStatus } from "@/lib/licence";

const VALID: LicenceStatus = {
  state: "valid",
  reason: null,
  plan: "basic",
  features: ["chat", "generation"],
  limits: { generations_per_period: 3 },
  period_start: "2026-09-01T00:00:00Z",
  period_end: "2026-10-01T00:00:00Z",
  days_remaining: 20,
  near_expiry: false,
  enforcement: "enforce",
};

describe("LicenceBanner", () => {
  it("renders nothing when enforcement is off, even for an expired licence", () => {
    const { container } = render(
      <LicenceBannerView status={{ ...VALID, state: "expired", enforcement: "off" }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for a valid licence that is not near expiry, or an unknown status", () => {
    expect(render(<LicenceBannerView status={VALID} />).container).toBeEmptyDOMElement();
    expect(render(<LicenceBannerView status={null} />).container).toBeEmptyDOMElement();
  });

  it("warns with the days remaining when the licence is near expiry", () => {
    render(<LicenceBannerView status={{ ...VALID, near_expiry: true, days_remaining: 3 }} />);
    const banner = screen.getByTestId("licence-banner");
    expect(banner).toHaveAttribute("data-licence-state", "near_expiry");
    expect(banner).toHaveTextContent("expires in 3 days (2026-10-01)");
    expect(banner).toHaveTextContent("view-only until it is renewed");
  });

  it("uses the singular for one day left", () => {
    render(<LicenceBannerView status={{ ...VALID, near_expiry: true, days_remaining: 1 }} />);
    expect(screen.getByTestId("licence-banner")).toHaveTextContent("expires in 1 day ");
  });

  it("shows a persistent view-only notice for an expired licence", () => {
    render(<LicenceBannerView status={{ ...VALID, state: "expired", days_remaining: 0 }} />);
    const banner = screen.getByRole("alert");
    expect(banner).toHaveAttribute("data-licence-state", "expired");
    expect(banner).toHaveTextContent("expired on 2026-10-01");
    expect(banner).toHaveTextContent("view-only");
    expect(banner).toHaveTextContent("existing documentation and conversations remain readable");
  });

  it.each([
    ["missing", "No licence is installed"],
    ["invalid", "could not be verified"],
    ["not_yet_valid", "not valid until 2026-09-01"],
  ] as const)("explains the %s state", (state, text) => {
    render(<LicenceBannerView status={{ ...VALID, state }} />);
    expect(screen.getByRole("alert")).toHaveTextContent(text);
  });

  it("reads the shared status from the licence context", () => {
    render(
      <LicenceContext.Provider value={{ status: { ...VALID, state: "missing" }, refresh: () => {} }}>
        <LicenceBanner />
      </LicenceContext.Provider>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("No licence is installed");
  });

  it("keeps its text ASCII-only", () => {
    render(<LicenceBannerView status={{ ...VALID, state: "expired" }} />);
    // eslint-disable-next-line no-control-regex
    expect(screen.getByRole("alert").textContent).toMatch(/^[\x00-\x7F]*$/);
  });
});
