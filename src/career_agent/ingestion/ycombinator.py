from __future__ import annotations
import re
from typing import Iterator
from loguru import logger
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from .base import BaseScraper
from .normalizer import RawJob

class YCombinatorScraper(BaseScraper):
    source_name = "ycombinator"
    
    def fetch_raw(self) -> Iterator[RawJob]:
        logger.info("[ycombinator] Fetching jobs from YCombinator Job Board...")
        url = "https://www.ycombinator.com/jobs"
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="networkidle")
                page.wait_for_timeout(3000)
                
                # Scroll down a few times to trigger lazy-loaded listings
                for _ in range(5):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1000)
                
                content = page.content()
                browser.close()
                
            soup = BeautifulSoup(content, "html.parser")
            
            # Find all anchors that link to jobs (which match the pattern "/jobs/role/")
            job_anchors = soup.find_all("a", href=re.compile(r"/jobs/role/"))
            
            # Locate parents of these anchors to capture full job card contexts
            job_elements = []
            for a in job_anchors:
                # Go up to the closest container (typically list item or div card)
                parent = a.find_parent("li") or a.find_parent("div", class_=re.compile(r"card|row|item"))
                if parent and parent not in job_elements:
                    job_elements.append(parent)
                    
            logger.info(f"[ycombinator] Found {len(job_elements)} job card elements.")
            
            for el in job_elements:
                try:
                    job_link_el = el.find("a", href=re.compile(r"/jobs/role/"))
                    if not job_link_el:
                        continue
                        
                    job_url = "https://www.ycombinator.com" + job_link_el["href"]
                    source_id = job_link_el["href"].split("/jobs/role/")[-1].split("?")[0]
                    
                    title = job_link_el.text.strip()
                    
                    # Find company link
                    company_el = el.find("a", href=re.compile(r"/companies/"))
                    company = company_el.text.strip() if company_el else "YC Startup"
                    
                    # Extract full context text for description and metadata
                    full_text = el.get_text(separator=" ").strip()
                    
                    # Deduce remote status
                    is_remote = "remote" in full_text.lower() or "remote" in title.lower()
                    
                    # Location heuristic
                    location = "Remote"
                    # Match location indicators like 'Bengaluru', 'San Francisco', etc.
                    loc_match = re.search(r"\b(Bengaluru|Bangalore|Hyderabad|Remote|San Francisco|New York|London|India)\b", full_text, re.IGNORECASE)
                    if loc_match:
                        location = loc_match.group(1).capitalize()
                    
                    yield RawJob(
                        source=self.source_name,
                        source_id=source_id,
                        title=title,
                        company=company,
                        location=location,
                        url=job_url,
                        description=full_text,
                        tags=["ycombinator", "startup"],
                        is_remote=is_remote,
                        posted_at=None
                    )
                except Exception as ex:
                    logger.debug(f"[ycombinator] Failed parsing card element: {ex}")
                    continue
        except Exception as e:
            logger.error(f"[ycombinator] Scraping failed: {e}")
