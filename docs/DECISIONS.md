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
| 019 | Intent and Route separation | Accepted | 2026-09-06 |
| 020 | AgentState as domain state (not framework state) | Accepted | 2026-09-06 |
| 021 | Agent layer has no direct database access | Accepted | 2026-09-06 |
| 022 | Static knowledge vs dynamic data / business actions (agent level) | Accepted | 2026-09-06 |
| 023 | Ambiguous requests are never guessed (CLARIFY) | Accepted | 2026-09-06 |
| 024 | LangGraph is orchestration only; Phase 4A stays framework-agnostic | Accepted | 2026-09-06 |
| 025 | Tool Registry is an explicit allowlist | Accepted | 2026-09-06 |
| 026 | Tool arguments are schema-validated | Accepted | 2026-09-06 |
| 027 | user_id comes from the trusted execution context | Accepted | 2026-09-06 |
| 028 | Agent never reaches the DB; tools are the execution boundary | Accepted | 2026-09-06 |
| 029 | Tools do not carry business rules | Accepted | 2026-09-06 |
| 030 | ToolResult uses stable domain schemas | Accepted | 2026-09-06 |
| 031 | Phase 4B executes one tool call first (refund = eligibility + conditional refund) | Accepted | 2026-09-06 |
| 032 | Risk Engine is pure and policy-driven | Accepted | 2026-09-06 |
| 033 | Refund execution defaults to Human Approval | Accepted | 2026-09-06 |
| 034 | Approval binds the original ToolRequest and resumes its snapshot | Accepted | 2026-09-06 |
| 035 | Execute → Verify re-checks authoritative business state | Accepted | 2026-09-06 |
| 036 | Refund amount is never user- or agent-controlled | Accepted | 2026-09-06 |
| 037 | Internal Tools and MCP Tools coexist | Accepted | 2026-09-07 |
| 038 | MCP never bypasses Risk Control (no refund/cancel over MCP) | Accepted | 2026-09-07 |
| 039 | MCP errors are normalized into internal ToolResult | Accepted | 2026-09-07 |
| 040 | LLM Provider isolated behind an abstraction | Accepted | 2026-09-07 |
| 041 | LLM output is an untrusted proposal | Accepted | 2026-09-07 |
| 042 | Evaluation is a fixed offline dataset; Observability stays single-process | Accepted | 2026-09-07 |
| 043 | 售后案件使用独立领域模型，而不是扩展 Ticket | Accepted | 2026-09-09 |
| 044 | AfterSalesCase 是售后任务的持久化业务对象；LLM 只提供结构化提议 | Accepted | 2026-09-10 |
| 045 | Eligibility is decided by deterministic business logic, not the LLM | Accepted | 2026-09-11 |
| 046 | Policy facts come from retrieved evidence through a minimal adapter | Accepted | 2026-09-11 |
| 047 | Treatment planning is constrained by Case requested_action and eligibility | Accepted | 2026-09-11 |
| 048 | After-sales execution passes the existing Risk Gate; success requires Verify | Accepted | 2026-09-11 |
| 049 | Low-risk refund auto execution (P-REFUND-LOW-RISK-AUTO) | Accepted | 2026-09-11 |
| 050 | Demo seed timestamps are anchored to an injectable clock | Accepted | 2026-09-11 |
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
## Decision 019 — Intent and Route are separate

**Decision:**
Intent (what the user wants) and Route (which pipeline step runs next) are separate typed enums
(`app/agent/state.py`). The router only maps Intent + extracted entities onto a Route; it never
contains order / refund / RAG business logic. Examples: REFUND_INQUIRY → RAG while
REFUND_REQUEST → REFUND_TOOL; UNSUPPORTED → ESCALATE; AMBIGUOUS → CLARIFY.

**Reason:**
Classification and routing are different concerns with different future implementations
(LLM classifier vs model/rule router). Keeping them separate avoids `if intent == ...` business
logic piling up in the router and keeps both independently replaceable and testable.

## Decision 020 — AgentState is domain state, not framework state

**Decision:**
`app/agent/state.py` defines our own domain `AgentState` / `AgentResult` dataclasses. Business and
orchestration code depend on these types; they never depend on a LangGraph state object. A later
LangGraph adapter maps domain state onto graph state (adapter layer), never the reverse.

**Reason:**
The orchestration framework must stay replaceable (Decision 002 constraint); domain state is the
stable contract across phases (observability / evaluation / HITL later read the same fields).

## Decision 021 — Agent layer has no direct database access

**Decision:**
The agent package never imports SQLAlchemy / `app.db` / `app.services` / `app.api` and never opens
a session. Knowledge reaches the workflow only through the existing `RetrievalPipeline` entry
point (injected as the `RetrievalRunner` interface); dynamic business data/actions are reached only
through planned `ToolRequest`s executed later by Tool → Service → Repository.

**Reason:**
Enforces Decision 001/003 boundaries at the Agent layer: no LLM → SQL, no RAG → order database,
no writing order state into the knowledge base, and no Agent code path that can bypass Service
validation (architecture test asserts the package imports stay clean).

## Decision 022 — Static knowledge vs dynamic data / business actions (agent level)

**Decision:**
At the Agent layer: static knowledge questions route to RAG; dynamic business data and business
actions route to the business tool interface. Phase 4A plans a `ToolRequest` but never executes a
tool; Phase 4B executes it through the Tool layer under Service rules (Risk Control / HITL in Phase 5).

**Reason:**
Restates Decision 001 at the agent boundary: policy content must never substitute authoritative
order / refund / logistics data, and business actions must never execute outside business rules.

## Decision 023 — Ambiguous requests are never guessed

**Decision:**
When a required entity is missing (e.g. refund / cancel without an explicit order id) or the
message references several distinct orders, the workflow routes to CLARIFY and returns a
`needs_clarification` Response State. The agent never picks a "most recent" order or the first id.

**Reason:**
Guessing order references on high-impact actions creates wrong-target risk. Asking is
deterministic, cheap and auditable, and matches PRD "do not guess" requirements for after-sales
actions.

## Decision 024 — LangGraph is orchestration only; Phase 4A stays framework-agnostic

**Decision:**
LangGraph remains the target orchestration / runtime layer (Decision 002), but Phase 4A adds no
LangGraph dependency. Phase 4A ships a small deterministic state machine
(`app/agent/workflow.py`) whose nodes are orchestration only; `AgentState` can be mapped onto
LangGraph state through a later adapter without rewriting business code.

**Reason:**
Phase 4A has no exercised need for an external graph runtime; adding LangGraph now would add
complexity without value and violate the dependency discipline (AGENTS.md "do not add unnecessary
dependencies"). The adapter seam keeps Decision 002 reachable later.

## Decision 025 — Tool Registry is an explicit allowlist

**Decision:**
All business tools are registered in one `ToolRegistry` (`backend/app/tools/registry.py`).
The Agent / ToolExecutor resolves tools by name from the registry only; there is no
`getattr` / `eval` / dynamic `importlib` dispatch. Duplicate registration raises
explicitly; unknown names produce the stable `UNKNOWN_TOOL` error.

**Reason:**
The registry is a security boundary: it makes the full set of callable tools
auditable and prevents a model or agent from invoking arbitrary functions.

## Decision 026 — Tool arguments must pass schema validation

**Decision:**
Every tool defines an explicit Pydantic input schema (`backend/app/tools/definitions.py`).
Model- or agent-produced arguments are never trusted: the ToolExecutor validates them
before any handler runs (missing fields / wrong types / empty strings / invalid order
references all normalize to `VALIDATION_ERROR`).

**Reason:**
"JSON came out of a model" is not an excuse to skip validation; invalid arguments must
fail fast with a stable, structured result instead of reaching the Service layer.

## Decision 027 — user_id uses the trusted execution context

**Decision:**
`ToolExecutionContext.user_id` comes from the authenticated Agent / session context.
The ToolExecutor always overrides any model-supplied `user_id` in arguments with the
trusted context value before validation.

**Reason:**
A model cannot escalate privileges by passing `user_id="another_user"`; identity is a
session property, never an argument.

## Decision 028 — Agent never reaches the DB; tools are the execution boundary

**Decision:**
The Agent layer never imports SQLAlchemy / `app.db` / `app.services` / `app.api`
(architecture test enforced since Phase 4A). Phase 4B business actions run through an
injected ToolExecutor: Agent → Tool → Service → Repository → Database.

**Reason:**
Keeps Decision 021 enforceable now that tools actually execute: there is still no Agent
code path that can bypass Service validation or write directly to the database.

## Decision 029 — Tools do not carry business rules

**Decision:**
Refund eligibility, authoritative refund amounts, the cancellation state machine,
duplicate-refund detection and ticket entity validation stay in the Service layer.
Tool handlers only normalize input/output and enforce the execution-time authorization
boundary (an order that exists but belongs to another user is rejected).

**Reason:**
Business rules must have exactly one owner (the Service layer) so Mock (Phase 2B) and
real backends behave identically; the Tool layer is a thin, replaceable adapter.

## Decision 030 — ToolResult uses stable domain schemas

**Decision:**
Tools never return ORM objects. Each tool declares an output schema
(`OrderToolOutput` / `LogisticsToolOutput` / `RefundEligibilityToolOutput` /
`RefundToolOutput` / `CancelOrderToolOutput` / `TicketToolOutput`) and the executor
stores JSON-serializable dicts in `AgentState.tool_results`.

**Reason:**
A stable, schema-bound result keeps Agent state serializable, prevents internal
database fields / sensitive data from leaking and gives observability / evaluation a
fixed contract to consume.

## Decision 031 — Phase 4B executes one tool call first (refund is conditional)

**Decision:**
Phase 4B keeps execution simple: a normal request plans one tool call, executes it once
and stores one ToolResult (the loop is data-driven and can grow later). REFUND_REQUEST
is the one explicit exception: it runs `check_refund_eligibility` first and only appends
`create_refund` when eligibility says eligible=True; otherwise the refund service is
never reached. Full Risk Control / Human-in-the-loop lands in Phase 5.

**Reason:**
Fidelity to the refund requirement (never create a refund on an ineligible order)
matters more than mechanical one-call symmetry; the sequence stays readable and the
second step is not a general agent loop.

## Decision 032 — Risk Engine is pure and policy-driven

**Decision:**
Phase 5 introduces `app/risk` (RiskEngine / RiskPolicy / RiskLevel / RiskAction /
RiskDecision / RiskContext). The mapping from operation (+ business context) to a
decision lives in policy data (including `high_value_refund_threshold = 500`),
never inside Tool Handlers. Operations without a registered rule are BLOCKed
(fail-closed) instead of auto-executed.

**Reason:**
Rules become auditable and changeable without touching tool or agent code, and an
unregistered operation can never silently execute.

## Decision 033 — Refund execution defaults to Human Approval

**Decision:**
`create_refund` is HIGH (HUMAN_APPROVAL); refunds at or above the configured
threshold are CRITICAL (still HUMAN_APPROVAL). Eligibility checks stay LOW /
AUTO_EXECUTE.

**Reason:**
A refund is real financial loss; the MVP never lets the agent execute a
money-affecting operation unconditionally, even after an eligibility check passes.

## Decision 034 — Approval binds the original ToolRequest and resumes its snapshot

**Decision:**
`approval_requests` persists tool_name + tool_arguments as an immutable snapshot
plus request_id / risk_level / reason / status / timestamps / resolved_by.
Approving resumes exactly that stored snapshot through the Tool Executor; the LLM
is never asked to re-plan or re-generate arguments after approval.

**Reason:**
Prevents the reviewed action (order X) silently becoming a different action
(order Y) at execution time, and keeps resume deterministic and auditable.

## Decision 035 — Execute → Verify re-checks authoritative business state

**Decision:**
After a successful `create_refund` / `cancel_order`, the workflow re-queries the
Repository / DB and requires the expected authoritative state (refund row exists
with status PENDING and amount == order.total_amount; order.status == CANCELLED).
Any mismatch ends the run with VERIFICATION_FAILED.

**Reason:**
Tool output can be stale or wrong; the database is the source of truth, so a fake
"tool success" can never be reported to the user as a completed refund/cancel.

## Decision 036 — Refund amount is never user- or agent-controlled

**Decision:**
Tool input schemas do not accept an amount field; the Service derives the refund
amount from the order's authoritative total_amount. The workflow passes only the
eligibility result amount into the RiskContext for classification and never into
execution.

**Reason:**
Neither the end user nor the model can influence financial amounts; there is one
authoritative computation of the refund amount.

## Decision 037 — Internal Tools and MCP Tools coexist

**Decision:**
Phase 6 exposes only three MCP tools over a local stdio MCP Server:
`get_order` / `get_logistics` / `create_ticket`. Refund, cancel and eligibility
keep running through the existing internal Tool Executor behind the Risk Gate.
The Agent sees a single tool-provider interface (`MCPToolAdapter`) that forwards
MCP-exposed tools to the MCP Client and everything else to the internal
executor.

**Reason:**
MCP adds standardization, tool discovery and provider decoupling; it is not a
Tool Calling replacement and does not justify rewriting an executor already
validated with Risk Control + Human-in-the-loop (Phase 5).

## Decision 038 — MCP never bypasses Risk Control (no refund/cancel over MCP)

**Decision:**
MCP exposes no refund or cancel tool, and its handlers never perform risk
evaluation or approval. The Risk Engine runs before any tool-provider call, and
MCP tool handlers go `Service → Repository → DB` only (never raw business SQL);
`user_id` comes from the trusted adapter context, and cross-user order access is
rejected.

**Reason:**
High-risk operations must keep their Risk + Human Approval path; a simple MCP
refund tool would let a client bypass Phase 5, which the architecture must
prevent by construction.

## Decision 039 — MCP errors are normalized into the internal ToolResult vocabulary

**Decision:**
The MCP Client maps unknown tool / invalid arguments / server error / malformed
result into internal `ToolResult` statuses with stable `MCP_*` codes; raw MCP
SDK exceptions never reach the Agent layer.

**Reason:**
Keeps Agent and observability / evaluation consumers on one error contract
regardless of transport (internal executor or MCP).

## Decision 040 — LLM Provider isolated behind an abstraction

**Decision:**
The Agent never calls a vendor SDK directly. Real DeepSeek is exposed through a
minimal `LLMProvider` Protocol (`backend/app/llm/base.py`) with one
OpenAI-compatible `DeepSeekProvider` implementation (httpx). The workflow only
depends on the abstraction, so the model vendor can be swapped without touching
Agent / Risk / Approval / Tool / MCP logic.

**Reason:**
Avoids coupling Agent business logic to a specific model vendor; tests and CI
inject lightweight fakes behind the same interface (zero network), and future
model integration (OpenAI Responses API) can add a provider without rewriting
the workflow.

## Decision 041 — LLM output is an untrusted proposal; deterministic business controls remain authoritative

**Decision:**
LLM output (intent / entities / final wording) is treated as an untrusted
proposal. It never executes tools, never decides eligibility or amounts, never
changes risk levels and never authorizes approvals. Execution stays behind
Risk Gate → User Confirmation / Human Approval → Tool Executor / MCP Adapter →
Verify; the Business Service remains the authoritative source of facts.

**Reason:**
Prompt injection or a malformed model response cannot bypass deterministic
security boundaries by construction; for high-risk actions "LLM failure =>
do not auto-execute" holds (fallback to deterministic classifier / CLARIFY).


## Decision 042 — Evaluation is a fixed offline dataset; Observability stays single-process

**Decision:**
Phase 7C evaluation uses a FIXED local dataset executed against the real agent
path (`run_chat` / `finalize_approval`) inside an isolated temporary SQLite
database; metrics are categorical pass / fail / N/A checks (Intent / Entity /
Route / Risk / Approval / Execution / Verification) and only count cases with
an explicit expectation. Observability is a single-process event projection:
AgentState records every Risk Gate decision (`risk_decisions`) and the demo run
payload timeline carries Understand / Route / tool / Risk Gate / Human Approval
/ Execute / Verify / Finalize steps with status + summary + provider. No
distributed tracing, OTel, Kafka, Celery or Prometheus is added for the demo.

**Reason:**
The interview demo must be reproducible, explainable and honest: an isolated DB
keeps evaluation writes out of the live demo data, N/A prevents fabricated
scores, and the recorded risk-gate events make the decision chain auditable.
Production-scale evaluation/tracing remains out of scope and is not claimed.

## Decision 043 — 售后案件使用独立领域模型，而不是扩展 Ticket

**Decision:**
Phase 9 新增独立的 `AfterSalesCase`（表 `after_sales_cases`）承载一次完整售后处理案件，不把售后流程状态、已收集 / 缺失信息、AI 摘要塞进既有 `Ticket`。`risk_level` 复用 Phase 5 的 `RiskLevel` 词表并以 `String(20)` 存储（与 `approval_requests` 一致），`collected_information` / `missing_information` 使用 `sa.JSON`（SQLite 与 PostgreSQL 通用）。

**Reason:**
`Ticket` 的语义是「人对人的工单」——分类 + 优先级 + 处理状态；售后案件需要独立的流程状态机（INFORMATION_COLLECTION → ELIGIBILITY_CHECK → PROCESSING → PENDING_HUMAN / COMPLETED / REJECTED）、结构化的已收集与缺失信息，以及转人工时使用的 AI 摘要。复用 Ticket 会让一张表承担两种生命周期，并让后续 9B~9E 的 Agent / 工具写入路径与人工工单互相污染。独立表同时避免修改既有订单 / 退款 / 工单逻辑，符合本阶段「最小兼容修改」约束。

## Decision 044 — AfterSalesCase 是售后任务的持久化业务对象;LLM 只提供结构化提议

**Decision:**
AfterSalesCase 是售后任务的持久化业务对象;LLM（或确定性 NLU）负责从自然语言中提出结构化信息（案件类型 / 诉求 / 问题描述），但业务事实由 Business System 提供，Case Service 不直接执行退款 / 换货 / 维修。
同时：Agent 的 Case 管理步骤只在既有意图管线无法处理的意图（`UNSUPPORTED` / `AMBIGUOUS`）上运行，保证 RAG / Order / Logistics / Cancel / Refund / Ticket 的既有行为不变。

**Reason:**
售后任务的生命周期（信息收集 → 资格校验 → 处理 → 完成 / 拒绝 / 转人工）比一次问答长得多，需要跨轮次持久化，因此用独立的 `AfterSalesCase` 承载，而不是把状态放在会话内存或聊天记录里。

把「用户说了什么」与「业务事实是什么」分开，是因为二者来源不同：用户或模型只能提供声明，例如「我好像是上周买的」「订单是 ORD-1004」，这些都不是订单的权威状态。订单是否存在、购买时间、金额、可退性必须来自 Business System。因此 Case 只把用户声明存进 `collected_information`，`order_id`（orders 外键）仅在订单确实存在时写入；系统中不存在的引用只保留为字符串，绝不升级为业务事实。

Case Service 不调用 RefundService / RiskEngine / LLM / MCP，是为了保持领域边界：退款 / 换货 / 维修属于后续阶段的业务动作，必须各自经过既有的 Risk Gate 与 HITL，而不能被案件层直接触发。

把 Case 步骤限制在 `UNSUPPORTED` / `AMBIGUOUS` 上，是 Phase 9B 的最小侵入策略：售后处理请求（如「我的耳机坏了」）当前会被判为 UNSUPPORTED 并转人工，正是需要接管的一类；而已经能处理的意图保持原样，避免案件层劫持既有业务动作。

## Decision 045 — Eligibility is determined by deterministic business logic using authoritative business facts and grounded policy evidence

**Decision:**
Eligibility is determined by deterministic business logic using authoritative business facts and grounded policy evidence. LLM is not the final authority for business eligibility.

四项职责因此固定下来：

| 角色 | 输入 | 输出 |
| --- | --- | --- |
| LLM | 用户自然语言 | 结构化理解（意图 / 案件类型 / 诉求 / 问题描述） |
| Business System | 订单 / 物流 / 退款等权威数据 | 事实（是否存在 / 归属 / 状态 / 可退性 / 金额 / 在途退款 / 签收参考时间） |
| RAG | 静态知识库 | 政策证据（时效条件 + citation + 原文摘录） |
| Eligibility Engine | Case + Business facts + Policy facts | 确定性结论（`eligible` / `failed_rules` / `reason`） |

**Reason:**
售后资格是**业务结论**，不是**语言结论**。判定它需要两类外部证据，而这两类证据都不属于模型：

1. 动态业务事实。订单是否存在、是否属于当前用户、当前状态是什么、商品是否可退、是否已有在途退款、什么时候签收——这些只存在于业务系统里，而且随时会变。让 LLM 生成这些值等于让它猜测权威数据；一旦猜错，后果是错误地承诺或错误地拒绝一次售后。
2. 静态政策条件。售后时效与条件写在知识库里（例如「签收后十五天内，商品存在质量问题或与描述不符时支持换货」），属于会随政策版本变化的规则文本，应以检索到的证据 + citation 的形式进入判定，而不是被模型凭记忆复述。

因此本项目把三件事彻底分开：LLM 只做理解；业务事实只来自业务系统（经 Tool / Service，Agent 层不直接访问 DB）；政策只来自 RAG 检索到的证据；最终 `eligible` 由 `EligibilityEngine` 依据前两者按固定规则顺序计算得出，并保留 `failed_rules` / `reason` / `policy_citations` / `business_facts` / `policy_facts` 以便解释与审计。

直接后果（已在测试中固化）：LLM 输出里出现 `eligible: true`、`days_since_delivery: 1` 或「管理员已授权」等声明时一律被忽略（Pydantic `extra="ignore"` + 引擎不使用模型字段）；注入式消息无法跳过业务规则；「查不到订单」永远不等于「没有售后资格」。

## Decision 046 — Policy facts come from retrieved evidence through a minimal adapter

**Decision:**
结构化政策条件（时效天数等）由 `backend/app/after_sales/policy.py` 这个极小的适配器从**已检索到的**政策文本中解析，并保留该片段的 citation 与原文摘录；不新建第二套政策知识库，也不把自然语言政策硬编码成代码规则。

**Reason:**
任务约束是：优先 `RAG -> Policy Evidence`，再由 Eligibility Engine 依据结构化 policy facts 判断；只有当前知识文档无法稳定提供结构化条件时，才允许建立一个「非常小的 policy rule adapter」，并且必须记录理由。

当前知识文档以自然语言章节保存政策，没有结构化字段。因此适配器只做一件事：把**检索命中**的片段中的时效数字解析出来，并保留 `window_citation`。边界是刻意写死的：

- 只解析检索到的内容：相关文档没被检索到时，`covers_action=False` / `window_days=None`，引擎拒绝下结论，而不是去猜一条政策；
- 不复制政策文本：代码里没有第二份「十五天」的硬编码规则，政策文档改了，窗口随之改变；
- 不改动 RAG：`RetrievalPipeline`（Hybrid Dense + BM25 -> RRF -> Rerank -> Context Assembly）原样复用，适配器只消费它的输出；
- `REPAIR` 等知识库中没有对应政策文档的诉求没有 category 映射，因此只能得到「政策未覆盖」，不会被误判为「不符合条件」。

## Decision 047 — Treatment planning is constrained by Case requested_action and deterministic eligibility results

**Decision:**
售后「处理方案」(TreatmentPlan)由确定性规则生成:`用户请求(requested_action) + Case(case_type / problem) + EligibilityResult + 业务规则 → TreatmentPlan`。`action` 只能来自 `REFUND` / `EXCHANGE` / `REPAIR`,并且**必须等于用户自己提出的 `requested_action`**;LLM 不能独立选择一个可执行的业务动作,也不能把退款请求变成换货。`eligible != True`、`requested_action = UNKNOWN` 时不选动作、`requires_execution=False`、不创建执行型工单。

**Reason:**
如果让 LLM 自由决定「退款 / 换货 / 维修」,业务动作就会受模型随机性影响:同一句「我的耳机坏了」在不同采样下可能生成不同的售后动作,而这直接决定真实业务后果。因此动作的来源必须是**用户明确表达的诉求**这一事实,而不是模型的判断。Eligibility 已经回答了「能不能处理」(确定性业务规则 + 权威业务事实 + 检索到的政策证据),Treatment Plan 只回答「按用户诉求怎么准备」,两者都不接受 LLM 的自由发挥——LLM 只负责语言理解(把自然语言变成结构化的 case 事实)。

边界(Phase 9D 强制):

- TreatmentPlan 由纯领域模块 `backend/app/after_sales/treatment.py` 生成:无 SQLAlchemy、无 Service、无 LLM、无 retrieval(测试含源码级 import 守卫),可离线确定性复现;
- 动作校验是**白名单**:`EXECUTABLE_ACTIONS = (REFUND, EXCHANGE, REPAIR)`;UNKNOWN 或非法值一律视为「不能自动决定」,要求补充信息或转人工,而不是自选;
- `eligible=False` -> `REJECTED`,不建执行型工单;`eligible=None` -> 保持 `ELIGIBILITY_CHECK` / `INFORMATION_COLLECTION`,不建执行型工单;
- 工单只经 `TicketService → Repository → DB` 创建(不直接写库、不绕过 `TicketService`),`AgentWorkflow` 只依赖注入的 `AfterSalesTreatmentPlannerLike` Protocol,Agent 层仍不 import SQLAlchemy / Service;
- 工单描述使用结构化模板,其中的订单状态 / 资格判断 / 政策依据必须取自已有调查结果与检索到的 citation,LLM 不得编造;
- **本阶段不执行任何业务动作**:没有 create_refund / cancel_order / 换货 / 维修,真正的执行必须走既有 Risk Gate + Human-in-the-loop + Execute → Verify(后续 Phase);
- 同一 Case 重复处理只复用已有工单(幂等),不重复创建;创建失败必须如实返回失败,不伪造 ticket_id。

## Decision 048 — After-sales execution must pass the existing Risk Gate and Tool Executor, and success is only proven by re-reading business state

**Decision:**
After-sales execution must pass the existing Risk Gate and Tool Executor. Successful tool invocation is not sufficient for completion; business state must be re-read and verified before the Case becomes COMPLETED.

**Reason:**
「工具调用成功」只证明一次调用返回了成功，不证明业务状态真的改变了：退款可能落库失败、金额可能不符、订单可能对不上、写操作可能被重复提交。如果把 `ToolResult.status == SUCCESS` 直接当成 `COMPLETED`，Agent 就会向用户确认一件并未发生的事——这是售后场景最严重的错误类型。因此本项目的规则是：

- **Execute = 发起业务动作**（复用既有 Tool Executor → RefundService → Repository，绝不新增第二套退款实现）；
- **Verify = 确认业务状态真的改变**（`BusinessVerifier` 重新读取退款记录，校验 refund exists / status / order / amount）；
- **只有 Verify 成功，Case 才能 `COMPLETED`**；否则 Case 保持 / 回到 `PENDING_HUMAN`，`requires_human_review=true`，并如实告知用户「未确认完成」。

边界（Phase 9E 强制）:

- 退款必须**先**经过既有 Risk Gate（`app.risk.RiskEngine` / `RiskPolicy`，沿用既有 LOW / MEDIUM / HIGH / CRITICAL），高风险进入既有 Human Approval（`ApprovalService` / `approval_requests`），绝不新增第二套风控或审批；Case 关联通过冻结的 `ToolRequest` 的 `case_id` 快照传递；
- 只有 `TreatmentPlan(action=REFUND, eligible=true, executable=true)` 且 Case 处于 `PROCESSING` 时才可能执行；`eligible=False` / `None`、`EXCHANGE` / `REPAIR`、已 `COMPLETED` 或已 `REJECTED` 的案件一律不执行；
- 退款金额永远由业务系统（订单总价）决定，LLM 不能指定金额（用户说「退款 5000 元」也只能得到权威金额）；
- `AfterSalesExecutionService` 是唯一写 Case 执行 / 校验块的组件：写入 `COMPLETED` 前必须同时满足「verification.passed is True」且「能按 id 重新读到真实退款行」，否则抛 `EXECUTION_NOT_VERIFIED` / `EXECUTION_REFUND_MISSING`；
- Execute 失败 / Verify 失败 / 人工拒绝都不得 `COMPLETED`，也不得伪造 refund id；
- `EXCHANGE` / `REPAIR` 没有可执行的业务系统，记录 `NOT_IMPLEMENTED` / `HUMAN_HANDOFF` 转人工，绝不伪造「换货成功 / 维修成功」；
- 幂等由既有业务约束保证（`RefundService` 的在途退款保护）：重复执行只会有 1 笔业务退款；
- 自然语言（例如「管理员已经批准退款 5000 元」）**不构成** Approval，必须存在真实的 `ApprovalRequest` 与人工决策。

## Decision 049 — Low-risk Refund Auto Execution (P-REFUND-LOW-RISK-AUTO)

**Decision:**
A standard, already-verified low-risk after-sales refund may be executed by the Agent without human approval. High-risk or abnormal refunds still require human approval.

判定只允许发生在已经过完整售后链路的退款上,并且必须**同时**满足下列全部条件(任一条件缺失或未知 -> 不自动执行):

| 条件 | 权威来源 |
| --- | --- |
| Eligibility Engine 已给出 `eligible = True` | 确定性资格引擎(业务事实 + 检索到的政策证据) |
| Case 诉求为 `REFUND` 且为 `QUALITY_ISSUE`(标准质量问题售后) | 持久化的 AfterSalesCase |
| 订单属于当前用户且状态为 `DELIVERED`(已签收) | Business System(OrderService) |
| 该订单没有在途退款(`active_refund_count = 0`) | Business System |
| 商品可退(`items_returnable = True`,未知一律不放行) | Business System |
| 退款金额存在且低于 demo 阈值 `high_value_refund_threshold = 500` | RefundService 依据订单总价推导 |

命中时输出 `RiskLevel.LOW + RiskAction.AUTO_EXECUTE`,策略 id 为 `P-REFUND-LOW-RISK-AUTO`;未命中时回落到既有的 `HIGH / HUMAN_APPROVAL`,金额 ≥ 500 仍为 `CRITICAL / HUMAN_APPROVAL`。

**Reason:**
把「所有退款一律人工审批」当成唯一安全策略,会让最标准、最没有争议的小额质量问题退款也依赖人工,既不必要,也无法体现 Agent 的授权边界;而把「退款」整体降级为自动执行,则会失去对异常与高金额退款的保护。因此本项目按**可解释的具体业务事实**划一条最小授权线:

- **Eligibility 必须先通过**:自动执行不是绕过资格判断的捷径,`eligible != True` 时永远不进入自动路径;
- **风险判断只使用权威业务事实**:上下文由 Workflow 从持久化的 Case 与确定性 Eligibility 结果复制,LLM / 用户输入不是事实来源;unknown 视为「无法证明低风险」,一律不放行;
- **退款金额由 RefundService 依据订单金额决定**:金额不进入 ToolRequest 参数,LLM 与用户都不能指定(用户说「退款 5000 元」仍只得到权威金额);
- **Risk Gate 不得被绕过**:自动执行只是 Risk Gate 的一种决策结果,`AUTO_EXECUTE` 之后仍走同一个 ToolExecutor → RefundService → Repository;
- **Execute 之后必须 Verify**:`BusinessVerifier` 重新读取权威业务状态(退款行存在 / 状态 / 归属 / 金额);
- **Verify 失败不得 `COMPLETED`**:Case 保持 / 回到 `PENDING_HUMAN`,`requires_human_review = true`,不伪造成功;
- **高风险与异常继续人工审批**:金额 ≥ 500、非质量问题、非已签收、有在途退款、可退性未知、资格未通过等情况一律 `HUMAN_APPROVAL`;
- **路由只对售后退款生效**:同时报告商品质量问题并要求退款的请求进入 After-Sales Case 链路后再判定;历史兼容的一次性 `REFUND_TOOL` 流程没有 Case 事实,其风险等级保持 `HIGH / HUMAN_APPROVAL` 不变。

边界与范围:

- 这是**本项目 demo 定义的业务风险策略**,不是生产级金融风控规则,也不构成任何合规结论;阈值与条件是 policy/config 数据,不是硬编码在业务逻辑里;
- 不新增第二套 RefundService / RiskEngine / ApprovalService,不新增风险等级,不改变 Execute → Verify 语义;
- `days_since_delivery` 仅用于可观测性:售后时效窗口由确定性 Eligibility Engine 判定一次,风险层不重复推导,避免出现第二套时效规则。

## Decision 050 — Demo seed timestamps are anchored to an injectable clock, not fixed calendar dates

**Decision:**
`seed_demo_orders(session, *, now=None)` derives every demo timestamp from a single anchor: ORD-1001 `now - 4d`, ORD-1002 `now - 3d`, ORD-1003 `now - 2d`, ORD-2001 `now - 1d`, logistics `updated_at = now - 3d` / `estimated_delivery = (now - 1d).date()`. When `now` is omitted the real clock is used (`_reference_now()`), so the live demo always sits inside the 15-day after-sales window; tests and the evaluation runner pass a fixed anchor (`DEMO_ANCHOR = 2026-09-11T12:00+00:00` in `app/evaluation/dataset.py`, `ANCHOR` in the after-sales / demo tests) for determinism.

**Reason:**
固定日历日期会让 demo 随时间「过期」:签收时间写死为 2026-08-22 后,真实时钟一旦超过 15 天窗口,在线 demo 的 ORD-1003 就会被 Eligibility 正确判为 `REJECTED`,Decision 049 的 `P-REFUND-LOW-RISK-AUTO` 自动退款现场无法演示。把锚点做成可注入参数后:

- **生产业务规则一行未改**:Eligibility / Risk Policy / RefundService / Verify 不变,时效窗口仍由真实时钟与检索到的政策文本决定;
- **演示可复现**:线上 bootstrap 传真实时钟,新种子库上的签收时间永远落在窗口内;测试 / 评测注入固定锚点,结果与运行日期无关;
- **优先级不变**:ORD-1001(`now - 4d`,金额 1299 >= 500)依然在窗口内,因此仍是 `CRITICAL / HUMAN_APPROVAL`,高额退款不会被自动执行;
- **不做时钟 mocking**:只把「种子数据的时间」参数化,判定方仍然读真实时钟;
- **不改写既有数据库**:`demo_orders_present` 幂等短路,已存在的 demo DB 不会被重新种子;若某个持久化 DB 的签收时间已经老化,需要重建种子库。
