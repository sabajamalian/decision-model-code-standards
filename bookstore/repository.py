"""Data access functions. All SQL uses query parameters."""

from __future__ import annotations

import sqlite3
from typing import Any

BOOK_SELECT = "SELECT id, title, author_id, isbn, price, stock FROM books"


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def list_books(conn: sqlite3.Connection, limit: int, offset: int) -> list[dict[str, Any]]:
    """Return a page of books ordered by id."""
    rows = conn.execute(BOOK_SELECT + " ORDER BY id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [dict(row) for row in rows]


def get_book(conn: sqlite3.Connection, book_id: int) -> dict[str, Any] | None:
    """Return one book by id, or None if it does not exist."""
    row = conn.execute(BOOK_SELECT + " WHERE id = ?", (book_id,)).fetchone()
    return _row_to_dict(row)


def create_book(conn: sqlite3.Connection, book: dict[str, Any]) -> dict[str, Any]:
    """Insert a validated book and return the stored record."""
    cur = conn.execute(
        "INSERT INTO books (title, author_id, isbn, price, stock) VALUES (?, ?, ?, ?, ?)",
        (book["title"], book["author_id"], book["isbn"], book["price"], book["stock"]),
    )
    conn.commit()
    return get_book(conn, cur.lastrowid)  # type: ignore[return-value]


def update_book(conn: sqlite3.Connection, book_id: int, book: dict[str, Any]) -> dict[str, Any] | None:
    """Replace a book's fields. Returns the updated record, or None if it does not exist."""
    cur = conn.execute(
        "UPDATE books SET title = ?, author_id = ?, isbn = ?, price = ?, stock = ? WHERE id = ?",
        (book["title"], book["author_id"], book["isbn"], book["price"], book["stock"], book_id),
    )
    conn.commit()
    return get_book(conn, book_id) if cur.rowcount else None


def delete_book(conn: sqlite3.Connection, book_id: int) -> bool:
    """Delete a book. Returns True if a row was removed."""
    cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    conn.commit()
    return cur.rowcount > 0


def list_authors(conn: sqlite3.Connection, limit: int, offset: int) -> list[dict[str, Any]]:
    """Return a page of authors ordered by name."""
    rows = conn.execute("SELECT id, name, bio FROM authors ORDER BY name LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [dict(row) for row in rows]


def get_author(conn: sqlite3.Connection, author_id: int) -> dict[str, Any] | None:
    """Return one author by id, or None if it does not exist."""
    row = conn.execute("SELECT id, name, bio FROM authors WHERE id = ?", (author_id,)).fetchone()
    return _row_to_dict(row)


def create_author(conn: sqlite3.Connection, author: dict[str, Any]) -> dict[str, Any]:
    """Insert a validated author and return the stored record."""
    cur = conn.execute("INSERT INTO authors (name, bio) VALUES (?, ?)", (author["name"], author["bio"]))
    conn.commit()
    return get_author(conn, cur.lastrowid)  # type: ignore[return-value]


def list_books_by_author(conn: sqlite3.Connection, author_id: int) -> list[dict[str, Any]]:
    """Return every book written by the given author, ordered by title."""
    rows = conn.execute(BOOK_SELECT + " WHERE author_id = ? ORDER BY title", (author_id,)).fetchall()
    return [dict(row) for row in rows]
