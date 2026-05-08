import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Tailwind class composer used by every shadcn-svelte component.
 * Combines `clsx` (for conditional class arrays/objects) with `tailwind-merge`
 * (for resolving conflicts like `p-2 p-4` to `p-4`). Standard shadcn pattern.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Formats an ISO timestamp into a short, locale-aware human string. */
export function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    return iso;
  }
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
