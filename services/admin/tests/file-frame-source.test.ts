import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  FRAME_BYTES,
  FRAME_SAMPLES,
  TARGET_SAMPLE_RATE,
  createFileFrameStarter,
  decodeAudioFile,
  floatToS16leFrames,
} from "../src/lib/file-frame-source";

// ---------------------------------------------------------------------------
// floatToS16leFrames: conversion correctness + slicing edge case
// ---------------------------------------------------------------------------

describe("floatToS16leFrames", () => {
  it("converts a known waveform to s16le with the same clip/round as the worklet", () => {
    // One full frame of distinct, hand-verifiable values.
    const samples = new Float32Array(FRAME_SAMPLES);
    samples[0] = 0; // -> 0
    samples[1] = 1; // clip -> 0x7fff = 32767
    samples[2] = -1; // -> -0x8000 = -32768
    samples[3] = 2; // over-range, clip to 1 -> 32767
    samples[4] = -2; // under-range, clip to -1 -> -32768
    samples[5] = 0.5; // round(0.5 * 32767) = 16384 (round-half-up)
    samples[6] = -0.5; // round(-0.5 * 32768) = -16384
    const frames = floatToS16leFrames(samples);
    expect(frames).toHaveLength(1);
    expect(frames[0].byteLength).toBe(FRAME_BYTES);
    const view = new DataView(frames[0]);
    expect(view.getInt16(0, true)).toBe(0);
    expect(view.getInt16(2, true)).toBe(32767);
    expect(view.getInt16(4, true)).toBe(-32768);
    expect(view.getInt16(6, true)).toBe(32767);
    expect(view.getInt16(8, true)).toBe(-32768);
    expect(view.getInt16(10, true)).toBe(16384);
    expect(view.getInt16(12, true)).toBe(-16384);
  });

  it("zero-pads the final partial frame to a full 6400 bytes", () => {
    // 1.5 frames worth of samples: second frame is half-full and must be
    // zero-padded out to FRAME_SAMPLES.
    const len = FRAME_SAMPLES + FRAME_SAMPLES / 2;
    const samples = new Float32Array(len).fill(1);
    const frames = floatToS16leFrames(samples);
    expect(frames).toHaveLength(2);
    expect(frames[1].byteLength).toBe(FRAME_BYTES);
    const view = new DataView(frames[1]);
    // First half of the final frame is the filled samples (1 -> 32767).
    expect(view.getInt16(0, true)).toBe(32767);
    expect(view.getInt16((FRAME_SAMPLES / 2 - 1) * 2, true)).toBe(32767);
    // Second half is zero-padding.
    expect(view.getInt16((FRAME_SAMPLES / 2) * 2, true)).toBe(0);
    expect(view.getInt16((FRAME_SAMPLES - 1) * 2, true)).toBe(0);
  });

  it("returns no frames for an empty sample buffer", () => {
    expect(floatToS16leFrames(new Float32Array(0))).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// decodeAudioFile: decode + resample mocked at the AudioContext level
// ---------------------------------------------------------------------------

function makeFakeAudioBuffer(durationSec: number, channelData: Float32Array): AudioBuffer {
  return {
    duration: durationSec,
    numberOfChannels: 1,
    length: channelData.length,
    sampleRate: 44_100,
    getChannelData: () => channelData,
  } as unknown as AudioBuffer;
}

describe("decodeAudioFile", () => {
  it("decodes then resamples to 16kHz mono and reports duration + frame count", async () => {
    const decodedData = new Float32Array(44_100).fill(0.25); // 1s at 44.1k
    const decodedBuffer = makeFakeAudioBuffer(1.0, decodedData);
    // Rendered (resampled) buffer: exactly TARGET_SAMPLE_RATE samples for 1s.
    const renderedData = new Float32Array(TARGET_SAMPLE_RATE).fill(0.25);
    const renderedBuffer = makeFakeAudioBuffer(1.0, renderedData);

    const audioContextFactory = () => ({
      decodeAudioData: vi.fn().mockResolvedValue(decodedBuffer),
      close: vi.fn().mockResolvedValue(undefined),
    });
    const offlineAudioContextFactory = vi.fn((_c: number, _l: number, _sr: number) => ({
      createBufferSource: () => ({
        buffer: null,
        connect: vi.fn(),
        start: vi.fn(),
      }),
      destination: {},
      startRendering: vi.fn().mockResolvedValue(renderedBuffer),
    })) as unknown as (c: number, l: number, sr: number) => OfflineAudioContext;

    const file = {
      name: "test.wav",
      size: 123,
      arrayBuffer: async () => new ArrayBuffer(8),
    } as unknown as File;

    const result = await decodeAudioFile(file, {
      audioContextFactory,
      offlineAudioContextFactory,
    });

    expect(result.sampleRate).toBe(TARGET_SAMPLE_RATE);
    expect(result.durationSec).toBe(1.0);
    expect(result.samples.length).toBe(TARGET_SAMPLE_RATE);
    // 16000 / 3200 = 5 frames exactly.
    expect(result.frameCount).toBe(Math.ceil(TARGET_SAMPLE_RATE / FRAME_SAMPLES));
    // Resample requested at the target rate with 1 channel.
    expect(offlineAudioContextFactory).toHaveBeenCalledWith(
      1,
      TARGET_SAMPLE_RATE,
      TARGET_SAMPLE_RATE,
    );
  });
});

// ---------------------------------------------------------------------------
// createFileFrameStarter: cadence, progress, completion callback
// ---------------------------------------------------------------------------

describe("createFileFrameStarter", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("emits one frame per tick at 5Hz, reports progress, fires onComplete after the last", async () => {
    // 2.5 frames worth -> 3 frames (last zero-padded).
    const samples = new Float32Array(FRAME_SAMPLES * 2 + FRAME_SAMPLES / 2).fill(0.5);
    const emitted: ArrayBuffer[] = [];
    const progress: Array<[number, number]> = [];
    let completeCalls = 0;

    const starter = createFileFrameStarter(samples, {
      onProgress: (sent, total) => progress.push([sent, total]),
      onComplete: () => {
        completeCalls += 1;
      },
    });

    await starter({} as MediaStream, (pcm) => emitted.push(pcm));

    // No frames before the first tick.
    expect(emitted).toHaveLength(0);
    vi.advanceTimersByTime(200);
    expect(emitted).toHaveLength(1);
    expect(progress[progress.length - 1]).toEqual([1, 3]);
    vi.advanceTimersByTime(200);
    expect(emitted).toHaveLength(2);
    vi.advanceTimersByTime(200);
    expect(emitted).toHaveLength(3);
    expect(progress[progress.length - 1]).toEqual([3, 3]);
    expect(completeCalls).toBe(1);

    // Each emitted frame is a full 6400-byte s16le buffer.
    for (const frame of emitted) {
      expect(frame.byteLength).toBe(FRAME_BYTES);
    }
    // No further ticks fire after completion.
    vi.advanceTimersByTime(1000);
    expect(emitted).toHaveLength(3);
    expect(completeCalls).toBe(1);
  });

  it("stop() cancels the interval mid-stream", async () => {
    const samples = new Float32Array(FRAME_SAMPLES * 5).fill(0.1);
    const emitted: ArrayBuffer[] = [];
    let completeCalls = 0;
    const starter = createFileFrameStarter(samples, {
      onComplete: () => {
        completeCalls += 1;
      },
    });
    const controller = await starter({} as MediaStream, (pcm) => emitted.push(pcm));
    vi.advanceTimersByTime(400); // 2 frames
    expect(emitted).toHaveLength(2);
    await controller.stop();
    vi.advanceTimersByTime(2000);
    // No more frames after stop, and onComplete never fired (interrupted).
    expect(emitted).toHaveLength(2);
    expect(completeCalls).toBe(0);
  });

  it("completes immediately for an empty sample buffer without emitting frames", async () => {
    const emitted: ArrayBuffer[] = [];
    let completeCalls = 0;
    const starter = createFileFrameStarter(new Float32Array(0), {
      onComplete: () => {
        completeCalls += 1;
      },
    });
    await starter({} as MediaStream, (pcm) => emitted.push(pcm));
    // onComplete is scheduled as a microtask.
    await Promise.resolve();
    expect(emitted).toHaveLength(0);
    expect(completeCalls).toBe(1);
  });
});
