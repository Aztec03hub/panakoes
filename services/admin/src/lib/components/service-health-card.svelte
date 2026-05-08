<script lang="ts">
  import { Card, CardContent, CardHeader, CardTitle } from "$lib/components/ui/card";
  import HealthBadge from "$lib/components/health-badge.svelte";
  import { formatTimestamp } from "$lib/utils";
  import type { ServiceHealth } from "$lib/types";

  export let health: ServiceHealth;
</script>

<Card class="flex flex-col" data-testid="service-card" data-service={health.service}>
  <CardHeader class="flex flex-row items-center justify-between space-y-0 pb-2">
    <CardTitle tag="h3" class="text-base font-semibold">
      {health.display_name}
    </CardTitle>
    <HealthBadge status={health.status} />
  </CardHeader>
  <CardContent class="flex flex-1 flex-col justify-between gap-3">
    <div class="text-xs text-muted-foreground">
      <div data-testid="last-check">
        Last check: {formatTimestamp(health.last_check)}
      </div>
      {#if health.message}
        <div class="mt-1 text-foreground" data-testid="health-message">
          {health.message}
        </div>
      {/if}
    </div>
    <a
      href="/dashboard/{health.service}"
      class="text-sm font-medium text-primary underline-offset-4 hover:underline"
      data-testid="details-link"
    >
      View details
    </a>
  </CardContent>
</Card>
