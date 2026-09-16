"""Retention job for PostgreSQL data (run on a schedule, e.g. daily).

    python -m app.db.retention [--audit-days 365]

* audit_events older than the retention period are deleted.
* conversations past expires_at are deleted (messages and tool_calls cascade).

Runs tenant by tenant with `app.tenant_id` set, so it works under row-level security with the
least-privileged application role; it never needs a superuser. Tenants come from the tenant registry.
Redis-held conversations need no job: their keys expire on their own.
"""

import argparse
from dataclasses import dataclass
import os

import psycopg

from ..core.config import Settings
from ..tenancy import TenantRegistry


@dataclass
class RetentionResult:
    audit_events_deleted: int = 0
    conversations_deleted: int = 0


def purge(database_url: str, tenant_ids: list[str], *, audit_retention_days: int) -> RetentionResult:
    if audit_retention_days < 1:
        raise ValueError("audit_retention_days must be at least 1")
    result = RetentionResult()
    with psycopg.connect(database_url) as conn:
        for tenant_id in tenant_ids:
            with conn.transaction():
                conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                result.audit_events_deleted += conn.execute(
                    "DELETE FROM audit_events WHERE occurred_at < now() - make_interval(days => %s)", (audit_retention_days,)
                ).rowcount
                result.conversations_deleted += conn.execute("DELETE FROM conversations WHERE expires_at < now()").rowcount
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    settings = Settings.from_env()
    parser.add_argument("--audit-days", type=int, default=settings.audit_retention_days)
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set")
        return 2
    tenants = [t.id for t in TenantRegistry.load(settings.data_dir / "tenants.json").all()]
    result = purge(url, tenants, audit_retention_days=args.audit_days)
    print(f"audit_events_deleted={result.audit_events_deleted} conversations_deleted={result.conversations_deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
