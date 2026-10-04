"""Compatibility entry point for existing shortcuts and autostart commands."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    entry = Path(__file__).resolve().parent / "bin" / "ssh-tunnel-manager.pyw"
    runpy.run_path(str(entry), run_name="__main__")
