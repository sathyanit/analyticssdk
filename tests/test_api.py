from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from analytics_app.main import create_app
from analytics_app.server_sdk import AnalyticsClient, business_event


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "test.db", dashboard_token="admin", server_key="secret")) as client:
        yield client


ADMIN = {"Authorization": "Bearer admin"}


def event(**overrides):
    return {"event_id": str(uuid4()), "name": "page_view", "anonymous_id": "visitor-a",
            "session_id": "session-a", "path": "/demo", "occurred_at": datetime.now(timezone.utc).isoformat(),
            "properties": {}, **overrides}


def report(client, **params):
    response = client.get("/api/v1/analytics", headers=ADMIN, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_ingest_retry_and_metrics(client):
    first = event()
    second = event(name="cart_added")
    assert client.post("/api/v1/events", json={"events": [first, first, second]}).json() == {"accepted": 2, "duplicates": 1}
    assert client.post("/api/v1/events", json={"events": [first]}).json() == {"accepted": 0, "duplicates": 1}
    result = report(client)
    assert result["summary"] == {"events": 2, "visitors": 1, "page_views": 1, "orders": 0}
    assert result["pages"] == [{"path": "/demo", "views": 1, "visitors": 1}]
    assert len(result["daily"]) == 7
    assert sum(d["events"] for d in result["daily"]) == 2


def test_atomic_order_and_replay(client):
    payload = {"anonymous_id": "visitor-a", "session_id": "session-a", "items": [{"item_id": "bowl", "quantity": 2}]}
    client.post("/api/v1/events", json={"events": [event()]})
    responses = [client.post("/api/v1/orders", json=payload, headers={"Idempotency-Key": "same-order"}) for _ in range(2)]
    assert all(r.status_code == 201 for r in responses)
    first, replay = [r.json() for r in responses]
    assert first["order_id"] == replay["order_id"]
    assert first["total_cents"] == 2900
    assert replay["replayed"] is True
    payload["items"][0]["quantity"] = 3
    assert client.post("/api/v1/orders", json=payload, headers={"Idempotency-Key": "same-order"}).status_code == 409
    result = report(client)
    assert result["summary"] == {"events": 2, "visitors": 1, "page_views": 1, "orders": 1}
    order_event = next(e for e in result["recent"] if e["name"] == "order_created")
    assert order_event["source"] == "server"
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM orders").fetchone()[0] == 1


def test_concurrent_order_retries(client):
    def submit(_):
        return client.post("/api/v1/orders", json={"anonymous_id": "v", "items": [{"item_id": "pizza", "quantity": 1}]}, headers={"Idempotency-Key": "concurrent"})
    with ThreadPoolExecutor(max_workers=5) as executor:
        responses = list(executor.map(submit, range(5)))
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["order_id"] for r in responses}) == 1
    assert report(client)["summary"]["orders"] == 1


def test_utc_boundaries_and_filter(client):
    day = (datetime.now(timezone.utc) - timedelta(days=2)).date()
    next_day = day + timedelta(days=1)
    events = [event(occurred_at=f"{day}T00:00:00Z"),
              event(occurred_at=f"{day}T23:59:59.999Z", anonymous_id="visitor-b"),
              event(occurred_at=f"{next_day}T00:00:00Z"),
              event(occurred_at=f"{next_day}T01:00:00+02:00", name="cart_added")]
    assert client.post("/api/v1/events", json={"events": events}).status_code == 200
    result = report(client, start=str(day), end=str(day))
    assert result["summary"] == {"events": 3, "visitors": 2, "page_views": 2, "orders": 0}
    assert report(client, start=str(day), end=str(day), event_name="cart_added")["summary"]["events"] == 1
    assert report(client, start="2025-01-01", end="2025-01-02")["summary"]["events"] == 0
    assert client.get("/api/v1/analytics?start=2025-01-01&end=2025-04-01", headers=ADMIN).status_code == 422
    assert client.get(f"/api/v1/analytics?start={next_day}&end={day}", headers=ADMIN).status_code == 422


@pytest.mark.parametrize("changes", [
    {"name": "order_created"}, {"name": "bad event"}, {"anonymous_id": ""},
    {"occurred_at": "2020-01-01T00:00:00Z"}, {"occurred_at": "2026-01-01T12:00:00"},
    {"path": "/demo?email=private"}, {"properties": {"nested": {"password": "no"}}},
    {"properties": {"large": "x" * 8193}}, {"source": "server"},
])
def test_bad_events_are_rejected_atomically(client, changes):
    response = client.post("/api/v1/events", json={"events": [event(), event(**changes)]})
    assert response.status_code == 422
    assert report(client)["summary"]["events"] == 0


def test_permissions_and_body_limits(client):
    assert client.get("/api/v1/analytics").status_code == 401
    assert client.post("/api/v1/server/events", json={"events": [event()]}).status_code == 401
    payload = {"events": [event(name="order_created")]}
    assert client.post("/api/v1/server/events", json=payload, headers={"Authorization": "Bearer secret"}).json()["accepted"] == 1
    assert client.post("/api/v1/events", json={"events": []}).status_code == 422
    assert client.post("/api/v1/events", json={"events": [event() for _ in range(51)]}).status_code == 422
    assert client.post("/api/v1/events", content="x" * 262145, headers={"Content-Type": "application/json"}).status_code == 413
    assert client.post("/api/v1/events", content="hello").status_code == 415
    assert client.post("/api/v1/events", json={"events": [event()]}, headers={"Origin": "https://evil.test"}).status_code == 403
    response = client.options("/api/v1/events", headers={"Origin": "http://localhost:8000"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:8000"


def test_invalid_orders_do_not_create_business_events(client):
    for items in [[{"item_id": "unknown", "quantity": 1}], [{"item_id": "bowl", "quantity": 0}],
                  [{"item_id": "bowl", "quantity": 1}, {"item_id": "bowl", "quantity": 1}]]:
        assert client.post("/api/v1/orders", json={"anonymous_id": "v", "items": items}, headers={"Idempotency-Key": str(uuid4())}).status_code == 422
    assert report(client)["summary"]["orders"] == 0


def test_order_and_event_roll_back_together(client, monkeypatch):
    def fail(*args):
        raise RuntimeError("storage failure")
    monkeypatch.setattr(client.app.state.store, "insert", fail)
    with pytest.raises(RuntimeError, match="storage failure"):
        client.post("/api/v1/orders", json={"anonymous_id": "v", "items": [{"item_id": "bowl", "quantity": 1}]}, headers={"Idempotency-Key": "rollback"})
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM orders").fetchone()[0] == 0


def test_rate_limit(client):
    for _ in range(120):
        assert client.get("/api/v1/menu").status_code == 200
    assert client.get("/api/v1/menu").status_code == 429


def test_persistence_and_static_pages(tmp_path):
    path = tmp_path / "persist.db"
    with TestClient(create_app(path)) as first:
        first.post("/api/v1/events", json={"events": [event()]})
    with TestClient(create_app(path)) as second:
        assert second.get("/api/v1/analytics").json()["summary"]["events"] == 1
        for url in ["/", "/dashboard", "/demo", "/static/sdk.js", "/health"]:
            assert second.get(url).status_code == 200


def test_unconfigured_remote_reporting_is_denied(tmp_path):
    app = create_app(tmp_path / "remote.db", dashboard_token="", server_key="")
    with TestClient(app, client=("198.51.100.10", 5000)) as remote:
        assert remote.get("/api/v1/analytics").status_code == 403
        assert remote.post("/api/v1/server/events", json={"events": [event()]}).status_code == 401


def test_maximum_date_is_validation_error(client):
    assert client.get("/api/v1/analytics?start=9999-12-31&end=9999-12-31", headers=ADMIN).status_code == 422


def test_server_client_reuses_event_on_retry(monkeypatch):
    seen = []
    def post(url, **kwargs):
        seen.append(kwargs["json"])
        return httpx.Response(503 if len(seen) == 1 else 200, json={"accepted": 1}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr("analytics_app.server_sdk.time.sleep", lambda _: None)
    assert AnalyticsClient("http://example.test", "secret").send(business_event("signup_completed", "v")) == {"accepted": 1}
    assert len(seen) == 2 and seen[0] == seen[1]
