import sys
import json
from pathlib import Path
from datetime import datetime, timezone

# Add src/ to import path
sys.path.insert(0, str(Path(__file__).resolve().parents[0] / "src"))

from mcp.server.fastmcp import FastMCP
from career_agent.database.operations import search_similar_jobs, search_similar_cv_chunks, get_connection
from career_agent.execution.daemon import run_pipeline

# Initialize FastMCP Server
mcp = FastMCP("Career Agent")

@mcp.tool()
def search_jobs(query: str, top_k: int = 10) -> str:
    """
    Search similar job listings in the database by semantic text (e.g. skills, titles, or tags).
    Returns formatted results containing company name, title, location, application URL, and match similarity.
    """
    try:
        results = search_similar_jobs(query, top_k=top_k)
        if not results:
            return "No matching jobs found in the database."
            
        formatted = []
        for idx, j in enumerate(results, 1):
            formatted.append(
                f"{idx}. **{j['company']}** — *{j['title']}* (Similarity: `{j['similarity']:.4f}`)\n"
                f"   - Job ID: `{j['id']}`\n"
                f"   - Location: {j['location'] or 'Not Specified'}\n"
                f"   - Remote: {'Yes 🏠' if j['is_remote'] else 'No 🏢'}\n"
                f"   - URL: {j['url']}\n"
                f"   - Tags: {j['tags']}"
            )
        return "\n\n".join(formatted)
    except Exception as e:
        return f"Error executing job search: {e}"

@mcp.tool()
def search_accomplishments(query: str, top_k: int = 5) -> str:
    """
    Query accomplishments from your Master CV matching a target text (e.g. a job descriptor or project requirement).
    Returns the categories, accomplishment text, tags, and similarity scores.
    """
    try:
        results = search_similar_cv_chunks(query, top_k=top_k)
        if not results:
            return "No matching accomplishments found in your Master CV."
            
        formatted = []
        for idx, c in enumerate(results, 1):
            formatted.append(
                f"{idx}. *[{c['category'].upper()}]* {c['content']} (Similarity: `{c['similarity']:.4f}`)\n"
                f"   - Tags: {c['tags']}"
            )
        return "\n\n".join(formatted)
    except Exception as e:
        return f"Error executing accomplishments search: {e}"

@mcp.tool()
def update_application_status(job_id: str, status: str, notes: str = "") -> str:
    """
    Update the tracking state of a job application.
    Valid statuses: discovered, interested, applied, screening, interviewing, offer, rejected, withdrawn.
    Notes are optional and will be appended to previous tracking logs.
    """
    valid_statuses = {
        "discovered", "interested", "applied", 
        "screening", "interviewing", "offer", 
        "rejected", "withdrawn"
    }
    status = status.lower().strip()
    if status not in valid_statuses:
        return f"Invalid status: '{status}'. Valid options are: {', '.join(valid_statuses)}"
        
    conn = get_connection()
    now_str = datetime.now(timezone.utc).isoformat()
    
    try:
        # Check if job exists
        job = conn.execute("SELECT title, company FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            return f"Job ID '{job_id}' not found in the database. Run run_ingestion_and_matching first."
            
        # Check if application tracking row exists
        app = conn.execute("SELECT status, notes FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        
        if app:
            # Update existing tracker
            if notes:
                new_notes = (app["notes"] or "") + f"\n[{now_str}] {notes}"
                conn.execute("""
                    UPDATE applications 
                    SET status = ?, last_updated = ?, notes = ? 
                    WHERE job_id = ?
                """, (status, now_str, new_notes, job_id))
            else:
                conn.execute("""
                    UPDATE applications 
                    SET status = ?, last_updated = ? 
                    WHERE job_id = ?
                """, (status, now_str, job_id))
            action = "Updated"
        else:
            # Create new tracker
            formatted_notes = f"[{now_str}] {notes}" if notes else ""
            conn.execute("""
                INSERT INTO applications (id, job_id, status, last_updated, notes)
                VALUES (?, ?, ?, ?, ?)
            """, (job_id, job_id, status, now_str, formatted_notes))
            action = "Created"
            
        conn.commit()
        return f"Success! {action} application tracking for '{job['title']}' at '{job['company']}' to status: '{status}'."
    except Exception as e:
        return f"Error updating application status: {e}"
    finally:
        conn.close()

@mcp.tool()
def run_ingestion_and_matching() -> str:
    """
    Manually trigger the full pipeline run: fetches new internships,
    creates vector embeddings, computes similarity metrics, and dispatches Slack match alerts.
    """
    try:
        run_pipeline()
        return "Successfully executed full ingestion and matchmaking cycle."
    except Exception as e:
        return f"Error executing pipeline cycle: {e}"

@mcp.tool()
def find_recruiter_contacts(company_name: str, domain: str) -> str:
    """
    Search the company website/careers page to discover recruiter, HR, and hiring manager names/emails.
    Guesses email variations and runs zero-cost SMTP mailbox handshakes to verify which mailboxes are active.
    company_name: Name of the target company (e.g. "Stripe").
    domain: Corporate domain name (e.g. "stripe.com").
    """
    try:
        from career_agent.llm.contact import discover_and_verify_emails
        results = discover_and_verify_emails(company_name, domain)
        if not results:
            return f"No active/verified contact channels found for {company_name} ({domain})."
            
        formatted = []
        for idx, item in enumerate(results, 1):
            formatted.append(
                f"{idx}. **{item['name']}** — *{item['role']}*\n"
                f"   - Verified Email: `{item['email']}`"
            )
        return f"Verified Recruiter Contacts for **{company_name}**:\n\n" + "\n\n".join(formatted)
    except Exception as e:
        return f"Error executing contact discovery: {e}"

if __name__ == "__main__":
    mcp.run()

