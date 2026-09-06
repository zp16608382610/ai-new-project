# Decision Records

> 记录当前已确定的架构决策。新决策追加编号,不改写历史条目;如需推翻,新增一条「Supersedes」记录。

| ID | 主题 | 状态 | 日期 |
| --- | --- | --- | --- |
| 001 | Static knowledge vs dynamic data | Accepted | 2026-09-06 |
| 002 | LangGraph | Accepted | 2026-09-06 |
| 003 | MCP | Accepted | 2026-09-06 |
| 004 | Risk Control | Accepted | 2026-09-06 |
| 005 | Human-in-the-loop | Accepted | 2026-09-06 |
| 006 | Knowledge versioning | Accepted | 2026-09-06 |
| 007 | Knowledge chunking strategy | Accepted | 2026-09-06 |
| 008 | Ingestion idempotency | Accepted | 2026-09-06 |
| 009 | Embedding abstraction | Accepted | 2026-09-06 |
| 010 | Hybrid dense+sparse retrieval | Accepted | 2026-09-06 |
| 011 | BM25 implementation (stdlib) | Accepted | 2026-09-06 |
| 012 | RRF fusion | Accepted | 2026-09-06 |
| 013 | pgvector integration strategy | Accepted | 2026-09-06 |
| 014 | Retrieval metadata/lifecycle filtering | Accepted | 2026-09-06 |
| 015 | Retrieval vs reranking separation | Accepted | 2026-09-06 |
| 016 | Context assembly independence | Accepted | 2026-09-06 |
| 017 | Token budget owned by context layer | Accepted | 2026-09-06 |
| 018 | Deterministic reranker (architecture/test only) | Accepted | 2026-09-06 |

## Decision 001 — Static knowledge vs dynamic data

**Decision:**
Static knowledge uses RAG.
Dynamic business data uses Tools.

**Reason:**
Business data changes frequently and should come from authoritative systems.

## Decision 002 — LangGraph

**Decision:**
LangGraph is the orchestration/runtime layer.

**Reason:**
The project contains stateful multi-step workflows, branching, retries, interruption and resume.

补充约束:业务逻辑不应重度依赖 LangGraph(编排层可替换,见 ARCHITECTURE §10)。

## Decision 003 — MCP

**Decision:**
MCP is used for standardized tool exposure.

**Reason:**
Decouple Agent orchestration from business tool services.

补充约束:MCP 不负责 Agent decision logic(决策在 Orchestrator 层)。

## Decision 004 — Risk Control

**Decision:**
High-risk operations cannot directly execute without risk evaluation.

**Reason:**
退款/取消等操作影响资金与履约,必须先经风控评估;等级定义见 PRD §7 与 ARCHITECTURE §6。

## Decision 005 — Human-in-the-loop

**Decision:**
High-risk or anomalous operations may interrupt the workflow and require human approval.

**Reason:**
对 CRITICAL 或风控命中/异常操作,以 Interrupt → Approval → Resume 流程转人工审批,全程留痕。

## Decision 006 — Knowledge versioning

**Decision:**
Knowledge documents are versioned and immutable: the natural key is (category, title, version); old versions are never overwritten.

**Reason:**
Enterprise knowledge bases change over time; retrieval must distinguish active vs archived versions and effective dates. Policy evolution happens by creating a new version, not by mutating history.

补充约束:同版本、不同内容在同一自然键下被拒绝(`KnowledgeIngestionError`),引导走「新建版本」路径。

## Decision 007 — Knowledge chunking strategy

**Decision:**
Phase 3A uses a deterministic, section-aware chunker (normalize → split by `##`/`###` section boundaries → split long sections by paragraph then characters), implemented with the Python standard library only.

**Reason:**
Prefer semantic/section-aware chunking over blind fixed-N character splits; traceable chunks (document_id + section) are the goal, not maximum sophistication. Keeping it dependency-free makes it easy to replace later without changing the ingestion interface.

## Decision 008 — Ingestion idempotency

**Decision:**
Idempotency is based on a document checksum: sha256 of the normalized UTF-8 text is stored on `knowledge_documents.checksum`; re-ingesting identical content is a no-op and never duplicates chunks.

**Reason:**
Deterministic seed knowledge must be safely repeatable (development, CI, re-seed) without uncontrolled duplicate rows.

## Decision 009 — Embedding abstraction

**Decision:**
Embedding access goes through an `EmbeddingProvider` interface (`embed(texts) -> list[list[float]]`). Phase 3A ships only a deterministic local stub (`DeterministicEmbeddingProvider`) used for interface verification; no vendor or vector framework is bound, and no real embedding is stored.

**Reason:**
Do not couple the knowledge model or ingestion pipeline to one embedding vendor before Phase 3B; keep the swap point clean and tests offline/deterministic. pgvector / real vectors belong to Phase 3B.

## Decision 010 — Hybrid dense+sparse retrieval

**Decision:**
Retrieval runs dense (vector similarity) and sparse (BM25) in parallel and fuses the ranked lists; the result exposes `retrieval_methods` per candidate so callers can audit which method(s) produced the hit.

**Reason:**
Dense captures paraphrase-overlap; sparse guarantees exact-term recall for policy keywords. Neither alone is sufficient for a mixed FAQ/policy corpus; fusing keeps the boundary testable per method and hybrid.

## Decision 011 — BM25 implementation / dependency

**Decision:**
BM25 is implemented in pure Python (standard library only): Okapi BM25 over a deterministic CJK tokenizer (sliding character bigrams with function-word bigram filtering and a minimum-shared-term gate).

**Reason:**
No dependency is justified for ~60 lines of BM25; LangChain (or any ranking library) solely for BM25 would violate dependency discipline. Char-bigram tokenization needs no external segmenter and is deterministic for the fixed Chinese corpus.

## Decision 012 — RRF fusion

**Decision:**
Fusion uses Reciprocal Rank Fusion (RRF, k=60): score(chunk) = Σ 1/(k + rank_in_method).

**Reason:**
Dense cosine and BM25 scores live on different scales; naive summation requires calibration. RRF is rank-based, needs no score normalization, is deterministic, and boosts chunks found by both retrievers.

## Decision 013 — pgvector integration strategy

**Decision:**
PostgreSQL + pgvector is the production vector path; in this environment (no Docker/PostgreSQL) the vector database boundary stays clean and unverified:
- `DenseRetriever` is the swap interface for a future pgvector-backed implementation.
- Local dev/tests use the deterministic `EmbeddingProvider` path; no vector column/migration is added and no unrelated vector DB is substituted.
- Real embedding model/provider selection and `knowledge_chunks` embedding column land together with pgvector in a later phase, and pgvector must not be claimed working until actually integration-tested.

**Reason:**
Avoid fabricating integration results; keep retrieval independently testable now and swappable later.

## Decision 014 — Retrieval metadata / lifecycle filtering

**Decision:**
Filtering uses a simple typed `RetrievalFilter(status/category/language)` — no filtering DSL. The repository layer enforces `status == ACTIVE` by default (Phase 3A lifecycle rule), so ARCHIVED/DRAFT documents cannot silently compete with ACTIVE versions; explicit `status=None` is an internal/test-only override.

**Reason:**
Simple, typed, and testable; lifecycle correctness (active-version-only retrieval) is enforced at the single data-access boundary instead of being re-implemented per retriever.

## Decision 015 — Retrieval vs reranking separation

**Decision:**
Retrieval (candidate generation) and reranking are separate layers with separate interfaces: retrieval produces typed, traceable candidates; reranking is a pluggable pass that re-orders those candidates for the final Top-N.

**Reason:**
Retrieval optimizes recall with cheap/fast methods (dense + BM25 + RRF); reranking optimizes precision for what finally matters. Splitting the layers keeps each independently testable and lets a real Cross-Encoder / LLM-based reranker / provider reranking API replace the local implementation without touching retrieval or context assembly.

## Decision 016 — Context assembly independence

**Decision:**
A dedicated context assembly layer decides what finally reaches the LLM: dedupe, ACTIVE-version preference, ordering, chunk-count cap, token budget, and per-item citation/traceability.

**Reason:**
「Retrieval 找候选,Reranker 负责相关性排序,Context Assembly 负责决定最终给模型什么」。Keeping assembly independent prevents mixing search logic with prompt construction and grounds any future generation strictly in the assembled context (Grounding Boundary).

## Decision 017 — Token budget owned by the context layer

**Decision:**
Token budget lives in the context layer as a typed `ContextBudget` (max_tokens / reserve_tokens). Without a tokenizer dependency, `estimate_tokens()` is a deterministic approximation (documented as approximation, not a real tokenizer); budget numbers never scatter across callers.

**Reason:**
Centralizing the budget prevents over-budget contexts, reserves tokens for future system/user prompts and the answer, and gives one swap point for a tokenizer-backed implementation later.

## Decision 018 — Deterministic reranker is architecture/test implementation only

**Decision:**
Phase 3C ships only `DeterministicReranker`, a deterministic, standard-library, lexical scoring implementation. It is explicitly NOT a semantic reranker and must not be described as one.

**Reason:**
The phase focuses on architecture and testability, not model quality. Keeping the `Reranker` interface as the swap point avoids adding heavyweight ML dependencies (transformers / torch / sentence-transformers) without a proven need; a semantic reranker can be introduced behind the same interface in a later phase.
