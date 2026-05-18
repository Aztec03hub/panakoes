---
category: Changed
---

- `.github/workflows`: bump 3 GitHub Actions to their node20/24-compatible majors, batching what would otherwise be 3 separate Dependabot PRs (#374, #375, #377) into one coordinated review. All three are drop-in runtime bumps (node16 EOL aged out their previous majors); no workflow logic or action input changes required. Specifically: `marocchino/sticky-pull-request-comment` v2 to v3 in `terraform-plan-on-pr.yml`; `dorny/paths-filter` v3 to v4 in `image-bake-on-change.yml`; `peter-evans/create-pull-request` v7 to v8 in `image-bake.yml`. Closes Dependabot PRs #374, #375, #377.
