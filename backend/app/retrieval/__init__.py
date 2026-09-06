"""Retrieval layer (Phase 3B): Query -> Dense + Sparse -> RRF Fusion -> Ranked Candidates.

Independent from FastAPI and from the future Agent layer.
Does NOT generate answers (Phase 3C+ owns reranking / context assembly / grounding).
"""