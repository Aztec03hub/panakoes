/**
 * Main-thread bootstrap for the PCM-frame AudioWorklet.
 *
 * Creates an AudioContext at sampleRate=16000 (Chromium + Firefox accept
 * this directly; Safari needs polyfilling that v1 doesn't ship per
 * `docs/design/realtime-streaming-transcription.md`), loads the worklet
 * module from `/audio-worklet-processor.js`, wires the worklet node into
 * the mic MediaStreamSource, and forwards each posted Frame back to the
 * caller via `onFrame`.
 *
 * The module is intentionally tiny: it owns the AudioContext lifecycle
 * so the StreamingSession can `start()` + `stop()` it as one unit without
 * the caller having to juggle Worklet, AudioContext, and MediaStreamSource
 * separately. Dependency-injected end-to-end so unit tests can drive it
 * with mocks (no real `AudioWorklet` exists in jsdom).
 */

export interface AudioWorkletControllerDeps {
  /** Factory for AudioContext. Defaults to `globalThis.AudioContext`. Tests
   *  inject a mock that records the requested sampleRate and exposes a
   *  shimmed audioWorklet + createMediaStreamSource. */
  audioContextFactory?: (opts: AudioContextOptions) => AudioContext;
}

export interface AudioWorkletController {
  /** Stop the worklet, disconnect the source, close the AudioContext. */
  stop(): Promise<void>;
}

/**
 * Start the AudioWorklet pipeline.
 *
 * Resolves once the worklet module loads and the worklet node is wired
 * to the mic stream; from that moment the `onFrame` callback fires once
 * per 200 ms PCM frame (3200 samples s16le = 6400 bytes).
 *
 * The worklet posts the PCM as a transferable ArrayBuffer; the controller
 * passes the buffer to `onFrame` verbatim so the caller can base64-encode
 * it cheaply without an extra copy.
 *
 * @param stream The microphone MediaStream obtained from `getUserMedia`.
 * @param onFrame Callback invoked once per 6400-byte PCM frame.
 * @param deps Injectable dependencies for tests.
 */
export async function startAudioWorklet(
  stream: MediaStream,
  onFrame: (pcm: ArrayBuffer) => void,
  deps: AudioWorkletControllerDeps = {},
): Promise<AudioWorkletController> {
  const factory =
    deps.audioContextFactory ??
    ((opts: AudioContextOptions) =>
      new (
        globalThis as unknown as { AudioContext: new (o: AudioContextOptions) => AudioContext }
      ).AudioContext(opts));

  const audioContext = factory({ sampleRate: 16000 });
  await audioContext.audioWorklet.addModule("/audio-worklet-processor.js");
  const workletNode = new (
    globalThis as unknown as {
      AudioWorkletNode: new (ctx: AudioContext, name: string) => AudioWorkletNode;
    }
  ).AudioWorkletNode(audioContext, "pcm-frame-processor");
  workletNode.port.onmessage = (event: MessageEvent) => {
    const data = event.data as { type?: string; pcm?: ArrayBuffer };
    if (data?.type === "pcm-frame" && data.pcm !== undefined) {
      onFrame(data.pcm);
    }
  };
  const source = audioContext.createMediaStreamSource(stream);
  source.connect(workletNode);
  // Worklet does not need to be heard; do NOT connect to destination.

  let stopped = false;
  return {
    async stop() {
      if (stopped) {
        return;
      }
      stopped = true;
      try {
        workletNode.port.onmessage = null;
        workletNode.disconnect();
      } catch {
        // Disconnect can throw if the node was never connected; harmless.
      }
      try {
        source.disconnect();
      } catch {
        // Same.
      }
      try {
        await audioContext.close();
      } catch {
        // Close can fail on a context that the platform already torn down;
        // harmless.
      }
    },
  };
}
