"""Pydantic v2 schemas for the Wishlist feature."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


class WishCreate(BaseModel):
    title: str
    author: Optional[str] = None
    series: Optional[str] = None
    series_index: Optional[float] = None
    cover_url: Optional[str] = None
    source: Optional[str] = None  # hardcover | google_books | open_library | manual
    source_id: Optional[str] = None
    isbn: Optional[str] = None
    note: Optional[str] = None
    # Whole-series wishes created from a Hardcover series result carry a canonical
    # series id and the true volume count (primary_books_count).
    external_series_id: Optional[str] = None
    series_total: Optional[int] = None

    @field_validator("cover_url")
    @classmethod
    def _validate_cover_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not v.startswith(("http://", "https://")):
            raise ValueError("cover_url must start with http:// or https://")
        return v

    @field_validator("title")
    @classmethod
    def _validate_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be empty")
        return v


class WishCoverageVolume(BaseModel):
    """A library book that satisfies (part of) a whole-series wish."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    series_index: Optional[float] = None
    cover_path: Optional[str] = None


class WishOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    kind: str
    status: str
    title: str
    author: Optional[str] = None
    series: Optional[str] = None
    series_index: Optional[float] = None
    cover_url: Optional[str] = None
    source: Optional[str] = None
    source_id: Optional[str] = None
    isbn: Optional[str] = None
    note: Optional[str] = None
    fulfilled_book_id: Optional[int] = None
    fulfilled_by: Optional[int] = None
    fulfilled_at: Optional[datetime] = None
    suggested_book_ids: Optional[list[int]] = None
    # For whole-series wishes: the volumes currently in the library (coverage)
    # and the true total volume count (from Hardcover), for an "X of N" view.
    series_coverage: Optional[list[WishCoverageVolume]] = None
    series_total: Optional[int] = None
    external_series_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("suggested_book_ids", mode="before")
    @classmethod
    def _decode_suggested_book_ids(cls, v):
        """Decode the JSON-encoded list[int] stored in the DB."""
        if v is None:
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, ValueError):
                return None
        return None


class WishAdminOut(WishOut):
    """Admin view adds requester username."""
    requester_username: Optional[str] = None


class WishSearchResult(BaseModel):
    """A single candidate returned by the wishlist search proxy."""
    source: str
    source_id: str
    title: str
    author: Optional[str] = None
    cover_url: Optional[str] = None
    series: Optional[str] = None
    series_index: Optional[float] = None
    isbn: Optional[str] = None
    year: Optional[int] = None
    description: Optional[str] = None


class WishSeriesResult(BaseModel):
    """A series entity returned by the Hardcover series search."""
    source: str
    source_id: str          # canonical Hardcover series id
    name: str
    author: Optional[str] = None
    total: Optional[int] = None   # true volume count (primary_books_count)
    slug: Optional[str] = None
    cover_url: Optional[str] = None  # first volume's cover


class FulfillRequest(BaseModel):
    # Optional: whole-series wishes are closed via "mark complete" with no book.
    book_id: Optional[int] = None
