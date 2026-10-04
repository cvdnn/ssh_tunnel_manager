"""Move legacy root runtime files into data/ and logs/ after validation."""

import argparse
import filecmp
import json
from pathlib import Path
import shutil


DATA_NAMES = ("settings.json", "tunnels.json", "ssh_connections.json")
LOG_NAME = "ssh_tunnel.log"


def migrate(root: Path, *, apply: bool = False) -> list[str]:
    root = Path(root).resolve()
    backup_root = root / ".review-backup" / "layout"
    moves = []
    for name in (*DATA_NAMES, LOG_NAME):
        source = root / name
        if not source.exists():
            continue
        target = root / ("logs" if name == LOG_NAME else "data") / name
        if target.exists():
            raise ValueError(f"目标已存在，拒绝覆盖：{target}")
        backup = backup_root / name
        if backup.exists():
            raise ValueError(f"备份已存在，拒绝覆盖：{backup}")
        if name in DATA_NAMES:
            json.loads(source.read_text(encoding="utf-8-sig"))
        moves.append((source, target))
    if apply:
        backup_root.mkdir(parents=True, exist_ok=True)
        for source, _ in moves:
            backup = backup_root / source.name
            shutil.copy2(source, backup)
            if not filecmp.cmp(source, backup, shallow=False):
                raise OSError(f"备份校验失败：{source.name}")
        for source, target in moves:
            target.parent.mkdir(exist_ok=True)
            source.replace(target)
    return [source.name for source, _ in moves]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="执行迁移；默认仅预览")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    names = migrate(root, apply=args.apply)
    action = "已迁移" if args.apply else "待迁移"
    print(f"{action}：{', '.join(names) if names else '无'}")


if __name__ == "__main__":
    main()
