<script lang="ts">
  // Static "no admin access" page. The layout's auth+role gate
  // redirects authenticated non-admins here so they see a clean
  // explanation instead of a dashboard shell full of 403 toasts.
  //
  // Listed under PUBLIC_PATHS in `+layout.svelte` so the gate does
  // not recursively try to bounce the user elsewhere.
  import { Button } from "$lib/components/ui/button";
  import { signOut } from "$lib/auth.svelte";
  import { goto } from "$app/navigation";

  async function handleSignOut() {
    const serverRevoke = signOut();
    await goto("/login", { replaceState: true });
    await serverRevoke;
  }
</script>

<svelte:head>
  <title>Access denied · Panakoes Admin</title>
</svelte:head>

<section
  class="mx-auto flex max-w-md flex-col items-center gap-4 py-16 text-center"
  data-testid="forbidden-page"
>
  <h1 class="text-2xl font-semibold">No admin access</h1>
  <p class="text-muted-foreground">
    You don't have admin access. Contact the workspace owner to request
    a role change, then sign in again.
  </p>
  <Button variant="outline" size="sm" onclick={handleSignOut}>
    Sign out
  </Button>
</section>
