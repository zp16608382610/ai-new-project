# Risk Control(Phase 5 MVP)

> 本文是 Enterprise AI Customer Service Agent 的 Risk Control / Human-in-the-loop 说明,面向面试讲解与评审阅读。
> Phase 状态与其余文档见 [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)、[ARCHITECTURE.md](ARCHITECTURE.md)、[DECISIONS.md](DECISIONS.md)(Decision 032–036)。

## 为什么需要 Risk Control

Agent 能调用业务工具;工具能改变真实业务状态(取消订单、创建退款)。如果让 Agent 无条件直接执行写操作,一次意图误判或参数错误就可能造成真实损失(例如误退款、误取消)。因此系统在「Agent 决定要做什么」与「工具真正执行」之间插入一道**风险边界**:Agent 仍然可以规划 ToolRequest,但能否执行、以什么方式执行,由 Risk Engine + Risk Gate 决定。

设计目标(不是生产级风控平台,而是面试 MVP 的最小可信演示):

1. Agent 有清晰的风险控制边界;
2. 不同 Tool 有不同风险等级;
3. 中风险操作需要用户确认;
4. 高风险操作需要人工审批;
5. 执行后必须 Verify 业务状态,不轻信工具返回值。

## Risk Level 与处置动作

四个等级 + 一个处置动作:

| RiskLevel | RiskAction | 说明 | MVP 示例 |
| --- | --- | --- | --- |
| LOW | AUTO_EXECUTE | 只读/低影响,直接执行 | FAQ / 知识问答、订单查询、物流查询、退款资格查询 |
| MEDIUM | USER_CONFIRM | 写操作,先问用户 | 取消订单 |
| HIGH | HUMAN_APPROVAL | 涉及真实损失,人工审批 | 普通退款(金额 < 500) |
| CRITICAL | HUMAN_APPROVAL | 损失高,人工审批 | 高金额退款(金额 ≥ 500) |
| (未知操作) | BLOCK | fail-closed,绝不静默执行 | 未注册规则的操作 |

风险规则集中在 `backend/app/risk/policy.py`,不在 Tool Handler 里写死;高金额退款阈值 `high_value_refund_threshold = 500` 是 policy/config 数据,便于讲解「策略与处理解耦」。

## Risk Engine 与 Risk Gate(职责边界)

职责分离,保持既有分层不被破坏:

- **Agent**:理解用户意图、生成 ToolRequest(要做什么)。
- **Risk Engine**(`app/risk/`):输入 `ToolRequest + RiskContext` → 输出 `RiskDecision(risk_level / action / reason / policy_id)`。**纯分类逻辑,不访问 Repository / Database。**
- **Risk Gate**(`app/agent/workflow.py` 内):按 RiskAction 决定是否放行。
- **Tool Executor**:只负责执行。
- **Service**:只负责真实业务规则(资格、金额、状态机)。

Risk Engine 需要的业务上下文(例如订单退款金额)由 Workflow/Service 提前查询后放入 `RiskContext`;引擎不自行读库,也不接受用户/Agent 提供的金额作为可信输入。

```text
Agent
 ↓
Risk Engine
 ↓
Risk Gate
 ↓
User Confirmation / Human Approval
 ↓
Tool Executor
 ↓
Service
 ↓
Repository
 ↓
Database
 ↓
Verify
```

## User Confirmation(中风险)

取消订单示例:

```
用户:帮我取消 ORD-1002
Agent → cancel_order → Risk Engine → MEDIUM → USER_CONFIRM
系统:取消订单 ORD-1002 将导致订单进入取消状态,请确认是否继续?
```

- 未确认(`user_confirmed=None`):返回 `WAITING_USER_CONFIRMATION`,**绝不执行**。
- 确认(`user_confirmed=True`):放行 → Tool Executor → Service → Verify。
- 拒绝(`user_confirmed=False`):返回 `REJECTED`,不执行。

## Human Approval + Approval API + Resume(高风险)

退款示例(即使 Agent 判断正确,也不会无条件执行):

```
Agent → refund(ORD-1001) → Risk Engine → HIGH/CRITICAL → HUMAN_APPROVAL
approval_requests 新增 PENDING 记录 → 返回 WAITING_HUMAN_APPROVAL(不执行)
```

- **数据**:`approval_requests` 表(Alembic `7a9c1e4b8d2f`)保存 id / request_id / tool_name / tool_arguments / user_id / risk_level / reason / status / created_at / resolved_at / resolved_by;status ∈ PENDING / APPROVED / REJECTED。
- **API**:`GET /api/v1/approvals`(待审批列表)、`POST /api/v1/approvals/{id}/approve`、`POST /api/v1/approvals/{id}/reject`;不存在 → 404,重复处理 → 409 `APPROVAL_ALREADY_RESOLVED`。
- **绑定原始 ToolRequest(最重要)**:approval 保存的是**原始请求快照**(tool_name + tool_arguments,例如 `create_refund` + `{order_id: "ORD-1001"}`)。approve 后 resume 执行的是这个快照,**不会重新让 LLM 生成参数**——防止「审批的是 ORD-1001,执行时变成 ORD-1002」。
- **拒绝**:reject → REJECTED,不执行。
- **业务规则仍然生效**:resume 时如果订单已有在途退款,Service 依旧拒绝(重复退款不因审批被绕过)。

## Execute → Verify(执行后验证)

MVP 中两个写工具执行成功后会**重新查询权威业务状态**:

- **Refund Verify**:执行退款后重查 DB —— refund 记录存在、status == PENDING、amount == 订单权威 total_amount。
- **Cancel Verify**:取消后重查订单 —— order.status == CANCELLED。
- 验证失败:run_status / AgentResult = `VERIFICATION_FAILED`,**不会**向用户报「退款/取消成功」。

理由:Tool 返回 `success = true` 不代表业务系统真的完成了;数据库才是权威。若工具声称成功但库里没有退款记录,结果必须是 `VERIFICATION_FAILED`,而不是「退款成功」。

## Agent / Risk / Tool / Service 职责边界小结

- Agent 负责意图与规划;不直接执行工具、不直接访问 DB。
- Risk Engine 负责分级;不执行、不写库、不信任用户金额。
- Risk Gate 负责放行策略;只出现在工作流里。
- Tool Executor 负责执行与参数校验/授权归一化。
- Service 负责业务规则(退款资格、权威金额、重复检测、取消状态机)。
- Repository 是唯一数据库访问边界;Verify 通过 Repository 重查状态。

## Interview Explanation

> “我不会让 Agent 直接执行高风险工具。
> Agent 负责理解用户意图并生成 ToolRequest。
> Risk Engine 根据 Tool 和业务上下文做风险分级。
> 低风险查询可以自动执行;
> 取消订单需要用户确认;
> 退款属于高风险操作,需要人工审批。
> 审批通过后,我不会重新让 LLM 生成参数,而是恢复原始 ToolRequest。
> 工具执行之后,我还会重新查询权威业务状态进行 Verify,避免出现工具返回成功但业务系统实际上没有完成的假成功。”

## 验证与范围

- 新增测试 40 例(Risk 分级 / User Confirmation / Approval API / Approve→Resume / Reject / 快照绑定 / Verify 失败 / 业务安全),全套 313 例全绿;backend `compileall` 通过;零新增依赖。
- MVP 边界:不实现复杂 RBAC、Kafka、Redis、分布式锁、完整审计平台、Fraud Detection、真实支付/退款/物流系统、Console 前端审批页(审批走 API);退款只创建 PENDING 申请,不执行资金操作。
- 风控/HITL 接入点在 Agent 工具执行路径(workflow 注入 RiskEngine / ApprovalGateway / Verifier);Phase 2B 直连 Mock HTTP API 保持原样。

## Phase 9E 补充:售后执行同样经过这道门(Decision 048)

- 售后案件(9A–9D)一旦 `TreatmentPlan(action=REFUND, executable=true)`,执行**不会**绕过本文件描述的任何一层:必须先经过 Risk Engine / Risk Gate,高风险进 Human Approval,再经 Tool Executor → RefundService。
- 触发点不同:9E 的 `create_refund` / `check_refund_eligibility` ToolRequest 由 `AgentWorkflow._run_case_execution` 构造,并携带 `case_id`,以便审批 Resume 时精确定位 Case;审批使用的仍是同一个 `ApprovalService` / `approval_requests`,**没有第二套审批**。
- Verify 语义不变而且更严格:`AfterSalesExecutionService` 写 `COMPLETED` 之前会**再次**经 Repository 重新读取退款行;没有通过的 verification(`EXECUTION_NOT_VERIFIED`)或读不到退款行(`EXECUTION_REFUND_MISSING`)都会拒绝完成。
- 风控规则、风险等级与策略**未做任何修改**;9E 没有新增风险等级,也没有允许 LLM 决定金额或绕过审批。`EXCHANGE` / `REPAIR` 没有可执行的业务系统,记录 `NOT_IMPLEMENTED` / `HUMAN_HANDOFF` 转人工,不伪造成功。
