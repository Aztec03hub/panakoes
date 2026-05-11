<script lang="ts">
  import { untrack } from "svelte";
  import * as Table from "$lib/components/ui/table";
  import {
    ApiError,
    COST_FORECAST_HORIZONS,
    fetchCostForecast,
    formatUsdCents,
  } from "$lib/api";
  import { formatTimestamp } from "$lib/utils";
  import type { CostForecast, ForecastBucket } from "$lib/types";

  // Horizon state. Default 30 days matches the by-service "current month
  // so far" cadence operators are already trained on. The dropdown is
  // populated from the shared `COST_FORECAST_HORIZONS` constant so the
  // page and the API helper agree on the supported menu.
  let horizonDays = $state<number>(30);

  let forecast = $state<CostForecast | null>(null);
  let loading = $state(true);
  let errorMessage = $state<string | null>(null);

  /** Re-fetch with the current horizon. Called on mount and on every
   *  dropdown change; the page re-renders against the fresh payload
   *  without a route reload (mirrors cost/anomalies/+page.svelte). */
  async function refresh() {
    loading = true;
    errorMessage = null;
    try {
      forecast = await fetchCostForecast(horizonDays);
    } catch (err) {
      if (err instanceof ApiError) {
        errorMessage = `Cost forecast endpoint returned HTTP ${err.status}`;
      } else {
        errorMessage = err instanceof Error ? err.message : "Unknown error";
      }
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    untrack(() => refresh());
  });

  /** Sum the per-day predicted spend across the whole horizon. Useful
   *  as a single-line "total spend predicted" headline above the table. */
  function totalPredictedCents(buckets: readonly ForecastBucket[]): number {
    return buckets.reduce((acc, b) => acc + b.predicted_cost_cents, 0);
  }

  // ---------------------------------------------------------------------
  // Inline SVG chart helpers.
  //
  // No chart dep is in `package.json` and the brief forbids adding one,
  // so we hand-roll a tiny line + confidence-band SVG. The math is
  // straightforward: map each bucket's index to an x position across
  // CHART_WIDTH, map each cents value to a y position across CHART_HEIGHT
  // (inverted because SVG y grows downward), then emit one polygon for
  // the [lower, upper] band and one polyline for the predicted line.
  // ---------------------------------------------------------------------

  const CHART_WIDTH = 720;
  const CHART_HEIGHT = 220;
  const CHART_PADDING_X = 32;
  const CHART_PADDING_Y = 16;

  /** Largest cents value across all three series. Drives the y scale.
   *  Floored at 1 so an all-zero forecast still produces a valid scale. */
  function chartCeiling(buckets: readonly ForecastBucket[]): number {
    let ceiling = 1;
    for (const b of buckets) {
      if (b.upper_bound_cents > ceiling) ceiling = b.upper_bound_cents;
      if (b.predicted_cost_cents > ceiling) ceiling = b.predicted_cost_cents;
    }
    return ceiling;
  }

  /** x position for bucket index `i` given total bucket count `n`. */
  function xFor(i: number, n: number): number {
    if (n <= 1) return CHART_PADDING_X;
    const usable = CHART_WIDTH - 2 * CHART_PADDING_X;
    return CHART_PADDING_X + (usable * i) / (n - 1);
  }

  /** y position for a cents value given the chart ceiling. */
  function yFor(cents: number, ceiling: number): number {
    const usable = CHART_HEIGHT - 2 * CHART_PADDING_Y;
    const ratio = ceiling > 0 ? cents / ceiling : 0;
    return CHART_HEIGHT - CHART_PADDING_Y - usable * ratio;
  }

  /** Polyline points for the predicted-spend line. */
  function predictedPath(buckets: readonly ForecastBucket[], ceiling: number): string {
    return buckets
      .map((b, i) => `${xFor(i, buckets.length)},${yFor(b.predicted_cost_cents, ceiling)}`)
      .join(" ");
  }

  /** Polygon points for the [lower, upper] confidence band. We walk
   *  the upper bound forward then the lower bound backward to close
   *  the shape, which is the standard SVG area-band trick. */
  function confidenceBandPath(buckets: readonly ForecastBucket[], ceiling: number): string {
    const n = buckets.length;
    const upper = buckets.map((b, i) => `${xFor(i, n)},${yFor(b.upper_bound_cents, ceiling)}`);
    const lower = buckets
      .map((b, i) => `${xFor(i, n)},${yFor(b.lower_bound_cents, ceiling)}`)
      .reverse();
    return [...upper, ...lower].join(" ");
  }
</script>

<div class="flex flex-col gap-6">
  <div class="flex items-end justify-between">
    <div>
      <h1 class="text-2xl font-bold tracking-tight">Cost forecast</h1>
      <p class="text-sm text-muted-foreground">
        Predicted spend over a configurable horizon, sourced from Cost
        Explorer's built-in forecasting model. The shaded band is the
        model's lower-to-upper confidence interval.
      </p>
    </div>
    {#if forecast}
      <div class="text-xs text-muted-foreground text-right">
        <p>Horizon: {forecast.horizon_days} days · Model: {forecast.model}</p>
        <p>{formatTimestamp(forecast.queried_at)}</p>
      </div>
    {/if}
  </div>

  <div class="flex flex-wrap items-center gap-4">
    <label class="flex flex-col gap-1 text-sm">
      <span class="text-muted-foreground">Horizon (days)</span>
      <select
        class="border rounded px-2 py-1 text-sm bg-background"
        bind:value={horizonDays}
        onchange={refresh}
      >
        {#each COST_FORECAST_HORIZONS as h (h)}
          <option value={h}>{h}</option>
        {/each}
      </select>
    </label>
  </div>

  {#if loading}
    <p class="text-muted-foreground">Loading cost forecast..</p>
  {:else if errorMessage}
    <p class="text-destructive" role="alert">{errorMessage}</p>
  {:else if forecast}
    {#if forecast.buckets.length === 0}
      <p class="text-muted-foreground">
        No forecast data returned for this horizon. Cost Explorer needs at
        least 14 days of history before it will emit a forecast; if this is
        a brand-new account, check back after the account has accumulated
        enough usage.
      </p>
    {:else}
      {@const ceiling = chartCeiling(forecast.buckets)}
      <div class="rounded border p-4 bg-card">
        <p class="text-sm text-muted-foreground mb-2">
          Total predicted spend over {forecast.horizon_days} days:
          <span class="font-semibold text-foreground">
            {formatUsdCents(totalPredictedCents(forecast.buckets))}
          </span>
        </p>
        <svg
          viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
          class="w-full h-auto"
          role="img"
          aria-label="Predicted daily spend with confidence band"
        >
          <polygon
            points={confidenceBandPath(forecast.buckets, ceiling)}
            class="fill-blue-200/60 stroke-none"
          />
          <polyline
            points={predictedPath(forecast.buckets, ceiling)}
            class="fill-none stroke-blue-600 stroke-2"
          />
        </svg>
      </div>

      <Table.Root>
        <Table.Header>
          <Table.Row>
            <Table.Head>Date</Table.Head>
            <Table.Head class="text-right tabular-nums">Predicted</Table.Head>
            <Table.Head class="text-right tabular-nums">Lower</Table.Head>
            <Table.Head class="text-right tabular-nums">Upper</Table.Head>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {#each forecast.buckets as b (b.date)}
            <Table.Row>
              <Table.Cell class="font-medium">{b.date}</Table.Cell>
              <Table.Cell class="text-right tabular-nums">
                {formatUsdCents(b.predicted_cost_cents)}
              </Table.Cell>
              <Table.Cell class="text-right tabular-nums text-muted-foreground">
                {formatUsdCents(b.lower_bound_cents)}
              </Table.Cell>
              <Table.Cell class="text-right tabular-nums text-muted-foreground">
                {formatUsdCents(b.upper_bound_cents)}
              </Table.Cell>
            </Table.Row>
          {/each}
          <Table.Row class="font-semibold">
            <Table.Cell>Total</Table.Cell>
            <Table.Cell class="text-right tabular-nums">
              {formatUsdCents(totalPredictedCents(forecast.buckets))}
            </Table.Cell>
            <Table.Cell class="text-right tabular-nums text-muted-foreground">
              {formatUsdCents(
                forecast.buckets.reduce((a, b) => a + b.lower_bound_cents, 0),
              )}
            </Table.Cell>
            <Table.Cell class="text-right tabular-nums text-muted-foreground">
              {formatUsdCents(
                forecast.buckets.reduce((a, b) => a + b.upper_bound_cents, 0),
              )}
            </Table.Cell>
          </Table.Row>
        </Table.Body>
      </Table.Root>
    {/if}
  {/if}
</div>
