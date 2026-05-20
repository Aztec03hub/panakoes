<script lang="ts">
  import Loader2 from "@lucide/svelte/icons/loader-2";
  import Mic from "@lucide/svelte/icons/mic";
  import Square from "@lucide/svelte/icons/square";
  import Copy from "@lucide/svelte/icons/copy";
  import { onDestroy } from "svelte";
  import { Button } from "$lib/components/ui/button";
  import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
  } from "$lib/components/ui/card";
  import {
    RealtimeSession,
    type SessionChunk,
  } from "$lib/realtime-session";

  /**
   * Chunked-batch pseudo-realtime transcription.
   *
   * The browser captures audio continuously, rotates the MediaRecorder
   * every 8 seconds, and fires each chunk through the existing async
   * Whisper-on-Batch ingestion path. The page polls each chunk's
   * ingestion record until the transcript materializes, then renders
   * the chunks in chronological order and an updated combined-transcript
   * card above them.
   *
   * This shape reuses the entire async backend (ingestion-api -> S3 ->
   * SQS -> transcribe-worker -> AWS Batch GPU -> DDB) without a single
   * backend change. The latency tradeoff is documented in the help copy
   * at the bottom of the card. True streaming is a future workstream
   * (FOLLOWUPS.md).
   */

  let session: RealtimeSession | null = null;
  let isActive = $state(false);
  let chunks = $state<SessionChunk[]>([]);
  let sessionElapsedSec = $state(0);
  let errorMessage = $state("");
  let elapsedTimer: ReturnType<typeof setInterval> | null = null;
  let startedAtMs = 0;

  const combinedTranscript = $derived(
    chunks
      .filter((c) => c.status === "complete" && c.transcript !== null && c.transcript !== "")
      .map((c) => c.transcript)
      .join(" "),
  );

  function onChunkUpdate(chunk: SessionChunk): void {
    // Replace by index so Svelte reactivity sees a fresh array. We mirror
    // the session's read-only list into our own reactive array.
    const idx = chunks.findIndex((c) => c.index === chunk.index);
    if (idx === -1) {
      chunks = [...chunks, { ...chunk }];
    } else {
      const next = chunks.slice();
      next[idx] = { ...chunk };
      chunks = next;
    }
  }

  function onSessionError(err: Error): void {
    errorMessage = err.message;
  }

  function fmtElapsed(totalSec: number): string {
    const mm = Math.floor(totalSec / 60);
    const ss = Math.floor(totalSec % 60);
    return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  }

  function fmtSize(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  }

  function statusLabel(status: SessionChunk["status"]): string {
    switch (status) {
      case "recording":
        return "Recording";
      case "uploading":
        return "Uploading";
      case "transcribing":
        return "Transcribing";
      case "complete":
        return "Complete";
      case "failed":
        return "Failed";
    }
  }

  function statusBadgeClass(status: SessionChunk["status"]): string {
    switch (status) {
      case "recording":
        return "bg-muted text-muted-foreground";
      case "uploading":
        return "bg-blue-100 text-blue-900 dark:bg-blue-900 dark:text-blue-100";
      case "transcribing":
        return "bg-amber-100 text-amber-900 dark:bg-amber-900 dark:text-amber-100";
      case "complete":
        return "bg-emerald-100 text-emerald-900 dark:bg-emerald-900 dark:text-emerald-100";
      case "failed":
        return "bg-destructive/15 text-destructive";
    }
  }

  async function startSession(): Promise<void> {
    if (isActive || session !== null) return;
    errorMessage = "";
    chunks = [];
    session = new RealtimeSession({
      chunkSeconds: 8,
      pollIntervalMs: 2000,
      onChunkUpdate,
      onError: onSessionError,
    });
    await session.start();
    if (!session.isActive) {
      // start() resolved without becoming active (permission denied or
      // similar). The onError callback already captured the message.
      session = null;
      return;
    }
    isActive = true;
    startedAtMs = Date.now();
    sessionElapsedSec = 0;
    elapsedTimer = setInterval(() => {
      sessionElapsedSec = Math.floor((Date.now() - startedAtMs) / 1000);
    }, 1000);
  }

  async function stopSession(): Promise<void> {
    if (session === null) return;
    await session.stop();
    isActive = false;
    if (elapsedTimer !== null) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
    session = null;
  }

  function toggle(): void {
    if (isActive) {
      void stopSession();
    } else {
      void startSession();
    }
  }

  async function copyCombined(): Promise<void> {
    if (combinedTranscript === "") return;
    try {
      await navigator.clipboard.writeText(combinedTranscript);
    } catch (err) {
      errorMessage = err instanceof Error ? err.message : "copy failed";
    }
  }

  onDestroy(() => {
    if (session !== null) {
      void session.stop();
    }
    if (elapsedTimer !== null) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
  });
</script>

<svelte:head>
  <title>Realtime · Panakoes</title>
</svelte:head>

<div class="flex flex-col items-center gap-6 py-8">
  <Card class="w-full max-w-2xl">
    <CardHeader>
      <CardTitle>Realtime transcription (chunked batch)</CardTitle>
      <CardDescription>
        Pseudo-realtime via 8-second batched chunks. Each chunk routes through
        the same async pipeline as <code>/upload</code>: ingestion-api issues a
        pre-signed PUT, S3 receives the bytes, transcribe-worker dispatches a
        Whisper-large-v3 job on AWS Batch g4dn.xlarge Spot, and query-api
        surfaces the transcript when it lands.
      </CardDescription>
    </CardHeader>
    <CardContent>
      <div class="flex flex-col items-center gap-4">
        <button
          type="button"
          onclick={toggle}
          aria-label={isActive ? "Stop session" : "Start session"}
          class="relative flex h-32 w-32 items-center justify-center rounded-full border-4 transition-colors {isActive
            ? 'border-destructive bg-destructive/10'
            : 'border-primary bg-primary/5 hover:bg-primary/10'}"
        >
          {#if isActive}
            <span class="absolute inset-0 animate-ping rounded-full border-4 border-destructive opacity-30"></span>
            <Square class="h-12 w-12 text-destructive" />
          {:else}
            <Mic class="h-12 w-12 text-primary" />
          {/if}
        </button>

        <div class="flex flex-col items-center gap-1">
          <span class="font-mono text-2xl tabular-nums">{fmtElapsed(sessionElapsedSec)}</span>
          <span class="text-xs text-muted-foreground">
            {isActive ? "Click to stop" : chunks.length > 0 ? "Session stopped" : "Click to start"}
          </span>
        </div>

        {#if errorMessage}
          <p class="text-sm text-destructive">{errorMessage}</p>
        {/if}
      </div>
    </CardContent>
  </Card>

  {#if chunks.length > 0}
    <Card class="w-full max-w-2xl">
      <CardHeader>
        <CardTitle>Combined transcript</CardTitle>
        <CardDescription>
          Concatenation of every completed chunk, in chronological order. Updates
          live as each chunk's transcript lands.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {#if combinedTranscript === ""}
          <p class="text-sm text-muted-foreground">No completed chunks yet.</p>
        {:else}
          <p class="whitespace-pre-wrap text-sm leading-relaxed">{combinedTranscript}</p>
        {/if}
      </CardContent>
    </Card>
  {/if}

  {#if chunks.length > 0}
    <div class="w-full max-w-2xl space-y-3">
      {#each chunks as chunk (chunk.index)}
        <Card>
          <CardHeader class="flex flex-row items-start justify-between gap-2 space-y-0 pb-2">
            <div class="flex flex-col gap-1">
              <CardTitle class="text-base">Chunk {chunk.index + 1}</CardTitle>
              <CardDescription class="text-xs">
                {chunk.durationSeconds.toFixed(1)}s · {fmtSize(chunk.sizeBytes)}
              </CardDescription>
            </div>
            <span
              class="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium {statusBadgeClass(
                chunk.status,
              )}"
            >
              {#if chunk.status === "uploading" || chunk.status === "transcribing"}
                <Loader2 class="h-3 w-3 animate-spin" />
              {/if}
              {statusLabel(chunk.status)}
            </span>
          </CardHeader>
          <CardContent class="pt-2 text-sm">
            {#if chunk.status === "complete" && chunk.transcript}
              <p class="whitespace-pre-wrap leading-relaxed">{chunk.transcript}</p>
            {:else if chunk.status === "failed"}
              <p class="text-destructive">{chunk.errorMessage ?? "transcription failed"}</p>
            {:else if chunk.status === "transcribing"}
              <p class="text-muted-foreground">Waiting on Whisper on AWS Batch...</p>
            {:else if chunk.status === "uploading"}
              <p class="text-muted-foreground">Uploading to S3...</p>
            {:else}
              <p class="text-muted-foreground">Captured, awaiting upload.</p>
            {/if}
          </CardContent>
        </Card>
      {/each}
    </div>
  {/if}

  {#if chunks.length > 0 && !isActive}
    <div class="w-full max-w-2xl">
      <Button variant="outline" onclick={copyCombined} disabled={combinedTranscript === ""}>
        <Copy class="mr-2 h-4 w-4" />
        Copy combined transcript
      </Button>
    </div>
  {/if}

  <Card class="w-full max-w-2xl">
    <CardContent class="pt-6">
      <p class="text-xs text-muted-foreground">
        Pseudo-realtime via 8-second batched chunks. Each chunk is independently
        transcribed by Whisper-large-v3 on a g4dn.xlarge Spot GPU. Expected
        delay: 5 to 15 seconds per chunk once the GPU is warm. The first chunk
        after idle adds 3 to 5 minutes for cold-start. There is a sub-100ms
        audio gap between consecutive chunks (the recorder-rotation tradeoff).
        <strong>NEXT:</strong> true streaming via per-session GPU plus WebSocket.
        See FOLLOWUPS.md.
      </p>
    </CardContent>
  </Card>
</div>
