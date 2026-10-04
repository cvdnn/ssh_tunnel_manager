# Project Layout Implementation Plan

> **For agentic workers:** Execute tasks inline in this workspace; the current worktree contains uncommitted connection-management changes.

**Goal:** Move executable code out of the repository root into `src/`, provide a `bin/` entry point and `scripts/` tools, and move local runtime files into `data/` and `logs/` without losing user configuration.

**Architecture:** Keep the first migration behavioral: the large GUI module stays intact under `src/`, while one path module provides stable repository-root paths. The launcher controls import order and splash subprocess startup. Migration is explicit and conflict-safe.

**Tech Stack:** Python 3.12, PySide6, unittest, PowerShell 7.

---

### Task 1: Baseline and path behavior

- [x] Run full existing unittest suite and record count/failures (105 tests passed).
- [x] Add a failing test proving path resolution from `src/` points at repository `data/`, `logs/`, and `src/assets/` regardless of current directory.
- [x] Implement `src/paths.py` and verify the targeted test.

### Task 2: Move application code and preserve entry behavior

- [x] Move the three root Python application files into `src/`, keeping all existing uncommitted source edits.
- [x] Add `bin/ssh-tunnel-manager.pyw` and a thin root compatibility launcher.
- [x] Ensure the launcher starts the splash child before importing the full GUI and tests can import src modules.
- [x] Update tests for startup child path, run targeted startup tests, then full regression.

### Task 3: Runtime data migration

- [x] Add failing migration tests for absent source, existing target conflict, invalid JSON, repeat run, and byte-for-byte copy.
- [x] Implement a preview/execute migration CLI under `scripts/`, with no overwrite and verification before source cleanup.
- [x] Migrate this workspace's local runtime files after the application is closed. Verify source and target content hashes.
- [x] Update `.gitignore` so `data/` and `logs/` remain local, and add sanitized examples under `config/examples/`.

### Task 4: Tooling, documentation, and verification

- [x] Move preview renderers into `scripts/preview/`, remove their dependency on personal tunnel files, and keep output in `artifacts/ui/`.
- [x] Add `scripts/dev.ps1` and `scripts/test.ps1` using script-relative paths.
- [x] Update README launch, test and file descriptions.
- [x] Run full unittest, syntax compile, pip check, offscreen preview, and import/launch smoke checks.
- [x] Review `git diff` and verify pre-existing edits are preserved.

The larger second-stage split of `src/app.py` by GUI and worker responsibility is described in `docs/project-structure-plan.md` and follows after this behavior-preserving layout migration.
