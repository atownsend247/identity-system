from __future__ import annotations


def _login(client, auth, email="alex@example.com", password="password123"):
    auth.create_user(email, password, name="Alex")
    client.post("/login", data={"email": email, "password": password})


def test_update_name(client, auth):
    _login(client, auth)
    resp = client.patch("/profile", json={"name": "Alexandra"})
    assert resp.status_code == 204
    assert client.get("/me").json()["name"] == "Alexandra"


def test_update_email_requires_current_password(client, auth):
    _login(client, auth)

    bad = client.patch(
        "/profile",
        json={"email": "alexandra@example.com", "current_password": "wrong"},
    )
    assert bad.status_code == 401
    assert client.get("/me").json()["email"] == "alex@example.com"

    ok = client.patch(
        "/profile",
        json={"email": "alexandra@example.com", "current_password": "password123"},
    )
    assert ok.status_code == 204
    assert client.get("/me").json()["email"] == "alexandra@example.com"


def test_update_email_rejects_duplicate(client, auth):
    auth.create_user("sam@example.com", "password123", name="Sam")
    _login(client, auth)

    resp = client.patch("/profile", json={"email": "SAM@example.com"})
    assert resp.status_code == 409


def test_change_password_then_old_session_is_revoked(client, auth):
    _login(client, auth)

    resp = client.post("/password", json={"new_password": "brand-new-secret"})
    assert resp.status_code == 204

    assert client.get("/me").status_code == 401

    relogin = client.post(
        "/login",
        data={"email": "alex@example.com", "password": "brand-new-secret"},
        follow_redirects=False,
    )
    assert relogin.status_code == 303
