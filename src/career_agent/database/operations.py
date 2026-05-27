"""
DB write/read helpers used by the ingestion pipeline.
All functions accept an open sqlite3.Connection — no pooling here,
kept simple for Day 1. Connection lifecycle is the caller's responsibility.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Optional
from loguru import logger

from .schema import get_connection
from ..ingestion.normalizer import NormalizedJob
from ..embeddings.generator import embedder, vec_to_bytes


# ── Upsert helpers ────────────────────────────────────────────────

UPSERT_JOB_SQL = """
INSERT INTO jobs (
    id, source, source_id, title, company,
    location, url, description, tags,
    is_remote, posted_at, fetched_at, is_active
) VALUES (
    :id, :source, :source_id, :title, :company,
    :location, :url, :description, :tags,
    :is_remote, :posted_at, :fetched_at, :is_active
)
ON CONFLICT(source, source_id) DO UPDATE SET
    title       = excluded.title,
    company     = excluded.company,
    location    = excluded.location,
    url         = excluded.url,
    description = excluded.description,
    tags        = excluded.tags,
    is_remote   = excluded.is_remote,
    fetched_at  = excluded.fetched_at,
    is_active   = excluded.is_active
"""


def upsert_jobs(jobs: list[NormalizedJob]) -> dict[str, int]:
    """Bulk upsert normalized jobs. Returns counts."""
    if not jobs:
        return {"inserted": 0, "updated": 0}

    conn = get_connection()
    inserted = updated = 0
    try:
        for job in jobs:
            row = job.__dict__
            cur = conn.execute(UPSERT_JOB_SQL, row)
            if cur.lastrowid:
                inserted += 1
            else:
                updated += 1
        conn.commit()
    finally:
        conn.close()

    logger.info(f"DB upsert: {inserted} inserted, {updated} updated")
    return {"inserted": inserted, "updated": updated}


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """Split text into semantic chunks of fixed size with an overlap."""
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end].strip())
        start += chunk_size - overlap
    return chunks


def embed_and_store_jobs(job_ids: Optional[list[str]] = None) -> int:
    """
    Generate chunked embeddings for jobs that don't have one yet,
    then insert into vec_jobs virtual table.
    If job_ids is None, process all un-embedded active jobs.
    """
    conn = get_connection()
    emb = embedder()
    stored = 0

    try:
        # Find jobs not yet in vec_jobs
        if job_ids:
            placeholders = ",".join("?" * len(job_ids))
            rows = conn.execute(
                f"SELECT id, title, company, description FROM jobs "
                f"WHERE id IN ({placeholders}) AND is_active=1",
                job_ids,
            ).fetchall()
        else:
            existing = {
                r[0] for r in conn.execute("SELECT job_id FROM vec_jobs").fetchall()
            }
            rows = conn.execute(
                "SELECT id, title, company, description FROM jobs WHERE is_active=1"
            ).fetchall()
            rows = [r for r in rows if r["id"] not in existing]

        if not rows:
            logger.info("No new jobs to embed")
            return 0

        # Build chunked text for each job
        all_chunks = []
        for r in rows:
            title = r["title"]
            company = r["company"]
            desc = r["description"] or ""
            
            # Chunk 1: Header (Title + Company)
            chunks = [f"{title} at {company}"]
            # Description chunks
            desc_chunks = chunk_text(desc, chunk_size=500, overlap=100)
            chunks.extend(desc_chunks)
            
            for chunk in chunks:
                all_chunks.append((r["id"], chunk))

        if not all_chunks:
            logger.info("No chunks to embed")
            return 0

        logger.info(f"Embedding {len(all_chunks)} semantic chunks for {len(rows)} jobs...")

        # Batch embed all chunks
        chunk_texts = [item[1] for item in all_chunks]
        vectors = emb.embed(chunk_texts)

        for (job_id, chunk_text), vec in zip(all_chunks, vectors):
            vec_bytes = vec_to_bytes(vec)
            conn.execute(
                "INSERT INTO vec_jobs(job_id, embedding) VALUES (?, ?)",
                (job_id, vec_bytes),
            )
            stored += 1

        conn.commit()
        logger.success(f"Stored {stored} job chunk embeddings")
    finally:
        conn.close()

    return stored


def search_similar_jobs(query: str, top_k: int = 10) -> list[dict]:
    """
    Vector similarity search: given a text query, return top-K matching jobs.
    Returns list of job dicts with added 'similarity' field.
    """
    conn = get_connection()
    emb = embedder()

    try:
        query_vec = emb.embed_one(query)
        query_bytes = vec_to_bytes(query_vec)

        # sqlite-vec KNN query
        results = conn.execute("""
            SELECT
                j.id, j.title, j.company, j.location,
                j.url, j.tags, j.is_remote, j.posted_at,
                v.distance
            FROM vec_jobs v
            JOIN jobs j ON v.job_id = j.id
            WHERE j.is_active = 1
              AND v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance
        """, (query_bytes, top_k)).fetchall()

        jobs = []
        for r in results:
            d = dict(r)
            # sqlite-vec returns L2 distance; convert to cosine-like score
            # For normalized vectors: similarity = 1 - (distance² / 2)
            dist = d.pop("distance")
            d["similarity"] = round(1.0 - (dist ** 2) / 2, 4)
            d["tags"] = json.loads(d.get("tags", "[]"))
            jobs.append(d)

        return jobs
    finally:
        conn.close()


def store_cv_chunks(chunks: list[dict]) -> int:
    """
    Store CV chunks in the database and index them for similarity search.
    chunks parameter is a list of dicts: [{'content': str, 'category': str, 'tags': list[str]}]
    """
    import hashlib
    conn = get_connection()
    emb = embedder()
    stored = 0
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        for c in chunks:
            content = c["content"].strip()
            category = c["category"].strip()
            tags = c.get("tags", [])

            # Generate SHA-1 ID based on content
            hasher = hashlib.sha1()
            hasher.update(content.encode("utf-8"))
            chunk_id = hasher.hexdigest()

            # Insert into relational table
            conn.execute("""
                INSERT OR REPLACE INTO cv_chunks (id, content, category, tags, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (chunk_id, content, category, json.dumps(tags), created_at))

            # Compute embedding and insert into vector table
            vec = emb.embed_one(content)
            vec_bytes = vec_to_bytes(vec)

            conn.execute("""
                INSERT OR REPLACE INTO vec_cv (chunk_id, embedding)
                VALUES (?, ?)
            """, (chunk_id, vec_bytes))

            stored += 1

        conn.commit()
    finally:
        conn.close()

    logger.success(f"Stored and indexed {stored} CV chunks")
    return stored


def search_similar_cv_chunks(query: str, category: Optional[str] = None, top_k: int = 5) -> list[dict]:
    """
    Vector similarity search for CV chunks based on a query string (e.g. a job description).
    Optionally filter by category.
    """
    conn = get_connection()
    emb = embedder()

    try:
        query_vec = emb.embed_one(query)
        query_bytes = vec_to_bytes(query_vec)

        # Base query joining vec_cv with cv_chunks
        sql = """
            SELECT
                c.id, c.content, c.category, c.tags,
                v.distance
            FROM vec_cv v
            JOIN cv_chunks c ON v.chunk_id = c.id
        """
        params = [query_bytes]

        if category:
            sql += " WHERE c.category = ? AND v.embedding MATCH ?"
            params = [category, query_bytes]
        else:
            sql += " WHERE v.embedding MATCH ?"

        sql += " AND k = ? ORDER BY v.distance"
        params.append(top_k)

        results = conn.execute(sql, params).fetchall()

        chunks = []
        for r in results:
            d = dict(r)
            dist = d.pop("distance")
            d["similarity"] = round(1.0 - (dist ** 2) / 2, 4)
            d["tags"] = json.loads(d.get("tags", "[]"))
            chunks.append(d)

        return chunks
    finally:
        conn.close()

