#!/usr/bin/env python3
"""Pre-match prediction workflow (CLI wrapper around workflow service)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import create_database
from app.workflows.service import run_pre_match_workflow as _run_pre_match


def main():
    parser = argparse.ArgumentParser(description="Pre-match prediction workflow")
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--with-ai", action="store_true")
    parser.add_argument("--with-ensemble", action="store_true")
    args = parser.parse_args()

    create_database()
    run_id = _run_pre_match(
        hours=args.hours, limit=args.limit,
        with_ai=args.with_ai, with_ensemble=args.with_ensemble,
        only_missing=True, trigger_source="script",
    )
    print(f"Workflow run_id={run_id}")


if __name__ == "__main__":
    main()
