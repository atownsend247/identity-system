from __future__ import annotations

import base64
import hashlib
import os
from urllib.parse import parse_qs, urlparse

import jwt

REDIRECT_URI = "https://jenkins.example.com/securityRealm/finishLogin"

AUTHORIZE_PARAMS = {
    "client_id": "jenkins",
    "redirect_uri": REDIRECT_URI,
    "response_type": "code",
    "state": "xyz",
}


def _create_user(auth):
    return auth.create_user("alex@example.com", "password123", name="Alex")


def _login(client, auth):
    _create_user(auth)
    client.post("/api/login", json={"email": "alex@example.com", "password": "password123"})


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


def _authorize(client, *, scope="openid", use_pkce=True, **overrides):
    verifier = None
    params = {**AUTHORIZE_PARAMS, "scope": scope, **overrides}
    if use_pkce:
        verifier, challenge = _pkce_pair()
        params.setdefault("code_challenge", challenge)
        params.setdefault("code_challenge_method", "S256")
    resp = client.get("/api/oidc/authorize", params=params, follow_redirects=False)
    return resp, verifier


def _code_from(redirect_resp) -> str:
    query = parse_qs(urlparse(redirect_resp.headers["location"]).query)
    return query["code"][0]


def _token_request(
    client, *, code, client_secret, verifier=None, client_id="jenkins", redirect_uri=REDIRECT_URI
):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if verifier is not None:
        data["code_verifier"] = verifier
    return client.post("/api/oidc/token", data=data)


def test_discovery_document(client, settings):
    resp = client.get("/.well-known/openid-configuration")
    assert resp.status_code == 200
    body = resp.json()
    assert body["issuer"] == settings.oidc_issuer_url
    assert body["authorization_endpoint"] == f"{settings.oidc_issuer_url}/api/oidc/authorize"
    assert body["token_endpoint"] == f"{settings.oidc_issuer_url}/api/oidc/token"
    assert body["jwks_uri"] == f"{settings.oidc_issuer_url}/api/oidc/jwks.json"
    assert body["code_challenge_methods_supported"] == ["S256"]


def test_jwks_exposes_one_public_signing_key(client):
    resp = client.get("/api/oidc/jwks.json")
    assert resp.status_code == 200
    keys = resp.json()["keys"]
    assert len(keys) == 1
    assert keys[0]["kty"] == "RSA"
    assert keys[0]["use"] == "sig"
    assert keys[0]["alg"] == "RS256"
    assert "d" not in keys[0]  # public key only - never the private exponent


def test_authorize_without_session_redirects_to_login_preserving_request(client):
    resp, _ = _authorize(client)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?rd=%2Fapi%2Foidc%2Fauthorize")


def test_authorize_rejects_unregistered_client(client, auth):
    _login(client, auth)
    resp, _ = _authorize(client, client_id="not-registered")
    assert resp.status_code == 400


def test_authorize_rejects_redirect_uri_not_an_exact_registry_match(client, auth):
    _login(client, auth)
    resp, _ = _authorize(client, redirect_uri=REDIRECT_URI + "/extra")
    assert resp.status_code == 400


def test_authorize_rejects_scope_missing_openid(client, auth):
    _login(client, auth)
    resp, _ = _authorize(client, scope="email")
    assert resp.status_code == 302
    query = parse_qs(urlparse(resp.headers["location"]).query)
    assert query["error"] == ["invalid_scope"]


def test_authorize_rejects_scope_not_allowed_for_client(client, auth):
    _login(client, auth)
    resp, _ = _authorize(client, scope="openid admin")
    query = parse_qs(urlparse(resp.headers["location"]).query)
    assert query["error"] == ["invalid_scope"]


def test_authorize_rejects_code_challenge_with_unsupported_method(client, auth):
    _login(client, auth)
    resp, _ = _authorize(client, use_pkce=False, code_challenge="abc", code_challenge_method="plain")
    query = parse_qs(urlparse(resp.headers["location"]).query)
    assert query["error"] == ["invalid_request"]


def test_full_round_trip_without_pkce(client, auth, oidc_client_secret):
    # Confidential clients (every client registered here) authenticate at
    # /api/oidc/token with their client_secret regardless - PKCE is
    # supported but not required, since an RP not sending it (e.g. Jenkins'
    # OIDC plugin without PKCE enabled) must still be able to complete the
    # flow (see oidc.py's module docstring).
    _login(client, auth)
    authorize_resp, verifier = _authorize(client, use_pkce=False)
    assert verifier is None
    code = _code_from(authorize_resp)

    token_resp = _token_request(client, code=code, client_secret=oidc_client_secret)
    assert token_resp.status_code == 200
    assert token_resp.json()["token_type"] == "Bearer"


def test_full_authorization_code_pkce_round_trip(client, auth, oidc_client_secret):
    _login(client, auth)
    authorize_resp, verifier = _authorize(
        client, scope="openid email profile", nonce="n-0s6_wz"
    )
    assert authorize_resp.status_code == 302
    location = authorize_resp.headers["location"]
    assert location.startswith(REDIRECT_URI)
    assert parse_qs(urlparse(location).query)["state"] == ["xyz"]
    code = _code_from(authorize_resp)

    token_resp = _token_request(client, code=code, verifier=verifier, client_secret=oidc_client_secret)
    assert token_resp.status_code == 200
    body = token_resp.json()
    assert body["token_type"] == "Bearer"

    claims = jwt.decode(body["id_token"], options={"verify_signature": False})
    assert claims["sub"]
    assert claims["aud"] == "jenkins"
    assert claims["email"] == "alex@example.com"
    assert claims["name"] == "Alex"
    assert claims["nonce"] == "n-0s6_wz"

    userinfo_resp = client.get(
        "/api/oidc/userinfo", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert userinfo_resp.status_code == 200
    info = userinfo_resp.json()
    assert info["sub"] == claims["sub"]
    assert info["email"] == "alex@example.com"
    assert info["name"] == "Alex"


def test_code_cannot_be_reused(client, auth, oidc_client_secret):
    _login(client, auth)
    authorize_resp, verifier = _authorize(client)
    code = _code_from(authorize_resp)

    first = _token_request(client, code=code, verifier=verifier, client_secret=oidc_client_secret)
    assert first.status_code == 200

    second = _token_request(client, code=code, verifier=verifier, client_secret=oidc_client_secret)
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


def test_token_rejects_expired_code(client, auth, oidc_client_secret, monkeypatch):
    _login(client, auth)
    authorize_resp, verifier = _authorize(client)
    code = _code_from(authorize_resp)

    import identity_system.oidc as oidc_module

    future = oidc_module.time.time() + 3600
    monkeypatch.setattr(oidc_module.time, "time", lambda: future)

    resp = _token_request(client, code=code, verifier=verifier, client_secret=oidc_client_secret)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_token_rejects_wrong_pkce_verifier(client, auth, oidc_client_secret):
    _login(client, auth)
    authorize_resp, _verifier = _authorize(client)
    code = _code_from(authorize_resp)

    resp = _token_request(client, code=code, verifier="wrong-verifier", client_secret=oidc_client_secret)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_token_rejects_wrong_client_secret(client, auth):
    _login(client, auth)
    authorize_resp, verifier = _authorize(client)
    code = _code_from(authorize_resp)

    resp = _token_request(client, code=code, verifier=verifier, client_secret="not-the-secret")
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"


def test_userinfo_rejects_garbage_bearer_token(client):
    resp = client.get("/api/oidc/userinfo", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


def test_userinfo_requires_bearer_scheme(client):
    resp = client.get("/api/oidc/userinfo")
    assert resp.status_code == 401
