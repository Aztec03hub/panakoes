<script lang="ts">
  import { onMount } from "svelte";
  import ServiceHealthCard from "$lib/components/service-health-card.svelte";
  import { fetchHealth, ApiError } from "$lib/api";
  import { formatTimestamp } from "$lib/utils";
  import type { HealthSnapshot } from "$lib/types";

  let snapshot: HealthSnapshot | null = null;
  let loading = true;
  let errorMessage: string | null = null;

  onMount(async () => {
    try {
      snapshot = await fetchHealth();
    } catch (err) {
      if (err instanceof ApiError) {
        errorMessage = `Health endpoint returned HTTP ${err.status}`;
      } else {
        errorMessage = err instanceof Error ? err.message : "Unknown error";
      }
    } finally {
      loading = false;
    }
  });
</script>

<div class="flex flex-col gap-6">
  <div class="flex items-end justify-between">
    <div>
      <h1 class="text-2xl font-bold tracking-tight">Service health</h1>
      <p class="text-sm text-muted-foreground">
        Read-only Tier 1 dashboard. Mocked data; live data lands when the
        health-aggregator service ships.
      </p>
    </div>
    {#if snapshot}
      <p class="text-xs text-muted-foreground">
        Snapshot: {formatTimestamp(snapshot.generated_at)}
      </p>
    {/if}
  </div>

  {#if loading}
    <p class="text-muted-foreground">Loading health snapshot...</p>
  {:else if errorMessage}
    <p class="text-destructive" role="alert">{errorMessage}</p>
  {:else if snapshot}
    <div
      class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
      data-testid="services-grid"
    >
      {#each snapshot.services as service (service.service)}
        <ServiceHealthCard health={service} />
      {/each}
    </div>
  {/if}
</div>
