"""Small reusable server client. The caller chooses durable retry scheduling."""
import time
from uuid import uuid4

import httpx

from .models import Event
from .storage import utcnow


def business_event(name, anonymous_id, *, properties=None, session_id=None, user_id=None, event_id=None):
    return Event(event_id=event_id or uuid4(), name=name, anonymous_id=anonymous_id,
                 session_id=session_id, user_id=user_id, occurred_at=utcnow(), properties=properties or {})


class AnalyticsClient:
    def __init__(self, endpoint, server_key, *, timeout=3, attempts=3):
        self.endpoint = endpoint.rstrip("/") + "/api/v1/server/events"
        self.server_key = server_key
        self.timeout = timeout
        if not 1 <= attempts <= 5:
            raise ValueError("attempts must be between 1 and 5")
        self.attempts = attempts

    def send(self, event: Event):
        # Event object (including ID and timestamp) is reused across all attempts.
        for attempt in range(self.attempts):
            try:
                response = httpx.post(self.endpoint, headers={"Authorization": f"Bearer {self.server_key}"},
                                      json={"events": [event.model_dump(mode="json")]}, timeout=self.timeout)
                if response.status_code != 429 and response.status_code < 500:
                    response.raise_for_status()
                    return response.json()
                response.raise_for_status()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code == 429 or exc.response.status_code >= 500
                if not retryable or attempt == self.attempts - 1:
                    raise
                time.sleep(min(0.25 * 2 ** attempt, 2))
