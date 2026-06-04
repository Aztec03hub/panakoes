<script lang="ts">
  import Loader2 from "@lucide/svelte/icons/loader-2";
  import Mic from "@lucide/svelte/icons/mic";
  import MicOff from "@lucide/svelte/icons/mic-off";
  import Square from "@lucide/svelte/icons/square";
  import Play from "@lucide/svelte/icons/play";
  import Copy from "@lucide/svelte/icons/copy";
  import FileAudio from "@lucide/svelte/icons/file-audio";
  import { Button } from "$lib/components/ui/button";
  import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
  } from "$lib/components/ui/card";
  import {
    type LogEntry,
    StreamingSessionImpl,
    type StreamStatus,
  } from "$lib/streaming-session";
  import {
    createFileFrameStarter,
    decodeAudioFile,
  } from "$lib/file-frame-source";

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
  /**
   * Scrolling event log surfaced below the controls. Every state
   * transition, every received WS message, every error gets pushed
   * here by the streaming session's `onLog` callback. Capped to
   * `LOG_MAX_ENTRIES` so a long session does not eat unbounded memory;
   * oldest entries are dropped when the cap is hit.
   */
  let logEntries = $state<LogEntry[]>([]);
  let logCollapsed = $state(false);
  /**
   * Smart auto-scroll: when the user is parked at the bottom (or close
   * to it), new entries auto-scroll into view. When the user has
   * scrolled up to read older entries, we leave their viewport alone
   * and surface a small "Jump to latest" hint. The check runs on every
   * scroll event against `LOG_BOTTOM_TOLERANCE_PX`.
   */
  let logContainerEl: HTMLDivElement | null = $state(null);
  let logAutoScroll = $state(true);
  const LOG_MAX_ENTRIES = 500;
  const LOG_BOTTOM_TOLERANCE_PX = 24;

  /**
   * File-upload transcription state. When a file session is active, the mic
   * button is disabled and the file frame source drives the same GPU + WS
   * pipeline a mic session does. `fileActive` is true from the moment a file
   * session starts until it ends (gracefully drained or torn down).
   */
  let selectedFile = $state<File | null>(null);
  let fileActive = $state(false);
  let fileDecoding = $state(false);
  let fileFramesSent = $state(0);
  let fileFramesTotal = $state(0);
  let fileError = $state("");
  /** Set when the file frame source has emitted its last frame. */
  let fileFramesComplete = $state(false);
  let fileInputEl: HTMLInputElement | null = $state(null);
  /** Timer that holds the session open for trailing partials/finals after
   *  the last frame is sent, then ends the session gracefully. */
  let fileDrainTimer: ReturnType<typeof setTimeout> | null = null;
  /** Seconds to keep the session open after frames complete so the GPU can
   *  emit trailing partials and the final segment. */
  const FILE_DRAIN_GRACE_MS = 10_000;

  const fileBusy = $derived(fileActive || fileDecoding);

  // Finals are word/short-token segments (LocalAgreement-2 commits per
  // token), so join with spaces into flowing text, not per-token paragraphs.
  const transcript = $derived(finalSegments.join(" ").replace(/\s+/g, " ").trim());
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
    // File path: the drain grace timer must not start until the session has
    // actually DELIVERED the queued frames to the GPU. Frames emitted during
    // spawning-gpu only sit in the local queue; "all frames emitted" there
    // means nothing has been transcribed yet (first e2e run ended the
    // session 30s in, 7 minutes before the GPU went ready). `transcribing`
    // is the state the session reaches once the catch-up replay drained.
    if (fileActive && fileFramesComplete && s === "transcribing") {
      startFileDrainGrace();
    }
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

  /**
   * Append one log entry to the scrolling event panel. Caps the array
   * at `LOG_MAX_ENTRIES` by dropping the oldest entry; a session that
   * runs for 110 minutes can easily produce thousands of entries, so an
   * unbounded array would balloon DOM nodes and memory. The cap is
   * sized to comfortably hold a full reconnect-cold-start arc.
   */
  function onSessionLog(entry: LogEntry): void {
    if (logEntries.length >= LOG_MAX_ENTRIES) {
      logEntries = [...logEntries.slice(logEntries.length - LOG_MAX_ENTRIES + 1), entry];
    } else {
      logEntries = [...logEntries, entry];
    }
  }

  /**
   * Pad a number to `width` characters left of the decimal. Tiny
   * helper used only by `fmtLogTime`; broken out so the formatter
   * reads as one expression.
   */
  function pad(n: number, width: number): string {
    return String(n).padStart(width, "0");
  }

  /** Format the event timestamp as `HH:MM:SS.mmm` in local time. */
  function fmtLogTime(ts: number): string {
    const d = new Date(ts);
    return `${pad(d.getHours(), 2)}:${pad(d.getMinutes(), 2)}:${pad(d.getSeconds(), 2)}.${pad(d.getMilliseconds(), 3)}`;
  }

  /** Tailwind color class for the level column. */
  function logLevelClass(level: LogEntry["level"]): string {
    switch (level) {
      case "info":
        return "text-muted-foreground";
      case "warn":
        return "text-amber-700 dark:text-amber-300";
      case "error":
        return "text-destructive";
    }
  }

  /**
   * Recompute whether the user is parked at the bottom of the log
   * container. Called from the scroll listener. The tolerance is wide
   * enough to absorb sub-pixel rounding and rapidly-arriving entries
   * pushing the bottom edge a few pixels below `scrollTop + clientHeight`.
   */
  function recomputeLogAutoScroll(): void {
    const el = logContainerEl;
    if (el === null) {
      return;
    }
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    logAutoScroll = distanceFromBottom <= LOG_BOTTOM_TOLERANCE_PX;
  }

  /**
   * Scroll the log container to the very bottom. Used both by the
   * reactive `$effect` below and by the "Jump to latest" affordance.
   */
  function scrollLogToBottom(): void {
    const el = logContainerEl;
    if (el === null) {
      return;
    }
    el.scrollTop = el.scrollHeight;
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
    logEntries = [];
    logAutoScroll = true;
    revokePlaybackUrl();
    const next = new StreamingSessionImpl({
      onStatusChange,
      onTranscript,
      onRecordingChange,
      onError: onSessionError,
      onLog: onSessionLog,
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

  function onFileSelected(event: Event): void {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    selectedFile = file;
    fileError = "";
  }

  /**
   * Start a file-driven streaming session. Decodes + resamples the selected
   * file, then opens a session exactly like the mic path but injects a file
   * frame source in place of the mic worklet starter (and a no-op
   * getUserMedia). Frames enqueue during spawning-gpu and replay on ready,
   * identical to mic frames. Once all frames are sent and the queue drains,
   * the session is held open ~10 s for trailing partials/finals, then ended
   * gracefully via the same stop path the End-session button uses.
   */
  async function transcribeFile(): Promise<void> {
    if (fileBusy || isSessionLive || session !== null) return;
    const file = selectedFile;
    if (file === null) {
      fileError = "Pick an audio file first.";
      return;
    }
    fileError = "";
    errorMessage = "";
    partialText = "";
    finalSegments = [];
    status = "idle";
    recording = false;
    sessionElapsedSec = 0;
    recordingSegments = [];
    nowTickMs = Date.now();
    logEntries = [];
    logAutoScroll = true;
    fileFramesSent = 0;
    fileFramesTotal = 0;
    fileFramesComplete = false;
    revokePlaybackUrl();

    fileDecoding = true;
    onSessionLog({
      ts: Date.now(),
      level: "info",
      source: "file",
      message: `file-selected: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`,
    });

    let decoded: Awaited<ReturnType<typeof decodeAudioFile>>;
    try {
      decoded = await decodeAudioFile(file);
    } catch (err) {
      fileDecoding = false;
      fileError = err instanceof Error ? err.message : "decode failed";
      onSessionLog({
        ts: Date.now(),
        level: "error",
        source: "file",
        message: `decode failed: ${fileError}`,
      });
      return;
    }
    fileDecoding = false;
    fileFramesTotal = decoded.frameCount;
    onSessionLog({
      ts: Date.now(),
      level: "info",
      source: "file",
      message: `file-selected: ${file.name}, ${file.size} bytes, duration ${decoded.durationSec.toFixed(1)}s`,
    });
    onSessionLog({
      ts: Date.now(),
      level: "info",
      source: "file",
      message: `decode-done: ${decoded.frameCount} frames at ${decoded.sampleRate} Hz`,
    });

    const frameStarter = createFileFrameStarter(decoded.samples, {
      onProgress: (sent, total) => {
        fileFramesSent = sent;
        fileFramesTotal = total;
      },
      onComplete: () => {
        onFileFramesComplete();
      },
    });

    fileActive = true;
    const next = new StreamingSessionImpl({
      onStatusChange,
      onTranscript,
      onRecordingChange,
      onError: onSessionError,
      onLog: onSessionLog,
      deps: {
        // No mic for the file path; the session calls getUserMedia before
        // the worklet starter, so hand it a dummy stream it never touches.
        getUserMedia: async () => ({ getTracks: () => [] }) as unknown as MediaStream,
        startAudioWorklet: frameStarter,
      },
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
    // Begin emitting frames. The session queues them during spawning-gpu and
    // replays on ready, exactly like mic frames.
    onSessionLog({
      ts: Date.now(),
      level: "info",
      source: "file",
      message: "file-streaming-started",
    });
    await next.startRecording();
  }

  /** Fired by the file frame source once the last frame is emitted. If the
   *  session is already transcribing (GPU caught up), start the drain grace
   *  immediately; otherwise wait for onStatusChange to reach `transcribing`
   *  (frames are still queued client-side during spawning-gpu/catching-up,
   *  so ending on emission would discard the whole recording). */
  function onFileFramesComplete(): void {
    if (fileFramesComplete) return;
    fileFramesComplete = true;
    onSessionLog({
      ts: Date.now(),
      level: "info",
      source: "file",
      message: `file-frames-complete: ${fileFramesTotal} frames emitted`,
    });
    if (status === "transcribing") {
      startFileDrainGrace();
    } else {
      onSessionLog({
        ts: Date.now(),
        level: "info",
        source: "file",
        message: `file-awaiting-gpu: all frames queued; holding session until GPU catch-up completes (status: ${status})`,
      });
    }
  }

  /** Holds the session open for trailing partials/finals, then ends it
   *  gracefully. Only called once the session is in `transcribing`. */
  function startFileDrainGrace(): void {
    if (fileDrainTimer !== null) return;
    onSessionLog({
      ts: Date.now(),
      level: "info",
      source: "file",
      message: `file-session-draining: holding session open ${Math.floor(FILE_DRAIN_GRACE_MS / 1000)}s for trailing partials/finals`,
    });
    fileDrainTimer = setTimeout(() => {
      fileDrainTimer = null;
      void endFileSession();
    }, FILE_DRAIN_GRACE_MS);
  }

  /** End the active file session via the shared stop path, then clear the
   *  file-session flags so the mic controls re-enable. */
  async function endFileSession(): Promise<void> {
    if (fileDrainTimer !== null) {
      clearTimeout(fileDrainTimer);
      fileDrainTimer = null;
    }
    await stopSession();
    fileActive = false;
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
      if (fileDrainTimer !== null) {
        clearTimeout(fileDrainTimer);
        fileDrainTimer = null;
      }
      revokePlaybackUrl();
    };
  });

  $effect(() => {
    // Auto-scroll the log panel to the bottom whenever a new entry is
    // appended, BUT only when the user is already parked at (or near)
    // the bottom. This preserves the scroll position if the user has
    // scrolled up to read an older entry: they keep their viewport and
    // a "Jump to latest" button surfaces below. The dependency on
    // `logEntries.length` keeps this effect tied to actual appends and
    // out of the way of unrelated re-renders.
    logEntries.length;
    if (logAutoScroll) {
      // Run after the DOM has flushed the new entry so scrollHeight
      // reflects the latest content; queueMicrotask is enough.
      queueMicrotask(() => {
        scrollLogToBottom();
      });
    }
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
          disabled={fileBusy}
          onclick={() => {
            void toggleRecording();
          }}
          aria-label={recording ? "Pause recording" : "Start recording"}
          aria-pressed={recording}
          class="relative flex h-32 w-32 items-center justify-center rounded-full border-4 transition-colors disabled:cursor-not-allowed disabled:opacity-40 {recording
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
            data-testid="session-status"
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
              if (fileActive) {
                void endFileSession();
              } else {
                void toggleSession();
              }
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

        <div class="flex w-full flex-col items-center gap-2 border-t pt-4">
          <span class="text-xs uppercase tracking-wide text-muted-foreground">
            Or transcribe an audio file
          </span>
          <div class="flex w-full max-w-md flex-col items-center gap-2 sm:flex-row sm:justify-center">
            <input
              bind:this={fileInputEl}
              data-testid="file-upload-input"
              type="file"
              accept="audio/*"
              disabled={fileBusy || isSessionLive}
              onchange={onFileSelected}
              class="block w-full text-sm text-muted-foreground file:mr-3 file:rounded-md file:border file:border-input file:bg-background file:px-3 file:py-1.5 file:text-sm file:font-medium hover:file:bg-accent disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
            />
            <Button
              data-testid="file-transcribe-button"
              variant="outline"
              size="sm"
              disabled={fileBusy || isSessionLive || selectedFile === null}
              onclick={() => {
                void transcribeFile();
              }}
            >
              <FileAudio class="mr-2 h-4 w-4" />
              Transcribe file
            </Button>
          </div>
          {#if fileDecoding}
            <span class="text-xs text-muted-foreground">Decoding audio file...</span>
          {:else if fileActive}
            <span class="text-xs text-muted-foreground">
              {#if fileFramesComplete}
                All {fileFramesTotal} frames sent. Draining transcription...
              {:else}
                Streaming file: frame {fileFramesSent}/{fileFramesTotal}
              {/if}
            </span>
          {/if}
          {#if fileError}
            <p class="text-xs text-destructive">{fileError}</p>
          {/if}
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

  {#if status !== "idle"}
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
            {:else if status === "ended"}
              Session ended without producing any transcripts. The recording was
              likely too short or too quiet for LocalAgreement-2 to commit a
              segment (it needs sustained speech across multiple inference
              windows).
            {:else if status === "failed"}
              Session failed. See the event log below for the underlying error.
            {:else}
              Speak to begin transcription.
            {/if}
          </p>
        {:else}
          <!-- Finals arrive as word/short-token segments from LocalAgreement-2,
               not full sentences; render them as one flowing paragraph rather
               than one paragraph per token (e2e run 8 showed word-per-line). -->
          <div data-testid="transcript-text">
            <p class="whitespace-pre-wrap text-sm leading-relaxed">{transcript}</p>
            {#if partialText !== ""}
              <p class="whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground italic">
                {partialText}
              </p>
            {/if}
          </div>
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
    <CardHeader class="flex flex-row items-start justify-between gap-2 space-y-0">
      <div class="flex flex-col gap-1">
        <CardTitle>Event log</CardTitle>
        <CardDescription>
          Live timeline of session state, WebSocket messages, mic events,
          and errors. {logEntries.length}
          {logEntries.length === 1 ? "entry" : "entries"}
          {#if logEntries.length >= LOG_MAX_ENTRIES}(capped at {LOG_MAX_ENTRIES}, oldest dropped){/if}.
        </CardDescription>
      </div>
      <Button
        variant="outline"
        size="sm"
        onclick={() => {
          logCollapsed = !logCollapsed;
        }}
        aria-expanded={!logCollapsed}
        aria-controls="event-log-body"
      >
        {logCollapsed ? "Expand" : "Collapse"}
      </Button>
    </CardHeader>
    {#if !logCollapsed}
      <CardContent>
        <div class="relative">
          <div
            id="event-log-body"
            bind:this={logContainerEl}
            onscroll={recomputeLogAutoScroll}
            role="log"
            aria-live="polite"
            aria-label="Realtime session event log"
            class="h-64 overflow-y-auto rounded-md border bg-muted/30 p-2 font-mono text-xs leading-relaxed"
          >
            {#if logEntries.length === 0}
              <p class="text-muted-foreground italic">
                No events yet. Start a session to watch the live timeline.
              </p>
            {:else}
              {#each logEntries as entry, idx (idx)}
                <div class="flex gap-2 whitespace-pre-wrap break-words">
                  <span class="shrink-0 text-muted-foreground/80 tabular-nums">{fmtLogTime(entry.ts)}</span>
                  <span class="w-12 shrink-0 uppercase {logLevelClass(entry.level)}">{entry.level}</span>
                  <span class="w-16 shrink-0 text-muted-foreground">{entry.source}</span>
                  <span class={entry.level === "error" ? "text-destructive" : ""}>{entry.message}</span>
                </div>
              {/each}
            {/if}
          </div>
          {#if !logAutoScroll && logEntries.length > 0}
            <button
              type="button"
              class="absolute right-3 bottom-3 rounded-md border bg-background px-2 py-1 text-xs shadow-sm hover:bg-accent"
              onclick={() => {
                logAutoScroll = true;
                scrollLogToBottom();
              }}
            >
              Jump to latest
            </button>
          {/if}
        </div>
      </CardContent>
    {/if}
  </Card>

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
