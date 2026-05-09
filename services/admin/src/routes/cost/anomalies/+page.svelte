<script lang="ts">
  // Phase 0 placeholder. Phase 2 wires fetchCostAnomalies() against the
  // cost-api `/cost/anomalies` endpoint, populated by the anomaly detector
  // that reads alert-state for dedup. Returns `CostAnomalyList` (see
  // $lib/types).
</script>

<div class="flex flex-col gap-6">
  <div>
    <h1 class="text-2xl font-bold tracking-tight">Cost anomalies</h1>
    <p class="text-sm text-muted-foreground">
      Cost-anomaly alerts the detector emitted, deduplicated against
      alert-state's quiet-period window. Wires up in Phase 2.
    </p>
  </div>

  <p class="text-sm text-muted-foreground">Coming in Phase 2.</p>
</div>
