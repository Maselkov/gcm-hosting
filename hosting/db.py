"""The accounts and the bases they own. Only this app's own records: what
happens inside a base stays in that base's container."""

import os
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager

from hosting import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    -- "discord:<id>", or "dev:<name>" with DEV_LOGIN
    identity TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS bases (
    slug TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES accounts(id),
    -- Kept so the owner can see their config.lua again. The base holds
    -- the same key in its environment.
    api_key TEXT NOT NULL,
    created_at REAL NOT NULL,
    -- Disk quota (hosting/quota.py): NULL is config.QUOTA_BYTES.
    quota_bytes INTEGER,
    -- Its data volume's size at the last check.
    size_bytes INTEGER,
    -- Set while it's stopped for being over quota.
    suspended_at REAL,
    -- Set while the host has stopped it (the control panel): it stays
    -- stopped until started there, whatever its quota.
    held_at REAL,
    -- When its game last reported (hosting/activity.py), NULL if never.
    last_report_at REAL
);
"""
# Columns added since a table was first made: (table, column, definition).
ADDED_COLUMNS = [("bases", "held_at", "REAL"), ("bases", "last_report_at", "REAL")]


def path():
    return os.path.join(config.DATA_DIR, "hosting.db")


@contextmanager
def transaction():
    with closing(sqlite3.connect(path(), timeout=10)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            yield conn


def init():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with transaction() as conn:
        conn.executescript(SCHEMA)
        for table, column, definition in ADDED_COLUMNS:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def account_for(identity, name):
    """The account signed in as identity, made on first sign-in. Its name
    follows the identity's current one."""
    with transaction() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE identity = ?", (identity,)).fetchone()
        if row:
            conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (name, row[0]))
            return row[0]
        account_id = f"acc_{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO accounts (id, identity, name, created_at) VALUES (?, ?, ?, ?)",
            (account_id, identity, name, time.time()),
        )
        return account_id


def account(account_id):
    with transaction() as conn:
        row = conn.execute("SELECT id, name, identity FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return {"id": row[0], "name": row[1], "identity": row[2]} if row else None


BASE_COLUMNS = (
    "bases.slug, bases.api_key, bases.created_at, bases.quota_bytes, bases.size_bytes, bases.suspended_at, "
    "bases.held_at, bases.last_report_at, accounts.name, accounts.identity"
)
BASE_FROM = "bases JOIN accounts ON accounts.id = bases.owner_id"


def _base(row):
    slug, api_key, created_at, quota, size, suspended_at, held_at, last_report_at, owner_name, owner_identity = row
    return {
        "slug": slug,
        "api_key": api_key,
        "created_at": created_at,
        "quota_bytes": quota if quota is not None else config.QUOTA_BYTES,
        "custom_quota": quota is not None,
        "size_bytes": size,
        "suspended_at": suspended_at,
        "held_at": held_at,
        "last_report_at": last_report_at,
        "owner_name": owner_name,
        "owner_identity": owner_identity,
    }


def bases_of(account_id):
    with transaction() as conn:
        rows = conn.execute(
            f"SELECT {BASE_COLUMNS} FROM {BASE_FROM} WHERE owner_id = ? ORDER BY bases.created_at",
            (account_id,),
        ).fetchall()
    return [_base(r) for r in rows]


def all_bases():
    with transaction() as conn:
        rows = conn.execute(f"SELECT {BASE_COLUMNS} FROM {BASE_FROM} ORDER BY bases.slug").fetchall()
    return [_base(r) for r in rows]


def base(slug):
    with transaction() as conn:
        row = conn.execute(f"SELECT {BASE_COLUMNS} FROM {BASE_FROM} WHERE bases.slug = ?", (slug,)).fetchone()
    return _base(row) if row else None


def set_held(slug, held_at):
    """Marks a base stopped (held_at, a time) or started (None) by the host."""
    with transaction() as conn:
        conn.execute("UPDATE bases SET held_at = ? WHERE slug = ?", (held_at, slug))


def record_report(slug, last_report_at):
    with transaction() as conn:
        conn.execute("UPDATE bases SET last_report_at = ? WHERE slug = ?", (last_report_at, slug))


def count_accounts():
    with transaction() as conn:
        return conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]


def record_size(slug, size_bytes, suspended_at):
    with transaction() as conn:
        conn.execute(
            "UPDATE bases SET size_bytes = ?, suspended_at = ? WHERE slug = ?",
            (size_bytes, suspended_at, slug),
        )


def set_quota(slug, quota_bytes):
    """Returns False if there's no such base."""
    with transaction() as conn:
        return conn.execute("UPDATE bases SET quota_bytes = ? WHERE slug = ?", (quota_bytes, slug)).rowcount == 1


NAME_TAKEN = "That name is taken."


def claim_base(slug, owner_id, api_key):
    """Records a new base, unless the slug is taken or a limit is reached.
    Returns None, or why not."""
    try:
        with transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            (owned,) = conn.execute("SELECT COUNT(*) FROM bases WHERE owner_id = ?", (owner_id,)).fetchone()
            if owned >= config.MAX_BASES_PER_ACCOUNT:
                return "You have as many bases as an account can."
            (total,) = conn.execute("SELECT COUNT(*) FROM bases").fetchone()
            if total >= config.MAX_BASES:
                return "This host is full for now."
            conn.execute(
                "INSERT INTO bases (slug, owner_id, api_key, created_at) VALUES (?, ?, ?, ?)",
                (slug, owner_id, api_key, time.time()),
            )
    except sqlite3.IntegrityError:
        return NAME_TAKEN
    return None


def rename_base(old, new):
    """Returns NAME_TAKEN if new is, otherwise None."""
    try:
        with transaction() as conn:
            conn.execute("UPDATE bases SET slug = ? WHERE slug = ?", (new, old))
    except sqlite3.IntegrityError:
        return NAME_TAKEN
    return None


def owner_of(slug):
    with transaction() as conn:
        row = conn.execute("SELECT owner_id FROM bases WHERE slug = ?", (slug,)).fetchone()
    return row[0] if row else None


def release_base(slug):
    with transaction() as conn:
        conn.execute("DELETE FROM bases WHERE slug = ?", (slug,))
