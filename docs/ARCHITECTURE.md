# Architecture — Enterprise AI Customer Service Agent

> 目标架构(尚未全部实现;Phase 1 骨架、Phase 2A 数据层、Phase 2B Mock Business API、Phase 2C 场景验证、Phase 3A 知识库接入、Phase 3B 混合检索、Phase 3C 重排 + 上下文组装、Phase 4A Agent Workflow 骨架、Phase 4B Tool Execution、Phase 5 Risk Control + Human-in-the-loop、Phase 6 MCP MVP、Phase 7A Frontend Demo、Phase 7B LLM Provider 与 Phase 7C Evaluation + Observability 已落地)。对应决策记录见 docs/DECISIONS.md,阶段拆分见 docs/DEVELOPMENT_PLAN.md。

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

- 提供 Customer Service MCP Server(`ecommerce-customer-service`),统一暴露售后域工具与上下文。
- 通过 MCP 标准化工具/上下文暴露;Agent 编排与业务工具服务解耦。
- **MCP 不负责 Agent decision logic**:决策(要不要调用、风险多高、怎么回复)在 Orchestrator 层完成。
- Phase 6 MVP 落地:本地 stdio MCP Server 只暴露 `get_order` / `get_logistics` / `create_ticket`;`create_refund` / `cancel_order` 等高危操作保留在内部 Tool Executor + Risk Gate,不通过 MCP 暴露(见 §20 与 docs/MCP.md)。

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

评测分层(目标):Retrieval / Generation / Agent / Tool / Product 五层。

- Phase 7C 已落地 Agent / Tool 层**固定数据集评测 MVP**(`backend/app/evaluation/`):9 类场景 11 个 case,七项指标(Intent / Entity / Route / Risk / Approval / Execution / Verification),带显式预期才计分,不适用标 N/A;详见 §23.2。
- Retrieval 层评测(Recall@K / NDCG)与 Product 指标(退款成功率 / 一次解决率 / 人工介入率)保留至后续阶段(Phase 8 / PRD §9)。

## 9. Observability

端到端 trace 目标:

```text
request → model → state → retrieval → tool → result → answer
```

记录模型调用、状态流转、检索引用、工具结果、风控/审批事件。

- Phase 7C 已落地**单机事件投影 MVP**:每个 run payload 的 `steps`(Understand / Route / tool / Risk Gate / Human Approval / Execute / Verify / Finalize)携带 status / summary / provider / result_data,AgentState 记录每次 Risk Gate 判定(`risk_decisions`);详见 §23.3。
- 生产级 distributed tracing / 指标平台保留 Phase 8;不引入 OTel / Kafka / Prometheus(面试 Demo 边界)。

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


## 20. MCP Integration(Phase 6 落地)

Phase 6 在既有 Tool Execution(§18)与 Risk Control / HITL(§19)之上叠加标准化工具暴露,不推翻任何既有组件。

Internal Tool(非 MCP):

```text
Agent
 ↓
Tool Registry → Tool Executor
 ↓
Business Service(业务规则唯一归属)
 ↓
Repository → DB
```

MCP Tool:

```text
Agent
 ↓
Tool Provider(MCPToolAdapter)
 ↓
MCP Client(stdio,list_tools / call_tool)
 ↓
MCP Server(ecommerce-customer-service)
 ↓
Business Service(业务规则唯一归属)
 ↓
Repository → DB
```

共同点:两条路径最终都进入 Service 层——业务规则唯一归属不变(Decision 029)。

- **只暴露 3 个 MCP Tool**:`get_order` / `get_logistics` / `create_ticket`;其余工具(REFUND / CANCEL / eligibility)继续走内部 Tool Executor。
- **Risk Gate 先于 MCP**:Workflow 先经 RiskEngine 分级,再把执行交给 tool provider;adapter 只在 LOW / 已确认 / 已审批后才可能触达 MCP Client——MCP 不能绕过 Phase 5。
- **Refund / Cancel 不通过 MCP**:MCP 没有 `create_refund` / `cancel_order` / `check_refund_eligibility`;资金字段被 schema 排除,越权访问被授权检查拒绝(Decision 038)。
- **服务边界**:MCP Tool handler → Service → Repository → DB;handler 不直接写业务 SQL;业务失败以 JSON envelope 返回,服务端异常由兜底边界归一化,不泄漏内部细节。
- **错误归一化**:MCP Client 把 tool not found / invalid arguments / server error / malformed result 映射为内部 `ToolResult`(status + `MCP_*` 错误码),SDK exception 不进入 Agent 层(Decision 039)。
- **实现**:`backend/app/mcp/`(tools.py / server.py / client.py / adapter.py);stdio 本地传输;一次调用一条短生命周期连接;仅新增官方依赖 `mcp==2.1.1`(Python 3.13.14 兼容)。
- **验证**:新增 24 例 MCP 测试(server 6 / client 10 / adapter 8),全套 337 例全绿;`compileall` 通过。详见 docs/MCP.md。

## 21. Frontend Demo Layer(Phase 7A 落地)

Phase 7A 的目的不是扩展后端能力,而是把「已完成的 Agent 能力」变成面试现场可演示的产品 Demo。三个前端页面(`/chat` / `/console` / `/evaluation`)只是**展示层**,不承担任何 Agent 决策逻辑。

```text
Chat UI · Console UI · Evaluation UI(Next.js + TypeScript,展示层)
        │  fetch("/api/v1/...")
        ▼
Next dev server rewrites(frontend/next.config.mjs,/api/v1 → http://127.0.0.1:8000)
        ▼
FastAPI /api/v1/demo/*(backend/app/demo/,薄层:只做编排与 JSON 投影)
        ▼
AgentWorkflow → RetrievalPipeline / MCPToolAdapter / RiskEngine
        ▼
ApprovalService → ToolExecutor(内部)/ MCP(stdio)→ Business Service
        ▼
Repository → Database(demo.db,SQLite)
```

- **前端不硬编码业务结果**:订单状态、物流、退款金额、知识来源全部来自后端真实 API;订单 ID 取自 demo seed(ORD-1001 / ORD-1002 / ORD-1003),不在 UI 猜测。
- **前端不做风险 / 审批决策**:RiskEngine 分级、是否需要用户确认 / 人工审批、退款金额、执行与校验全部由后端决定;前端只把真实返回值呈现给用户与运营。
- **`/chat`** 展示会话 + 右侧 Agent Trace(Intent / Route / RAG 来源 / Tool Provider / Risk / Approval / Verification / Final Response),让决策过程可理解(例如「为什么没有直接退款」→ 高风险 → 人工审批)。
- **`/console`** 展示 Approval Queue、Risk Decision、Agent Action、订单权威状态与金额,提供 Approve / Reject;审批通过后展示 Resume → Execute → Verify 结果。
- **`/evaluation`**(Phase 7C)运行固定数据集评测:执行真实 Agent 链路(隔离临时 DB),展示 Total / Passed / Failed、Intent / Entity / Route / Risk / Approval / Execution / Verification 七项指标与逐 case Expected / Actual,**不伪造**指标(详见 §23)。
- **约束**:Demo 层不重写 AgentWorkflow / RiskEngine / ToolExecutor / MCP / HITL;会话与审批投影为进程内存储,演示用单 worker 后端;不把业务规则写进 UI。
## 22. LLM Provider Layer(Phase 7B 落地)

```text
User → LLM(理解)→ Agent Workflow → RAG / Tool / MCP
     → Risk Gate → User Confirmation / Human Approval
     → Execute → Verify → LLM Final Response(仅基于权威证据)
```

- **LLM ≠ Business Logic**:真实 DeepSeek 只做两件事——① 意图 + 参数理解(结构化输出,Pydantic 校验,失败回退);② 把 workflow 已收集的权威证据(RAG 知识 / ToolResult)转成自然语言最终回复。它不查询数据库、不调用 Repository、不修改订单 / 退款、不创建审批、不调用 MCP、不执行工具。
- **抽象层**(`backend/app/llm/`):`base.py`(`LLMProvider` Protocol,OpenAI-compatible 消息)、`deepseek.py`(`DeepSeekProvider`,httpx 轻量客户端,仅调用 `/chat/completions`)、`prompts.py`(集中 System Prompt,无任何 secret)、`nlu.py`(严格 JSON → Pydantic,失败即回退)、`respond.py`(最终回复生成,无证据不调用)、`errors.py`(统一错误:LLM_TIMEOUT / LLM_AUTH_ERROR / LLM_RATE_LIMITED / LLM_PROVIDER_ERROR / LLM_INVALID_OUTPUT / LLM_CONFIG_ERROR)。
- **Deterministic 保留**:`LLM_ENABLED=false`(默认)或未配置 Key 时,`/chat` 走既有确定性 IntentClassifier / 规则路由 / Risk / Approval / Execute / Verify;任何一次 LLM 失败都回退确定性路径——高风险动作「宁可不执行,也不自动执行」。
- **边界不变**:LLM 输出是 untrusted proposal;Risk Gate、用户确认、人工审批、Tool Executor / MCP Adapter 与权威 Business Service 仍是唯一执行与授权来源(Decision 040 / 041)。
- **安全**:API Key 只存在于请求头,不进入日志、Trace、API 响应、异常或前端;Key 由 `Settings`(环境变量 / `.env`)提供,`.env` 已被 .gitignore 忽略。


## 23. Evaluation + Observability(Phase 7C 落地)

### 23.1 执行与授权链(面试口径)

```text
LLM(理解 / 提议,untrusted)
 ↓
Agent Workflow
 ↓
RAG / Tool / MCP(能力来源)
 ↓
Risk Gate(风险分级 + 门禁)
 ↓
User Confirmation / Human Approval(HITL)
 ↓
Business Service(确定性业务规则,唯一授权来源)
 ↓
Repository
 ↓
Database
 ↓
Verify(重查权威业务状态)
 ↓
Final Response
```

- **LLM 是不可信执行器。** LLM 只负责「理解」和「提议」(意图 / 实体 / 措辞);它不能直接修改数据库、不能绕过 Risk Gate、不能绕过 Approval、不能决定业务规则。任何一次 LLM 失败都安全回退到确定性流程,高风险动作「宁可不执行,也不自动执行」。
- **Risk Gate 与 Business Rule 明确区分**:Risk Gate 只回答「这个操作要不要用户确认 / 人工审批」;Business Service 只回答「这个业务操作当前是否真的允许」。两者独立、可单独测试。
- **Verify**:执行后重新读取权威业务状态(退款单存在且金额等于订单权威总额、订单状态确实变为 CANCELLED),不信任工具自报成功。

### 23.2 Evaluation(轻量 MVP)

- 固定数据集:`backend/app/evaluation/dataset.py`(9 类场景、11 个 case,含 Prompt Injection 与「不要人工审批」越权指令)。
- Runner:在隔离临时 SQLite 上跑与 `/demo/chat` 完全相同的 `run_chat` / `finalize_approval`;`use_llm=false` 确定性离线,`use_llm=true` 走真实 DeepSeek。
- 指标:Intent / Entity / Route / Risk / Approval / Execution / Verification 七项准确率;带显式预期的维度才计分,其余标记 N/A。
- API:`GET/POST /api/v1/demo/evaluation/cases|run`;前端 `/evaluation` 展示 Summary + 指标 + Case Table(Expected vs Actual)。

### 23.3 Observability / Trace(单机、非生产级)

```text
Request → Understand(Intent)→ Route → RAG / Tool / MCP
        → Risk Gate → Approval → Execute → Verify → Final Response
```

- 每个 Agent 请求的 payload 即事件投影:`request_id / session_id / user_id / created_at / updated_at` + `steps`(Understand / Route / tool / Risk Gate / Human Approval / Execute / Verify / Finalize)+ `risk / approval / approval_resolution / sources / llm`。
- AgentState 记录每次 Risk Gate 判定(`risk_decisions`),步骤带 status / summary / provider / result_data。
- 边界:进程内存储、单 worker;禁止写入 API Key / Authorization / secret / 非必要个人信息;无分布式 tracing(保留 Phase 8)。

## 24. After-Sales Domain Model(Phase 9A 落地)

```text
AfterSalesCase (售后案件;Phase 9A 只落地数据对象 + 最小 CRUD)
  case_id / user_id / order_id(可空) / case_type / requested_action
  problem_description / status / risk_level
  collected_information(JSON) / missing_information(JSON) / ai_summary
```

- **定位**:一个 `AfterSalesCase` = 一次完整售后处理案件(从信息收集到完成 / 拒绝 / 转人工)。Phase 9A 只建立该数据对象与最小 CRUD(`create_case / get_case / update_case / list_cases`);不接入 Agent / Intent / RAG / Tool Executor / MCP / Risk Gate / HITL / 前端,不实现自动退款或自动换货。
- **状态机**:`INFORMATION_COLLECTION → ELIGIBILITY_CHECK → PROCESSING → COMPLETED`;`PENDING_HUMAN` 表示转人工,`REJECTED` 表示终态(业务不允许)。
- **与 Ticket 的区别**:`Ticket` 是「人对人的工单」(分类 / 优先级 / 处理状态);`AfterSalesCase` 承载 AI 参与的售后流程状态、结构化已收集 / 缺失信息与转人工摘要(Decision 043)。
- **风控词表复用**:`risk_level` 复用 Phase 5 `RiskLevel`(LOW / MEDIUM / HIGH / CRITICAL),以 `String(20)` 存储,与 `approval_requests` 一致,不定义第二套枚举。
- **边界**:退款资格、金额、换货、风控分级、人工审批与 LLM 摘要留给后续 9B~9E;本层只负责案件数据本身。Service 拒绝非法枚举值 / risk level / JSON 载荷。

## 25. After-Sales Case Agent Integration(Phase 9B 落地)

```text
POST /api/v1/demo/chat
  -> AgentWorkflow.execute
       UNDERSTAND
       -> CLASSIFY_INTENT
       -> CASE_MANAGEMENT (只在意图为 UNSUPPORTED / AMBIGUOUS 时运行)
            DeterministicAfterSalesCaseDetector -> AfterSalesSignal
            AfterSalesCaseManager.handle(user_id, session_id, active_case_id, message, entities)
              -> AfterSalesService (create_case / update_case)
              -> collected_information / missing_information
              -> INFORMATION_COLLECTION | ELIGIBILITY_CHECK
       -> ROUTE (既有 RAG / *TOOL / CLARIFY / ESCALATE 分支完全不变)
  -> _with_llm_response -> build_run_payload(含 case 字段) -> DemoRunStore
```

- **门控规则**:Case 管理步骤只在既有分类器无法处理的意图(`UNSUPPORTED` / `AMBIGUOUS`)上运行,这样 RAG / Order / Logistics / Cancel / Refund / Ticket 的既有 intent -> route 行为与执行逻辑完全不变(Decision 044)。售后处理请求("我的耳机坏了")此前正是 UNSUPPORTED -> ESCALATE 的一类。
- **识别规则(确定性)**:`is_case_request` = 命中质量 / 物流争议类问题标记,或换货 / 维修诉求标记;单独的退款短语不算案件请求(退款请求继续走既有 REFUND_REQUEST 流程)。明确要求转人工的消息仍走 ESCALATE,不建案件。
- **信息收集规则**:三项信息分别判定 - `order_id`(用户是否给出订单引用)、`requested_action`(换货 / 维修 / 退款,`UNKNOWN` 视为缺失)、`problem_description`(是否真的描述过问题)。任一项缺失 -> `INFORMATION_COLLECTION` 并向用户追问缺失项;三项齐备 -> `ELIGIBILITY_CHECK`(本阶段只改状态,不执行资格判断)。
- **业务事实边界**:`AfterSalesCase.order_id` 只在订单确实存在时写入(经 OrderRepository 解析);用户给出但系统中不存在的引用(如 `ORD-1004`)只保存为 `collected_information.order_ref`。LLM 输出至多是输入,永远不是业务事实。
- **编排边界**:`AfterSalesCaseManager` 只依赖 `AfterSalesService`;不调用 RefundService / RiskEngine / LLM / MCP,不执行任何写业务动作。Case Service 异常在 workflow 内被捕获并降级到确定性路径(聊天不中断、不返回 500)。
- **Session 关联**:不新增数据库字段。demo 层用 `DemoRunStore` 中同一 session 最近一次带 case 的 run 作为 session -> case 的最小链接(进程内);复用前重新校验案件存在 / 归属用户 / 状态可继续(仅 `INFORMATION_COLLECTION` / `ELIGIBILITY_CHECK`)。
- **展示层**:`build_run_payload` 增加 `case` 字段与 `Case Upsert` / `Information Collection` 时间线步骤;`build_text` 按 `missing_information` 生成确定性的追问或下一步说明,不经过 LLM,也不声称任何未执行的动作。

## 26. After-Sales Investigation + Eligibility（Phase 9C 落地）

### 26.1 四类职责（Grounding Boundary）

| 角色 | 负责 | 不负责 |
| --- | --- | --- |
| LLM | 自然语言理解（案件类型 / 诉求 / 问题描述） | 业务事实、政策结论、`eligible` |
| Business Tool / Service | 权威业务事实（订单是否存在 / 归属 / 状态 / 商品可退性 / 金额 / 在途退款 / 签收参考时间） | 政策解释与资格结论 |
| RAG（既有 Hybrid Retrieval） | 政策证据 + citation + 原文摘录 | 业务事实与最终资格结论 |
| Eligibility Engine（确定性） | 最终资格结论 + failed rules + reason | 金额计算与退款 / 换货执行 |

### 26.2 调用链

```
workflow  Understand -> Case Upsert -> CASE_INVESTIGATION -> Finalize
                    (only when the case reached ELIGIBILITY_CHECK)

service   AfterSalesInvestigationService.investigate(case)
            |- Order Investigation  -> OrderRepository / LogisticsRepository /
            |                          RefundRepository -> BusinessFacts
            |- Policy Investigation -> RetrievalPipeline.run(query) -> PolicyFacts
            '- EligibilityEngine.evaluate(case, business, policy) -> EligibilityResult
          -> AfterSalesService.update_case(status / collected_information / ai_summary)
```

分层边界：`app/agent/` 只依赖注入的 `CaseInvestigatorLike` Protocol（不 import SQLAlchemy / Repository / Service；`tests/test_after_sales_eligibility.py` 含源码级架构守卫）；`app/after_sales/` 是纯领域层（无 DB、无 LLM）；数据库访问集中在 `app/services/after_sales_investigation.py`。

### 26.3 规则、状态与业务红线

- 规则顺序固定（`EligibilityEngine.rules`）：`order_available` -> `order_owned_by_user` -> `policy_evidence` -> `policy_covers_action` -> `order_status_delivered` / `no_active_refund` / `items_returnable` -> `after_sales_window` -> `exchange_branch`。
- `eligible` 三态：`True` -> `PROCESSING`；`False` -> `REJECTED`；`None` -> `INFORMATION_COLLECTION`（缺权威信息）或保持 `ELIGIBILITY_CHECK`（政策未覆盖 / 缺时效信息，`requires_human_review=True`）。
- 业务红线：订单不存在 / 订单不属于当前用户 / 缺少订单号属于**调查失败或信息问题**，永远返回 `eligible=None` + `missing_information`，而不是 `eligible=False`。
- 签收参考时间：`orders` 表没有 `delivered_at`。规则为 DELIVERED 物流记录优先，否则使用 DELIVERED 订单的 `updated_at`，并把取值来源写入 `business_facts.delivery_reference_source`（不推测、不臆造）。
- 时效窗口：由检索到的政策文本解析（如「签收后十五天内」-> 15 天）并保留 `window_citation`；demo 种子数据的签收时间以可注入的 `now` 为锚点相对生成（`seed_demo_orders(session, now=...)`，见 Decision 050），线上 bootstrap 传真实时钟因此签收时间始终落在窗口内；测试与 Evaluation 通过注入固定锚点 / `reference_time` 固定窗口，避免结果随真实时间漂移。
- 金额：资格判定不计算金额。退款金额仍由业务系统（RefundService）在真正执行时给出。

### 26.4 Demo 与 Observability

`/api/v1/demo/chat` payload 新增 `eligibility` / `investigation`；timeline 复用既有机制，新增 `Order Investigation` / `Policy Retrieval` / `Eligibility Check` 步骤（不在前端新增第二套 trace）。`/chat` 可看到 Case ID / 案件状态 / 订单调查 / 政策依据 / 资格结果；不暴露任何内部敏感信息。前端无需改动。

### 26.5 明确未实现

退款 / 换货 / 维修的**执行**、售后金额计算、人工审批流的扩大、真实 Embedding / pgvector 的语义政策检索、规模化评测与生产级可观测性仍属后续 Phase（9D 及以后 / Phase 8）。

## 27. After-Sales Treatment Plan + Ticket Creation（Phase 9D 落地）

### 27.1 职责边界（本阶段新增的两个角色）

| 角色 | 负责 | 不负责 |
| --- | --- | --- |
| Eligibility Engine（9C，既有） | 判断「能不能处理」：三态 `eligible` | 决定具体动作、创建工单 |
| **Treatment Planner**（9D，纯领域 `app/after_sales/treatment.py`） | 按用户诉求 + eligibility + 业务规则生成受约束的 `TreatmentPlan` | 执行动作、访问数据库、调用 LLM |
| **Ticket Service / AfterSalesTreatmentService**（9D，服务层） | 经既有 `TicketService` 创建 / 复用售后工单并写回案件 | 执行退款 / 换货 / 维修、修改 Risk / HITL |
| Risk Gate / HITL / Execute / Verify（5 / 7C，既有） | 真正的执行授权与执行后校验 | 本阶段完全不参与 |

### 27.2 调用链

```
HTTP / Chat
  -> AgentWorkflow
       Understand -> CASE_MANAGEMENT -> CASE_INVESTIGATION -> CASE_TREATMENT -> FINALIZE
                                                              (only eligible is True
                                                               and case status PROCESSING)

  CASE_TREATMENT -> AfterSalesTreatmentService.plan_and_register(case, eligibility)
        |- TreatmentPlanner.plan(case_facts, EligibilitySummary) -> TreatmentPlan
        |- TicketRepository.list_by_case(case.id)   # idempotency lookup
        |- TicketService.create_ticket(..., case_id=case.id) -> Repository -> DB
        '- AfterSalesService.update_case(collected_information["treatment_plan"])
```

数据关系:`after_sales_cases 1 —— N tickets`(`tickets.case_id` 可空 FK + 索引,迁移 `8b1f3c5d7e90`)。普通支持工单 `case_id` 为 NULL,行为完全不变。

### 27.3 TreatmentPlan 与状态语义

- 字段:`action` / `reason` / `case_id` / `case_status` / `case_type` / `order_id` / `order_ref` / `required_next_step` / `requires_execution` / `requires_human_review` / `executable` / `ticket_id`(工单创建成功后的 Ticket ID,未创建为 `null`) / `ticket_category` / `policy_citations`。
- `action ∈ {REFUND, EXCHANGE, REPAIR}` 且必须等于 `case.requested_action`;否则 `action=None`、`executable=False`、`requires_human_review=True`(Decision 047)。
- 案件状态语义不变:`PROCESSING` = 「售后处理任务已建立,可以进入后续执行」;**不是** `COMPLETED`(真正的业务变更尚未发生)。
- 处理方案存放于 `after_sales_cases.collected_information["treatment_plan"]`(含 ticket 块与 `ticket_registered` / `ticket_error`),复用既有 JSON 列,不新建第二张表。

### 27.4 幂等与失败语义

- 创建前先按 `case_id` 查询既有工单:存在即复用(`created=false`,`count=N`),只查不建,保证「同一 Case 只有 1 个 Ticket」。
- 工单创建失败:捕获异常并记录到 `treatment_plan.ticket_error`,`ticket_registered=false`,返回值带明确 `error`,案件状态保持 `PROCESSING`;**绝不**向用户声称工单已创建,也绝不伪造 ticket_id。
- 工单描述使用结构化模板(售后类型 / 申请动作 / 问题描述 / 订单 + 订单状态 / 资格判断 / 政策依据 / 下一步),业务事实全部来自已有调查结果与检索 citation。

### 27.5 Demo 与 Observability

`/api/v1/demo/chat` payload 新增 `treatment` / `ticket`;timeline 复用既有机制,在 `Eligibility Check` 之后新增 `Treatment Plan` / `Ticket Creation` 两个步骤,`/chat` 可见 Case ID / Eligibility / Treatment Action / Ticket ID / Case status,前端无需改动。售后时效按真实时钟判定,但 demo 种子数据的签收时间以可注入的 `now` 为锚点相对生成(线上 bootstrap 传真实时钟,见 Decision 050),因此新种子库上的线上 demo 始终落在 15 天窗口内;脚本化 demo / 评测可通过可选 `reference_time` 固定窗口(若 demo DB 被长期持久化超过窗口,需重建种子库)。评测 runner 为**每个 case 单独准备一次性种子数据库**,避免跨 case 状态泄漏(共享 DB 时,退款 case 留下的在途退款会让后续 ORD-1003 售后 case 命中 `no_active_refund` 而误判 REJECTED)。

### 27.6 明确未实现

退款 / 换货 / 维修的**执行**、售后金额计算、人工复核与审批扩大、售后执行后的 Verify、真实 Embedding 语义政策检索与规模化评测仍属后续 Phase(9E / Phase 8)。本阶段没有触发任何真实业务变更。

## 28. After-Sales Execution + Verify（Phase 9E 落地）

### 28.1 职责边界（本阶段新增的一个角色）

| 角色 | 负责 | 不负责 |
| --- | --- | --- |
| Treatment Planner / Ticket Service（9D，既有） | 生成受约束的 `TreatmentPlan`、创建 / 复用售后工单 | 执行任何业务动作 |
| **AfterSalesExecutionService**（9E，服务层） | 把一次 Risk Gate → Execute → Verify 的结果写回 Case；**只在重新读取并校验业务状态后**才允许 `COMPLETED` | 执行退款、决定风险等级、计算金额、写业务表 |
| Risk Engine / Risk Gate（5，既有） | 决定是否允许执行（LOW / MEDIUM / HIGH / CRITICAL，规则不变） | 发起业务写操作、决定金额 |
| ApprovalService / approval_requests（5，既有） | 高风险的人工审批与 Resume | 决定金额或资格 |
| Tool Executor（4B，既有） | 实际调用工具 | 判定业务是否可以执行 |
| RefundService / RefundRepository（2B，既有） | 唯一的退款实现与权威业务事实 | 决定是否需要审批 |
| BusinessVerifier（5，既有） | 执行后重新读取权威业务状态并校验 | 发起业务写操作 |

### 28.2 调用链

```text
TreatmentPlan(action=REFUND, eligible=true, executable=true)
  -> AgentWorkflow._run_case_execution
       |- Case status must be PROCESSING (COMPLETED / REJECTED: no execution)
       |- ToolRequest(check_refund_eligibility, {order_id, case_id})
       |- Risk Gate (RiskEngine / RiskPolicy)                 # BEFORE any write
       |    |- HUMAN_APPROVAL -> ApprovalService -> approval_requests (PENDING)
       |    |                       -> Case PENDING_HUMAN, execution PENDING_APPROVAL
       |    '- AUTO_EXECUTE   -> continue
       '- ToolExecutor -> RefundService.create_refund -> RefundRepository -> DB
            '- BusinessVerifier.verify (re-reads the refund row)
                 |- passed  -> record_execution(COMPLETED)
                 |              '- re-reads the refund row AGAIN before writing COMPLETED
                 '- failed  -> execution VERIFICATION_FAILED, Case PENDING_HUMAN

Human decision (approve / reject)
  -> AgentWorkflow.resume_after_approval(approval_id)
       |- reject  -> execution REJECTED, Case PENDING_HUMAN, nothing written
       '- approve -> replay the FROZEN ToolRequest snapshot (case_id included)
                     -> ToolExecutor -> RefundService -> BusinessVerifier
                     -> Case COMPLETED (or PENDING_HUMAN on failure)
```

`AfterSalesExecutionService` 是唯一写 Case 执行结果的组件；`AgentWorkflow` 只依赖注入的 `AfterSalesExecutionRecorderLike` Protocol，Agent 层仍然不 import SQLAlchemy。

### 28.3 状态语义

| Case status | 含义 |
| --- | --- |
| `PROCESSING` | 售后任务已建立（9D），等待执行 / 正在执行 |
| `PENDING_HUMAN` | 已发起执行或需要人工决定：等待人工审批、执行失败、校验失败、或换货 / 维修无真实业务系统 |
| `COMPLETED` | **仅**在重新读取并校验业务状态成功后写入（退款记录真实存在且与订单匹配） |
| `REJECTED` | 资格被拒 / 业务约束拒绝（例如 `no_active_refund`） |

### 28.4 Execute ≠ Verify

- **Execute** 复用既有 `create_refund` 工具（经既有 Risk Gate 与 Tool Executor），调用的是既有 `RefundService`，不新增第二套退款实现；
- **Verify** 由 `BusinessVerifier` 重新读取数据库，而不是相信工具返回的 `success`；
- `AfterSalesExecutionService.record_execution` 在写 `COMPLETED` 前会独立地**再次**按 id 读取退款行：没有通过的 verification → `EXECUTION_NOT_VERIFIED`；读不到退款行 → `EXECUTION_REFUND_MISSING`。两者都会阻止 Case 完成。

### 28.5 幂等与失败语义

- **重复执行**：既有 `RefundService` 的在途退款约束拒绝第二笔，案件转为 `REJECTED`（`failed_rules=["no_active_refund"]`），退款总数保持 1；已 `COMPLETED` 的案件不会再次进入执行分支；
- **Execute 失败**：`execution.status=FAILED`，`refund_id` 为 `null`，无退款行，Case 转 `PENDING_HUMAN`，绝不声称完成；
- **Verify 失败**：`execution.status=VERIFICATION_FAILED`，`verification.passed=false`，写操作可能已发生但 Case **不得** `COMPLETED`，转人工复核；
- **人工拒绝**：`execution.status=REJECTED`，没有任何写操作；
- **换货 / 维修**：`execution.status=NOT_IMPLEMENTED` + `next_step=HUMAN_HANDOFF`，或资格未覆盖时保持 `ELIGIBILITY_CHECK`，均不伪造成功。

### 28.6 Demo 与 Observability

`/api/v1/demo/chat` payload 新增 `execution` / `verification`；timeline 在真实的 `Risk Gate` / `Approval` / `Execute` / `Verify` 发生时才展示对应步骤，未发生的步骤不展示。文案如实区分「等待人工审批 / 已拒绝 / 执行失败 / 校验未通过 / 未执行」与「已完成（已由数据库权威状态校验）」。

### 28.7 明确未实现

换货 / 维修的真实执行、真实支付 / 银行 API、售后金额与库存联动、LLM 语义 Grounding 验证器、向量数据库（pgvector）与规模化评测仍属后续 Phase（9F / Phase 8）。本阶段只让 REFUND 真正执行，且只有一笔 `RefundStatus.PENDING` 记录，不涉及任何真实资金流动。
