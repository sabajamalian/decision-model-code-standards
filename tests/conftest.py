from __future__ import annotations

from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from bookstore import create_app


@pytest.fixture()
def app(tmp_path) -> Iterator[Flask]:
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.sqlite3")})
    yield app


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    return app.test_client()
