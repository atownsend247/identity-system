from __future__ import annotations


def _create(auth):
    return auth.create_user("alex@example.com", "password123", name="Alex")


def test_login_sets_cookie_and_redirects_to_me_by_default(client, auth):
    _create(auth)
    resp = client.post(
        "/login",
        data={"email": "alex@example.com", "password": "password123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/me"
    assert "identity_session" in resp.cookies


def test_login_wrong_password_rerenders_form_with_401(client, auth):
    _create(auth)
    resp = client.post(
        "/login",
        data={"email": "alex@example.com", "password": "wrong"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert "incorrect email or password" in resp.text


def test_login_redirects_to_trusted_rd(client, auth):
    _create(auth)
    rd = "https://app1.example.com/dashboard"
    resp = client.post(
        "/login",
        data={"email": "alex@example.com", "password": "password123", "rd": rd},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == rd


def test_login_falls_back_to_me_for_untrusted_rd(client, auth):
    _create(auth)
    resp = client.post(
        "/login",
        data={
            "email": "alex@example.com",
            "password": "password123",
            "rd": "https://evil.example.net/phish",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/me"


def test_logout_clears_cookie_and_the_old_session_stops_working(client, auth):
    _create(auth)
    login = client.post(
        "/login", data={"email": "alex@example.com", "password": "password123"}
    )
    assert client.get("/me").status_code == 200

    resp = client.post("/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert client.get("/me").status_code == 401
