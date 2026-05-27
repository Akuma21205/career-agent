from __future__ import annotations
import re
from typing import Iterator
from loguru import logger
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from .base import BaseScraper
from .normalizer import RawJob

class WellfoundScraper(BaseScraper):
    source_name = "wellfound"
    
    def fetch_raw(self) -> Iterator[RawJob]:
        logger.info("[wellfound] Fetching AI jobs from Wellfound...")
        url = "https://wellfound.com/role/l/artificial-intelligence-engineer"
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_extra_http_headers({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9"
                })
                page.goto(url, wait_until="networkidle")
                page.wait_for_timeout(4000)
                content = page.content()
                browser.close()
                
            soup = BeautifulSoup(content, "html.parser")
            
            if "checking your browser" in content.lower() or "cloudflare" in content.lower():
                logger.warning("[wellfound] Encountered Cloudflare browser verification check. Skipping direct crawl.")
                return
                
            cards = soup.select("div[class*='jobListing'], div[class*='StartupSearchResult'], div.styles_result__")
            if not cards:
                cards = soup.select(".styles_component__B2wU-, .styles_component__")
                
            logger.info(f"[wellfound] Found {len(cards)} startup job cards.")
            
            for el in cards:
                try:
                    title_el = el.select_one("a[class*='jobTitle'], a.styles_title__")
                    if not title_el:
                        continue
                    title = title_el.text.strip()
                    job_url = "https://wellfound.com" + title_el["href"]
                    source_id = title_el["href"].split("/")[-1]
                    
                    company_el = el.select_one("h2[class*='startupName'], .styles_startupName__")
                    company = company_el.text.strip() if company_el else "Wellfound Startup"
                    
                    location_el = el.select_one("span[class*='location'], .styles_location__")
                    location = location_el.text.strip() if location_el else "Remote / US"
                    
                    is_remote = "remote" in location.lower() or "remote" in title.lower()
                    
                    full_text = el.get_text(separator=" ").strip()
                    
                    yield RawJob(
                        source=self.source_name,
                        source_id=source_id,
                        title=title,
                        company=company,
                        location=location,
                        url=job_url,
                        description=full_text,
                        tags=["wellfound", "startup"],
                        is_remote=is_remote,
                        posted_at=None
                    )
                except Exception as ex:
                    logger.debug(f"[wellfound] Failed parsing element: {ex}")
                    continue
        except Exception as e:
            logger.error(f"[wellfound] Scraping failed: {e}")
