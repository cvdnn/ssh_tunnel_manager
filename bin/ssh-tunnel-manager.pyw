"""GUI entry point (also callable with Python on macOS/Linux)."""

from pathlib import Path
import sys
import argparse
import os

parser = argparse.ArgumentParser(description='SSH Tunnel Manager')
parser.add_argument('--data-dir', help='运行数据根目录，其下使用 data/ 和 logs/')
parser.add_argument('--startup-splash', action='store_true', help=argparse.SUPPRESS)
args = parser.parse_args()
if args.data_dir:
    os.environ['SSH_TUNNEL_MANAGER_HOME'] = str(Path(args.data_dir).expanduser().resolve())

source_dir = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(source_dir))

from bootstrap import launch


if __name__ == "__main__":
    launch()
