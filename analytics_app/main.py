import hashlib
import json
import os
import secrets
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, time as daytime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .models import Batch, Order
from .server_sdk import business_event
from .storage import Store, stamp, utcnow

STATIC = Path(__file__).parent / "static"
MENU = [
    {"id": "bowl", "name": "Harvest bowl", "description": "Roasted squash, ancient grains, lemon tahini", "price_cents": 1450, "icon": "🥗", "category": "Bowls"},
    {"id": "pizza", "name": "Burrata & basil", "description": "Wood-fired sourdough, tomato, fresh burrata", "price_cents": 1800, "icon": "🍕", "category": "From the oven"},
    {"id": "pasta", "name": "Wild mushroom pasta", "description": "Rigatoni, woodland mushrooms, parmesan", "price_cents": 1650, "icon": "🍝", "category": "From the kitchen"},
    {"id": "lemonade", "name": "Garden lemonade", "description": "Fresh lemon, rosemary, a little sparkle", "price_cents": 550, "icon": "🍋", "category": "Something to sip"},
]


def create_app(db_path=None, *, dashboard_token=None, server_key=None, allowed_origins=None):
    app = FastAPI(title="Gather Analytics", version="1.0.0", redoc_url=None)
    store = Store(db_path or os.getenv("DATABASE_PATH", "analytics.db"))
    app.state.store = store
    admin_key = dashboard_token if dashboard_token is not None else os.getenv("DASHBOARD_TOKEN", "")
    business_key = server_key if server_key is not None else os.getenv("ANALYTICS_SERVER_KEY", "")
    origins = allowed_origins if allowed_origins is not None else os.getenv("ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",")
    buckets = OrderedDict()
    lock = threading.Lock()

    @app.middleware("http")
    async def safeguards(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in origins:
            return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
        if request.method == "OPTIONS":
            response = JSONResponse({})
        else:
            if request.url.path.startswith("/api/"):
                now = time.monotonic()
                # Do not trust client-controlled X-Forwarded-For headers.
                bucket_key = (request.client.host if request.client else "unknown", request.url.path.startswith("/api/v1/analytics"))
                with lock:
                    entry = buckets.pop(bucket_key, (now, 0))
                    start, count = entry if now - entry[0] < 60 else (now, 0)
                    buckets[bucket_key] = (start, count + 1)
                    if len(buckets) > 10000:
                        buckets.popitem(last=False)
                if count >= 120:
                    return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"})
            if request.method in ("POST", "PUT", "PATCH"):
                if request.headers.get("content-type", "").split(";")[0] != "application/json":
                    return JSONResponse({"detail": "Use application/json"}, status_code=415)
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 262144:
                        return JSONResponse({"detail": "Body exceeds 256 KiB"}, status_code=413)
                request._body = bytes(body)
            response = await call_next(request)
        if origin in origins:
            response.headers.update({"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                                     "Access-Control-Allow-Headers": "Content-Type, Authorization, Idempotency-Key",
                                     "Access-Control-Allow-Methods": "GET, POST, OPTIONS"})
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        if request.url.path == "/docs":
            # Swagger's standard renderer uses an inline initializer and CDN assets.
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def dashboard_auth(request: Request, authorization: str | None = Header(default=None)):
        if admin_key:
            if not secrets.compare_digest(authorization or "", f"Bearer {admin_key}"):
                raise HTTPException(401, "Dashboard token required")
        elif not request.client or request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise HTTPException(403, "Set DASHBOARD_TOKEN for remote access")

    def server_auth(authorization: str | None = Header(default=None)):
        if not business_key or not secrets.compare_digest(authorization or "", f"Bearer {business_key}"):
            raise HTTPException(401, "Valid server key required")

    @app.get("/health")
    def health():
        with store.connect() as db:
            db.execute("SELECT 1")
        return {"status": "ok"}

    def ingest(batch, source):
        if source == "browser" and any(e.name == "order_created" for e in batch.events):
            raise HTTPException(422, "order_created is a server event")
        with store.connect() as db:
            accepted = sum(store.insert(db, e, source) for e in batch.events)
        return {"accepted": accepted, "duplicates": len(batch.events) - accepted}

    @app.post("/api/v1/events")
    def browser_events(batch: Batch):
        return ingest(batch, "browser")

    @app.post("/api/v1/server/events", dependencies=[Depends(server_auth)])
    def server_events(batch: Batch):
        return ingest(batch, "server")

    @app.get("/api/v1/menu")
    def menu():
        return MENU

    @app.post("/api/v1/orders", status_code=201)
    def order(payload: Order, idempotency_key: str = Header(min_length=1, max_length=128)):
        if len({i.item_id for i in payload.items}) != len(payload.items):
            raise HTTPException(422, "Merge duplicate items")
        catalog = {item["id"]: item for item in MENU}
        if any(item.item_id not in catalog for item in payload.items):
            raise HTTPException(422, "Unknown menu item")
        digest = hashlib.sha256(json.dumps(payload.model_dump(), sort_keys=True).encode()).hexdigest()
        with store.connect() as db:
            # Serialize check-and-create so simultaneous retries cannot create two orders.
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM orders WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if previous:
                if previous["request_hash"] != digest:
                    raise HTTPException(409, "Idempotency key reused with different input")
                return {"order_id": previous["order_id"], "total_cents": previous["total_cents"], "replayed": True}
            total = sum(catalog[i.item_id]["price_cents"] * i.quantity for i in payload.items)
            order_id = str(uuid4())
            db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?)", (order_id, idempotency_key, digest,
                       payload.anonymous_id, total, json.dumps([i.model_dump() for i in payload.items]), stamp(utcnow())))
            event = business_event("order_created", payload.anonymous_id, session_id=payload.session_id,
                                   properties={"order_id": order_id, "total_cents": total, "currency": "USD",
                                               "item_count": sum(i.quantity for i in payload.items)})
            store.insert(db, event, "server")
        return {"order_id": order_id, "total_cents": total, "replayed": False}

    @app.get("/api/v1/analytics", dependencies=[Depends(dashboard_auth)])
    def analytics(start: date | None = None, end: date | None = None, event_name: str | None = Query(default=None, max_length=64)):
        end = end or utcnow().date()
        start = start or end - timedelta(days=6)
        if start > end or (end - start).days > 89 or end == date.max:
            raise HTTPException(422, "Choose an inclusive date range of 1–90 days")
        lower = stamp(datetime.combine(start, daytime.min, timezone.utc))
        upper = stamp(datetime.combine(end + timedelta(days=1), daytime.min, timezone.utc))
        where = "occurred_at >= ? AND occurred_at < ?"
        args = [lower, upper]
        if event_name:
            where += " AND name = ?"
            args.append(event_name)
        with store.connect() as db:
            summary = dict(db.execute(f"""SELECT COUNT(*) AS events,
                COUNT(DISTINCT anonymous_id) AS visitors,
                COALESCE(SUM(name='page_view'),0) AS page_views,
                COALESCE(SUM(name='order_created' AND source='server'),0) AS orders
                FROM events WHERE {where}""", args).fetchone())
            daily = {row["day"]: dict(row) for row in db.execute(f"""SELECT substr(occurred_at,1,10) AS day,
                COUNT(*) AS events, COUNT(DISTINCT anonymous_id) AS visitors,
                SUM(name='page_view') AS page_views,
                SUM(name='order_created' AND source='server') AS orders
                FROM events WHERE {where} GROUP BY day ORDER BY day""", args)}
            pages = [dict(row) for row in db.execute(f"""SELECT path, COUNT(*) AS views,
                COUNT(DISTINCT anonymous_id) AS visitors FROM events
                WHERE {where} AND name='page_view' GROUP BY path ORDER BY views DESC, path LIMIT 20""", args)]
            names = [dict(row) for row in db.execute(f"SELECT name, source, COUNT(*) AS count FROM events WHERE {where} GROUP BY name,source ORDER BY count DESC,name", args)]
            recent = [dict(row) for row in db.execute(f"""SELECT event_id,name,source,path,occurred_at,received_at,
                properties FROM events WHERE {where} ORDER BY occurred_at DESC, event_id LIMIT 30""", args)]
        for row in recent:
            row["properties"] = json.loads(row["properties"])
        days = [(start + timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
        return {"start": start, "end": end, "timezone": "UTC", "summary": summary,
                "daily": [daily.get(day, {"day": day, "events": 0, "visitors": 0, "page_views": 0, "orders": 0}) for day in days],
                "pages": pages, "event_types": names, "recent": recent}

    @app.get("/", include_in_schema=False)
    @app.get("/dashboard", include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC / "index.html")

    @app.get("/demo", include_in_schema=False)
    def demo():
        return FileResponse(STATIC / "demo.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
