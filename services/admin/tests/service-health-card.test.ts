import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import ServiceHealthCard from "../src/lib/components/service-health-card.svelte";
import type { ServiceHealth } from "../src/lib/types";

const baseHealth: ServiceHealth = {
  service: "auth",
  display_name: "Auth",
  status: "healthy",
  last_check: "2026-05-08T11:59:50.000Z",
};

describe("ServiceHealthCard", () => {
  it("renders the display name", () => {
    render(ServiceHealthCard, { props: { health: baseHealth } });
    expect(screen.getByText("Auth")).toBeInTheDocument();
  });

  it("renders the health badge with the right status data attribute", () => {
    render(ServiceHealthCard, { props: { health: baseHealth } });
    const badge = screen.getByTestId("health-badge");
    expect(badge).toHaveAttribute("data-status", "healthy");
    expect(badge.textContent?.trim()).toBe("Healthy");
  });

  it("renders the unhealthy variant with message", () => {
    const unhealthy: ServiceHealth = {
      ...baseHealth,
      service: "transcriber-stream",
      display_name: "Transcriber (stream)",
      status: "unhealthy",
      message: "GPU container failed health probe",
    };
    render(ServiceHealthCard, { props: { health: unhealthy } });
    expect(screen.getByTestId("health-badge")).toHaveAttribute("data-status", "unhealthy");
    expect(screen.getByTestId("health-message").textContent?.trim()).toBe(
      "GPU container failed health probe",
    );
  });

  it("renders the unknown variant", () => {
    const unknown: ServiceHealth = { ...baseHealth, status: "unknown" };
    render(ServiceHealthCard, { props: { health: unknown } });
    const badge = screen.getByTestId("health-badge");
    expect(badge).toHaveAttribute("data-status", "unknown");
    expect(badge.textContent?.trim()).toBe("Unknown");
  });

  it("links to the service detail page", () => {
    render(ServiceHealthCard, { props: { health: baseHealth } });
    const link = screen.getByTestId("details-link");
    expect(link).toHaveAttribute("href", "/dashboard/auth");
  });

  it("shows the last-check timestamp", () => {
    render(ServiceHealthCard, { props: { health: baseHealth } });
    expect(screen.getByTestId("last-check").textContent).toContain("Last check:");
  });
});
