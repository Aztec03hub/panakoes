import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AudioWorkletController } from "../src/lib/audio-worklet";
import {
  type LogEntry,
  StreamingSessionImpl,
  createStreamingSession,
} from "../src/lib/streaming-session";

// ---------------------------------------------------------------------------
// Test helpers: fake WebSocket + MediaStream + AudioWorklet controller
//
// jsdom does not ship WebSocket or AudioWorklet, and `getUserMedia` is
// stubbed out. We synthesize the minimum surface streaming-session.ts
// touches: open/onmessage/onerror/onclose, send(), close(), readyState.
// ---------------------------------------------------------------------------

interface SentMessage {
  action: string;
  [k: string]: unknown;
}

interface FakeWebSocket {
  readyState: number;
  send: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  /** Test helpers */
  url: string;
  sentMessages: SentMessage[];
  emitOpen: () => void;
  emitMessage: (data: unknown) => void;
  emitClose: () => void;
}

function makeFakeWebSocketFactory(): {
  factory: (url: string) => WebSocket;
  sockets: FakeWebSocket[];
} {
  const sockets: FakeWebSocket[] = [];
  const factory = (url: string): WebSocket => {
    const sent: SentMessage[] = [];
    const ws: FakeWebSocket = {
      url,
      readyState: 0, // CONNECTING
      send: vi.fn((payload: string) => {
        sent.push(JSON.parse(payload) as SentMessage);
      }),
      close: vi.fn(() => {
        ws.readyState = 3; // CLOSED
      }),
      onopen: null,
      onmessage: null,
      onerror: null,
      onclose: null,
      sentMessages: sent,
      emitOpen: () => {
        ws.readyState = 1; // OPEN
        ws.onopen?.(new Event("open"));
      },
      emitMessage: (data: unknown) => {
        const dataString = typeof data === "string" ? data : JSON.stringify(data);
        ws.onmessage?.({ data: dataString } as MessageEvent);
      },
      emitClose: () => {
        ws.readyState = 3;
        ws.onclose?.(new CloseEvent("close"));
      },
    };
    sockets.push(ws);
    return ws as unknown as WebSocket;
  };
  return { factory, sockets };
}

interface FakeTrack {
  stop: ReturnType<typeof vi.fn>;
}

function makeFakeStream(): MediaStream & { tracks: FakeTrack[] } {
  const tracks: FakeTrack[] = [{ stop: vi.fn() }];
  return {
    tracks,
    getTracks: () => tracks,
  } as unknown as MediaStream & { tracks: FakeTrack[] };
}

interface FakeWorkletHandle {
  emitFrame: (pcm: ArrayBuffer) => void;
  controller: AudioWorkletController;
  stopCalls: number;
}

function makeFakeWorkletStarter(): {
  starter: (
    stream: MediaStream,
    onFrame: (pcm: ArrayBuffer) => void,
  ) => Promise<AudioWorkletController>;
  handle: FakeWorkletHandle;
} {
  const handle: FakeWorkletHandle = {
    emitFrame: () => {
      throw new Error("worklet not started");
    },
    controller: {
      async stop() {
        handle.stopCalls += 1;
      },
    },
    stopCalls: 0,
  };
  const starter = async (
    _stream: MediaStream,
    onFrame: (pcm: ArrayBuffer) => void,
  ): Promise<AudioWorkletController> => {
    handle.emitFrame = (pcm) => onFrame(pcm);
    return handle.controller;
  };
  return { starter, handle };
}

function makePcmBuffer(byteCount = 6400): ArrayBuffer {
  return new ArrayBuffer(byteCount);
}

// Provide WebSocket constants on globalThis (jsdom does not).
const ORIGINAL_WEBSOCKET = (globalThis as { WebSocket?: unknown }).WebSocket;
beforeEach(() => {
  (globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket = {
    CONNECTING: 0,
    OPEN: 1,
    CLOSING: 2,
    CLOSED: 3,
  } as unknown as typeof WebSocket;
});

afterEach(() => {
  (globalThis as unknown as { WebSocket: typeof WebSocket | undefined }).WebSocket =
    ORIGINAL_WEBSOCKET as typeof WebSocket | undefined;
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("StreamingSessionImpl", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("createStreamingSession returns a usable instance with idle status and isRecording=false", () => {
    const session = createStreamingSession({ wsUrl: "wss://test.example/dev" });
    expect(session.status).toBe("idle");
    expect(session.partialText).toBe("");
    expect(session.finalSegments).toEqual([]);
    expect(session.isRecording).toBe(false);
    expect(session.getRecordedBlob()).toBeNull();
  });

  it("start() opens WS with token, transitions connecting -> spawning-gpu, does NOT acquire mic", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const getUserMedia = vi.fn().mockResolvedValue(stream);
    const session = new StreamingSessionImpl({
      wsUrl: "wss://test.example/dev",
      token: "test-jwt-token",
      deps: {
        webSocketFactory: factory,
        getUserMedia,
        startAudioWorklet: starter,
        encodePcm: () => "ENCODED",
      },
    });
    await session.start();
    expect(sockets).toHaveLength(1);
    expect(sockets[0].url).toContain("token=test-jwt-token");
    expect(session.status).toBe("connecting");
    sockets[0].emitOpen();
    expect(session.status).toBe("spawning-gpu");
    // Mic is NOT acquired by start().
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(session.isRecording).toBe(false);
    await session.stop();
  });

  it("startRecording() acquires the mic, sets isRecording true, fires onRecordingChange", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const getUserMedia = vi.fn().mockResolvedValue(stream);
    const recordingEvents: boolean[] = [];
    const session = new StreamingSessionImpl({
      wsUrl: "wss://test.example/dev",
      token: "tok",
      onRecordingChange: (r) => recordingEvents.push(r),
      deps: {
        webSocketFactory: factory,
        getUserMedia,
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    await session.startRecording();
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(session.isRecording).toBe(true);
    expect(recordingEvents).toEqual([true]);
    await session.stop();
  });

  it("stopRecording() stops the worklet, releases mic tracks, keeps WS open", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter, handle } = makeFakeWorkletStarter();
    const session = new StreamingSessionImpl({
      wsUrl: "wss://test.example/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    await session.startRecording();
    await session.stopRecording();
    expect(handle.stopCalls).toBe(1);
    expect(stream.tracks[0].stop).toHaveBeenCalled();
    expect(session.isRecording).toBe(false);
    // WS still open (not closed).
    expect(sockets[0].close).not.toHaveBeenCalled();
    expect(sockets[0].readyState).toBe(1);
    // Session status unchanged (still spawning-gpu or whatever).
    expect(session.status).toBe("spawning-gpu");
    await session.stop();
  });

  it("can stopRecording then startRecording again on the same WS connection", async () => {
    const stream1 = makeFakeStream();
    const stream2 = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter, handle } = makeFakeWorkletStarter();
    const getUserMedia = vi
      .fn<(c: MediaStreamConstraints) => Promise<MediaStream>>()
      .mockResolvedValueOnce(stream1)
      .mockResolvedValueOnce(stream2);
    const session = new StreamingSessionImpl({
      wsUrl: "wss://test.example/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia,
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    await session.startRecording();
    expect(session.isRecording).toBe(true);
    await session.stopRecording();
    expect(session.isRecording).toBe(false);
    await session.startRecording();
    expect(session.isRecording).toBe(true);
    // First worklet was stopped; second was started.
    expect(handle.stopCalls).toBeGreaterThanOrEqual(1);
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    // WS still open across the recording cycle (the whole point of the split).
    expect(sockets).toHaveLength(1);
    expect(sockets[0].close).not.toHaveBeenCalled();
    await session.stop();
  });

  it("start() with parentSessionId and promptSeedText appends both query params", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const session = new StreamingSessionImpl({
      wsUrl: "wss://test.example/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "x",
      },
    });
    await session.start("prev-session-abc", "the cat sat on");
    const url = sockets[0].url;
    expect(url).toContain("parent_session_id=prev-session-abc");
    expect(url).toContain("prompt_seed_text=the+cat+sat+on");
    await session.stop();
  });

  it("promptSeedText longer than 200 chars is truncated", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const session = new StreamingSessionImpl({
      wsUrl: "wss://test.example/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "x",
      },
    });
    const longSeed = "a".repeat(500);
    await session.start("p1", longSeed);
    const url = sockets[0].url;
    const params = new URL(url).searchParams;
    expect(params.get("prompt_seed_text")?.length).toBe(200);
    await session.stop();
  });

  it("captured frames are queued during spawning-gpu and not transmitted", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter, handle } = makeFakeWorkletStarter();
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
        now: () => 1000,
      },
    });
    await session.start();
    sockets[0].emitOpen();
    await session.startRecording();
    handle.emitFrame(makePcmBuffer());
    handle.emitFrame(makePcmBuffer());
    // Frames must be queued, not sent (spawning-gpu).
    const audioSends = sockets[0].sentMessages.filter((m) => m.action === "audio-frame");
    expect(audioSends).toHaveLength(0);
    await session.stop();
  });

  it("frame envelope has shape {action, v, seq, ts_ms_delta, pcm_b64} after ready (JSON+base64 per CRIT-02)", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter, handle } = makeFakeWorkletStarter();
    let nowValue = 0;
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "PAYLOAD_B64",
        now: () => nowValue,
      },
    });
    await session.start();
    sockets[0].emitOpen();
    await session.startRecording();
    sockets[0].emitMessage({ type: "ready" });
    // Empty queue at ready -> transition to transcribing immediately.
    expect(session.status).toBe("transcribing");
    nowValue = 1234;
    handle.emitFrame(makePcmBuffer());
    const audioSends = sockets[0].sentMessages.filter((m) => m.action === "audio-frame");
    expect(audioSends).toHaveLength(1);
    const envelope = audioSends[0] as Record<string, unknown>;
    expect(envelope.action).toBe("audio-frame");
    expect(envelope.v).toBe(1);
    expect(envelope.seq).toBe(0);
    expect(typeof envelope.ts_ms_delta).toBe("number");
    expect(envelope.pcm_b64).toBe("PAYLOAD_B64");
    // Critically: NO binary, NO ArrayBuffer.
    expect(typeof envelope.pcm_b64).toBe("string");
    // Second frame: seq increments.
    handle.emitFrame(makePcmBuffer());
    const audioSends2 = sockets[0].sentMessages.filter((m) => m.action === "audio-frame");
    expect(audioSends2[1].seq).toBe(1);
    await session.stop();
  });

  it("burst flush drains queued frames at 10 Hz on ready (DEG-03)", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter, handle } = makeFakeWorkletStarter();
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
        now: () => 0,
      },
    });
    await session.start();
    sockets[0].emitOpen();
    await session.startRecording();
    // Queue 5 frames during spawning-gpu.
    for (let i = 0; i < 5; i++) {
      handle.emitFrame(makePcmBuffer());
    }
    expect(sockets[0].sentMessages.filter((m) => m.action === "audio-frame")).toHaveLength(0);

    sockets[0].emitMessage({ type: "ready" });
    expect(session.status).toBe("catching-up");

    // 10 Hz = 100 ms period. Advance 100 ms at a time.
    await vi.advanceTimersByTimeAsync(100);
    expect(sockets[0].sentMessages.filter((m) => m.action === "audio-frame")).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(100);
    expect(sockets[0].sentMessages.filter((m) => m.action === "audio-frame")).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(100);
    expect(sockets[0].sentMessages.filter((m) => m.action === "audio-frame")).toHaveLength(3);
    await vi.advanceTimersByTimeAsync(100);
    expect(sockets[0].sentMessages.filter((m) => m.action === "audio-frame")).toHaveLength(4);
    await vi.advanceTimersByTimeAsync(100);
    expect(sockets[0].sentMessages.filter((m) => m.action === "audio-frame")).toHaveLength(5);

    // One more tick drains the queue and transitions to transcribing.
    await vi.advanceTimersByTimeAsync(100);
    expect(session.status).toBe("transcribing");
    await session.stop();
  });

  it("frames captured during catching-up are appended to the queue (live frames during burst)", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter, handle } = makeFakeWorkletStarter();
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
        now: () => 0,
      },
    });
    await session.start();
    sockets[0].emitOpen();
    await session.startRecording();
    for (let i = 0; i < 2; i++) {
      handle.emitFrame(makePcmBuffer());
    }
    sockets[0].emitMessage({ type: "ready" });
    expect(session.status).toBe("catching-up");
    handle.emitFrame(makePcmBuffer()); // live, enqueued mid-burst
    // Drain.
    await vi.advanceTimersByTimeAsync(500);
    const audioCount = sockets[0].sentMessages.filter((m) => m.action === "audio-frame").length;
    expect(audioCount).toBe(3);
    expect(session.status).toBe("transcribing");
    await session.stop();
  });

  it("partial and final messages mutate partialText and finalSegments", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const transcripts: { type: string; text: string }[] = [];
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      onTranscript: (msg) => transcripts.push(msg),
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    sockets[0].emitMessage({ type: "ready" });
    sockets[0].emitMessage({ type: "partial", text: "the cat" });
    expect(session.partialText).toBe("the cat");
    sockets[0].emitMessage({ type: "partial", text: "the cat sat" });
    expect(session.partialText).toBe("the cat sat");
    sockets[0].emitMessage({ type: "final", text: "the cat sat on the mat." });
    expect(session.partialText).toBe("");
    expect(session.finalSegments).toEqual(["the cat sat on the mat."]);
    expect(transcripts).toHaveLength(3);
    expect(transcripts[2]).toEqual({ type: "final", text: "the cat sat on the mat." });
    await session.stop();
  });

  it("partial and final messages still arrive after stopRecording (the user-quoted bug fix)", async () => {
    // The whole point of the recording / session split: the user records
    // 10 seconds, stops recording while the GPU finishes catching up, and
    // STILL receives the transcripts on the live WS.
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    await session.startRecording();
    await session.stopRecording();
    expect(session.isRecording).toBe(false);
    expect(sockets[0].close).not.toHaveBeenCalled();
    sockets[0].emitMessage({ type: "ready" });
    sockets[0].emitMessage({ type: "final", text: "ten seconds of audio." });
    expect(session.finalSegments).toEqual(["ten seconds of audio."]);
    await session.stop();
  });

  it("ping{seq} from GPU triggers an upstream ping-echo with the same seq", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    sockets[0].emitMessage({ type: "ping", seq: 42 });
    const pingEcho = sockets[0].sentMessages.find((m) => m.action === "ping-echo");
    expect(pingEcho).toBeDefined();
    expect(pingEcho?.seq).toBe(42);
    await session.stop();
  });

  it("SPA pings every 60 s during spawning-gpu (HIGH-05)", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    expect(session.status).toBe("spawning-gpu");
    expect(sockets[0].sentMessages.filter((m) => m.action === "ping")).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(sockets[0].sentMessages.filter((m) => m.action === "ping")).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(sockets[0].sentMessages.filter((m) => m.action === "ping")).toHaveLength(2);
    // After ready, the spawn-ping cadence stops.
    sockets[0].emitMessage({ type: "ready" });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(sockets[0].sentMessages.filter((m) => m.action === "ping")).toHaveLength(2);
    await session.stop();
  });

  it("final-chunk reassembles tokens in seq order on ended", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    sockets[0].emitMessage({ type: "ready" });
    // Out-of-order chunks arrive.
    sockets[0].emitMessage({ type: "final-chunk", seq: 1, total: 3, tokens: ["middle"] });
    sockets[0].emitMessage({ type: "final-chunk", seq: 0, total: 3, tokens: ["start"] });
    sockets[0].emitMessage({ type: "final-chunk", seq: 2, total: 3, tokens: ["end"] });
    sockets[0].emitMessage({ type: "ended", expected_chunks: 3, audio_upto: 1234 });
    expect(session.status).toBe("ended");
    expect(session.finalSegments).toContain("start middle end");
    await session.stop();
  });

  it("error message from server transitions to failed and invokes onError", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const errors: Error[] = [];
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      onError: (e) => errors.push(e),
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    sockets[0].emitMessage({ type: "error", code: "spot-interrupted", message: "GPU lost" });
    expect(session.status).toBe("failed");
    expect(errors).toHaveLength(1);
    expect(errors[0].message).toContain("spot-interrupted");
    expect(errors[0].message).toContain("GPU lost");
    await session.stop();
  });

  it("startRecording() rejection from getUserMedia invokes onError and leaves session live", async () => {
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const errors: Error[] = [];
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      onError: (e) => errors.push(e),
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockRejectedValue(new Error("permission denied")),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    await session.startRecording();
    expect(session.isRecording).toBe(false);
    expect(errors[0].message).toBe("permission denied");
    // Session not torn down by a getUserMedia rejection; the WS is still
    // live so the user can retry with permission granted.
    expect(session.status).toBe("spawning-gpu");
    await session.stop();
  });

  it("stop() closes the WS, stops the worklet, releases mic tracks, and flips recording off", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter, handle } = makeFakeWorkletStarter();
    const recordingEvents: boolean[] = [];
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      onRecordingChange: (r) => recordingEvents.push(r),
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    await session.startRecording();
    await session.stop();
    expect(handle.stopCalls).toBe(1);
    expect(sockets[0].close).toHaveBeenCalled();
    expect(stream.tracks[0].stop).toHaveBeenCalled();
    expect(session.status).toBe("ended");
    expect(session.isRecording).toBe(false);
    expect(recordingEvents).toEqual([true, false]);
  });

  it("onclose transitions to ended if not already terminal", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    sockets[0].emitClose();
    expect(session.status).toBe("ended");
  });

  it("status callbacks fire on every transition", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const seen: string[] = [];
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      onStatusChange: (s) => seen.push(s),
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    sockets[0].emitMessage({ type: "ready" });
    await session.stop();
    expect(seen).toContain("connecting");
    expect(seen).toContain("spawning-gpu");
    expect(seen).toContain("transcribing");
    expect(seen).toContain("ended");
  });

  it("malformed JSON in a server message invokes onError without crashing", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const errors: Error[] = [];
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      onError: (e) => errors.push(e),
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    // emitMessage will JSON.stringify; pass a string instead to bypass.
    sockets[0].onmessage?.({ data: "{not json" } as MessageEvent);
    expect(errors.length).toBeGreaterThanOrEqual(1);
    await session.stop();
  });

  it("session-ending-soon is observed without state change", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    sockets[0].emitMessage({ type: "ready" });
    sockets[0].emitMessage({
      type: "session-ending-soon",
      reason: "api-gw-2h-limit",
      warn_at_ms: 5000,
    });
    expect(session.status).toBe("transcribing");
    await session.stop();
  });

  it("getRecordedBlob returns null before any frames are captured", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    expect(session.getRecordedBlob()).toBeNull();
    await session.start();
    sockets[0].emitOpen();
    await session.startRecording();
    expect(session.getRecordedBlob()).toBeNull();
    await session.stop();
  });

  it("getRecordedBlob returns a WAV blob with RIFF/WAVE header and PCM payload after frames are captured", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter, handle } = makeFakeWorkletStarter();
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    await session.startRecording();
    // Emit two frames of 6400 bytes each = 12800 bytes of PCM payload.
    handle.emitFrame(makePcmBuffer());
    handle.emitFrame(makePcmBuffer());
    const blob = session.getRecordedBlob();
    expect(blob).not.toBeNull();
    expect(blob?.type).toBe("audio/wav");
    // 44-byte WAV header + 12800 bytes of PCM = 12844 bytes total.
    expect(blob?.size).toBe(44 + 12_800);
    const bytes = new Uint8Array(await (blob as Blob).arrayBuffer());
    // "RIFF" + size + "WAVE"
    expect(String.fromCharCode(bytes[0], bytes[1], bytes[2], bytes[3])).toBe("RIFF");
    expect(String.fromCharCode(bytes[8], bytes[9], bytes[10], bytes[11])).toBe("WAVE");
    // "fmt " + 16
    expect(String.fromCharCode(bytes[12], bytes[13], bytes[14], bytes[15])).toBe("fmt ");
    // sample rate at offset 24 = 16000 (little-endian).
    const view = new DataView(bytes.buffer);
    expect(view.getUint32(24, true)).toBe(16_000);
    // bits per sample at offset 34 = 16.
    expect(view.getUint16(34, true)).toBe(16);
    // "data"
    expect(String.fromCharCode(bytes[36], bytes[37], bytes[38], bytes[39])).toBe("data");
    expect(view.getUint32(40, true)).toBe(12_800);
    await session.stop();
  });

  it("onLog fires on start() with a session-source 'Opening WebSocket' entry and JWT redaction", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const logs: LogEntry[] = [];
    // Use a JWT-like string distinct enough from the redaction marker
    // that we can prove the raw value does not leak into the event log.
    const secretJwt = "eyJzecretpayload12345";
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: secretJwt,
      onLog: (entry) => logs.push(entry),
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    // The "Opening WebSocket" session entry appears with the redaction
    // in place; the literal JWT must not leak into the log.
    const opening = logs.find((e) => e.message.startsWith("Opening WebSocket"));
    expect(opening).toBeDefined();
    expect(opening?.source).toBe("session");
    expect(opening?.level).toBe("info");
    expect(opening?.message).toContain("token=<redacted>");
    expect(opening?.message).not.toContain(secretJwt);
    sockets[0].emitOpen();
    // ws-source "Connected" entry on handshake.
    const connected = logs.find((e) => e.source === "ws" && e.message.startsWith("Connected"));
    expect(connected).toBeDefined();
    await session.stop();
  });

  it("onLog fires for each received WS message with source 'ws'", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter } = makeFakeWorkletStarter();
    const logs: LogEntry[] = [];
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      onLog: (entry) => logs.push(entry),
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    const beforeCount = logs.filter(
      (e) => e.source === "ws" && e.message.startsWith("Received"),
    ).length;
    sockets[0].emitMessage({ type: "ready" });
    sockets[0].emitMessage({ type: "partial", text: "the cat" });
    sockets[0].emitMessage({ type: "final", text: "the cat sat on the mat." });
    const receivedLogs = logs.filter((e) => e.source === "ws" && e.message.startsWith("Received"));
    // Three new "Received" entries appended (ready, partial, final).
    expect(receivedLogs.length - beforeCount).toBe(3);
    // The partial entry carries the text snippet so the user can read
    // mid-flight transcription progress from the event log.
    expect(
      receivedLogs.some(
        (e) => e.message.includes("type: partial") && e.message.includes("the cat"),
      ),
    ).toBe(true);
    await session.stop();
  });

  it("omitting onLog (undefined) does not crash the session lifecycle", async () => {
    const stream = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter, handle } = makeFakeWorkletStarter();
    // No onLog passed at all: the entire session should run end-to-end
    // without any throw or unhandled rejection, and isRecording / status
    // should still observe the normal transitions.
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia: vi.fn().mockResolvedValue(stream),
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    await session.startRecording();
    sockets[0].emitMessage({ type: "ready" });
    handle.emitFrame(makePcmBuffer());
    sockets[0].emitMessage({ type: "partial", text: "hi" });
    sockets[0].emitMessage({ type: "final", text: "hi there." });
    await session.stopRecording();
    expect(session.status).toBe("transcribing");
    expect(session.finalSegments).toEqual(["hi there."]);
    await session.stop();
    expect(session.status).toBe("ended");
  });

  it("recorded PCM persists across stopRecording / startRecording cycles within a single session", async () => {
    const stream1 = makeFakeStream();
    const stream2 = makeFakeStream();
    const { factory, sockets } = makeFakeWebSocketFactory();
    const { starter, handle } = makeFakeWorkletStarter();
    const getUserMedia = vi
      .fn<(c: MediaStreamConstraints) => Promise<MediaStream>>()
      .mockResolvedValueOnce(stream1)
      .mockResolvedValueOnce(stream2);
    const session = new StreamingSessionImpl({
      wsUrl: "wss://x/dev",
      token: "tok",
      deps: {
        webSocketFactory: factory,
        getUserMedia,
        startAudioWorklet: starter,
        encodePcm: () => "B64",
      },
    });
    await session.start();
    sockets[0].emitOpen();
    await session.startRecording();
    handle.emitFrame(makePcmBuffer());
    await session.stopRecording();
    const blobAfterFirst = session.getRecordedBlob();
    expect(blobAfterFirst?.size).toBe(44 + 6400);
    await session.startRecording();
    handle.emitFrame(makePcmBuffer());
    const blobAfterSecond = session.getRecordedBlob();
    expect(blobAfterSecond?.size).toBe(44 + 12_800);
    await session.stop();
  });
});
