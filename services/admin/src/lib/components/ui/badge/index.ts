// @ts-expect-error tsc does not recognize named exports from .svelte module-context blocks; svelte-check (pnpm typecheck) handles this correctly.
import Root, { badgeVariants } from "./badge.svelte";

export { Root, Root as Badge, badgeVariants };
// @ts-expect-error same SvelteKit + tsc interop quirk for type re-exports.
export type { BadgeVariant } from "./badge.svelte";
