from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import field_validator
from urllib.parse import urlsplit


class SourceCreate(BaseModel):
    type: Literal["rss", "podcast", "youtube"] = "rss"
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=4)
    tags: list[str] = Field(default_factory=list)
    priority: int = Field(default=3, ge=1, le=5)
    poll_interval: int = Field(default=60, ge=5)
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url must be an HTTP(S) address")
        if parsed.username or parsed.password:
            raise ValueError("embedded URL credentials are not allowed")
        return value.strip()


class SourceUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    tags: list[str] | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    poll_interval: int | None = Field(default=None, ge=5)
    enabled: bool | None = None


class SourceRead(SourceCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    last_success_at: datetime | None
    consecutive_failures: int


class ItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_id: int
    canonical_url: str
    content_type: str
    title: str
    author: str | None
    published_at: datetime
    clean_text: str | None
    status: str


class BriefRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    brief_date: date
    status: str
    content_json: dict[str, Any]
    content_markdown: str
    model: str


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    job_type: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    input_count: int
    output_count: int
    error: str | None
