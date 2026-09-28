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
