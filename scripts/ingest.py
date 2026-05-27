#!/usr/bin/env python3
"""
Day 1 ingestion runner.
Usage:
    python scripts/ingest.py                 # run all sources
    python scripts/ingest.py --source simplify
    python scripts/ingest.py --embed-only    # re-embed without fetching
"""
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path

# make src/ importable when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.progress import track

from career_agent.database.schema import init as init_db
from career_agent.database.operations import upsert_jobs, embed_and_store_jobs
from career_agent.ingestion.simplifyjobs import SimplifyJobsScraper

console = Console()


def run_simplifyjobs() -> dict:
    logger.info("Starting SimplifyJobs ingestion")
    scraper = SimplifyJobsScraper()
    jobs = list(track(scraper.scrape(), description="Fetching SimplifyJobs..."))
    return upsert_jobs(jobs)


SOURCE_MAP = {
    "simplify": run_simplifyjobs,
    # Day 2+: "linkedin": run_linkedin, "naukri": run_naukri, ...
}


def main():
    parser = argparse.ArgumentParser(description="Career Agent — Ingestion Runner")
    parser.add_argument("--source", choices=list(SOURCE_MAP.keys()), default=None,
                        help="Run a specific source only")
    parser.add_argument("--embed-only", action="store_true",
                        help="Skip fetching; only run embedding step")
    parser.add_argument("--no-embed", action="store_true",
                        help="Skip embedding after ingestion")
    args = parser.parse_args()

    console.rule("[bold]Career Agent — Ingestion[/bold]")

    # Always ensure DB is initialised
    init_db()

    total_inserted = total_updated = 0

    if not args.embed_only:
        sources = [args.source] if args.source else list(SOURCE_MAP.keys())
        for src in sources:
            console.print(f"\n[cyan]Running source:[/cyan] {src}")
            result = SOURCE_MAP[src]()
            total_inserted += result.get("inserted", 0)
            total_updated += result.get("updated", 0)

        # Summary table
        table = Table(title="Ingestion Summary", show_header=True)
        table.add_column("Metric", style="dim")
        table.add_column("Count", style="bold green")
        table.add_row("New jobs inserted", str(total_inserted))
        table.add_row("Existing jobs updated", str(total_updated))
        console.print(table)

    if not args.no_embed:
        console.print("\n[cyan]Generating embeddings...[/cyan]")
        embedded = embed_and_store_jobs()
        console.print(f"[green]Embedded {embedded} new job vectors[/green]")

    console.rule("[bold green]Done[/bold green]")


if __name__ == "__main__":
    main()