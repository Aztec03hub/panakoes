/**
 * True-streaming transcription session over WebSocket + AudioWorklet.
 *
 * Replaces the chunked-batch `RealtimeSession` per design v7 (see
 * `docs/design/realtime-streaming-transcription.md`). Wire-up summary:
 *
 *   1. Opens `wss://...execute-api.../dev?token=<JWT>` (token in query
 *      string, picked up by `ws-authorizer`). Optional reconnect-context
 *      query params: `parent_session_id`, `prompt_seed_text` (max 200
 *      chars).
 *   2. Recording is decoupled from the session lifecycle. The session can
 *      stay open while the mic is paused so the user can let in-flight
 *      transcription drain, play back what they captured, then resume.
 *   3. `startRecording()` acquires the mic via
 *      `getUserMedia({ audio: { sampleRate: 16000, channelCount: 1,
 *      echoCancellation: true } })` and pipes through an AudioWorklet
 *      that emits 200 ms PCM frames (3200 samples s16le = 6400 bytes raw).
 *   4. Each frame is wrapped in a JSON envelope:
 *        { action: "audio-frame", v: 1, seq, ts_ms_delta, pcm_b64 }
 *      where `pcm_b64 = btoa(String.fromCharCode(...pcmBytes))` of the
 *      6400-byte payload. The raw PCM is also retained for local playback.
 *   5. Status state machine:
 *        idle -> connecting -> spawning-gpu -> catching-up -> ready ->
 *        transcribing -> ended | failed
 *      During `spawning-gpu`, frames are queued locally and the SPA pings
 *      every 60 s to keep API GW's 10-min idle timer fresh. On `ready`,
 *      the queue is drained at 10 Hz (burst flush per DEG-03) into the
 *      WS while live frames continue to accrue; once drained, the state
 *      transitions to `transcribing` at the native 5 Hz capture rate.
 *   6. Downstream messages: `ready | partial | final | final-chunk |
 *      ended | error | ping | session-ending-soon`. On `ping{seq}` from
 *      the GPU, the SPA replies with `{"action":"ping-echo","seq":N}`.
 *      On `final-chunk`, individual seq buffers are re-assembled into
 *      `finalSegments`. On `ended`, status transitions to `ended`.
 *
 * The class is dependency-injected end-to-end (WebSocket factory,
 * `getUserMedia`, `startAudioWorklet`, clock, base64 encoder) so unit
 * tests can drive it with mocks under `vi.useFakeTimers()`.
 */

import type { AudioWorkletController } from "./audio-worklet";
import { startAudioWorklet as defaultStartAudioWorklet } from "./audio-worklet";
import { currentSession } from "./auth.svelte";
import { WS_URL as DEFAULT_WS_URL } from "./config";

/** State machine for the streaming session, per design v7. */
export type StreamStatus =
  | "idle"
  | "connecting"
  | "spawning-gpu"
  | "catching-up"
  | "ready"
  | "transcribing"
  | "ended"
  | "failed";

/** Severity for {@link LogEntry}. `info` for normal transitions and
 *  observations, `warn` for recoverable anomalies, `error` for fatal
 *  errors that already routed through {@link StreamingSessionOptions.onError}. */
export type LogLevel = "info" | "warn" | "error";

/**
 * A single observability event emitted by the session. The page collects
 * these into a scrolling event-log panel so the user can watch the live
 * progression of "Opening WS -> Connected -> Spawning GPU -> Catching up
 * -> Transcribing" without asking the orchestrator to check the backend.
 *
 *   ts:      Date.now() wall-clock at emission.
 *   level:   severity, used for color coding in the UI.
 *   source:  short stable tag identifying the subsystem ("session", "ws",
 *            "mic", "catchup"). Useful for filtering and for log copy/paste.
 *   message: human-readable one-line description.
 */
export interface LogEntry {
  ts: number;
  level: LogLevel;
  source: string;
  message: string;
}

/** Public observer callbacks the page wires. */
export interface StreamingSessionOptions {
  /** WebSocket base URL; defaults to `config.WS_URL`. Test override. */
  wsUrl?: string;
  /** JWT bearer token; defaults to the current auth session's token. */
  token?: string;
  /** Status-transition callback. */
  onStatusChange?: (s: StreamStatus) => void;
  /** Per-message transcript callback. `text` is the full payload from
   *  the server message; the consumer is free to append or replace. */
  onTranscript?: (msg: { type: "partial" | "final"; text: string }) => void;
  /** Recording-state callback. Fires whenever `isRecording` flips. */
  onRecordingChange?: (recording: boolean) => void;
  /** Fatal-error callback. */
  onError?: (err: Error) => void;
  /**
   * Observability-event callback. Fires for every meaningful internal
   * transition (WS handshake, status change, frame queueing, error). The
   * page wires this to a scrolling on-screen event-log panel. Errors that
   * also route through {@link onError} are logged here with level "error"
   * so the user sees a single ordered timeline; the two callbacks are not
   * mutually exclusive.
   *
   * Throws inside this callback are swallowed so a buggy consumer cannot
   * tear down the session. Pass `undefined` (or omit) to disable logging
   * with zero overhead.
   */
  onLog?: (entry: LogEntry) => void;
  /** Injectable deps; tests pass their own. */
  deps?: StreamingSessionDeps;
}

/** Injectable dependencies. */
export interface StreamingSessionDeps {
  /** WebSocket factory; defaults to `new WebSocket(url)`. */
  webSocketFactory?: (url: string) => WebSocket;
  /** mic-acquire factory; defaults to `navigator.mediaDevices.getUserMedia`. */
  getUserMedia?: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
  /** AudioWorklet starter; defaults to `lib/audio-worklet.ts`. */
  startAudioWorklet?: typeof defaultStartAudioWorklet;
  /** Monotonic clock source; defaults to `performance.now`. */
  now?: () => number;
  /** Base64 encoder for an ArrayBuffer of PCM. Defaults to a chunked
   *  String.fromCharCode + btoa. Tests pass a deterministic stub. */
  encodePcm?: (pcm: ArrayBuffer) => string;
}

/** Public class API per the design doc. */
export interface StreamingSession {
  start(parentSessionId?: string, promptSeedText?: string): Promise<void>;
  stop(): Promise<void>;
  startRecording(): Promise<void>;
  stopRecording(): Promise<void>;
  getRecordedBlob(): Blob | null;
  readonly status: StreamStatus;
  readonly partialText: string;
  readonly finalSegments: readonly string[];
  readonly isRecording: boolean;
}

/** Catch-up replay cadence during burst flush (DEG-03 = 10 Hz). */
const CATCHUP_FRAME_HZ = 10;
/** Ping cadence during spawning-gpu state, in milliseconds. */
const SPAWN_PING_INTERVAL_MS = 60_000;
/**
 * Stall watchdog window. If `spawning-gpu` runs this long without any
 * inbound WS message, the SPA logs a `warn` entry and flips
 * `isSpawnStuck`. The backend status pipeline emits at every phase
 * boundary (router-accepted, spawn-message-received, pool-claimed,
 * session-row-updated, run-instances-issued, instance-launching,
 * ec2-ecr-login, ec2-image-pull-start/done, ec2-prewarm-start/done,
 * ec2-container-launched, container-started, cuda-checked,
 * model-loading, model-loaded, warmup-complete, ready). The longest
 * naked window between phases is the image pull, which on a cold
 * EBS-lazy-loaded volume can run a few minutes; 90 s sits above the
 * 30-60 s normal spread between phases but below the failure horizon
 * for "no activity at all".
 */
const SPAWN_STALL_TIMEOUT_MS = 90_000;
/** PCM frame parameters used by the AudioWorklet. */
const PCM_SAMPLE_RATE = 16_000;
const PCM_BITS_PER_SAMPLE = 16;
const PCM_CHANNELS = 1;

/** Default chunked base64 encoder for an ArrayBuffer. `btoa` chokes on
 *  long strings on some browsers; chunk to 0x8000 bytes per pass. */
function defaultEncodePcm(pcm: ArrayBuffer): string {
  const bytes = new Uint8Array(pcm);
  const chunkSize = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode.apply(null, Array.from(chunk));
  }
  return btoa(binary);
}

/** Concrete implementation backing the public {@link StreamingSession} interface. */
export class StreamingSessionImpl implements StreamingSession {
  private readonly wsUrl: string;
  private readonly tokenOverride: string | undefined;
  private readonly onStatusChange?: (s: StreamStatus) => void;
  private readonly onTranscript?: (msg: { type: "partial" | "final"; text: string }) => void;
  private readonly onRecordingChange?: (recording: boolean) => void;
  private readonly onError?: (err: Error) => void;
  private readonly onLog?: (entry: LogEntry) => void;

  private readonly webSocketFactory: (url: string) => WebSocket;
  private readonly getUserMediaFn: (c: MediaStreamConstraints) => Promise<MediaStream>;
  private readonly startAudioWorkletFn: typeof defaultStartAudioWorklet;
  private readonly nowFn: () => number;
  private readonly encodePcmFn: (pcm: ArrayBuffer) => string;

  private _status: StreamStatus = "idle";
  private _partialText = "";
  private readonly _finalSegments: string[] = [];
  private _isRecording = false;

  private ws: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private worklet: AudioWorkletController | null = null;
  private seq = 0;
  private startWallMs = 0;
  private spawnPingTimer: ReturnType<typeof setInterval> | null = null;
  private catchupTimer: ReturnType<typeof setInterval> | null = null;
  /**
   * Fires `stallTimeoutMs` after the last inbound WS message while we're
   * in `spawning-gpu`. The server-side status events from the router /
   * spawner / EC2 / container cover the happy path and the spawn-callback
   * exception paths, but they cannot cover the silent-failure cases:
   * operator purging the spawn queue, the spawner ECS task itself
   * crashing, the SQS message hitting DLQ after maxReceiveCount, or
   * cloud-init dying before its first `post_status` call. The watchdog
   * is the catch-all that converts those invisible black holes into a
   * visible "no server activity in Ns" log entry.
   */
  private spawnStallTimer: ReturnType<typeof setTimeout> | null = null;
  private _spawnStuck = false;
  private readonly pendingFrames: ArrayBuffer[] = [];
  /** Recorded PCM frames retained for local playback. One entry per frame. */
  private readonly recordedPcm: Uint8Array[] = [];
  private readyReceived = false;
  /** Map of seq -> tokens for `final-chunk` re-assembly. */
  private readonly finalChunkBuffer = new Map<number, string[]>();
  private expectedFinalChunks: number | null = null;
  /** Guard against concurrent startRecording calls. */
  private recordingStartInFlight = false;

  constructor(opts: StreamingSessionOptions = {}) {
    this.wsUrl = opts.wsUrl ?? DEFAULT_WS_URL;
    this.tokenOverride = opts.token;
    this.onStatusChange = opts.onStatusChange;
    this.onTranscript = opts.onTranscript;
    this.onRecordingChange = opts.onRecordingChange;
    this.onError = opts.onError;
    this.onLog = opts.onLog;

    const deps = opts.deps ?? {};
    this.webSocketFactory = deps.webSocketFactory ?? ((url: string) => new WebSocket(url));
    this.getUserMediaFn = deps.getUserMedia ?? ((c) => navigator.mediaDevices.getUserMedia(c));
    this.startAudioWorkletFn = deps.startAudioWorklet ?? defaultStartAudioWorklet;
    this.nowFn = deps.now ?? (() => performance.now());
    this.encodePcmFn = deps.encodePcm ?? defaultEncodePcm;
  }

  get status(): StreamStatus {
    return this._status;
  }

  get partialText(): string {
    return this._partialText;
  }

  get finalSegments(): readonly string[] {
    return this._finalSegments;
  }

  get isRecording(): boolean {
    return this._isRecording;
  }

  /**
   * Open the WebSocket and transition to `spawning-gpu` once the WS
   * opens. Does NOT acquire the microphone; the caller invokes
   * `startRecording()` separately so recording is decoupled from session
   * lifetime (the user can pause recording without tearing down the GPU).
   *
   * `parentSessionId` + `promptSeedText` are forwarded as query-string
   * args only on a 110-min reconnect (design doc CRIT-01 fix); leave
   * undefined for fresh sessions.
   */
  async start(parentSessionId?: string, promptSeedText?: string): Promise<void> {
    if (this._status !== "idle" && this._status !== "ended" && this._status !== "failed") {
      return;
    }
    this.setStatus("connecting");
    this.startWallMs = this.nowFn();
    this.seq = 0;
    this._partialText = "";
    this._finalSegments.length = 0;
    this.pendingFrames.length = 0;
    this.recordedPcm.length = 0;
    this.readyReceived = false;
    this.finalChunkBuffer.clear();
    this.expectedFinalChunks = null;

    let ws: WebSocket;
    let url: string;
    try {
      url = this.buildWsUrl(parentSessionId, promptSeedText);
      // Log the URL with the token redacted; we log even the full base URL
      // so the user can verify they hit the right environment, but a JWT
      // in `?token=` is sensitive enough to mask out of the visible log.
      const redacted = url.replace(/(token=)[^&]+/, "$1<redacted>");
      this.emitLog("info", "session", `Opening WebSocket to ${redacted}`);
      ws = this.webSocketFactory(url);
    } catch (err) {
      this.emitError(err);
      this.setStatus("failed");
      return;
    }
    this.ws = ws;
    const handshakeStartedAtMs = this.nowFn();

    ws.onopen = () => {
      const handshakeMs = Math.floor(this.nowFn() - handshakeStartedAtMs);
      this.emitLog("info", "ws", `Connected (handshake ${handshakeMs}ms)`);
      this.setStatus("spawning-gpu");
      this.armSpawnPing();
    };
    ws.onmessage = (event: MessageEvent) => {
      this.handleMessage(event);
    };
    ws.onerror = () => {
      // emitError logs under source "session"; the transport-specific
      // entry above is omitted so the log timeline has a single
      // error row per fault rather than a duplicated pair.
      this.emitError(new Error("WebSocket transport error"));
    };
    ws.onclose = () => {
      this.emitLog("info", "ws", "Closed");
      this.handleClose();
    };
  }

  /**
   * Begin capturing audio. Acquires the mic, starts the AudioWorklet,
   * begins emitting frames. Safe to call when already recording (no-op).
   * Frames flow on the existing WebSocket; if the session has not been
   * started yet the caller is responsible for sequencing the two calls.
   */
  async startRecording(): Promise<void> {
    if (this._isRecording || this.recordingStartInFlight) {
      return;
    }
    this.recordingStartInFlight = true;
    try {
      this.emitLog("info", "mic", "Acquiring microphone (getUserMedia)");
      let stream: MediaStream;
      try {
        stream = await this.getUserMediaFn({
          audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true },
        });
      } catch (err) {
        this.emitError(err);
        return;
      }
      this.stream = stream;
      try {
        this.emitLog("info", "mic", "Starting AudioWorklet");
        this.worklet = await this.startAudioWorkletFn(stream, (pcm) => {
          this.handleFrame(pcm);
        });
      } catch (err) {
        this.emitError(err);
        this.releaseStream();
        return;
      }
      this._isRecording = true;
      this.emitLog("info", "mic", "Recording (16kHz mono, 200ms frames)");
      this.emitRecordingChange(true);
    } finally {
      this.recordingStartInFlight = false;
    }
  }

  /**
   * Stop the AudioWorklet + release the mic tracks. The WebSocket stays
   * open so in-flight transcription continues; partials/finals still
   * arrive. Safe to call when not recording (no-op).
   */
  async stopRecording(): Promise<void> {
    if (!this._isRecording) {
      return;
    }
    this._isRecording = false;
    this.emitLog("info", "mic", "Recording paused");
    if (this.worklet !== null) {
      const w = this.worklet;
      this.worklet = null;
      try {
        await w.stop();
      } catch {
        // Worklet stop can throw on already-closed contexts; harmless.
      }
    }
    this.releaseStream();
    this.emitRecordingChange(false);
  }

  /**
   * Return a WAV `Blob` of everything captured so far, or `null` if
   * nothing has been recorded. The blob is freshly constructed on each
   * call; callers should `URL.revokeObjectURL` any prior URL before
   * creating a new one. The PCM buffer persists across `stopRecording` /
   * `startRecording` cycles within the same session; `stop()` clears it.
   */
  getRecordedBlob(): Blob | null {
    if (this.recordedPcm.length === 0) {
      return null;
    }
    let total = 0;
    for (const frame of this.recordedPcm) {
      total += frame.byteLength;
    }
    const pcm = new Uint8Array(total);
    let offset = 0;
    for (const frame of this.recordedPcm) {
      pcm.set(frame, offset);
      offset += frame.byteLength;
    }
    const wav = buildWavBlob(pcm, PCM_SAMPLE_RATE, PCM_CHANNELS, PCM_BITS_PER_SAMPLE);
    return wav;
  }

  /**
   * Stop the worklet, close the WS, release the MediaStream, cancel all
   * timers. Safe to call from any state; idempotent.
   */
  async stop(): Promise<void> {
    if (this.spawnPingTimer !== null) {
      clearInterval(this.spawnPingTimer);
      this.spawnPingTimer = null;
    }
    if (this.catchupTimer !== null) {
      clearInterval(this.catchupTimer);
      this.catchupTimer = null;
    }
    this.clearStallWatchdog();
    if (this._isRecording) {
      this._isRecording = false;
      this.emitRecordingChange(false);
    }
    if (this.worklet !== null) {
      const w = this.worklet;
      this.worklet = null;
      try {
        await w.stop();
      } catch {
        // Worklet stop can throw on already-closed contexts; harmless.
      }
    }
    this.releaseStream();
    if (this.ws !== null) {
      try {
        if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
          this.ws.close();
        }
      } catch {
        // Some test mocks throw on close; harmless.
      }
      this.ws = null;
    }
    if (this._status !== "ended" && this._status !== "failed") {
      this.setStatus("ended");
    }
  }

  // ---------------------------------------------------------------------------
  // WebSocket URL + message dispatch
  // ---------------------------------------------------------------------------

  private buildWsUrl(parentSessionId?: string, promptSeedText?: string): string {
    const token = this.tokenOverride ?? currentSession.value?.token ?? "";
    const params = new URLSearchParams();
    params.set("token", token);
    if (parentSessionId !== undefined && parentSessionId !== "") {
      params.set("parent_session_id", parentSessionId);
    }
    if (promptSeedText !== undefined && promptSeedText !== "") {
      // Cap to 200 chars per design v7 (DDB row constraint).
      params.set("prompt_seed_text", promptSeedText.slice(0, 200));
    }
    return `${this.wsUrl}?${params.toString()}`;
  }

  private handleMessage(event: MessageEvent): void {
    // Any inbound message proves the backend is alive; reset the stall
    // watchdog before we even parse so a malformed message still counts
    // as a heartbeat.
    this.resetStallWatchdog();
    let parsed: unknown;
    try {
      parsed = JSON.parse(typeof event.data === "string" ? event.data : String(event.data));
    } catch (err) {
      this.emitError(err);
      return;
    }
    if (typeof parsed !== "object" || parsed === null) {
      return;
    }
    const msg = parsed as { type?: string; [k: string]: unknown };
    // Emit one log entry per received message so the user sees a live
    // timeline of GPU-side events. For text-bearing messages a short
    // snippet of the payload is included; ping/ping-echo are noisy and
    // intentionally short. Errors get logged again under "session" by
    // emitError below so the error row carries the more readable shape.
    if (msg.type !== undefined) {
      this.emitLog("info", "ws", `Received ${this.formatReceivedMessage(msg)}`);
    }
    switch (msg.type) {
      case "status": {
        // Real-time spawn + container init observability. The backend
        // (streaming-router, gpu-spawner, EC2 cloud-init, transcriber-
        // stream container) emits one of these envelopes at every
        // meaningful phase boundary so the session log panel turns the
        // formerly silent multi-minute spawn into a visible timeline.
        // Source label is "ws" so the entry sits alongside other
        // server-pushed events; the `stage` tag carries the canonical
        // phase id and `detail` is the human-readable string.
        const stage = typeof msg.stage === "string" ? msg.stage : "(unknown)";
        const detail = typeof msg.detail === "string" ? msg.detail : "";
        this.emitLog("info", "ws", `${stage}: ${detail}`);
        break;
      }
      case "ready":
        this.onReady();
        break;
      case "partial": {
        const text = typeof msg.text === "string" ? msg.text : "";
        this._partialText = text;
        this.emitTranscript("partial", text);
        break;
      }
      case "final": {
        const text = typeof msg.text === "string" ? msg.text : "";
        this._finalSegments.push(text);
        this._partialText = "";
        this.emitTranscript("final", text);
        break;
      }
      case "final-chunk": {
        const seq = typeof msg.seq === "number" ? msg.seq : -1;
        const total = typeof msg.total === "number" ? msg.total : 0;
        const tokens = Array.isArray(msg.tokens) ? (msg.tokens as string[]) : [];
        if (seq >= 0) {
          this.finalChunkBuffer.set(seq, tokens);
          this.expectedFinalChunks = total;
        }
        break;
      }
      case "ended": {
        this.assembleFinalChunks();
        this.setStatus("ended");
        break;
      }
      case "error": {
        const code = typeof msg.code === "string" ? msg.code : "unknown";
        const message = typeof msg.message === "string" ? msg.message : code;
        this.emitError(new Error(`${code}: ${message}`));
        this.setStatus("failed");
        break;
      }
      case "ping": {
        const seq = typeof msg.seq === "number" ? msg.seq : 0;
        this.sendPingEcho(seq);
        break;
      }
      case "session-ending-soon":
        // Information-only for v1; the page may surface a hint. The GPU
        // closes the WS on its own ~5 s later (warn_at_ms in the message).
        break;
      default:
        // Unknown message type; intentionally ignored for forward-compat.
        break;
    }
  }

  private onReady(): void {
    if (this.readyReceived) {
      return;
    }
    this.readyReceived = true;
    if (this.spawnPingTimer !== null) {
      clearInterval(this.spawnPingTimer);
      this.spawnPingTimer = null;
    }
    if (this.pendingFrames.length === 0) {
      this.setStatus("transcribing");
      return;
    }
    // Burst-flush per DEG-03: drain the queue at 10 Hz, then transition
    // to live `transcribing`. New live frames continue to enqueue while
    // catchup runs; the timer drains them in order.
    this.emitLog(
      "info",
      "catchup",
      `Replaying ${this.pendingFrames.length} queued frames at ${CATCHUP_FRAME_HZ}Hz`,
    );
    this.setStatus("catching-up");
    this.armCatchupTimer();
  }

  private armCatchupTimer(): void {
    const periodMs = Math.floor(1000 / CATCHUP_FRAME_HZ);
    this.catchupTimer = setInterval(() => {
      const frame = this.pendingFrames.shift();
      if (frame === undefined) {
        if (this.catchupTimer !== null) {
          clearInterval(this.catchupTimer);
          this.catchupTimer = null;
        }
        if (this._status === "catching-up") {
          this.setStatus("transcribing");
        }
        return;
      }
      this.transmitFrame(frame);
    }, periodMs);
  }

  private armSpawnPing(): void {
    this.spawnPingTimer = setInterval(() => {
      this.sendPing();
    }, SPAWN_PING_INTERVAL_MS);
  }

  /**
   * Stall-watchdog API. Armed when we enter `spawning-gpu`, reset every
   * time an inbound WS message arrives. If it fires we surface a warn in
   * the event log and flip the `isSpawnStuck` flag.
   */
  private armStallWatchdog(): void {
    this.clearStallWatchdog();
    this.spawnStallTimer = setTimeout(() => {
      this.spawnStallTimer = null;
      if (this._status !== "spawning-gpu") {
        return;
      }
      this._spawnStuck = true;
      const seconds = Math.floor(SPAWN_STALL_TIMEOUT_MS / 1000);
      this.emitLog(
        "warn",
        "ws",
        `No server activity for ${seconds}s. Spawn may have failed silently (e.g., capacity issue, spawner crash, queue purged, EC2 bootstrap died). Check CloudWatch /panakoes/dev/gpu-spawner and /panakoes/dev/transcriber-stream.`
      );
    }, SPAWN_STALL_TIMEOUT_MS);
  }

  private clearStallWatchdog(): void {
    if (this.spawnStallTimer !== null) {
      clearTimeout(this.spawnStallTimer);
      this.spawnStallTimer = null;
    }
  }

  private resetStallWatchdog(): void {
    if (this._status === "spawning-gpu") {
      this.armStallWatchdog();
    }
  }

  /** True once the stall watchdog has fired this session. */
  get isSpawnStuck(): boolean {
    return this._spawnStuck;
  }

  private sendPing(): void {
    if (this.ws === null || this.ws.readyState !== WebSocket.OPEN) {
      return;
    }
    try {
      this.ws.send(JSON.stringify({ action: "ping" }));
    } catch (err) {
      this.emitError(err);
    }
  }

  private sendPingEcho(seq: number): void {
    if (this.ws === null || this.ws.readyState !== WebSocket.OPEN) {
      return;
    }
    try {
      this.ws.send(JSON.stringify({ action: "ping-echo", seq }));
    } catch (err) {
      this.emitError(err);
    }
  }

  private assembleFinalChunks(): void {
    if (this.expectedFinalChunks === null || this.finalChunkBuffer.size === 0) {
      return;
    }
    const tokens: string[] = [];
    for (let i = 0; i < this.expectedFinalChunks; i++) {
      const chunk = this.finalChunkBuffer.get(i);
      if (chunk !== undefined) {
        tokens.push(...chunk);
      }
    }
    if (tokens.length > 0) {
      this._finalSegments.push(tokens.join(" "));
      this.emitTranscript("final", tokens.join(" "));
    }
    this.finalChunkBuffer.clear();
    this.expectedFinalChunks = null;
  }

  // ---------------------------------------------------------------------------
  // Frame capture + send
  // ---------------------------------------------------------------------------

  private handleFrame(pcm: ArrayBuffer): void {
    // Retain a copy for local playback regardless of WS state. The
    // AudioWorklet passes its buffer verbatim and reuses it for the next
    // frame in some runtimes, so we snapshot.
    this.recordedPcm.push(new Uint8Array(pcm.slice(0)));

    // During spawning-gpu, queue locally (DEG-03). Once `ready` arrives,
    // the catchup timer drains the queue at 10 Hz; new live frames keep
    // enqueueing until the queue is empty.
    if (this._status === "spawning-gpu" || this._status === "connecting") {
      this.pendingFrames.push(pcm);
      return;
    }
    if (this._status === "catching-up") {
      this.pendingFrames.push(pcm);
      return;
    }
    if (this._status === "transcribing") {
      this.transmitFrame(pcm);
      return;
    }
    // Frames captured in any other state (ended, failed) are dropped.
  }

  private transmitFrame(pcm: ArrayBuffer): void {
    if (this.ws === null || this.ws.readyState !== WebSocket.OPEN) {
      return;
    }
    const seq = this.seq;
    this.seq += 1;
    const tsMsDelta = Math.floor(this.nowFn() - this.startWallMs);
    const pcmB64 = this.encodePcmFn(pcm);
    const envelope = {
      action: "audio-frame",
      v: 1,
      seq,
      ts_ms_delta: tsMsDelta,
      pcm_b64: pcmB64,
    };
    try {
      this.ws.send(JSON.stringify(envelope));
    } catch (err) {
      this.emitError(err);
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  private setStatus(s: StreamStatus): void {
    if (this._status === s) {
      return;
    }
    const prev = this._status;
    this._status = s;
    this.emitLog("info", "session", `Status: ${prev} -> ${s}`);
    if (s === "spawning-gpu") {
      this.armStallWatchdog();
    } else {
      this.clearStallWatchdog();
    }
    if (this.onStatusChange !== undefined) {
      try {
        this.onStatusChange(s);
      } catch {
        // Swallow consumer-callback throws.
      }
    }
  }

  private emitTranscript(type: "partial" | "final", text: string): void {
    if (this.onTranscript === undefined) {
      return;
    }
    try {
      this.onTranscript({ type, text });
    } catch {
      // Swallow consumer-callback throws.
    }
  }

  private emitRecordingChange(recording: boolean): void {
    if (this.onRecordingChange === undefined) {
      return;
    }
    try {
      this.onRecordingChange(recording);
    } catch {
      // Swallow consumer-callback throws.
    }
  }

  private emitError(err: unknown): void {
    const wrapped = err instanceof Error ? err : new Error(String(err));
    // Errors flow into the event log as well so the user sees a single
    // ordered timeline of everything that happened. The `onError`
    // callback is still fired for fatal-error UI surface (red banner).
    this.emitLog("error", "session", wrapped.message);
    if (this.onError === undefined) {
      return;
    }
    try {
      this.onError(wrapped);
    } catch {
      // Swallow consumer-callback throws.
    }
  }

  /**
   * Format a received WS message for the event log. Keeps the line
   * short so the panel stays readable: `type` is always shown; partial
   * and final messages get a 60-char snippet of `text`; final-chunk
   * shows seq/total; ping shows seq. Everything else falls back to
   * just the type tag.
   */
  private formatReceivedMessage(msg: { type?: string; [k: string]: unknown }): string {
    const type = msg.type ?? "(unknown)";
    if (type === "partial" || type === "final") {
      const text = typeof msg.text === "string" ? msg.text : "";
      const snippet = text.length > 60 ? `${text.slice(0, 57)}...` : text;
      return `{type: ${type}, text: "${snippet}"}`;
    }
    if (type === "final-chunk") {
      const seq = typeof msg.seq === "number" ? msg.seq : "?";
      const total = typeof msg.total === "number" ? msg.total : "?";
      return `{type: final-chunk, seq: ${seq}/${total}}`;
    }
    if (type === "ping" || type === "ping-echo") {
      const seq = typeof msg.seq === "number" ? msg.seq : "?";
      return `{type: ${type}, seq: ${seq}}`;
    }
    if (type === "error") {
      const code = typeof msg.code === "string" ? msg.code : "unknown";
      return `{type: error, code: ${code}}`;
    }
    if (type === "status") {
      const stage = typeof msg.stage === "string" ? msg.stage : "(unknown)";
      return `{type: status, stage: ${stage}}`;
    }
    return `{type: ${type}}`;
  }

  /**
   * Emit one observability entry to the optional {@link onLog} callback.
   * Cheap no-op when no consumer is registered. Throws inside the
   * callback are swallowed; a buggy consumer cannot tear down the
   * session.
   */
  private emitLog(level: LogLevel, source: string, message: string): void {
    if (this.onLog === undefined) {
      return;
    }
    try {
      this.onLog({ ts: Date.now(), level, source, message });
    } catch {
      // Swallow consumer-callback throws.
    }
  }

  private releaseStream(): void {
    if (this.stream === null) {
      return;
    }
    for (const track of this.stream.getTracks()) {
      try {
        track.stop();
      } catch {
        // Track stop is best-effort; ignore.
      }
    }
    this.stream = null;
  }

  private handleClose(): void {
    if (this.spawnPingTimer !== null) {
      clearInterval(this.spawnPingTimer);
      this.spawnPingTimer = null;
    }
    if (this.catchupTimer !== null) {
      clearInterval(this.catchupTimer);
      this.catchupTimer = null;
    }
    this.clearStallWatchdog();
    if (this._status !== "ended" && this._status !== "failed") {
      this.setStatus("ended");
    }
  }
}

/** Convenience constructor returning the {@link StreamingSession} interface. */
export function createStreamingSession(opts: StreamingSessionOptions = {}): StreamingSessionImpl {
  return new StreamingSessionImpl(opts);
}

// ---------------------------------------------------------------------------
// WAV blob assembly for local playback
// ---------------------------------------------------------------------------

/**
 * Wrap a raw 16-bit little-endian PCM buffer in a WAV (RIFF) header so a
 * standard `<audio>` element can decode it without a custom MIME type.
 * Header layout (44 bytes, mono 16 kHz s16le):
 *   "RIFF" + size + "WAVE" + "fmt " + 16 + 1 (PCM) + channels +
 *   sampleRate + byteRate + blockAlign + bitsPerSample +
 *   "data" + dataSize + <pcm payload>
 */
function buildWavBlob(
  pcm: Uint8Array,
  sampleRate: number,
  channels: number,
  bitsPerSample: number,
): Blob {
  const byteRate = (sampleRate * channels * bitsPerSample) / 8;
  const blockAlign = (channels * bitsPerSample) / 8;
  const dataSize = pcm.byteLength;
  const header = new ArrayBuffer(44);
  const view = new DataView(header);
  // "RIFF" chunk descriptor
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeAscii(view, 8, "WAVE");
  // "fmt " sub-chunk
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true); // sub-chunk size for PCM
  view.setUint16(20, 1, true); // audio format: 1 = PCM
  view.setUint16(22, channels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitsPerSample, true);
  // "data" sub-chunk
  writeAscii(view, 36, "data");
  view.setUint32(40, dataSize, true);
  // Copy the PCM into a fresh ArrayBuffer-backed view so the BlobPart
  // type narrows past the lib.dom union (Uint8Array<SharedArrayBuffer>
  // is not assignable to BlobPart under TS strict mode).
  const pcmCopy = new ArrayBuffer(pcm.byteLength);
  new Uint8Array(pcmCopy).set(pcm);
  return new Blob([header, pcmCopy], { type: "audio/wav" });
}

function writeAscii(view: DataView, offset: number, value: string): void {
  for (let i = 0; i < value.length; i++) {
    view.setUint8(offset + i, value.charCodeAt(i));
  }
}
