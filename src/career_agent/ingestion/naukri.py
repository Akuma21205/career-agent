from __future__ import annotations
import re
from typing import Iterator
from loguru import logger
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from .base import BaseScraper
from .normalizer import RawJob

class NaukriScraper(BaseScraper):
    source_name = "naukri"
    
    def fetch_raw(self) -> Iterator[RawJob]:
        logger.info("[naukri] Fetching AI intern jobs from Naukri...")
        url = "https://www.naukri.com/ai-intern-or-machine-learning-intern-jobs"
        
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
            
            # Locate job cards in the HTML structure
            cards = soup.select(".srp-job-tuple, .jobTuple, [data-job-id]")
            logger.info(f"[naukri] Found {len(cards)} job card elements.")
            
            for el in cards:
                try:
                    title_el = el.select_one("a.title, a[class*='title']")
                    if not title_el:
                        continue
                    title = title_el.text.strip()
                    job_url = title_el["href"]
                    if job_url.startswith("/"):
                        job_url = "https://www.naukri.com" + job_url
                        
                    source_id = el.get("data-job-id") or job_url.split("-")[-1]
                    
                    company_el = el.select_one("a.comp-name, .companyName, a[class*='comp-name']")
                    company = company_el.text.strip() if company_el else "Naukri Company"
                    
                    location_el = el.select_one(".locWdth, .loc-wrap, span[class*='loc']")
                    location = location_el.text.strip() if location_el else "India"
                    
                    is_remote = "remote" in location.lower() or "work from home" in location.lower() or "remote" in title.lower()
                    
                    desc_el = el.select_one(".job-desc, .jobDescription")
                    desc = desc_el.text.strip() if desc_el else ""
                    full_text = f"{desc} {el.get_text(separator=' ').strip()}"
                    
                    yield RawJob(
                        source=self.source_name,
                        source_id=str(source_id),
                        title=title,
                        company=company,
                        location=location,
                        url=job_url,
                        description=full_text,
                        tags=["naukri", "india"],
                        is_remote=is_remote,
                        posted_at=None
                    )
                except Exception as ex:
                    logger.debug(f"[naukri] Failed parsing card element: {ex}")
                    continue
        except Exception as e:
            logger.error(f"[naukri] Scraping failed: {e}")
