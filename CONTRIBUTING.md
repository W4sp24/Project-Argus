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

## CI/CD

Four workflow files. Two of them are reusable and hold the actual work, so
"how we test" and "how we build" each exist exactly once and cannot drift
apart:

| File | What it is |
| --- | --- |
| `.github/actions/python-env/` | Composite action. The **only** place the Python dependency set is written. |
| `.github/workflows/_test.yml` | Reusable. Jobs `python`, `web`, `e2e`. Takes the runner OS. |
| `.github/workflows/_package.yml` | Reusable. The Windows PyInstaller + electron-builder build. Takes `publish`. |
| `.github/workflows/ci.yml` | Entry point for branch work. |
| `.github/workflows/release.yml` | Entry point for tags. |

Never add an install step to a job. If a job needs Python dependencies it uses
the composite action, and if the app gains a dependency it goes in
`pyproject.toml`. Installing something in one workflow and not another is how
v0.2.0 shipped a backend missing two connector libraries.

### Scenario 1 — you open a pull request

Three checks run on `ubuntu-latest`, in parallel, ~7 minutes total:

| Check | What it does |
| --- | --- |
| `test / python` | `ruff check .` (repo-wide), `pytest -q`, and a smoke test that boots the real backend and exercises its import chain |
| `test / web` | `tsc --noEmit`, `next lint`, `next build`, and asserts the three version manifests agree |
| `test / e2e` | Playwright against a **production build** of the dashboard and a real backend on a throwaway vault |

Linux is a fast proxy, not the shipping platform — see Scenario 3.

### Scenario 2 — a check fails

**Read the annotations first.** They are on the run summary page and need no
login. Playwright failures appear there with file, line and error; so do
frozen-backend smoke failures. Raw job logs and artifacts both require repo
access, so annotations are the thing to look at.

For e2e there is also a `playwright-report-<os>` artifact with the full HTML
report and traces.

Reproduce locally — these are the same commands CI runs:

```bash
.venv/Scripts/python -m ruff check .            # or .venv/bin/ruff on non-Windows
.venv/Scripts/python -m pytest -q
.venv/Scripts/python desktop/tests/smoke_backend.py --target desktop/backend/argus_server.py
cd web && npx tsc --noEmit && npm run lint && npm run build
node desktop/scripts/check-versions.mjs
cd web && npm run e2e                            # ports 8000 and 3100 must be free
```

Two traps worth knowing before you debug an e2e failure:

- **A wall of red is usually one failure.** The dev/prod server sometimes dies
  partway through a run and everything after it fails with
  `ERR_CONNECTION_REFUSED` in a uniform ~3s. Find the *first* failure and check
  whether the rest are connection errors before believing the count. This is a
  known, unfixed harness problem.
- **e2e runs with no OS keyring on purpose.** Unlike the `python` job it does
  not install `keyrings.alt`, because a machine with an unreadable keyring is a
  state real users reach — and running that way is what caught the Todoist
  connector taking down the whole dashboard. Do not "fix" a keyring-shaped e2e
  failure by installing a backend there.

### Scenario 3 — your PR is merged

A push to `main` re-runs the identical `_test.yml` on **`windows-latest`** —
the platform Argus actually ships on — and then builds the real installer
without publishing anything. Budget 30–40 minutes.

That build is the point: publishing a GitHub release freezes its assets
forever, so a packaging bug found at tag time is unrecoverable. Rehearsing it
on every merge means tagging is a path already walked. It leaves an
`argus-windows` artifact — download and install it.

**Do not tag until you have seen `main` go green**, including the `package`
job.

Note that `push` is wired to `main` only. Merges into the `v0.2` integration
branch get per-PR Linux coverage but no Windows run and no packaging rehearsal
until `v0.2` reaches `main`.

### Scenario 4 — cutting a release

1. Bump **all three** manifests to the new version in one commit:
   `pyproject.toml`, `web/package.json`, `desktop/package.json`. The `web` job
   asserts they equal the tag, so a half-bumped release fails in ~2 minutes
   rather than shipping a mislabelled installer.
2. `git tag v0.2.2 && git push origin v0.2.2`

The tag runs `guard` → `test` (windows) → `package` (publish). ~45 minutes.
A red suite means no build, no draft, nothing published. Assets are attached to
a **draft**, which is only published after the `.exe` and `latest.yml` are
confirmed present.

Rehearse with a pre-release tag (`v0.0.7-test`) before shipping anything real,
then delete the test release.

### Scenario 5 — a release fails

- **Build failed:** you are left with a draft release, not a published one.
  Inspect it, delete it, fix, re-tag the same version.
- **Already published:** that version is finished. Release immutability means
  a published release can never accept new assets, so the only way forward is
  a new version number. `guard` refuses to build over it in seconds rather
  than after a 30-minute build.

### Branch protection

Workflows report status; they do not block a merge. Required checks are
configured under *Settings → Rules → Rulesets* on `main`, and must be exactly
`test / python`, `test / web` and `test / e2e`.

Do **not** require `package` — it never runs on a pull request, so requiring it
blocks every PR permanently.
