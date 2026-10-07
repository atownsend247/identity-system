from __future__ import annotations


def _login(client, auth, email="alex@example.com", password="password123", name="Alex"):
    auth.create_user(email, password, name=name)
    client.post("/api/login", json={"email": email, "password": password})


def test_admin_users_requires_login(client):
    resp = client.get("/api/admin/users")
    assert resp.status_code == 401


def test_admin_users_requires_the_allowlisted_email(client, auth):
    _login(client, auth)  # alex@example.com is not in settings.admin_emails
    resp = client.get("/api/admin/users")
    assert resp.status_code == 403


def test_admin_users_lists_users_and_last_login(client, auth):
    auth.create_user("nolog@example.com", "password123", name="Never Logged In")
    _login(client, auth, email="admin@example.com", password="password123", name="Admin")

    resp = client.get("/api/admin/users")
    assert resp.status_code == 200
    by_email = {u["email"]: u for u in resp.json()}

    assert by_email["admin@example.com"]["last_login_at"] is not None
    assert by_email["nolog@example.com"]["last_login_at"] is None
    assert by_email["nolog@example.com"]["name"] == "Never Logged In"


def test_admin_can_create_a_user(client, auth):
    _login(client, auth, email="admin@example.com", password="password123", name="Admin")

    resp = client.post(
        "/api/admin/users",
        json={"email": "new@example.com", "password": "password123", "name": "New Person"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "new@example.com"
    assert body["name"] == "New Person"
    assert body["totp_enabled"] is False
    assert body["last_login_at"] is None

    listed = {u["email"] for u in client.get("/api/admin/users").json()}
    assert "new@example.com" in listed

    # The new account can actually sign in with the password the admin set.
    other = client.__class__(client.app, base_url=client.base_url)
    login = other.post("/api/login", json={"email": "new@example.com", "password": "password123"})
    assert login.status_code == 200


def test_admin_create_user_defaults_name_from_email(client, auth):
    _login(client, auth, email="admin@example.com", password="password123", name="Admin")
    resp = client.post("/api/admin/users", json={"email": "noname@example.com", "password": "password123"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "noname"


def test_admin_create_user_requires_login_and_admin(client, auth):
    body = {"email": "x@example.com", "password": "password123"}
    assert client.post("/api/admin/users", json=body).status_code == 401

    _login(client, auth, email="alex@example.com", password="password123")
    assert client.post("/api/admin/users", json=body).status_code == 403


def test_admin_create_user_rejects_duplicate_email(client, auth):
    _login(client, auth, email="admin@example.com", password="password123", name="Admin")
    body = {"email": "admin@example.com", "password": "password123"}
    assert client.post("/api/admin/users", json=body).status_code == 409


def test_admin_create_user_rejects_weak_password(client, auth):
    _login(client, auth, email="admin@example.com", password="password123", name="Admin")
    resp = client.post("/api/admin/users", json={"email": "weak@example.com", "password": "short"})
    assert resp.status_code == 422
