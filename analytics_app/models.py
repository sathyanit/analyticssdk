import json
import re
from datetime import datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .storage import utcnow

Identity = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[\w.:-]+$")]


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    anonymous_id: Identity
    user_id: Identity | None = None
    session_id: Identity | None = None
    occurred_at: datetime
    path: str | None = Field(default=None, max_length=512)
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def valid_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp must include a timezone")
        now = utcnow()
        if value > now + timedelta(minutes=5) or value < now - timedelta(days=90):
            raise ValueError("Timestamp must be within the last 90 days and at most 5 minutes ahead")
        return value

    @field_validator("path")
    @classmethod
    def safe_path(cls, value):
        if value is not None and (not value.startswith("/") or "?" in value or "#" in value or value.startswith("//")):
            raise ValueError("Use a relative pathname without query parameters or fragments")
        return value

    @field_validator("properties")
    @classmethod
    def bounded_properties(cls, value):
        try:
            encoded = json.dumps(value, allow_nan=False)
        except (ValueError, TypeError):
            raise ValueError("Properties must be finite JSON")
        if len(encoded.encode()) > 8192:
            raise ValueError("Properties exceed 8 KiB")
        # Avoid accidentally accepting common personal/credential fields, including nested objects.
        if re.search(r'"(?:email|password|token|authorization|phone|address)"\s*:', encoded, re.I):
            raise ValueError("Personal data and credentials are not allowed")
        return value


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[Event] = Field(min_length=1, max_length=50)


class OrderItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str
    quantity: int = Field(strict=True, ge=1, le=20)


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anonymous_id: Identity
    session_id: Identity | None = None
    items: list[OrderItem] = Field(min_length=1, max_length=20)
