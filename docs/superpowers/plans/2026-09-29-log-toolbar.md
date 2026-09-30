# Log Toolbar Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify the log toolbar so the card has one visual boundary and the clear action is a lightweight transparent control.

**Architecture:** Keep the existing `CardWidget` and behavior. Assign stable object names to the toolbar controls, style the clear action with transparent/hover/pressed states, and remove the redundant `TextEdit` top border. Verify structure, behavior, and rendered output without starting SSH.

**Tech Stack:** Python 3.12, PySide6, PySide6-Fluent-Widgets, unittest, Qt offscreen rendering.

---

### Task 1: Add a failing log-toolbar regression test

**Files:**
- Modify: `C:/Users/cvdnn/ops/tunnel_manager/tests/test_tunnel_manager.py`
- Test: `C:/Users/cvdnn/ops/tunnel_manager/tests/test_tunnel_manager.py`

- [x] Add `test_log_toolbar_uses_single_boundary` to construct `MainWindow` with isolated files and assert that `btn_clear_log.objectName()` is `clearLogButton`, its stylesheet contains `border: none`, and `window.log_text.styleSheet()` contains `border: none` without `border-top`.
- [x] Put text in `window.log_text`, click `btn_clear_log`, and assert the text is empty.
- [x] Run `& .\.venv\Scripts\python.exe -m unittest discover -s tests -k log_toolbar -v`; expect failure because the button is local-only and the text area still has `border-top`.

### Task 2: Implement the lightweight toolbar

**Files:**
- Modify: `C:/Users/cvdnn/ops/tunnel_manager/ssh_tunnel_manager.pyw`

- [x] Store the clear control as `self.btn_clear_log`, assign object name `clearLogButton`, keep height 30px, and apply transparent default plus pale hover/pressed states with a 6px radius.
- [x] Remove `border-top: 1px solid #e2e8f0` from the log text stylesheet while retaining `border: none` and lower corner radii.
- [x] Run the focused test and expect PASS.

### Task 3: Render and verify

**Files:**
- Modify: `C:/Users/cvdnn/ops/tunnel_manager/tests/render_preview.py`
- Modify: `C:/Users/cvdnn/ops/tunnel_manager/README.md`

- [x] Save a dedicated `log-toolbar.png` from the preview script and document the lightweight clear action in README.
- [x] Render with default scale and `QT_SCALE_FACTOR=1.5`; inspect both images for a single card outline and no inner toolbar border.
- [x] Run `& .\.venv\Scripts\python.exe -m unittest discover -s tests -v`, `py_compile`, and `pip check`; expect zero test failures, exit code 0, and no broken requirements.

## Self-review

The three design requirements map directly to Tasks 1–3. Function and object names are consistent, and the plan contains no placeholders. The project is not a Git repository, so commit steps do not apply.

## Completion note

The visual review found an icon/text overlap caused by the local QSS replacing Fluent's icon spacing. A failing regression assertion was added first, then the button received `padding: 4px 10px 4px 30px`. Default and 1.5× previews confirm the icon and label are separated.
