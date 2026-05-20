import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type ChunkStatus, RealtimeSession, type SessionChunk } from "../src/lib/realtime-session";
import type { CreateIngestionResponse, IngestionRecord, TranscriptStatus } from "../src/lib/types";

// ---------------------------------------------------------------------------
// Test helpers: fake MediaStream + MediaRecorder
//
// The real DOM types are not available in jsdom (MediaRecorder is missing,
// MediaStream is partial), so we synthesize the minimum surface the
// RealtimeSession touches: getTracks(), start(), stop(), state,
// ondataavailable, onstop.
// ---------------------------------------------------------------------------

interface FakeTrack {
  stop: ReturnType<typeof vi.fn>;
}

interface FakeStream {
  getTracks: () => FakeTrack[];
}

function makeFakeStream(): FakeStream & { tracks: FakeTrack[] } {
  const tracks: FakeTrack[] = [{ stop: vi.fn() }];
  return {
    tracks,
    getTracks: () => tracks,
  };
}

interface FakeRecorderHandle {
  recorder: {
    state: "inactive" | "recording" | "paused";
    start: () => void;
    stop: () => void;
    ondataavailable: ((event: { data: Blob }) => void) | null;
    onstop: (() => void) | null;
  };
  emitData: (blob: Blob) => void;
  emitStop: () => void;
}

function makeRecorderFactory() {
  const handles: FakeRecorderHandle[] = [];
  const factory = (_stream: MediaStream, _opts?: MediaRecorderOptions) => {
    const inner: FakeRecorderHandle["recorder"] = {
      state: "inactive",
      ondataavailable: null,
      onstop: null,
      start: () => {
        inner.state = "recording";
      },
      stop: () => {
        inner.state = "inactive";
        // Mimic the real MediaRecorder: ondataavailable fires once on stop
        // when no `timeslice` is configured, THEN onstop fires.
        inner.ondataavailable?.({ data: new Blob(["audio-bytes"], { type: "audio/webm" }) });
        inner.onstop?.();
      },
    };
    const handle: FakeRecorderHandle = {
      recorder: inner,
      emitData: (blob) => inner.ondataavailable?.({ data: blob }),
      emitStop: () => inner.onstop?.(),
    };
    handles.push(handle);
    return inner as unknown as MediaRecorder;
  };
  return { factory, handles };
}

function makeIngestionResponse(id: string): CreateIngestionResponse {
  return {
    ingestion_id: id,
    upload_url: `https://s3.example.com/${id}`,
    expires_at: "2099-01-01T00:00:00Z",
  };
}

function makeIngestionRecord(
  id: string,
  transcriptStatus: TranscriptStatus | null,
  transcriptText?: string,
  errorMessage?: string,
): IngestionRecord {
  return {
    ingestion_id: id,
    user_id: "u_test",
    filename: `${id}.webm`,
    content_type: "audio/webm",
    size_bytes: 1024,
    s3_key: `ingest/${id}`,
    status: "uploaded",
    created_at: "2026-05-20T05:00:00Z",
    updated_at: "2026-05-20T05:00:00Z",
    transcript_status: transcriptStatus,
    transcript:
      transcriptStatus === "succeeded"
        ? {
            text: transcriptText ?? "hello world",
            segments: [],
            language: "en",
            duration_seconds: 8,
          }
        : null,
    transcript_error_message: errorMessage ?? null,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("RealtimeSession", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("captures a chunk, submits it, and emits state transitions recording -> uploading -> transcribing -> complete", async () => {
    const stream = makeFakeStream();
    const { factory, handles } = makeRecorderFactory();
    const createIngestion = vi.fn().mockResolvedValue(makeIngestionResponse("ing-0"));
    const uploadToPresigned = vi.fn().mockResolvedValue(undefined);
    const fetchIngestion = vi
      .fn()
      .mockResolvedValueOnce(makeIngestionRecord("ing-0", "pending"))
      .mockResolvedValueOnce(makeIngestionRecord("ing-0", "succeeded", "the cat sat"));

    const updates: { index: number; status: ChunkStatus }[] = [];
    const session = new RealtimeSession({
      chunkSeconds: 8,
      pollIntervalMs: 100,
      onChunkUpdate: (c: SessionChunk) => {
        updates.push({ index: c.index, status: c.status });
      },
      deps: {
        getUserMedia: vi.fn().mockResolvedValue(stream as unknown as MediaStream),
        mediaRecorderFactory: factory,
        createIngestion: createIngestion as never,
        uploadToPresigned: uploadToPresigned as never,
        fetchIngestion: fetchIngestion as never,
      },
    });

    await session.start();
    expect(session.isActive).toBe(true);
    expect(handles.length).toBe(1);
    expect(handles[0].recorder.state).toBe("recording");

    // Rotate the recorder by advancing 8s. The first recorder stops (emitting
    // data + onstop), which queues an upload, then a fresh recorder starts.
    await vi.advanceTimersByTimeAsync(8000);
    // Let the createIngestion + uploadToPresigned promises settle.
    await vi.runOnlyPendingTimersAsync();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    // First chunk should be at least submitted; states observed include
    // recording -> uploading -> transcribing in order.
    const seenForChunk0 = updates.filter((u) => u.index === 0).map((u) => u.status);
    expect(seenForChunk0).toContain("recording");
    expect(seenForChunk0).toContain("uploading");
    expect(seenForChunk0).toContain("transcribing");

    // Drive polling: first poll returns pending, second returns succeeded.
    await vi.advanceTimersByTimeAsync(150);
    await Promise.resolve();
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(150);
    await Promise.resolve();
    await Promise.resolve();

    const chunk0 = session.chunks.find((c) => c.index === 0);
    expect(chunk0).toBeDefined();
    expect(chunk0?.status).toBe("complete");
    expect(chunk0?.transcript).toBe("the cat sat");
    expect(seenForChunk0).toContain("recording");

    await session.stop();
  });

  it("stops polling after transcript_status becomes succeeded", async () => {
    const stream = makeFakeStream();
    const { factory } = makeRecorderFactory();
    const fetchIngestion = vi
      .fn()
      .mockResolvedValue(makeIngestionRecord("ing-1", "succeeded", "done"));

    const session = new RealtimeSession({
      chunkSeconds: 8,
      pollIntervalMs: 50,
      deps: {
        getUserMedia: vi.fn().mockResolvedValue(stream as unknown as MediaStream),
        mediaRecorderFactory: factory,
        createIngestion: vi.fn().mockResolvedValue(makeIngestionResponse("ing-1")) as never,
        uploadToPresigned: vi.fn().mockResolvedValue(undefined) as never,
        fetchIngestion: fetchIngestion as never,
      },
    });

    await session.start();
    await vi.advanceTimersByTimeAsync(8000);
    await vi.runOnlyPendingTimersAsync();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    // Advance well past the first poll; succeeded should terminate the loop.
    await vi.advanceTimersByTimeAsync(60);
    await Promise.resolve();
    await Promise.resolve();
    const callsAfterFirst = fetchIngestion.mock.calls.length;
    // Advance another 500ms; no further polls should fire.
    await vi.advanceTimersByTimeAsync(500);
    await Promise.resolve();
    await Promise.resolve();
    expect(fetchIngestion.mock.calls.length).toBe(callsAfterFirst);

    const chunk = session.chunks[0];
    expect(chunk.status).toBe("complete");

    await session.stop();
  });

  it("stops polling after transcript_status becomes failed and populates errorMessage", async () => {
    const stream = makeFakeStream();
    const { factory } = makeRecorderFactory();
    const fetchIngestion = vi
      .fn()
      .mockResolvedValue(makeIngestionRecord("ing-x", "failed", undefined, "whisper crashed"));

    const session = new RealtimeSession({
      chunkSeconds: 8,
      pollIntervalMs: 50,
      deps: {
        getUserMedia: vi.fn().mockResolvedValue(stream as unknown as MediaStream),
        mediaRecorderFactory: factory,
        createIngestion: vi.fn().mockResolvedValue(makeIngestionResponse("ing-x")) as never,
        uploadToPresigned: vi.fn().mockResolvedValue(undefined) as never,
        fetchIngestion: fetchIngestion as never,
      },
    });

    await session.start();
    await vi.advanceTimersByTimeAsync(8000);
    await vi.runOnlyPendingTimersAsync();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    await vi.advanceTimersByTimeAsync(60);
    await Promise.resolve();
    await Promise.resolve();
    const callsAfterFirst = fetchIngestion.mock.calls.length;
    await vi.advanceTimersByTimeAsync(500);
    await Promise.resolve();
    await Promise.resolve();
    expect(fetchIngestion.mock.calls.length).toBe(callsAfterFirst);

    const chunk = session.chunks[0];
    expect(chunk.status).toBe("failed");
    expect(chunk.errorMessage).toBe("whisper crashed");

    await session.stop();
  });

  it("stop() finalizes the current chunk and releases stream tracks", async () => {
    const stream = makeFakeStream();
    const { factory } = makeRecorderFactory();
    const session = new RealtimeSession({
      chunkSeconds: 8,
      pollIntervalMs: 50,
      deps: {
        getUserMedia: vi.fn().mockResolvedValue(stream as unknown as MediaStream),
        mediaRecorderFactory: factory,
        createIngestion: vi.fn().mockResolvedValue(makeIngestionResponse("ing-stop")) as never,
        uploadToPresigned: vi.fn().mockResolvedValue(undefined) as never,
        fetchIngestion: vi
          .fn()
          .mockResolvedValue(makeIngestionRecord("ing-stop", "succeeded", "stop test")) as never,
      },
    });

    await session.start();
    // Run for 3s, well under the chunkSeconds rotation.
    await vi.advanceTimersByTimeAsync(3000);
    await session.stop();

    expect(session.isActive).toBe(false);
    // The mock track's stop() must have been called on the MediaStream.
    expect(stream.tracks[0].stop).toHaveBeenCalled();
  });

  it("permission denial calls onError, does not throw", async () => {
    const denial = Object.assign(new Error("Permission denied"), {
      name: "NotAllowedError",
    });
    const onError = vi.fn();
    const session = new RealtimeSession({
      onError,
      deps: {
        getUserMedia: vi.fn().mockRejectedValue(denial),
        mediaRecorderFactory: vi.fn() as never,
        createIngestion: vi.fn() as never,
        uploadToPresigned: vi.fn() as never,
        fetchIngestion: vi.fn() as never,
      },
    });

    await expect(session.start()).resolves.toBeUndefined();
    expect(onError).toHaveBeenCalledOnce();
    expect((onError.mock.calls[0][0] as Error).message).toContain("Permission denied");
    expect(session.isActive).toBe(false);
  });

  it("two consecutive chunks have distinct indexes and filenames", async () => {
    const stream = makeFakeStream();
    const { factory } = makeRecorderFactory();
    const createIngestion = vi
      .fn()
      .mockResolvedValueOnce(makeIngestionResponse("ing-0"))
      .mockResolvedValueOnce(makeIngestionResponse("ing-1"));
    const fetchIngestion = vi.fn().mockResolvedValue(makeIngestionRecord("ing-any", "pending"));

    const session = new RealtimeSession({
      chunkSeconds: 8,
      pollIntervalMs: 1000,
      deps: {
        getUserMedia: vi.fn().mockResolvedValue(stream as unknown as MediaStream),
        mediaRecorderFactory: factory,
        createIngestion: createIngestion as never,
        uploadToPresigned: vi.fn().mockResolvedValue(undefined) as never,
        fetchIngestion: fetchIngestion as never,
      },
    });

    await session.start();
    // Rotate twice: 0 -> 1, 1 -> 2.
    await vi.advanceTimersByTimeAsync(8000);
    await Promise.resolve();
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(8000);
    await Promise.resolve();
    await Promise.resolve();

    expect(session.chunks.length).toBeGreaterThanOrEqual(2);
    const first = session.chunks[0];
    const second = session.chunks[1];
    expect(first.index).toBe(0);
    expect(second.index).toBe(1);
    expect(first.filename).not.toBe(second.filename);
    expect(first.filename).toMatch(/^rt-.*-chunk-0\.webm$/);
    expect(second.filename).toMatch(/^rt-.*-chunk-1\.webm$/);

    await session.stop();
  });
});
