# Contributing to Argus

## Branching model

- `main` is the released line. Releases are cut by pushing a `v*` tag (the tag-driven release workflow in `.github/workflows/release.yml`).
- `v0.2` is the long-lived **integration branch** for the next release line. All new feature work branches off `v0.2` and merges back into `v0.2` via a reviewed pull request.
- **Start a feature:**
  ```bash
  git checkout v0.2
  git pull --ff-only origin v0.2
  git checkout -b feature/<short-name>
  ```
- Open a PR from `feature/<short-name>` **into `v0.2`** (never into `main`). Get it reviewed and merged.
- `v0.2` merges into `main` **only at release time**, after which a `v0.2.x` tag is pushed to trigger the release build.
- **Naming note:** feature branches are flat (`feature/<name>`), NOT nested under the integration branch (e.g. not `v0.2/feature/<name>`). Git stores refs as file paths, so a branch literally named `v0.2` makes any `v0.2/...` ref impossible to create. Flat `feature/*` also matches the repo's existing convention.
