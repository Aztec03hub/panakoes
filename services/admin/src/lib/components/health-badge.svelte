<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import type { HealthStatus } from "$lib/types";

  export let status: HealthStatus;

  /**
   * Map health status to a shadcn-svelte Badge variant.
   * green=healthy => `success`
   * red=unhealthy => `destructive`
   * gray=unknown => `secondary`
   *
   * Status enum mirrors the panakoes-models Python enum so the frontend stays
   * single-source-of-truth aligned with the backend health-aggregator.
   */
  function variantFor(s: HealthStatus): "success" | "destructive" | "secondary" {
    switch (s) {
      case "healthy":
        return "success";
      case "unhealthy":
        return "destructive";
      default:
        return "secondary";
    }
  }

  function labelFor(s: HealthStatus): string {
    switch (s) {
      case "healthy":
        return "Healthy";
      case "unhealthy":
        return "Unhealthy";
      default:
        return "Unknown";
    }
  }
</script>

<Badge variant={variantFor(status)} data-testid="health-badge" data-status={status}>
  {labelFor(status)}
</Badge>
