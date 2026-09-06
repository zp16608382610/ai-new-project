"""Retrieval layer.

Phase 3B: Query -> Query Processing -> Dense + Sparse -> RRF Fusion ->
Ranked Candidates (app/retrieval: text / bm25 / dense / fusion / service).

Phase 3C: Candidate Set -> Reranking -> Top N -> Context Assembly ->
Final Context (app/retrieval: rerank / context / pipeline).

The layer is independent from FastAPI and from the future Agent layer. It
never generates answers, executes business operations, or fabricates content.
"""