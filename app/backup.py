"""Database backups and a quick health summary of the data.

python -m app.backup              write a timestamped copy next to the database
python -m app.backup --output X   write the copy to X
python -m app.backup --check      only print the summary of the live database
python -m app.backup --check X    print the summary of the copy at X
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from app import db

DEFAULT_KEEP = 14
_PREFIX = "digest-"


def backup_directory() -> Path:
    return db.DB_PATH.parent / "backups"


def prune_backups(directory: Path, keep: int) -> list[Path]:
    """Delete all but the newest `keep` timestamped backups. Returns what was removed."""
    backups = sorted(directory.glob(f"{_PREFIX}*.db"))
    removed = backups[: max(0, len(backups) - keep)]
    for path in removed:
        path.unlink()
    return removed


def create_backup(output: Path | None = None, keep: int = DEFAULT_KEEP) -> Path:
    if output is not None:
        db.backup_database(output)
        return output

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = backup_directory() / f"{_PREFIX}{stamp}.db"
    db.backup_database(path)
    # Only the default directory is pruned: an explicit output is the caller's file.
    prune_backups(path.parent, keep)
    return path


def print_summary(path: Path | None = None) -> bool:
    summary = db.database_summary(path)
    for name, value in summary.items():
        print(f"{name}: {'-' if value is None else value}")
    return summary["integrity"] == "ok"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Back up or check the digest database.")
    parser.add_argument("--output", type=Path, help="Write the backup to this file.")
    parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP,
        help=f"Timestamped backups to keep in the default directory (default {DEFAULT_KEEP}).",
    )
    parser.add_argument(
        "--check",
        nargs="?",
        const="",
        metavar="PATH",
        help="Print integrity and row counts of the live database, or of the copy at PATH.",
    )
    args = parser.parse_args(argv)

    if args.check is not None:
        target = Path(args.check) if args.check else None
        if target is not None and not target.is_file():
            raise SystemExit(f"error: {target} does not exist")
        if not print_summary(target):
            raise SystemExit("error: integrity check failed")
        return

    if args.keep < 1:
        raise SystemExit("error: --keep must be at least 1")

    db.init_db()
    path = create_backup(args.output, args.keep)
    print(f"backup: {path}")
    if not print_summary(path):
        raise SystemExit("error: integrity check failed")


if __name__ == "__main__":
    main()
