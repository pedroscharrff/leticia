from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import uuid


@dataclass
class TenantRow:
    id: str
    name: str
    api_key: str
    callback_url: str
    plan: str
    schema_name: str
    active: bool
    created_at: datetime


# ── Pydantic schemas for the REST API ────────────────────────────────────────
from pydantic import BaseModel, HttpUrl, Field


class TenantCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    callback_url: str
    plan: str = Field("basic", pattern="^(basic|pro|enterprise)$")


class TenantResponse(BaseModel):
    id: str
    name: str
    callback_url: str
    plan: str
    schema_name: str
    active: bool
    api_key: str
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_row(cls, row) -> "TenantResponse":
        d = dict(row)
        d["id"] = str(d["id"])
        return cls(**d)
