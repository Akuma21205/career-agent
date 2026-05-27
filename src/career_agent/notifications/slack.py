import httpx
from datetime import datetime
from loguru import logger
from career_agent.config import settings

def send_slack_message(payload: dict, webhook_url: str | None = None) -> bool:
    """Send a generic JSON payload to the Slack Webhook URL."""
    url = webhook_url or settings.slack_webhook_url
    if not url:
        logger.warning("Slack webhook URL is not configured. Skipping notification.")
        return False
        
    try:
        r = httpx.post(url, json=payload, timeout=10.0)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to dispatch Slack notification: {e}")
        return False

def send_job_match_alert(job: dict, matches: list[dict], analysis: dict | None = None) -> bool:
    """
    Format and dispatch a structured Block Kit match alert to Slack.
    job: dictionary containing company, title, location, similarity, url, etc.
    matches: list of dictionaries representing top matching CV accomplishments.
    analysis: optional LLM job description analysis (stipend, summary, skills match, confidence).
    """
    company = job.get("company", "Unknown Company")
    title = job.get("title", "Unknown Title")
    location = job.get("location", "Not Specified") or "Not Specified"
    url = job.get("url", "#")
    
    is_remote_val = job.get("is_remote", 0)
    remote_status = "Yes 🏠" if is_remote_val == 1 or is_remote_val is True else "No 🏢"
    
    posted_date = job.get("posted_at")
    if posted_date:
        try:
            dt = datetime.fromisoformat(posted_date.replace("Z", "+00:00"))
            posted_display = dt.strftime("%b %d, %Y")
        except Exception:
            posted_display = "Recently"
    else:
        posted_display = "Recently"
        
    # Extract LLM analysis fields or default to fallback values
    if not analysis:
        analysis = {
            "stipend": "💵 Unspecified / None mentioned",
            "about": "No core responsibilities or description analyzed.",
            "matched_skills": [],
            "missing_skills": [],
            "confidence_level": "Medium",
            "success_probability": 50,
            "confidence_reason": "No automated AI analysis performed."
        }
        
    stipend = analysis.get("stipend", "💵 Unspecified / None mentioned")
    about = analysis.get("about", "No core responsibilities or description analyzed.")
    matched_skills = analysis.get("matched_skills", [])
    missing_skills = analysis.get("missing_skills", [])
    confidence_level = analysis.get("confidence_level", "Medium")
    success_probability = analysis.get("success_probability", 50)
    confidence_reason = analysis.get("confidence_reason", "")
    
    # Emojis for confidence match level
    conf_level_norm = str(confidence_level).strip().capitalize()
    if "High" in conf_level_norm:
        conf_emoji = "🟢"
    elif "Low" in conf_level_norm:
        conf_emoji = "🔴"
    else:
        conf_emoji = "🟡"
        
    # Format skills lists into bold readable spans
    matched_skills_text = ", ".join(f"`{s}`" for s in matched_skills) if matched_skills else "_None explicitly matched_"
    missing_skills_text = ", ".join(f"`{s}`" for s in missing_skills) if missing_skills else "_None explicitly missing_"
    
    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🎯 New Internship Match Discovered!"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Company*: {company}\n*Title*: {title}\n*Location*: {location}\n*Remote*: {remote_status}\n*Posted Date*: {posted_display}"
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*💵 Stipend*: {stipend}\n\n*📝 What the Job is About*:\n{about}"
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*📈 Match Confidence*: {conf_emoji} *{confidence_level}* (`{success_probability}%` success probability)\n"
                        f"*Reason*: {confidence_reason}\n\n"
                        f"*✅ Skills Matched ({len(matched_skills)})*:\n{matched_skills_text}\n\n"
                        f"*⚠️ Missing Requirements*:\n{missing_skills_text}"
                    )
                }
            },
            {
                "type": "divider"
            }
        ]
    }
    
    # Add a direct action button to the application URL if it is valid
    if url and url != "#" and url.startswith("http"):
        payload["blocks"].append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Apply Now 🚀"
                    },
                    "url": url,
                    "style": "primary"
                }
            ]
        })
        
    # Dynamic routing map based on job source
    source_webhooks = {
        "linkedin": settings.linkedin_slack_webhook_url,
        "greenhouse": settings.greenhouse_slack_webhook_url,
        "glassdoor": settings.glassdoor_slack_webhook_url,
        "indeed": settings.indeed_slack_webhook_url,
        "internshala": settings.internshala_slack_webhook_url,
        "naukri": settings.naukri_slack_webhook_url,
        "prosple": settings.prosple_slack_webhook_url,
        "ycombinator": settings.ycombinator_slack_webhook_url,
        "gmail": settings.gmail_slack_webhook_url,
    }
    
    src = job.get("source")
    webhook_url = source_webhooks.get(src) if src else None
    
    # Fallback to default webhook if source-specific one is not configured
    if not webhook_url:
        webhook_url = settings.slack_webhook_url
        
    return send_slack_message(payload, webhook_url=webhook_url)

def send_hr_contact_alert(company_name: str, domain: str, contacts: list[dict]) -> bool:
    """
    Format and dispatch a verified recruiter contacts card to the Slack HR channel.
    """
    if not contacts:
        logger.info(f"No contacts to dispatch for {company_name}")
        return False
        
    fields_content = []
    for idx, c in enumerate(contacts, 1):
        name = c.get("name", "Unknown Recruiter")
        email = c.get("email", "No Email")
        role = c.get("role", "HR / Recruiter")
        verified_flag = "✅ Verified" if c.get("verified", True) else "🔍 Inferred"
        fields_content.append(
            f"{idx}. 👤 *{name}* — {role}\n"
            f"   📧 Email: `{email}` ({verified_flag})"
        )
        
    contacts_mrkdwn = "\n\n".join(fields_content)
    
    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🔍 HR Recruiter Contacts Discovered!"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Here are active/verified corporate recruiter and hiring manager contacts for *{company_name}* (`{domain}`). Use these directly for cold outreach/mailing!"
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": contacts_mrkdwn
                }
            },
            {
                "type": "divider"
            }
        ]
    }
    
    webhook_url = settings.hr_slack_webhook_url or settings.slack_webhook_url
    if not webhook_url:
        logger.warning("HR Slack webhook URL is not configured. Skipping recruiter contacts notification.")
        return False
        
    return send_slack_message(payload, webhook_url=webhook_url)

