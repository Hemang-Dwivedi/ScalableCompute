# tests/test_sync_api.py
import pytest
from fastapi.testclient import TestClient
from App.api import app

client = TestClient(app)


def test_sync_endpoint_accepts_results_and_returns_ok():
    payload = {
        "worker_id": "worker_1",
        "results": [
            {
                "worker_id": "worker_1",
                "digits": 100,
                "pi_head": "3.14159265",
                "computed_at": "2026-04-16T00:00:00",
                "synced": True,
            }
        ],
    }
    r = client.post("/sync", json=payload)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["received"] == 1


def test_sync_endpoint_accepts_empty_results_list():
    r = client.post("/sync", json={"worker_id": "worker_1", "results": []})
    assert r.status_code == 200
    assert r.json()["received"] == 0


def test_sync_endpoint_rejects_missing_worker_id():
    r = client.post("/sync", json={"results": []})
    assert r.status_code == 422


def test_websocket_connection_is_accepted():
    with client.websocket_connect("/ws") as ws:
        assert ws is not None
