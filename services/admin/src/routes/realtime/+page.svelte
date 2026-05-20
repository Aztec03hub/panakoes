<script lang="ts">
  import Loader2 from "@lucide/svelte/icons/loader-2";
  import Mic from "@lucide/svelte/icons/mic";
  import Square from "@lucide/svelte/icons/square";
  import Copy from "@lucide/svelte/icons/copy";
  import { Button } from "$lib/components/ui/button";
  import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
  } from "$lib/components/ui/card";
  import {
    StreamingSessionImpl,
    type StreamStatus,
  } from "$lib/streaming-session";

  /**
   * True-streaming realtime transcription via per-session GPU + WebSocket.
   *
   * Replaces the chunked-batch implementation per design v7
   * (`docs/design/realtime-streaming-transcription.md`). The browser captures
   * 200 ms mono 16 kHz PCM frames via an AudioWorklet, ships them through
   * an API Gateway WebSocket to a session-spawned `g4dn.xlarge` Spot GPU
   * running faster-whisper-large + Silero VAD, and renders sentence-final
   * segments as they emit (with a running unconfirmed partial above).
   *
   * Status state machine (StreamStatus):
   *   idle -> connecting -> spawning-gpu -> catching-up -> ready ->
   *   transcribing -> ended | failed
   *
   * During `spawning-gpu` (cold-start of the GPU instance + container,
   * 102 to 192 s typical) the SPA queues PCM frames locally and pings the
   * WS every 60 s to keep the API GW idle timer fresh. On `ready` the queue
   * drains at 10 Hz (the burst-flush rate-limit per DEG-03) and live
   * partials begin to appear; once drained the page settles into the live
   * 5 Hz capture cadence.
   */

  let session: StreamingSessionImpl | null = null;
  let status = $state<StreamStatus>("idle");
  let partialText = $state("");
  let finalSegments = $state<string[]>([]);
  let sessionElapsedSec = $state(0);
  let errorMessage = $state("");
  let elapsedTimer: ReturnType<typeof setInterval> | null = null;
  let startedAtMs = 0;

  const transcript = $derived(finalSegments.join("\n\n"));
  const isActive = $derived(
    status === "connecting" ||
      status === "spawning-gpu" ||
      status === "catching-up" ||
      status === "ready" ||
      status === "transcribing",
  );

  function statusLabel(s: StreamStatus): string {
    switch (s) {
      case "idle":
        return "Idle";
      case "connecting":
        return "Connecting...";
      case "spawning-gpu":
        return "Spawning GPU (this can take 2 to 3 minutes on cold-start)";
      case "catching-up":
        return "Transcription catching up...";
      case "ready":
        return "Ready";
      case "transcribing":
        return "Transcribing";
      case "ended":
        return "Session ended";
      case "failed":
        return "Failed";
    }
  }

  function statusBadgeClass(s: StreamStatus): string {
    switch (s) {
      case "idle":
        return "bg-muted text-muted-foreground";
      case "connecting":
      case "spawning-gpu":
        return "bg-blue-100 text-blue-900 dark:bg-blue-900 dark:text-blue-100";
      case "catching-up":
        return "bg-amber-100 text-amber-900 dark:bg-amber-900 dark:text-amber-100";
      case "ready":
      case "transcribing":
        return "bg-emerald-100 text-emerald-900 dark:bg-emerald-900 dark:text-emerald-100";
      case "ended":
        return "bg-muted text-muted-foreground";
      case "failed":
        return "bg-destructive/15 text-destructive";
    }
  }

  function fmtElapsed(totalSec: number): string {
    const mm = Math.floor(totalSec / 60);
    const ss = Math.floor(totalSec % 60);
    return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  }

  function onStatusChange(s: StreamStatus): void {
    status = s;
  }

  function onTranscript(msg: { type: "partial" | "final"; text: string }): void {
    if (msg.type === "partial") {
      partialText = msg.text;
      return;
    }
    finalSegments = [...finalSegments, msg.text];
    partialText = "";
  }

  function onSessionError(err: Error): void {
    errorMessage = err.message;
  }

  async function startSession(): Promise<void> {
    if (isActive || session !== null) return;
    errorMessage = "";
    partialText = "";
    finalSegments = [];
    session = new StreamingSessionImpl({
      onStatusChange,
      onTranscript,
      onError: onSessionError,
    });
    await session.start();
    startedAtMs = Date.now();
    sessionElapsedSec = 0;
    elapsedTimer = setInterval(() => {
      sessionElapsedSec = Math.floor((Date.now() - startedAtMs) / 1000);
    }, 1000);
  }

  async function stopSession(): Promise<void> {
    if (session === null) return;
    await session.stop();
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

  async function copyTranscript(): Promise<void> {
    if (transcript === "") return;
    try {
      await navigator.clipboard.writeText(transcript);
    } catch (err) {
      errorMessage = err instanceof Error ? err.message : "copy failed";
    }
  }

  $effect(() => {
    // Cleanup on unmount: Svelte 5 idiomatic replacement for onDestroy.
    // The explicit `onDestroy` import was triggering a current_component
    // null deref in the runtime; $effect's cleanup hook runs in the right
    // reactive scope without that hazard.
    return () => {
      if (session !== null) {
        void session.stop();
      }
      if (elapsedTimer !== null) {
        clearInterval(elapsedTimer);
        elapsedTimer = null;
      }
    };
  });
</script>

<svelte:head>
  <title>Realtime Streaming Panakoes</title>
</svelte:head>

<div class="flex flex-col items-center gap-6 py-8">
  <Card class="w-full max-w-2xl">
    <CardHeader>
      <CardTitle>Realtime streaming transcription</CardTitle>
      <CardDescription>
        Per-session GPU plus WebSocket. Each session spawns a dedicated
        <code>g4dn.xlarge</code> Spot instance running faster-whisper-large
        with Silero VAD. Cold-start adds 2 to 3 minutes (one-time per
        session); once ready, partials appear in 200 to 500 ms windows.
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
          <span
            class="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium {statusBadgeClass(
              status,
            )}"
          >
            {#if status === "connecting" || status === "spawning-gpu" || status === "catching-up"}
              <Loader2 class="h-3 w-3 animate-spin" />
            {/if}
            {statusLabel(status)}
          </span>
        </div>

        {#if errorMessage}
          <p class="text-sm text-destructive">{errorMessage}</p>
        {/if}
      </div>
    </CardContent>
  </Card>

  {#if isActive || finalSegments.length > 0 || partialText !== ""}
    <Card class="w-full max-w-2xl">
      <CardHeader>
        <CardTitle>Transcript</CardTitle>
        <CardDescription>
          Sentence-final segments emit as the GPU's LocalAgreement-2 layer
          confirms them. The running unconfirmed partial appears below.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {#if finalSegments.length === 0 && partialText === ""}
          <p class="text-sm text-muted-foreground">
            {#if status === "spawning-gpu"}
              Waiting on GPU cold-start. Audio is being captured and queued.
            {:else if status === "catching-up"}
              Catching up on queued audio.
            {:else}
              Speak to begin transcription.
            {/if}
          </p>
        {:else}
          {#each finalSegments as segment, idx (idx)}
            <p class="whitespace-pre-wrap text-sm leading-relaxed">{segment}</p>
          {/each}
          {#if partialText !== ""}
            <p class="whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground italic">
              {partialText}
            </p>
          {/if}
        {/if}
      </CardContent>
    </Card>
  {/if}

  {#if finalSegments.length > 0 && !isActive}
    <div class="w-full max-w-2xl">
      <Button variant="outline" onclick={copyTranscript} disabled={transcript === ""}>
        <Copy class="mr-2 h-4 w-4" />
        Copy transcript
      </Button>
    </div>
  {/if}

  <Card class="w-full max-w-2xl">
    <CardContent class="pt-6">
      <p class="text-xs text-muted-foreground">
        True streaming via per-session GPU plus WebSocket. The browser
        captures 200 ms PCM frames via an AudioWorklet at 16 kHz mono and
        ships them as JSON envelopes with base64-encoded payloads.
        Steady-state partial latency is 200 to 500 ms. Cold-start (first
        session of the day, no warm pool) adds 102 to 192 seconds; the
        catch-up phase drains queued frames at 10 Hz for 30 to 60 seconds
        after that. Sessions auto-finalize at 110 minutes (API Gateway
        2-hour limit) by reconnecting with prompt context preserved.
      </p>
    </CardContent>
  </Card>
</div>
