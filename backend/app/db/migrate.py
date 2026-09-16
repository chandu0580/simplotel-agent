"""Forward-only SQL migrations.

* Files `migrations/NNNN_name.sql` run in order, each in its own transaction.
* `schema_migrations` records the version and a SHA-256 checksum; editing an applied file is an error.
* A session advisory lock stops two replicas starting at the same time from migrating concurrently.

Usage: `python -m app.db.migrate` (reads DATABASE_URL). Run it as a deploy step before new replicas start,
using a role that owns the schema; the application itself should connect as a less-privileged role.
"""

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import sys

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
_LOCK_ID = 0x5A11_0001
logger = logging.getLogger("hotel_assistant.db")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        version, _, name = path.stem.partition("_")
        if not version.isdigit():
            raise MigrationError(f"Migration file name must start with a number: {path.name}")
        migrations.append(Migration(version, name, path.read_text(encoding="utf-8")))
    versions = [m.version for m in migrations]
    if len(versions) != len(set(versions)):
        raise MigrationError("Duplicate migration versions")
    return migrations


def migrate(database_url: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Applies pending migrations; returns the versions applied."""
    applied_now = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, name text NOT NULL, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {row[0]: row[1] for row in conn.execute("SELECT version, checksum FROM schema_migrations")}
            for migration in discover(directory):
                if migration.version in done:
                    if done[migration.version] != migration.checksum:
                        raise MigrationError(f"Migration {migration.version}_{migration.name} changed after it was applied")
                    continue
                with conn.transaction():
                    conn.execute(migration.sql)
                    conn.execute("INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)", (migration.version, migration.name, migration.checksum))
                logger.info("migration_applied version=%s name=%s", migration.version, migration.name)
                applied_now.append(migration.version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
    return applied_now


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    applied = migrate(url)
    print(f"applied: {', '.join(applied) if applied else 'nothing (up to date)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
