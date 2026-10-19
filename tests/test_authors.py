from __future__ import annotations


def test_list_authors_sorted_by_name(client):
    body = client.get("/authors").get_json()
    assert [a["name"] for a in body["items"]] == ["Octavia E. Butler", "Ursula K. Le Guin"]


def test_create_and_get_author(client):
    resp = client.post("/authors", json={"name": "N. K. Jemisin", "bio": "Broken Earth trilogy."})
    assert resp.status_code == 201
    author = resp.get_json()
    got = client.get(resp.headers["Location"])
    assert got.status_code == 200
    assert got.get_json() == author


def test_create_author_validation(client):
    resp = client.post("/authors", json={"name": "  ", "bio": 5})
    assert resp.status_code == 400
    assert set(resp.get_json()["error"]["details"]) == {"name", "bio"}


def test_duplicate_author_conflicts(client):
    assert client.post("/authors", json={"name": "Octavia E. Butler"}).status_code == 409


def test_get_missing_author(client):
    assert client.get("/authors/42").status_code == 404


def test_list_author_books(client):
    resp = client.get("/authors/1/books")
    assert resp.status_code == 200
    assert [b["title"] for b in resp.get_json()["items"]] == ["Kindred", "Parable of the Sower"]


def test_list_books_for_missing_author(client):
    assert client.get("/authors/42/books").status_code == 404
