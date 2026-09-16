"""PostgreSQL domain model: migrations, tenant isolation (composite keys + row-level security), constraints, audit sink."""

import shutil
from urllib.parse import quote, urlsplit, urlunsplit
import uuid

from fastapi.testclient import TestClient
import psycopg
from psycopg import errors
import pytest

from app.container import build_container
from app.core.clock import FixedClock
from app.core.config import Settings
from app.core.events import DomainEvent
from app.core.metrics import Metrics
from app.db.audit import PostgresAuditSink
from app.db.migrate import MIGRATIONS_DIR, MigrationError, migrate
from app.main import create_app
from tests.conftest import BLR, GOA, TODAY

TENANT_TABLES = ["hotels", "rooms", "knowledge_documents", "knowledge_versions", "conversations", "messages", "tool_calls", "bookings", "audit_events", "evaluations"]


def _with_db(url: str, dbname: str, user: str | None = None, password: str | None = None) -> str:
    parts = urlsplit(url)
    netloc = parts.netloc if not user else f"{quote(user)}:{quote(password or '')}@{parts.netloc.rsplit('@', 1)[-1]}"
    return urlunsplit((parts.scheme, netloc, f"/{dbname}", parts.query, ""))


@pytest.fixture(scope="module")
def databases():
    import os

    admin = os.environ.get("TEST_DATABASE_URL")
    if not admin:
        pytest.skip("TEST_DATABASE_URL not set")
    suffix = uuid.uuid4().hex[:8]
    dbname, role, password = f"sa_test_{suffix}", f"sa_app_{suffix}", uuid.uuid4().hex
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
        conn.execute(f"CREATE ROLE \"{role}\" LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{password}'")
    owner_url = _with_db(admin, dbname)
    assert migrate(owner_url) == ["0001"]
    with psycopg.connect(owner_url, autocommit=True) as conn:
        conn.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        conn.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"')
    yield {"owner": owner_url, "app": _with_db(admin, dbname, role, password)}
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        conn.execute(f'DROP ROLE IF EXISTS "{role}"')


def _seed(owner_url):
    with psycopg.connect(owner_url, autocommit=True) as conn:  # superuser: bypasses RLS for setup
        conn.execute("INSERT INTO tenants (id, name) VALUES ('tenant-a', 'A'), ('tenant-b', 'B') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO hotels (tenant_id, id, name, timezone, currency) VALUES ('tenant-a', 'hotel-a', 'A', 'UTC', 'INR'), ('tenant-b', 'hotel-b', 'B', 'UTC', 'INR') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO rooms (tenant_id, hotel_id, id, name, max_occupancy, base_rate_minor) VALUES ('tenant-a', 'hotel-a', 'room-1', 'R', 2, 500000), ('tenant-b', 'hotel-b', 'room-1', 'R', 2, 500000) ON CONFLICT DO NOTHING")


def _as_tenant(conn, tenant_id):
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))


def _booking(tenant, hotel, booking_id, key, room="room-1", check_in="2026-10-07", check_out="2026-10-08"):
    return (
        "INSERT INTO bookings (tenant_id, hotel_id, id, room_id, status, check_in, check_out, adults, total_price_minor, currency, guest_reference, idempotency_key, request_fingerprint) "
        "VALUES (%s, %s, %s, %s, 'confirmed', %s, %s, 2, 500000, 'INR', 'guest-1', %s, 'fp')",
        (tenant, hotel, booking_id, room, check_in, check_out, key),
    )


def test_migrations_are_idempotent_and_detect_edits(databases, tmp_path):
    assert migrate(databases["owner"]) == []
    edited = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, edited)
    path = next(edited.glob("0001_*.sql"))
    path.write_text(path.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="changed after it was applied"):
        migrate(databases["owner"], edited)


def test_every_tenant_table_has_forced_row_level_security(databases):
    with psycopg.connect(databases["owner"]) as conn:
        rows = dict(conn.execute("SELECT relname, relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = ANY(%s)", (TENANT_TABLES + ["tenants"],)).fetchall())
        columns = {r[0] for r in conn.execute("SELECT table_name FROM information_schema.columns WHERE column_name = 'tenant_id' AND table_schema = 'public'")}
        indexes = {r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")}
    assert rows == {t: True for t in TENANT_TABLES + ["tenants"]}
    assert set(TENANT_TABLES) <= columns
    assert {"conversations_expiry", "audit_events_retention", "bookings_stay", "bookings_tenant_id_hotel_id_idempotency_key_key"} <= indexes


def test_row_level_security_isolates_tenants(databases):
    _seed(databases["owner"])
    with psycopg.connect(databases["app"]) as conn:
        with conn.transaction():
            assert conn.execute("SELECT count(*) FROM hotels").fetchone()[0] == 0  # no tenant set: nothing visible
        with conn.transaction():
            _as_tenant(conn, "tenant-a")
            assert [r[0] for r in conn.execute("SELECT id FROM hotels")] == ["hotel-a"]
            conn.execute(*_booking("tenant-a", "hotel-a", "BK-A1", "idem-a-0001"))
        with pytest.raises(errors.InsufficientPrivilege):  # RLS WITH CHECK: can't write another tenant's rows
            with conn.transaction():
                _as_tenant(conn, "tenant-a")
                conn.execute(*_booking("tenant-b", "hotel-b", "BK-B1", "idem-b-0001"))
        with conn.transaction():
            _as_tenant(conn, "tenant-b")
            assert conn.execute("SELECT count(*) FROM bookings").fetchone()[0] == 0  # A's booking is invisible to B
            assert conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = 'BK-A1'").rowcount == 0
            assert conn.execute("DELETE FROM bookings WHERE id = 'BK-A1'").rowcount == 0
        with conn.transaction():
            _as_tenant(conn, "tenant-a")
            assert conn.execute("SELECT status FROM bookings WHERE id = 'BK-A1'").fetchone()[0] == "confirmed"


def test_composite_foreign_keys_block_cross_tenant_references(databases):
    _seed(databases["owner"])
    with psycopg.connect(databases["owner"], autocommit=True) as conn:  # superuser bypasses RLS: the FK alone must block it
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_booking("tenant-a", "hotel-b", "BK-X1", "idem-x-0001"))  # tenant A booking in tenant B's hotel
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute("INSERT INTO conversations (tenant_id, hotel_id, id, channel, expires_at) VALUES ('tenant-b', 'hotel-a', 'c1', 'web', now() + interval '1 day')")


def test_constraints_protect_booking_integrity(databases):
    _seed(databases["owner"])
    with psycopg.connect(databases["app"]) as conn:
        with conn.transaction():
            _as_tenant(conn, "tenant-a")
            conn.execute(*_booking("tenant-a", "hotel-a", "BK-U1", "idem-dupe-0001"))
        with pytest.raises(errors.UniqueViolation):
            with conn.transaction():
                _as_tenant(conn, "tenant-a")
                conn.execute(*_booking("tenant-a", "hotel-a", "BK-U2", "idem-dupe-0001"))  # same key, same hotel
        with conn.transaction():
            _as_tenant(conn, "tenant-b")
            conn.execute(*_booking("tenant-b", "hotel-b", "BK-U3", "idem-dupe-0001"))  # same key, other tenant: allowed
        with pytest.raises(errors.CheckViolation):
            with conn.transaction():
                _as_tenant(conn, "tenant-a")
                conn.execute(*_booking("tenant-a", "hotel-a", "BK-U4", "idem-dates-0001", check_in="2026-10-08", check_out="2026-10-08"))
        with pytest.raises(errors.CheckViolation):
            with conn.transaction():
                _as_tenant(conn, "tenant-a")
                conn.execute("INSERT INTO knowledge_documents (tenant_id, hotel_id, id, topic, status) VALUES ('tenant-a', 'hotel-a', 'd1', 'pool', 'published')")


def test_audit_sink_writes_tenant_scoped_events(databases):
    container = build_container(Settings.for_tests(), clock=FixedClock(TODAY))
    sink = PostgresAuditSink(databases["app"], Metrics())
    try:
        sink.sync_tenants(container.tenants, container.knowledge)
        sink.sync_tenants(container.tenants, container.knowledge)  # idempotent
        sink.publish(DomainEvent(name="ConversationStarted", tenant_id="tenant-demo", hotel_id=GOA, conversation_id="conv_1", channel="web", data={"locale": "en"}))
        sink.publish(DomainEvent(name="AvailabilityChecked", tenant_id="tenant-metro", hotel_id=BLR, data={"nights": 2}))
        assert sink.flush(10) and sink.status() == "ok"
    finally:
        sink.close()
    with psycopg.connect(databases["app"]) as conn:
        with conn.transaction():
            _as_tenant(conn, "tenant-demo")
            assert [r[0] for r in conn.execute("SELECT name FROM audit_events")] == ["ConversationStarted"]
        with conn.transaction():
            _as_tenant(conn, "tenant-metro")
            assert [r[0] for r in conn.execute("SELECT name FROM audit_events")] == ["AvailabilityChecked"]


def test_app_with_database_url_records_audit_events_and_reports_readiness(databases):
    container = build_container(Settings.for_tests(database_url=databases["app"]), clock=FixedClock(TODAY))
    with TestClient(create_app(container=container)) as client:
        cid = client.post(f"/api/v1/hotels/{GOA}/conversations", json={}).json()["conversation_id"]
        client.post(f"/api/v1/hotels/{GOA}/conversations/{cid}/messages", json={"message": "Is there a pool? My phone is +91 98765 43210"})
        assert container.audit.flush(10)
        ready = client.get("/ready").json()
    # the lifespan shutdown closed the sink (graceful shutdown path)
    assert ready["checks"]["audit_store"] == "ok"
    with psycopg.connect(databases["app"]) as conn, conn.transaction():
        _as_tenant(conn, "tenant-demo")
        rows = conn.execute("SELECT name, data::text FROM audit_events WHERE conversation_id = %s", (cid,)).fetchall()
    names = {r[0] for r in rows}
    assert {"ConversationStarted", "GuestQuestionAsked", "AssistantResponseGenerated"} <= names
    stored = " ".join(r[1] for r in rows)
    assert "98765" not in stored and "Is there a pool" not in stored and "phone" not in stored.lower()  # ids and counts only, no guest text


def test_idempotent_booking_replays_record_one_confirmation(databases):
    from tests.test_tools_and_resilience import BOOKING, guest, tool_ctx

    container = build_container(Settings.for_tests(database_url=databases["app"], feature_flags={"booking_tools_enabled": True}), clock=FixedClock(TODAY))
    try:
        ctx = tool_ctx(container, principal=guest(), guest_confirmed=True, idempotency_key="idem-audit-0001")
        results = [container.tools.execute("create_booking", BOOKING, ctx, invoked_by="api") for _ in range(3)]
        assert all(r.ok for r in results) and len({r.data.booking_id for r in results}) == 1
        assert container.audit.flush(10)
    finally:
        container.close()
    with psycopg.connect(databases["app"]) as conn, conn.transaction():
        _as_tenant(conn, "tenant-demo")
        counts = dict(conn.execute("SELECT name, count(*) FROM audit_events WHERE name LIKE 'Booking%' GROUP BY name").fetchall())
    assert counts == {"BookingRequested": 3, "BookingConfirmed": 1}


def test_retention_purges_old_audit_events_and_expired_conversations_per_tenant(databases):
    from app.db.retention import purge

    _seed(databases["owner"])
    with psycopg.connect(databases["owner"], autocommit=True) as conn:
        for tenant, hotel in (("tenant-a", "hotel-a"), ("tenant-b", "hotel-b")):
            conn.execute(
                "INSERT INTO audit_events (tenant_id, hotel_id, event_id, name, occurred_at) VALUES (%s, %s, %s, 'Old', now() - interval '400 days'), (%s, %s, %s, 'Recent', now())",
                (tenant, hotel, f"old-{tenant}", tenant, hotel, f"new-{tenant}"),
            )
            conn.execute(
                "INSERT INTO conversations (tenant_id, hotel_id, id, channel, created_at, expires_at) VALUES (%s, %s, 'expired', 'web', now() - interval '2 days', now() - interval '1 day'), (%s, %s, 'live', 'web', now(), now() + interval '1 day')",
                (tenant, hotel, tenant, hotel),
            )
            conn.execute("INSERT INTO messages (tenant_id, hotel_id, conversation_id, seq, role, content) VALUES (%s, %s, 'expired', 0, 'user', 'hi')", (tenant, hotel))

    result = purge(databases["app"], ["tenant-a", "tenant-b"], audit_retention_days=365)  # least-privileged role, RLS applies

    assert result.audit_events_deleted == 2 and result.conversations_deleted == 2
    with psycopg.connect(databases["owner"]) as conn:
        assert {r[0] for r in conn.execute("SELECT event_id FROM audit_events WHERE event_id LIKE 'old-%' OR event_id LIKE 'new-%'")} == {"new-tenant-a", "new-tenant-b"}
        assert {r[0] for r in conn.execute("SELECT id FROM conversations WHERE tenant_id IN ('tenant-a', 'tenant-b')")} == {"live"}
        assert conn.execute("SELECT count(*) FROM messages WHERE conversation_id = 'expired'").fetchone()[0] == 0  # cascaded
    with pytest.raises(ValueError):
        purge(databases["app"], ["tenant-a"], audit_retention_days=0)
