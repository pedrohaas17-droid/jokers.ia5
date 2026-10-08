"""SQLite/PostgreSQL persistence for CAKTO ADS AI.

All tenant-owned records carry a user_id and are queried with that owner ID.
SQLite remains the zero-setup local store; hosted deployments can use PostgreSQL
through DATABASE_URL/POSTGRES_URL without changing the API or UI contract.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    import psycopg
except ImportError:  # psycopg is required only when a PostgreSQL URL is configured.
    psycopg = None

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("CAKTO_DB_PATH", ROOT / "data" / "cakto_ads.db"))
INTEGRITY_ERRORS = (sqlite3.IntegrityError,) + ((psycopg.IntegrityError,) if psycopg else ())


class HybridRow(dict):
    """Dictionary row that also supports SQLite-style integer indexing."""

    def __init__(self, columns, values):
        values = tuple(values)
        super().__init__((name, value) for name, value in zip(columns, values))
        self._ordered_values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._ordered_values[key]
        return super().__getitem__(key)


def hybrid_row(cursor):
    description = cursor.description or ()
    columns = [column.name if hasattr(column, "name") else column[0] for column in description]
    return lambda values: HybridRow(columns, values)


class PostgresConnection:
    """Small DB-API wrapper translating existing qmark SQL to psycopg syntax."""

    def __init__(self, connection):
        self._connection = connection

    def execute(self, query, parameters=None):
        return self._connection.execute(query.replace("?", "%s"), parameters)

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def close(self):
        return self._connection.close()


def _database_url():
    for key in ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL", "POSTGRES_URL_NON_POOLING"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def using_postgres() -> bool:
    return bool(_database_url())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    database_url = _database_url()
    if database_url:
        if psycopg is None:
            raise RuntimeError("DATABASE_URL está definido, mas psycopg não está instalado.")
        connection = psycopg.connect(
            database_url,
            row_factory=hybrid_row,
            connect_timeout=10,
        )
        # Pooled serverless Postgres endpoints may not preserve prepared statements.
        connection.prepare_threshold = None
        return PostgresConnection(connection)
    if os.environ.get("VERCEL") == "1":
        raise RuntimeError("Configure DATABASE_URL com um PostgreSQL persistente na Vercel.")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


@contextmanager
def db_session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def json_dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_load(value, fallback=None):
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _split_sql_script(script: str):
    for part in script.split(";"):
        lines = [line for line in part.splitlines() if not line.lstrip().startswith("--")]
        statement = "\n".join(lines).strip()
        if statement:
            yield statement


def init_db() -> None:
    postgres = using_postgres()
    with db_session() as conn:
        if not postgres:
            conn.execute("PRAGMA journal_mode = WAL")
        schema = """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

            CREATE TABLE IF NOT EXISTS password_resets (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                niche TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                price REAL,
                promo_price REAL,
                sales_url TEXT NOT NULL DEFAULT '',
                checkout_url TEXT NOT NULL DEFAULT '',
                benefits TEXT NOT NULL DEFAULT '',
                differentiators TEXT NOT NULL DEFAULT '',
                target_audience TEXT NOT NULL DEFAULT '',
                guarantee TEXT NOT NULL DEFAULT '',
                bonuses TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                headline TEXT NOT NULL DEFAULT '',
                subheadline TEXT NOT NULL DEFAULT '',
                faq TEXT NOT NULL DEFAULT '',
                testimonials TEXT NOT NULL DEFAULT '',
                cta TEXT NOT NULL DEFAULT '',
                mechanism TEXT NOT NULL DEFAULT '',
                relevant_links TEXT NOT NULL DEFAULT '',
                unit_cost REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_products_user ON products(user_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                product_id TEXT NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                current_step INTEGER NOT NULL DEFAULT 0,
                selected_avatar_id TEXT,
                selected_angle_id TEXT,
                selected_offer_id TEXT,
                selected_copy_id TEXT,
                selected_creative_id TEXT,
                stale_from TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_projects_product ON projects(product_id);

            CREATE TABLE IF NOT EXISTS avatars (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_avatars_project ON avatars(project_id, created_at);

            CREATE TABLE IF NOT EXISTS angles (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_angles_project ON angles(project_id, created_at);

            CREATE TABLE IF NOT EXISTS offers (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_offers_project ON offers(project_id, created_at);

            CREATE TABLE IF NOT EXISTS copies (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_copies_project ON copies(project_id, created_at);

            CREATE TABLE IF NOT EXISTS creatives (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_creatives_project ON creatives(project_id, created_at);

            CREATE TABLE IF NOT EXISTS campaigns (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS metrics (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_metrics_project ON metrics(project_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS ab_tests (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_abtests_project ON ab_tests(project_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS product_analyses (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                source_url TEXT NOT NULL,
                status TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_product_analyses_product ON product_analyses(product_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS project_insights (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- Additive phase-three persistence for strategy decisions and history.
            CREATE TABLE IF NOT EXISTS campaign_agent_runs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                product_id TEXT REFERENCES products(id) ON DELETE SET NULL,
                project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'strategies',
                selected_strategy_id TEXT NOT NULL DEFAULT '',
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_runs_user ON campaign_agent_runs(user_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_agent_runs_product ON campaign_agent_runs(product_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_agent_runs_project ON campaign_agent_runs(project_id);

            -- Short-lived, server-verified public page extracts for the review gate.
            CREATE TABLE IF NOT EXISTS campaign_agent_page_analyses (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                requested_url TEXT NOT NULL,
                source_url TEXT NOT NULL,
                data TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_page_analyses_user ON campaign_agent_page_analyses(user_id, expires_at);
            """
        if postgres:
            schema = schema.replace(" COLLATE NOCASE", "")
            for statement in _split_sql_script(schema):
                conn.execute(statement)
            product_columns = {
                row[0] for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND table_name=?",
                    ("products",),
                ).fetchall()
            }
        else:
            conn.executescript(schema)
            product_columns = {row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
        # Lightweight migration for databases created by phase one.
        migrations = {
            "headline": "TEXT NOT NULL DEFAULT ''", "subheadline": "TEXT NOT NULL DEFAULT ''",
            "faq": "TEXT NOT NULL DEFAULT ''", "testimonials": "TEXT NOT NULL DEFAULT ''",
            "cta": "TEXT NOT NULL DEFAULT ''", "mechanism": "TEXT NOT NULL DEFAULT ''",
            "relevant_links": "TEXT NOT NULL DEFAULT ''", "unit_cost": "REAL",
        }
        for column, definition in migrations.items():
            if column not in product_columns:
                conn.execute(f"ALTER TABLE products ADD COLUMN {column} {definition}")


def row_dict(row):
    return dict(row) if row is not None else None
