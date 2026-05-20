<script lang="ts">
  import { page } from "$app/state";
  import { onDestroy } from "svelte";
  import Loader2 from "@lucide/svelte/icons/loader-2";
  import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
  } from "$lib/components/ui/card";
  import { ApiError, fetchIngestion, fetchSummary } from "$lib/api";
  import type { IngestionRecord, SummaryRecord } from "$lib/types";

  /**
   * Page that polls the ingestion + summary endpoints until both
   * finish, then renders the transcript and the AI-generated summary.
   *
   * Polling cadence:
   *   - first 30s: every 2s
   *   - next 60s: every 5s
   *   - after that: every 10s
   * Cap polling at 15 minutes so a stuck transcription does not hammer
   * the API forever; users can refresh manually after that.
   */

  const POLL_CAP_MS = 15 * 60 * 1000;
  const POLL_START = Date.now();

  let ingestion = $state<IngestionRecord | null>(null);
  let summary = $state<SummaryRecord | null>(null);
  let errorMessage = $state<string | null>(null);
  let pollTimeout = $state<ReturnType<typeof setTimeout> | null>(null);

  function nextDelayMs(): number {
    const elapsed = Date.now() - POLL_START;
    if (elapsed < 30_000) return 2_000;
    if (elapsed < 90_000) return 5_000;
    return 10_000;
  }

  function isTerminal(record: IngestionRecord | null): boolean {
    if (record === null) return false;
    if (record.transcript_status === "failed") return true;
    if (record.transcript_status === "succeeded") return true;
    if (record.status === "failed") return true;
    return false;
  }

  async function poll() {
    const id = page.params.id;
    if (!id) return;
    try {
      ingestion = await fetchIngestion(id);
      if (ingestion.transcript_status === "succeeded") {
        try {
          summary = await fetchSummary(ingestion.ingestion_id);
        } catch (err) {
          if (err instanceof ApiError && err.status === 404) {
            // Summary not yet generated; keep polling.
            summary = null;
          } else {
            throw err;
          }
        }
      }
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 404) {
          errorMessage = "Ingestion not found yet (or not yours).";
        } else {
          errorMessage = `Fetch failed: HTTP ${err.status}`;
        }
      } else {
        errorMessage = err instanceof Error ? err.message : "Unknown error";
      }
    }

    const havePolledLongEnough = Date.now() - POLL_START > POLL_CAP_MS;
    const shouldStop =
      havePolledLongEnough ||
      (isTerminal(ingestion) && summary !== null) ||
      ingestion?.transcript_status === "failed";

    if (!shouldStop) {
      pollTimeout = setTimeout(poll, nextDelayMs());
    }
  }

  $effect(() => {
    poll();
  });

  onDestroy(() => {
    if (pollTimeout !== null) clearTimeout(pollTimeout);
  });

  function statusBadge(): { label: string; cls: string } {
    if (ingestion === null) return { label: "loading", cls: "bg-muted text-muted-foreground" };
    if (ingestion.status === "failed") return { label: "upload failed", cls: "bg-destructive/15 text-destructive" };
    if (ingestion.transcript_status === "failed")
      return { label: "transcription failed", cls: "bg-destructive/15 text-destructive" };
    if (ingestion.transcript_status === "succeeded" && summary !== null)
      return { label: "complete", cls: "bg-green-500/15 text-green-700 dark:text-green-400" };
    if (ingestion.transcript_status === "succeeded")
      return { label: "transcribed, summarizing", cls: "bg-amber-500/15 text-amber-700 dark:text-amber-400" };
    if (ingestion.status === "uploaded") return { label: "transcribing", cls: "bg-blue-500/15 text-blue-700 dark:text-blue-400" };
    return { label: "uploaded", cls: "bg-muted text-muted-foreground" };
  }
</script>

<svelte:head>
  <title>Ingestion · Panakoes</title>
</svelte:head>

<div class="flex flex-col gap-6">
  <div class="flex items-end justify-between">
    <div>
      <h1 class="text-2xl font-bold tracking-tight">Ingestion</h1>
      <p class="text-sm text-muted-foreground">
        {ingestion?.filename ?? page.params.id}
      </p>
    </div>
    <span class="inline-flex items-center rounded-full px-3 py-1 text-xs font-medium {statusBadge().cls}">
      {#if ingestion === null || !isTerminal(ingestion) || summary === null}
        <Loader2 class="mr-1 h-3 w-3 animate-spin" />
      {/if}
      {statusBadge().label}
    </span>
  </div>

  {#if errorMessage}
    <div class="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
      {errorMessage}
    </div>
  {/if}

  {#if ingestion}
    <Card>
      <CardHeader>
        <CardTitle>Upload</CardTitle>
        <CardDescription>
          Created {ingestion.created_at} · last updated {ingestion.updated_at}
        </CardDescription>
      </CardHeader>
      <CardContent class="text-sm">
        <dl class="grid grid-cols-2 gap-2">
          <dt class="text-muted-foreground">Ingestion ID</dt>
          <dd class="font-mono break-all">{ingestion.ingestion_id}</dd>
          <dt class="text-muted-foreground">Content type</dt>
          <dd>{ingestion.content_type}</dd>
          <dt class="text-muted-foreground">Size</dt>
          <dd>{ingestion.size_bytes.toLocaleString()} bytes</dd>
          <dt class="text-muted-foreground">Upload status</dt>
          <dd>{ingestion.status}</dd>
          <dt class="text-muted-foreground">Transcript status</dt>
          <dd>{ingestion.transcript_status ?? "pending"}</dd>
        </dl>
      </CardContent>
    </Card>

    {#if ingestion.transcript_status === "failed"}
      <Card>
        <CardHeader>
          <CardTitle>Transcription failed</CardTitle>
          <CardDescription>{ingestion.transcript_error_message ?? "no error message"}</CardDescription>
        </CardHeader>
      </Card>
    {/if}

    {#if ingestion.transcript}
      <Card>
        <CardHeader>
          <CardTitle>Transcript</CardTitle>
          <CardDescription>
            {ingestion.transcript.language ?? "unknown language"} ·
            {ingestion.transcript.duration_seconds?.toFixed(1) ?? "?"}s ·
            {ingestion.transcript.segments.length} segment(s)
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p class="whitespace-pre-wrap text-sm">{ingestion.transcript.text}</p>
        </CardContent>
      </Card>
    {/if}
  {/if}

  {#if summary}
    <Card>
      <CardHeader>
        <CardTitle>AI summary</CardTitle>
        <CardDescription>
          {summary.model} · {summary.created_at}
        </CardDescription>
      </CardHeader>
      <CardContent class="text-sm">
        <p class="whitespace-pre-wrap">{summary.summary_text}</p>
        {#if summary.action_items.length > 0}
          <h3 class="mt-4 font-medium">Action items</h3>
          <ul class="mt-2 list-disc pl-5">
            {#each summary.action_items as item}
              <li>{item}</li>
            {/each}
          </ul>
        {/if}
      </CardContent>
    </Card>
  {/if}
</div>
