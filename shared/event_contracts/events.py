from datetime import datetime, timezone
from uuid import uuid4
from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    event_version: int = 1
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str | None = None
    payload: dict


def make_event(event_type: str, payload: dict, correlation_id: str | None = None) -> dict:
    return EventEnvelope(event_type=event_type, payload=payload, correlation_id=correlation_id).model_dump(mode="json")
