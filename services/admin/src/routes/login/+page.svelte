<script lang="ts">
  import { goto } from "$app/navigation";
  import { page } from "$app/stores";
  import { Button } from "$lib/components/ui/button";
  import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
  } from "$lib/components/ui/card";
  import { AuthError, signIn } from "$lib/auth.svelte";

  let email = $state("");
  let password = $state("");
  let submitting = $state(false);
  let errorMessage = $state("");

  /**
   * Sign-in handler.
   *
   * Posts to the auth service via the `signIn` helper in `$lib/auth`,
   * which persists the returned JWT into the session store. On success
   * we navigate to the `from` query param (set by the layout's auth
   * gate when the user was bounced here) or fall back to `/dashboard`.
   * On failure we render the typed error message.
   */
  async function onsubmit(event: SubmitEvent) {
    event.preventDefault();
    if (submitting) {
      return;
    }
    submitting = true;
    errorMessage = "";
    try {
      await signIn(email, password);
      const fromParam = $page.url.searchParams.get("from");
      const target = fromParam !== null && fromParam !== "" ? fromParam : "/dashboard";
      await goto(target, { replaceState: true });
    } catch (err) {
      if (err instanceof AuthError) {
        errorMessage = err.message;
      } else if (err instanceof Error) {
        errorMessage = err.message;
      } else {
        errorMessage = "Sign in failed. Try again.";
      }
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
        Use your Panakoes admin credentials.
      </CardDescription>
    </CardHeader>
    <CardContent>
      <form class="flex flex-col gap-4" {onsubmit}>
        <label class="flex flex-col gap-1 text-sm">
          <span>Email</span>
          <input
            class="rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            type="email"
            name="email"
            autocomplete="username"
            inputmode="email"
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
