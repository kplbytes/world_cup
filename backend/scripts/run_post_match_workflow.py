#!/usr/bin/env python3
"""Post-match workflow (CLI wrapper around workflow service)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import create_database
from app.workflows.service import run_post_match_workflow as _run_post_match


def main():
    parser = argparse.ArgumentParser(description="Post-match workflow")
    parser.add_argument("--since-hours", type=int, default=24, help="Look back hours for finished matches")
    args = parser.parse_args()

    create_database()
    run_id = _run_post_match(
        since_hours=args.since_hours,
        trigger_source="script",
    )
    print(f"Workflow run_id={run_id}")


if __name__ == "__main__":
    main()
