from __future__ import annotations


def _create(auth):
    return auth.create_user("alex@example.com", "password123", name="Alex")


def test_login_sets_cookie_and_returns_default_redirect(client, auth):
    _create(auth)
    resp = client.post(
        "/api/login", json={"email": "alex@example.com", "password": "password123"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "alex@example.com"
    assert body["redirect_to"] == "/account"
    assert "identity_session" in resp.cookies


def test_login_wrong_password_returns_401_json(client, auth):
    _create(auth)
    resp = client.post(
        "/api/login", json={"email": "alex@example.com", "password": "wrong"}
    )
    assert resp.status_code == 401
    assert "incorrect email or password" in resp.json()["detail"]
    assert "identity_session" not in resp.cookies


def test_login_redirect_to_uses_trusted_rd(client, auth):
    _create(auth)
    rd = "https://app1.example.com/dashboard"
    resp = client.post(
        "/api/login",
        json={"email": "alex@example.com", "password": "password123", "rd": rd},
    )
    assert resp.status_code == 200
    assert resp.json()["redirect_to"] == rd


def test_login_redirect_to_falls_back_for_untrusted_rd(client, auth):
    _create(auth)
    resp = client.post(
        "/api/login",
        json={
            "email": "alex@example.com",
            "password": "password123",
            "rd": "https://evil.example.net/phish",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["redirect_to"] == "/account"


def test_logout_clears_cookie_and_the_old_session_stops_working(client, auth):
    _create(auth)
    client.post("/api/login", json={"email": "alex@example.com", "password": "password123"})
    assert client.get("/me").status_code == 200

    resp = client.post("/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert client.get("/me").status_code == 401
