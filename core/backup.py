"""
IM8 Bot — Database Backups
Creates consistent point-in-time snapshots of the SQLite database and rotates
old copies. Uses SQLite's ``VACUUM INTO``, which writes a fully-consistent,
defragmented copy even while the WAL-mode connection is live — no need to stop
the bot or copy ``-wal``/``-shm`` sidecar files.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger("im8bot.backup")


async def create_backup(
    connection: aiosqlite.Connection,
    backup_dir: str,
    keep: int = 14,
) -> Path | None:
    """Writes a timestamped snapshot of the live database and prunes old ones.

    Args:
        connection: the live aiosqlite connection (its database is snapshotted).
        backup_dir: directory to write snapshots into (created if missing).
        keep: how many of the most recent snapshots to retain.

    Returns the path to the new snapshot, or ``None`` on failure.
    """
    out_dir = Path(backup_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Backup: could not create backup directory '{backup_dir}': {e}")
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"im8bot-{stamp}.db"

    try:
        # VACUUM INTO requires the destination not to exist; the timestamped
        # name makes a collision practically impossible, but guard anyway.
        if target.exists():
            target.unlink()
        # The path is interpolated (VACUUM INTO does not accept a bound
        # parameter for the filename); the value is bot-controlled, not user
        # input, and single-quotes are escaped defensively.
        safe = str(target).replace("'", "''")
        await connection.execute(f"VACUUM INTO '{safe}'")
        logger.info(f"Backup: wrote snapshot → {target}")
    except Exception as e:
        logger.error(f"Backup: snapshot failed: {e}")
        return None

    _prune(out_dir, keep)
    return target


def _prune(out_dir: Path, keep: int) -> None:
    """Deletes all but the ``keep`` most recent snapshots."""
    try:
        snapshots = sorted(
            out_dir.glob("im8bot-*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in snapshots[max(keep, 0):]:
            try:
                stale.unlink()
                logger.info(f"Backup: pruned old snapshot {stale.name}")
            except Exception as e:
                logger.warning(f"Backup: could not prune {stale.name}: {e}")
    except Exception as e:
        logger.warning(f"Backup: prune scan failed: {e}")
