from __future__ import annotations
import re
import httpx
from typing import Iterator
from loguru import logger
from .base import BaseScraper
from .normalizer import RawJob

class GreenhouseScraper(BaseScraper):
    source_name = "greenhouse"
    
    # Known AI and developer tech companies using Greenhouse
    board_tokens = [
        "anthropic", "cohere", "scaleai", "replicate", 
        "huggingface", "perplexity", "characterai", "eleutherai",
        "openai", "mistralai", "stabilityai", "langchain",
        "pinecone", "weaviate", "togetherai", "groq",
        "midjourney", "assemblyai", "elevenlabs", "lumaai",
        "vapi", "relevanceai", "runpod"
    ]
    
    def fetch_raw(self) -> Iterator[RawJob]:
        logger.info("[greenhouse] Fetching jobs from target boards...")
        
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for token in self.board_tokens:
                url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
                try:
                    r = client.get(url)
                    if r.status_code == 404:
                        logger.warning(f"[greenhouse] Board token '{token}' returned 404.")
                        continue
                    r.raise_for_status()
                    data = r.json()
                    
                    jobs = data.get("jobs", [])
                    logger.info(f"[greenhouse] Received {len(jobs)} jobs for '{token}'")
                    
                    for item in jobs:
                        source_id = f"{token}_{item.get('id')}"
                        title = item.get("title", "")
                        location = item.get("location", {}).get("name", "")
                        absolute_url = item.get("absolute_url", "")
                        html_content = item.get("content", "")
                        posted_at = item.get("updated_at")
                        
                        # Strip HTML tags to extract raw description text
                        clean_desc = re.sub(r'<[^>]*>', ' ', html_content).strip()
                        clean_desc = re.sub(r'\s+', ' ', clean_desc)
                        
                        is_remote = "remote" in location.lower() or "remote" in title.lower()
                        
                        yield RawJob(
                            source=self.source_name,
                            source_id=source_id,
                            title=title,
                            company=token.capitalize(),
                            location=location,
                            url=absolute_url,
                            description=clean_desc,
                            tags=["greenhouse", token],
                            is_remote=is_remote,
                            posted_at=posted_at
                        )
                except Exception as e:
                    logger.error(f"[greenhouse] Failed to fetch board '{token}': {e}")
