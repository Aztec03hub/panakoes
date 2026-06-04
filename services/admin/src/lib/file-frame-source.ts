/**
 * File-driven PCM frame source for the realtime streaming pipeline.
 *
 * Lets the user transcribe an uploaded audio file through the exact same
 * GPU + WebSocket pipeline the microphone uses, with zero changes to the
 * wire protocol. The trick: {@link StreamingSessionImpl} already accepts an
 * injectable `startAudioWorklet` dependency (see `streaming-session.ts`),
 * a function of shape `(stream, onFrame, deps?) => Promise<controller>`.
 * Instead of wiring a mic + AudioWorklet, we build a controller that reads
 * a decoded audio file, slices it into the same 3200-sample / 6400-byte
 * s16le frames the worklet emits, and posts them to `onFrame` at the same
 * 5 Hz wall-clock cadence the mic would. From the session's point of view
 * this is indistinguishable from a microphone: frames enqueue during
 * `spawning-gpu`, replay on `ready`, and stream live thereafter.
 *
 * Pipeline:
 *   1. `decodeAudioFile(file, deps)` -> AudioBuffer via
 *      `AudioContext.decodeAudioData`, then resample to 16 kHz mono using
 *      an `OfflineAudioContext`. Returns the Float32 mono samples + the
 *      original duration so the page can log frame count and duration.
 *   2. `floatToS16leFrames(samples)` slices the mono Float32 stream into
 *      3200-sample frames (the final partial frame is zero-padded) and
 *      converts each to a 6400-byte little-endian s16le ArrayBuffer,
 *      mirroring the worklet's clip + round conversion exactly.
 *   3. `createFileFrameStarter(...)` returns a `startAudioWorklet`-shaped
 *      function that, when the session invokes it, kicks off a 5 Hz
 *      interval that posts one frame per tick, reports progress, and
 *      fires `onComplete` once the last frame is emitted.
 *
 * Everything is dependency-injected (AudioContext factory, OfflineAudioContext
 * factory, clock/interval) so unit tests can drive it deterministically
 * under `vi.useFakeTimers()` without a real Web Audio implementation.
 */

import type { AudioWorkletController } from "./audio-worklet";

/** Target sample rate of the streaming pipeline (matches the worklet). */
export const TARGET_SAMPLE_RATE = 16_000;
/** Samples per 200 ms frame at 16 kHz (matches the worklet's FRAME_SAMPLES). */
export const FRAME_SAMPLES = 3_200;
/** Bytes per frame: 3200 samples * 2 bytes (s16le). */
export const FRAME_BYTES = FRAME_SAMPLES * 2;
/** Live capture cadence the mic emits at (5 Hz = one 200 ms frame per tick). */
export const FRAME_EMIT_HZ = 5;

/** Result of decoding + resampling an uploaded audio file. */
export interface DecodedAudio {
  /** Mono 16 kHz Float32 samples in the [-1, 1] range. */
  samples: Float32Array;
  /** Original file duration in seconds (before resample, for display). */
  durationSec: number;
  /** Resampled sample rate (always {@link TARGET_SAMPLE_RATE}). */
  sampleRate: number;
  /** Number of 3200-sample frames the samples slice into (ceil). */
  frameCount: number;
}

/** Injectable dependencies for {@link decodeAudioFile}. */
export interface DecodeDeps {
  /** Factory for a decode-capable AudioContext. Defaults to the platform
   *  `AudioContext`. Tests inject a stub whose `decodeAudioData` returns a
   *  known AudioBuffer. */
  audioContextFactory?: () => Pick<AudioContext, "decodeAudioData" | "close">;
  /** Factory for an OfflineAudioContext used to resample to 16 kHz mono.
   *  Defaults to the platform `OfflineAudioContext`. */
  offlineAudioContextFactory?: (
    channels: number,
    length: number,
    sampleRate: number,
  ) => OfflineAudioContext;
}

/**
 * Decode an uploaded {@link File} and resample it to 16 kHz mono Float32.
 *
 * Uses `AudioContext.decodeAudioData` to decode whatever container/codec
 * the browser supports (wav, mp3, m4a, ogg, flac, ...), then renders the
 * decoded buffer through an `OfflineAudioContext` configured at the target
 * sample rate to get a clean mono 16 kHz stream. The OfflineAudioContext
 * handles both downmix-to-mono (via the channel count = 1) and resample
 * in one render pass.
 */
export async function decodeAudioFile(file: File, deps: DecodeDeps = {}): Promise<DecodedAudio> {
  const audioContextFactory =
    deps.audioContextFactory ??
    (() => new (globalThis as unknown as { AudioContext: new () => AudioContext }).AudioContext());
  const offlineFactory =
    deps.offlineAudioContextFactory ??
    ((channels: number, length: number, sampleRate: number) =>
      new (
        globalThis as unknown as {
          OfflineAudioContext: new (c: number, l: number, sr: number) => OfflineAudioContext;
        }
      ).OfflineAudioContext(channels, length, sampleRate));

  const arrayBuffer = await file.arrayBuffer();
  const ctx = audioContextFactory();
  let decoded: AudioBuffer;
  try {
    decoded = await ctx.decodeAudioData(arrayBuffer);
  } finally {
    try {
      await ctx.close();
    } catch {
      // Some contexts cannot be closed twice; harmless.
    }
  }

  const durationSec = decoded.duration;
  // Length of the resampled mono buffer: round so a fractional final
  // sample does not silently truncate audible tail content.
  const targetLength = Math.max(1, Math.round(durationSec * TARGET_SAMPLE_RATE));
  const offline = offlineFactory(1, targetLength, TARGET_SAMPLE_RATE);
  const sourceNode = offline.createBufferSource();
  sourceNode.buffer = decoded;
  sourceNode.connect(offline.destination);
  sourceNode.start();
  const rendered = await offline.startRendering();
  const samples = rendered.getChannelData(0);
  // Copy out of the AudioBuffer-owned storage so the caller holds a stable
  // Float32Array independent of the OfflineAudioContext lifecycle.
  const out = new Float32Array(samples.length);
  out.set(samples);

  const frameCount = Math.max(0, Math.ceil(out.length / FRAME_SAMPLES));
  return {
    samples: out,
    durationSec,
    sampleRate: TARGET_SAMPLE_RATE,
    frameCount,
  };
}

/**
 * Convert mono Float32 samples to an array of 6400-byte s16le frames.
 *
 * Mirrors the worklet's conversion exactly (static/audio-worklet-processor.js):
 *   - clip each sample to [-1, 1]
 *   - negative samples scale by 0x8000, positive by 0x7fff
 *   - round to nearest integer
 *   - write little-endian into a DataView
 * The final frame is zero-padded out to a full 3200 samples so every frame
 * is exactly 6400 bytes, matching the fixed-size frames the worklet emits.
 */
export function floatToS16leFrames(samples: Float32Array): ArrayBuffer[] {
  const frameCount = Math.ceil(samples.length / FRAME_SAMPLES);
  const frames: ArrayBuffer[] = [];
  for (let f = 0; f < frameCount; f++) {
    const pcm = new ArrayBuffer(FRAME_BYTES);
    const view = new DataView(pcm);
    const base = f * FRAME_SAMPLES;
    for (let i = 0; i < FRAME_SAMPLES; i++) {
      const srcIndex = base + i;
      let s = srcIndex < samples.length ? samples[srcIndex] : 0;
      if (s > 1) s = 1;
      else if (s < -1) s = -1;
      const intSample = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
      view.setInt16(i * 2, intSample, true);
    }
    frames.push(pcm);
  }
  return frames;
}

/** Progress + completion callbacks for the file frame starter. */
export interface FileFrameSourceCallbacks {
  /** Fires once per emitted frame with the 1-based count and the total. */
  onProgress?: (sent: number, total: number) => void;
  /** Fires once, right after the last frame is emitted. */
  onComplete?: () => void;
}

/** Injectable timer deps so tests can drive the cadence with fake timers. */
export interface FileFrameStarterDeps {
  /** Interval scheduler; defaults to `setInterval`. */
  setIntervalFn?: (cb: () => void, ms: number) => ReturnType<typeof setInterval>;
  /** Interval canceller; defaults to `clearInterval`. */
  clearIntervalFn?: (handle: ReturnType<typeof setInterval>) => void;
}

/**
 * Build a `startAudioWorklet`-shaped function that streams the decoded file
 * frames into the session's `onFrame` callback at {@link FRAME_EMIT_HZ}.
 *
 * The returned function matches the signature the session injects in place
 * of the real AudioWorklet starter: `(stream, onFrame, deps?) => controller`.
 * The `stream` argument is ignored (there is no mic); `onFrame` is the
 * session's frame sink. Calling `controller.stop()` cancels the interval,
 * so the session's normal teardown path stops the file stream cleanly.
 *
 * Frames are pre-sliced from `samples` so the per-tick cost is just a
 * postMessage-equivalent callback, keeping the cadence accurate.
 */
export function createFileFrameStarter(
  samples: Float32Array,
  callbacks: FileFrameSourceCallbacks = {},
  deps: FileFrameStarterDeps = {},
): (stream: MediaStream, onFrame: (pcm: ArrayBuffer) => void) => Promise<AudioWorkletController> {
  const setIntervalFn = deps.setIntervalFn ?? ((cb, ms) => setInterval(cb, ms));
  const clearIntervalFn = deps.clearIntervalFn ?? ((h) => clearInterval(h));
  const frames = floatToS16leFrames(samples);
  const total = frames.length;
  const periodMs = Math.floor(1000 / FRAME_EMIT_HZ);

  return async (
    _stream: MediaStream,
    onFrame: (pcm: ArrayBuffer) => void,
  ): Promise<AudioWorkletController> => {
    let index = 0;
    let completed = false;
    let handle: ReturnType<typeof setInterval> | null = null;

    const finish = (): void => {
      if (handle !== null) {
        clearIntervalFn(handle);
        handle = null;
      }
      if (!completed) {
        completed = true;
        callbacks.onComplete?.();
      }
    };

    if (total === 0) {
      // Nothing to stream; complete on the next microtask so the caller's
      // controller is returned first (mirrors the async tick of a real
      // stream that emits then completes).
      queueMicrotask(finish);
    } else {
      handle = setIntervalFn(() => {
        if (index >= frames.length) {
          finish();
          return;
        }
        const pcm = frames[index];
        index += 1;
        onFrame(pcm);
        callbacks.onProgress?.(index, total);
        if (index >= frames.length) {
          finish();
        }
      }, periodMs);
    }

    return {
      async stop() {
        if (handle !== null) {
          clearIntervalFn(handle);
          handle = null;
        }
      },
    };
  };
}
