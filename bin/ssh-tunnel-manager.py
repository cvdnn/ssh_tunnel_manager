"""Portable console entry point; shares argument handling with the GUI entry."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).with_suffix('.pyw')), run_name='__main__')
