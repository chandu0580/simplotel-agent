-- Domain model for the hotel guest assistant.
--
-- Tenant isolation is enforced three ways:
--   1. Every tenant-owned table carries tenant_id and uses (tenant_id, hotel_id, ...) composite keys,
--      so a foreign key can never point at another tenant's hotel, room or conversation.
--   2. Row-level security: rows are visible and writable only when tenant_id matches the
--      transaction setting app.tenant_id (SET LOCAL app.tenant_id = '...').
--   3. FORCE ROW LEVEL SECURITY applies the policies to the table owner too. Superusers still
--      bypass RLS, so the application must connect as a non-superuser role.
--
-- Money is stored in minor units (bigint). Timestamps are timestamptz.

CREATE TABLE tenants (
    id          text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]{1,62}$'),
    name        text NOT NULL,
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE hotels (
    tenant_id   text NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    id          text NOT NULL CHECK (id ~ '^[a-z0-9][a-z0-9_-]{1,62}$'),
    name        text NOT NULL,
    timezone    text NOT NULL,
    currency    char(3) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (id)  -- hotel ids are globally unique (they appear in public URLs)
);

CREATE TABLE rooms (
    tenant_id         text NOT NULL,
    hotel_id          text NOT NULL,
    id                text NOT NULL,
    name              text NOT NULL,
    max_occupancy     integer NOT NULL CHECK (max_occupancy > 0),
    base_rate_minor   bigint NOT NULL CHECK (base_rate_minor >= 0),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, hotel_id, id),
    FOREIGN KEY (tenant_id, hotel_id) REFERENCES hotels (tenant_id, id) ON DELETE CASCADE
);

CREATE TABLE knowledge_documents (
    tenant_id        text NOT NULL,
    hotel_id         text NOT NULL,
    id               text NOT NULL,
    topic            text NOT NULL,
    status           text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'archived')),
    current_version  integer,
    effective_from   date,
    effective_to     date,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, hotel_id, id),
    FOREIGN KEY (tenant_id, hotel_id) REFERENCES hotels (tenant_id, id) ON DELETE CASCADE,
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from),
    CHECK (status <> 'published' OR current_version IS NOT NULL)
);
CREATE INDEX knowledge_documents_servable ON knowledge_documents (tenant_id, hotel_id, status);

CREATE TABLE knowledge_versions (
    tenant_id     text NOT NULL,
    hotel_id      text NOT NULL,
    document_id   text NOT NULL,
    version       integer NOT NULL CHECK (version > 0),
    title         text NOT NULL,
    content       text NOT NULL,
    content_hash  text NOT NULL,
    created_by    text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, hotel_id, document_id, version),
    FOREIGN KEY (tenant_id, hotel_id, document_id) REFERENCES knowledge_documents (tenant_id, hotel_id, id) ON DELETE CASCADE
);

CREATE TABLE conversations (
    tenant_id   text NOT NULL,
    hotel_id    text NOT NULL,
    id          text NOT NULL,
    channel     text NOT NULL,
    locale      text,
    version     integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, hotel_id, id),
    FOREIGN KEY (tenant_id, hotel_id) REFERENCES hotels (tenant_id, id) ON DELETE CASCADE,
    CHECK (expires_at > created_at)
);
CREATE INDEX conversations_expiry ON conversations (expires_at);  -- retention purge

CREATE TABLE messages (
    tenant_id        text NOT NULL,
    hotel_id         text NOT NULL,
    conversation_id  text NOT NULL,
    seq              integer NOT NULL CHECK (seq >= 0),
    role             text NOT NULL CHECK (role IN ('user', 'assistant')),
    content          text NOT NULL CHECK (length(content) <= 8000),
    reply_type       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, hotel_id, conversation_id, seq),
    FOREIGN KEY (tenant_id, hotel_id, conversation_id) REFERENCES conversations (tenant_id, hotel_id, id) ON DELETE CASCADE
);

CREATE TABLE tool_calls (
    tenant_id        text NOT NULL,
    hotel_id         text NOT NULL,
    id               uuid NOT NULL,
    conversation_id  text,
    trace_id         text NOT NULL,
    tool_name        text NOT NULL,
    status           text NOT NULL CHECK (status IN ('ok', 'error')),
    error_code       text,
    latency_ms       integer NOT NULL CHECK (latency_ms >= 0),
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, hotel_id, id),
    FOREIGN KEY (tenant_id, hotel_id) REFERENCES hotels (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, hotel_id, conversation_id) REFERENCES conversations (tenant_id, hotel_id, id) ON DELETE CASCADE
);
CREATE INDEX tool_calls_recent ON tool_calls (tenant_id, hotel_id, created_at DESC);

CREATE TABLE bookings (
    tenant_id            text NOT NULL,
    hotel_id             text NOT NULL,
    id                   text NOT NULL,
    room_id              text NOT NULL,
    status               text NOT NULL CHECK (status IN ('confirmed', 'cancelled')),
    check_in             date NOT NULL,
    check_out            date NOT NULL,
    adults               integer NOT NULL CHECK (adults >= 1),
    children             integer NOT NULL DEFAULT 0 CHECK (children >= 0),
    total_price_minor    bigint NOT NULL CHECK (total_price_minor >= 0),
    currency             char(3) NOT NULL,
    guest_reference      text NOT NULL,  -- opaque subject id, never contact details
    idempotency_key      text NOT NULL CHECK (length(idempotency_key) >= 8),
    request_fingerprint  text NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, hotel_id, id),
    UNIQUE (tenant_id, hotel_id, idempotency_key),  -- durable idempotency backstop
    FOREIGN KEY (tenant_id, hotel_id, room_id) REFERENCES rooms (tenant_id, hotel_id, id),
    CHECK (check_out > check_in)
);
CREATE INDEX bookings_stay ON bookings (tenant_id, hotel_id, check_in);

CREATE TABLE audit_events (
    tenant_id        text NOT NULL,
    hotel_id         text NOT NULL,
    event_id         text NOT NULL,
    name             text NOT NULL,
    conversation_id  text,  -- no FK: audit history outlives deleted conversations
    channel          text,
    data             jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at      timestamptz NOT NULL,
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, hotel_id, event_id),
    FOREIGN KEY (tenant_id, hotel_id) REFERENCES hotels (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX audit_events_recent ON audit_events (tenant_id, hotel_id, occurred_at DESC);
CREATE INDEX audit_events_retention ON audit_events (occurred_at);

CREATE TABLE evaluations (
    tenant_id       text NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    id              text NOT NULL,
    label           text NOT NULL,
    provider        text NOT NULL,
    model           text NOT NULL,
    prompt_version  text NOT NULL,
    total           integer NOT NULL CHECK (total >= 0),
    passed          integer NOT NULL CHECK (passed >= 0),
    summary         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    CHECK (passed <= total)
);

-- Row-level security -------------------------------------------------------------------------
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenants
    USING (id = current_setting('app.tenant_id', true))
    WITH CHECK (id = current_setting('app.tenant_id', true));

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['hotels', 'rooms', 'knowledge_documents', 'knowledge_versions', 'conversations',
                             'messages', 'tool_calls', 'bookings', 'audit_events', 'evaluations']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
                          USING (tenant_id = current_setting('app.tenant_id', true))
                          WITH CHECK (tenant_id = current_setting('app.tenant_id', true))$p$, t);
    END LOOP;
END $$;
