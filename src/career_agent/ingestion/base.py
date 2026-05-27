from __future__ import annotations
from typing import Iterator
from abc import ABC, abstractmethod
from .normalizer import RawJob, NormalizedJob

class BaseScraper(ABC):
    source_name: str

    @abstractmethod
    def fetch_raw(self) -> Iterator[RawJob]:
        """Fetch raw jobs from source and yield RawJob objects."""
        pass

    def scrape(self) -> Iterator[NormalizedJob]:
        """Fetch raw jobs, normalize them, and yield NormalizedJob objects."""
        from .normalizer import normalize_job
        for raw in self.fetch_raw():
            yield normalize_job(raw)
