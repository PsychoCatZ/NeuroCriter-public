#!/usr/bin/env python3
"""Ежедневный бэкап базы зверька: консистентная копия SQLite и ротация.

    python3 backup.py /var/lib/neurocritter /var/backups/neurocritter [хранить_копий]

Копирование на другой VPS (rsync или scp) делается отдельной строкой в cron,
см. deploy/README.md. Память зверька невосстановима, поэтому бэкап обязателен.
"""
import sqlite3
import sys
import time
from pathlib import Path


def main():
    src = Path(sys.argv[1]) / "pet.sqlite"
    dest_dir = Path(sys.argv[2])
    keep = int(sys.argv[3]) if len(sys.argv) > 3 else 14
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = dest_dir / f"pet-{stamp}.sqlite"
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    copy = sqlite3.connect(target)
    source.backup(copy)
    copy.close()
    source.close()
    for old in sorted(dest_dir.glob("pet-*.sqlite"))[:-keep]:
        old.unlink()
    print(f"копия: {target}")


if __name__ == "__main__":
    main()
