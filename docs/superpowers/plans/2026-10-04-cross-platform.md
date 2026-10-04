# Cross-platform implementation plan

**Goal:** Remove Windows-only startup dependencies and provide platform-aware desktop integration while preserving existing tunnel data.

**Architecture:** Keep OpenSSH/worker logic. Add a Qt-independent platform adapter for process flags, SSH lookup and per-user login startup. Resolve runtime paths separately from bundled resources. Existing checkout data retains precedence unless an explicit data home is selected; fresh installs use user directories.

**Scope:** Source execution on Windows, macOS and Linux desktops. No installer packaging or claim of native macOS/Linux validation from Windows.

- [ ] Add regression tests for non-Windows splash flags, tray fallback, platform startup files and runtime paths; confirm failures.
- [ ] Implement platform_support.py, preserving Windows Run registration and adding macOS LaunchAgent/Linux XDG autostart. Quote executable paths without a shell; test only temporary paths and fake registry.
- [ ] Update paths.py/bootstrap.py for user storage and --data-dir; retain existing project data without copying or overwriting.
- [ ] Update app.py for platform process flags, native tray menus outside Windows, safe no-tray close, fonts, executable selection and RDP availability.
- [ ] Add portable Python launch/test/development entry points and CI matrix. Update tests that assume Windows and document installation, data precedence and verification limits.
- [ ] Run focused tests then the full unittest/compileall/pip-check suite; inspect diff for user-data writes and platform assumptions.

Validation command: `.venv/Scripts/python.exe scripts/test.py` on Windows, `.venv/bin/python scripts/test.py` on POSIX. CI executes on all three operating systems; native tray/login/session behavior still needs desktop smoke testing.
