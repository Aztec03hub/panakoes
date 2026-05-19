<script lang="ts" module>
  import { tv, type VariantProps } from "tailwind-variants";

  export const buttonVariants = tv({
    base:
      "inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        destructive:
          "bg-destructive text-destructive-foreground hover:bg-destructive/90",
        outline:
          "border border-input bg-background hover:bg-accent hover:text-accent-foreground",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-9 rounded-md px-3",
        lg: "h-11 rounded-md px-8",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  });

  export type ButtonVariant = VariantProps<typeof buttonVariants>["variant"];
  export type ButtonSize = VariantProps<typeof buttonVariants>["size"];
</script>

<script lang="ts">
  import type { Snippet } from "svelte";
  import type {
    HTMLAnchorAttributes,
    HTMLButtonAttributes,
  } from "svelte/elements";
  import { cn } from "$lib/utils";

  // Migrated to Svelte 5 runes. Renders an <a> when `href` is provided,
  // otherwise a <button>. Both branches forward arbitrary HTML attributes
  // via `...rest`, so callers can pass `data-testid`, `aria-*`, `id`,
  // `title`, etc. without each needing an explicit prop.
  //
  // Type discipline: HTMLButtonAttributes.type accepts `null`, which
  // collides with the narrower "button" | "submit" | "reset" the
  // component exposes. We Omit `type`, `disabled`, `class`, and
  // `onclick` from the underlying HTML interfaces before the
  // intersection, then re-add them via `CommonExtras` with the narrowed
  // shapes the component actually supports.

  type CommonExtras = {
    variant?: ButtonVariant;
    size?: ButtonSize;
    class?: string;
    disabled?: boolean;
    onclick?: (event: MouseEvent) => void;
    children?: Snippet;
  };
  type ButtonAsButton = Omit<
    HTMLButtonAttributes,
    "class" | "type" | "disabled" | "onclick"
  > & {
    href?: never;
    type?: "button" | "submit" | "reset";
  } & CommonExtras;
  type ButtonAsAnchor = Omit<HTMLAnchorAttributes, "class" | "onclick"> & {
    href: string;
    type?: never;
  } & CommonExtras;
  type Props = ButtonAsButton | ButtonAsAnchor;

  let {
    variant = "default",
    size = "default",
    type = "button",
    disabled = false,
    href = undefined,
    onclick = undefined,
    class: className = undefined,
    children,
    ...rest
  }: Props = $props();

  // The discriminated union narrows on `href`, but `...rest` retains the
  // union shape because TS cannot prove which branch we're on at the
  // spread site (the event-handler signatures differ between
  // HTMLButtonElement and HTMLAnchorElement). Asserting to the per-branch
  // attribute interface at each spread is the canonical workaround and
  // produces zero runtime cost.
  const restAsAnchor = $derived(rest as HTMLAnchorAttributes);
  const restAsButton = $derived(rest as HTMLButtonAttributes);
</script>

{#if href}
  <a
    {href}
    class={cn(buttonVariants({ variant, size }), className)}
    role="button"
    aria-disabled={disabled}
    {onclick}
    {...restAsAnchor}
  >
    {@render children?.()}
  </a>
{:else}
  <button
    {type}
    {disabled}
    class={cn(buttonVariants({ variant, size }), className)}
    {onclick}
    {...restAsButton}
  >
    {@render children?.()}
  </button>
{/if}
