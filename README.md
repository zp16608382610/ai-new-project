# Enterprise AI Customer Service Agent

企业级 AI 电商售后客服 Agent。规划能力(RAG 已落地 Phase 3A 知识入库、Phase 3B 本地混合检索与 Phase 3C 本地重排 + 上下文组装;Phase 4A Agent Workflow 骨架与 Phase 4B Tool Execution(Agent → Tool → Service → Repository → Database)已落地;Phase 5 已实现**Risk Control + Human-in-the-loop**;Phase 6 已落地 **MCP MVP**(Agent → Risk → MCP Client → MCP Server → Tool → Service,内部工具与 MCP 工具并存);Phase 7A 已把上述 Agent 能力打包为**可现场演示的 Frontend Demo**(`/chat` + `/console` + `/evaluation`,前端只调用真实后端 API、纯展示层);Phase 7B 已接入**可选真实 DeepSeek**(默认 `LLM_ENABLED=false`:仅做意图理解与最终回复生成,业务事实来自 RAG / Tools,高风险动作由 Risk / HITL 控制,无 Key / 失败自动回退确定性流程);Phase 7C 已落地**固定数据集评测 + 单机 Observability Trace + Final Demo 打包**(规模化评测 / 生产级可观测基座仍属 Phase 8);Phase 9A–9D 已落地**售后案件领域模型 → Case 接入 → 调查与资格判定 → 处理方案与售后工单**(仍不执行退款 / 换货 / 维修);见 docs/DEVELOPMENT_PLAN.md):

- Agent
- RAG
- Tool Calling
- MCP
- Risk Control
- Human-in-the-loop
- Evaluation
- Observability

## Current Phase

**Phase 9D – After-Sales Treatment Plan + Ticket Creation(completed)**

Phase 1(Foundation)、Phase 2A(数据层)、Phase 2B(Mock Business API)、Phase 2C(Business Scenario Tests)、Phase 3A(知识库接入)、Phase 3B(混合检索)、Phase 3C(Reranking + Context Assembly)、Phase 4A(Agent Workflow 骨架)、Phase 4B(Tool Execution)、Phase 5(Risk Control + Human-in-the-loop)与 Phase 6(MCP MVP)已完成。当前状态:FastAPI 骨架、7 张 Mock 业务表 + 订单 / 物流 / 退款 / 取消 / 工单 HTTP API、Repository / Service 分层与场景化测试;知识库 `knowledge_documents` / `knowledge_chunks`(Alembic 迁移 `018c7c0772c7`)+ 入库管线;检索层 `app/retrieval/`:**Query Processing → Dense + BM25 → RRF Fusion → Candidate Set → Reranking → Context Assembly → Final Context**(内部 `RetrievalPipeline`,无公开 RAG 端点);Agent 层 `app/agent/`(Intent / Route / Workflow)与工具层 `app/tools/`(Tool Registry → Tool Executor → Service → Repository → Database,六个售后工具 + Pydantic 校验 + trusted user_id 授权 + 统一 ToolResult)。Phase 5 在 Agent 工具执行路径接入风险门:Risk Engine 分级(LOW / MEDIUM / HIGH / CRITICAL)+ Risk Gate(取消需用户确认、退款默认人工审批)+ approval_requests 审批表 + /api/v1/approvals 审批 API + Resume 原 ToolRequest + Execute→Verify(详见 docs/RISK_CONTROL.md)。Phase 6 在工具执行路径叠加 MCP:本地 stdio MCP Server(`ecommerce-customer-service`,官方 SDK `mcp==2.1.1`)只暴露 `get_order` / `get_logistics` / `create_ticket`;`MCPToolAdapter` 让 ORDER_STATUS / LOGISTICS_TRACKING / CREATE_TICKET 走 MCP,REFUND / CANCEL 仍走内部 Tool Executor + Risk Gate——Risk Gate 先于 MCP,退款不通过 MCP 绕过审批(详见 docs/MCP.md 与 docs/ARCHITECTURE.md §20)。检索默认只取 ACTIVE 版本(退款 v1/v2 已测试);支持 category / language / status typed 过滤;27 条确定性检索数据集 + rerank/context 单元与端到端用例。RAG 检索仍为确定性流水线(无 LLM rewrite);Phase 7B 起,LLM 最终回复生成只消费 RAG / Tool 权威证据(独立语义 Grounding 验证器尚未实现);`DeterministicReranker` 是「可替换架构 + 确定性测试实现」而非语义模型;Context 受 token budget(默认 2000,支持 reserve)控制并保留 citation/溯源;真实 Embedding 模型与 pgvector 未接入/未实测。

Phase 7A 在既有组件之上增加薄层 `backend/app/demo/`(仅做编排与 JSON 投影,不重写 AgentWorkflow / RiskEngine / ToolExecutor / MCP)与三个前端页面:`/chat`(会话 + 右侧 Agent Trace,真实调用 `/api/v1/demo/chat`)、`/console`(Approval Queue / Risk / Agent Action / Approve / Reject / Resume / Verify,真实调用审批 API)、`/evaluation`(基础展示页,不伪造指标)。演示场景:A 知识问答 → RAG;B 订单查询 → Tool;C 物流 → MCP(Trace 显示 Provider: MCP);D 取消订单 → Risk + 用户确认;E 退款 → Risk + 人工审批 → Resume → Verify。Demo 数据库为本地 SQLite(`python -m app.demo.bootstrap` 生成 `backend/demo.db`,含 ORD-1001 / ORD-1002 / ORD-1003 演示订单);会话与审批记录为进程内存储,演示需以单 worker 运行后端。前端 `npm run build` 通过;后端新增 5 例 demo API 测试(见 tests/test_demo_api.py),Phase 7A 完成时全套 342 例全绿。详细演示步骤见下方「Demo(Phase 7A)」。

Phase 7B 在不重写 Agent / Risk / Approval / Tools / MCP / Retrieval 的前提下,把真实 DeepSeek 接入 `/chat`:新增 `backend/app/llm/`(LLMProvider 抽象 + DeepSeekProvider(httpx,OpenAI-compatible)+ 集中 prompts + 结构化意图理解(Pydantic 校验)+ 基于权威证据的最终回复生成)。默认 `LLM_ENABLED=false`:无 Key / 测试 / CI / 现场断网都走确定性流程;开启后 LLM 输出仅是 untrusted proposal,仍需经过 Risk Gate → 用户确认 / 人工审批 → Execute → Verify。API Key 只出现在请求头,绝不进入日志 / Trace / API 响应 / 前端。新增 20 例 LLM / 安全测试,全套 362 例全绿(均为确定性 / 离线可复现验证);真实 DeepSeek 已在本机演示环境(配置 DEEPSEEK_API_KEY)以真实 API 在线验证通过,无 Key / CI / 现场断网时仍走确定性流程。详见 docs/ARCHITECTURE.md §22 与 docs/DECISIONS.md Decision 040 / 041。


Phase 7C 在不动 Agent / Risk / Approval / Tools / MCP / Retrieval 核心的前提下完成三项打包:① **Evaluation**(`backend/app/evaluation/`):9 类场景 11 个固定 case(FAQ/RAG、订单、物流、取消、退款、越权、业务规则拒绝、信息缺失、Prompt Injection / 越权指令),复用真实 `/demo/chat` 链路在隔离临时 SQLite 上执行,输出 Intent / Entity / Route / Risk / Approval / Execution / Verification 七项指标与逐 case Expected / Actual / failure reason——确定性离线模式(use_llm=false)11/11 通过,`POST /api/v1/demo/evaluation/run` 与前端 `/evaluation` 页可一键复现;② **Observability / Trace**:AgentState 记录每次 Risk Gate 判定(`risk_decisions`),demo timeline 输出结构化 Risk Gate 步骤(Tool → Risk Gate → Approval → Execute → Verify → Final Response),不记录 API Key / secret;③ **Final Demo Packaging**:docs/FINAL_DEMO.md(8 个固定演示场景 + 面试讲解点 + Known Limitations)。新增 11 例 Evaluation / Trace 测试,全套 391 例全绿(确定性离线运行);详见 docs/ARCHITECTURE.md §23 与 docs/DECISIONS.md Decision 042。



Phase 9A 落地独立售后案件领域模型(`after_sales_cases` + `AfterSalesCaseStatus`,不接入 Agent);Phase 9B 让 Agent 识别售后处理请求并创建 / 更新 Case、完成基础信息收集(`Intent.AFTER_SALES_REQUEST` / `Route.AFTER_SALES_CASE` / `WorkflowStage.CASE_MANAGEMENT`,见 docs/ARCHITECTURE.md §25);Phase 9C 让 `ELIGIBILITY_CHECK` 的案件自动完成售后调查:Order Investigation(业务事实:订单存在性 / 归属 / 状态 / 商品可退性 / 在途退款 / 签收参考时间)+ Policy Investigation(复用既有 Hybrid RAG 取得政策证据与 citation)→ 由纯领域 `EligibilityEngine` 给出确定性资格结论(三态 `eligible`),案件随之进入 `PROCESSING` / `REJECTED` / 返回 `INFORMATION_COLLECTION`;Agent 层仍不直接访问数据库,调查通过注入的 Protocol 完成。LLM 只做理解,不产生业务事实,也不能决定资格(见 docs/ARCHITECTURE.md §26 与 docs/DECISIONS.md Decision 045 / 046)。本阶段**不执行**退款 / 换货 / 维修,不改动 RefundService / CancelOrder / Risk Gate / HITL / Execute / Verify;新增 35 例测试(含 6 个售后评测 case 与新 `CASE` 指标),全套 480 例全绿。

Phase 9D 让 `eligible=true` 的案件继续往前走一步:由纯领域 `TreatmentPlanner`(`backend/app/after_sales/treatment.py`,无 SQLAlchemy / 无 LLM)生成结构化 **TreatmentPlan**(action 只能是 REFUND / EXCHANGE / REPAIR,且必须等于用户自己提出的 `requested_action`;`eligible=False` / `None` 或诉求为 UNKNOWN 时不选动作、不建执行型工单),再由 `AfterSalesTreatmentService` 复用既有 `TicketService` 创建 / 复用**售后工单**(`tickets.case_id → after_sales_cases.id`,Alembic 迁移 `8b1f3c5d7e90`),把处理方案写入 `after_sales_cases.collected_information["treatment_plan"]`,案件保持 `PROCESSING`(表示「任务已建立、等待执行」,不是 COMPLETED)。**幂等**:同一案件重复处理只复用工单,不重复创建;**不执行任何业务动作**(无 create_refund / cancel_order / 换货 / 维修),真正执行留给后续 Phase。Demo timeline 新增 `Treatment Plan` / `Ticket Creation` 步骤,`/chat` 可见 Case ID / Eligibility / Treatment Action / Ticket ID / Case status;Evaluation 新增 6 个 case 与 5 项指标(`treatment_plan_accuracy` / `ticket_creation_success` / `ticket_id_presence` / `duplicate_ticket_rate` / `execution_not_triggered`)。新增 27 例测试,全套 **509 例全绿**;零新增第三方依赖。见 docs/ARCHITECTURE.md §27 与 docs/DECISIONS.md Decision 047。

## Next Phase

**Next: Phase 9E – After-Sales Execution(退款 / 换货 / 维修),not started**

Phase 9E 及以后仍需补齐:售后资格的**执行**(退款 / 换货 / 维修,必须复用既有 Risk Gate + HITL + Execute → Verify)、售后金额计算、人工复核流程。Phase 8 保留规模化评测集(Retrieval / Generation / Agent / Tool / Product 五层)、生产级指标与日志聚合;Phase 3D(知识库管理 + 真实 Embedding / pgvector 实机验证)与 Phase 9 Final Demo(现场彩排 / 验收)仍为 not started,范围独立,可后续单独推进。详细路线见 [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md)。

## 架构一览

Knowledge path(FAQ / 知识问答):

```text
User → Agent → Retrieval → Context → Response
```

Business path(订单 / 物流 / 工单):

```text
User → Agent → Tool Request → Risk → MCP / Internal Tool → Service → Verify → Response
```

High-risk path(退款 / 取消):

```text
User → Agent → Tool Request → Risk → Human Approval → Resume → Tool → Verify → Response
```

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 与 [docs/MCP.md](docs/MCP.md)。

## 目录结构

```text
.
├── backend/            # FastAPI 后端(健康检查 + 数据层 + Mock Business API + 知识库/检索/Agent 层骨架 + MCP + Phase 7A demo 薄层)
├── frontend/           # Next.js + TypeScript 前端(Demo: Chat / Console / Evaluation 页面,纯展示层)
├── tests/              # 后端测试
├── docs/               # 文档:PRD / ARCHITECTURE / DECISIONS / DEVELOPMENT_PLAN / RAG_DESIGN / RISK_CONTROL / MCP
├── AGENTS.md           # Codex 开发规范
├── docker-compose.yml  # PostgreSQL + Redis
├── .env.example        # 环境变量示例
├── .gitignore
└── README.md
```

## 技术栈与版本记录

> 规则:版本不确定时使用当前稳定版本,并在此记录。✅ = 本机已实际验证。

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.13.14 ✅ | 本机 Python313;另有 3.11.9 |
| Node.js | 24.19.0 ✅ | 本机已装 |
| npm | 11.17.0 ✅ | Windows 下请用 `npm.cmd`(PowerShell 执行策略限制) |
| FastAPI | 0.141.1 | 后端框架 |
| Uvicorn | 0.52.4 | ASGI 服务器 |
| Pydantic | 2.13.5 | FastAPI 依赖(传递安装) |
| pydantic-settings | 2.15.0 | .env / 环境变量配置 |
| pytest | 9.1.1 | 测试 |
| httpx | 0.28.1 | FastAPI TestClient 依赖 |
| SQLAlchemy | 2.0.52 | ORM 数据访问(requirements.txt 锁定;结构/约束已用 SQLite 测试验证) |
| Alembic | 1.19.2 | 数据库迁移(initial schema 与 knowledge 迁移 `018c7c0772c7` 均已在全新 SQLite 库 upgrade 验证) |
| psycopg[binary] | 3.3.5 | PostgreSQL 驱动(已安装;真实 PostgreSQL 未实机验证) |
| mcp | 2.1.1 | 官方 MCP Python SDK(Phase 6:MCP Server MCPServer / Client,stdio 已实机验证) |
| Next.js | 16.3.4 | 前端框架 |
| React | 19.2.8 | |
| TypeScript | 7.0.2 | |
| PostgreSQL 镜像 | pgvector/pgvector:pg17 | 内置 pgvector,⚠️ 未实机验证 |
| Redis 镜像 | redis:7.4-alpine | ⚠️ 未实机验证 |

## 快速开始

### 1. 启动 Docker(可选:提供 PostgreSQL + Redis)

> ⚠️ Docker 尚未在当前机器实机验证,因为当前环境没有安装 Docker。本 README 不声称 Docker 已验证。

```bash
cp .env.example .env    # 首次执行;Windows: copy .env.example .env
docker compose up -d    # 启动 postgres 和 redis
docker compose ps
```

当前阶段只有数据库容器,没有后端/前端容器。

### 2. 启动 Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

# 数据库:默认 DATABASE_URL 指向本地 PostgreSQL(与 docker-compose 凭据一致,见 .env.example)。
# 本机没有 Docker 时,可临时指向 SQLite 便于本地启动/测试:
# $env:DATABASE_URL = "sqlite+pysqlite:///./local.db"

uvicorn app.main:app --reload
```

如果 `python` 不在 PATH,直接用解释器完整路径创建虚拟环境,例如:

```powershell
C:\Users\ZZZpp\AppData\Local\Programs\Python\Python313\python.exe -m venv .venv
```

验证:

```bash
curl http://127.0.0.1:8000/api/v1/health
# 期望返回 {"status": "ok"}
```

交互式 API 文档:http://127.0.0.1:8000/docs

### 3. 启动 Frontend

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:3000,可访问页面:

- `/` — 首页
- `/chat` — AI 客服会话(真实调用 Agent,右侧 Trace 展示决策过程)
- `/console` — 风控审批控制台(Approval Queue / Approve / Reject / Resume / Verify)
- `/evaluation` — 评测基础页(不显示伪造指标)

## Demo(Phase 7A)

前端为纯展示层:所有意图分类、检索、工具执行、风控分级与审批决定都来自真实后端 API,页面不硬编码业务结果、不做风险 / 审批决策。

### 演示页面

- `/chat` — 左侧会话 + 快捷场景按钮,右侧 Agent Trace(Intent / Route / RAG 来源 / Tool Provider / Risk / Approval / Verification / Final Response)
- `/console` — Approval Queue + Approval Detail(Risk Level / Agent Action / 订单权威状态 / 金额)+ Approve / Reject;审批通过后显示 Resume → Execute → Verify
- `/evaluation` — Reliability(场景 / 工具 / 风控 / MCP / 检索测试)与 AI Quality(Evaluation framework:Planned / Lightweight MVP)基础展示

### 5 分钟演示流程

1. 准备并启动 Demo 后端(单 worker;Demo 会话记录为进程内存储,不要开多 worker):

```bash
cd backend
python -m app.demo.bootstrap                      # 生成 demo.db(演示订单 + 知识库)
# Windows PowerShell:
$env:DATABASE_URL = "sqlite+pysqlite:///./demo.db"
# macOS / Linux:
# export DATABASE_URL="sqlite+pysqlite:///./demo.db"
uvicorn app.main:app --port 8000
```

2. 启动前端:

```bash
cd frontend
npm install
npm run dev
```

3. 打开 http://localhost:3000/chat 依次演示:

- A 知识问答 → RAG:点「咨询退款规则」,右侧显示检索到的知识来源
- B 订单查询 → Tool:点「查询订单」,执行 `get_order`
- C 物流查询 → MCP:点「查询物流」,Trace 中工具步骤显示 `Provider: MCP`
- D 取消订单 → Risk + 用户确认:点「取消订单」,出现确认卡片,点「确认,继续执行」
- E 退款 → Risk + 人工审批:点「申请退款」,消息变为「等待客服审核」;切换到 `/console`,打开审批单 → `Approve`,页面显示 Resume → Execute → Verify

打开 `/console` 可查看 Approval Queue / Risk Decision / Agent Action / Approve / Reject;打开 `/evaluation` 查看评测体系说明。

> Demo 说明:演示订单 ID(ORD-1001 / ORD-1002 / ORD-1003)来自 demo seed,UI 不猜测订单号;前端通过 Next rewrites 将 `/api/v1/*` 代理到 `http://127.0.0.1:8000`(见 frontend/next.config.mjs)。

## LLM 配置(Phase 7B)

真实 DeepSeek 通过环境变量控制,默认关闭(确定性流程)。配置写入本地 `.env`(模板见 `.env.example`,不要把真实 Key 提交进 Git):

```bash
LLM_ENABLED=false            # false=确定性流程;true=开启真实 DeepSeek
DEEPSEEK_API_KEY=            # 只放入本地 .env,绝不提交 / 打印
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

说明:

«DeepSeek 用于自然语言理解和响应生成;业务事实由 RAG/Tools 获取;高风险动作由 Risk/HITL 控制。」

- 开启后 `/chat` 由真实 DeepSeek 做意图理解 + 最终回复生成;任何一次 LLM 失败(超时 / 429 / 5xx / Key 无效 / 输出非法)都会安全回退到确定性流程,绝不绕过 Risk Gate / 用户确认 / 人工审批 / Verify。
- API Key 只出现在请求头:不进入日志、Agent Trace、API 响应、异常或前端。无 Key 或 `LLM_ENABLED=false` 时 Demo、测试与 CI 均可离线运行。

## Mock Business API(Phase 2B)

统一前缀 `/api/v1`(沿用既有 API version prefix;OpenAPI 文档在 http://127.0.0.1:8000/docs):

| Method | Path | 能力 | Agent Tool(Phase 4B 已接入) |
| --- | --- | --- | --- |
| GET | /api/v1/orders/{order_id} | 订单聚合(买家、明细、金额、状态、时间) | get_order |
| GET | /api/v1/orders/{order_id}/logistics | 最新物流记录 | get_logistics |
| GET | /api/v1/users/{user_id}/orders | 用户订单列表(可按 status 过滤) | — |
| POST | /api/v1/refunds/check-eligibility | 退款资格判定(Service 权威规则) | check_refund_eligibility |
| POST | /api/v1/refunds | 创建退款申请(PENDING,金额由订单推导) | create_refund |
| POST | /api/v1/orders/{order_id}/cancel | 取消订单(仅 PENDING/PAID/SHIPPED 且无在途退款) | cancel_order |
| POST | /api/v1/tickets | 创建售后工单 | create_ticket |

错误响应统一为 `{"status": "error", "code": ..., "detail": ...}`(404 / 409 / 422 等按错误类型区分)。

## 测试

在项目根目录使用 backend 虚拟环境运行:

```bash
backend/.venv/Scripts/python -m pytest tests -v
```

后端测试覆盖:health(HTTP 200)、Phase 2A 数据层(FK 约束、状态列 CHECK、seed 确定性与业务断言)、Phase 2B Mock Business API(订单/物流/退款/取消/工单与 Repository 边界)、Phase 2C 业务场景(工作流、跨域一致性、不变量)、Phase 3A 知识库(文档/版本化/生命周期/分块/元数据持久化/幂等与冲突拒绝/溯源/seed)、Phase 3B 检索(dense / sparse-BM25 / hybrid+RRF / 元数据与 ACTIVE 过滤 / 版本行为 / 溯源 / 空查询与无结果 / 27 条确定性数据集)与 Phase 3C 重排 + 上下文组装(rerank 排序信号 / 版本安全去重 / ACTIVE 优先 / token budget / 截断 / 溯源 / 多版本不合并 / 确定性 / 端到端 pipeline)、Phase 4A Agent Workflow(Intent 分类 / Intent≠Route / 实体提取与不猜单 / RAG 分支接真实 RetrievalPipeline / ToolRequest 只规划不执行 / Clarify / Escalate / Response State 序列化 / agent 层无 DB import 边界)与 Phase 4B Tool Execution(Tool Registry allowlist / Pydantic 输入校验 / user_id trusted context 与跨用户越权拒绝 / 六工具正反场景 / Executor 错误归一化 / Agent↔Tool 全链路 / REFUND 条件退款与端到端 DB 持久化)与 Phase 5 Risk Control + HITL(Risk Engine 分级 / 取消→用户确认 / 退款→人工审批 / Approval API / Resume 绑定原 ToolRequest / Execute→Verify / 金额不可控与业务安全)与 Phase 6 MCP(MCP list_tools 只暴露三工具 / schema 不含金额字段 / 真实 stdio 子进程调用 / 错误映射与越权拒绝 / Agent ORDER_STATUS·LOGISTICS_TRACKING·CREATE_TICKET 经 MCP / REFUND 不 bypass Risk Gate)与 Phase 7B LLM(provider 成功 / 超时 / 鉴权 / 限流 / 5xx / 畸形输出 / 缺 Key、结构化输出校验、LLM 失败回退确定性、prompt injection 不能绕过 Risk / 确认 / 审批、模型不能执行任意工具、MCP / RAG 在 LLM 路径下仍工作、API Key 永不泄漏)。数据/检索/工具测试运行于内存 SQLite,MCP 测试使用文件 SQLite + 真实 stdio 子进程;全套 **391 例全绿**(较 Phase 7B 的 380 例新增 11 例 Phase 7C Evaluation / Observability 测试;确定性离线运行 LLM_ENABLED=false);前端 `npm run build` 通过;PostgreSQL / pgvector 集成测试待具备 Docker 的环境执行。

## 说明与限制

- 当前**已实现** Phase 4A Agent Workflow 骨架(AgentState / Intent 分类 / Routing / RAG 分支 / ToolRequest 接口)与 Phase 4B Tool Execution(Tool Registry / Tool Executor / 六个售后工具 / 参数校验 / trusted user_id 授权 / ToolResult),并已完成 Phase 5 Risk Control + HITL(Risk Engine / Risk Gate / 用户确认 / 人工审批 / Resume / Verify,见 docs/RISK_CONTROL.md),以及 Phase 6 MCP MVP(MCP Server / MCP Client / MCPToolAdapter,内部工具与 MCP 工具并存,见 docs/MCP.md)。Phase 7B 已把真实 DeepSeek 作为**可选**能力接入 `/chat`(默认 `LLM_ENABLED=false`,LLM 只负责理解与最终回复生成,不接触数据库 / 业务服务 / 风控);固定数据集评测(隔离临时 DB)与单机 Trace(风险门事件 + Demo Timeline)已随 Phase 7C 落地;规模化评测与生产级 distributed tracing 保留至 Phase 8(Decision 042);LangGraph 运行时仍未引入(仅作为后续编排运行时设计,见 Decision 024)。Phase 3A 知识入库 + Phase 3B 本地混合检索(Dense + BM25 + RRF)、Phase 3C 重排 + 上下文组装已完成并通过测试。业务工具只经注入的 ToolExecutor 执行,Agent 层仍不直接访问 DB;`DeterministicIntentClassifier` 为确定性规则实现(非生产 NLP),LLM 意图理解仅在注入时启用;本阶段未引入 LangGraph(仅作后续编排运行时,经 adapter 接入)。真实 Embedding 模型与 pgvector 未接入/未实测。退款创建仅生成 PENDING 申请,不执行资金操作;Agent 侧执行退款须人工审批,执行后再 Verify(Phase 5)。
- PostgreSQL 选用带 pgvector 的官方镜像,为后续 RAG 阶段做准备;9 张表(7 业务表 + 2 知识库表)经 Alembic 迁移创建,数据层、API 与迁移均以 SQLite 验证,真实 PostgreSQL / pgvector 未实机验证。

- Docker 尚未实机验证(本机未安装 Docker),`docker-compose.yml` 为「已编写、未验证」状态,README 不作已验证声明。
- 产品范围见 [docs/PRD.md](docs/PRD.md),目标架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),决策记录见 [docs/DECISIONS.md](docs/DECISIONS.md),开发规范见 [AGENTS.md](AGENTS.md)。
- 后续如需更新依赖版本,先改本文件「技术栈与版本记录」并同步锁定文件。
