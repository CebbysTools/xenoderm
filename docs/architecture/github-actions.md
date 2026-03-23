# GitHub Actions & Workflows

This document describes the CI/CD setup for xenoderm and the library of custom GitHub Actions that support the project. All custom actions live under `.github/actions/` and are tested by a dedicated workflow before being used in other workflows.

---

## Directory Layout

```
.github/
├── actions/
│   └── setup-ghidra/         Custom action: install Ghidra
│       ├── action.yml
│       └── README.md
│
└── workflows/
    ├── test-actions.yml      Integration tests for all custom actions
    ├── ci.yml                Main CI: lint, unit tests, type checks   (future)
    └── release.yml           Package and publish to PyPI              (future)
```

---

## Custom Actions Catalogue

| Action | Path | Purpose |
|--------|------|---------|
| `setup-ghidra` | `.github/actions/setup-ghidra` | Download, cache, and configure Ghidra + Java |

More actions will be added as the project grows:

| Planned action | Purpose |
|---------------|---------|
| `export-xdm` | Run `xenoderm_export.py` in headless Ghidra on a test binary, produce `.xdm` |
| `run-xenoderm` | Run xenoderm headless decompilation and capture output |

---

## Workflow: `test-actions.yml`

### Purpose

Every custom action in `.github/actions/` must have a corresponding job in `test-actions.yml`. This workflow is the single source of truth for verifying that all actions work correctly before they are referenced by other workflows.

### Trigger policy

```yaml
on:
  push:
    paths:
      - '.github/actions/**'
      - '.github/workflows/test-actions.yml'
  pull_request:
    paths:
      - '.github/actions/**'
      - '.github/workflows/test-actions.yml'
  workflow_dispatch:        # allow manual runs
```

Only files under `.github/actions/**` and the test workflow itself trigger this run, keeping CI fast for unrelated changes.

### Job structure

One job per action. Jobs are independent and run in parallel.

```
test-actions.yml
│
├── job: test-setup-ghidra        tests the setup-ghidra action
│   └── matrix: [ubuntu, windows, macos]
│
├── job: test-export-xdm          (future)
│
└── job: summary
    └── depends-on: all test jobs
        reports pass/fail table
```

---

## Action: `setup-ghidra`

### What it does

1. Resolves the requested Ghidra version (or `latest` via GitHub API).
2. Installs the required Java version (Ghidra 11.x → Java 17; Ghidra 10.x → Java 11).
3. Checks the runner cache for a matching Ghidra installation.
4. If not cached: downloads the official Ghidra `.zip` from `github.com/NationalSecurityAgency/ghidra/releases`, verifies the SHA-256, and unzips.
5. Saves to cache for future runs.
6. Sets the following outputs and environment variables:

| Output / Env var | Value |
|-----------------|-------|
| `GHIDRA_HOME` | Absolute path to the Ghidra install directory |
| `GHIDRA_HEADLESS` | Absolute path to `analyzeHeadless` (or `.bat` on Windows) |
| `GHIDRA_VERSION` | Resolved version string, e.g. `11.1.2` |

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `version` | no | `latest` | Ghidra version tag, e.g. `11.1.2` or `latest` |
| `java-distribution` | no | `temurin` | JDK distribution passed to `actions/setup-java` |
| `install-path` | no | `$RUNNER_TOOL_CACHE/ghidra` | Where to install Ghidra |
| `cache` | no | `true` | Whether to cache the Ghidra download |

### Outputs

| Output | Description |
|--------|-------------|
| `ghidra-home` | Path to Ghidra installation directory |
| `ghidra-version` | Resolved version string |
| `cache-hit` | `true` if Ghidra was restored from cache |

### Caching strategy

The cache key is `ghidra-<version>-<runner-os>`. A cache hit skips the download entirely. The downloaded zip is deleted after extraction to keep workspace clean.

### Platform support

| Runner | Status | Notes |
|--------|--------|-------|
| `ubuntu-latest` | supported | primary target |
| `windows-latest` | supported | uses `.bat` wrapper for `analyzeHeadless` |
| `macos-latest` | supported | x86 and ARM (M-series) runners |

### Usage example

```yaml
- uses: ./.github/actions/setup-ghidra
  with:
    version: '11.1.2'

- name: Verify
  run: |
    echo "Ghidra: $GHIDRA_HOME"
    $GHIDRA_HEADLESS --help
```

---

## Action Testing Conventions

Each job that tests a custom action follows this pattern:

1. **Install** — call the action under test.
2. **Smoke test** — verify the key outputs exist and are non-empty.
3. **Functional test** — run a minimal real operation (e.g. `analyzeHeadless --help` for `setup-ghidra`).
4. **Cache test** — run the action a second time in the same job; assert `cache-hit == true` and that the second run completes faster.
5. **Failure case** — call the action with an invalid input and assert it fails with a non-zero exit code.

---

## Adding a New Custom Action

1. Create `.github/actions/<action-name>/action.yml`.
2. Add a corresponding job to `test-actions.yml`.
3. The job must cover at minimum: install, smoke test, functional test.
4. Update the catalogue table in this document.
5. No other workflow should reference the new action until `test-actions.yml` passes for it.

---

## Security Considerations

- All custom actions pin third-party actions to a specific commit SHA, not a mutable tag.
- `setup-ghidra` verifies the SHA-256 of the downloaded zip against the hash published on the Ghidra GitHub release page before extracting.
- The `GHIDRA_HEADLESS` path is never constructed from user-controlled inputs to prevent path injection.
- Secrets (e.g. `GITHUB_TOKEN` for the releases API) are accessed via `${{ secrets.* }}` and never echoed to logs.
