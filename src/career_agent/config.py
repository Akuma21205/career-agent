from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    db_path: str = "jobs.db"
    embedding_dim: int = 384  # Default for all-MiniLM-L6-v2 and hash fallback
    embedding_provider: str = "local"  # "local", "gemini", or "hash"
    embedding_model: str = "all-MiniLM-L6-v2"
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None
    llm_model: str = "deepseek/deepseek-v4-flash:free"
    fallback_llm_model: str = "models/gemini-2.5-flash"
    slack_webhook_url: str | None = None
    linkedin_slack_webhook_url: str | None = None
    greenhouse_slack_webhook_url: str | None = None
    glassdoor_slack_webhook_url: str | None = None
    indeed_slack_webhook_url: str | None = None
    internshala_slack_webhook_url: str | None = None
    naukri_slack_webhook_url: str | None = None
    prosple_slack_webhook_url: str | None = None
    ycombinator_slack_webhook_url: str | None = None
    gmail_slack_webhook_url: str | None = None
    hr_slack_webhook_url: str | None = None
    match_threshold: float = 0.40
    daily_slack_target: int = 30
    min_backfill_threshold: float = 0.20
    gmail_enabled: bool = True
    gmail_search_query: str = "subject:internship OR subject:intern"
    gmail_lookback_days: int = 3

    # Advanced Matchmaking Settings
    job_intent_profile: str = "Internship in Agentic AI Engineering, LLM Orchestration, RAG, Multi-Agent Systems, Python Backend development, and ML pipelines."
    target_roles: str = "AI Engineer, Machine Learning Engineer, AI Researcher, Data Scientist, LLM Developer, Agentic AI Specialist, Software Engineer Intern"
    tier1_companies: str = "google,meta,apple,microsoft,netflix,amazon,nvidia,openai,anthropic,cohere,scaleai"
    tier2_companies: str = "perplexity,characterai,huggingface,langchain,pinecone,weaviate,groq,togetherai,mistralai,replicate,runpod"
    role_similarity_threshold: float = 0.65


    @property
    def db_path_abs(self) -> str:
        p = Path(self.db_path)
        if p.is_absolute():
            return str(p)
        # Resolve relative to project root (2 levels up from src/career_agent/config.py)
        project_root = Path(__file__).resolve().parents[2]
        return str(project_root / p)

settings = Settings()
