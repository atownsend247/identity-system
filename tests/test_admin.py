from __future__ import annotations


def _login(client, auth, email="alex@example.com", password="password123", name="Alex"):
    auth.create_user(email, password, name=name)
    client.post("/login", data={"email": email, "password": password})


def test_admin_requires_login(client):
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?rd=/admin"


def test_admin_requires_the_allowlisted_email(client, auth):
    _login(client, auth)  # alex@example.com is not in settings.admin_emails
    resp = client.get("/admin")
    assert resp.status_code == 403


def test_admin_lists_users_and_last_login(client, auth):
    auth.create_user("nolog@example.com", "password123", name="Never Logged In")
    _login(client, auth, email="admin@example.com", password="password123", name="Admin")

    resp = client.get("/admin")
    assert resp.status_code == 200
    body = resp.text
    assert "admin@example.com" in body
    assert "nolog@example.com" in body
    assert "never" in body  # nolog@example.com has no last_login_at yet
