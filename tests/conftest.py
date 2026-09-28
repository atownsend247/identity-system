from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sessionkit import AuthService, SqliteAuthStore
from sessionkit.sqlite_store import connect

from identity_system.app import create_app
from identity_system.config import Settings


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
    )


@pytest.fixture
def auth(store, settings) -> AuthService:
    return AuthService(store, session_days=settings.session_days, issuer=settings.issuer)


@pytest.fixture
def client(auth, settings) -> TestClient:
    app = create_app(auth, settings)
    # The session cookie is scoped to settings.cookie_domain (.example.com);
    # TestClient's default host ("testserver") doesn't match that domain, so
    # its cookie jar would silently drop the cookie between requests unless
    # the client's own host is a subdomain of it.
    return TestClient(app, base_url="http://sso.example.com")
