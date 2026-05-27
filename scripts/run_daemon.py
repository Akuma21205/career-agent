#!/usr/bin/env python3
import sys
from pathlib import Path

# Add src/ to import path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from career_agent.execution.daemon import main

if __name__ == "__main__":
    main()
