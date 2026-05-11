<script lang="ts">
  import { untrack } from "svelte";
  import { ApiError, fetchAuditLog } from "$lib/api";
  import { formatTimestamp } from "$lib/utils";
  import type { AuditLogEntry, AuditLogPage } from "$lib/types";

  // Tier 3.3 audit-log read view. Filterable by `tier3_action`, paginated
  // by opaque cursor. The endpoint queries the Tier3ActionIndex GSI on
  // `panakoes-dev-audit-log`; that index is sparse so it sees only Tier 3
  // events (lifecycle operations write `tier3_action` on every row).

  let page = $state<AuditLogPage | null>(null);
  let loading = $state(true);
  let errorMessage = $state<string | null>(null);
  let filterAction = $state("");

  /** Re-issue the request from page 0 with the current filter. */
  async function load(action: string): Promise<void> {
    loading = true;
    errorMessage = null;
    try {
      page = await fetchAuditLog(action === "" ? {} : { tier3_action: action });
    } catch (err) {
      page = null;
      if (err instanceof ApiError) {
        errorMessage = `Audit-log endpoint returned HTTP ${err.status}`;
      } else {
        errorMessage = err instanceof Error ? err.message : "Unknown error";
      }
    } finally {
      loading = false;
    }
  }

  /** Load the next page using the cursor from the current response. */
  async function loadNext(): Promise<void> {
    if (page?.next_cursor == null) return;
    loading = true;
    errorMessage = null;
    try {
      const filters: { tier3_action?: string; cursor: string } = {
        cursor: page.next_cursor,
      };
      if (filterAction !== "") filters.tier3_action = filterAction;
      page = await fetchAuditLog(filters);
    } catch (err) {
      if (err instanceof ApiError) {
        errorMessage = `Audit-log endpoint returned HTTP ${err.status}`;
      } else {
        errorMessage = err instanceof Error ? err.message : "Unknown error";
      }
    } finally {
      loading = false;
    }
  }

  function applyFilter(event: Event): void {
    event.preventDefault();
    void load(filterAction.trim());
  }

  function clearFilter(): void {
    filterAction = "";
    void load("");
  }

  function payloadSummary(entry: AuditLogEntry): string {
    const keys = Object.keys(entry.payload);
    if (keys.length === 0) return "(empty)";
    return keys.map((k) => `${k}=${JSON.stringify(entry.payload[k])}`).join(", ");
  }

  $effect(() => {
    untrack(() => {
      void load("");
    });
  });
</script>

<div class="flex flex-col gap-6">
  <div>
    <h1 class="text-2xl font-bold tracking-tight">Audit log</h1>
    <p class="text-sm text-muted-foreground">
      Filterable read view of every Tier 3 lifecycle event. Backed by the
      Tier3ActionIndex GSI on the audit-log table; the index is sparse so
      only operator-driven Tier 3 actions appear here.
    </p>
  </div>

  <form class="flex items-end gap-2" onsubmit={applyFilter}>
    <label class="flex flex-col gap-1 text-sm">
      <span class="font-medium">Filter by tier3_action</span>
      <input
        type="text"
        bind:value={filterAction}
        placeholder="e.g. terminate-session"
        class="h-9 w-72 rounded-md border border-input bg-background px-3 text-sm"
        data-testid="audit-filter-input"
      />
    </label>
    <button
      type="submit"
      class="h-9 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
      data-testid="audit-filter-submit"
    >
      Apply
    </button>
    {#if filterAction !== ""}
      <button
        type="button"
        class="h-9 rounded-md border border-input px-4 text-sm"
        onclick={clearFilter}
      >
        Clear
      </button>
    {/if}
  </form>

  {#if loading}
    <p class="text-muted-foreground">Loading audit log...</p>
  {:else if errorMessage}
    <p class="text-destructive" role="alert">{errorMessage}</p>
  {:else if page === null || page.entries.length === 0}
    <p class="text-muted-foreground" data-testid="audit-empty">
      No Tier 3 audit events match the current filter.
    </p>
  {:else}
    <div class="overflow-x-auto rounded-md border">
      <table class="w-full text-sm" data-testid="audit-table">
        <thead class="bg-muted text-left text-xs uppercase tracking-wide">
          <tr>
            <th class="px-3 py-2">Timestamp</th>
            <th class="px-3 py-2">Action</th>
            <th class="px-3 py-2">Tier 3 action</th>
            <th class="px-3 py-2">Actor</th>
            <th class="px-3 py-2">Service</th>
            <th class="px-3 py-2">Request id</th>
            <th class="px-3 py-2">Payload</th>
          </tr>
        </thead>
        <tbody>
          {#each page.entries as entry (entry.request_id + entry.action)}
            <tr class="border-t">
              <td class="whitespace-nowrap px-3 py-2 font-mono text-xs">
                {formatTimestamp(entry.timestamp)}
              </td>
              <td class="px-3 py-2">{entry.action}</td>
              <td class="px-3 py-2">{entry.tier3_action ?? ""}</td>
              <td class="px-3 py-2">{entry.actor_id}</td>
              <td class="px-3 py-2">{entry.source_service}</td>
              <td class="px-3 py-2 font-mono text-xs">{entry.request_id}</td>
              <td class="px-3 py-2 text-xs text-muted-foreground">
                {payloadSummary(entry)}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <div class="flex items-center justify-between text-xs text-muted-foreground">
      <span>
        {page.entries.length} entries; snapshot {formatTimestamp(page.generated_at)}
      </span>
      {#if page.next_cursor !== null}
        <button
          type="button"
          class="h-8 rounded-md border border-input px-3 text-xs"
          onclick={loadNext}
          data-testid="audit-load-next"
        >
          Load more
        </button>
      {/if}
    </div>
  {/if}
</div>
