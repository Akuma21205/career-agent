from __future__ import annotations
import re
from typing import Iterator
from loguru import logger
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from .base import BaseScraper
from .normalizer import RawJob

class ProspleScraper(BaseScraper):
    source_name = "prosple"
    
    def fetch_raw(self) -> Iterator[RawJob]:
        logger.info("[prosple] Fetching tech/AI jobs from India Prosple...")
        url = "https://in.prosple.com/search-jobs?sectors=126"
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                context = browser.new_context(ignore_https_errors=True)
                page = context.new_page()
                page.goto(url, wait_until="commit", timeout=30000)
                page.wait_for_timeout(6000)
                content = page.content()
                browser.close()
                
            soup = BeautifulSoup(content, "html.parser")
            
            cards = soup.find_all("section")
            logger.info(f"[prosple] Found {len(cards)} job card elements.")
            
            for el in cards:
                try:
                    # Find links containing jobs-internships
                    title_el = el.find("a", href=re.compile(r"/jobs-internships/|/opportunities/"))
                    if not title_el:
                        continue
                    title = title_el.text.strip()
                    job_url = title_el["href"]
                    if job_url.startswith("/"):
                        job_url = "https://in.prosple.com" + job_url
                    source_id = job_url.split("/")[-1]
                    
                    # Company is image alt text
                    img_el = el.find("img")
                    company = img_el["alt"].strip() if img_el and img_el.get("alt") else "Prosple Employer"
                    
                    # Find location text next to SVG pin
                    loc_svg = el.find("svg", viewBox="0 0 256 256")
                    location = loc_svg.parent.text.strip() if loc_svg and loc_svg.parent else "India"
                    
                    is_remote = "remote" in location.lower() or "remote" in title.lower() or "remote" in el.text.lower()
                    
                    full_text = el.get_text(separator=" ").strip()
                    
                    yield RawJob(
                        source=self.source_name,
                        source_id=str(source_id),
                        title=title,
                        company=company,
                        location=location,
                        url=job_url,
                        description=full_text,
                        tags=["prosple", "india", "graduate-job"],
                        is_remote=is_remote,
                        posted_at=None
                    )
                except Exception as ex:
                    logger.debug(f"[prosple] Failed parsing card element: {ex}")
                    continue
        except Exception as e:
            logger.error(f"[prosple] Scraping failed: {e}")
