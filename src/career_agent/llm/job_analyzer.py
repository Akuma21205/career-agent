import sqlite3
import json
import re
from career_agent.database.schema import get_connection
from career_agent.llm.client import generate_text
from loguru import logger

def fetch_master_skills() -> list[str]:
    """Retrieve all skills from the Master CV stored in the database."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT content FROM cv_chunks WHERE category = 'skill'").fetchall()
        skills = []
        for r in rows:
            content = r["content"].strip()
            # Strip markdown bullet dashes if present
            if content.startswith("- "):
                content = content[2:]
            elif content.startswith("* "):
                content = content[2:]
            skills.append(content)
        return skills
    except Exception as e:
        logger.error(f"Failed to fetch skills from database: {e}")
        return []
    finally:
        conn.close()

def analyze_job_description(job: dict) -> dict:
    """
    Run LLM analysis on the job description to extract stipend, brief summary,
    overlap with Master CV skills, and success probability.
    """
    title = job.get("title", "Unknown Role")
    company = job.get("company", "Unknown Company")
    description = job.get("description", "")
    
    # Fetch registered Master CV skills
    master_skills = fetch_master_skills()
    master_skills_str = "\n".join(f"- {s}" for s in master_skills)
    
    prompt = f"""
Analyze the following internship/job posting.
Company: {company}
Role: {title}
Job Description:
{description[:4000]}

Here is the candidate's list of registered skills from their Master CV:
{master_skills_str}

Please extract the following structural details and output them in strict JSON format ONLY:
1. **stipend**: Determine if a stipend/salary is mentioned in the description. Be specific (e.g. "💵 Yes, $25/hour" or "💵 Unspecified / None mentioned").
2. **about**: A concise, 2-sentence summary of what this job is about and its core responsibilities.
3. **matched_skills**: Identify which specific skills from the candidate's Master CV are explicitly or implicitly required by this job description. Return them as a JSON list.
4. **missing_skills**: Identify any critical required technical skills mentioned in the job description that the candidate does not have in their registered Master CV list. Return them as a JSON list.
5. **confidence_level**: Assess how well the candidate's skills match the job requirements (output "High", "Medium", or "Low").
6. **success_probability**: Estimate a percentage probability of application success/match strength (integer from 0 to 100).
7. **confidence_reason**: A very brief (1-sentence) explanation for your confidence rating.

Response MUST be a valid JSON object matching this schema:
{{
  "stipend": "string",
  "about": "string",
  "matched_skills": ["string"],
  "missing_skills": ["string"],
  "confidence_level": "string",
  "success_probability": 85,
  "confidence_reason": "string"
}}
"""
    system_instruction = "You are a professional technical hiring assistant. Return valid JSON objects matching the requested schema. Do not output any thinking or markdown block tags other than the JSON itself."
    
    try:
        response = generate_text(prompt, system_instruction=system_instruction)
        # Parse JSON block
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            return data
        else:
            return json.loads(response)
    except Exception as e:
        logger.error(f"Failed to analyze job description via LLM: {e}")
        return {
            "stipend": "💵 Unspecified",
            "about": f"Position as {title} at {company}.",
            "matched_skills": [],
            "missing_skills": [],
            "confidence_level": "Medium",
            "success_probability": 50,
            "confidence_reason": "Failed to run LLM analysis."
        }
