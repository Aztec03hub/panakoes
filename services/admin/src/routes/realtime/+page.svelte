<script lang="ts">
  import Loader2 from "@lucide/svelte/icons/loader-2";
  import Mic from "@lucide/svelte/icons/mic";
  import MicOff from "@lucide/svelte/icons/mic-off";
  import Square from "@lucide/svelte/icons/square";
  import Play from "@lucide/svelte/icons/play";
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
   * Recording state is decoupled from session state. The big mic button
   * toggles recording on / off (start, pause, resume) without tearing
   * down the WebSocket or the GPU. The separate End-session button is
   * the only thing that closes the pipeline. This split exists so the
   * user can let in-flight transcription drain after a short clip
   * (especially during the 102 to 192 s cold-start window) and play
   * back what they captured before deciding to record more.
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

  /**
   * One recording segment captured by a start/stopRecording cycle.
   * `endMs === null` means the segment is still open (recording active).
   * Cumulative recording time is the sum of `(endMs ?? nowMs) - startMs`
   * across every segment, which lets the recording timer pause-and-resume
   * across multiple mic toggles within a single session.
   */
  interface RecordingSegment {
    startMs: number;
    endMs: number | null;
  }

  let session = $state<StreamingSessionImpl | null>(null);
  let status = $state<StreamStatus>("idle");
  let recording = $state(false);
  let partialText = $state("");
  let finalSegments = $state<string[]>([]);
  let sessionElapsedSec = $state(0);
  let recordingSegments = $state<RecordingSegment[]>([]);
  /**
   * Monotonic tick driven by the 1Hz interval below. Reactive deriveds
   * that need to re-render every second while a recording segment is open
   * (the open segment's elapsed time grows in real time) read this so
   * Svelte knows to re-evaluate. Closed segments do not depend on it; they
   * are immutable once `endMs` is set.
   */
  let nowTickMs = $state(0);
  let errorMessage = $state("");
  let playbackUrl = $state<string | null>(null);
  let elapsedTimer: ReturnType<typeof setInterval> | null = null;
  let startedAtMs = 0;

  const transcript = $derived(finalSegments.join("\n\n"));
  const isSessionLive = $derived(
    status === "connecting" ||
      status === "spawning-gpu" ||
      status === "catching-up" ||
      status === "ready" ||
      status === "transcribing",
  );
  const canRecord = $derived(
    status === "spawning-gpu" ||
      status === "catching-up" ||
      status === "ready" ||
      status === "transcribing",
  );

  /**
   * Cumulative recording-elapsed seconds across every segment in the
   * current session. The open segment (if any) is measured against
   * `nowTickMs` so the value advances at the 1Hz ticker cadence; closed
   * segments contribute their fixed `endMs - startMs` window. Returns
   * floor seconds for display.
   */
  const recordingElapsedSec = $derived.by(() => {
    let totalMs = 0;
    for (const seg of recordingSegments) {
      const endMs = seg.endMs ?? nowTickMs;
      totalMs += Math.max(0, endMs - seg.startMs);
    }
    return Math.floor(totalMs / 1000);
  });
  const hasRecordedAnything = $derived(recordingSegments.length > 0);

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

  function onRecordingChange(r: boolean): void {
    recording = r;
    // Segment bookkeeping: opening on start, closing on stop. The open
    // segment's elapsed time is computed in `recordingElapsedSec` against
    // the 1Hz `nowTickMs` so the display advances live.
    const nowMs = Date.now();
    if (r) {
      recordingSegments = [...recordingSegments, { startMs: nowMs, endMs: null }];
    } else {
      const last = recordingSegments[recordingSegments.length - 1];
      if (last !== undefined && last.endMs === null) {
        const closed: RecordingSegment = { startMs: last.startMs, endMs: nowMs };
        recordingSegments = [...recordingSegments.slice(0, -1), closed];
      }
    }
    // Whenever recording flips off (pause or end-session), refresh the
    // playback URL so the audio element shows the latest captured clip.
    // Whenever it flips on, drop the previous URL so we do not stream a
    // stale blob underneath an active capture.
    revokePlaybackUrl();
    if (!r) {
      refreshPlaybackUrl();
    }
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

  function revokePlaybackUrl(): void {
    if (playbackUrl !== null) {
      try {
        URL.revokeObjectURL(playbackUrl);
      } catch {
        // Some test environments stub URL; ignore.
      }
      playbackUrl = null;
    }
  }

  function refreshPlaybackUrl(): void {
    const current = session;
    if (current === null) return;
    const blob = current.getRecordedBlob();
    if (blob === null) return;
    try {
      playbackUrl = URL.createObjectURL(blob);
    } catch {
      // jsdom / older browsers may not implement createObjectURL.
      playbackUrl = null;
    }
  }

  async function startSession(): Promise<void> {
    // Guard against double-starts on an already-live session. After a
    // failed or ended terminal state the previous session object must be
    // torn down first (the caller is expected to End first, then Start).
    if (isSessionLive || session !== null) return;
    errorMessage = "";
    partialText = "";
    finalSegments = [];
    status = "idle";
    recording = false;
    sessionElapsedSec = 0;
    recordingSegments = [];
    nowTickMs = Date.now();
    revokePlaybackUrl();
    const next = new StreamingSessionImpl({
      onStatusChange,
      onTranscript,
      onRecordingChange,
      onError: onSessionError,
    });
    session = next;
    await next.start();
    startedAtMs = Date.now();
    nowTickMs = startedAtMs;
    elapsedTimer = setInterval(() => {
      const now = Date.now();
      nowTickMs = now;
      sessionElapsedSec = Math.floor((now - startedAtMs) / 1000);
    }, 1000);
  }

  /**
   * Tear down the current session if any. Idempotent: safe to call on a
   * null session or one already in a terminal (`ended` / `failed`) state;
   * `StreamingSessionImpl.stop()` is itself idempotent. Always clears the
   * local `session` reference so a subsequent `startSession()` can run.
   * Keeps the playback blob URL live so the user can still review the
   * last clip after End-session.
   */
  async function stopSession(): Promise<void> {
    const current = session;
    if (current === null) return;
    if (elapsedTimer !== null) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
    try {
      // Snapshot the blob BEFORE stop() so the playback URL survives
      // teardown.
      if (current.getRecordedBlob() !== null) {
        revokePlaybackUrl();
        try {
          const blob = current.getRecordedBlob();
          if (blob !== null) {
            playbackUrl = URL.createObjectURL(blob);
          }
        } catch {
          // jsdom; ignore.
        }
      }
      await current.stop();
    } catch (err) {
      errorMessage = err instanceof Error ? err.message : "stop failed";
    } finally {
      // Clear after the await so onRecordingChange callbacks from inside
      // stop() can still read `session` for the refreshPlaybackUrl path.
      session = null;
      recording = false;
      // Defensive: if any segment is still open (the session was torn
      // down without the worklet's recording-off callback firing), close
      // it now so the recording timer stops ticking. The session-on path
      // through stop() should emit onRecordingChange(false) and handle
      // this, but a transport error can route around that.
      const last = recordingSegments[recordingSegments.length - 1];
      if (last !== undefined && last.endMs === null) {
        const closed: RecordingSegment = { startMs: last.startMs, endMs: Date.now() };
        recordingSegments = [...recordingSegments.slice(0, -1), closed];
      }
    }
  }

  async function toggleSession(): Promise<void> {
    // Three logical cases:
    //   1. session live (isSessionLive)         -> end it
    //   2. dead session (failed/ended, instance still pinned) -> tear
    //      down then start fresh on the next click
    //   3. no session                           -> start
    if (isSessionLive) {
      await stopSession();
      return;
    }
    if (session !== null) {
      await stopSession();
    }
    await startSession();
  }

  async function toggleRecording(): Promise<void> {
    const current = session;
    if (current === null) {
      // No session yet: start one AND begin recording. This matches the
      // user expectation that the big mic button is the primary control.
      await startSession();
      const justStarted = session;
      if (justStarted !== null) {
        await justStarted.startRecording();
      }
      return;
    }
    if (!canRecord) {
      // Session is terminal or in an unrecoverable state. Start fresh.
      await stopSession();
      await startSession();
      const justStarted = session;
      if (justStarted !== null) {
        await justStarted.startRecording();
      }
      return;
    }
    if (current.isRecording) {
      await current.stopRecording();
    } else {
      await current.startRecording();
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
      revokePlaybackUrl();
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
          onclick={() => {
            void toggleRecording();
          }}
          aria-label={recording ? "Pause recording" : "Start recording"}
          aria-pressed={recording}
          class="relative flex h-32 w-32 items-center justify-center rounded-full border-4 transition-colors {recording
            ? 'border-destructive bg-destructive/10'
            : 'border-primary bg-primary/5 hover:bg-primary/10'}"
        >
          {#if recording}
            <span class="absolute inset-0 animate-ping rounded-full border-4 border-destructive opacity-30"></span>
            <Mic class="h-12 w-12 text-destructive" />
          {:else if isSessionLive}
            <MicOff class="h-12 w-12 text-primary" />
          {:else}
            <Mic class="h-12 w-12 text-primary" />
          {/if}
        </button>

        <div class="flex flex-col items-center gap-2">
          {#if isSessionLive || hasRecordedAnything}
            <div class="flex items-start gap-6">
              <div class="flex flex-col items-center">
                <span class="font-mono text-2xl tabular-nums">{fmtElapsed(sessionElapsedSec)}</span>
                <span class="text-xs uppercase tracking-wide text-muted-foreground">session</span>
              </div>
              <div class="flex flex-col items-center">
                <span
                  class="font-mono text-2xl tabular-nums {recording
                    ? 'text-destructive'
                    : 'text-muted-foreground'}"
                >
                  {fmtElapsed(recordingElapsedSec)}
                </span>
                <span class="text-xs uppercase tracking-wide text-muted-foreground">recording</span>
              </div>
            </div>
          {/if}
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
          {#if isSessionLive && !recording}
            <span class="text-xs text-muted-foreground">
              Session is open. Press the mic to resume recording, or end the session below.
            </span>
          {/if}
        </div>

        <div class="flex items-center gap-2">
          <Button
            variant={isSessionLive ? "destructive" : "outline"}
            size="sm"
            onclick={() => {
              void toggleSession();
            }}
          >
            {#if isSessionLive}
              <Square class="mr-2 h-4 w-4" />
              End session
            {:else}
              <Play class="mr-2 h-4 w-4" />
              Start session
            {/if}
          </Button>
        </div>

        {#if !recording && playbackUrl !== null}
          <div class="flex w-full flex-col items-center gap-1">
            <span class="text-xs text-muted-foreground">Playback of captured audio</span>
            <audio controls src={playbackUrl} class="w-full max-w-md"></audio>
          </div>
        {/if}

        {#if errorMessage}
          <p class="text-sm text-destructive">{errorMessage}</p>
        {/if}
      </div>
    </CardContent>
  </Card>

  {#if isSessionLive || finalSegments.length > 0 || partialText !== ""}
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

  {#if finalSegments.length > 0 && !isSessionLive}
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
