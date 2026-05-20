<script lang="ts">
  import { goto } from "$app/navigation";
  import Loader2 from "@lucide/svelte/icons/loader-2";
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

  let selectedFile = $state<File | null>(null);
  let submitting = $state(false);
  let statusMessage = $state("");
  let errorMessage = $state("");

  function fmtBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  function onFileChange(event: Event) {
    const input = event.target as HTMLInputElement;
    selectedFile = input.files && input.files.length > 0 ? input.files[0] : null;
    errorMessage = "";
    statusMessage = "";
  }

  async function onSubmit(event: SubmitEvent) {
    event.preventDefault();
    if (submitting || !selectedFile) return;
    submitting = true;
    errorMessage = "";
    statusMessage = "";
    const file = selectedFile;

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
      // Short pause so the user sees the success state before navigation.
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
      <CardTitle>Upload audio</CardTitle>
      <CardDescription>
        Drop or pick an audio file. The dashboard requests a pre-signed S3 PUT
        from <code>ingestion-api</code>, uploads the bytes directly to S3, and
        then redirects to a status page where the transcript and AI summary
        appear as soon as <code>transcribe-worker</code> and
        <code>summarization</code> finish processing.
      </CardDescription>
    </CardHeader>
    <CardContent>
      <form onsubmit={onSubmit} class="flex flex-col gap-4">
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

        <Button type="submit" disabled={!selectedFile || submitting}>
          {#if submitting}
            <Loader2 class="mr-2 h-4 w-4 animate-spin" />
            Uploading...
          {:else}
            Upload + transcribe
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
        Accepted formats: MP3, MP4/M4A, WAV, WebM, OGG, FLAC. Max
        {fmtBytes(MAX_UPLOAD_BYTES)}. The async transcription path uses
        Whisper-large-v3 on an EC2 g4dn.xlarge Spot GPU; cold-start adds a few
        minutes if no recent activity. AI summary uses Claude Haiku by default.
      </p>
    </CardContent>
  </Card>
</div>
