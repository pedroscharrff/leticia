from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class SessionRow(BaseModel):
    id: str
    phone: str
    session_key: str
    customer_profile: Optional[str] = "indefinido"
    turn_count: int = 0
    created_at: datetime
    updated_at: datetime
