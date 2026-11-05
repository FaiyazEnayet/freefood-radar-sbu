"""Core data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Event:
    """One campus event candidate, from any scraper."""

    title: str
    description: str = ""
    club: str = ""
    location: str = ""
    starts_at: datetime | None = None
    url: str = ""
    source: str = ""  # e.g. "corq", "instagram", "discord", "seed"
    # Filled in by the classifier / digest stages:
    food_probability: float = 0.0
    food_keywords: list[str] = field(default_factory=list)
    score: float = 0.0
    # Filled in by the evidence layer during digest scoring:
    offer_likelihood: float = 0.0
    evidence_categories: list[str] = field(default_factory=list)
    access_caveats: list[str] = field(default_factory=list)
    eligibility: str = "unspecified"  # "general" | "restricted" | "unspecified"
    evidence_status: str = "unscored"  # "scored" | "uncertain" | "insufficient_evidence" | "unscored"

    def text(self) -> str:
        return f"{self.title}\n{self.description}".strip()

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["starts_at"] = self.starts_at.isoformat() if self.starts_at else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        d = dict(d)
        if d.get("starts_at"):
            d["starts_at"] = datetime.fromisoformat(d["starts_at"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
