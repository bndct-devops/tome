from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

VALID_STATUSES = {"ongoing", "finished", "hiatus", "unknown"}


class ArcBase(BaseModel):
    series_name: str
    name: str
    start_index: float
    end_index: float
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v

    @field_validator("series_name")
    @classmethod
    def series_name_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("series_name must not be empty")
        return v

class ArcCreate(ArcBase):
    pass


class ArcUpdate(BaseModel):
    name: Optional[str] = None
    start_index: Optional[float] = None
    end_index: Optional[float] = None
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_non_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("name must not be empty")
        return v


class ArcOut(BaseModel):
    id: int
    series_name: str
    name: str
    start_index: float
    end_index: float
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SeriesMetaOut(BaseModel):
    series_name: str
    status: str
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SeriesMetaUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: str) -> str:
        if v not in VALID_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")
        return v
