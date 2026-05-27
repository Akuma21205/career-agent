import re
import smtplib
import httpx
from typing import Optional
from loguru import logger
from playwright.sync_api import sync_playwright
from career_agent.config import settings

def resolve_mx_records(domain: str) -> list[str]:
    """Resolve MX records for a domain using Cloudflare DNS-over-HTTPS (DoH)."""
    url = f"https://cloudflare-dns.com/dns-query?name={domain}&type=MX"
    headers = {"Accept": "application/dns-json"}
    try:
        r = httpx.get(url, headers=headers, timeout=5.0)
        r.raise_for_status()
        data = r.json()
        answers = data.get("Answer", [])
        
        records = []
        for ans in answers:
            if ans.get("type") == 15:  # MX record type is 15
                parts = ans.get("data", "").split()
                if len(parts) >= 2:
                    priority = int(parts[0])
                    exchange = parts[1].rstrip(".")
                    records.append((priority, exchange))
        records.sort()  # Sort by priority
        return [rec[1] for rec in records]
    except Exception as e:
        logger.debug(f"Failed to resolve MX records for {domain} via DoH: {e}")
        return []

def verify_email_smtp(email: str, mx_servers: list[str]) -> bool:
    """Perform SMTP handshake validation for a target email address."""
    if not mx_servers:
        return False
        
    # Only check top 2 MX servers to prevent massive delays when port 25 is blocked
    for server in mx_servers[:2]:
        try:
            logger.info(f"SMTP Handshake: connecting to {server} to verify {email}...")
            smtp = smtplib.SMTP(server, port=25, timeout=3.0)
            smtp.helo("gmail.com")
            smtp.mail("sender@gmail.com")
            code, message = smtp.rcpt(email)
            smtp.quit()
            
            # SMTP code 250 means OK, mailbox exists
            if code == 250:
                logger.success(f"SMTP verification succeeded: {email} exists (250 OK)")
                return True
            else:
                msg = message.decode("utf-8", errors="ignore")
                logger.info(f"SMTP verification rejected {email} (Code {code}): {msg}")
        except Exception as e:
            logger.debug(f"SMTP connection failed on server {server}: {e}")
            continue
    return False

def generate_email_combinations(name: str, domain: str) -> list[str]:
    """Generate common professional email address variations based on a recruiter's name."""
    parts = re.split(r'\s+', name.strip().lower())
    if not parts:
        return []
        
    first = parts[0]
    last = parts[-1] if len(parts) > 1 else ""
    
    variations = []
    if last:
        variations.append(f"{first}.{last}@{domain}")
        variations.append(f"{first}{last}@{domain}")
        variations.append(f"{first}@{domain}")
        variations.append(f"{first[0]}{last}@{domain}")
        variations.append(f"{first}.{last[0]}@{domain}")
    else:
        variations.append(f"{first}@{domain}")
        
    # Standard roles
    variations.append(f"careers@{domain}")
    variations.append(f"hr@{domain}")
    variations.append(f"recruiting@{domain}")
    variations.append(f"jobs@{domain}")
    
    return list(dict.fromkeys(variations))

def query_qwen_coder(prompt: str) -> str:
    """Send parser requests to qwen/qwen3-coder:free via OpenRouter."""
    if not settings.openrouter_api_key:
        raise ValueError("OPENROUTER_API_KEY is not set.")
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Akuma21205/career-agent",
        "X-Title": "Career Agent HR Discovery"
    }
    
    payload = {
        "model": "qwen/qwen3-coder:free",
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        resp_json = r.json()
        
        if "choices" in resp_json and len(resp_json["choices"]) > 0:
            return resp_json["choices"][0]["message"]["content"]
        else:
            raise RuntimeError(f"Unexpected response structure: {resp_json}")

def discover_hr_contacts(company_name: str, domain: str) -> list[dict]:
    """
    Search company pages, crawl careers information,
    and parse contacts using qwen/qwen3-coder:free.
    """
    subpages = ["/careers", "/contact", "/about", "/jobs", ""]
    base_url = f"https://www.{domain}"
    
    raw_contacts = []
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            for sub in subpages:
                url = base_url + sub
                try:
                    logger.info(f"[contact_discovery] Crawling {url}...")
                    page.goto(url, wait_until="domcontentloaded", timeout=12000)
                    page.wait_for_timeout(2000)
                    
                    text = page.locator("body").inner_text()
                    snippet = text[:7000].strip()
                    if not snippet:
                        continue
                        
                    prompt = (
                        f"Extract recruiter, HR, hiring manager names and email addresses from the following page text. "
                        f"Company Name: {company_name}\n"
                        f"Domain: {domain}\n\n"
                        f"Page Text Snippet:\n{snippet}\n\n"
                        f"Return JSON format ONLY. If none are found, return empty array. "
                        f"Required JSON schema:\n"
                        f"[{{\"name\": \"Manager Name\", \"email\": \"email@domain.com\", \"role\": \"Recruiter\"}}]"
                    )
                    
                    response = query_qwen_coder(prompt)
                    
                    # Extract JSON block
                    json_match = re.search(r'\[\s*\{.*\}\s*\]', response, re.DOTALL)
                    if json_match:
                        parsed = json.loads(json_match.group(0))
                        for item in parsed:
                            if item not in raw_contacts:
                                raw_contacts.append(item)
                except Exception as page_err:
                    logger.debug(f"Failed crawling page {url}: {page_err}")
                    continue
                    
            browser.close()
    except Exception as e:
        logger.error(f"Playwright session failed: {e}")
        
    return raw_contacts

def discover_and_verify_emails(company_name: str, domain: str) -> list[dict]:
    """
    Crawl, parse recruiter names with Qwen-3-Coder, generate variations,
    and verify addresses using the SMTP handshake verification.
    """
    logger.info(f"Starting email discovery & verification for {company_name} ({domain})...")
    
    # 1. Scrape and parse contacts
    raw_contacts = discover_hr_contacts(company_name, domain)
    mx_servers = resolve_mx_records(domain)
    
    verified_contacts = []
    
    # 2. Iterate and verify
    for contact in raw_contacts:
        name = contact.get("name", "")
        email = contact.get("email", "")
        role = contact.get("role", "HR/Recruitment")
        
        if email:
            # Verify the email found on the page
            if verify_email_smtp(email, mx_servers):
                verified_contacts.append({"name": name, "email": email, "role": role, "verified": True})
        elif name:
            # Generate combinations and verify them
            logger.info(f"recruiter name '{name}' found without email. Guessing variations...")
            variations = generate_email_combinations(name, domain)
            
            for var in variations:
                if verify_email_smtp(var, mx_servers):
                    verified_contacts.append({"name": name, "email": var, "role": role, "verified": True})
                    break  # Stop guessing once we find a valid mailbox!
                    
    # 3. Fallback: If no custom recruiter was found, verify standard corporate role-based mailboxes
    if not verified_contacts:
        logger.info("No specific recruiter contacts resolved. Checking standard role-based mailboxes...")
        role_emails = [f"careers@{domain}", f"hr@{domain}", f"recruiting@{domain}", f"jobs@{domain}"]
        for r_email in role_emails:
            if verify_email_smtp(r_email, mx_servers):
                verified_contacts.append({"name": f"{company_name} Careers", "email": r_email, "role": "Hiring Department", "verified": True})
                
    # 4. Outbound SMTP Check Failure Fallback: 
    # If still empty (e.g. SMTP port 25 is blocked locally or timed out), 
    # preserve any discovered raw contacts, and fallback to the main standard role mailboxes.
    if not verified_contacts:
        logger.warning("SMTP handshake verification yielded zero active mailboxes (potentially blocked port 25). Falling back to inferred contacts.")
        for contact in raw_contacts:
            name = contact.get("name", "")
            email = contact.get("email", "")
            role = contact.get("role", "HR/Recruitment")
            if email:
                verified_contacts.append({"name": name, "email": email, "role": role, "verified": False})
            elif name:
                # Guessed standard professional email
                parts = re.split(r'\s+', name.strip().lower())
                first = parts[0] if parts else "recruiter"
                last = parts[-1] if len(parts) > 1 else ""
                guessed = f"{first}.{last}@{domain}" if last else f"{first}@{domain}"
                verified_contacts.append({"name": name, "email": guessed, "role": role, "verified": False})
                
        # If still empty (no scraped contacts found on site), supply standard corporate outreach addresses
        if not verified_contacts:
            logger.info("Supplying standard inferred corporate mailboxes.")
            verified_contacts.append({"name": f"{company_name} Careers", "email": f"careers@{domain}", "role": "Hiring Department", "verified": False})
            verified_contacts.append({"name": f"{company_name} HR", "email": f"hr@{domain}", "role": "Human Resources", "verified": False})
            
    return verified_contacts

import json
