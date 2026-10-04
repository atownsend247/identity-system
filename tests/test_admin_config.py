from __future__ import annotations

from identity_system.oidc_clients import OidcClient, hash_client_secret, verify_client_secret

REDIRECT_URI = "https://new-rp.example.com/callback"


def _login(client, auth, email, password="password123"):
    auth.create_user(email, password, name=email.split("@")[0])
    client.post("/api/login", json={"email": email, "password": password})


def _admin(client, auth):
    _login(client, auth, "admin@example.com")


def _non_admin(client, auth):
    _login(client, auth, "alex@example.com")


ADMIN_ROUTES = [
    ("get", "/api/admin/apps", None),
    ("post", "/api/admin/apps", {"name": "X", "url": "https://x.example.com", "description": ""}),
    ("put", "/api/admin/apps/1", {"name": "X", "url": "https://x.example.com", "description": ""}),
    ("delete", "/api/admin/apps/1", None),
    ("get", "/api/admin/oidc-clients", None),
    ("post", "/api/admin/oidc-clients", {"client_id": "x", "redirect_uris": [REDIRECT_URI]}),
    ("put", "/api/admin/oidc-clients/x", {"redirect_uris": [REDIRECT_URI], "allowed_scopes": ["openid"]}),
    ("post", "/api/admin/oidc-clients/x/rotate-secret", None),
    ("delete", "/api/admin/oidc-clients/x", None),
]


def _call(client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    return getattr(client, method)(path, **kwargs)


def test_every_admin_config_route_requires_login(client):
    for method, path, body in ADMIN_ROUTES:
        assert _call(client, method, path, body).status_code == 401, (method, path)


def test_every_admin_config_route_rejects_non_admins(client, auth):
    _non_admin(client, auth)
    for method, path, body in ADMIN_ROUTES:
        assert _call(client, method, path, body).status_code == 403, (method, path)


def test_me_reports_is_admin(client, auth):
    _admin(client, auth)
    assert client.get("/me").json()["is_admin"] is True


def test_me_reports_not_admin_for_others(client, auth):
    _non_admin(client, auth)
    assert client.get("/me").json()["is_admin"] is False


# ----------------------------------------------------------------- apps


def test_admin_can_add_update_and_delete_apps(client, auth, apps):
    _admin(client, auth)

    created = client.post(
        "/api/admin/apps",
        json={"name": "Wiki", "url": "https://wiki.example.com", "description": "Docs"},
    )
    assert created.status_code == 201
    app_id = created.json()["id"]

    public = client.get("/api/apps").json()
    assert {"name": "Wiki", "url": "https://wiki.example.com", "description": "Docs"} in public
    # Public shape stays exactly name/url/description - no admin-only id leaks.
    assert all(set(entry) == {"name", "url", "description"} for entry in public)

    updated = client.put(
        f"/api/admin/apps/{app_id}",
        json={"name": "Wiki v2", "url": "https://wiki.example.com", "description": "Docs"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Wiki v2"
    assert {"name": "Wiki v2", "url": "https://wiki.example.com", "description": "Docs"} in (
        client.get("/api/apps").json()
    )

    assert client.delete(f"/api/admin/apps/{app_id}").status_code == 204
    assert client.delete(f"/api/admin/apps/{app_id}").status_code == 404
    assert client.put(f"/api/admin/apps/{app_id}", json={"name": "x", "url": "https://x.example.com"}).status_code == 404
    assert all(entry["name"] != "Wiki v2" for entry in client.get("/api/apps").json())


def test_admin_app_listing_includes_ids(client, auth, apps):
    _admin(client, auth)
    listing = client.get("/api/admin/apps").json()
    assert listing[0]["name"] == "Example App"
    assert "id" in listing[0]


def test_app_url_must_be_absolute_http(client, auth):
    _admin(client, auth)
    for bad in ["javascript:alert(1)", "finance.example.com", "https://", "https://a b.example.com"]:
        resp = client.post("/api/admin/apps", json={"name": "X", "url": bad})
        assert resp.status_code == 422, bad


# ------------------------------------------------------------- oidc clients


def _create_client(client, client_id="new-rp", **overrides):
    body = {
        "client_id": client_id,
        "redirect_uris": [REDIRECT_URI],
        "allowed_scopes": ["openid", "email"],
        **overrides,
    }
    return client.post("/api/admin/oidc-clients", json=body)


def test_created_client_returns_secret_once_and_stores_only_a_hash(client, auth, registry, oidc_clients):
    _admin(client, auth)

    resp = _create_client(client)
    assert resp.status_code == 201
    secret = resp.json()["client_secret"]
    assert len(secret) >= 32

    listing = client.get("/api/admin/oidc-clients").json()
    new_rp = next(c for c in listing if c["client_id"] == "new-rp")
    assert new_rp == {
        "client_id": "new-rp",
        "redirect_uris": [REDIRECT_URI],
        "allowed_scopes": ["email", "openid"],
    }
    assert all("client_secret" not in c and "client_secret_hash" not in c for c in listing)

    stored = registry.get_oidc_client("new-rp")
    assert stored.client_secret_hash != secret
    assert verify_client_secret(stored, secret)


def test_duplicate_and_malformed_clients_are_rejected(client, auth, oidc_clients):
    _admin(client, auth)
    assert _create_client(client, client_id="jenkins").status_code == 409
    assert _create_client(client, client_id="has spaces").status_code == 422
    assert _create_client(client, redirect_uris=["not-a-url"]).status_code == 422
    assert _create_client(client, allowed_scopes=["admin"]).status_code == 422


def test_admin_created_client_completes_the_oidc_flow(client, auth):
    _admin(client, auth)
    secret = _create_client(client).json()["client_secret"]

    authorize = client.get(
        "/api/oidc/authorize",
        params={
            "client_id": "new-rp",
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email",
            "state": "xyz",
        },
        follow_redirects=False,
    )
    assert authorize.status_code == 302
    assert "code=" in authorize.headers["location"]
    code = authorize.headers["location"].split("code=")[1].split("&")[0]

    token = client.post(
        "/api/oidc/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": "new-rp",
            "client_secret": secret,
        },
    )
    assert token.status_code == 200
    assert token.json()["token_type"] == "Bearer"


def test_update_changes_redirect_uris_and_scopes_live(client, auth, oidc_clients):
    _admin(client, auth)
    new_uri = "https://jenkins-new.example.com/securityRealm/finishLogin"
    resp = client.put(
        "/api/admin/oidc-clients/jenkins",
        json={"redirect_uris": [new_uri], "allowed_scopes": ["openid"]},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "client_id": "jenkins",
        "redirect_uris": [new_uri],
        "allowed_scopes": ["openid"],
    }

    # The old redirect_uri is no longer registered, so /authorize refuses it
    # outright - no restart needed.
    old = client.get(
        "/api/oidc/authorize",
        params={
            "client_id": "jenkins",
            "redirect_uri": "https://jenkins.example.com/securityRealm/finishLogin",
            "response_type": "code",
            "scope": "openid",
        },
        follow_redirects=False,
    )
    assert old.status_code == 400


def test_rotate_secret_replaces_the_old_secret(client, auth, registry, oidc_clients, oidc_client_secret):
    _admin(client, auth)
    resp = client.post("/api/admin/oidc-clients/jenkins/rotate-secret")
    assert resp.status_code == 200
    new_secret = resp.json()["client_secret"]
    assert new_secret != oidc_client_secret

    stored = registry.get_oidc_client("jenkins")
    assert verify_client_secret(stored, new_secret)
    assert not verify_client_secret(stored, oidc_client_secret)


def test_delete_client_stops_it_authorizing(client, auth, oidc_clients):
    _admin(client, auth)
    assert client.delete("/api/admin/oidc-clients/jenkins").status_code == 204
    assert client.delete("/api/admin/oidc-clients/jenkins").status_code == 404

    resp = client.get(
        "/api/oidc/authorize",
        params={
            "client_id": "jenkins",
            "redirect_uri": "https://jenkins.example.com/securityRealm/finishLogin",
            "response_type": "code",
            "scope": "openid",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_legacy_import_seeds_the_registry_once(registry, apps, oidc_clients):
    # The `apps` and `oidc_clients` fixtures seed through the public add_*
    # methods; the one-shot import is what main.py uses on a real first start.
    fresh_apps = [{"name": "Imported", "url": "https://imported.example.com", "description": "d"}]
    assert registry.legacy_import_done() is False

    registry.import_legacy(fresh_apps, [])

    assert registry.legacy_import_done() is True
    assert {a["name"] for a in registry.list_apps()} >= {"Example App", "Imported"}
