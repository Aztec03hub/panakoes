// Dynamic [service] route renders client-side from the SPA fallback
// (build/index.html). Prerendering disabled because the service IDs are not
// known at build time; CloudFront will serve index.html for any unmatched
// path and SvelteKit's client router resolves the param.
export const prerender = false;
