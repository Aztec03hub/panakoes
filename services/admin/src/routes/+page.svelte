<script lang="ts">
  import { onMount } from "svelte";
  import { goto } from "$app/navigation";

  // Landing page: redirect to /dashboard on mount. We do this client-side
  // because the static adapter cannot perform a server redirect.
  onMount(() => {
    goto("/dashboard", { replaceState: true });
  });
</script>

<div class="flex h-64 items-center justify-center text-muted-foreground">
  Redirecting to dashboard...
</div>
