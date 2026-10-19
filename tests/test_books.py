from __future__ import annotations

VALID_BOOK = {"title": "The Dispossessed", "author_id": 2, "price": 12.5, "stock": 3, "isbn": "9780061054884"}


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_list_books_returns_seed_data(client):
    resp = client.get("/books")
    assert resp.status_code == 200
    body = resp.get_json()
    assert [b["title"] for b in body["items"]] == ["Kindred", "Parable of the Sower", "A Wizard of Earthsea"]
    assert body["limit"] == 20 and body["offset"] == 0


def test_list_books_pagination(client):
    body = client.get("/books?limit=1&offset=1").get_json()
    assert [b["title"] for b in body["items"]] == ["Parable of the Sower"]


def test_list_books_rejects_bad_pagination(client):
    assert client.get("/books?limit=abc").status_code == 400
    assert client.get("/books?limit=500").status_code == 400
    assert client.get("/books?offset=-1").status_code == 400


def test_get_book(client):
    resp = client.get("/books/1")
    assert resp.status_code == 200
    assert resp.get_json()["title"] == "Kindred"


def test_get_missing_book_returns_404(client):
    resp = client.get("/books/999")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"


def test_create_book(client):
    resp = client.post("/books", json=VALID_BOOK)
    assert resp.status_code == 201
    book = resp.get_json()
    assert book["title"] == "The Dispossessed"
    assert resp.headers["Location"] == f"/books/{book['id']}"
    assert client.get(resp.headers["Location"]).status_code == 200


def test_create_book_validation_errors(client):
    resp = client.post("/books", json={"title": "", "author_id": "x", "price": -1, "isbn": "12"})
    assert resp.status_code == 400
    details = resp.get_json()["error"]["details"]
    assert set(details) == {"title", "author_id", "price", "isbn"}


def test_create_book_requires_json_object(client):
    assert client.post("/books", data="not json", content_type="text/plain").status_code == 400
    assert client.post("/books", json=[1, 2]).status_code == 400


def test_create_book_unknown_author_conflicts(client):
    resp = client.post("/books", json={**VALID_BOOK, "author_id": 999})
    assert resp.status_code == 409


def test_create_book_duplicate_isbn_conflicts(client):
    resp = client.post("/books", json={**VALID_BOOK, "isbn": "9780807083697"})
    assert resp.status_code == 409


def test_replace_book(client):
    resp = client.put("/books/1", json={**VALID_BOOK, "title": "Kindred (2nd ed.)", "author_id": 1})
    assert resp.status_code == 200
    assert resp.get_json()["title"] == "Kindred (2nd ed.)"


def test_replace_missing_book_returns_404(client):
    assert client.put("/books/999", json=VALID_BOOK).status_code == 404


def test_delete_book(client):
    assert client.delete("/books/3").status_code == 204
    assert client.get("/books/3").status_code == 404
    assert client.delete("/books/3").status_code == 404


def test_method_not_allowed_is_json(client):
    resp = client.patch("/books/1", json={})
    assert resp.status_code == 405
    assert "error" in resp.get_json()
