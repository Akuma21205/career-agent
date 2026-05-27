from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass
class RawJob:
    source: str
    source_id: str
    title: str
    company: str
    location: str
    url: str
    description: str
    tags: list[str]
    is_remote: bool
    posted_at: str | None

@dataclass
class NormalizedJob:
    id: str
    source: str
    source_id: str
    title: str
    company: str
    location: str
    url: str
    description: str
    tags: str  # JSON array
    is_remote: int
    posted_at: str | None
    fetched_at: str
    is_active: int = 1

def generate_job_id(source: str, source_id: str) -> str:
    """Generate SHA-1 hash of source and source_id."""
    hasher = hashlib.sha1()
    hasher.update(f"{source}:{source_id}".encode("utf-8"))
    return hasher.hexdigest()

def normalize_job(raw: RawJob) -> NormalizedJob:
    """Normalize a RawJob into a NormalizedJob."""
    job_id = generate_job_id(raw.source, raw.source_id)
    return NormalizedJob(
        id=job_id,
        source=raw.source,
        source_id=raw.source_id,
        title=raw.title.strip() if raw.title else "",
        company=raw.company.strip() if raw.company else "",
        location=raw.location.strip() if raw.location else "",
        url=raw.url.strip() if raw.url else "",
        description=raw.description.strip() if raw.description else "",
        tags=json.dumps(raw.tags),
        is_remote=1 if raw.is_remote else 0,
        posted_at=raw.posted_at,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        is_active=1,
    )
