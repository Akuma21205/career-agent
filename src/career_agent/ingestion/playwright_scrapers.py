from __future__ import annotations
import re
from typing import Iterator
from loguru import logger
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from .base import BaseScraper
from .normalizer import RawJob

class PlaywrightScrapers(BaseScraper):
    source_name = "playwright_unified"
    
    def scrape_linkedin(self) -> list[RawJob]:
        """Scrape public LinkedIn guest job search API without requiring auth."""
        logger.info("[playwright_scrapers] Crawling public LinkedIn listings...")
        jobs = []
        
        search_configs = [
            # India listings
            {"keywords": "AI Intern", "location": "India"},
            {"keywords": "Machine Learning Intern", "location": "India"},
            {"keywords": "AI Researcher", "location": "India"},
            # Global / Remote listings
            {"keywords": "AI Intern", "location": "Remote"},
            {"keywords": "Machine Learning Intern", "location": "Remote"},
            {"keywords": "AI Intern Remote", "location": "United States"},
            {"keywords": "Machine Learning Intern Remote", "location": "United States"},
        ]
        
        for cfg in search_configs:
            kw = cfg["keywords"]
            loc = cfg["location"]
            kw_encoded = kw.replace(" ", "+")
            loc_encoded = loc.replace(" ", "+")
            
            # Pull jobs from last 7 days
            url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={kw_encoded}&location={loc_encoded}&f_TPR=r604800"
            try:
                import httpx
                r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}, timeout=15.0)
                if r.status_code != 200:
                    continue
                    
                soup = BeautifulSoup(r.text, "html.parser")
                cards = soup.find_all("li")
                logger.info(f"[playwright_scrapers] LinkedIn found {len(cards)} listings for keywords='{kw}', location='{loc}'")
                
                for el in cards:
                    try:
                        title_el = el.select_one(".base-search-card__title")
                        if not title_el:
                            continue
                        title = title_el.text.strip()
                        
                        link_el = el.select_one("a.base-card__full-link")
                        job_url = link_el["href"].split("?")[0] if link_el else ""
                        
                        source_id_el = el.select_one("div[data-entity-id]")
                        source_id = source_id_el["data-entity-id"] if source_id_el else job_url.split("-")[-1]
                        
                        company_el = el.select_one(".base-search-card__subtitle")
                        company = company_el.text.strip() if company_el else "LinkedIn Employer"
                        
                        location_el = el.select_one(".job-search-card__location")
                        location = location_el.text.strip() if location_el else loc
                        
                        is_remote = "remote" in location.lower() or "remote" in title.lower() or "remote" in loc.lower()
                        
                        desc_text = el.get_text(separator=" ").strip()
                        
                        jobs.append(RawJob(
                            source="linkedin",
                            source_id=str(source_id),
                            title=title,
                            company=company,
                            location=location,
                            url=job_url,
                            description=desc_text,
                            tags=["linkedin", loc.lower()],
                            is_remote=is_remote,
                            posted_at=None
                        ))
                    except Exception as ex:
                        continue
            except Exception as e:
                logger.debug(f"[playwright_scrapers] LinkedIn failed for keywords='{kw}', location='{loc}': {e}")
        return jobs

    def scrape_indeed(self, page) -> list[RawJob]:
        """Scrape Indeed India using Playwright browser automation."""
        logger.info("[playwright_scrapers] Crawling Indeed India listings...")
        jobs = []
        url = "https://in.indeed.com/jobs?q=AI+Intern&l=Remote"
        
        try:
            page.goto(url, wait_until="commit", timeout=30000)
            page.wait_for_timeout(6000)
            content = page.content()
            
            if "cloudflare" in content.lower():
                logger.warning("[playwright_scrapers] Indeed blocked by Cloudflare verification. Skipping.")
                return []
                
            soup = BeautifulSoup(content, "html.parser")
            cards = soup.select(".job_seen_beacon, td.resultContent")
            logger.info(f"[playwright_scrapers] Indeed found {len(cards)} job cards.")
            
            for el in cards:
                try:
                    title_el = el.select_one("h2.jobTitle a, a[data-jk]")
                    if not title_el:
                        continue
                    title = title_el.text.strip()
                    job_key = title_el.get("data-jk") or title_el["href"].split("jk=")[-1].split("&")[0]
                    job_url = f"https://in.indeed.com/viewjob?jk={job_key}"
                    
                    company_el = el.select_one(".companyName, [class*='companyName']")
                    company = company_el.text.strip() if company_el else "Indeed Employer"
                    
                    location_el = el.select_one(".companyLocation, [class*='companyLocation']")
                    location = location_el.text.strip() if location_el else "India"
                    
                    is_remote = "remote" in location.lower() or "remote" in title.lower()
                    
                    desc_text = el.get_text(separator=" ").strip()
                    
                    jobs.append(RawJob(
                        source="indeed",
                        source_id=str(job_key),
                        title=title,
                        company=company,
                        location=location,
                        url=job_url,
                        description=desc_text,
                        tags=["indeed", "india"],
                        is_remote=is_remote,
                        posted_at=None
                    ))
                except Exception as ex:
                    continue
        except Exception as e:
            logger.error(f"[playwright_scrapers] Indeed scraping failed: {e}")
        return jobs

    def scrape_glassdoor(self, page) -> list[RawJob]:
        """Scrape Glassdoor India using Playwright browser automation."""
        logger.info("[playwright_scrapers] Crawling Glassdoor India listings...")
        jobs = []
        url = "https://www.glassdoor.co.in/Job/india-ai-intern-jobs-SRCH_IL.0,5_IN115_KO6,15.htm"
        
        try:
            page.goto(url, wait_until="commit", timeout=30000)
            page.wait_for_timeout(6000)
            content = page.content()
            
            if "cloudflare" in content.lower():
                logger.warning("[playwright_scrapers] Glassdoor blocked by Cloudflare verification. Skipping.")
                return []
                
            soup = BeautifulSoup(content, "html.parser")
            cards = soup.select("[data-test='jobListing']")
            logger.info(f"[playwright_scrapers] Glassdoor found {len(cards)} job listings.")
            
            for el in cards:
                try:
                    title_el = el.select_one("[data-test='job-title']")
                    if not title_el:
                        continue
                    title = title_el.text.strip()
                    job_url = title_el["href"]
                    if job_url.startswith("/"):
                        job_url = "https://www.glassdoor.co.in" + job_url
                        
                    source_id = el.get("data-jobid") or job_url.split("jl=")[-1].split("&")[0]
                    
                    company_el = el.select_one("[class*='compactEmployerName']")
                    company = company_el.text.strip() if company_el else "Glassdoor Employer"
                    
                    location_el = el.select_one("[data-test='emp-location']")
                    location = location_el.text.strip() if location_el else "India"
                    
                    is_remote = "remote" in location.lower() or "remote" in title.lower()
                    
                    desc_el = el.select_one("[data-test='descSnippet']")
                    desc = desc_el.text.strip() if desc_el else ""
                    
                    salary_el = el.select_one("[data-test='detailSalary']")
                    salary_text = f"Salary: {salary_el.text.strip()}. " if salary_el else ""
                    full_text = f"{salary_text}{desc} {el.get_text(separator=' ').strip()}"
                    
                    jobs.append(RawJob(
                        source="glassdoor",
                        source_id=str(source_id),
                        title=title,
                        company=company,
                        location=location,
                        url=job_url,
                        description=full_text,
                        tags=["glassdoor", "india"],
                        is_remote=is_remote,
                        posted_at=None
                    ))
                except Exception as ex:
                    continue
        except Exception as e:
            logger.error(f"[playwright_scrapers] Glassdoor scraping failed: {e}")
        return jobs

    def fetch_raw(self) -> Iterator[RawJob]:
        # 1. Fetch public LinkedIn listings
        for job in self.scrape_linkedin():
            yield job
            
        # 2. Fetch Indeed & Glassdoor listings using browser automation
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                context = browser.new_context(ignore_https_errors=True)
                page = context.new_page()
                
                # Fetch Indeed
                for job in self.scrape_indeed(page):
                    yield job
                    
                # Fetch Glassdoor
                for job in self.scrape_glassdoor(page):
                    yield job
                    
                browser.close()
        except Exception as e:
            logger.error(f"[playwright_scrapers] Playwright session failed: {e}")
