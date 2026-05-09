<script lang="ts">
  // Phase 0 placeholder. Phase 1 (Tier 3.1) wires the safety pattern
  // (typed-confirmation + idempotency-by-key + step-up MFA) and the first
  // three lifecycle operations (terminate session, revoke API credential,
  // force password reset) against admin-api. Phase 2 (Tier 3.2) extends
  // with five additional ops on the proven pattern. The request/response
  // envelopes are declared in $lib/types as `LifecycleRequest<P>` /
  // `LifecycleResponse<R>`.
</script>

<div class="flex flex-col gap-6">
  <div>
    <h1 class="text-2xl font-bold tracking-tight">Lifecycle controls</h1>
    <p class="text-sm text-muted-foreground">
      Tier 3 of the admin dashboard. Every dangerous operation routes through
      the typed-confirmation + step-up MFA + audit-before-AND-after safety
      pattern backed by `panakoes-dev-lifecycle-state`. Wires up in Phase 1.
    </p>
  </div>

  <p class="text-sm text-muted-foreground">Coming in Phase 1.</p>
</div>
