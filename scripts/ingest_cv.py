#!/usr/bin/env python3
import re
import sys
import json
from pathlib import Path

# Add src to system path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loguru import logger
from career_agent.database.schema import init as init_db
from career_agent.database.operations import store_cv_chunks

def parse_master_cv(file_path: str) -> list[dict]:
    path = Path(file_path)
    if not path.exists():
        logger.error(f"Master CV not found at: {file_path}")
        return []
        
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
        
    # Split content by category headers: # [category:category_name]
    pattern = r"^#\s+\[category:(\w+)\]"
    
    sections = re.split(pattern, content, flags=re.MULTILINE)
    
    chunks = []
    
    # Iterate in pairs of (category_name, section_content)
    for i in range(1, len(sections), 2):
        category = sections[i]
        sec_content = sections[i+1]
        
        # Split section content into lines
        lines = sec_content.strip().split("\n")
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # If line is a list item: starts with '- ' or '* '
            is_bullet = False
            bullet_text = line
            if line.startswith("- "):
                is_bullet = True
                bullet_text = line[2:]
            elif line.startswith("* "):
                is_bullet = True
                bullet_text = line[2:]
                
            # Extract tags: look for `[tags: "tag1", "tag2"]`
            tags = []
            tag_match = re.search(r"\[tags:\s*([^\]]+)\]", bullet_text)
            if tag_match:
                tag_str = tag_match.group(1)
                # Split by comma and strip quotes
                tags = [t.strip().strip('"').strip("'") for t in tag_str.split(",")]
                # Remove tag bracket from bullet_text
                bullet_text = re.sub(r"\s*\[tags:\s*[^\]]+\]", "", bullet_text).strip()
                
            chunks.append({
                "content": bullet_text,
                "category": category,
                "tags": tags
            })
            
    return chunks

def main():
    cv_path = "master_cv.md"
    logger.info(f"Parsing Master CV: {cv_path}")
    chunks = parse_master_cv(cv_path)
    
    if not chunks:
        logger.error("No chunks parsed from Master CV.")
        return
        
    logger.info(f"Parsed {len(chunks)} accomplishments from Master CV.")
    
    # Always ensure DB is initialized
    init_db()
    
    # Store in database
    store_cv_chunks(chunks)
    logger.success("Master CV Ingestion Completed successfully!")

if __name__ == "__main__":
    main()
