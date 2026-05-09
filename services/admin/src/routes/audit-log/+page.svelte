<script lang="ts">
  // Phase 0 placeholder. Phase 3 (Tier 3.3) wires the filterable, paginated
  // audit-log read view against admin-api's `GET /admin/audit-log` endpoint,
  // which queries the Tier3ActionIndex GSI on `panakoes-dev-audit-log`.
  // Returns `AuditLogPage` (see $lib/types).
</script>

<div class="flex flex-col gap-6">
  <div>
    <h1 class="text-2xl font-bold tracking-tight">Audit log</h1>
    <p class="text-sm text-muted-foreground">
      Filterable read view of every Tier 3 lifecycle event, queried via the
      Tier3ActionIndex GSI for sub-second paginated lookups. Wires up in
      Phase 3.
    </p>
  </div>

  <p class="text-sm text-muted-foreground">Coming in Phase 3.</p>
</div>
