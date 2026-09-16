"""Durable audit trail: domain events written to PostgreSQL `audit_events`.

Events never contain guest message text (see app.core.events), so the table holds no conversation content.

Writes are asynchronous: `publish` puts the event on a bounded queue and returns, so a slow or
unavailable database never slows guest requests. A background thread writes batches, one transaction
per tenant with `SET LOCAL app.tenant_id` so row-level security applies. When the queue is full, events
are dropped and counted (`audit_events_total{outcome="dropped"}`). This is a best-effort audit log, not
a transactional outbox; booking records that must never be lost belong in the booking transaction itself.
"""

from collections import defaultdict
import json
import logging
import queue
import threading
import time

from psycopg_pool import ConnectionPool

from ..core.events import DomainEvent
from ..core.metrics import Metrics
from ..tenancy import TenantRegistry

logger = logging.getLogger("hotel_assistant.audit")
_STOP = object()


class PostgresAuditSink:
    def __init__(self, database_url: str, metrics: Metrics, *, max_queue: int = 10_000, batch_size: int = 200, pool: ConnectionPool | None = None):
        self.pool = pool or ConnectionPool(database_url, min_size=1, max_size=4, open=True, timeout=5, kwargs={"connect_timeout": 5})
        self.metrics = metrics
        self.batch_size = batch_size
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._last_error: str | None = None
        self._worker = threading.Thread(target=self._run, name="audit-writer", daemon=True)
        self._worker.start()

    # ---- EventPublisher
    def publish(self, event: DomainEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self.metrics.audit_events_total.labels("dropped").inc()

    # ---- lifecycle
    def status(self) -> str:
        return "failing" if self._last_error else "ok"

    def flush(self, timeout: float = 10.0) -> bool:
        """Waits until queued events are written (or timeout). Used in tests and at shutdown."""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.02)
        return self._queue.unfinished_tasks == 0

    def close(self, timeout: float = 10.0) -> None:
        self.flush(timeout)
        self._queue.put(_STOP)
        self._worker.join(timeout)
        self.pool.close()

    # ---- tenant registry sync (hotels must exist before events reference them)
    def sync_tenants(self, registry: TenantRegistry, knowledge) -> None:
        for tenant in registry.all():
            with self.pool.connection() as conn, conn.transaction():
                conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant.id,))
                conn.execute(
                    "INSERT INTO tenants (id, name, status) VALUES (%s, %s, %s) ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, status = EXCLUDED.status, updated_at = now()",
                    (tenant.id, tenant.name, tenant.status),
                )
                for hotel_id in tenant.hotels:
                    profile = knowledge.profile(hotel_id)
                    conn.execute(
                        "INSERT INTO hotels (tenant_id, id, name, timezone, currency) VALUES (%s, %s, %s, %s, %s) "
                        "ON CONFLICT (tenant_id, id) DO UPDATE SET name = EXCLUDED.name, timezone = EXCLUDED.timezone, currency = EXCLUDED.currency, updated_at = now()",
                        (tenant.id, hotel_id, profile.name, profile.timezone, profile.currency),
                    )

    # ---- worker
    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                self._queue.task_done()
                return
            batch = [item]
            while len(batch) < self.batch_size:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is _STOP:
                    self._queue.put(_STOP)  # handle after this batch
                    self._queue.task_done()
                    break
                batch.append(nxt)
            try:
                self._write(batch)
                self._last_error = None
                self.metrics.audit_events_total.labels("written").inc(len(batch))
            except Exception as exc:  # never crash the worker; the events are lost and counted
                self._last_error = type(exc).__name__
                logger.error("audit_write_failed events=%d error=%s", len(batch), type(exc).__name__)
                self.metrics.audit_events_total.labels("failed").inc(len(batch))
            finally:
                for _ in batch:
                    self._queue.task_done()

    def _write(self, batch: list[DomainEvent]) -> None:
        by_tenant: dict[str, list[DomainEvent]] = defaultdict(list)
        for event in batch:
            by_tenant[event.tenant_id].append(event)
        with self.pool.connection() as conn:
            for tenant_id, events in by_tenant.items():
                with conn.transaction(), conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    cur.executemany(
                        "INSERT INTO audit_events (tenant_id, hotel_id, event_id, name, conversation_id, channel, data, occurred_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s) ON CONFLICT DO NOTHING",
                        [(e.tenant_id, e.hotel_id, e.event_id, e.name, e.conversation_id, e.channel, json.dumps(e.data, default=str), e.occurred_at) for e in events],
                    )
