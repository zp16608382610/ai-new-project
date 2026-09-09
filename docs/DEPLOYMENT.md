# Deployment(Phase 8B)

> 本文说明如何把「Enterprise AI Customer Service Agent」以 Docker Compose 部署到
> 单台 VPS。这是 **Interview Demo**,不是生产系统;刻意保持简单(SQLite + 单进程),
> 不引入 PostgreSQL / Redis / pgvector / 多 worker / K8s。

## 1. 目标架构

```text
Internet
   │
   ▼
 Caddy :443            (可选 profile;需要域名时启用)
   │
   ▼
Next.js :3000          (frontend 容器,next start)
   │   /api/v1/*  (Next rewrites)
   ▼
FastAPI :8000          (backend 容器,uvicorn 单 worker)
   │
   ├── Agent(RAG / Risk Gate / Tool Executor)
   ├── MCP Client ──▶ MCP stdio subprocess(python -m app.mcp.server,容器内)
   ├── Evaluation
   └── SQLite demo.db
          │
          ▼
      Docker named volume(acsa-demo_demo_db,/data)

FastAPI ──▶ DeepSeek API(可选,LLM_ENABLED=true 时才调用)
```

要点:

- `Agent` 仍不直接访问数据库:`Agent → ToolRequest → Risk Gate → Tool Executor /
  MCP Adapter → Service → Repository → Database`。
- Backend 容器不直接暴露到公网;唯一公网入口是 Caddy(或本地验证时的 3000)。
- DeepSeek API Key 只通过环境变量注入 backend 容器,不进入镜像、不进入前端、不打日志。

## 2. 前置条件

- 一台 Linux VPS(Docker + Docker Compose v2);没有 VPS 时,也可在任意装好
  Docker 的机器上本地验证。
- 域名(仅启用 Caddy HTTPS 时需要;没有域名可先跑 frontend+backend 两个服务)。

## 3. 本地 Docker 构建与启动

```bash
# 在项目根目录执行
docker compose -f docker-compose.deploy.yml build
docker compose -f docker-compose.deploy.yml up -d

# 查看状态 / 日志
docker compose -f docker-compose.deploy.yml ps
docker compose -f docker-compose.deploy.yml logs -f backend
```

启动后检查:

- 健康检查:`curl http://localhost:3000/api/v1/health` → `{"status":"ok"}`
  (走 Next rewrite → backend:8000,证明 frontend→backend 链路通)
- 页面:`http://localhost:3000/`、`/chat`、`/console`、`/evaluation`
- 后端日志应显示 `[entrypoint] demo bootstrap (idempotent)` 后启动 uvicorn。

### 3.1 带 Caddy 的 HTTPS 启动

```bash
# 1) 把 deploy/Caddyfile 中 demo.example.com 换成你的域名,
#    并将域名 A/AAAA 记录指向 VPS。
# 2) 放行 80/443 后:
docker compose -f docker-compose.deploy.yml --profile caddy up -d --build
```

Caddy 会自动申请并续期 TLS。没有域名时,Caddy 保持关闭,直接用
`http://<VPS-IP>:3000` 或本地 `http://localhost:3000` 验证。

## 4. 环境变量与 Secret

模板见根目录 `.env.production.example`(不含任何真实 Secret):

```bash
cp .env.production.example .env.production   # .env.production 已被 .gitignore 忽略
# 编辑 .env.production 填入 DEEPSEEK_API_KEY(可选)
docker compose -f docker-compose.deploy.yml --env-file .env.production up -d
```

或直接在 shell `export DEEPSEEK_API_KEY=...` 后启动 compose(compose 会做
`${VAR:-default}` 插值),backend 服务内部再以容器环境变量形式收到这些值。

- **DeepSeek Key 只从环境注入**:不进镜像、不进前端、不进日志。
- **`LLM_ENABLED=false`(默认)**:即使 Key 未配置或配置错误,`/chat` 仍可用确定性
  流程完整演示,并且**不会产生任何 API 费用**。面试需要真实大模型回答时再设
  `LLM_ENABLED=true`。
- 本机 / CI 如需后端直接连 PostgreSQL,可覆盖 `DATABASE_URL`;当前部署不使用。

## 5. 数据库

- 当前 Demo **默认使用 SQLite**,文件位于 backend 容器内 `/data/demo.db`,由
  named volume `demo_db` 持久化(`docker compose down` / 重启不会丢数据)。
- 容器每次启动都会执行 `python -m app.demo.bootstrap`(已幂等验证:重复执行不会
  重复插入用户 / 订单 / 知识库数据),随后启动 uvicorn。
- 需要重置 Demo 数据时:

  ```bash
  docker compose -f docker-compose.deploy.yml down -v   # 删除 named volume(连同数据)
  docker compose -f docker-compose.deploy.yml up -d     # 重新 bootstrap
  ```

  > 谨慎使用 `-v`:会同时清空 demo 数据与 Caddy 证书数据卷。

- **PostgreSQL 是架构兼容选项,但本阶段不启用**:代码层已通过 SQLAlchemy +
  `psycopg` 兼容;根目录 `docker-compose.yml` 仍保留 postgres/redis 供本地开发。
  本部署不引入 pgvector。

## 6. MCP

- MCP 当前仍是 **stdio subprocess**:由 backend 容器内部每次调用
  `python -m app.mcp.server`(继承同一容器环境与 `DATABASE_URL`)。
- 因此在公网单机 Docker 部署中可原样运行,无需把 MCP 改成 HTTP。
- 前提:backend 容器内 Python 环境装有 `mcp==2.1.1`(requirements.txt 已含),
  且工作目录 / 模块路径能 import `app`(镜像已固定为 `/app/backend`)。

## 7. 面试自检清单

面试前在公网(或本地)过一遍:

1. `GET /api/v1/health` → 200 `{"status":"ok"}`
2. `/chat` 输入「退款规则是什么?」→ RAG 分支返回知识内容(带来源)
3. `/chat` 输入「ORD-1001 到哪里了?」→ `LOGISTICS_TRACKING` → MCP
   `get_logistics` → 物流信息
4. `/console` 打开审批队列;触发一笔退款后确认能 Approve → Resume → Verify
5. `/evaluation` 一键运行确定性评测 → 11/11 PASS

## 8. 公网安全说明(Interview Demo limitation)

**已知限制:当前 Demo 的 `/console` 与 `/evaluation` 没有完整用户鉴权**,任何能
访问公网入口的人都可以查看 / 操作审批队列或触发评测。这是刻意保留的
**Interview Demo limitation**。正式上线前,生产环境需要补充:

- authentication(登录 / 身份)
- authorization + admin / operator 角色
- API rate limiting
- secret management(如 Docker secrets / KMS)
- audit logging

不建议为了本阶段引入完整认证系统。若希望面试期减少暴露,可只在使用时临时放行
Caddy 端口,或通过 Caddy 增加一个 Basic Auth 层(最小临时手段)。

## 9. 回滚

- 镜像本地 tag:`acsa-demo-backend:local`、`acsa-demo-frontend:local`。
- 部署新版本前给镜像打版本 tag,例如
  `docker tag acsa-demo-backend:local acsa-demo-backend:7c`。
- 回滚 = 切回旧 tag 并重启:

  ```bash
  docker compose -f docker-compose.deploy.yml down
  docker compose -f docker-compose.deploy.yml up -d   # 使用旧镜像 tag
  ```

- SQLite 数据在 named volume 中,回滚镜像不影响已有 demo 数据;需要干净状态时
  按第 5 节重建。
## 10. Render(免费托管)自动休眠说明

本仓库同时在 Render 上部署了前端与后端两个免费 Web Service:

- 前端:`https://ai-new-project-1.onrender.com/chat`
- 后端:`https://ai-new-project-pavw.onrender.com`,健康检查
  `GET /api/v1/health` → `{"status": "ok"}`

Render 免费实例在约 **15 分钟无入站请求后会自动休眠**;下一次请求会触发冷启动
(通常 30–60 秒),若前端代理请求在冷启动完成前超时,会表现为聊天提问报
“请求失败 / 无法连接”。处理方式:

- 聊天前端(`frontend/app/chat/ChatClient.tsx`)已内置自动唤醒:页面打开即请求同源
  `/api/v1/health`(经 Next.js rewrite 转发到后端),未就绪时在约 **120 秒**总时限内
  串行轮询(单次请求最长 20 秒),全程显示“AI 服务正在启动,首次加载可能需要 30–60 秒,
  请稍候…”并禁用发送;只有超过最大等待时间才展示失败与“重试连接”。后端就绪前绝不
  直接 POST `/api/v1/demo/chat`。发送瞬间后端再次休眠时,只对“确认请求未到达后端”的
  冷启动失败(网关 5xx 且无 JSON body)唤醒后自动重发一次;网络中断/超时等无法确认的
  失败绝不重复提交业务请求。整个过程无需手动访问后端地址或 `/health`。
- 保活:`.github/workflows/keep-alive.yml` 每 10 分钟 ping 一次前后端 URL,
  使免费实例基本不进入休眠;也可用 UptimeRobot 等外部监控替代。
- 彻底解决:将实例升级到 Render 付费档(不自动休眠)。
