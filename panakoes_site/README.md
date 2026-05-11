# panakoes.com landing page

Static one-page introduction to the Panakoes open-source project, deployed to Cloudflare Pages at `panakoes.com`. Sibling to `lafayettelabs.com` (the parent LLC's marketing site), reusing the same paper / ink / signal design system.

This directory is intentionally NOT part of the Panakoes monorepo build pipeline. It is a standalone Cloudflare Pages project served from this path on every push to `main`.

## Tech

- Plain HTML and CSS. Zero JavaScript, zero external fonts, zero external assets.
- Total uncompressed page weight is well under the 30 KB budget (HTML + CSS + inline SVG diagram).
- Fonts use a system stack that falls back through Inter Tight / Helvetica Neue / Arial. The `panakoes.com` page intentionally does not pull from `fonts.googleapis.com` so the page renders with one TLS handshake and the CSP can forbid third-party origins.
- Responsive across mobile / tablet / desktop via CSS grid + clamp() type ramps.

## Visual continuity with lafayettelabs.com

The CSS variables (`--paper`, `--ink`, `--signal`, `--mute`, `--hairline`, `--grotesk`, `--serif`, `--mono`) and the editorial composition (numlabel section markers, hairline rules, spec rows, italic-serif emphasis inside grotesk display) are lifted from the LaFayette Labs site's `PaperLayout.astro` design system so the two sites read as siblings. One intentional departure: this site uses a system-font stack instead of Google Fonts (Inter Tight + Cormorant Garamond + IBM Plex Mono) to keep the network waterfall trivial and the CSP locked to same-origin. Visitors on macOS / iOS / modern Windows will see the system geometric sans-serif; everything else falls back through Helvetica Neue / Arial. If Phil wants exact font parity with the LL site, swap in a Google Fonts `<link>` and add `fonts.googleapis.com` + `fonts.gstatic.com` to the CSP `font-src` / `style-src` directives.

## Cloudflare Pages deployment

The deployment is git-integrated via the GitHub Actions workflow at `.github/workflows/panakoes-site-deploy.yml` (uses `cloudflare/pages-action@v1` with the `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` repo secrets that already exist for the LaFayette Labs site).

### One-time Cloudflare dashboard setup (Phil does this)

1. Cloudflare dashboard -> Workers & Pages -> Create application -> Pages -> "Direct Upload".
2. **Project name:** `panakoes-site` (the workflow's `projectName` is wired to this exact value).
3. Production branch: `main`.
4. After the first successful Actions run uploads a deployment, attach the custom domain:
   - Custom domains -> Set up a custom domain -> `panakoes.com` (apex).
   - Repeat for `www.panakoes.com` (Cloudflare will offer to create the CNAME automatically since the zone is on the same account).
5. Cloudflare DNS for the `panakoes.com` zone will get a CNAME flattening record for the apex pointing at `panakoes-site.pages.dev`. Cloudflare handles this automatically when you click "Activate".

### Why direct-upload via Actions instead of Cloudflare git-integration

The git-integration mode runs `npm install && npm run build` in the Cloudflare build environment. We have no build step (static HTML + CSS), so direct-upload via Actions is faster (no build minutes), gives us preview URLs on every PR, and lets the `_headers` and `_redirects` files ship verbatim. Interview talk-track: direct-upload also keeps the deploy step in the same audit trail as every other CI run instead of splitting it across two providers.

### Required secrets (already configured for the LaFayette Labs site)

- `CLOUDFLARE_API_TOKEN`: scoped to Pages:Edit on the account. Create at My Profile -> API Tokens with the "Cloudflare Pages: Edit" template.
- `CLOUDFLARE_ACCOUNT_ID`: visible in the Cloudflare dashboard sidebar.

If either is missing or scoped wrong, the workflow will fail at the `cloudflare/pages-action` step with a 401 / 403; rotate the token rather than reusing a long-lived one.

## Local preview

```bash
cd panakoes_site
python3 -m http.server 8080
# then open http://localhost:8080
```

That's it. No build step, no `node_modules`.

## Page weight verification

```bash
# total uncompressed weight of everything served on first paint
du -sb panakoes_site/index.html panakoes_site/assets/styles.css panakoes_site/assets/images/architecture-diagram.svg
```

Target: under 30 KB total.

## Files

| Path | Purpose |
|---|---|
| `index.html` | The page |
| `assets/styles.css` | Design system + layout |
| `assets/images/architecture-diagram.svg` | Inline-rendered architecture overview |
| `_headers` | Cloudflare Pages headers (CSP, HSTS, X-Frame-Options, asset cache) |
| `_redirects` | Short-link aliases (`/github`, `/docs`, `/security`, etc.) |
| `robots.txt` / `sitemap.xml` | SEO basics |

## What this site does NOT do

- No analytics. No telemetry. No cookies. If Phil wants Cloudflare Web Analytics later, add the snippet via Pages env var, not source.
- No JS framework. No SSR. No build step.
- No contact form. Inquiry funnel goes through `lafayettelabs.com/inquiry/`.
