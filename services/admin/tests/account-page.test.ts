import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import AccountPage from "../src/routes/account/+page.svelte";

// Mock the lib/api module so we control the network behavior without
// touching globalThis.fetch. The page imports the named helper, so we
// stub the named export.
vi.mock("../src/lib/api", () => ({
  createBillingPortalSession: vi.fn(),
}));

import { createBillingPortalSession } from "../src/lib/api";

const mockedCreate = createBillingPortalSession as unknown as ReturnType<typeof vi.fn>;

afterEach(() => {
  mockedCreate.mockReset();
});

describe("AccountPage", () => {
  it("renders the manage-subscription button", () => {
    render(AccountPage, {
      props: {
        redirect: vi.fn(),
        currentUrl: () => "https://panakoes.com/account",
      },
    });
    const button = screen.getByTestId("manage-subscription-button");
    expect(button).toBeInTheDocument();
    expect(button.textContent?.trim()).toBe("Manage subscription");
  });

  it("posts to the billing endpoint and redirects to the response url on click", async () => {
    mockedCreate.mockResolvedValueOnce({
      url: "https://billing.stripe.com/session/bps_test_123",
    });
    const redirect = vi.fn();

    render(AccountPage, {
      props: {
        redirect,
        currentUrl: () => "https://panakoes.com/account",
      },
    });

    await userEvent.click(screen.getByTestId("manage-subscription-button"));

    expect(mockedCreate).toHaveBeenCalledTimes(1);
    expect(mockedCreate).toHaveBeenCalledWith("https://panakoes.com/account");
    expect(redirect).toHaveBeenCalledWith("https://billing.stripe.com/session/bps_test_123");
  });

  it("surfaces a user-facing error when the billing service fails", async () => {
    mockedCreate.mockRejectedValueOnce(new Error("HTTP 502"));
    const redirect = vi.fn();

    render(AccountPage, {
      props: {
        redirect,
        currentUrl: () => "https://panakoes.com/account",
      },
    });

    await userEvent.click(screen.getByTestId("manage-subscription-button"));

    expect(redirect).not.toHaveBeenCalled();
    const error = await screen.findByTestId("portal-error");
    expect(error.textContent).toContain("Could not open the billing portal");
    expect(error.textContent).toContain("HTTP 502");
  });

  it("surfaces a generic error when a non-Error is thrown", async () => {
    mockedCreate.mockRejectedValueOnce("oops");
    const redirect = vi.fn();

    render(AccountPage, {
      props: {
        redirect,
        currentUrl: () => "https://panakoes.com/account",
      },
    });

    await userEvent.click(screen.getByTestId("manage-subscription-button"));

    expect(redirect).not.toHaveBeenCalled();
    const error = await screen.findByTestId("portal-error");
    expect(error.textContent?.trim()).toBe("Could not open the billing portal.");
  });
});
