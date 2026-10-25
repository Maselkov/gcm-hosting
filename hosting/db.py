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
    created_at REAL NOT NULL
);
"""


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
        row = conn.execute("SELECT id, name FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return {"id": row[0], "name": row[1]} if row else None


def bases_of(account_id):
    with transaction() as conn:
        rows = conn.execute(
            "SELECT slug, api_key, created_at FROM bases WHERE owner_id = ? ORDER BY created_at",
            (account_id,),
        ).fetchall()
    return [{"slug": s, "api_key": k, "created_at": c} for s, k, c in rows]


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
        return "That name is taken."
    return None


def owner_of(slug):
    with transaction() as conn:
        row = conn.execute("SELECT owner_id FROM bases WHERE slug = ?", (slug,)).fetchone()
    return row[0] if row else None


def release_base(slug):
    with transaction() as conn:
        conn.execute("DELETE FROM bases WHERE slug = ?", (slug,))
