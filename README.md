# Enterprise AI Customer Service Agent

企业级 AI 电商售后客服 Agent。规划能力(RAG 已落地 Phase 3A 知识入库与 Phase 3B 本地混合检索;**生成/编排与其余能力尚未实现**;见 docs/DEVELOPMENT_PLAN.md):

- Agent
- RAG
- Tool Calling
- MCP
- Risk Control
- Human-in-the-loop
- Evaluation
- Observability

## Current Phase

**Phase 3B – RAG · Hybrid Retrieval completed**

Phase 1(Foundation)、Phase 2A(数据层)、Phase 2B(Mock Business API)、Phase 2C(Business Scenario Tests)、Phase 3A(知识库接入)与 Phase 3B(混合检索)已完成。当前状态:FastAPI 骨架、7 张 Mock 业务表 + 订单 / 物流 / 退款 / 取消 / 工单 HTTP API、Repository / Service 分层与场景化测试;知识库 `knowledge_documents` / `knowledge_chunks`(Alembic 迁移 `018c7c0772c7`)+ 入库管线;以及检索层 `app/retrieval/`:**Query Processing → Dense + BM25 → RRF Fusion → Ranked Candidates**(仅内部 `RetrievalService`,无公开 RAG 端点)。检索默认只取 ACTIVE 版本(退款 v1/v2 已测试);支持 category / language / status typed 过滤;27 条确定性检索数据集。**Reranker / Context Assembly / LLM 生成尚未实现**;真实 Embedding 模型与 pgvector 未接入/未实测。

## Next Phase

**Phase 3C – RAG · Reranking + Context Assembly(not started)**

Reranker 与 Context Assembly(为生成准备上下文与引用格式)。详细路线见 [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md)。
## 目录结构

```text
.
├── backend/            # FastAPI 后端(健康检查 + 数据层 + Mock Business API + 知识库/检索:schemas/service/repository/routes/knowledge/retrieval)
├── frontend/           # Next.js + TypeScript 前端(页面占位)
├── tests/              # 后端测试
├── docs/               # 文档:PRD / ARCHITECTURE / DECISIONS / DEVELOPMENT_PLAN
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
- `/chat` — Chat(占位)
- `/console` — Console(占位)
- `/evaluation` — Evaluation(占位)

## Mock Business API(Phase 2B)

统一前缀 `/api/v1`(沿用既有 API version prefix;OpenAPI 文档在 http://127.0.0.1:8000/docs):

| Method | Path | 能力 | 未来 Tool |
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

后端测试覆盖:health(HTTP 200)、Phase 2A 数据层(FK 约束、状态列 CHECK、seed 确定性与业务断言)、Phase 2B Mock Business API(订单/物流/退款/取消/工单与 Repository 边界)、Phase 2C 业务场景(工作流、跨域一致性、不变量)、Phase 3A 知识库(文档/版本化/生命周期/分块/元数据持久化/幂等与冲突拒绝/溯源/seed)、Phase 3B 检索(dense / sparse-BM25 / hybrid+RRF / 元数据与 ACTIVE 过滤 / 版本行为 / 溯源 / 空查询与无结果 / 27 条确定性数据集)。数据库与检索测试运行于内存 SQLite(全套 117 例全绿);PostgreSQL / pgvector 集成测试待具备 Docker 的环境执行。

## 说明与限制

- 当前**未实现** Agent / Tool Calling / MCP / 风控 / Human-in-the-loop 与 RAG 的生成/重排(见 Phase 状态);Phase 3A 知识入库 + Phase 3B 本地混合检索(Dense + BM25 + RRF)已完成并通过测试。真实 Embedding 模型与 pgvector 未接入/未实测。风控与人工审批属 Phase 7。退款创建仅生成 PENDING 申请,不执行资金操作。
- PostgreSQL 选用带 pgvector 的官方镜像,为后续 RAG 阶段做准备;9 张表(7 业务表 + 2 知识库表)经 Alembic 迁移创建,数据层、API 与迁移均以 SQLite 验证,真实 PostgreSQL / pgvector 未实机验证。

- Docker 尚未实机验证(本机未安装 Docker),`docker-compose.yml` 为「已编写、未验证」状态,README 不作已验证声明。
- 产品范围见 [docs/PRD.md](docs/PRD.md),目标架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),决策记录见 [docs/DECISIONS.md](docs/DECISIONS.md),开发规范见 [AGENTS.md](AGENTS.md)。
- 后续如需更新依赖版本,先改本文件「技术栈与版本记录」并同步锁定文件。