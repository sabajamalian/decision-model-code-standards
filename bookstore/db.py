"""SQLite connection handling and schema setup."""

from __future__ import annotations

import logging
import sqlite3

import click
from flask import Flask, current_app, g

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    bio TEXT
);

CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author_id INTEGER NOT NULL REFERENCES authors (id) ON DELETE RESTRICT,
    isbn TEXT UNIQUE,
    price REAL NOT NULL CHECK (price >= 0),
    stock INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0)
);
"""

SEED_AUTHORS = [
    ("Octavia E. Butler", "American science fiction author."),
    ("Ursula K. Le Guin", "Author of the Earthsea and Hainish books."),
]

SEED_BOOKS = [
    ("Kindred", 1, "9780807083697", 16.95, 12),
    ("Parable of the Sower", 1, "9781538732182", 15.99, 4),
    ("A Wizard of Earthsea", 2, "9780547773742", 9.99, 7),
]


def get_db() -> sqlite3.Connection:
    """Return the request-scoped SQLite connection, opening it on first use."""
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    """Close the request-scoped connection if one was opened."""
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(seed: bool = True) -> None:
    """Create the schema and, optionally, insert demo data when the tables are empty."""
    conn = get_db()
    conn.executescript(SCHEMA)
    if seed and conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0] == 0:
        conn.executemany("INSERT INTO authors (name, bio) VALUES (?, ?)", SEED_AUTHORS)
        conn.executemany(
            "INSERT INTO books (title, author_id, isbn, price, stock) VALUES (?, ?, ?, ?, ?)",
            SEED_BOOKS,
        )
        logger.info("Seeded %d authors and %d books", len(SEED_AUTHORS), len(SEED_BOOKS))
    conn.commit()


@click.command("init-db")
@click.option("--no-seed", is_flag=True, help="Create tables without demo data.")
def init_db_command(no_seed: bool) -> None:
    """Create the database tables (flask --app bookstore init-db)."""
    init_db(seed=not no_seed)
    click.echo("Initialized the database.")


def init_app(app: Flask) -> None:
    """Register database hooks and CLI commands, then make sure the schema exists."""
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    with app.app_context():
        init_db(seed=app.config.get("SEED_DATA", True))
