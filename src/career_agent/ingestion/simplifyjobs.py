"""
SimplifyJobs scraper — pulls Summer 2026 internship listings
directly from the public GitHub JSON file via HTTP.
No auth, no scraping, completely free.
"""
from __future__ import annotations
import json
from typing import Iterator
from datetime import datetime, timezone

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseScraper
from .normalizer import RawJob

LISTINGS_URL = (
    "https://raw.githubusercontent.com/"
    "SimplifyJobs/Summer2026-Internships/dev/"
    ".github/scripts/listings.json"
)

# Fields we pull from each listing dict
_TITLE_KEYS   = ("title",)
_COMPANY_KEYS = ("company_name", "company")
_URL_KEYS     = ("url", "application_url")
_DATE_KEYS    = ("date_updated", "date_posted")


def _get(d: dict, *keys: str, default="") -> str:
    for k in keys:
        if k in d and d[k]:
            return str(d[k])
    return default


def _parse_ts(raw) -> str | None:
    """Convert epoch int or ISO string to ISO-8601 UTC string."""
    if not raw:
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=timezone.utc).isoformat()
        return str(raw)
    except Exception:
        return None


class SimplifyJobsScraper(BaseScraper):
    source_name = "simplifyjobs"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _download(self) -> list[dict]:
        logger.info(f"Fetching {LISTINGS_URL}")
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            r = client.get(LISTINGS_URL)
            r.raise_for_status()
            return r.json()

    def fetch_raw(self) -> Iterator[RawJob]:
        listings = self._download()
        logger.info(f"[simplifyjobs] {len(listings)} total listings received")

        active_count = 0
        for item in listings:
            # Skip inactive / non-visible listings
            if not item.get("active", True):
                continue
            if not item.get("is_visible", True):
                continue

            # Flatten locations list → comma-separated string
            locs = item.get("locations", []) or []
            location = ", ".join(locs) if locs else ""
            is_remote = any("remote" in l.lower() for l in locs)

            # Tags from terms (e.g. "Summer 2026") + any extra labels
            tags = list(item.get("terms", [])) + list(item.get("labels", []))

            source_id = str(
                item.get("id") or
                item.get("company_name", "") + "_" + item.get("title", "")
            )

            yield RawJob(
                source=self.source_name,
                source_id=source_id,
                title=_get(item, *_TITLE_KEYS),
                company=_get(item, *_COMPANY_KEYS),
                location=location,
                url=_get(item, *_URL_KEYS),
                description=item.get("description") or "",
                tags=tags,
                is_remote=is_remote,
                posted_at=_parse_ts(item.get("date_updated") or item.get("date_posted")),
            )
            active_count += 1

        logger.info(f"[simplifyjobs] {active_count} active listings yielded")
