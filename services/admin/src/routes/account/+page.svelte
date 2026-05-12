<script lang="ts">
  /**
   * Account settings page.
   *
   * Today's only widget is the "Manage subscription" button, which mints a
   * Stripe Customer Portal session via `POST /v1/billing/portal-session`
   * and redirects the user to the hosted portal so they can upgrade,
   * downgrade, update their card, or view past invoices without an admin
   * in the loop.
   *
   * The portal triggers the same `customer.subscription.*` webhooks the
   * checkout flow already handles, so any change made inside the portal
   * flows back through the billing service's webhook handler without
   * extra wiring here.
   */
  import { buttonVariants } from "$lib/components/ui/button";
  import { createBillingPortalSession } from "$lib/api";

  let loading = $state(false);
  let errorMessage = $state<string | null>(null);

  // Injectable side-effects for tests. Production uses the live globals.
  let {
    redirect = (url: string) => {
      window.location.assign(url);
    },
    currentUrl = () => window.location.href,
  }: {
    redirect?: (url: string) => void;
    currentUrl?: () => string;
  } = $props();

  async function openPortal(): Promise<void> {
    errorMessage = null;
    loading = true;
    try {
      const response = await createBillingPortalSession(currentUrl());
      redirect(response.url);
    } catch (err) {
      // The billing service returns 422 for an off-allowlist return URL,
      // 401 for auth (which `apiFetch` also handles via the global
      // sign-out side-effect), and 5xx for stripe outages. Surface a
      // single human-readable message; the exact status is in the
      // `ApiError` for the support handoff.
      errorMessage =
        err instanceof Error
          ? `Could not open the billing portal: ${err.message}`
          : "Could not open the billing portal.";
    } finally {
      loading = false;
    }
  }
</script>

<section class="space-y-6">
  <header class="space-y-1">
    <h1 class="text-2xl font-semibold">Account settings</h1>
    <p class="text-sm text-muted-foreground">
      Manage your subscription, payment method, and invoices through the
      Stripe-hosted Customer Portal.
    </p>
  </header>

  <div class="rounded-lg border bg-card p-6 space-y-4">
    <div class="space-y-1">
      <h2 class="text-lg font-medium">Subscription</h2>
      <p class="text-sm text-muted-foreground">
        Opens the secure Stripe Customer Portal in this tab. You can return to
        this page when you are done.
      </p>
    </div>
    <button
      type="button"
      data-testid="manage-subscription-button"
      class={buttonVariants({ variant: "default", size: "default" })}
      onclick={openPortal}
      disabled={loading}
    >
      {loading ? "Opening portal..." : "Manage subscription"}
    </button>
    {#if errorMessage !== null}
      <p class="text-sm text-destructive" role="alert" data-testid="portal-error">
        {errorMessage}
      </p>
    {/if}
  </div>
</section>
