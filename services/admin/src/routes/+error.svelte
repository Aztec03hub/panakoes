<script lang="ts">
  import { page } from "$app/stores";
  import { Button } from "$lib/components/ui/button";
</script>

<div class="container flex flex-col items-center justify-center gap-4 py-24 text-center">
  <h1 class="text-4xl font-bold">{$page.status}</h1>
  <p class="text-lg text-muted-foreground">
    {$page.error?.message ?? "Something went wrong."}
  </p>
  <p class="max-w-md text-sm text-muted-foreground">
    If this keeps happening, check the admin-api logs in CloudWatch or ping
    the on-call rotation. The error has been recorded in the access logs.
  </p>
  <Button href="/dashboard" variant="default">
    Back to dashboard
  </Button>
</div>
