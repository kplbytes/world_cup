# World Cup Prediction System Plan

## Goal
Build a locally runnable 2026 World Cup prediction dashboard covering groups A-L, every match, qualification probabilities, data provenance, and automatic post-match updates while the local program is running.

## Phases
- [complete] Validate requirements, architecture, and free data sources
- [complete] Write and approve the product/technical design
- [complete] Create an implementation plan
- [pending] Implement the data model, ingestion, prediction, simulation, API, and dashboard with tests
- [pending] Verify local runtime, data refresh, post-match recomputation, and rendered UI

## Current Decisions
- Support a pure local application; Docker and cloud deployment are out of scope.
- Use FastAPI, SQLite, and React/Vite, launched through local scripts.
- Use replaceable data-source adapters with cached snapshots and provenance.
- Treat official FIFA fixtures/results as the authority for conflict checks.
- Poll for updates while the application is running and expose a manual refresh action.
- Implement using the approved plan at `docs/superpowers/plans/2026-06-13-world-cup-prediction-system.md`.

## Errors Encountered
- Current directory is not a Git repository and contains no project files.
