"""API exceptions and JSON error handlers."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Base class for errors that map to a JSON error response."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        """Store the human-readable message and optional structured details."""
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationError(ApiError):
    """Raised when a request payload or query parameter is invalid."""

    status_code = 400
    code = "validation_error"


class NotFoundError(ApiError):
    """Raised when a requested resource does not exist."""

    status_code = 404
    code = "not_found"


def _error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body


def register_error_handlers(app: Flask) -> None:
    """Attach handlers so every error is returned as JSON with the right status code."""

    @app.errorhandler(ApiError)
    def handle_api_error(exc: ApiError) -> tuple[Any, int]:
        """Translate an ApiError into its JSON representation."""
        return jsonify(_error_body(exc.code, exc.message, exc.details)), exc.status_code

    @app.errorhandler(sqlite3.IntegrityError)
    def handle_integrity_error(exc: sqlite3.IntegrityError) -> tuple[Any, int]:
        """Report constraint violations (duplicate ISBN, unknown author) as 409 Conflict."""
        logger.warning("Integrity error: %s", exc)
        return jsonify(_error_body("conflict", "The request conflicts with existing data.")), 409

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException) -> tuple[Any, int]:
        """Return framework HTTP errors (404, 405, 415...) as JSON."""
        status = exc.code or 500
        code = (exc.name or "error").lower().replace(" ", "_")
        return jsonify(_error_body(code, exc.description or "")), status

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception) -> tuple[Any, int]:
        """Log unexpected failures and hide internals from the client."""
        logger.exception("Unhandled error: %s", exc)
        return jsonify(_error_body("internal_error", "An unexpected error occurred.")), 500
