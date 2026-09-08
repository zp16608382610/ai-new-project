"""Evaluation package (Phase 7C).

A deliberately lightweight, fixed dataset evaluation that runs the REAL agent
workflow (the same path as /demo/chat) against an isolated seeded SQLite
database and reports seven head-line metrics plus per-case expected/actual.

Scope guardrails (interview demo, not a production eval platform):
    - fixed local dataset, no training / model routing / A/B testing;
    - metrics are categorical pass/fail/N/A checks, no fabricated scores;
    - the runner never touches the live demo database.
"""
