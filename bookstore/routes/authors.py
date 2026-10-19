"""Routes for /authors."""

from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request, url_for

from bookstore import repository
from bookstore.db import get_db
from bookstore.errors import NotFoundError
from bookstore.validation import get_json_object, pagination, validate_author

logger = logging.getLogger(__name__)

authors_bp = Blueprint("authors", __name__, url_prefix="/authors")


@authors_bp.get("")
def list_authors() -> tuple[Any, int]:
    """List authors with `limit`/`offset` pagination."""
    limit, offset = pagination(request)
    authors = repository.list_authors(get_db(), limit, offset)
    return jsonify(items=authors, limit=limit, offset=offset), 200


@authors_bp.post("")
def create_author() -> tuple[Any, int, dict[str, str]]:
    """Create an author from a validated JSON payload and return 201 with a Location header."""
    payload = validate_author(get_json_object(request))
    author = repository.create_author(get_db(), payload)
    logger.info("Created author %s", author["id"])
    return jsonify(author), 201, {"Location": url_for("authors.get_author", author_id=author["id"])}


@authors_bp.get("/<int:author_id>")
def get_author(author_id: int) -> tuple[Any, int]:
    """Return a single author or 404."""
    author = repository.get_author(get_db(), author_id)
    if author is None:
        raise NotFoundError(f"Author {author_id} was not found.")
    return jsonify(author), 200


@authors_bp.get("/<int:author_id>/books")
def list_author_books(author_id: int) -> tuple[Any, int]:
    """List the books written by an author, or 404 if the author does not exist."""
    conn = get_db()
    if repository.get_author(conn, author_id) is None:
        raise NotFoundError(f"Author {author_id} was not found.")
    return jsonify(items=repository.list_books_by_author(conn, author_id)), 200
