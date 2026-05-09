<script lang="ts">
  import { onMount } from "svelte";
  import * as Table from "$lib/components/ui/table";
  import { ApiError, fetchCostByTenant } from "$lib/api";
  import { formatTimestamp } from "$lib/utils";
  import type { TenantCostBreakdown } from "$lib/types";

  // Default window: current calendar month so far (UTC). `from` is the
  // 1st of the current month, `to` is today (exclusive end). ISO date
  // math via `Date.toISOString().slice(0, 10)` keeps the UI dependency
  // free; the user-controlled date pickers land in Day 2.4.
  const today = new Date();
  const monthStart = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1));
  const fromDate = monthStart.toISOString().slice(0, 10);
  const toDate = today.toISOString().slice(0, 10);

  let breakdown: TenantCostBreakdown | null = null;
  let loading = true;
  let errorMessage: string | null = null;

  onMount(async () => {
    try {
      breakdown = await fetchCostByTenant(fromDate, toDate);
    } catch (err) {
      if (err instanceof ApiError) {
        errorMessage = `Cost endpoint returned HTTP ${err.status}`;
      } else {
        errorMessage = err instanceof Error ? err.message : "Unknown error";
      }
    } finally {
      loading = false;
    }
  });

  /** Format integer cents as `$X.XX`. */
  function formatCents(cents: number): string {
    return `$${(cents / 100).toFixed(2)}`;
  }
</script>

<div class="flex flex-col gap-6">
  <div class="flex items-end justify-between">
    <div>
      <h1 class="text-2xl font-bold tracking-tight">Cost by tenant</h1>
      <p class="text-sm text-muted-foreground">
        Per-tenant spend for the current calendar month so far, sourced
        from the tenant-cost-rollup nightly aggregator.
      </p>
    </div>
    {#if breakdown}
      <div class="text-xs text-muted-foreground text-right">
        <p>Window: {breakdown.from_date} to {breakdown.to_date}</p>
        <p>
          {breakdown.cache_hit ? "Served from cache" : "Live read"} ·
          {formatTimestamp(breakdown.queried_at)}
        </p>
      </div>
    {/if}
  </div>

  {#if loading}
    <p class="text-muted-foreground">Loading per-tenant breakdown...</p>
  {:else if errorMessage}
    <p class="text-destructive" role="alert">{errorMessage}</p>
  {:else if breakdown}
    {#if breakdown.tenants.length === 0}
      <p class="text-muted-foreground">
        No tenant cost data for this window. The nightly aggregator may not
        have run yet, or no tenants had activity in this period.
      </p>
    {:else}
      <Table.Root>
        <Table.Header>
          <Table.Row>
            <Table.Head>Tenant</Table.Head>
            <Table.Head>Tenant ID</Table.Head>
            <Table.Head class="text-right tabular-nums">Cost</Table.Head>
            <Table.Head class="text-right tabular-nums">% of total</Table.Head>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {#each breakdown.tenants as row (row.tenant_id)}
            <Table.Row>
              <Table.Cell class="font-medium">{row.display_name}</Table.Cell>
              <Table.Cell class="text-xs text-muted-foreground">
                {row.tenant_id}
              </Table.Cell>
              <Table.Cell class="text-right tabular-nums">{formatCents(row.cost_cents)}</Table.Cell>
              <Table.Cell class="text-right tabular-nums">
                {row.percent_of_total.toFixed(2)}%
              </Table.Cell>
            </Table.Row>
          {/each}
          <Table.Row class="font-semibold">
            <Table.Cell colspan={2}>Total</Table.Cell>
            <Table.Cell class="text-right tabular-nums">{formatCents(breakdown.total_cents)}</Table.Cell>
            <Table.Cell class="text-right tabular-nums">100.00%</Table.Cell>
          </Table.Row>
        </Table.Body>
      </Table.Root>
    {/if}
  {/if}
</div>
