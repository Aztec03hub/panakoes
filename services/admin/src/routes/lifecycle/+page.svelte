<script lang="ts">
  import {
    ApiError,
    blockTenant,
    blockUserSessions,
    forceBillingRecompute,
    forceFailIngestion,
    killBatchJob,
    killStreamingSession,
    revokeApiKey,
    terminateSession,
  } from "$lib/api";
  import type {
    BlockTenantResult,
    BlockUserSessionsResult,
    ForceBillingRecomputeResult,
    ForceFailIngestionResult,
    KillBatchJobResult,
    KillStreamingSessionResult,
    LifecycleOperationKind,
    LifecycleResponse,
    RevokeApiKeyResult,
    TerminateSessionResult,
  } from "$lib/types";
  import { generateIdempotencyKey } from "$lib/uuid";
  import { formatTimestamp } from "$lib/utils";

  // Tier 3 lifecycle dashboard. Operator picks an operation, fills in the
  // target id + a free-form reason, types the exact confirmation string,
  // and submits. The page mints the idempotency key fresh on every submit
  // (replays are at most one effect server-side per ADR-032 layer 2; a
  // fresh key is what distinguishes "the operator wants to retry deliberately"
  // from "a stuck client is replaying"). The submit button is disabled until
  // every gate is satisfied client-side; admin-api re-validates regardless.

  type AnyResult =
    | TerminateSessionResult
    | ForceFailIngestionResult
    | BlockUserSessionsResult
    | BlockTenantResult
    | RevokeApiKeyResult
    | KillStreamingSessionResult
    | KillBatchJobResult
    | ForceBillingRecomputeResult;

  let operation: LifecycleOperationKind = "terminate-session";
  let targetId = "";
  let reason = "";
  let confirmation = "";
  let submitting = false;
  let response: LifecycleResponse<AnyResult> | null = null;
  let errorMessage: string | null = null;

  /** Build the confirmation string the operator must type to enable submit.
   *  Mirrors the template admin-api compares against on the server. */
  function expectedConfirmation(op: LifecycleOperationKind, id: string): string {
    if (id === "") return "";
    switch (op) {
      case "terminate-session":
        return `TERMINATE ${id}`;
      case "force-fail-ingestion":
        return `FAIL ${id}`;
      case "block-user-sessions":
        return `BLOCK USER ${id}`;
      case "block-tenant":
        return `BLOCK TENANT ${id}`;
      case "revoke-api-key":
        return `REVOKE KEY ${id}`;
      case "kill-streaming-session":
        return `KILL STREAM ${id}`;
      case "kill-batch-job":
        return `KILL JOB ${id}`;
      case "force-billing-recompute":
        return `RECOMPUTE BILLING ${id}`;
    }
  }

  /** UI label for the current operation's id field. */
  function idFieldLabel(op: LifecycleOperationKind): string {
    switch (op) {
      case "terminate-session":
        return "Session id";
      case "force-fail-ingestion":
        return "Ingestion id";
      case "block-user-sessions":
        return "User id";
      case "block-tenant":
        return "Tenant id";
      case "revoke-api-key":
        return "API key id";
      case "kill-streaming-session":
        return "Streaming session id";
      case "kill-batch-job":
        return "Batch job id";
      case "force-billing-recompute":
        return "Tenant id";
    }
  }

  function idFieldPlaceholder(op: LifecycleOperationKind): string {
    switch (op) {
      case "terminate-session":
        return "e.g. sess_01H...";
      case "force-fail-ingestion":
        return "e.g. ing_01H...";
      case "block-user-sessions":
        return "e.g. user_01H...";
      case "block-tenant":
        return "e.g. tenant_01H...";
      case "revoke-api-key":
        return "e.g. key_01H...";
      case "kill-streaming-session":
        return "e.g. sess_01H...";
      case "kill-batch-job":
        return "e.g. job_01H...";
      case "force-billing-recompute":
        return "e.g. tenant_01H...";
    }
  }

  function operationLabel(op: LifecycleOperationKind): string {
    switch (op) {
      case "terminate-session":
        return "Terminate session";
      case "force-fail-ingestion":
        return "Force fail ingestion";
      case "block-user-sessions":
        return "Block user sessions";
      case "block-tenant":
        return "Block tenant";
      case "revoke-api-key":
        return "Revoke API key";
      case "kill-streaming-session":
        return "Kill streaming session";
      case "kill-batch-job":
        return "Kill Batch job";
      case "force-billing-recompute":
        return "Force billing recompute";
    }
  }

  $: trimmedId = targetId.trim();
  $: trimmedReason = reason.trim();
  $: expected = expectedConfirmation(operation, trimmedId);
  $: confirmationMatches = expected !== "" && confirmation === expected;
  $: canSubmit =
    !submitting && trimmedId !== "" && trimmedReason !== "" && confirmationMatches;

  /** Reset form state when the operator switches operations so a stale
   *  confirmation string can't accidentally satisfy a different op. */
  function onOperationChange(): void {
    confirmation = "";
  }

  function resetAll(): void {
    targetId = "";
    reason = "";
    confirmation = "";
    response = null;
    errorMessage = null;
  }

  async function handleSubmit(event: Event): Promise<void> {
    event.preventDefault();
    if (!canSubmit) return;

    submitting = true;
    response = null;
    errorMessage = null;

    const idempotencyKey = generateIdempotencyKey();

    try {
      const baseRequest = {
        idempotency_key: idempotencyKey,
        confirmation: expected,
        params: { reason: trimmedReason },
      } as const;
      if (operation === "terminate-session") {
        response = await terminateSession(trimmedId, {
          idempotency_key: idempotencyKey,
          confirmation: expected,
          params: { session_id: trimmedId, reason: trimmedReason },
        });
      } else if (operation === "force-fail-ingestion") {
        response = await forceFailIngestion(trimmedId, baseRequest);
      } else if (operation === "block-user-sessions") {
        response = await blockUserSessions(trimmedId, baseRequest);
      } else if (operation === "block-tenant") {
        response = await blockTenant(trimmedId, baseRequest);
      } else if (operation === "revoke-api-key") {
        response = await revokeApiKey(trimmedId, baseRequest);
      } else if (operation === "kill-streaming-session") {
        response = await killStreamingSession(trimmedId, baseRequest);
      } else if (operation === "kill-batch-job") {
        response = await killBatchJob(trimmedId, baseRequest);
      } else {
        response = await forceBillingRecompute(trimmedId, baseRequest);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        errorMessage = `Lifecycle endpoint returned HTTP ${err.status}`;
      } else {
        errorMessage = err instanceof Error ? err.message : "Unknown error";
      }
    } finally {
      submitting = false;
    }
  }

  function statusClass(status: string): string {
    if (status === "succeeded") return "text-green-600 font-semibold";
    if (status === "failed") return "text-destructive font-semibold";
    if (status === "cancelled") return "text-muted-foreground font-semibold";
    return "text-foreground font-semibold";
  }
</script>

<div class="flex flex-col gap-6">
  <div>
    <h1 class="text-2xl font-bold tracking-tight">Lifecycle controls</h1>
    <p class="text-sm text-muted-foreground">
      Tier 3 of the admin dashboard. Every dangerous operation routes through
      the typed-confirmation, idempotency-by-key, step-up MFA, and
      audit-before-AND-after safety pattern (ADR-032). Read the runbook before
      you touch anything here.
    </p>
  </div>

  <form class="flex flex-col gap-4 rounded-md border bg-muted/30 p-4" onsubmit={handleSubmit}>
    <fieldset class="flex flex-col gap-2">
      <legend class="text-sm font-semibold">Operation</legend>
      <div class="flex flex-wrap gap-2" role="radiogroup" aria-label="Lifecycle operation">
        {#each ["terminate-session", "force-fail-ingestion", "block-user-sessions", "block-tenant", "revoke-api-key", "kill-streaming-session", "kill-batch-job", "force-billing-recompute"] as LifecycleOperationKind[] as opKind (opKind)}
          <label
            class="flex items-center gap-2 rounded-md border bg-background px-3 py-2 text-sm"
            class:border-primary={operation === opKind}
          >
            <input
              type="radio"
              name="operation"
              value={opKind}
              bind:group={operation}
              onchange={onOperationChange}
              data-testid={`op-${opKind}`}
            />
            <span>{operationLabel(opKind)}</span>
          </label>
        {/each}
      </div>
    </fieldset>

    <label class="flex flex-col gap-1 text-sm">
      <span class="font-medium">{idFieldLabel(operation)}</span>
      <input
        type="text"
        bind:value={targetId}
        placeholder={idFieldPlaceholder(operation)}
        class="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
        data-testid="lifecycle-target-id"
        autocomplete="off"
      />
    </label>

    <label class="flex flex-col gap-1 text-sm">
      <span class="font-medium">Reason</span>
      <textarea
        bind:value={reason}
        rows="3"
        placeholder="Why are you running this operation? Captured in the audit log."
        class="rounded-md border border-input bg-background px-3 py-2 text-sm"
        data-testid="lifecycle-reason"
      ></textarea>
    </label>

    <div class="flex flex-col gap-1 text-sm">
      <span class="font-medium">Confirmation</span>
      <p class="text-xs text-muted-foreground">
        {#if trimmedId === ""}
          Enter the target id above to reveal the confirmation string.
        {:else}
          Type this exactly to enable submit:
          <code class="rounded bg-background px-1 py-0.5 font-mono text-xs">{expected}</code>
        {/if}
      </p>
      <input
        type="text"
        bind:value={confirmation}
        placeholder={expected === "" ? "" : `Type: ${expected}`}
        class="h-9 w-full rounded-md border border-input bg-background px-3 font-mono text-sm"
        class:border-destructive={confirmation !== "" && !confirmationMatches}
        data-testid="lifecycle-confirmation"
        autocomplete="off"
        disabled={trimmedId === ""}
      />
      {#if confirmation !== "" && !confirmationMatches}
        <p class="text-xs text-destructive">Does not match the expected confirmation string.</p>
      {/if}
    </div>

    <div class="flex flex-wrap gap-2">
      <button
        type="submit"
        class="h-9 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
        disabled={!canSubmit}
        data-testid="lifecycle-submit"
      >
        {submitting ? "Running..." : "Run operation"}
      </button>
      <button
        type="button"
        class="h-9 rounded-md border border-input px-4 text-sm"
        onclick={resetAll}
        disabled={submitting}
        data-testid="lifecycle-reset"
      >
        Reset
      </button>
    </div>
  </form>

  {#if errorMessage}
    <p class="text-destructive" role="alert" data-testid="lifecycle-error">{errorMessage}</p>
  {/if}

  {#if response}
    <div class="rounded-md border bg-muted/30 p-4" data-testid="lifecycle-response">
      <h2 class="text-lg font-semibold">Response</h2>
      <dl class="mt-2 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-muted-foreground">Status</dt>
          <dd class={statusClass(response.status)}>{response.status}</dd>
        </div>
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Idempotency key
          </dt>
          <dd class="font-mono text-xs break-all">{response.idempotency_key}</dd>
        </div>
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Audit request id
          </dt>
          <dd class="font-mono text-xs break-all">{response.audit_request_id}</dd>
        </div>
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Started at
          </dt>
          <dd>{formatTimestamp(response.started_at)}</dd>
        </div>
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Finished at
          </dt>
          <dd>{response.finished_at ? formatTimestamp(response.finished_at) : "(in flight)"}</dd>
        </div>
      </dl>

      {#if response.error_message}
        <div class="mt-4">
          <p class="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Error message
          </p>
          <p class="text-sm text-destructive">{response.error_message}</p>
        </div>
      {/if}

      {#if response.result}
        <div class="mt-4">
          <p class="text-xs font-medium uppercase tracking-wide text-muted-foreground">Result</p>
          <pre
            class="mt-1 overflow-x-auto rounded-md border bg-background p-3 text-xs"
            data-testid="lifecycle-result-json">{JSON.stringify(response.result, null, 2)}</pre>
        </div>
      {/if}
    </div>
  {/if}
</div>
