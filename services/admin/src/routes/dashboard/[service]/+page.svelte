<script lang="ts">
  import { onMount } from "svelte";
  import { page } from "$app/stores";
  import { Card, CardContent, CardHeader, CardTitle } from "$lib/components/ui/card";
  import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "$lib/components/ui/table";
  import { Badge } from "$lib/components/ui/badge";
  import HealthBadge from "$lib/components/health-badge.svelte";
  import { fetchServiceDetail, ApiError } from "$lib/api";
  import { formatTimestamp } from "$lib/utils";
  import type { ServiceDetail } from "$lib/types";

  let detail: ServiceDetail | null = null;
  let loading = true;
  let errorMessage: string | null = null;

  // SvelteKit guarantees the [service] param is present when this route
  // matches; non-null assert to satisfy the stricter `string | undefined`
  // typing that landed in @sveltejs/kit 2.59.
  $: serviceId = $page.params.service as string;

  onMount(async () => {
    try {
      detail = await fetchServiceDetail(serviceId);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        // No mock JSON exists for this service yet; surface a friendly hint
        // instead of the raw 404.
        errorMessage = `No detail mock available for "${serviceId}". Add static/dashboard/${serviceId}.json to enable this page.`;
      } else if (err instanceof ApiError) {
        errorMessage = `Detail endpoint returned HTTP ${err.status}`;
      } else {
        errorMessage = err instanceof Error ? err.message : "Unknown error";
      }
    } finally {
      loading = false;
    }
  });

  function levelVariant(level: string): "default" | "secondary" | "destructive" | "warning" {
    switch (level) {
      case "ERROR":
        return "destructive";
      case "WARN":
        return "warning";
      case "INFO":
        return "default";
      default:
        return "secondary";
    }
  }
</script>

<div class="flex flex-col gap-6">
  <div class="flex items-center gap-3">
    <a href="/dashboard" class="text-sm text-muted-foreground hover:text-foreground">
      &larr; All services
    </a>
  </div>

  {#if loading}
    <p class="text-muted-foreground">Loading service detail...</p>
  {:else if errorMessage}
    <p class="text-destructive" role="alert">{errorMessage}</p>
  {:else if detail}
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold tracking-tight">{detail.display_name}</h1>
        <p class="text-sm text-muted-foreground">
          Last check: {formatTimestamp(detail.health.last_check)}
        </p>
      </div>
      <HealthBadge status={detail.health.status} />
    </div>

    <Card>
      <CardHeader>
        <CardTitle tag="h2">Resource usage (mocked)</CardTitle>
      </CardHeader>
      <CardContent>
        <dl class="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div>
            <dt class="text-xs uppercase text-muted-foreground">CPU</dt>
            <dd class="text-2xl font-semibold">{detail.metrics.cpu_percent.toFixed(1)}%</dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-muted-foreground">Memory</dt>
            <dd class="text-2xl font-semibold">
              {detail.metrics.memory_mb} <span class="text-sm font-normal text-muted-foreground">/ {detail.metrics.memory_limit_mb} MB</span>
            </dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-muted-foreground">Status</dt>
            <dd class="mt-1"><HealthBadge status={detail.health.status} /></dd>
          </div>
        </dl>
      </CardContent>
    </Card>

    <Card>
      <CardHeader>
        <CardTitle tag="h2">Recent log entries (mocked)</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead class="w-48">Timestamp</TableHead>
              <TableHead class="w-24">Level</TableHead>
              <TableHead>Message</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {#each detail.recent_logs as entry}
              <TableRow>
                <TableCell class="font-mono text-xs">
                  {formatTimestamp(entry.timestamp)}
                </TableCell>
                <TableCell>
                  <Badge variant={levelVariant(entry.level)}>{entry.level}</Badge>
                </TableCell>
                <TableCell class="font-mono text-xs">{entry.message}</TableCell>
              </TableRow>
            {/each}
          </TableBody>
        </Table>
      </CardContent>
    </Card>

    <Card>
      <CardHeader>
        <CardTitle tag="h2">Recent errors (mocked)</CardTitle>
      </CardHeader>
      <CardContent>
        {#if detail.recent_errors.length === 0}
          <p class="text-sm text-muted-foreground">No recent errors.</p>
        {:else}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead class="w-48">Last seen</TableHead>
                <TableHead class="w-20">Count</TableHead>
                <TableHead>Message</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {#each detail.recent_errors as entry}
                <TableRow>
                  <TableCell class="font-mono text-xs">
                    {formatTimestamp(entry.timestamp)}
                  </TableCell>
                  <TableCell>{entry.count}</TableCell>
                  <TableCell class="font-mono text-xs">{entry.message}</TableCell>
                </TableRow>
              {/each}
            </TableBody>
          </Table>
        {/if}
      </CardContent>
    </Card>
  {/if}
</div>
