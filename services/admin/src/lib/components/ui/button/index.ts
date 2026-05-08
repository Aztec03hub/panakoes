// @ts-expect-error tsc does not recognize named exports from .svelte module-context blocks; svelte-check (pnpm typecheck) handles this correctly.
import Root, { buttonVariants } from "./button.svelte";

export { Root, Root as Button, buttonVariants };
// @ts-expect-error same SvelteKit + tsc interop quirk for type re-exports.
export type { ButtonVariant, ButtonSize } from "./button.svelte";
