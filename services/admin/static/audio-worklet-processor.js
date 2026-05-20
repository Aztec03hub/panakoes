/**
 * AudioWorkletProcessor that captures 200 ms frames of 16 kHz mono signed
 * 16-bit PCM and posts each frame back to the main thread.
 *
 * The AudioContext is created at sampleRate=16000 in the main thread
 * (lib/audio-worklet.ts), so this processor sees inputs that are already
 * mono 16 kHz Float32 in the [-1, 1] range. No client-side resampling is
 * required here; we only buffer, convert to s16le, and post.
 *
 * Frame size: 16000 samples/sec * 0.2 sec = 3200 samples = 6400 bytes
 * (s16le). Matches the 200 ms cadence the Silero VAD on the GPU side
 * is sized for, per the design doc's "Audio frame format and transport"
 * section.
 *
 * Loaded by the main thread via `audioContext.audioWorklet.addModule(
 * "/audio-worklet-processor.js")`. Lives under `static/` so SvelteKit's
 * adapter-static includes it at the bundle root with no Vite import-rewrite.
 *
 * Posted message shape:
 *   { type: "pcm-frame", pcm: ArrayBuffer of 6400 bytes (s16le) }
 *
 * The main-thread session reads the ArrayBuffer, base64-encodes, and
 * wraps it in the JSON envelope before sending over the WebSocket.
 */

/* global AudioWorkletProcessor, registerProcessor, sampleRate */

const FRAME_SAMPLES = 3200; // 16000 Hz * 0.2 s

class PcmFrameProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(FRAME_SAMPLES);
    this._writeIndex = 0;
  }

  process(inputs) {
    // inputs is [input][channel][sample]. We expect mono so we read channel 0.
    const input = inputs[0];
    if (!input || input.length === 0) {
      return true;
    }
    const channel = input[0];
    if (!channel) {
      return true;
    }

    let read = 0;
    while (read < channel.length) {
      const room = FRAME_SAMPLES - this._writeIndex;
      const toCopy = Math.min(room, channel.length - read);
      for (let i = 0; i < toCopy; i++) {
        this._buffer[this._writeIndex + i] = channel[read + i];
      }
      this._writeIndex += toCopy;
      read += toCopy;
      if (this._writeIndex >= FRAME_SAMPLES) {
        // Convert float32 [-1, 1] to s16le.
        const pcm = new ArrayBuffer(FRAME_SAMPLES * 2);
        const view = new DataView(pcm);
        for (let i = 0; i < FRAME_SAMPLES; i++) {
          let s = this._buffer[i];
          if (s > 1) s = 1;
          else if (s < -1) s = -1;
          // Round-half-to-even is fine; just truncate via integer cast.
          const intSample = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
          view.setInt16(i * 2, intSample, true);
        }
        this.port.postMessage({ type: "pcm-frame", pcm }, [pcm]);
        this._writeIndex = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-frame-processor", PcmFrameProcessor);
