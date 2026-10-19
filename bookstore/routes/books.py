"""Routes for /books."""

from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request, url_for

from bookstore import repository
from bookstore.db import get_db
from bookstore.errors import NotFoundError
from bookstore.validation import get_json_object, pagination, validate_book

logger = logging.getLogger(__name__)

books_bp = Blueprint("books", __name__, url_prefix="/books")


@books_bp.get("")
def list_books() -> tuple[Any, int]:
    """List books with `limit`/`offset` pagination."""
    limit, offset = pagination(request)
    books = repository.list_books(get_db(), limit, offset)
    return jsonify(items=books, limit=limit, offset=offset), 200


@books_bp.post("")
def create_book() -> tuple[Any, int, dict[str, str]]:
    """Create a book from a validated JSON payload and return 201 with a Location header."""
    payload = validate_book(get_json_object(request))
    book = repository.create_book(get_db(), payload)
    logger.info("Created book %s", book["id"])
    return jsonify(book), 201, {"Location": url_for("books.get_book", book_id=book["id"])}


@books_bp.get("/<int:book_id>")
def get_book(book_id: int) -> tuple[Any, int]:
    """Return a single book or 404."""
    book = repository.get_book(get_db(), book_id)
    if book is None:
        raise NotFoundError(f"Book {book_id} was not found.")
    return jsonify(book), 200


@books_bp.put("/<int:book_id>")
def replace_book(book_id: int) -> tuple[Any, int]:
    """Replace every field of an existing book."""
    payload = validate_book(get_json_object(request))
    book = repository.update_book(get_db(), book_id, payload)
    if book is None:
        raise NotFoundError(f"Book {book_id} was not found.")
    logger.info("Updated book %s", book_id)
    return jsonify(book), 200


@books_bp.delete("/<int:book_id>")
def delete_book(book_id: int) -> tuple[str, int]:
    """Delete a book and return 204, or 404 if it does not exist."""
    if not repository.delete_book(get_db(), book_id):
        raise NotFoundError(f"Book {book_id} was not found.")
    logger.info("Deleted book %s", book_id)
    return "", 204
