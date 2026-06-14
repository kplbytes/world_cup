#!/usr/bin/env python3
"""Lock pre-match predictions (CLI wrapper around workflow service)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import create_database
from app.workflows.service import run_lock_workflow as _run_lock


def main():
    parser = argparse.ArgumentParser(description="Lock pre-match predictions (24h window)")
    parser.add_argument("--window-hours", type=int, default=24, help="Window in hours before kickoff")
    args = parser.parse_args()

    create_database()
    run_id = _run_lock(
        window_hours=args.window_hours,
        trigger_source="script",
    )
    print(f"Workflow run_id={run_id}")


if __name__ == "__main__":
    main()
