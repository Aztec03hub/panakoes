<script lang="ts">
  import { goto } from "$app/navigation";
  import { onDestroy } from "svelte";
  import Loader2 from "@lucide/svelte/icons/loader-2";
  import Mic from "@lucide/svelte/icons/mic";
  import Square from "@lucide/svelte/icons/square";
  import Upload from "@lucide/svelte/icons/upload";
  import { Button } from "$lib/components/ui/button";
  import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
  } from "$lib/components/ui/card";
  import { ApiError, createIngestion, uploadToPresigned } from "$lib/api";

  /**
   * Maximum upload size enforced client-side. The ingestion API also
   * caps `size_bytes <= 10 GiB` (Pydantic Field upper bound) and the
   * pre-signed URL binds the size into its SigV4 signature. The
   * client-side cap is a UX guard, not the source of truth.
   */
  const MAX_UPLOAD_BYTES = 200 * 1024 * 1024; // 200 MB

  /** Accepted audio MIME types. Whisper handles way more, but the
   * common-case set keeps the browser file picker tight. */
  const ACCEPTED_TYPES = [
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
    "audio/ogg",
    "audio/flac",
    "audio/x-m4a",
  ];

  /**
   * The browser's MediaRecorder picks the codec it knows best. WebM/Opus is
   * the consistent winner in Chromium and Firefox; Whisper accepts WebM
   * fine via ffmpeg. The recorder produces a single Blob; we wrap it in a
   * File at submit time with a sane filename so the ingestion-api row
   * carries a recognizable name.
   */
  const RECORDING_MIME = "audio/webm;codecs=opus";

  type Mode = "upload" | "record";

  let mode = $state<Mode>("upload");
  let selectedFile = $state<File | null>(null);
  let submitting = $state(false);
  let statusMessage = $state("");
  let errorMessage = $state("");

  // Recorder state
  let recorder = $state<MediaRecorder | null>(null);
  let recordedChunks: BlobPart[] = [];
  let recordedBlob = $state<Blob | null>(null);
  let recordingSeconds = $state(0);
  let recordingTimer: ReturnType<typeof setInterval> | null = null;
  let recordedAudioUrl = $state<string | null>(null);

  function fmtBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  function fmtSeconds(s: number): string {
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${r.toString().padStart(2, "0")}`;
  }

  function clearError() {
    errorMessage = "";
    statusMessage = "";
  }

  function setMode(m: Mode) {
    if (mode === m) return;
    if (recorder?.state === "recording") {
      stopRecording();
    }
    if (recordedAudioUrl) {
      URL.revokeObjectURL(recordedAudioUrl);
      recordedAudioUrl = null;
    }
    recordedBlob = null;
    selectedFile = null;
    recordingSeconds = 0;
    clearError();
    mode = m;
  }

  function onFileChange(event: Event) {
    const input = event.target as HTMLInputElement;
    selectedFile = input.files && input.files.length > 0 ? input.files[0] : null;
    clearError();
  }

  // ---------------------------------------------------------------------------
  // Recording flow
  //
  // The MediaRecorder API records mic input to a Blob in one of the codecs
  // the browser supports. We accumulate chunks during the session and
  // assemble them on stop. The user can play back, re-record, or submit.
  // ---------------------------------------------------------------------------

  async function startRecording() {
    clearError();
    recordedBlob = null;
    if (recordedAudioUrl) {
      URL.revokeObjectURL(recordedAudioUrl);
      recordedAudioUrl = null;
    }
    recordedChunks = [];
    recordingSeconds = 0;

    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      errorMessage =
        "Your browser does not support audio recording. Use the upload tab to send a file instead.";
      return;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      errorMessage =
        msg.toLowerCase().includes("permission") || msg.toLowerCase().includes("denied")
          ? "Microphone access denied. Allow microphone permission in your browser and try again."
          : `Could not access microphone: ${msg}`;
      return;
    }

    const supportsOpus =
      typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(RECORDING_MIME);
    const mimeType = supportsOpus ? RECORDING_MIME : undefined;
    recorder = mimeType
      ? new MediaRecorder(stream, { mimeType })
      : new MediaRecorder(stream);

    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        recordedChunks.push(event.data);
      }
    };
    recorder.onstop = () => {
      const finalMime = recorder?.mimeType || mimeType || "audio/webm";
      recordedBlob = new Blob(recordedChunks, { type: finalMime });
      if (recordedAudioUrl) URL.revokeObjectURL(recordedAudioUrl);
      recordedAudioUrl = URL.createObjectURL(recordedBlob);
      stream.getTracks().forEach((t) => t.stop());
      if (recordingTimer !== null) {
        clearInterval(recordingTimer);
        recordingTimer = null;
      }
    };

    recorder.start();
    recordingTimer = setInterval(() => {
      recordingSeconds += 1;
    }, 1000);
  }

  function stopRecording() {
    if (recorder && recorder.state === "recording") {
      recorder.stop();
    }
  }

  function resetRecording() {
    if (recorder?.state === "recording") stopRecording();
    if (recordedAudioUrl) {
      URL.revokeObjectURL(recordedAudioUrl);
      recordedAudioUrl = null;
    }
    recordedBlob = null;
    recordingSeconds = 0;
    clearError();
  }

  onDestroy(() => {
    if (recorder?.state === "recording") {
      recorder.stop();
    }
    if (recordingTimer !== null) clearInterval(recordingTimer);
    if (recordedAudioUrl) URL.revokeObjectURL(recordedAudioUrl);
  });

  // ---------------------------------------------------------------------------
  // Submit (upload + record share this path)
  // ---------------------------------------------------------------------------

  function buildFile(): File | null {
    if (mode === "upload") return selectedFile;
    if (mode === "record" && recordedBlob) {
      const ext = recordedBlob.type.includes("webm") ? "webm" : recordedBlob.type.includes("ogg") ? "ogg" : "mp4";
      const ts = new Date().toISOString().replace(/[:.]/g, "-");
      return new File([recordedBlob], `recording-${ts}.${ext}`, { type: recordedBlob.type });
    }
    return null;
  }

  async function onSubmit(event: SubmitEvent) {
    event.preventDefault();
    const file = buildFile();
    if (submitting || !file) return;
    submitting = true;
    errorMessage = "";
    statusMessage = "";

    if (file.size > MAX_UPLOAD_BYTES) {
      errorMessage = `File too large (${fmtBytes(file.size)}). Max is ${fmtBytes(MAX_UPLOAD_BYTES)}.`;
      submitting = false;
      return;
    }

    try {
      statusMessage = "Requesting pre-signed upload URL...";
      const ingestion = await createIngestion({
        filename: file.name,
        content_type: file.type || "application/octet-stream",
        size_bytes: file.size,
      });

      statusMessage = `Uploading ${fmtBytes(file.size)} to S3...`;
      await uploadToPresigned(ingestion.upload_url, file);

      statusMessage = "Upload complete. Redirecting to transcript view...";
      await new Promise((resolve) => setTimeout(resolve, 600));
      await goto(`/ingestion/${encodeURIComponent(ingestion.ingestion_id)}`);
    } catch (err) {
      if (err instanceof ApiError) {
        errorMessage = `Upload failed: HTTP ${err.status} at ${err.url}`;
      } else {
        errorMessage = err instanceof Error ? err.message : "Unknown error";
      }
      statusMessage = "";
    } finally {
      submitting = false;
    }
  }
</script>

<svelte:head>
  <title>Upload audio · Panakoes</title>
</svelte:head>

<div class="flex justify-center py-8">
  <Card class="w-full max-w-xl">
    <CardHeader>
      <CardTitle>Add audio</CardTitle>
      <CardDescription>
        Upload a file or record live from your microphone. The dashboard
        requests a pre-signed S3 PUT from <code>ingestion-api</code>, uploads
        the bytes directly to S3, and then redirects to a status page where
        the transcript and AI summary appear as soon as
        <code>transcribe-worker</code> and <code>summarization</code> finish.
      </CardDescription>
    </CardHeader>
    <CardContent>
      <!-- Tab switcher -->
      <div role="tablist" aria-label="Audio source" class="inline-flex rounded-md border bg-muted p-1 text-sm mb-4">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "upload"}
          tabindex={mode === "upload" ? 0 : -1}
          class="flex items-center gap-2 rounded px-3 py-1.5 transition-colors {mode === 'upload' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
          onclick={() => setMode("upload")}
        >
          <Upload class="h-4 w-4" />
          <span>Upload file</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "record"}
          tabindex={mode === "record" ? 0 : -1}
          class="flex items-center gap-2 rounded px-3 py-1.5 transition-colors {mode === 'record' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
          onclick={() => setMode("record")}
        >
          <Mic class="h-4 w-4" />
          <span>Record live</span>
        </button>
      </div>

      <form onsubmit={onSubmit} class="flex flex-col gap-4">
        {#if mode === "upload"}
          <label class="flex flex-col gap-1 text-sm">
            <span class="font-medium">Audio file</span>
            <input
              type="file"
              accept={ACCEPTED_TYPES.join(",")}
              onchange={onFileChange}
              class="block w-full rounded-md border border-input bg-background px-3 py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-secondary file:px-3 file:py-1 file:text-sm file:font-medium hover:file:bg-secondary/80"
            />
            {#if selectedFile}
              <span class="text-xs text-muted-foreground">
                {selectedFile.name} · {fmtBytes(selectedFile.size)} · {selectedFile.type || "unknown type"}
              </span>
            {/if}
          </label>
        {:else}
          <div class="flex flex-col gap-3">
            <div class="flex items-center justify-center rounded-lg border bg-muted/30 px-6 py-8">
              {#if recorder?.state === "recording"}
                <div class="flex flex-col items-center gap-3">
                  <div class="relative">
                    <span class="absolute inset-0 -m-3 rounded-full bg-destructive/20 animate-ping"></span>
                    <div class="relative flex h-16 w-16 items-center justify-center rounded-full bg-destructive text-destructive-foreground">
                      <Mic class="h-8 w-8" />
                    </div>
                  </div>
                  <span class="text-3xl font-mono font-semibold tabular-nums">{fmtSeconds(recordingSeconds)}</span>
                  <span class="text-xs text-muted-foreground">Recording...</span>
                </div>
              {:else if recordedBlob}
                <div class="flex w-full flex-col items-center gap-3">
                  <span class="text-sm font-medium">Recording ready ({fmtSeconds(recordingSeconds)} · {fmtBytes(recordedBlob.size)})</span>
                  {#if recordedAudioUrl}
                    <audio controls src={recordedAudioUrl} class="w-full"></audio>
                  {/if}
                </div>
              {:else}
                <div class="flex flex-col items-center gap-3 text-muted-foreground">
                  <div class="flex h-16 w-16 items-center justify-center rounded-full bg-secondary">
                    <Mic class="h-8 w-8" />
                  </div>
                  <span class="text-sm">Click Start to record from your microphone</span>
                </div>
              {/if}
            </div>

            <div class="flex flex-wrap gap-2">
              {#if recorder?.state === "recording"}
                <Button type="button" variant="destructive" onclick={stopRecording}>
                  <Square class="mr-2 h-4 w-4" />
                  Stop
                </Button>
              {:else}
                <Button type="button" onclick={startRecording} disabled={submitting}>
                  <Mic class="mr-2 h-4 w-4" />
                  {recordedBlob ? "Re-record" : "Start recording"}
                </Button>
                {#if recordedBlob}
                  <Button type="button" variant="outline" onclick={resetRecording} disabled={submitting}>
                    Discard
                  </Button>
                {/if}
              {/if}
            </div>
          </div>
        {/if}

        <Button type="submit" disabled={!buildFile() || submitting || recorder?.state === "recording"}>
          {#if submitting}
            <Loader2 class="mr-2 h-4 w-4 animate-spin" />
            Uploading...
          {:else}
            {mode === "record" ? "Submit recording for transcription" : "Upload + transcribe"}
          {/if}
        </Button>

        {#if statusMessage}
          <p class="text-sm text-muted-foreground">{statusMessage}</p>
        {/if}
        {#if errorMessage}
          <p class="text-sm text-destructive">{errorMessage}</p>
        {/if}
      </form>

      <p class="mt-6 text-xs text-muted-foreground">
        Accepted upload formats: MP3, MP4/M4A, WAV, WebM, OGG, FLAC. Live recording
        captures WebM/Opus from your microphone. Max {fmtBytes(MAX_UPLOAD_BYTES)}.
        Transcription uses Whisper-large-v3 fp16 on an EC2 g4dn.xlarge Spot GPU
        (AWS Batch). Cold-start adds a few minutes if no recent activity; subsequent
        jobs finish in seconds.
      </p>
    </CardContent>
  </Card>
</div>
