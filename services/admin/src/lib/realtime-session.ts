/**
 * Chunked-batch pseudo-realtime transcription session.
 *
 * Captures audio continuously via a MediaRecorder, rotates the recorder
 * every `chunkSeconds` so each slice is a self-contained WebM blob, fires
 * each slice through the existing async Whisper-on-Batch ingestion path
 * (createIngestion + uploadToPresigned), then polls query-api until the
 * transcript materializes. Uploads run in parallel; recording rotation
 * never blocks on the previous chunk's upload or transcription.
 *
 * Pseudo-realtime is the explicit tradeoff: between the moment a chunk's
 * MediaRecorder stops and the next one starts, there is a sub-100ms gap
 * where audio is dropped. The cleaner alternative is true streaming via
 * per-session GPU + WebSocket (FOLLOWUPS.md). This shape ships TODAY
 * without backend changes by reusing the locked async path.
 *
 * The class is dependency-injected end-to-end (api wrappers, MediaRecorder
 * factory, getUserMedia, clock) so unit tests can drive it with mocks
 * under `vi.useFakeTimers()`.
 */

import {
  createIngestion as defaultCreateIngestion,
  fetchIngestion as defaultFetchIngestion,
  uploadToPresigned as defaultUploadToPresigned,
} from "./api";

/** Lifecycle state for a single recorded slice. */
export type ChunkStatus = "recording" | "uploading" | "transcribing" | "complete" | "failed";

/** Snapshot of a single chunk. The same object reference is reused across
 *  transitions so consumers can compare identity if they need to. */
export interface SessionChunk {
  index: number;
  ingestionId: string | null;
  filename: string;
  status: ChunkStatus;
  durationSeconds: number;
  sizeBytes: number;
  transcript: string | null;
  errorMessage: string | null;
}

/** Injectable dependencies. Defaults wire through to `lib/api.ts` + the
 *  browser globals. */
export interface RealtimeSessionDeps {
  createIngestion?: typeof defaultCreateIngestion;
  uploadToPresigned?: typeof defaultUploadToPresigned;
  fetchIngestion?: typeof defaultFetchIngestion;
  mediaRecorderFactory?: (stream: MediaStream, opts?: MediaRecorderOptions) => MediaRecorder;
  getUserMedia?: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
  now?: () => number;
}

/** Public construction options. */
export interface RealtimeSessionOptions {
  chunkSeconds?: number;
  mimeType?: string;
  pollIntervalMs?: number;
  maxParallelPolls?: number;
  onChunkUpdate?: (chunk: SessionChunk) => void;
  onError?: (err: Error) => void;
  deps?: RealtimeSessionDeps;
}

/** Minimum partial-chunk duration to bother uploading on stop(). Anything
 *  shorter is dropped silently (an over-eager click-stop right after start
 *  should not flood the backend with sub-second blobs). */
const MIN_PARTIAL_CHUNK_MS = 250;

const DEFAULT_MIME_TYPE = "audio/webm;codecs=opus";

/**
 * Public class. One instance per UI session.
 */
export class RealtimeSession {
  private readonly chunkSeconds: number;
  private readonly mimeType: string;
  private readonly pollIntervalMs: number;
  private readonly onChunkUpdate?: (chunk: SessionChunk) => void;
  private readonly onError?: (err: Error) => void;

  private readonly createIngestionFn: typeof defaultCreateIngestion;
  private readonly uploadToPresignedFn: typeof defaultUploadToPresigned;
  private readonly fetchIngestionFn: typeof defaultFetchIngestion;
  private readonly mediaRecorderFactory: (
    stream: MediaStream,
    opts?: MediaRecorderOptions,
  ) => MediaRecorder;
  private readonly getUserMediaFn: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
  private readonly nowFn: () => number;

  private active = false;
  private sessionStartIso = "";
  private stream: MediaStream | null = null;
  private currentRecorder: MediaRecorder | null = null;
  private currentChunkParts: Blob[] = [];
  private currentChunkIndex = 0;
  private currentChunkStartMs = 0;
  private rotationTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly chunkList: SessionChunk[] = [];
  private readonly pollTimers = new Map<number, ReturnType<typeof setTimeout>>();
  private readonly inFlightUploads = new Set<Promise<void>>();
  private stopCount = 0;

  constructor(opts: RealtimeSessionOptions = {}) {
    this.chunkSeconds = opts.chunkSeconds ?? 8;
    this.mimeType = opts.mimeType ?? DEFAULT_MIME_TYPE;
    this.pollIntervalMs = opts.pollIntervalMs ?? 2000;
    this.onChunkUpdate = opts.onChunkUpdate;
    this.onError = opts.onError;

    const deps = opts.deps ?? {};
    this.createIngestionFn = deps.createIngestion ?? defaultCreateIngestion;
    this.uploadToPresignedFn = deps.uploadToPresigned ?? defaultUploadToPresigned;
    this.fetchIngestionFn = deps.fetchIngestion ?? defaultFetchIngestion;
    this.mediaRecorderFactory =
      deps.mediaRecorderFactory ?? ((stream, options) => new MediaRecorder(stream, options));
    this.getUserMediaFn =
      deps.getUserMedia ?? ((constraints) => navigator.mediaDevices.getUserMedia(constraints));
    this.nowFn = deps.now ?? (() => performance.now());
  }

  /** True between `start()` resolving successfully and `stop()` completing. */
  get isActive(): boolean {
    return this.active;
  }

  /** Read-only chunk list in monotonically increasing index order. */
  get chunks(): readonly SessionChunk[] {
    return this.chunkList;
  }

  /**
   * Acquire the mic, start the first recorder, arm the rotation timer.
   *
   * Permission denials, lack of MediaRecorder support, or any other
   * acquisition error are surfaced via `onError(err)` rather than
   * thrown. The method resolves either way so the caller can keep its
   * UI flow simple.
   */
  async start(): Promise<void> {
    if (this.active) {
      return;
    }
    try {
      this.stream = await this.getUserMediaFn({ audio: true });
    } catch (err) {
      this.emitError(err);
      return;
    }
    this.active = true;
    this.sessionStartIso = new Date().toISOString();
    this.currentChunkIndex = 0;
    this.stopCount = 0;
    this.startNewRecorder();
    this.armRotationTimer();
  }

  /**
   * Stop the rotation timer, finalize the current chunk (if it carries
   * at least `MIN_PARTIAL_CHUNK_MS` of audio), release the MediaStream,
   * and let in-flight uploads + polls drain naturally. A second call to
   * `stop()` aborts polling immediately.
   */
  async stop(): Promise<void> {
    this.stopCount += 1;
    if (!this.active && this.stopCount === 1) {
      return;
    }
    if (this.rotationTimer !== null) {
      clearTimeout(this.rotationTimer);
      this.rotationTimer = null;
    }
    if (this.currentRecorder !== null && this.currentRecorder.state !== "inactive") {
      // Don't auto-start a fresh recorder after this stop fires.
      const finalRecorder = this.currentRecorder;
      this.currentRecorder = null;
      finalRecorder.stop();
    }
    if (this.stream !== null) {
      for (const track of this.stream.getTracks()) {
        track.stop();
      }
      this.stream = null;
    }
    this.active = false;
    if (this.stopCount >= 2) {
      // Hard-abort: cancel every outstanding poll timer.
      for (const timer of this.pollTimers.values()) {
        clearTimeout(timer);
      }
      this.pollTimers.clear();
    }
  }

  // ---------------------------------------------------------------------------
  // Recorder lifecycle
  // ---------------------------------------------------------------------------

  private startNewRecorder(): void {
    if (this.stream === null) {
      return;
    }
    let recorder: MediaRecorder;
    try {
      recorder = this.mediaRecorderFactory(this.stream, { mimeType: this.mimeType });
    } catch (err) {
      this.emitError(err);
      return;
    }
    this.currentRecorder = recorder;
    this.currentChunkParts = [];
    this.currentChunkStartMs = this.nowFn();

    const chunkIndex = this.currentChunkIndex;
    const sessionStartIso = this.sessionStartIso;
    const chunkStartMs = this.currentChunkStartMs;
    const mimeType = this.mimeType;

    recorder.ondataavailable = (event: BlobEvent) => {
      if (event.data && event.data.size > 0) {
        this.currentChunkParts.push(event.data);
      }
    };

    recorder.onstop = () => {
      const durationMs = this.nowFn() - chunkStartMs;
      const parts = this.currentChunkParts;
      this.currentChunkParts = [];
      const blob = new Blob(parts, { type: mimeType });

      // Drop sub-threshold blobs from the second stop() (final partial).
      if (durationMs < MIN_PARTIAL_CHUNK_MS && parts.length === 0) {
        return;
      }
      if (blob.size === 0) {
        return;
      }

      const ext = mimeType.startsWith("audio/mp4") ? "mp4" : "webm";
      const filename = `rt-${sessionStartIso}-chunk-${chunkIndex}.${ext}`;
      const chunk: SessionChunk = {
        index: chunkIndex,
        ingestionId: null,
        filename,
        status: "recording",
        durationSeconds: durationMs / 1000,
        sizeBytes: blob.size,
        transcript: null,
        errorMessage: null,
      };
      this.chunkList.push(chunk);
      this.emitUpdate(chunk);

      this.submitChunk(chunk, blob, mimeType);
    };

    try {
      recorder.start();
    } catch (err) {
      this.emitError(err);
    }
  }

  /** Arm the next rotation. On fire: stop the current recorder (which
   *  triggers `onstop` -> chunk creation + submit) and start a fresh one
   *  on the same MediaStream. */
  private armRotationTimer(): void {
    this.rotationTimer = setTimeout(() => {
      this.rotateRecorder();
    }, this.chunkSeconds * 1000);
  }

  private rotateRecorder(): void {
    if (!this.active) {
      return;
    }
    const finishingRecorder = this.currentRecorder;
    // Advance the index BEFORE the new recorder starts so its onstop
    // captures the right chunk number via closure capture in startNewRecorder.
    if (finishingRecorder !== null && finishingRecorder.state !== "inactive") {
      finishingRecorder.stop();
    }
    this.currentChunkIndex += 1;
    this.startNewRecorder();
    if (this.active) {
      this.armRotationTimer();
    }
  }

  // ---------------------------------------------------------------------------
  // Submission + polling
  // ---------------------------------------------------------------------------

  /** Fire-and-forget createIngestion + upload. On success, kicks off polling. */
  private submitChunk(chunk: SessionChunk, blob: Blob, mimeType: string): void {
    const promise = (async () => {
      try {
        chunk.status = "uploading";
        this.emitUpdate(chunk);

        // Wrap the Blob in a File for parity with the /upload page's flow;
        // the API contract for `createIngestion` works off raw size + mime
        // type so the File is a thin presentation choice.
        const file = new File([blob], chunk.filename, { type: mimeType });

        const ingestion = await this.createIngestionFn({
          filename: chunk.filename,
          content_type: mimeType,
          size_bytes: blob.size,
        });
        chunk.ingestionId = ingestion.ingestion_id;
        this.emitUpdate(chunk);

        await this.uploadToPresignedFn(ingestion.upload_url, file);

        chunk.status = "transcribing";
        this.emitUpdate(chunk);
        this.scheduleNextPoll(chunk);
      } catch (err) {
        chunk.status = "failed";
        chunk.errorMessage = err instanceof Error ? err.message : String(err);
        this.emitUpdate(chunk);
      }
    })();
    this.inFlightUploads.add(promise);
    void promise.finally(() => {
      this.inFlightUploads.delete(promise);
    });
  }

  /** Arm a single-shot setTimeout for this chunk's next poll. */
  private scheduleNextPoll(chunk: SessionChunk): void {
    if (this.stopCount >= 2) {
      // Hard-abort path: never schedule new polls after the user double-stops.
      return;
    }
    const existing = this.pollTimers.get(chunk.index);
    if (existing !== undefined) {
      clearTimeout(existing);
    }
    const timer = setTimeout(() => {
      this.pollTimers.delete(chunk.index);
      void this.pollChunk(chunk);
    }, this.pollIntervalMs);
    this.pollTimers.set(chunk.index, timer);
  }

  /** Poll query-api once; reschedule if not yet terminal. */
  private async pollChunk(chunk: SessionChunk): Promise<void> {
    if (chunk.ingestionId === null) {
      // The upload failed before populating the id; nothing to poll.
      return;
    }
    if (this.stopCount >= 2) {
      return;
    }
    try {
      const record = await this.fetchIngestionFn(chunk.ingestionId);
      if (record.transcript_status === "succeeded") {
        chunk.status = "complete";
        chunk.transcript = record.transcript?.text ?? "";
        this.emitUpdate(chunk);
        return;
      }
      if (record.transcript_status === "failed") {
        chunk.status = "failed";
        chunk.errorMessage = record.transcript_error_message ?? "transcription failed";
        this.emitUpdate(chunk);
        return;
      }
      // Not terminal: reschedule.
      this.scheduleNextPoll(chunk);
    } catch (err) {
      // Transient fetch failure: keep polling. Surfacing every transient
      // error to the UI would be noisy; the user will see it as "stuck in
      // transcribing" if the backend stays down.
      this.scheduleNextPoll(chunk);
      this.emitError(err);
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  private emitUpdate(chunk: SessionChunk): void {
    if (this.onChunkUpdate !== undefined) {
      try {
        this.onChunkUpdate(chunk);
      } catch {
        // A consumer throwing from the callback must not crash the session.
      }
    }
  }

  private emitError(err: unknown): void {
    if (this.onError === undefined) {
      return;
    }
    const wrapped = err instanceof Error ? err : new Error(String(err));
    try {
      this.onError(wrapped);
    } catch {
      // Swallow consumer-callback throws.
    }
  }
}
