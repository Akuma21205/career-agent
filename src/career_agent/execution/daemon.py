import sys
import time
from datetime import datetime, timezone, timedelta
from loguru import logger
from apscheduler.schedulers.blocking import BlockingScheduler

from career_agent.config import settings
from career_agent.database.schema import init as init_db, get_connection
from career_agent.database.operations import upsert_jobs, embed_and_store_jobs, search_similar_cv_chunks
from career_agent.ingestion.simplifyjobs import SimplifyJobsScraper
from career_agent.ingestion.greenhouse import GreenhouseScraper
from career_agent.ingestion.ycombinator import YCombinatorScraper
from career_agent.ingestion.internshala import InternshalaScraper
from career_agent.ingestion.naukri import NaukriScraper
from career_agent.ingestion.wellfound import WellfoundScraper
from career_agent.ingestion.prosple import ProspleScraper
from career_agent.ingestion.playwright_scrapers import PlaywrightScrapers
from career_agent.ingestion.gmail import GmailScraper
from career_agent.notifications.slack import send_job_match_alert, send_hr_contact_alert, send_slack_message
from career_agent.llm.job_analyzer import analyze_job_description
from career_agent.llm.client import generate_text
from career_agent.llm.contact import discover_and_verify_emails

def run_ingestion_cycle() -> dict:
    """Run job ingestion from all sources, upsert into DB, and generate embeddings."""
    logger.info("Starting multi-source ingestion cycle...")
    
    scrapers = [
        SimplifyJobsScraper(),
        GreenhouseScraper(),
        YCombinatorScraper(),
        InternshalaScraper(),
        NaukriScraper(),
        WellfoundScraper(),
        ProspleScraper(),
        PlaywrightScrapers()
    ]
    
    if settings.gmail_enabled:
        scrapers.append(GmailScraper())
    
    total_inserted = 0
    total_updated = 0
    
    for scraper in scrapers:
        try:
            logger.info(f"Running scraper sub-agent: {scraper.source_name}...")
            raw_jobs = list(scraper.scrape())
            logger.info(f"Scraper {scraper.source_name} yielded {len(raw_jobs)} normalized jobs.")
            counts = upsert_jobs(raw_jobs)
            total_inserted += counts.get("inserted", 0)
            total_updated += counts.get("updated", 0)
        except Exception as e:
            logger.error(f"Scraper sub-agent {scraper.source_name} failed: {e}")
            
    # Generate vector embeddings
    embedded = embed_and_store_jobs()
    logger.info(f"Multi-source ingestion cycle completed. {total_inserted} inserted, {total_updated} updated. Embedded {embedded} jobs.")
    return {"inserted": total_inserted, "updated": total_updated}

def load_job_intent_profile() -> str:
    """Load the user's Job Intent Profile from job_intent_profile.md."""
    try:
        from career_agent.config import PROJECT_ROOT
        path = PROJECT_ROOT / "job_intent_profile.md"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            # Return up to 3000 chars for high signal career profile content
            return content[:3000].strip()
    except Exception as e:
        logger.error(f"Failed to load job_intent_profile.md: {e}")
    return settings.job_intent_profile

def matches_role_keywords(title: str) -> bool:
    """Check if the job title corresponds to AI/ML/researcher roles semantically or strictly."""
    import re
    t = title.lower()
    
    # 1. Strict keyword check (fallback)
    keywords = {
        "ai", "ml", "artificial intelligence", "machine learning",
        "nlp", "computer vision", "llm", "deep learning", "agentic",
        "neural network", "researcher", "data scientist", "data science"
    }
    for kw in keywords:
        if len(kw) <= 3:
            if re.search(rf"\b{re.escape(kw)}\b", t):
                return True
        else:
            if kw in t:
                return True
                
    # 2. Semantic soft matching check
    try:
        from career_agent.embeddings.generator import embedder
        emb = embedder()
        title_vec = emb.embed_one(title)
        
        # Build semantic target query
        target_desc = "AI/ML Engineer, Agentic AI, LLM developer, researcher, or Data Scientist internship"
        target_vec = emb.embed_one(target_desc)
        
        sim = sum(a * b for a, b in zip(title_vec, target_vec))
        if sim >= settings.role_similarity_threshold:
            logger.info(f"Fuzzy semantic match found for title '{title}' (Similarity: {sim:.4f})")
            return True
    except Exception as e:
        logger.error(f"Fuzzy semantic title check failed: {e}")
        
    return False

def normalize_string(s: str) -> str:
    """Clean and lowercase a string to its bare alphanumeric form for robust comparison."""
    import re
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]", "", s.lower())

def normalize_url(url: str) -> str:
    """Normalize a URL by removing protocol schemes, 'www.', query parameters, and trailing slashes."""
    if not url:
        return ""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = parsed.path.rstrip('/')
        return f"{netloc}{path}"
    except Exception:
        return url.lower()

def matches_location(location: str, is_remote: bool, description: str = "") -> bool:
    """Check if the job location is remote, inside India, or an international remote job."""
    if is_remote:
        return True
    
    loc = (location or "").lower()
    desc = (description or "").lower()
    
    # Check for various remote keywords in the location
    remote_keywords = {"remote", "anywhere", "worldwide", "global", "wfh", "work from home", "telecommute", "distributed"}
    if any(kw in loc for kw in remote_keywords):
        return True
        
    # Also check if the description explicitly mentions global remote suitability
    if "remote friendly" in desc or "remote-friendly" in desc or "work from anywhere" in desc or "worldwide remote" in desc or "global remote" in desc:
        return True
    
    # List of Indian cities/hubs and country name
    hubs = {
        "india", "bangalore", "bengaluru", "hyderabad", "mumbai",
        "noida", "pune", "gurgaon", "gurugram", "chennai", "delhi", "kolkata"
    }
    for hub in hubs:
        if hub in loc:
            return True
    return False

def check_duplicate_exists(conn, company: str, title: str, url: str) -> bool:
    """Check if a duplicate job has already been alerted or tracked in the DB."""
    norm_url = normalize_url(url)
    
    # 1. First, check URL duplicate in applications table
    if norm_url:
        rows = conn.execute("""
            SELECT j.url, j.company, j.title 
            FROM applications a 
            JOIN jobs j ON a.job_id = j.id
        """).fetchall()
        
        norm_company = normalize_string(company)
        norm_title = normalize_string(title)
        
        for r in rows:
            if r["url"] and normalize_url(r["url"]) == norm_url:
                return True
            if normalize_string(r["company"]) == norm_company and normalize_string(r["title"]) == norm_title:
                return True
    else:
        # 2. Check company and title duplicate
        rows = conn.execute("""
            SELECT j.company, j.title 
            FROM applications a 
            JOIN jobs j ON a.job_id = j.id
        """).fetchall()
        
        norm_company = normalize_string(company)
        norm_title = normalize_string(title)
        
        for r in rows:
            if normalize_string(r["company"]) == norm_company and normalize_string(r["title"]) == norm_title:
                return True
                
    return False

def is_within_week(posted_at_str: str | None) -> bool:
    """Check if the job posting date is within the last 7 days."""
    if not posted_at_str:
        # Default to True so we don't drop jobs if the scraper couldn't extract the exact date
        return True
    try:
        dt_str = posted_at_str.replace("Z", "+00:00")
        posted_dt = datetime.fromisoformat(dt_str)
        if posted_dt.tzinfo is None:
            posted_dt = posted_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        difference = now - posted_dt
        return difference.days <= 7
    except Exception:
        return True

def resolve_company_domain(company_name: str, job_url: str, description: str) -> str | None:
    """Resolve the official corporate domain name of a company from job details."""
    import re
    from urllib.parse import urlparse
    
    # 1. First, check if the job URL points directly to a company website
    generic_domains = [
        "linkedin.com", "indeed.com", "glassdoor.co.in", "glassdoor.com", 
        "naukri.com", "internshala.com", "prosple.com", "ycombinator.com", 
        "wellfound.com", "simplify.jobs"
    ]
    
    if job_url and job_url.startswith("http"):
        try:
            parsed = urlparse(job_url)
            netloc = parsed.netloc.lower()
            if netloc.startswith("www."):
                netloc = netloc[4:]
            
            # If it's not a generic job board domain
            if not any(gen in netloc for gen in generic_domains):
                return netloc
        except Exception:
            pass

    # 2. Use LLM to resolve the domain name
    prompt = f"""
    Given the following company name and job details, resolve the official corporate website domain name (e.g. "stripe.com", "tekion.com", "qualcomm.com") of the company.
    
    Company Name: {company_name}
    Job URL: {job_url}
    Job Description Snippet:
    {description[:2000]}
    
    Return the domain name ONLY (e.g. "example.com"). Do not include "www.", "http", or any markdown code blocks or tags. If you cannot resolve it with high certainty, return "None".
    """
    try:
        response = generate_text(prompt).strip().lower()
        # Clean up any potential markdown code blocks or quotes
        response = re.sub(r"[`'\"]", "", response).strip()
        if " " in response or "none" in response or not response or "." not in response:
            clean_name = re.sub(r"[^a-zA-Z0-9]", "", company_name).lower()
            if clean_name:
                fallback_domain = f"{clean_name}.com"
                logger.info(f"Fallback domain resolved via heuristic: {fallback_domain}")
                return fallback_domain
            return None
        return response
    except Exception as e:
        logger.error(f"Failed to resolve company domain for {company_name} via LLM: {e}")
        # Fallback heuristic: clean name + .com
        clean_name = re.sub(r"[^a-zA-Z0-9]", "", company_name).lower()
        if clean_name:
            fallback_domain = f"{clean_name}.com"
            logger.info(f"Fallback domain resolved via heuristic: {fallback_domain}")
            return fallback_domain
        return None

def run_matchmaking_cycle() -> int:
    """
    Find jobs that have not been checked (match_score IS NULL),
    match them against Master CV, store scores, and send alerts if they are strong matches
    or selected via backfilling to meet the daily target.
    """
    logger.info("Starting matchmaking cycle...")
    conn = get_connection()
    alerts_sent = 0
    
    try:
        # Check how many jobs have been dispatched to Slack in the last 24 hours
        time_threshold = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        already_posted = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE last_updated >= ?",
            (time_threshold,)
        ).fetchone()[0]
        
        logger.info(f"Jobs posted to Slack in the last 24 hours: {already_posted}")
        remaining_target = max(0, settings.daily_slack_target - already_posted)
        logger.info(f"Remaining daily target to meet/reach: {remaining_target}")

        # Find all active jobs with match_score IS NULL
        unprocessed_jobs = conn.execute("""
            SELECT id, source, title, company, location, url, description, tags, is_remote, posted_at 
            FROM jobs 
            WHERE is_active = 1 AND match_score IS NULL
        """).fetchall()
        
        if not unprocessed_jobs:
            logger.info("No new jobs to match.")
            return 0
            
        logger.info(f"Found {len(unprocessed_jobs)} new jobs to run matchmaking for.")
        
        # 1. Build composite Negative Preference Vector from dismissed jobs
        neg_vec = None
        try:
            dismissed_jobs = conn.execute("SELECT title, description FROM jobs WHERE is_dismissed = 1").fetchall()
            if dismissed_jobs:
                from career_agent.embeddings.generator import embedder
                emb = embedder()
                neg_texts = [f"{d['title']}. {d['description'][:200]}" for d in dismissed_jobs[:10]]
                neg_vectors = emb.embed(neg_texts)
                dim = len(neg_vectors[0])
                avg_neg = [0.0] * dim
                for vec in neg_vectors:
                    for i in range(dim):
                        avg_neg[i] += vec[i]
                neg_vec = [v / len(neg_vectors) for v in avg_neg]
                logger.info(f"Loaded negative preference vector from {len(dismissed_jobs)} dismissed jobs.")
        except Exception as e:
            logger.error(f"Failed to build negative preference vector: {e}")

        # 2. Load JIP vector from job_intent_profile.md
        jip_vec = None
        try:
            from career_agent.embeddings.generator import embedder
            emb = embedder()
            jip_content = load_job_intent_profile()
            jip_vec = emb.embed_one(jip_content)
            logger.info("Loaded Job Intent Profile vector from job_intent_profile.md.")
        except Exception as e:
            logger.error(f"Failed to build JIP vector: {e}")
            
        filtered_roles = 0
        filtered_locations = 0
        filtered_old_jobs = 0
        
        candidates = []
        
        for job_row in unprocessed_jobs:
            job_id = job_row["id"]
            title = job_row["title"]
            company = job_row["company"]
            location = job_row["location"] or ""
            is_remote = bool(job_row["is_remote"])
            desc = job_row["description"] or ""
            posted_at = job_row["posted_at"]
            
            # 1. Apply Metadata Pre-Filtering (with fuzzy soft role check)
            if not matches_role_keywords(title):
                conn.execute("UPDATE jobs SET match_score = 0.0 WHERE id = ?", (job_id,))
                filtered_roles += 1
                continue
                
            if not matches_location(location, is_remote, desc):
                conn.execute("UPDATE jobs SET match_score = 0.0 WHERE id = ?", (job_id,))
                filtered_locations += 1
                continue
                
            if not is_within_week(posted_at):
                conn.execute("UPDATE jobs SET match_score = 0.0 WHERE id = ?", (job_id,))
                filtered_old_jobs += 1
                continue
                
            # 2. Run Chunked Semantic & Dual-Query Evaluation
            try:
                from career_agent.database.operations import chunk_text
                from career_agent.embeddings.generator import embedder
                emb = embedder()
                
                # Chunk job: header + description chunks
                chunks = [f"{title} at {company}"] + chunk_text(desc, chunk_size=500, overlap=100)
                
                chunk_scores = []
                all_matches = []
                
                for chunk in chunks:
                    # CV match
                    cv_matches = search_similar_cv_chunks(chunk, top_k=3)
                    cv_sim = max(m["similarity"] for m in cv_matches) if cv_matches else 0.0
                    all_matches.extend(cv_matches)
                    
                    # JIP match
                    jip_sim = 0.0
                    chunk_vec = None
                    if jip_vec:
                        chunk_vec = emb.embed_one(chunk)
                        jip_sim = sum(a * b for a, b in zip(chunk_vec, jip_vec))
                        
                    # Blend 50/50
                    chunk_score = 0.5 * cv_sim + 0.5 * jip_sim
                    
                    # Negative feedback penalty
                    if neg_vec and chunk_vec:
                        neg_sim = sum(a * b for a, b in zip(chunk_vec, neg_vec))
                        chunk_score -= 0.15 * max(0.0, neg_sim)
                        
                    chunk_scores.append(chunk_score)
                    
                # Base score is the maximum chunk score
                base_score = max(chunk_scores) if chunk_scores else 0.0
                
                # 3. Company Tier Boosting
                company_norm = company.lower().strip()
                tier1 = [c.strip() for c in settings.tier1_companies.split(",")]
                tier2 = [c.strip() for c in settings.tier2_companies.split(",")]
                
                boost = 0.0
                if any(t in company_norm for t in tier1):
                    boost = 0.15
                elif any(t in company_norm for t in tier2):
                    boost = 0.08
                    
                # 4. Time Decay
                days_old = 0.0
                if posted_at:
                    try:
                        posted_dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        days_old = max(0.0, (now - posted_dt).total_seconds() / 86400.0)
                    except Exception:
                        pass
                decay = max(0.5, 1.0 - 0.07 * days_old)
                
                # Compute final match score
                final_score = round(max(0.0, (base_score + boost) * decay), 4)
                
                # Update database
                conn.execute(
                    "UPDATE jobs SET match_score = ? WHERE id = ?",
                    (final_score, job_id)
                )
                
                # Format matches for Slack block kit output
                all_matches.sort(key=lambda x: x["similarity"], reverse=True)
                top_matches = all_matches[:3]
                
                candidates.append({
                    "job_row": job_row,
                    "max_sim": final_score,
                    "matches": top_matches
                })
                
            except Exception as eval_err:
                logger.error(f"Failed semantic matchmaking for job {job_id}: {eval_err}")
                # Fallback to a zero score
                conn.execute("UPDATE jobs SET match_score = 0.0 WHERE id = ?", (job_id,))
            
        # Commit match score updates
        conn.commit()
        
        if filtered_roles or filtered_locations or filtered_old_jobs:
            logger.info(f"Pre-filtering stats: {filtered_roles} skipped by role, {filtered_locations} skipped by location, {filtered_old_jobs} skipped by age (>7 days).")
            
        if not candidates:
            logger.info("No candidates passed pre-filtering in this run.")
            return 0
            
        # Partition candidates based on settings thresholds
        strong_matches = [c for c in candidates if c["max_sim"] >= settings.match_threshold]
        backfill_candidates = [
            c for c in candidates 
            if settings.min_backfill_threshold <= c["max_sim"] < settings.match_threshold
        ]
        
        # Sort by similarity score in descending order
        strong_matches.sort(key=lambda x: x["max_sim"], reverse=True)
        backfill_candidates.sort(key=lambda x: x["max_sim"], reverse=True)
        
        # Select jobs to post
        selected_to_post = list(strong_matches)
        
        # Backfill if target is not met and we have backfill candidates
        if len(strong_matches) < remaining_target and backfill_candidates:
            backfill_needed = remaining_target - len(strong_matches)
            backfill_selection = backfill_candidates[:backfill_needed]
            logger.info(f"Strong matches ({len(strong_matches)}) are fewer than remaining target ({remaining_target}). Backfilling {len(backfill_selection)} moderate matches (down to floor {settings.min_backfill_threshold}).")
            selected_to_post.extend(backfill_selection)
        elif len(strong_matches) < remaining_target:
            logger.info(f"Strong matches ({len(strong_matches)}) are fewer than remaining target ({remaining_target}), but no eligible moderate matches are available for backfilling.")
            
        logger.info(f"Selected {len(selected_to_post)} jobs to post to Slack in this run.")
        
        posted_in_current_batch = []
        for item in selected_to_post:
            job_row = item["job_row"]
            max_sim = item["max_sim"]
            matches = item["matches"]
            
            job_id = job_row["id"]
            title = job_row["title"]
            company = job_row["company"]
            location = job_row["location"] or ""
            is_remote = bool(job_row["is_remote"])
            desc = job_row["description"] or ""
            url = job_row["url"]
            
            # Check for duplicate in previously posted or in current batch
            if check_duplicate_exists(conn, company, title, url) or any(
                (normalize_string(company) == normalize_string(p["company"]) and normalize_string(title) == normalize_string(p["title"]))
                or (normalize_url(url) and normalize_url(p["url"]) and normalize_url(url) == normalize_url(p["url"]))
                for p in posted_in_current_batch
            ):
                logger.info(f"Skipping duplicate job: {title} at {company} (URL: {url})")
                continue
                
            posted_in_current_batch.append({"company": company, "title": title, "url": url})
            
            logger.success(f"POSTING JOB: {title} at {company} (Score: {max_sim:.4f})")
            
            # Try to insert into applications tracking table as 'discovered'
            now_str = datetime.now(timezone.utc).isoformat()
            try:
                conn.execute("""
                    INSERT INTO applications (id, job_id, status, last_updated, notes)
                    VALUES (?, ?, 'discovered', ?, '')
                """, (job_id, job_id, now_str))
            except Exception as db_err:
                logger.warning(f"Application already tracked for job {job_id}: {db_err}")
                
            # Format job dict for notification
            job_dict = dict(job_row)
            job_dict["similarity"] = max_sim
            
            # Run structured LLM job description analysis
            logger.info(f"Analyzing job description with LLM for matching position: {title} at {company}...")
            analysis = analyze_job_description(job_dict)
            
            # Send push alert to Slack
            sent = send_job_match_alert(job_dict, matches, analysis=analysis)
            if sent:
                alerts_sent += 1
                
            # Dispatch recruiter/HR contact discovery for cold outreach
            try:
                logger.info(f"Resolving corporate domain for cold outreach discovery: {company}...")
                domain = resolve_company_domain(company, url, desc)
                if domain:
                    logger.info(f"Corporate domain resolved: {domain}. Commencing HR contact discovery...")
                    contacts = discover_and_verify_emails(company, domain)
                    if contacts:
                        logger.success(f"Discovered {len(contacts)} verified recruiter contacts for {company}!")
                        send_hr_contact_alert(company, domain, contacts)
                    else:
                        logger.info(f"No active recruiter contacts verified for {company} ({domain}).")
                else:
                    logger.warning(f"Could not resolve domain for company: {company}. Skipping HR discovery.")
            except Exception as hr_err:
                logger.error(f"Error during recruiter contact discovery for {company}: {hr_err}")
                
        conn.commit()
    finally:
        conn.close()
        
    logger.info(f"Matchmaking cycle completed. Dispatched {alerts_sent} Slack match alerts.")
    return alerts_sent

def run_pipeline():
    """Run the complete pipeline: ingestion, indexing, matching."""
    logger.info("--- Starting Orchestration Pipeline Run ---")
    try:
        results = run_ingestion_cycle()
        alerts_sent = run_matchmaking_cycle()
        logger.success("--- Orchestration Pipeline Run Finished Successfully ---")
        
        # Dead-man's switch heartbeat Slack ping
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        payload = {
            "text": "🟢 *Career Agent Heartbeat*: Orchestration pipeline run succeeded.",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"🟢 *Career Agent Heartbeat*\n"
                            f"Orchestration pipeline run completed successfully.\n\n"
                            f"• *Jobs Ingested*: {results.get('inserted', 0)} new, {results.get('updated', 0)} updated\n"
                            f"• *Slack Alerts Sent*: {alerts_sent}"
                        )
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Timestamp: `{now_str}`"
                        }
                    ]
                }
            ]
        }
        send_slack_message(payload)
        
    except Exception as e:
        logger.exception(f"Pipeline execution encountered an error: {e}")
        try:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            payload = {
                "text": "🔴 *Career Agent Alert*: Orchestration pipeline run failed.",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"🔴 *Career Agent Failure Alert*\n"
                                f"Orchestration pipeline run failed with error: `{str(e)}`"
                            )
                        }
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"Timestamp: `{now_str}`"
                            }
                        ]
                    }
                ]
            }
            send_slack_message(payload)
        except Exception as slack_err:
            logger.error(f"Failed to send failure Slack notification: {slack_err}")

def main():
    logger.info("Starting Career Agent Daemon...")
    init_db()
    
    # Run once immediately on start
    run_pipeline()
    
    # Configure scheduler
    scheduler = BlockingScheduler()
    # Schedule to run every 8 hours
    scheduler.add_job(run_pipeline, 'interval', hours=8, id='orchestration_pipeline')
    
    logger.info("Daemon scheduled. Blocking loop active (runs every 8 hours). Press Ctrl+C to exit.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Daemon shutdown requested. Goodbye!")

if __name__ == "__main__":
    main()
