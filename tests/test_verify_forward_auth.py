from __future__ import annotations


def test_verify_without_cookie_is_401(client):
    resp = client.get("/verify")
    assert resp.status_code == 401


def test_verify_with_garbage_cookie_is_401(client):
    client.cookies.set("identity_session", "not-a-real-token", domain="example.com")
    resp = client.get("/verify")
    assert resp.status_code == 401


def test_verify_with_valid_session_returns_identity_headers(client, auth):
    user = auth.create_user("alex@example.com", "password123", name="Alex")
    client.post("/login", data={"email": "alex@example.com", "password": "password123"})

    resp = client.get("/verify")
    assert resp.status_code == 200
    assert resp.headers["x-auth-user-id"] == user.id
    assert resp.headers["x-auth-user-email"] == "alex@example.com"
    assert resp.headers["x-auth-user-name"] == "Alex"


def test_me_mirrors_verify_as_json(client, auth):
    auth.create_user("alex@example.com", "password123", name="Alex")
    client.post("/login", data={"email": "alex@example.com", "password": "password123"})

    resp = client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "alex@example.com"
    assert body["totp_enabled"] is False
