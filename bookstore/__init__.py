"""Bookstore REST API: a small Flask backend used as the target of the standards check."""

from __future__ import annotations

import logging
import os
from typing import Any

from flask import Flask, jsonify

from bookstore import db
from bookstore.errors import register_error_handlers
from bookstore.routes.authors import authors_bp
from bookstore.routes.books import books_bp


def create_app(config: dict[str, Any] | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        config: Optional overrides applied on top of the defaults (useful in tests).

    Returns:
        The configured Flask app.
    """
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=os.environ.get("BOOKSTORE_DATABASE", os.path.join(app.instance_path, "bookstore.sqlite3")),
        SEED_DATA=True,
    )
    if config:
        app.config.update(config)

    logging.basicConfig(level=os.environ.get("BOOKSTORE_LOG_LEVEL", "INFO"))
    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    register_error_handlers(app)
    app.register_blueprint(books_bp)
    app.register_blueprint(authors_bp)

    @app.get("/health")
    def health() -> tuple[Any, int]:
        """Report that the service is up."""
        return jsonify(status="ok"), 200

    return app
