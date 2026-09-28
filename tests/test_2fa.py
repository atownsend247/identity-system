from __future__ import annotations

import pyotp


def _login(client, auth, email="alex@example.com", password="password123"):
    auth.create_user(email, password, name="Alex")
    client.post("/api/login", json={"email": email, "password": password})


def test_setup_confirm_then_login_requires_otp(client, auth):
    _login(client, auth)

    setup = client.post("/2fa/setup")
    assert setup.status_code == 200
    secret = setup.json()["secret"]

    code = pyotp.TOTP(secret).now()
    confirm = client.post("/2fa/confirm", json={"otp": code})
    assert confirm.status_code == 200
    recovery_codes = confirm.json()["recovery_codes"]
    assert len(recovery_codes) == 10

    client.post("/logout")
    # now a bare password isn't enough
    resp = client.post(
        "/api/login",
        json={"email": "alex@example.com", "password": "password123"},
    )
    assert resp.status_code == 401
    assert "6-digit code" in resp.json()["detail"]

    # a fresh code works
    good = client.post(
        "/api/login",
        json={
            "email": "alex@example.com",
            "password": "password123",
            "otp": pyotp.TOTP(secret).now(),
        },
    )
    assert good.status_code == 200


def test_confirm_with_bad_code_is_422_json(client, auth):
    _login(client, auth)
    client.post("/2fa/setup")

    resp = client.post("/2fa/confirm", json={"otp": "000000"})
    assert resp.status_code == 422
    assert "detail" in resp.json()


def test_disable_requires_current_password(client, auth):
    _login(client, auth)
    setup = client.post("/2fa/setup").json()
    client.post("/2fa/confirm", json={"otp": pyotp.TOTP(setup["secret"]).now()})

    bad = client.post("/2fa/disable", json={"current_password": "wrong"})
    assert bad.status_code == 401

    ok = client.post("/2fa/disable", json={"current_password": "password123"})
    assert ok.status_code == 204


def test_recovery_codes_regenerate(client, auth):
    _login(client, auth)
    setup = client.post("/2fa/setup").json()
    client.post("/2fa/confirm", json={"otp": pyotp.TOTP(setup["secret"]).now()})

    resp = client.post(
        "/2fa/recovery-codes/regenerate", json={"current_password": "password123"}
    )
    assert resp.status_code == 200
    assert len(resp.json()["recovery_codes"]) == 10
