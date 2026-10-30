from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Source = Literal["wearable", "site", "nhi", "env", "merchant"]


class ConsentScope(BaseModel):
    """SPEC §4.1 範例：
    {"read": ["steps", "bp", "visits"], "share_summary_to": [...], "allow_proxy_redeem": true}
    """

    model_config = ConfigDict(extra="forbid")

    read: list[str] = Field(min_length=1)
    share_summary_to: list[uuid.UUID] = Field(default_factory=list)
    allow_proxy_redeem: bool = False


class ConsentCreate(BaseModel):
    source: Source
    scope: ConsentScope


class ConsentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    scope: dict[str, Any]
    version: int
    granted_at: datetime
    revoked_at: datetime | None


class ExplainOut(BaseModel):
    source: str
    name: str
    data: str
    purpose: str
    items: list[str]
    who_sees: list[str]
    impacts_if_revoked: list[str]


class RevokeOut(BaseModel):
    status: Literal["confirmation_required", "revoked"]
    message: str
    action_id: uuid.UUID | None = None
    consent: ConsentOut | None = None
    deletion_job_id: uuid.UUID | None = None
    deletion_due_at: datetime | None = None
