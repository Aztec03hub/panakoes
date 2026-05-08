import adapter from "@sveltejs/adapter-static";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

/** @type {import('@sveltejs/kit').Config} */
const config = {
  // `script: true` forces vitePreprocess to strip TypeScript annotations from
  // `<script lang="ts">` blocks before the Svelte compiler runs. Svelte 5 can
  // handle most TS natively, but its legacy compatibility printer (esrap)
  // chokes on `export let foo: T = init` patterns when components mix Svelte
  // 4 syntax with type annotations. Stripping up-front makes the pipeline
  // robust under Vitest, where the legacy path is exercised more aggressively
  // than during a normal SvelteKit build.
  preprocess: vitePreprocess({ script: true }),
  kit: {
    // SPA-mode static adapter: target deployment is S3 + CloudFront.
    // fallback: "index.html" makes every unknown path resolve to index.html
    // so client-side routing handles dynamic routes (e.g. /dashboard/[service]).
    adapter: adapter({
      pages: "build",
      assets: "build",
      fallback: "index.html",
      precompress: false,
      strict: true,
    }),
    alias: {
      $lib: "src/lib",
    },
  },
};

export default config;
