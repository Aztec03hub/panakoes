<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "$lib/components/ui/card";

  let email = "";
  let password = "";
  let submitting = false;
  let errorMessage = "";

  /**
   * Placeholder sign-in handler. Posts to the auth service contract
   * (POST /auth/sign-in) but does NOT yet persist the returned JWT or
   * register the session with `lib/auth.ts`. That wiring lands when the
   * Better-Auth Svelte client is added in a follow-up slice.
   */
  async function handleSubmit(event: SubmitEvent) {
    event.preventDefault();
    submitting = true;
    errorMessage = "";
    try {
      const res = await fetch("/auth/sign-in", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        errorMessage = `Sign in failed (HTTP ${res.status})`;
      }
    } catch (err) {
      errorMessage = err instanceof Error ? err.message : "Network error";
    } finally {
      submitting = false;
    }
  }
</script>

<div class="flex min-h-[60vh] items-center justify-center">
  <Card class="w-full max-w-sm">
    <CardHeader>
      <CardTitle>Sign in</CardTitle>
      <CardDescription>
        Use your Panakoes admin account. v0.1 placeholder; not wired yet.
      </CardDescription>
    </CardHeader>
    <CardContent>
      <form class="flex flex-col gap-4" on:submit={handleSubmit}>
        <label class="flex flex-col gap-1 text-sm">
          <span>Email</span>
          <input
            class="rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            type="email"
            name="email"
            autocomplete="email"
            required
            bind:value={email}
          />
        </label>
        <label class="flex flex-col gap-1 text-sm">
          <span>Password</span>
          <input
            class="rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            type="password"
            name="password"
            autocomplete="current-password"
            required
            bind:value={password}
          />
        </label>
        {#if errorMessage}
          <p class="text-sm text-destructive" role="alert">{errorMessage}</p>
        {/if}
        <Button type="submit" disabled={submitting}>
          {submitting ? "Signing in..." : "Sign in"}
        </Button>
      </form>
    </CardContent>
  </Card>
</div>
