"""
Gmail Scraper — pulls daily internship listings directly from your Gmail inbox
using Google OAuth credentials and the Gmail API, parsing listings via LLM.
"""
from __future__ import annotations
import os
import json
import base64
import re
from typing import Iterator
from datetime import datetime, timezone, timedelta
from loguru import logger
from bs4 import BeautifulSoup

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .base import BaseScraper
from .normalizer import RawJob
from ..config import settings
from ..llm.client import generate_text

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

def get_gmail_credentials() -> Credentials:
    """Load or refresh Google OAuth credentials."""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    secrets_dir = os.path.join(project_root, "secrets")
    
    token_path = os.path.join(secrets_dir, "token.json")
    creds_path = os.path.join(secrets_dir, "credentials.json")
    
    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception as e:
            logger.error(f"Failed to load existing token.json: {e}")

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as refresh_err:
                logger.warning(f"Could not refresh OAuth token: {refresh_err}. Triggering new authorization flow...")
                creds = None
                
        if not creds:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(
                    f"Google Client Secret credentials not found at {creds_path}. "
                    "Please place the client secret file there to authorize Gmail."
                )
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
            
        # Save the credentials for the next run
        os.makedirs(secrets_dir, exist_ok=True)
        with open(token_path, "w") as token:
            token.write(creds.to_json())
            
    return creds

def extract_email_body(message_data: dict) -> str:
    """Extract the text body of a Gmail message dict recursively."""
    payload = message_data.get("payload", {})
    
    def parse_parts(parts):
        text = ""
        html = ""
        for part in parts:
            mime = part.get("mimeType", "")
            body_data = part.get("body", {}).get("data", "")
            if body_data:
                try:
                    # Gmail Base64URL encoding uses '-' and '_' instead of '+' and '/'
                    decoded = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="ignore")
                except Exception:
                    decoded = ""
                
                if mime == "text/plain":
                    text += decoded
                elif mime == "text/html":
                    html += decoded
            
            if "parts" in part:
                t, h = parse_parts(part["parts"])
                text += t
                html += h
        return text, html

    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")
    
    if body_data:
        try:
            decoded = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="ignore")
        except Exception:
            decoded = ""
        if mime_type == "text/plain":
            return decoded
        elif mime_type == "text/html":
            return BeautifulSoup(decoded, "html.parser").get_text(separator=" ").strip()

    if "parts" in payload:
        text, html = parse_parts(payload["parts"])
        if text.strip():
            return text.strip()
        if html.strip():
            return BeautifulSoup(html, "html.parser").get_text(separator=" ").strip()
            
    return ""

def clean_json_response(text: str) -> list[dict]:
    """Extract and parse valid JSON array from LLM response."""
    text = text.strip()
    # Remove markdown code blocks if present
    match = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
    else:
        text = re.sub(r"^```(?:json)?", "", text, flags=re.MULTILINE)
        text = re.sub(r"```$", "", text, flags=re.MULTILINE)
        text = text.strip()
    
    try:
        return json.loads(text)
    except Exception as e:
        logger.error(f"Failed to parse JSON from LLM response: {e}. Raw response snippet: {text[:500]}")
        return []

class GmailScraper(BaseScraper):
    source_name = "gmail"

    def fetch_raw(self) -> Iterator[RawJob]:
        """Query emails and use LLM to extract job listings."""
        if not settings.gmail_enabled:
            logger.info("Gmail ingestion is disabled via settings.")
            return

        logger.info("[gmail] Fetching credentials and connecting to Gmail API...")
        try:
            creds = get_gmail_credentials()
            service = build("gmail", "v1", credentials=creds)
        except Exception as auth_err:
            logger.error(f"[gmail] Authentication/Connection failed: {auth_err}")
            return

        # Calculate time threshold based on lookback setting
        now = datetime.now(timezone.utc)
        lookback_date = now - timedelta(days=settings.gmail_lookback_days)
        # Format query to limit search space
        query = f"{settings.gmail_search_query} after:{lookback_date.strftime('%Y/%m/%d')}"
        logger.info(f"[gmail] Querying inbox with filter: '{query}'")

        try:
            results = service.users().messages().list(userId="me", q=query).execute()
            messages = results.get("messages", [])
        except Exception as api_err:
            logger.error(f"[gmail] Failed to query Gmail API: {api_err}")
            return

        if not messages:
            logger.info("[gmail] No matching emails found in lookback window.")
            return

        logger.info(f"[gmail] Found {len(messages)} potential emails. Processing...")

        for msg_summary in messages:
            msg_id = msg_summary["id"]
            try:
                msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
                
                # Extract headers
                headers = msg.get("payload", {}).get("headers", [])
                subject = "Unknown Subject"
                sender = "Unknown Sender"
                date_str = None
                
                for h in headers:
                    name = h.get("name", "").lower()
                    if name == "subject":
                        subject = h.get("value", "")
                    elif name == "from":
                        sender = h.get("value", "")
                    elif name == "date":
                        date_str = h.get("value", "")

                logger.info(f"[gmail] Parsing email: '{subject}' from {sender}")
                body = extract_email_body(msg)
                
                if not body.strip():
                    logger.warning(f"[gmail] Skipping message {msg_id} - empty body.")
                    continue

                # Use LLM to extract job listings
                prompt = f"""
                You are an expert recruitment parser. Extract all internship and job listings from the following email.
                
                Email Subject: {subject}
                Email Sender: {sender}
                Email Date: {date_str}
                
                Email Body:
                {body[:8000]}
                
                Provide the output as a valid JSON array of objects. Each object MUST represent a single internship/job listing and contain the following fields:
                - "source_id": A string uniquely identifying this job within this email (e.g. "company_role_1"). Must not have spaces.
                - "title": Title of the position.
                - "company": Name of the company offering the role.
                - "location": Location of the role (e.g. "Bangalore, India", or "Remote").
                - "url": Application URL or official link. If none is mentioned, return a link to search the company on Google (e.g. "https://www.google.com/search?q=careers+companyname") or if there is an email address to apply to, return "mailto:recruiter@email.com".
                - "description": A descriptive summary of the role, requirements, skills, and application instructions. Keep it descriptive (minimum 3 sentences).
                - "tags": A list of strings representing skills, requirements, or labels (e.g. ["Python", "AI", "Machine Learning", "Summer 2026"]).
                - "is_remote": A boolean (true/false) indicating if the role is remote.
                
                Ensure you return ONLY a valid JSON array. Do not include any explanation, markdown formatting tags (like ```json), or other characters outside the valid JSON block.
                If there are absolutely no job or internship listings in the email, return an empty array `[]`.
                """
                
                logger.info(f"[gmail] Requesting LLM extraction for message {msg_id}...")
                response_text = generate_text(prompt)
                extracted_jobs = clean_json_response(response_text)
                
                logger.info(f"[gmail] LLM extracted {len(extracted_jobs)} listings from message {msg_id}.")
                
                for idx, job in enumerate(extracted_jobs):
                    yield RawJob(
                        source=self.source_name,
                        source_id=f"{msg_id}_{job.get('source_id') or idx}",
                        title=job.get("title", "Unknown Title"),
                        company=job.get("company", "Unknown Company"),
                        location=job.get("location", "Not Specified"),
                        url=job.get("url", ""),
                        description=job.get("description", ""),
                        tags=job.get("tags", ["gmail"]),
                        is_remote=bool(job.get("is_remote", False)),
                        posted_at=None # Gmail dates can be parsed, but None defaults to fetching/arrival datetime
                    )
                    
            except Exception as e:
                logger.error(f"[gmail] Failed parsing message {msg_id}: {e}")
                continue
