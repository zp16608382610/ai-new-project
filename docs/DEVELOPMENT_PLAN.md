# Development Plan

> Enterprise AI Customer Service Agent 开发路线。状态同步 README「Current / Next Phase」。

## Phase 状态

- **Phase 1 — Foundation:completed**
- **Phase 2A — Mock Business System · Database & Data Model:completed**
- **Phase 2B — Mock Business System · Mock Business API:completed**
- **Phase 2C — Business Scenario Tests:completed**
- **Phase 3A — RAG · Knowledge Base & Ingestion:completed**
- **Phase 3B — RAG · Retrieval:completed**
- **Phase 3C — RAG · Reranking + Context Assembly:completed**
- **Phase 3D — RAG · Knowledge Management & Integration:not started**
- **Phase 4A — Agent · Workflow(State / Intent / Routing / RAG branch / ToolRequest interface):completed**
- **Phase 4B — Agent · Tool Execution:completed**(工具执行框架与售后域工具接入完成,原「Phase 5 — Tools」范围并入)
- **Phase 5 — Risk Control + Human-in-the-loop:completed**
- **Phase 6 — MCP:completed**(MCP MVP:内部 Tool Executor + MCP 工具并存;只暴露 get_order / get_logistics / create_ticket)
- **Phase 7A — Frontend Demo Workbench:completed**(把已完成 Agent 能力做成可现场演示的产品 Demo:/chat + /console + /evaluation,前端纯展示层、真实调用后端 API)

- **Phase 7B — Real DeepSeek LLM Integration:code completed / real API verification pending**(真实 DeepSeek 接入代码完成:LLM 只做意图/实体理解与最终回复生成,业务事实与风险执行仍由确定性 RAG / Tool / Risk / HITL 控制;默认 LLM_ENABLED=false;真实 DeepSeek 在线验证待配置 DEEPSEEK_API_KEY 后进行)


- **Phase 7C — Evaluation + Observability + Final Demo Packaging:planned**(下一阶段:五层评测与回归流程、端到端可观测与行为审计,以及最终演示打包 / 部署准备 / 验收与收尾)

## Phase 1 — Foundation [COMPLETED]

已完成:

- 仓库骨架:`backend/`、`frontend/`、`tests/`、`docs/`、`docker-compose.yml`
- 后端 FastAPI 基础服务:健康检查 `/api/v1/health`、环境变量配置、基础 logging、API 版本前缀、基础错误处理
- 前端 Next.js + TypeScript 页面占位:`/`、`/chat`、`/console`、`/evaluation`
- 测试基建:health 测试通过
- 工程规范与文档:AGENTS.md、PRD.md、ARCHITECTURE.md、DECISIONS.md、README.md
- docker-compose(PostgreSQL + Redis)已编写;⚠️ 本机无 Docker,未实机验证

说明:CI、密钥管理、生产级可观测性基座等未在本阶段落地,后续按需补齐(避免虚假宣称)。

## Phase 2A — Mock Business System · Database & Data Model [COMPLETED]

已完成(本阶段只做数据层,不提供 HTTP 业务 API):

- 定义电商售后领域对象与数据模型:用户、商品、订单、订单明细、物流、退款单、售后工单(7 张业务表)
- SQLAlchemy 2.x 数据模型 + Alembic 初始迁移(全量建表已在新库验证;状态列使用 CHECK 约束)
- 幂等确定性 seed:3 用户 / 8 商品 / 10 订单 / 12 明细 / 6 物流 / 3 退款 / 3 工单,覆盖 6 种订单状态、可退款与不可退订单、物流异常、退款 PENDING / COMPLETED / REJECTED
- Repository 数据访问层(Agent → Tool → Service → Repository → Database;禁止上层直接写 SQL)
- 数据层测试 9 例(FK 约束、状态 CHECK、seed 一致性与业务断言)+ health 测试;运行于内存 SQLite

说明:⚠️ PostgreSQL 链路仅以 SQLite 验证,本机无 Docker,未对真实 PostgreSQL 实机验证。

## Phase 2B — Mock Business System · Mock Business API [COMPLETED]

已完成(调用链:HTTP API → Service → Repository → Database;不含风控/HITL,属 Phase 5):

- Pydantic schemas 层(`app/schemas`):请求/响应契约,与 ORM 分离;金额统一序列化为 JSON number
- Service 层(`app/services`,框架无关):订单查询、物流查询、用户订单列表(可按 status 过滤)、退款资格判定、创建退款申请(PENDING;金额由订单权威数据推导,禁止客户端指定)、取消订单状态机(仅 PENDING / PAID / SHIPPED 且无在途退款可取消)、工单创建(校验 user/order 引用)
- API 层(`app/api/routes`,统一前缀 `/api/v1`):`GET /orders/{order_id}`、`GET /orders/{order_id}/logistics`、`GET /users/{user_id}/orders`、`POST /refunds/check-eligibility`、`POST /refunds`、`POST /orders/{order_id}/cancel`、`POST /tickets`
- 统一错误结构 `{"status": "error", "code": ..., "detail": ...}`:404 资源不存在 / 409 冲突(重复退款、重复取消)/ 422 业务不允许或参数校验失败;不泄漏 SQLAlchemy 细节
- Repository 增加聚合读取(订单全量、按用户+状态列表、最新物流、在途退款),测试验证 API 必须经由 Repository 访问数据
- 测试 27 例(内存 SQLite + 确定性 seed):订单/物流/用户订单/退款资格/退款创建/取消/工单正反场景 + Repository 边界;与 2A 及 health 合计 37 例全绿
- OpenAPI(/docs)自动暴露请求/响应 schema、错误响应与端点说明;未引入独立文档系统

说明:⚠️ PostgreSQL/Docker 仍不可用,全部以 SQLite 验证;pgvector 未测试。`create_refund` 仅创建 PENDING 申请,`cancel` 直接执行取消;风控与人工审批在 Phase 5 接入(见 ARCHITECTURE §12)。

## Phase 2C — Business Scenario Tests [COMPLETED]

已完成(仅业务场景验证,不含任何 AI 组件):

- 新增 `tests/test_business_scenarios.py`(26 例):S1 订单查询聚合、S2 物流查询(含无物流订单)、S3 不存在订单(404 受控错误、无实现泄漏)、S4 退款资格矩阵(eligible / 含不可退商品 / 已退款 / 在途退款 / 已取消 / 未支付 / 未签收)、S5 退款创建全链路(API→Service→Repository→DB;客户端伪造金额/状态被忽略,金额=订单权威金额)、S6 重复退款 409、S7 取消状态机(合法 PENDING/PAID/SHIPPED→CANCELLED 并持久化;非法转移 CANCELLED/DELIVERED/REFUNDED→CANCELLED 拒绝)、S8 工单创建(含省略 order_id 的用户级工单)
- 跨域一致性:退款在途(PENDING)→ 取消被拒;已取消 → 退款被拒;已退款 → 再次退款被拒
- 业务不变量:refund.amount == order.total_amount;取消订单不可再退款;单订单单一在途退款;ticket 引用完整性
- 架构边界:保留既有 API→Repository spy 测试;新增场景级验证取消经 Service→Repository,路由不直接执行 SQL
- 判定顺序说明:DELIVERED 订单不可取消优先于在途退款检查(返回 422 ORDER_NOT_CANCELLABLE);ORDER_HAS_ACTIVE_REFUND 为防御性守卫

验证:全套 63 例全绿(SQLite);真实 HTTP smoke(health / get order / get logistics / eligibility / create refund / duplicate 409 / cancel / create ticket / 404)通过。⚠️ PostgreSQL / pgvector 仍未实机验证。
## Phase 3A — RAG · Knowledge Base & Ingestion [COMPLETED]

已完成(仅知识库接入与入库管线,不含检索 / Embedding / RAG 生成):

- 知识数据模型:`knowledge_documents` + `knowledge_chunks`(SQLAlchemy,Alembic 迁移 `018c7c0772c7`;已在全新 SQLite 库 `upgrade head` 验证两表创建)。文档字段含 title / category / version / status / source / effective_date / language / checksum / metadata;chunk 含 document_id(FK CASCADE)/ chunk_index / section / content / metadata,预留 Phase 3B 增加 embedding 列。
- 生命周期:DRAFT → ACTIVE → ARCHIVED;仅 ACTIVE 是未来检索候选(Repository `list_active` 固化该规则)。
- 版本化:唯一键 (category, title, version),历史版本不覆盖;seed 内含「退款政策 v1(2026-08-01,DRAFT)与 v2(2026-09-01,ACTIVE)」以验证版本演进与历史保留。
- 确定性 seed 知识:7 篇合成政策文档(退款 / 退货 / 换货 / 物流配送 / 优惠券 / 客服 SOP;退款含 v1/v2),见 `app/knowledge/documents.py` 与 `app/knowledge/seed.py`。
- 规范化与分块:`app/knowledge/chunker.py`(normalize + 章节感知分块,纯标准库、确定性、可替换)。
- Embedding 抽象:`app/knowledge/embedding.py`(`EmbeddingProvider` 接口 + 本地确定性桩;真实 provider / 向量入库留 Phase 3B)。
- 摄取服务:`app/knowledge/ingestion.py`(KnowledgeSpec → normalize → sha256 checksum → 分块 → Repository → DB;幂等,与 FastAPI 解耦)。
- 测试:`tests/test_knowledge.py` 12 例(文档创建 / 版本化 / 生命周期 / chunk 关系与元数据 / 幂等与冲突拒绝 / 溯源 / seed;全套 75 例全绿)。
- 未新增任何依赖;未实现检索 API / Embedding / pgvector / RAG 生成。

说明:⚠️ 数据库侧以 SQLite 全新库迁移验证;PostgreSQL / pgvector 仍未实机验证(本机无 Docker)。

## Phase 3B — RAG · Retrieval [COMPLETED]

已完成(Query → Query Processing → Dense + Sparse → RRF Fusion → Ranked Candidates;不含生成 / Rerank / Context Assembly):

- 检索层 `app/retrieval/`:`types`(typed filters / candidates / result)、`text`(NFKC + 空白归一 + 空查询校验;确定性 CJK 字符二元组 tokenizer)、`bm25`(纯标准库 Okapi BM25 + 功能词二元组过滤 + 多词元最少共享门槛)、`dense`(`DenseRetriever` 抽象 + `LocalDenseRetriever` 本地确定性路径)、`fusion`(Reciprocal Rank Fusion)、`service`(`RetrievalService.retrieve(query, top_k, filters)`)、`dataset`(27 条确定性检索数据集)。
- Dense 边界:复用 Phase 3A `EmbeddingProvider`;本地 `DeterministicEmbeddingProvider` 已升级为「词面重叠感知的确定性 bag-of-bigrams 向量」——**明确非语义 embedding**,文档已注明;`DenseRetriever` 接口为未来 pgvector/真实 provider 保留替换点。
- Sparse:BM25 标准库实现(零新增依赖);tokenizer 对 CJK 用滑动二元组并过滤含功能字(的了么吗… )的噪声二元组,多词元查询需 ≥2 个不同共享词元,抑制单点假命中。
- Fusion:RRF(k=60),rank-based、确定性;两路命中同一 chunk 时合并并记录双方法。
- 元数据过滤:typed `RetrievalFilter(status=ACTIVE 默认 / category / language)`,Repository `list_retrieval_candidates` 强制 ACTIVE 生命周期规则(ARCHIVED/DRAFT 默认不参与检索,除非显式内部覆盖)。
- 检索结果:typed `RetrievalCandidate`(chunk_id / document_id / title / category / version / section / content / score / rank / retrieval_methods / metadata)+ `RetrievalResult`(含 latency_ms);chunk→document→version→source 可溯源;无候选时返回空 candidates,不虚构答案。
- 检索 API:仅内部 `RetrievalService`,未创建公开 customer-facing RAG 端点(符合边界)。
- 测试:`tests/test_retrieval.py` 42 例(dense/sparse/hybrid、top_k、空查询、RRF 确定性与去重、category/language 过滤、ARCHIVED 默认排除 + v1/v2 版本行为、溯源、无结果 typed empty、27 条数据集);全套 **117 例全绿**。
- 零新增依赖;未实现 LLM query rewrite / Reranker / Context Engineering / 生成(见 3C+)。

说明:⚠️ PostgreSQL / pgvector **未实机验证**(本机无 Docker);数据库级向量检索性能未测量。本地 SQLite 检索测试通过。

## Phase 3C — RAG · Reranking + Context Assembly [COMPLETED]

已完成(内部链路:Hybrid Retrieval → Top 20 → Reranking → Top 5 → Context Assembly → Final Context;不含 LLM 生成 / Grounding):

- Reranker 层 `app/retrieval/rerank.py`:`Reranker` Protocol + `DeterministicReranker`(确定性、纯标准库,**明确非语义**的「可替换 architecture + test implementation」,未来可替换为 Cross-Encoder / LLM-based / provider reranking API)。打分模型显式加权:lexical / title / section / exact-term overlap + retrieval rank component + method-diversity bonus − duplicate penalty(权重集中 `RerankWeights`;重复检测按 (document_id, content) 界定,不同版本的相同措辞不会被判重)。(Decision 015 / 018)
- Context Assembly 层 `app/retrieval/context.py`:`ContextAssembler` 接收 reranked candidates,执行「ACTIVE 优先(防御性;同 category+title+section 组)+ 版本安全去重(chunk_id / 同文档同节同内容;不同版本不合并、历史不删除)」,按 relevance 顺序整块装入预算,输出 typed `ContextItem` / `ContextPackage`(query / items / total_items / truncated / token_budget / estimated_tokens),每项携带完整 citation 与溯源(chunk_id / document_id / source_id / title / category / version / status / section / language / content / relevance_score / retrieval_methods)。(Decision 016)
- Token Budget:`estimate_tokens()` 为无 tokenizer 依赖的确定性近似(CJK≈1 字/token、ASCII≈4 字符/token,文档注明仅为 approximation);`ContextBudget(max_tokens=2000, reserve_tokens=…)` 完全由 context 层控制,超预算整块停止、不产生超预算 context、不截断到不可读;reserve 为未来 system/user prompt 与 answer 预留。(Decision 017)
- 两阶段 Top-K typed config 与内部入口:`app/retrieval/pipeline.py` 的 `RetrievalPipelineConfig` 默认 retrieval_top_k=20 / rerank_top_k=5 / max_context_tokens=2000(校验 rerank_top_k ≤ retrieval_top_k);`RetrievalPipeline.run(query)` FastAPI 无关,未创建公开 customer-facing RAG 端点。
- Grounding Boundary:Context Assembly 不编造答案、不自动补知识、不调用业务 API / 订单 / 退款 / 取消;静态知识走 RAG,动态业务事实在 Agent Phase 经 Tools 获取(见 docs/RAG_DESIGN.md)。
- 测试:新增 `tests/test_reranking.py`(重排排序 / title / exact term / section / retrieval rank / diversity / duplicate / 空候选 / 空查询 / top_k 与 rerank_top_k ≤ retrieval_top_k / 确定性 / weights 校验)与 `tests/test_context.py`(排序 / 元数据与溯源 / token budget 与 reserve / 截断 / 去重 / ACTIVE 优先 / 多版本不合并 / DRAFT 兜底 / schema / 校验 / 端到端 retrieval→rerank→context / 零结果 typed empty),共享工厂 `tests/retrieval_factories.py`;全套 **154 例全绿**(117 + 37),零新增依赖。
- 未实现:LLM 生成 / Grounding / 拒答策略(由 Phase 3D+ / Phase 4 承接);真实 Embedding 模型与 pgvector 仍未接入/未实测(Decision 013)。

## Phase 3D — RAG · Knowledge Management & Integration [NOT STARTED]

> 状态:尚未开始(在 Phase 4A 之后按需启动)。范围:知识库管理 API / 界面(上传、版本管理、激活/归档);真实 Embedding 模型与 pgvector 的接入与实机验证(Decision 013 的后续,范围在启动时细化);与 Phase 4 Agent(Knowledge Agent)的集成边界。不实现 LLM 生成/Agent 编排。

- 知识库管理 API / 管理界面(上传、版本管理、激活/归档)
- 与 Phase 4 Agent(Knowledge Agent)的集成边界
- 范围与拆解将在 Phase 3D 启动时细化
## Phase 4A — Agent · Workflow(State / Intent / Routing / RAG branch / ToolRequest interface)[COMPLETED]

已完成(Agent 层骨架;未调用真实 LLM、未执行 Tool、未接 MCP/风控/HITL;遵循「LangGraph 仅作编排且本阶段不引入」= Decision 024):

- 新增 `backend/app/agent/`:state(`Intent` / `Route` / `AgentState` / `AgentResult` / `ToolRequest` 强类型与序列化)、intent(`IntentClassifier` Protocol + `DeterministicIntentClassifier` 规则实现,文档注明非生产 NLP)、entities(`EntityExtractor` + 确定性正则实现;多订单不猜测)、router(`WorkflowRouter` + `RuleBasedRouter`;**Intent ≠ Route**)、workflow(`AgentWorkflow` framework-agnostic 状态机:START → UNDERSTAND → CLASSIFY_INTENT → ROUTE → RAG / BUSINESS_TOOL_REQUEST / CLARIFY / ESCALATE → FINALIZE → END)。
- RAG 分支:通过 `RetrievalRunner` 接口调用既有 `RetrievalPipeline.run(query)`(不复制检索逻辑),在 `AgentState.retrieved_context` 保留结构化 `ContextPackage`(含 citation/溯源),供后续 grounding / observability。
- 业务 Tool 分支:只产生 typed `ToolRequest`(tool_name / arguments / reason / requires_confirmation / status=PENDING),**绝不执行**(执行属 Phase 4B:Agent → Tool → Service → Repository → Database)。
- 路由示例:REFUND_INQUIRY → RAG、REFUND_REQUEST → REFUND_TOOL、ORDER_STATUS / LOGISTICS_TRACKING → 对应 TOOL、UNSUPPORTED → ESCALATE、AMBIGUOUS → CLARIFY;缺订单/多订单 → CLARIFY,**不猜单**。
- Response State:`AgentResult`(status / response / intent / route / citations / tool_requests / needs_clarification / escalation_required / error);自然语言最终回复由后续 LLM 阶段生成。
- 新增测试:`tests/test_agent_intent.py`、`tests/test_agent_router.py`、`tests/test_agent_workflow.py`(覆盖 25 项清单 + Scenario A–D,含真实 RetrievalPipeline 集成与「agent 层不 import sqlalchemy/app.db」边界);全套 154 + 59 = **213 例全绿**。
- 零新增依赖;不 commit / 不进入 Phase 4B。

## Phase 4B — Agent · Tool Execution [COMPLETED]

已完成(Phase 4A 只规划 `ToolRequest`;Phase 4B 真正执行:Agent → Tool → Service → Repository → Database;未接真实 LLM / 风控 / HITL,退款与取消仍走 Mock Service 规则,`create_refund` 只生成 PENDING 申请):

- 新增 `backend/app/tools/`:`base.py`(`RiskLevel` / `ToolResultStatus` / `ToolResult` / `ToolExecutionContext` / `ToolDefinition` 契约;ToolResult 为稳定 domain 对象,永不返回 ORM)、`errors.py`(Tool 层错误与稳定错误码,如 UNKNOWN_TOOL / UNAUTHORIZED_ORDER_ACCESS / INTERNAL_TOOL_ERROR)、`registry.py`(`ToolRegistry`:register / get / has / list,重复注册显式报错,显式 allowlist,禁止 getattr / eval / 动态 importlib)、`executor.py`(`ToolExecutor`:查 Registry → Pydantic 参数校验 → 覆盖 user_id 为可信上下文 → 执行 handler → 统一归一化为 ToolResult)、`definitions.py`(6 个工具的 Pydantic Input/Output schema:`GetOrderInput` … `CreateTicketInput` + `OrderToolOutput` … `TicketToolOutput`)、`handlers.py`(六个工具 handler + `build_default_registry(session)` 接线)。
- 工具清单(全部经 Service,不复制业务规则):`get_order` / `get_logistics` / `check_refund_eligibility` / `create_refund`(金额由 Service 权威决定,模型传 amount=0.01 无效)/ `cancel_order`(保留 requires_confirmation / risk metadata)/ `create_ticket`(reason/category 非空、description 非空、order_id 如存在须属于该用户)。
- Authorization:订单「存在」与「可访问」分离——跨用户查订单 / 物流 / 退款 / 取消一律拒绝(`UNAUTHORIZED_ORDER_ACCESS`),`user_id` 只来自可信 `ToolExecutionContext`,模型参数不能覆盖。
- Agent 集成(`backend/app/agent/workflow.py`):注入 `ToolExecutor` 后 ORDER_TOOL / LOGISTICS_TOOL / REFUND_TOOL / CANCEL_TOOL / TICKET_TOOL 真正执行,ToolResult 存入 `AgentState.tool_results` 并 FINALIZE;CLARIFY / ESCALATE / RAG 分支不执行工具;未注入 executor 时保持 Phase 4A 只规划行为(向后兼容)。
- Refund 流程:REFUND_REQUEST → `check_refund_eligibility` → eligible=True 才追加 `create_refund`;ineligible / 越权一律不触达退款 Service。
- ToolResult metadata 预留 observability:execution_id / request_id / tool_name / duration_ms / success(不引入 OpenTelemetry)。
- 新增测试:`tests/test_tools.py`(Registry / Validation / Authorization / 六工具正反场景)、`tests/test_tool_executor.py`(执行 / UNKNOWN_TOOL / 校验与业务错误归一化 / 异常归一化 / user_id 不可覆盖)、`tests/test_agent_tool_integration.py`(ORDER_STATUS / LOGISTICS / REFUND 条件退款 / CANCEL / TICKET 经 Agent 全链路 + CLARIFY / ESCALATE / RAG 不执行 + 端到端 User request → … → DB → AgentResult);全套 **213 + 60 = 273 例全绿**,`compileall -q backend` 通过。
- 零新增依赖;不 commit / 不进入 Phase 5(Risk Control + Human-in-the-loop)。

## Phase 5 — Risk Control + Human-in-the-loop [COMPLETED]

已完成(Phase 5 MVP;不引入 LangGraph / MCP / Redis / Kafka / RBAC / 生产级审计):

- **Risk Engine / Policy**(backend/app/risk/):LOW / MEDIUM / HIGH / CRITICAL + RiskAction(AUTO_EXECUTE / USER_CONFIRM / HUMAN_APPROVAL / BLOCK);规则集中在 policy 常量(含高金额退款阈值 500,见 backend/app/risk/policy.py),不写在 Tool Handler;未注册操作 fail-closed BLOCK;引擎纯逻辑、不触数据库。
- **Workflow Risk Gate**(backend/app/agent/workflow.py):ToolRequest → RiskEngine → Gate → ToolExecutor → Verify。LOW 自动执行;MEDIUM(取消订单)等待用户确认——confirmed=True 才执行、confirmed=False 返回 REJECTED 不执行;HIGH / CRITICAL(退款执行默认人工审批)持久化 PENDING approval_requests 并进入 WAITING_HUMAN_APPROVAL,不执行。
- **Approval 绑定原始 ToolRequest**:approval_requests 表(Alembic 迁移 7a9c1e4b8d2f)保存 tool_name + tool_arguments 快照;审批 API GET /api/v1/approvals、POST /{id}/approve、POST /{id}/reject;PENDING→APPROVED / REJECTED,重复处理返回 409;approve 后 resume 该快照执行——不重新让 LLM 生成参数;业务规则仍生效(重复退款在 resume 时仍被 Service 拒绝)。
- **Execute → Verify**(backend/app/services/verification.py):create_refund 后重查 DB(refund 存在 / status PENDING / amount == 订单权威 total_amount),cancel_order 后重查 order.status == CANCELLED;不符 → run_status / AgentResult = VERIFICATION_FAILED,不向用户报假成功。
- **退款金额不可控**:退款金额只由 Service 从 order.total_amount 推导,输入 schema 不收 amount,用户 / Agent 都不能控制退款金额。
- **测试**:新增 40 例(risk / risk_gate / approval_api / approval_flow 四文件),全套 313 例全绿;backend compileall 通过;零新增依赖。
- **文档**:新增 docs/RISK_CONTROL.md;ARCHITECTURE.md §6/§7/§19、DECISIONS.md(032-036)、README.md 已同步。

说明:Phase 5 风控 / HITL 接入点位于 Agent 工具执行路径(workflow 注入 RiskEngine / ApprovalGateway / Verifier);Phase 2B 的直连 Mock HTTP API 保持原样。Direct refund 仍只创建 PENDING 退款申请,不执行资金操作。

## Phase 6 — MCP [COMPLETED]

已完成(Phase 6 MCP MVP;仅本地 stdio + 官方 MCP Python SDK,不引入多 Server / Gateway / 认证 / remote / Redis / Kafka / Service Mesh):

- **目标区分**:Tool Calling(模型如何选择与调用工具)≠ MCP(工具能力如何以标准化协议暴露与发现);MCP 不是 Tool Calling 的替代品(见 docs/MCP.md)。
- **MCP Server**(backend/app/mcp/server.py):官方 SDK `MCPServer("ecommerce-customer-service")`,仅注册 `get_order` / `get_logistics` / `create_ticket`;`if __name__ == "__main__"` 走 stdio(`run_stdio_async`)。
- **MCP Client**(backend/app/mcp/client.py):最小 stdio client——list_tools 得到 name / description / input schema,call_tool 返回 JSON envelope;错误统一映射为内部 ToolResult(`MCP_TOOL_NOT_FOUND` / `MCP_INVALID_ARGUMENTS` / `MCP_SERVER_ERROR` / `MCP_MALFORMED_RESULT`),SDK exception 不进入 Agent 层。
- **Agent 集成**(backend/app/mcp/adapter.py):`MCPToolAdapter` 作为单一 tool-provider——ORDER_STATUS → MCP get_order、LOGISTICS_TRACKING → MCP get_logistics、CREATE_TICKET → MCP create_ticket;REFUND / CANCEL / eligibility 仍走内部 Tool Executor。
- **Risk Gate 不被绕过**:RiskEngine 先于 adapter 执行;MCP 无 refund / cancel 工具,refund 仍创建 PENDING 审批等人工,approve 后走内部执行(测试断言不触达 MCP client)。
- **Service 边界**:MCP Tool handler → Service → Repository → DB;不直接操作业务 SQL;跨用户访问返回 `UNAUTHORIZED_ORDER_ACCESS`;schema 不含 refund_amount / amount。
- **依赖**:仅新增官方 `mcp==2.1.1`(MCP 2.x `MCPServer`,非旧版 FastMCP 名称;Python 3.13.14 验证)。
- **测试**:新增 24 例(server 6 / client 10 / adapter 8),全套 337 例全绿;backend compileall 通过。
- **文档**:新增 docs/MCP.md;ARCHITECTURE.md §20、DECISIONS.md 037-039、README.md 已同步。

说明:Phase 6 不做 MCP refund / cancel / auth / gateway / remote / 多 server;不进入 Phase 7。

## Phase 7A — Frontend Demo Workbench [COMPLETED]

已完成(Phase 7A 只做「演示」,不新增 Multi-Agent / Kafka / K8s / 分布式追踪 / 复杂评测平台 / LangChain / CrewAI / AutoGen,也不重写现有 Agent / Risk / MCP 核心):
- **后端薄层**(backend/app/demo/,只编排与投影真实组件):
- `demo_seed.py` — 生成 demo.db(dev seed + 演示订单 ORD-1001 / ORD-1002 / ORD-1003 / ORD-2001 + 知识库)
- `store.py` — 进程内会话 / 运行 / 审批投影存储(域表仍由领域模型持久化)
- `payloads.py` — 把真实 AgentState / AgentResult / ApprovalView 转为前端 JSON(含 Intent / Route / Sources / Steps / Risk / Approval / resolution)
- `service.py` — 组装真实 AgentWorkflow + RiskEngine + ApprovalService + BusinessVerifier + RetrievalPipeline + MCPToolAdapter(不重写任何一个)
- `routes.py` — `/api/v1/demo/chat`、`/demo/runs/{id}`、`/demo/sessions/{sid}/runs`、`/demo/approvals`、`/demo/approvals/{id}/approve|reject`
- **前端页面**(frontend/,仅展示层):
- `/chat` — 会话区 + 右侧 Agent Trace(Intent / Route / RAG 来源 / Tool Provider / Risk / Approval / Verification / Final Response)与六个快捷场景按钮
- `/console` — Approval Queue + Detail(Risk Level / Agent Action / 订单权威状态 / 金额)+ Approve / Reject;通过后展示 Resume → Execute → Verify
- `/evaluation` — 基础展示页(Reliability 分类 + AI Quality 标记为 Planned / Lightweight MVP,不显示伪造指标)
- 前端不硬编码业务结果、不计算风险等级、不决定是否需要人工介入;经 Next rewrites 将 `/api/v1/*` 代理到后端 `http://127.0.0.1:8000`
- **演示场景**:A 知识问答 → RAG;B 订单查询 → Tool;C 物流 → MCP(Trace 显示 `Provider: MCP`);D 取消订单 → Risk + 用户确认;E 退款 → Risk + 人工审批 → Resume → Verify
- **测试**:新增 5 例 demo API 测试(tests/test_demo_api.py,HTTP 边界 + 真实 AgentWorkflow),全套 342 例全绿;backend compileall 通过;前端 `npm run build` 通过
- **文档**:README「Demo(Phase 7A)」章节、ARCHITECTURE.md §21(Frontend Demo Layer)已新增/同步

说明:Demo 会话 / 审批投影为进程内存储,后端需以单 worker 运行;不自动进入 Phase 7C。


## Phase 7B — Real DeepSeek LLM Integration [CODE COMPLETED / REAL API VERIFICATION PENDING]

代码已完成(Phase 7B 只做一件事:把真实 DeepSeek 接入现有 Agent,让 /chat 支持自然语言交互;不重写 Agent / Risk / Approval / Tools / MCP / Retrieval 核心);真实 DeepSeek 在线验证待配置 DEEPSEEK_API_KEY 后进行:
- **LLM 抽象层**(backend/app/llm/):`base.py`(LLMProvider Protocol,轻量 OpenAI-compatible 接口)、`deepseek.py`(httpx 实现的 DeepSeek provider)、`errors.py`(LLM_TIMEOUT / LLM_AUTH_ERROR / LLM_RATE_LIMITED / LLM_PROVIDER_ERROR / LLM_INVALID_OUTPUT / LLM_CONFIG_ERROR 统一错误)、`prompts.py`(集中 System Prompt,不含任何 secret)、`nlu.py`(意图+实体结构化理解:严格 JSON → Pydantic 校验,失败即回退)、`respond.py`(最终回复生成:只消费 RAG ContextPackage / ToolResult 权威事实)
- **配置**:新增 `LLM_ENABLED=false`(默认关闭)、`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`(deepseek-chat)、`DEEPSEEK_BASE_URL`(https://api.deepseek.com);无 Key / 测试 / CI / 现场网络异常时 /chat 仍走确定性流程
- **Agent 集成**(backend/app/agent/workflow.py 最小适配):可选注入 llm_intent / llm_responder;LLM 理解成功则用其 intent/entities 进入既有 RuleBasedRouter + 风险门,失败/输出非法则回退 DeterministicIntentClassifier / CLARIFY;`result.response` 承载 LLM 最终回复,确定性文本保留为兜底
- **边界不变**:LLM ≠ Database / Business Service / Risk Engine / Approval Service / Tool Executor;LLM output 仅视为 untrusted proposal;Risk Gate / User Confirmation / Human Approval / Execute / Verify / MCP Adapter / Tool Executor / 权威 Business Service 全部保留;退款/取消绝不因 LLM tool calling 或 prompt injection bypass(有测试证明)
- **Demo /chat**:demo 服务在 llm_enabled=true 且配置有效 Key 时真实调用 DeepSeek(意图理解 + 最终回复);前端 Trace 新增「LLM Provider: DeepSeek」展示,不显示 API Key / secret
- **依赖**:仅新增 `httpx==0.28.1`(作为 OpenAI-compatible HTTP 传输层,不引入 LangChain / LangGraph / LlamaIndex / CrewAI / AutoGen)
- **测试**:新增 20 例(tests/test_llm_provider.py 13 例 provider + tests/test_llm_agent.py 7 例 Agent 集成/安全),原 342 例不减少,全套 362 例全绿;backend compileall 通过;前端 `npm run build` 通过
- **文档**:README 新增 LLM 配置章节;ARCHITECTURE.md §22(LLM Provider Layer);DECISIONS.md Decision 040 / 041


## Phase 7C — Evaluation + Observability + Final Demo Packaging [PLANNED]
- 检索/生成/Agent/工具/产品五层评测与回归流程(Evaluation 页接通)
- 端到端 trace:request → model → state → retrieval → tool → result → answer(ToolResult metadata 已预留 execution_id / duration_ms 等)
- 指标、日志聚合与行为审计
- 端到端演示脚本与场景
- 部署/发布准备(镜像、compose 完善、文档)
- 验收与收尾
