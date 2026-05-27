from __future__ import annotations
import re
from typing import Iterator
from loguru import logger
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from .base import BaseScraper
from .normalizer import RawJob

class InternshalaScraper(BaseScraper):
    source_name = "internshala"
    
    def fetch_raw(self) -> Iterator[RawJob]:
        logger.info("[internshala] Fetching AI/ML internships from Internshala...")
        url = "https://internshala.com/internships/keywords-artificial-intelligence-ai,machine-learning-ml"
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(ignore_https_errors=True)
                page.goto(url, wait_until="commit", timeout=30000)
                page.wait_for_timeout(5000)
                content = page.content()
                browser.close()
                
            soup = BeautifulSoup(content, "html.parser")
            
            # Find internship card elements
            cards = soup.select("div.individual_internship")
            logger.info(f"[internshala] Found {len(cards)} internship cards.")
            
            for el in cards:
                try:
                    title_el = el.select_one("a.job-title-href, #job_title")
                    if not title_el:
                        continue
                    title = title_el.text.strip()
                    job_url = title_el["href"]
                    if job_url.startswith("/"):
                        job_url = "https://internshala.com" + job_url
                    
                    # Extract source ID from URL
                    source_id = el.get("internshipid") or job_url.split("/")[-1]
                    
                    company_el = el.select_one("p.company-name")
                    company = company_el.text.strip() if company_el else "Unknown Company"
                    
                    location_el = el.select_one(".locations")
                    location = location_el.text.strip() if location_el else "India"
                    
                    # Check if remote
                    is_remote = "work from home" in location.lower() or "remote" in location.lower() or "remote" in title.lower()
                    
                    # Extract stipend
                    stipend_el = el.select_one(".stipend")
                    stipend = stipend_el.text.strip() if stipend_el else "Paid"
                    
                    # Use full text context for search matching
                    desc = f"Stipend: {stipend}. " + el.get_text(separator=" ").strip()
                    
                    yield RawJob(
                         source=self.source_name,
                         source_id=str(source_id),
                         title=title,
                         company=company,
                         location=location,
                         url=job_url,
                         description=desc,
                         tags=["internshala", "internship", "india", stipend],
                         is_remote=is_remote,
                         posted_at=None
                    )
                except Exception as ex:
                    logger.debug(f"[internshala] Failed parsing card: {ex}")
                    continue
        except Exception as e:
            logger.error(f"[internshala] Scraping failed: {e}")
