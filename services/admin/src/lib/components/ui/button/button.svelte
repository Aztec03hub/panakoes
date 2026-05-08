<script lang="ts" context="module">
  import { tv, type VariantProps } from "tailwind-variants";

  export const buttonVariants = tv({
    base:
      "inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
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
  import { cn } from "$lib/utils";

  type $$Props = {
    variant?: ButtonVariant;
    size?: ButtonSize;
    type?: "button" | "submit" | "reset";
    disabled?: boolean;
    href?: string;
    class?: string;
  };

  export let variant: ButtonVariant = "default";
  export let size: ButtonSize = "default";
  export let type: "button" | "submit" | "reset" = "button";
  export let disabled = false;
  export let href: string | undefined = undefined;
  let className: string | undefined = undefined;
  export { className as class };
</script>

{#if href}
  <a
    {href}
    class={cn(buttonVariants({ variant, size }), className)}
    role="button"
    aria-disabled={disabled}
    on:click
  >
    <slot />
  </a>
{:else}
  <button
    {type}
    {disabled}
    class={cn(buttonVariants({ variant, size }), className)}
    on:click
  >
    <slot />
  </button>
{/if}
