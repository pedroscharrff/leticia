import json
from pydantic import BaseModel, field_validator
from typing import Optional, Any


class SkillConfig(BaseModel):
    skill_name: str
    ativo: bool = False
    llm_model: Optional[str] = None
    llm_provider: Optional[str] = None
    prompt_version: str = "v1"
    config_json: dict = {}

    @field_validator("config_json", mode="before")
    @classmethod
    def parse_config_json(cls, v: Any) -> dict:
        if isinstance(v, str):
            return json.loads(v) if v else {}
        return v or {}


class SkillUpdate(BaseModel):
    ativo: Optional[bool] = None
    llm_model: Optional[str] = None
    llm_provider: Optional[str] = None
    prompt_version: Optional[str] = None
    config_json: Optional[dict] = None
