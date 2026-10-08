"""PostgreSQL schema and data access. Pipeline records are stored as JSONB documents next to the columns that are
queried or constrained, so the typed pydantic models stay the source of truth.

Two compliance rules are enforced by the database itself, not only by application code:
  * a message can only reach status 'sent' if it was approved (CHECK constraint);
  * a message to a suppressed address cannot be inserted as 'scheduled' (trigger), so a suppression added while a
    sequence is being planned still wins.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id SERIAL PRIMARY KEY,
    domain TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'synthetic',
    status TEXT NOT NULL DEFAULT 'new',
    route TEXT,
    score INT,
    timezone TEXT,
    do_not_contact BOOLEAN NOT NULL DEFAULT false,
    profile JSONB,
    qualification JSONB,
    contact JSONB,
    contact_notes JSONB,
    error TEXT,
    research_calls INT NOT NULL DEFAULT 0,
    research_seconds REAL NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS pages (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    text TEXT,
    purged_at TIMESTAMPTZ,
    UNIQUE (account_id, url)
);
CREATE TABLE IF NOT EXISTS drafts (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    variant TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'superseded')),
    data JSONB NOT NULL,
    first_draft JSONB,
    contact JSONB NOT NULL,
    edited JSONB,
    model TEXT,
    calls INT NOT NULL DEFAULT 0,
    reviewer TEXT,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS feedback (
    id SERIAL PRIMARY KEY,
    draft_id INT NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    step INT NOT NULL,
    before TEXT NOT NULL,
    after TEXT NOT NULL,
    diff TEXT NOT NULL,
    stats JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS suppression (
    value TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('email', 'domain')),
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    draft_id INT NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    account_id INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    step INT NOT NULL,
    to_email TEXT NOT NULL,
    to_name TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    timezone TEXT NOT NULL,
    scheduled_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'sent', 'cancelled', 'deferred', 'blocked', 'paused')),
    status_reason TEXT,
    approved BOOLEAN NOT NULL,
    approved_by TEXT,
    sent_at TIMESTAMPTZ,
    message_id TEXT UNIQUE,
    unsubscribe_token TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT sent_requires_approval CHECK (status <> 'sent' OR (approved AND approved_by IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS messages_due ON messages (status, scheduled_at);
CREATE TABLE IF NOT EXISTS replies (
    id SERIAL PRIMARY KEY,
    message_id INT REFERENCES messages(id) ON DELETE SET NULL,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    from_email TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    body TEXT,
    received_at TIMESTAMPTZ NOT NULL,
    label TEXT,
    classification JSONB,
    actions JSONB,
    source TEXT NOT NULL DEFAULT 'simulator',
    purged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS meetings (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    reply_id INT REFERENCES replies(id) ON DELETE SET NULL,
    contact_email TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    timezone TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'held',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts INT NOT NULL DEFAULT 0,
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS crm_links (
    object_type TEXT NOT NULL,
    local_ref TEXT NOT NULL,
    crm_id TEXT NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (object_type, local_ref)
);
CREATE TABLE IF NOT EXISTS mock_crm (
    id SERIAL PRIMARY KEY,
    object_type TEXT NOT NULL,
    properties JSONB NOT NULL,
    associations JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS settings_kv (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION scout_block_suppressed() RETURNS trigger AS $$
BEGIN
    IF NEW.status = 'scheduled' AND EXISTS (
        SELECT 1 FROM suppression s
        WHERE (s.kind = 'email' AND s.value = lower(NEW.to_email))
           OR (s.kind = 'domain' AND s.value = split_part(lower(NEW.to_email), '@', 2))
    ) THEN
        NEW.status := 'blocked';
        NEW.status_reason := 'suppressed (database trigger)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS messages_suppression ON messages;
CREATE TRIGGER messages_suppression BEFORE INSERT OR UPDATE ON messages
    FOR EACH ROW EXECUTE FUNCTION scout_block_suppressed();
"""

TABLES = (
    "settings_kv", "mock_crm", "crm_links", "jobs", "audit_log", "meetings", "replies", "messages",
    "suppression", "feedback", "drafts", "pages", "accounts",
)  # fmt: skip


def _adapt(value: Any) -> Any:
    """Dicts and lists of dicts go to JSONB; lists of scalars stay arrays (for `= ANY(%s)`). Wrap a list of strings
    in Jsonb explicitly when the column is JSONB."""
    if isinstance(value, dict) or (isinstance(value, list) and any(isinstance(v, dict) for v in value)):
        return Jsonb(value)
    return value


class Store:
    def __init__(self, url: str, *, min_size: int = 1, max_size: int = 6) -> None:
        self.url = url
        self.pool = ConnectionPool(url, min_size=min_size, max_size=max_size, kwargs={"row_factory": dict_row, "autocommit": False}, open=True)

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def tx(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with self.pool.connection() as conn:
            yield conn  # type: ignore[misc]

    # ------------------------------------------------------------------ schema
    def migrate(self) -> None:
        with self.tx() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(424242)")
            conn.execute(SCHEMA)  # type: ignore[arg-type]

    def reset(self) -> None:
        with self.tx() as conn:
            for table in TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")  # noqa: S608 - fixed identifiers
        self.migrate()

    def truncate(self) -> None:
        with self.tx() as conn:
            conn.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")

    # ------------------------------------------------------------------ generic helpers
    def all(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[dict[str, Any]]:
        with self.tx() as conn:
            return list(conn.execute(sql, _params(params)).fetchall())  # type: ignore[arg-type]

    def one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> dict[str, Any] | None:
        with self.tx() as conn:
            return conn.execute(sql, _params(params)).fetchone()  # type: ignore[arg-type]

    def run(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> int:
        with self.tx() as conn:
            return conn.execute(sql, _params(params)).rowcount  # type: ignore[arg-type]

    def scalar(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> Any:
        row = self.one(sql, params)
        return next(iter(row.values())) if row else None

    # ------------------------------------------------------------------ audit
    def audit(self, actor: str, action: str, entity: str, entity_id: object = None, detail: dict[str, Any] | None = None) -> None:
        self.run(
            "INSERT INTO audit_log (actor, action, entity, entity_id, detail) VALUES (%s, %s, %s, %s, %s)",
            (actor, action, entity, None if entity_id is None else str(entity_id), detail or {}),
        )

    # ------------------------------------------------------------------ settings
    def get_setting(self, key: str) -> Any:
        row = self.one("SELECT value FROM settings_kv WHERE key = %s", (key,))
        return row["value"] if row else None

    def set_setting(self, key: str, value: Any) -> None:
        self.run(
            "INSERT INTO settings_kv (key, value, updated_at) VALUES (%s, %s, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            (key, Jsonb(value)),
        )

    # ------------------------------------------------------------------ suppression
    def suppressed(self, email: str) -> dict[str, Any] | None:
        email = email.lower().strip()
        domain = email.split("@", 1)[-1]
        return self.one(
            "SELECT * FROM suppression WHERE (kind = 'email' AND value = %s) OR (kind = 'domain' AND value = %s) LIMIT 1",
            (email, domain),
        )

    def suppressed_emails(self) -> set[str]:
        return {r["value"] for r in self.all("SELECT value FROM suppression WHERE kind = 'email'")}

    def suppress(self, value: str, *, kind: str, reason: str, source: str) -> bool:
        return (
            self.run(
                "INSERT INTO suppression (value, kind, reason, source) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (value.lower().strip(), kind, reason, source),
            )
            > 0
        )

    # ------------------------------------------------------------------ jobs
    def enqueue(self, kind: str, payload: dict[str, Any] | None = None) -> int:
        row = self.one("INSERT INTO jobs (kind, payload) VALUES (%s, %s) RETURNING id", (kind, Jsonb(payload or {})))
        assert row is not None
        return int(row["id"])

    def claim_job(self) -> dict[str, Any] | None:
        return self.one(
            "UPDATE jobs SET status = 'running', started_at = now(), attempts = attempts + 1 WHERE id = ("
            " SELECT id FROM jobs WHERE status = 'queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1"
            ") RETURNING *"
        )

    def finish_job(self, job_id: int, *, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        self.run(
            "UPDATE jobs SET status = %s, result = %s, error = %s, finished_at = now() WHERE id = %s",
            ("failed" if error else "done", Jsonb(result or {}), error, job_id),
        )


def _params(params: Sequence[Any] | dict[str, Any]) -> Sequence[Any] | dict[str, Any]:
    if isinstance(params, dict):
        return {k: _adapt(v) for k, v in params.items()}
    return [_adapt(v) for v in params]


def jsonable(value: Any) -> Any:
    """Rows to JSON-safe dicts for the API (datetimes to ISO strings)."""
    return json.loads(json.dumps(value, default=_default))


def _default(value: Any) -> Any:
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")
