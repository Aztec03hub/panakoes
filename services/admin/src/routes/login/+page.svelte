<script lang="ts">
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import Loader2 from "@lucide/svelte/icons/loader-2";
  import { Button } from "$lib/components/ui/button";
  import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
  } from "$lib/components/ui/card";
  import { AuthError, signIn } from "$lib/auth.svelte";

  /**
   * Client-side timeout for the sign-in fetch. Long enough that a normal
   * warm response (~500ms) plus a worst-case ECS Fargate cold start
   * (~12s) still completes, but short enough that a truly hung backend
   * surfaces a clear error to the user instead of an indefinite spinner.
   * The 11.6s cold-start measurement that motivated this value is
   * documented in the PR description.
   */
  const SIGN_IN_TIMEOUT_MS = 30_000;

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
   *
   * Wraps the fetch in an AbortController + setTimeout so a wedged
   * backend produces a clean "Sign in timed out" error instead of a
   * spinner that never resolves. The handler also records its own
   * click-to-resolve wall-clock duration to `console.debug` so Phil
   * can validate UX latency in DevTools without redeploying.
   */
  async function onsubmit(event: SubmitEvent) {
    event.preventDefault();
    if (submitting) {
      return;
    }
    submitting = true;
    errorMessage = "";
    const t0 = performance.now();
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), SIGN_IN_TIMEOUT_MS);
    try {
      const timedFetcher: typeof globalThis.fetch = (input, init) =>
        globalThis.fetch(input, { ...init, signal: controller.signal });
      await signIn(email, password, timedFetcher);
      const elapsed = Math.round(performance.now() - t0);
      console.debug(`[login] signIn ok in ${elapsed}ms`);
      const fromParam = page.url.searchParams.get("from");
      const target = fromParam !== null && fromParam !== "" ? fromParam : "/dashboard";
      await goto(target, { replaceState: true });
    } catch (err) {
      const elapsed = Math.round(performance.now() - t0);
      console.debug(`[login] signIn failed in ${elapsed}ms`, err);
      if (err instanceof DOMException && err.name === "AbortError") {
        errorMessage = "Sign in timed out. The auth service may be starting up; try again.";
      } else if (err instanceof AuthError) {
        errorMessage = err.message;
      } else if (err instanceof Error) {
        errorMessage = err.message;
      } else {
        errorMessage = "Sign in failed. Try again.";
      }
    } finally {
      clearTimeout(timeoutId);
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
            class="rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
            type="email"
            name="email"
            autocomplete="username"
            inputmode="email"
            required
            disabled={submitting}
            bind:value={email}
          />
        </label>
        <label class="flex flex-col gap-1 text-sm">
          <span>Password</span>
          <input
            class="rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
            type="password"
            name="password"
            autocomplete="current-password"
            required
            disabled={submitting}
            bind:value={password}
          />
        </label>
        {#if errorMessage}
          <p class="text-sm text-destructive" role="alert">{errorMessage}</p>
        {/if}
        <Button type="submit" disabled={submitting} data-testid="login-submit">
          {#if submitting}
            <Loader2 class="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
            <span>Signing in...</span>
          {:else}
            <span>Sign in</span>
          {/if}
        </Button>
        {#if submitting}
          <p class="text-xs text-muted-foreground text-center" aria-live="polite">
            First sign-in of the day can take up to 15 seconds while the auth service warms up.
          </p>
        {/if}
      </form>
    </CardContent>
  </Card>
</div>
