# Architecture — Enterprise AI Customer Service Agent

> 目标架构(尚未全部实现;Phase 1 骨架、Phase 2A 数据层、Phase 2B Mock Business API、Phase 2C 场景验证、Phase 3A 知识库接入、Phase 3B 混合检索、Phase 3C 重排 + 上下文组装与 Phase 4A Agent Workflow 骨架、Phase 4B Tool Execution 已落地)。对应决策记录见 docs/DECISIONS.md,阶段拆分见 docs/DEVELOPMENT_PLAN.md。

## 1. System Architecture

```text
User
 → API
 → Agent Orchestrator
    → RAG / Tools / MCP
    → Risk Control
    → Human Approval
    → Verification
 → Answer
```

- API:统一入口(`/api/v1`),鉴权、限流、会话管理。
- Agent Orchestrator:编排决策与执行(LangGraph 作为运行时)。
- RAG / Tools / MCP:三类能力来源——静态知识、动态业务数据、标准化工具暴露。
- Risk Control:对拟执行动作做风险分级评估。
- Human Approval:高风险/异常操作中断等待人工审批。
- Verification:执行后校验结果是否成功、是否符合预期。
- Answer:向用户输出最终回答(含引用/状态)。

## 2. Agent Architecture

```text
Supervisor / Router
├── Knowledge Agent   (FAQ / 知识问答)
├── Order Agent       (订单 / 物流查询)
└── After-sales Agent (退款 / 取消 / 投诉 / 售后)
```

原则:**不要为了 Multi-Agent 而 Multi-Agent**。

- Supervisor/Router 负责意图路由与任务分发。
- 简单任务可以直接处理:例如 FAQ 可直接走 RAG 后回答,不必强行进入子 Agent 或构造多步工作流。
- 子 Agent 仅在需要专门上下文/工具集时创建(订单域、售后域)。
- 业务逻辑不得重度依赖 LangGraph:编排层可替换,业务判断与规则应可独立测试。

## 3. RAG

```text
Query
 → Rewrite
 → Vector Search + BM25
 → Fusion
 → Reranker
 → Context Assembly
 → LLM
 → Grounding Validation
```

- 适用:静态知识(政策、流程、FAQ)。
- 输出必须可溯源(引用知识来源),通过 grounding validation 才能作答,否则承认未知或转人工。

## 4. Tools

工具清单(动态业务数据/操作;Phase 2A 已落地 mock 数据模型与 Repository,Phase 2B 已提供 mock HTTP API,Phase 4B 已通过 Tool Registry + ToolExecutor 接入 Agent——见 §18):

- `get_order` — 查订单
- `get_logistics` — 查物流
- `check_refund_eligibility` — 查退款资格
- `create_refund` — 发起退款(高风险)
- `cancel_order` — 取消订单(高风险)
- `create_ticket` — 创建工单/投诉单

工具只做能力暴露,不负责 Agent 决策。

## 5. MCP

- 提供 Customer Service MCP Server,统一暴露售后域工具与上下文。
- 通过 MCP 标准化工具/上下文暴露;Agent 编排与业务工具服务解耦。
- **MCP 不负责 Agent decision logic**:决策(要不要调用、风险多高、怎么回复)在 Orchestrator 层完成。

## 6. Risk

风险等级与处置(Phase 5 落地实现;业务视角另见 docs/PRD.md):

- LOW → AUTO_EXECUTE:FAQ / 知识问答、订单查询、物流查询、退款资格查询等只读操作直接执行。
- MEDIUM → USER_CONFIRM:取消订单。必须等待用户确认(confirmed=True 才执行;confirmed=False → REJECTED 不执行)。
- HIGH / CRITICAL → HUMAN_APPROVAL:退款执行默认人工审批;金额 ≥ 阈值 500(policy 常量,可配置)判为 CRITICAL,其余为 HIGH。
- 未知操作 → BLOCK(fail-closed),不会静默自动执行。

规则是 policy 数据(backend/app/risk/policy.py),不写在 Tool Handler;退款金额等业务上下文由 Workflow/Service 查询后提供给 Risk Engine,引擎不读 DB、不信任用户输入金额。高/极高风险操作**不能**未经风险控制直接执行。

## 7. Human-in-the-loop

```text
Interrupt → Approval → Resume
```

- 触发点:CRITICAL 操作、风控命中、异常/低置信会话、用户要求转人工。
- 审批:客服运营 approve / reject(留痕)。
- 恢复:审批通过后从断点恢复,继续原工作流。

落地(Phase 5):审批请求持久化到 `approval_requests`(绑定原始 ToolRequest 快照),经 `GET /api/v1/approvals`、`POST /api/v1/approvals/{id}/approve`、`POST /api/v1/approvals/{id}/reject` 处理;approve 后 workflow 恢复执行该审批绑定的快照(不重新让 LLM 生成参数),执行后再 Verify(见 §19 与 docs/RISK_CONTROL.md)。

## 8. Evaluation

五层评测(Phase 7):

- Retrieval / Generation / Agent / Tool / Product

评测集与回归流程是 first-class 组件,随功能一起演进(详见 PRD §9)。

## 9. Observability

端到端 trace:

```text
request → model → state → retrieval → tool → result → answer
```

记录模型调用、状态流转、检索引用、工具结果、风控/审批事件(详见 PRD §10)。

## 10. 分层依赖约定(对应 DECISIONS.md)

- 编排层:LangGraph(可替换抽象)。
- 业务/工具层:FastAPI 服务 + MCP 暴露,不反向依赖编排层。
- 知识层:静态知识入 RAG,动态数据走 Tools。
- 风控与人工审批:作为独立环节插入编排流程。

## 11. Database & Data Access(Phase 2A 落地)

数据访问调用链(禁止上层直接写 SQL):

```text
Agent → Tool → Service → Repository → Database
```

- Agent:决策与编排,不感知数据库细节。
- Tool:以业务语义暴露能力(如 `get_order`),负责输入校验与结果整理。
- Service:业务规则与流程(校验、状态机、资格判定)。Phase 2B 落地。
- Repository:数据访问封装(SQLAlchemy),提供 get / list / create 等原语。Phase 2A 已落地。
- Database:PostgreSQL(规划含 pgvector);Phase 2A 的结构与约束以 SQLite 验证。

为什么 Agent 不能直接访问数据库:

- 动态业务数据必须来自权威系统并经过业务校验,与 Decision 001(动态数据走 Tools)、Decision 003(MCP 标准化工具暴露)一致。
- 表结构、ORM、SQL 等数据访问细节不应泄漏进编排层;Repository 是数据库之上的稳定边界,避免编排层可替换性被破坏(对应 Decision 002 的补充约束)。
- 写操作(退款、取消订单)必须经过 Service 校验与 Risk Control / Human-in-the-loop,不能被 Agent 绕过(对应 Decision 004 / 005)。
- 查询语义(订单归属、可退款性、物流最新状态)属于业务规则,应集中在 Service 层,便于独立测试、评测与审计。

本调用链支撑 PRD §3 的售后场景:Order Lookup / Logistics / Refund Eligibility 为只读链路,Refund Execution / Cancel Order / Complaint 涉及写操作与风控(PRD §7)。

Phase 2A 落地范围(不含 HTTP 业务 API):

- 数据模型:users / products / orders / order_items / logistics / refunds / tickets;状态列统一使用 CHECK 约束而非数据库原生 enum,PostgreSQL 与 SQLite 行为一致。
- Alembic 初始迁移(upgrade 已验证可在全新库建出全部 7 张表)。
- 幂等确定性 seed(开发数据,非评测数据)。
- 约束:表结构变更一律走迁移;应用代码不得绕过 Repository 直接执行任意 SQL。

## 12. Mock Business System(Phase 2B 落地)

Phase 2B 把 Phase 2A 数据层封装为可被未来 Agent Tool 安全调用的 Mock Business API。当前调用链:

```text
HTTP API → Service → Repository → Database
```

各层职责:

- API 层(FastAPI,`app/api/routes`):参数绑定、Pydantic 请求/响应契约、领域错误→HTTP 映射。**不含业务规则、不直接访问数据库。**
- Service 层(`app/services`):业务规则与状态流转的唯一归属——退款资格判定、退款金额推导、取消状态机、防重复退款、引用校验。与 FastAPI 解耦,未来可被 Tool 层直接复用。
- Repository 层(`app/db/repository`):数据库访问唯一边界(聚合读取与写原语)。
- Schemas 层(`app/schemas`):Pydantic 请求/响应模型,与 ORM 模型分离,不直接暴露数据库对象。

为什么业务规则必须放在 Service:

- 同一套规则需要同时服务 HTTP 与未来的 Agent Tool;放在路由会造成重复实现和可绕过性。
- Service 是「LLM 不能决定退款/取消规则」的强制边界(对应 PRD §5 S4/S5、§6 合规、§7 风险分级):模型只能表达意图,是否允许由规则决定。
- 状态流转集中一处,便于独立测试与审计,避免散落的无条件 UPDATE。

为什么 Agent / LLM 不能直接操作数据库:

- 与 §11 相同:动态业务数据必须经过权威系统与业务校验(Decision 001/003),表结构与 SQL 不进入编排层(Decision 002),写操作先过风控与人工审批(Decision 004/005)。
- Agent 只负责意图理解与编排;执行一律通过 Tool →(本层)Service 完成,规则与审计不被绕过。

Mock API 与 Agent Tool 的映射(Phase 4B 已落地执行):

| Mock API(Phase 2B,前缀 /api/v1) | Agent Tool(Phase 4B) | 对应 PRD 场景 |
| --- | --- | --- |
| GET /orders/{order_id} | get_order | S2 Order Lookup |
| GET /orders/{order_id}/logistics | get_logistics | S3 Logistics |
| GET /users/{user_id}/orders | (支撑查询) | S2 |
| POST /refunds/check-eligibility | check_refund_eligibility | S4 Refund Eligibility |
| POST /refunds | create_refund(申请) | S5 Refund Execution(Phase 5 加风控/HITL) |
| POST /orders/{order_id}/cancel | cancel_order | S6 Cancel Order(Phase 5 加风控/HITL) |
| POST /tickets | create_ticket | S7 Complaint |

说明:Phase 2B 阶段 `create_refund` 只创建 PENDING 退款申请、`cancel` 直接执行取消,直连 Mock HTTP API 未接入风控;Phase 5 已在 Agent 工具执行路径接入 Risk Control / HITL(高危操作先过 Risk Control 再调用 Service,见 §19),资金操作仍仅创建 PENDING 退款申请。

## 13. Business Scenario Validation(Phase 2C 落地)

在引入任何 AI 组件之前,先用确定性的业务场景与跨域一致性测试验证 Phase 2A 数据层 + Phase 2B Mock API 的可靠性(见 `tests/test_business_scenarios.py`):

- 场景即工作流:订单/物流查询、退款资格矩阵、退款创建与重复保护、取消状态机、工单创建,均经由真实 HTTP → Service → Repository → Database 路径断言。
- 跨域一致性是重点:退款与取消互相冲突(在途退款 vs 取消、已取消 vs 退款、已退款 vs 再退款)由 Service 层裁决——Service 是最终权威,保证未来被独立调用的 Agent Tools 不会互相破坏业务状态。
- 业务不变量以测试固化:退款金额 == 订单金额;取消订单不可转退款;单订单单一在途退款;工单引用完整性。
- 目的:在 Phase 3+ 引入 RAG / Agent 之前,先建立「确定性、可测试、Agent 未来可安全操作」的业务基座。

## 14. Knowledge Base Architecture(Phase 3A 落地)

知识库当前只实现「入库」:Knowledge Source → Ingestion → Normalize/Chunk → Metadata → Database。**检索、Embedding 向量化与生成均属 Phase 3B+**,本阶段边界见 DEVELOPMENT_PLAN Phase 3A。

```text
Knowledge Source(KnowledgeSpec / seed 文档)
 → Ingestion Service(app/knowledge/ingestion.py)
    → Normalize + checksum(app/knowledge/chunker.py normalize)
    → Section-aware Chunking(app/knowledge/chunker.py chunk_document)
 → Knowledge Repository(app/db/repository.py)
 → Database(knowledge_documents / knowledge_chunks)
```

- **Document lifecycle**:`DRAFT → ACTIVE → ARCHIVED`(CHECK 约束 + Python enum,与业务表状态列同一约定)。仅 `ACTIVE` 是未来检索候选;`ARCHIVED`/`DRAFT` 保留记录但不会进入检索。仓库层以 `list_active()` 固化这一规则。
- **Document versioning**:唯一键 `(category, title, version)`;历史版本永不覆盖,演进 = 新建版本。`effective_date` 区分生效时间,seed 内含「退款政策 v1(2026-08-01,DRAFT)与 v2(2026-09-01,ACTIVE)」演示。(Decision 006)
- **Chunking**:确定性、章节感知——normalize(CRLF/行尾/空行)后以 `##`/`###` 为章节边界,超长章节按段落再按字符二次切分;纯标准库实现,接口可被未来 chunker 平滑替换。(Decision 007)
- **Metadata**:文档结构化字段(category / version / status / source / effective_date / language / checksum)+ 通用 `metadata` JSON;每个 chunk 携带来源文档的 metadata 快照(title/category/version/status/effective_date/source/language/section),支撑未来按 `category=refund, status=active, language=zh-CN` 过滤(过滤逻辑 Phase 3B 实现,不提前做)。
- **Source traceability**:`Document → Version → Chunk → Source` 可完整回溯——chunk 通过 `document_id` 关联文档,冗余快照保证即便版本被归档仍能引用与溯源。未来引用/引用生成(Phase 3C)直接使用该链路。
- **Ingestion service**:`IngestionService(session)` 持有 Normalize/Chunk 与 Repository,与 FastAPI 解耦(不建公开 ingestion HTTP API);提交在服务层,与 Phase 2 Service 约定一致。幂等:sha256(规范化文本)写入 `checksum`,同内容重复摄入为 no-op,同自然键不同内容抛 `KnowledgeIngestionError`。(Decision 008)
- **Repository boundary**:知识库数据访问只经 `KnowledgeDocumentRepository` / `KnowledgeChunkRepository`(get_by_natural_key / list_by_category / list_active / list_by_document),遵守 §11 调用链。
- **Embedding/Retrieval boundary**:`EmbeddingProvider.embed(texts)` 抽象已就位(`app/knowledge/embedding.py`)。Phase 3B 以本地确定性 dense 路径复用该抽象(见 §15);真实 embedding provider、`knowledge_chunks` 向量列与 pgvector 属后续且未实机验证(Decision 009 / 013)。

## 15. Retrieval Architecture(Phase 3B 落地)

检索层只产生「排序后的候选片段」,不生成答案:

```text
Query
 → Query Processing(NFKC / 空白归一 / 空查询校验;无 LLM rewrite)
 → Dense(向量相似度)+ Sparse(BM25)
 → RRF Fusion(k=60)
 → Ranked Candidates(typed,可溯源)
```

- **Dense retrieval**:`DenseRetriever` 抽象(`retrieve(query, top_k, filters)`),与 provider / 数据库解耦,不依赖未来 Agent。本地路径 `LocalDenseRetriever` 经 `EmbeddingProvider` 对「query + 候选 chunk 文本」编码后按 cosine 排序。⚠️ 本地桩是**词面重叠感知、确定性、非语义**的向量;语义质量需真实 embedding 模型(后续阶段)。
- **Sparse/BM25 retrieval**:纯标准库 Okapi BM25(k1=1.5,b=0.75);CJK 滑动字符二元组 tokenizer + 功能字二元组过滤 + 多词元最少共享门槛;单路候选带 `retrieval_methods=("bm25",)`。(Decision 011)
- **Hybrid retrieval / RRF**:两路独立检索后以 Reciprocal Rank Fusion 合并;同一 chunk 双路命中自动去重并标注 `("bm25","dense")`;分数为 rank-based RRF 值,不做跨尺度 raw score 相加。(Decision 010 / 012)
- **Metadata filtering**:typed `RetrievalFilter(status / category / language)`,无 DSL。
- **Active-version filtering**:Repository 单一边界默认 `status == ACTIVE`,ARCHIVED / DRAFT 不参与检索(除非显式内部覆盖 status=None);「退款政策 v1 ARCHIVED / v2 ACTIVE」由测试证明 v1 不会被静默召回。
- **Retrieval result schema**:`RetrievalCandidate`(chunk_id / document_id / title / category / version / section / content / score / rank / retrieval_methods / metadata)+ `RetrievalResult`(query / candidates / methods / latency_ms / filters);未生成自然语言引用。
- **Source traceability**:每个候选 chunk → document → version → source 完整可回溯(metadata 快照与 FK 双重可查)。
- **No-answer boundary**:无候选返回空 candidates(typed),不虚构答案;后续 Agent/RAG 层才决定 回答 / 追问 / 拒答 / 升级(§3 RAG 管线剩余部分)。
- **pgvector boundary**:`DenseRetriever` 为未来 pgvector 实现保留替换点;当前环境无 PostgreSQL/pgvector,数据库级向量检索与索引策略**未实测、不声称已验证**(Decision 013)。
- **Performance**:本地 dense 路径每次查询对当前 ACTIVE 语料做确定性全量打分(O(N),开发规模可接受);`RetrievalResult.latency_ms` 记录实际耗时用于衡量。生产路径(预计算向量 + pgvector ANN 索引)待后续阶段实测。
- **Internal API only**:只暴露 `RetrievalService.retrieve(...)`,不创建公开 customer-facing RAG 端点(Phase 4 再决定对外形态)。


## 16. Reranking + Context Assembly Architecture(Phase 3C 落地)

```text
Retrieval 找候选 → Reranker 排序 → Context Assembly 决定最终给模型什么

Hybrid Retrieval → Top 20(Candidate Set)
 → Reranking → Top 5
 → Context Assembly(去重 / ACTIVE 优先 / token budget / citation)
 → Final Context(typed ContextPackage)
 → Grounding Boundary → LLM(后续阶段)
```

- **Reranking 层**(\`app/retrieval/rerank.py\`):\`Reranker\` Protocol 为未来真实 reranker(Cross-Encoder / LLM-based / provider API)保留替换点;本阶段仅提供 \`DeterministicReranker\` —— 确定性、纯标准库的 **architecture/test implementation,明确不是语义 reranker**。打分 = lexical overlap + title bonus + section bonus + exact-term bonus + retrieval rank component + method-diversity bonus − per-document duplicate penalty;权重集中在 \`RerankWeights\`,重复检测以 (document_id, content) 为界,绝不把不同版本的相同措辞判为重复。(Decision 015 / 018)
- **两阶段 Top-K typed config**:\`RetrievalPipelineConfig\`(retrieval_top_k=20 → rerank_top_k=5,max_context_tokens=2000 / reserve_tokens),参数不散落;校验 rerank_top_k ≤ retrieval_top_k 等。(Decision 015)
- **Context Assembly 层**(\`app/retrieval/context.py\`):接收 reranked candidates,执行版本安全的去重(chunk_id;同文档同节同内容;不同版本不合并、不删除历史)与防御性 ACTIVE 优先(同 category+title+section 组;DRAFT/ARCHIVED 仅在该组无 ACTIVE 时兜底),再按 relevance 顺序整块装入 token budget,输出 \`ContextItem\` + \`ContextPackage\`(query / items / total_items / truncated / token_budget / estimated_tokens),每项保留 chunk_id / document_id / source_id / title / category / version / status / section / language / content / relevance_score / retrieval_methods 全量溯源。(Decision 016 / 017)
- **Token Budget 归属**:预算与 reserve 完全由 context 层(\`ContextBudget\`)控制;无 tokenizer 依赖时用确定性近似 \`estimate_tokens()\`(CJK≈1 字/token、ASCII≈4 字符/token,文档注明仅为 approximation),超预算整块停止、绝不截断到不可读;reserve 为未来 system/user prompt 与 answer 预留。(Decision 017)
- **Grounding Boundary**:Context Assembly 只决定「最终 Context 里有什么」;不编造答案、不自动补知识、不调用业务 API / 订单 / 退款 / 取消。静态知识走 RAG,动态业务事实在 Agent Phase 经 Tools 获取。
- **内部入口**:\`RetrievalPipeline.run(query)\`(FastAPI 无关);本阶段不创建公开 customer-facing RAG 端点。端到端链路 Query → Hybrid Retrieval → Rerank → Context 由测试覆盖(SQLite)。⚠️ PostgreSQL / pgvector 仍未实机验证(项目全套测试见 DEVELOPMENT_PLAN,Phase 4B 后共 273 例)。
## 17. Agent Workflow(Phase 4A 落地)

```text
User Message
 → UNDERSTAND(实体提取)
 → CLASSIFY_INTENT(Intent 与 Route 分离;见 §2)
 → ROUTE
     ├── RAG(REFUND_INQUIRY / KNOWLEDGE_QA)
     │     → 既有 RetrievalPipeline.run(query)→ ContextPackage(结构化保存于 AgentState.retrieved_context)
     ├── *TOOL(ORDER_STATUS / LOGISTICS_TRACKING / REFUND_REQUEST / CANCEL_ORDER / CREATE_TICKET)
     │     → ToolRequest → ToolExecutor 执行(ToolResult 回写 AgentState;见 §18)
     ├── CLARIFY(AMBIGUOUS / 缺订单号 / 多订单号——不猜单)
     └── ESCALATE(UNSUPPORTED / 用户要求转人工)
 → Response State(AgentResult)→ FINALIZE → END
```

- **AgentState / AgentResult / ToolRequest**(`backend/app/agent/state.py`):强类型 domain state,不依赖 LangGraph / FastAPI;`AgentResult`(Response State)包含 status / response / intent / route / citations / tool_requests / needs_clarification / escalation_required / error,自然语言最终回复由后续 LLM 阶段生成。(Decision 019 / 020)
- **Intent 分类**(`backend/app/agent/intent.py`):`IntentClassifier` Protocol + `DeterministicIntentClassifier`(规则 + 优先级 + ambiguity,明确为测试实现,非生产 NLP;未来 LLM classifier 可替换,Workflow 不感知具体模型)。Intent:KNOWLEDGE_QA / ORDER_STATUS / LOGISTICS_TRACKING / REFUND_INQUIRY / REFUND_REQUEST / CANCEL_ORDER / CREATE_TICKET / UNSUPPORTED / AMBIGUOUS。
- **实体提取**(`backend/app/agent/entities.py`):`EntityExtractor` + 确定性实现(order_id / tracking_number);多个不同订单引用不选择,交由 CLARIFY;user 身份来自会话上下文(`AgentState.user_id`),不从自由文本猜测。(Decision 023)
- **Router**(`backend/app/agent/router.py`):`WorkflowRouter` + `RuleBasedRouter`,只决定下一步(Intent ≠ Route),不含业务规则。示例:REFUND_INQUIRY → RAG,REFUND_REQUEST → REFUND_TOOL,UNSUPPORTED → ESCALATE,AMBIGUOUS → CLARIFY。
- **Workflow**(`backend/app/agent/workflow.py`):framework-agnostic 状态机;本阶段不引入 LangGraph(LangGraph 仍为目标编排运行时,后续经 adapter 将 `AgentState` 映射到 graph state)。RAG 分支只调用既有 `RetrievalPipeline`(`RetrievalRunner` 接口注入);业务分支创建 `ToolRequest`——未注入 ToolExecutor 时保持 Phase 4A 只规划行为,注入后经 §18 执行,Agent 层本身绝不直接访问 DB / Service。
- **边界**:Agent 层不 import SQLAlchemy / `app.db` / `app.services` / `app.api`(架构测试固化);不调用真实 LLM;业务工具只经注入的 ToolExecutor 执行(Agent → Tool → Service → Repository → Database);MCP / 风控 / HITL 属后续 Phase。
- **工具映射**(Phase 4B 实际执行;Phase 4A 仅接口):ORDER_STATUS→get_order、LOGISTICS_TRACKING→get_logistics、REFUND_REQUEST→check_refund_eligibility(eligible=True 才追加 create_refund;风控/HITL 属 Phase 5)、CANCEL_ORDER→cancel_order、CREATE_TICKET→create_ticket。
- **测试**:`tests/test_agent_intent.py`、`tests/test_agent_router.py`、`tests/test_agent_workflow.py`(含真实 RetrievalPipeline 集成 + Scenario A–D);Phase 4B 另加 `tests/test_tools.py` / `tests/test_tool_executor.py` / `tests/test_agent_tool_integration.py`(见 §18)。决策记录:DECISIONS 019–031;路线:DEVELOPMENT_PLAN Phase 4A / 4B。
## 18. Tool Execution Architecture(Phase 4B 落地)

```text
User
 ↓
Agent(Intent / Route → ToolRequest)
 ↓
Tool Registry(显式 allowlist)
 ↓
Tool Executor(Pydantic 校验 + trusted context + 错误归一化)
 ↓
Business Service(业务规则唯一归属)
 ↓
Repository
 ↓
DB
 ↓
ToolResult(稳定 domain schema + observability metadata)
 ↓
AgentState.tool_results → FINALIZE → Response State
```

- **Tool Registry**(`backend/app/tools/registry.py`):register / get / has / list;重复注册显式报错;未知工具返回稳定 `UNKNOWN_TOOL`。Registry 是安全 allowlist——禁止 `getattr` / `eval` / 动态 importlib 任意调用。
- **ToolExecutor**(`backend/app/tools/executor.py`):查 Registry → 用 Pydantic input schema 校验参数 → 以可信 `ToolExecutionContext`(request_id / session_id / user_id)执行 handler → 任何异常归一化为 `ToolResult`。`user_id` 只来自 trusted context,模型参数中的 `user_id` 一律被覆盖,无法越权(Decision 027)。
- **工具与授权**:六个工具(get_order / get_logistics / check_refund_eligibility / create_refund / cancel_order / create_ticket)全部经 Service 层调用;订单「存在」与「可访问」分离——跨用户查订单 / 物流 / 退款 / 取消返回 `UNAUTHORIZED_ORDER_ACCESS`(Decision 029)。
- **业务规则边界**:退款资格、权威退款金额、取消状态机、重复退款检测、工单引用校验全部保留在 Service 层,Tool 只做输入/输出归一化与执行期授权(Decision 029)。
- **ToolResult**(`backend/app/tools/base.py`):tool_name / status(SUCCESS / FAILED / VALIDATION_ERROR / NOT_FOUND / BUSINESS_ERROR)/ data(JSON-serializable output schema dump,不返回 ORM)/ error_code / error_message / metadata(execution_id / request_id / tool_name / duration_ms / success / requires_confirmation / risk_level)。错误码与既有业务错误对齐(ORDER_NOT_FOUND / ORDER_NOT_CANCELLABLE / DUPLICATE_REFUND …),不泄漏 traceback(Decision 030)。
- **Refund 条件执行**:REFUND_REQUEST → `check_refund_eligibility` → 仅 eligible=True 才追加并执行 `create_refund`;ineligible / 越权不触达退款 Service。
- **Cancellation**:`cancel_order` 继续使用现有 Service 状态机(DELIVERED / REFUNDED 不可取消、在途退款阻止取消);`requires_confirmation=True` 与 risk_level 仅作为 metadata 记录,完整 Risk Control / HITL 属 Phase 5。
- **一次 Tool Call first**:普通请求 = 一个 ToolRequest → 一个 ToolResult(循环为数据驱动结构,未来可扩展为 Tool Call → Result → reasoning → next Tool Call;决策 Decision 031)。
- **验证**:Phase 4B 新增 60 例测试(Registry / Validation / Authorization / 六工具 / Executor / Agent↔Tool 集成 / 端到端 DB),全套 273 例全绿;零新增依赖(Decision 025–031)。


## 19. Risk Control + Human-in-the-loop Architecture(Phase 5 落地)

Phase 5 MVP 把 Phase 4B 的执行链升级为:

```text
Agent(Intent / Route → ToolRequest)
 ↓
Risk Engine(ToolRequest + RiskContext → RiskDecision;纯分类,不触 DB)
 ↓
Risk Gate(按 RiskAction 分流)
 ↓
User Confirmation / Human Approval
 ↓
Tool Executor
 ↓
Business Service(业务规则唯一归属)
 ↓
Repository
 ↓
Database
 ↓
Verify(重查权威业务状态 → Final Response)
```

- **Risk 包**(backend/app/risk/):types(Level / Action / Decision / Context)、policy(规则 + `high_value_refund_threshold = 500`)、engine(纯分类)。等级 LOW / MEDIUM / HIGH / CRITICAL;动作 AUTO_EXECUTE / USER_CONFIRM / HUMAN_APPROVAL / BLOCK;未注册操作 fail-closed BLOCK。
- **Workflow Risk Gate**(backend/app/agent/workflow.py):`_execute_risk_gated` / `_execute_auto_or_confirmed` / `_wait_user_confirmation` / `_wait_human_approval` / `resume_after_approval`。LOW 直接执行;MEDIUM(取消)先等用户确认;HIGH / CRITICAL(退款)先建 PENDING 审批再停等;任何执行都只在确认/审批通过后进行。
- **审批模型**:`approval_requests` 表(Alembic `7a9c1e4b8d2f`,绑定 tool_name + tool_arguments 快照 + request_id / risk_level / reason / status / created_at / resolved_at / resolved_by);ApprovalService 状态机 PENDING → APPROVED / REJECTED;已处理再审批 → 409。
- **Approval API**(backend/app/api/routes/approvals.py):`GET /api/v1/approvals`(待审批)、`POST /api/v1/approvals/{id}/approve`、`POST /api/v1/approvals/{id}/reject`(404 不存在 / 409 已处理)。
- **Resume 语义**:approve 后从审批快照重建 ToolRequest 并执行(绝不重新让 LLM 生成参数);reject 返回 REJECTED 不执行;重复 resume / resume-after-reject 返回结构化错误。
- **Execute → Verify**(backend/app/services/verification.py):create_refund 后重查 DB(refund 存在 / status PENDING / amount == 订单权威 total_amount);cancel_order 后重查 order.status == CANCELLED;不符 → run_status = VERIFICATION_FAILED,不向用户报假成功。
- **金额边界**:退款金额只由 Service 从订单权威金额推导;工具输入 schema 不收 amount;RiskContext 只承载 Service 查询出的金额用于分级(Decision 036)。
- **Run 状态**:AgentRunStatus 增 WAITING_USER_CONFIRMATION / WAITING_HUMAN_APPROVAL / REJECTED / VERIFICATION_FAILED,与 AgentResultStatus 一一对应(可观测、可测试)。
- **验证**:新增 40 例测试(risk / risk_gate / approval_api / approval_flow),全套 313 例全绿;compileall 通过;零新增依赖。详情见 docs/RISK_CONTROL.md。