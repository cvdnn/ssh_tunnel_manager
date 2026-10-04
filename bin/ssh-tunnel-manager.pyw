"""Windows GUI entry point for the tunnel manager."""

from pathlib import Path
import sys

source_dir = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(source_dir))

from bootstrap import launch


if __name__ == "__main__":
    launch()
