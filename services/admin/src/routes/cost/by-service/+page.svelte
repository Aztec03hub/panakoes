<script lang="ts">
  import { ApiError, fetchCostByService, formatUsdCents } from "$lib/api";
  import type { ServiceCostBreakdown } from "$lib/types";

  // Default to "current calendar month so far" per the implementation plan's
  // date-range UX. Phase 2 will lift this into a date-range picker component
  // shared across all cost pages; for v0.1 we hardcode the defaults so the
  // page renders without a control surface.
  const today = new Date();
  const defaultTo = today.toISOString().slice(0, 10);
  const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
  const defaultFrom = monthStart.toISOString().slice(0, 10);

  let snapshot = $state<ServiceCostBreakdown | null>(null);
  let loading = $state(true);
  let errorMessage = $state<string | null>(null);

  $effect(() => {
    (async () => {
      try {
        snapshot = await fetchCostByService(defaultFrom, defaultTo);
      } catch (err) {
        if (err instanceof ApiError) {
          if (err.status === 401) {
            // Once Better-Auth's flow lands the dashboard owns the redirect.
            // For v0.1 surface a clear sign-in message rather than silently
            // bouncing through a half-wired auth flow.
            errorMessage = "Session expired. Please sign in again.";
          } else {
            errorMessage = `Cost API returned HTTP ${err.status}`;
          }
        } else {
          errorMessage = err instanceof Error ? err.message : "Unknown error";
        }
      } finally {
        loading = false;
      }
    })();
  });
</script>

<div class="flex flex-col gap-6">
  <div class="flex items-end justify-between">
    <div>
      <h1 class="text-2xl font-bold tracking-tight">Cost by service</h1>
      <p class="text-sm text-muted-foreground">
        Per-service AWS spend for {defaultFrom} through {defaultTo}.
      </p>
    </div>
    {#if snapshot}
      <p class="text-xs text-muted-foreground" data-testid="cache-marker">
        {snapshot.cache_hit ? "Served from cache" : "Fresh from Cost Explorer"}
      </p>
    {/if}
  </div>

  {#if loading}
    <p class="text-muted-foreground" data-testid="loading">
      Loading cost breakdown...
    </p>
  {:else if errorMessage}
    <p class="text-destructive" role="alert" data-testid="error">
      {errorMessage}
    </p>
  {:else if snapshot}
    <div class="rounded-md border" data-testid="services-table">
      <table class="w-full text-sm">
        <thead class="border-b bg-muted/50 text-left">
          <tr>
            <th class="px-3 py-2 font-medium">Service</th>
            <th class="px-3 py-2 font-medium text-right">Cost</th>
            <th class="px-3 py-2 font-medium text-right">% of total</th>
          </tr>
        </thead>
        <tbody>
          {#each snapshot.services as row (row.service)}
            <tr class="border-b last:border-b-0">
              <td class="px-3 py-2">{row.service}</td>
              <td class="px-3 py-2 text-right tabular-nums">
                {formatUsdCents(row.cost_cents)}
              </td>
              <td class="px-3 py-2 text-right tabular-nums">
                {row.percent_of_total.toFixed(2)}%
              </td>
            </tr>
          {/each}
          <tr class="bg-muted/30 font-medium">
            <td class="px-3 py-2">Total</td>
            <td class="px-3 py-2 text-right tabular-nums">
              {formatUsdCents(snapshot.total_cents)}
            </td>
            <td class="px-3 py-2 text-right tabular-nums">100.00%</td>
          </tr>
        </tbody>
      </table>
    </div>
  {/if}
</div>
