# SSH connections implementation plan

> Execute in this session with subagent-driven-development and test-driven-development. User approved the design and requested selective SSH config write-back.

**Goal:** Merge discovered and manual SSH connections, persist manual edits, preserve existing tunnel references, and provide explicit previewed export to SSH config.

**Architecture:** `ssh_connections.py` owns discovery, validation, persistence and export. `connection_settings.py` owns the settings editor. The existing main application resolves stable references and passes immutable connection snapshots into its worker pool.

**Tech Stack:** Python 3.12, PySide6, unittest, existing QFluentWidgets shell.

- [x] Backend: add failing tests for Include discovery, validation, atomic persistence, preview/export conflicts and concurrent edits; implement and rerun `python -m unittest discover -s tests -p test_ssh_connections.py`.
- [x] UI: add tests for editing drafts, cancellation and stable connection selections; implement source list, manual form, selected export preview and explicit write action.
- [x] Integration: resolve config/manual IDs, preserve legacy host aliases, pass configured SSH path and optional flags to workers, remove forced insecure host checking and demo auto-connections.
- [x] Settings: expose config path and application timeout settings, retain loaded unknown fields, report persistence failures.
- [x] Regression: run unittest discovery (105 passed), syntax compilation, dependency checks and offscreen previews; review backend spec compliance and overall quality.
- [x] Documentation: update README and accepted spec with connection management, parameter sources and write-back rules.

Write-back uses selected saved manual entries. Preview shows target path, alias and exact text. A separate explicit write button backs up existing bytes then atomically replaces the target. Existing Host aliases are never overwritten. Original content is retained; newly generated Host blocks are prepended so trailing Host/Match scope cannot capture them. Recheck source bytes and included conflicts before writing. Duplicate export is rejected. Real user SSH files are not touched during development tests.

Implementation detail clarified: selected validated draft entries may also be exported before settings save; the UI explicitly states export is immediate and is not undone by cancelling settings. A Host * boundary restores the original global scope. Relative Includes use the user SSH directory even with a custom -F root. Review fixes cover legacy source identity, configuration reference disappearance/restoration, malformed reference types, save failure and startup-held tunnel isolation.

Verification completed 2026-10-04: 105 unittest cases passed; py_compile, pip check, and git diff --check passed. Screenshots inspected in artifacts/ui/connections. No real SSH connection or config write-back was performed.
