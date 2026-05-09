<script lang="ts">
  import "../app.css";
  import { page } from "$app/stores";
  import { Button } from "$lib/components/ui/button";
  import { signOut } from "$lib/auth";

  /**
   * Build a breadcrumb trail from the current pathname. Splits on '/',
   * strips empties, and renders each segment as a non-link breadcrumb item.
   * Once routing lands on real pages we will swap segments to <a> elements
   * pointing at the parent path.
   */
  $: segments = $page.url.pathname.split("/").filter(Boolean);

  async function handleSignOut() {
    await signOut();
    // Once Better-Auth is wired we will redirect to /login here.
  }
</script>

<div class="min-h-screen bg-background text-foreground flex flex-col">
  <header class="border-b">
    <div class="container flex h-14 items-center justify-between gap-4">
      <a href="/" class="flex items-center gap-2 font-semibold">
        <span class="text-lg">Panakoes</span>
        <span class="text-sm text-muted-foreground">Admin</span>
      </a>
      <nav class="flex items-center gap-4 text-sm" aria-label="Primary">
        <a href="/dashboard" class="text-muted-foreground hover:text-foreground">
          Health
        </a>
        <a href="/cost" class="text-muted-foreground hover:text-foreground">
          Cost
        </a>
        <a href="/lifecycle" class="text-muted-foreground hover:text-foreground">
          Lifecycle
        </a>
        <a href="/audit-log" class="text-muted-foreground hover:text-foreground">
          Audit log
        </a>
        <span class="text-muted-foreground/50">|</span>
        <span aria-label="Breadcrumb" class="flex items-center gap-2">
          {#each segments as segment, i}
            <span aria-hidden="true" class="text-muted-foreground">/</span>
            <span class="text-foreground" data-testid="breadcrumb-segment">
              {segment}
            </span>
            {#if false}{i}{/if}
          {/each}
        </span>
      </nav>
      <Button variant="outline" size="sm" on:click={handleSignOut}>
        Sign Out
      </Button>
    </div>
  </header>
  <main class="container flex-1 py-6">
    <slot />
  </main>
  <footer class="border-t py-4 text-center text-xs text-muted-foreground">
    Panakoes Admin v0.1 (skeleton)
  </footer>
</div>
