from __future__ import annotations

import json

from identity_system.apps import load_apps


def test_apps_is_public_and_returns_the_configured_list(client):
    resp = client.get("/api/apps")
    assert resp.status_code == 200
    assert resp.json() == [
        {"name": "Example App", "url": "https://app.example.com", "description": "Test app."}
    ]


def test_load_apps_missing_file_returns_empty_list(tmp_path):
    assert load_apps(str(tmp_path / "nonexistent.json")) == []


def test_load_apps_reads_json(tmp_path):
    path = tmp_path / "apps.json"
    path.write_text(json.dumps([{"name": "A", "url": "https://a.example.com", "description": "d"}]))
    assert load_apps(str(path)) == [
        {"name": "A", "url": "https://a.example.com", "description": "d"}
    ]
