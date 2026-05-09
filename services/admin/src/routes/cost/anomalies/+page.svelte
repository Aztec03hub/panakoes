<script lang="ts">
  import { onMount } from "svelte";
  import * as Table from "$lib/components/ui/table";
  import { ApiError, fetchCostAnomalies, formatUsdCents } from "$lib/api";
  import { formatTimestamp } from "$lib/utils";
  import type { CostAnomalyList } from "$lib/types";

  // Filter state. `detectorFilter` is bound to the text input;
  // `activeOnly` to the toggle. Both default to the safe values: no
  // detector filter, active-only=true so the page is fast and CE-rate
  // -limit-safe by default.
  let detectorFilter: string = "";
  let activeOnly: boolean = true;

  let payload: CostAnomalyList | null = null;
  let loading = true;
  let errorMessage: string | null = null;

  /** Re-fetch with the current filter values. Called on mount AND any
   *  time the user changes a filter; the page re-renders against the
   *  fresh payload without a route reload. */
  async function refresh() {
    loading = true;
    errorMessage = null;
    try {
      const detector = detectorFilter.trim() === "" ? undefined : detectorFilter.trim();
      payload = await fetchCostAnomalies(detector, activeOnly);
    } catch (err) {
      if (err instanceof ApiError) {
        errorMessage = `Cost anomalies endpoint returned HTTP ${err.status}`;
      } else {
        errorMessage = err instanceof Error ? err.message : "Unknown error";
      }
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    refresh();
  });
</script>

<div class="flex flex-col gap-6">
  <div class="flex items-end justify-between">
    <div>
      <h1 class="text-2xl font-bold tracking-tight">Cost anomalies</h1>
      <p class="text-sm text-muted-foreground">
        Cost-anomaly alerts the detector emitted, deduplicated against
        alert-state's quiet-period window.
      </p>
    </div>
    {#if payload}
      <div class="text-xs text-muted-foreground text-right">
        <p>{formatTimestamp(payload.queried_at)}</p>
      </div>
    {/if}
  </div>

  <div class="flex flex-wrap items-center gap-4">
    <label class="flex flex-col gap-1 text-sm">
      <span class="text-muted-foreground">Detector</span>
      <input
        type="text"
        class="border rounded px-2 py-1 text-sm"
        placeholder="ce-monitor"
        bind:value={detectorFilter}
        on:change={refresh}
      />
    </label>
    <label class="flex items-center gap-2 text-sm">
      <input
        type="checkbox"
        bind:checked={activeOnly}
        on:change={refresh}
      />
      <span>Active only</span>
    </label>
  </div>

  {#if loading}
    <p class="text-muted-foreground">Loading cost anomalies...</p>
  {:else if errorMessage}
    <p class="text-destructive" role="alert">{errorMessage}</p>
  {:else if payload}
    {#if payload.anomalies.length === 0}
      <p class="text-muted-foreground">No active anomalies.</p>
    {:else}
      <Table.Root>
        <Table.Header>
          <Table.Row>
            <Table.Head>Signature</Table.Head>
            <Table.Head>Detector</Table.Head>
            <Table.Head>Tenant</Table.Head>
            <Table.Head>Dimension</Table.Head>
            <Table.Head class="text-right tabular-nums">Observed</Table.Head>
            <Table.Head class="text-right tabular-nums">Expected</Table.Head>
            <Table.Head class="text-right tabular-nums">Deviation</Table.Head>
            <Table.Head>First seen</Table.Head>
            <Table.Head>Last seen</Table.Head>
            <Table.Head>Status</Table.Head>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {#each payload.anomalies as a (a.signature)}
            <Table.Row>
              <Table.Cell class="font-mono text-xs">{a.signature}</Table.Cell>
              <Table.Cell>{a.detector}</Table.Cell>
              <Table.Cell class="text-xs text-muted-foreground">
                {a.tenant_id ?? "-"}
              </Table.Cell>
              <Table.Cell>{a.dimension_key}</Table.Cell>
              <Table.Cell class="text-right tabular-nums">
                {formatUsdCents(a.observed_cost_cents)}
              </Table.Cell>
              <Table.Cell class="text-right tabular-nums">
                {formatUsdCents(a.expected_cost_cents)}
              </Table.Cell>
              <Table.Cell class="text-right tabular-nums">
                {a.deviation_pct.toFixed(2)}%
              </Table.Cell>
              <Table.Cell class="text-xs">{formatTimestamp(a.first_seen)}</Table.Cell>
              <Table.Cell class="text-xs">{formatTimestamp(a.last_seen)}</Table.Cell>
              <Table.Cell>
                {#if a.suppressed}
                  <span
                    class="inline-flex items-center rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground"
                  >
                    suppressed
                  </span>
                {:else}
                  <span
                    class="inline-flex items-center rounded bg-green-100 px-2 py-0.5 text-xs text-green-800"
                  >
                    active
                  </span>
                {/if}
              </Table.Cell>
            </Table.Row>
          {/each}
        </Table.Body>
      </Table.Root>
    {/if}
  {/if}
</div>
