from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sessionkit import AuthService, SqliteAuthStore
from sessionkit.sqlite_store import connect

from identity_system.app import create_app
from identity_system.config import Settings
from identity_system.oidc_clients import OidcClient, hash_client_secret


@pytest.fixture
def store():
    conn = connect(":memory:", check_same_thread=False)
    try:
        yield SqliteAuthStore(conn)
    finally:
        conn.close()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        db_path=":memory:",
        cookie_domain=".example.com",
        cookie_secure=False,  # TestClient talks plain HTTP
        session_days=30,
        issuer="identity-system-test",
        admin_emails=frozenset({"admin@example.com"}),
        apps_path="/nonexistent/apps.json",  # tests pass `apps` to create_app directly
        oidc_issuer_url="http://sso.example.com",
        oidc_clients_path="/nonexistent/oidc_clients.json",  # tests pass `oidc_clients` directly
        oidc_signing_key_path="/nonexistent/oidc_signing_key.pem",  # tests pass a key directly
    )


@pytest.fixture
def auth(store, settings) -> AuthService:
    return AuthService(store, session_days=settings.session_days, issuer=settings.issuer)


@pytest.fixture
def apps() -> list[dict]:
    return [{"name": "Example App", "url": "https://app.example.com", "description": "Test app."}]


@pytest.fixture
def oidc_signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def oidc_client_secret() -> str:
    # Plaintext - tests need this to drive the token endpoint's client
    # auth; only its hash (below) is ever handed to create_app/the registry.
    return "s3cret-for-tests-only"


@pytest.fixture
def oidc_clients(oidc_client_secret) -> dict[str, OidcClient]:
    client = OidcClient(
        client_id="jenkins",
        client_secret_hash=hash_client_secret(oidc_client_secret),
        redirect_uris=frozenset({"https://jenkins.example.com/securityRealm/finishLogin"}),
        allowed_scopes=frozenset({"openid", "email", "profile"}),
    )
    return {client.client_id: client}


@pytest.fixture
def client(auth, settings, apps, oidc_clients, oidc_signing_key) -> TestClient:
    app = create_app(auth, settings, apps, oidc_clients, oidc_signing_key)
    # The session cookie is scoped to settings.cookie_domain (.example.com);
    # TestClient's default host ("testserver") doesn't match that domain, so
    # its cookie jar would silently drop the cookie between requests unless
    # the client's own host is a subdomain of it.
    return TestClient(app, base_url="http://sso.example.com")
