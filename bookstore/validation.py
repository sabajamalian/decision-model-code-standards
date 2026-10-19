"""Request payload and query-string validation helpers."""

from __future__ import annotations

from typing import Any

from flask import Request

from bookstore.errors import ValidationError

MAX_PAGE_SIZE = 100


def get_json_object(request: Request) -> dict[str, Any]:
    """Return the request body as a JSON object or raise ValidationError."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object.")
    return data


def _require_str(data: dict[str, Any], field: str, errors: dict[str, str], max_len: int) -> str | None:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        errors[field] = "must be a non-empty string"
        return None
    if len(value) > max_len:
        errors[field] = f"must be at most {max_len} characters"
        return None
    return value.strip()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_book(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a full book payload and return the cleaned values."""
    errors: dict[str, str] = {}
    title = _require_str(data, "title", errors, max_len=200)

    author_id = data.get("author_id")
    if not _is_int(author_id) or author_id < 1:
        errors["author_id"] = "must be a positive integer"

    price = data.get("price")
    if not isinstance(price, (int, float)) or isinstance(price, bool) or price < 0:
        errors["price"] = "must be a non-negative number"

    stock = data.get("stock", 0)
    if not _is_int(stock) or stock < 0:
        errors["stock"] = "must be a non-negative integer"

    isbn = data.get("isbn")
    if isbn is not None and (not isinstance(isbn, str) or not isbn.isdigit() or len(isbn) not in (10, 13)):
        errors["isbn"] = "must be a 10 or 13 digit string"

    if errors:
        raise ValidationError("Invalid book payload.", errors)
    return {"title": title, "author_id": author_id, "price": float(price), "stock": stock, "isbn": isbn}


def validate_author(data: dict[str, Any]) -> dict[str, Any]:
    """Validate an author payload and return the cleaned values."""
    errors: dict[str, str] = {}
    name = _require_str(data, "name", errors, max_len=120)
    bio = data.get("bio")
    if bio is not None and not isinstance(bio, str):
        errors["bio"] = "must be a string"
    if errors:
        raise ValidationError("Invalid author payload.", errors)
    return {"name": name, "bio": bio}


def pagination(request: Request) -> tuple[int, int]:
    """Parse and bound the `limit` and `offset` query parameters."""
    try:
        limit = int(request.args.get("limit", 20))
        offset = int(request.args.get("offset", 0))
    except ValueError as exc:
        raise ValidationError("limit and offset must be integers.") from exc
    if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
        raise ValidationError(f"limit must be 1-{MAX_PAGE_SIZE} and offset must be >= 0.")
    return limit, offset
