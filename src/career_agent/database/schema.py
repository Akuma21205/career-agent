"""
Database schema + sqlite-vec virtual table initialisation.
Run once: python -m career_agent.database.schema
"""
import sqlite3
from loguru import logger
import sqlite_vec
from career_agent.config import settings


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Jobs ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,          -- SHA1(source + source_id)
    source      TEXT NOT NULL,             -- simplifyjobs | linkedin | naukri | ...
    source_id   TEXT NOT NULL,
    title       TEXT NOT NULL,
    company     TEXT NOT NULL,
    location    TEXT,
    url         TEXT,
    description TEXT,
    tags        TEXT DEFAULT '[]',         -- JSON array of strings
    is_remote   INTEGER DEFAULT 0,
    posted_at   TEXT,                      -- ISO-8601
    fetched_at  TEXT NOT NULL,
    is_active   INTEGER DEFAULT 1,
    match_score REAL,                      -- filled after vector search
    is_dismissed INTEGER DEFAULT 0,
    is_saved     INTEGER DEFAULT 0,
    UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_source    ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_active    ON jobs(is_active);
CREATE INDEX IF NOT EXISTS idx_jobs_company   ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_fetched   ON jobs(fetched_at DESC);

-- ── Applications (FSM) ───────────────────────────────────────────
-- States: discovered → interested → applied → screening
--         → interviewing → offer | rejected | withdrawn
CREATE TABLE IF NOT EXISTS applications (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'discovered'
                    CHECK(status IN (
                        'discovered','interested','applied',
                        'screening','interviewing','offer',
                        'rejected','withdrawn'
                    )),
    applied_at   TEXT,
    last_updated TEXT NOT NULL,
    notes        TEXT DEFAULT '',
    cv_path      TEXT,                     -- path to tailored PDF
    UNIQUE(job_id)
);

CREATE INDEX IF NOT EXISTS idx_apps_status ON applications(status);

-- ── Master CV chunks ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cv_chunks (
    id         TEXT PRIMARY KEY,           -- SHA1(content)
    content    TEXT NOT NULL,
    category   TEXT NOT NULL               -- experience | project | skill | education | summary
                    CHECK(category IN ('experience','project','skill','education','summary')),
    tags       TEXT DEFAULT '[]',          -- JSON: keywords for keyword pre-filter
    created_at TEXT NOT NULL
);

-- ── Interview prep cache ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS interview_preps (
    id         TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    content    TEXT NOT NULL,              -- full generated markdown guide
    created_at TEXT NOT NULL,
    UNIQUE(job_id)
);

-- ── Ingestion audit log ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingestion_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    inserted    INTEGER DEFAULT 0,
    updated     INTEGER DEFAULT 0,
    skipped     INTEGER DEFAULT 0,
    error       TEXT
);
"""


def get_connection() -> sqlite3.Connection:
    """Return a WAL-enabled, sqlite-vec-loaded connection."""
    conn = sqlite3.connect(
        settings.db_path_abs,
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def create_vector_tables(conn: sqlite3.Connection, dim: int) -> None:
    """Create sqlite-vec virtual tables. Dimension is fixed at creation time."""
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_jobs USING vec0(
            job_id    TEXT,
            embedding float[{dim}]
        )
    """)
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_cv USING vec0(
            chunk_id  TEXT,
            embedding float[{dim}]
        )
    """)
    conn.commit()
    logger.info(f"Vector tables ready (dim={dim})")


def init() -> None:
    """Initialise all tables. Safe to run multiple times (idempotent)."""
    logger.info(f"Initialising database at {settings.db_path_abs}")
    conn = get_connection()
    try:
        conn.executescript(SCHEMA_SQL)
        
        # Migrations for existing databases
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN is_dismissed INTEGER DEFAULT 0")
        except Exception:
            pass  # Already exists
            
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN is_saved INTEGER DEFAULT 0")
        except Exception:
            pass  # Already exists
            
        create_vector_tables(conn, dim=settings.embedding_dim)
        conn.commit()
        logger.success("Database initialised successfully")
    finally:
        conn.close()


if __name__ == "__main__":
    init()
