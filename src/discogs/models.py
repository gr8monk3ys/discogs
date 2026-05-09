"""Typed domain models for Discogs entities."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class Format(BaseModel):
    name: str
    qty: int = 1
    descriptions: list[str] = Field(default_factory=list)


class Release(BaseModel):
    id: int
    master_id: int | None = None
    title: str
    year: int
    country: str | None = None
    formats: list[Format] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    community_have: int
    community_want: int
    community_avg_rating: float
    community_rating_count: int
    fetched_at: datetime

    @field_validator("year")
    @classmethod
    def _year_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"year must be non-negative, got {v}")
        return v

    @property
    def is_album_or_ep(self) -> bool:
        for fmt in self.formats:
            descs = {d.lower() for d in fmt.descriptions}
            name = fmt.name.lower()
            if {"lp", "album", "ep"} & descs:
                return True
            if name in {"album"}:
                return True
            if descs & {"single", "compilation", "dj-mix", "dj mix"}:
                continue
        return bool(self.formats) and not self.is_compilation and not self._is_single()

    @property
    def is_compilation(self) -> bool:
        return any("compilation" in d.lower() for f in self.formats for d in f.descriptions)

    def _is_single(self) -> bool:
        for fmt in self.formats:
            descs = {d.lower() for d in fmt.descriptions}
            if {"single", "7\""} & descs:
                return True
        return False


class Master(BaseModel):
    id: int
    title: str
    year: int
    main_release_id: int | None
    fetched_at: datetime


class Artist(BaseModel):
    id: int
    name: str
    profile: str | None
    fetched_at: datetime


class Label(BaseModel):
    id: int
    name: str
    parent_label: str | None
    releases_count: int
    fetched_at: datetime


class Credit(BaseModel):
    release_id: int
    artist_id: int
    role: str

    @property
    def normalized_role(self) -> str:
        # Discogs roles often have qualifiers in brackets: "Producer [Tracks A1]" -> "Producer"
        return self.role.split("[", 1)[0].strip()


class CollectionItem(BaseModel):
    release_id: int
    folder_id: int
    instance_id: int
    date_added: datetime


class WantlistItem(BaseModel):
    release_id: int
    date_added: datetime
    notes: str | None


class ArtistInfluence(BaseModel):
    source_artist_id: int
    influence_artist_id: int
    confidence: str  # 'high' | 'medium' | 'low'
    source: str = "claude"
    fetched_at: datetime
