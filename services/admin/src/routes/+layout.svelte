<script lang="ts">
  import "../app.css";
  import { afterNavigate, goto } from "$app/navigation";
  import { page } from "$app/stores";
  import { onMount } from "svelte";
  import { Button } from "$lib/components/ui/button";
  import {
    currentSession,
    isAuthenticated,
    signOut,
    validateSession,
  } from "$lib/auth.svelte";
  import {
    ATTR_USER_ID,
    ATTR_USER_ROLE,
    addAttributes,
    bootstrapOtel,
    getTracer,
    recordException,
  } from "$lib/otel";

  let { children } = $props();

  /**
   * On boot we do three things, all of which must not block the first paint:
   *   1. Re-validate any hydrated localStorage session against the auth
   *      service's GET /auth/me. Catches revoked sessions, signing-key
   *      rotations, and other server-side invalidations that the
   *      stateless JWT-only check cannot see. Runs once per page load.
   *   2. Lazy-bootstrap OTEL. The OTLP exporter + sdk-trace-web bundle is
   *      a dynamic import that stays out of the entry chunk; with the env
   *      var unset the import chain never even fetches. Errors are
   *      swallowed because telemetry must never break the page.
   *   3. Wire global exception capture (window.onerror +
   *      unhandledrejection) so uncaught errors surface as exception
   *      events on freshly-minted spans, alongside fetch + nav spans.
   */
  onMount(() => {
    void validateSession();
    void bootstrapOtel().catch((err) => {
      console.warn("otel bootstrap failed", err);
    });

    const onError = (event: ErrorEvent): void => {
      const span = getTracer().startSpan("window.onerror");
      recordException(span, event.error ?? event.message ?? "error");
      span.end();
    };
    const onRejection = (event: PromiseRejectionEvent): void => {
      const span = getTracer().startSpan("window.unhandledrejection");
      recordException(span, event.reason ?? "unhandledrejection");
      span.end();
    };
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  });

  // SvelteKit `afterNavigate` fires after the client-side router commits
  // a new route; emit one span per transition with the user-id
  // correlation when a session exists. Span lifetime is short by design,
  // it exists primarily to anchor the trace tree for any data-fetches
  // the destination page kicks off in its own `$effect`.
  afterNavigate((nav) => {
    const fromPath = nav.from?.url.pathname ?? "(initial)";
    const toPath = nav.to?.url.pathname ?? "(unknown)";
    const span = getTracer().startSpan(`route.${fromPath} -> ${toPath}`);
    addAttributes(span, {
      "panakoes.route.from": fromPath,
      "panakoes.route.to": toPath,
    });
    const session = currentSession.value;
    if (session !== null) {
      addAttributes(span, {
        [ATTR_USER_ID]: session.user.id,
        [ATTR_USER_ROLE]: session.user.role,
        "enduser.id": session.user.id,
      });
    }
    span.end();
  });

  /**
   * Build a breadcrumb trail from the current pathname. Splits on '/',
   * strips empties, and renders each segment as a non-link breadcrumb
   * item.
   */
  const segments = $derived($page.url.pathname.split("/").filter(Boolean));

  /** Routes the auth gate should NOT redirect away from. */
  const PUBLIC_PATHS = new Set<string>(["/login"]);

  function isPublic(path: string): boolean {
    return PUBLIC_PATHS.has(path);
  }

  /**
   * Auth gate. Runs whenever the current path changes. If the route
   * requires auth AND the user is not signed in, redirect to /login
   * with a `?from=` param so a successful sign-in returns them here.
   */
  $effect(() => {
    const pathname = $page.url.pathname;
    if (isPublic(pathname)) {
      return;
    }
    if (!isAuthenticated()) {
      const from = encodeURIComponent(pathname);
      void goto(`/login?from=${from}`, { replaceState: true });
    }
  });

  async function handleSignOut() {
    // Fire the local clear first (synchronous: store + localStorage)
    // then await the best-effort server revoke. The local clear is what
    // gates UX; the server call is durable revocation we don't want the
    // user blocked on.
    const serverRevoke = signOut();
    await goto("/login", { replaceState: true });
    await serverRevoke;
  }

  const signedIn = $derived(isAuthenticated());
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
          {#each segments as segment}
            <span aria-hidden="true" class="text-muted-foreground">/</span>
            <span class="text-foreground" data-testid="breadcrumb-segment">
              {segment}
            </span>
          {/each}
        </span>
      </nav>
      {#if signedIn}
        <Button variant="outline" size="sm" on:click={handleSignOut}>
          Sign Out
        </Button>
      {:else}
        <span class="text-xs text-muted-foreground">Signed out</span>
      {/if}
    </div>
  </header>
  <main class="container flex-1 py-6">
    {@render children?.()}
  </main>
  <footer class="border-t py-4 text-center text-xs text-muted-foreground">
    Panakoes Admin v0.1
  </footer>
</div>
