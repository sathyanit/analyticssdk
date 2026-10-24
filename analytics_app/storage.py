import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    anonymous_id TEXT NOT NULL, user_id TEXT, session_id TEXT,
                    occurred_at TEXT NOT NULL, received_at TEXT NOT NULL,
                    path TEXT, source TEXT NOT NULL CHECK(source IN ('browser','server')),
                    properties TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_time ON events(occurred_at);
                CREATE INDEX IF NOT EXISTS events_name_time ON events(name, occurred_at);
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                    request_hash TEXT NOT NULL, anonymous_id TEXT NOT NULL,
                    total_cents INTEGER NOT NULL, items TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def insert(self, db, event, source):
        result = db.execute("""INSERT INTO events
            (event_id,name,anonymous_id,user_id,session_id,occurred_at,received_at,path,source,properties)
            VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING""", (
            str(event.event_id), event.name, event.anonymous_id, event.user_id,
            event.session_id, stamp(event.occurred_at), stamp(utcnow()), event.path,
            source, json.dumps(event.properties, separators=(",", ":")),
        ))
        return result.rowcount
