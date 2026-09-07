# MCP(Phase 6 MVP)

> 本文档解释本项目 MCP 的定位、为何保留「Internal Tools + MCP Tools」并存,以及 Tool Calling 与 MCP 的区别。实现见 `backend/app/mcp/`,架构对照见 docs/ARCHITECTURE.md §20,阶段说明见 docs/DEVELOPMENT_PLAN.md Phase 6,决策见 docs/DECISIONS.md 037–039。

## 1. Tool Calling ≠ MCP

**Tool Calling 解决的是「模型如何选择和调用工具」的问题,属于模型能力 / Agent 决策一侧。**

**MCP 解决的是「工具能力如何以标准化协议暴露和发现」的问题,属于工具提供 / 集成一侧。**

因此 MCP 不是 Tool Calling 的替代品。

在本项目中:

- Agent 负责理解用户意图并决定使用什么能力(Intent / Route → ToolRequest);
- Risk Engine 负责判断风险并决定是否允许执行;
- MCP Client 负责通过标准协议调用工具(list_tools / call_tool);
- MCP Server 负责标准化暴露工具(name / description / input schema);
- Service 负责真正的业务规则(校验、状态机、金额推导、授权)。

## 2. 为什么不是所有 Tool 都改成 MCP

当前系统已经有内部 Tool Executor(Tool Registry → Pydantic 校验 → Service → Repository → DB),并已在 Phase 5 接入 Risk Control + Human-in-the-loop(见 docs/RISK_CONTROL.md)。

MCP 的核心价值:

- **标准化**:工具以统一协议暴露(name / description / input schema);
- **工具发现**:客户端 list_tools 动态发现能力,不依赖硬编码注册表之外的重复清单;
- **解耦**:Agent 编排与工具提供方解耦,未来可接入外部工具服务;
- **演进**:不推翻已被验证的内部执行链。

所以本项目采用 **Internal Tools + MCP Tools 并存**:

- MCP 只暴露三个安全 / 可追溯工具:`get_order` / `get_logistics` / `create_ticket`;
- REFUND / CANCEL 等高风险操作保留在内部 Tool Executor + Risk Gate(故意不通过 MCP 暴露,见 §5)。

## 3. 目标链路

```text
Agent
 ↓
ToolRequest
 ↓
Risk Engine(风险分级与门控,先于任何执行)
 ↓
Tool Provider(单一工具提供接口)
 ├── Internal Tool Executor(REFUND / CANCEL / check_refund_eligibility …)
 └── MCP Adapter(get_order / get_logistics / create_ticket)
      ↓
     MCP Client(stdio,list_tools / call_tool)
      ↓
     MCP Server(ecommerce-customer-service)
      ↓
     Service(业务规则唯一归属)
      ↓
     Repository
      ↓
     DB
```

## 4. 暴露的 MCP Tools

MCP Server 名称:`ecommerce-customer-service`。

| Tool | 输入 | 输出 | 走 Service |
| --- | --- | --- | --- |
| get_order | order_id(ORD-1001) | OrderToolOutput | OrderService.get_order |
| get_logistics | order_id | LogisticsToolOutput | OrderService.get_logistics |
| create_ticket | user_id / order_id(可选)/ reason / description | TicketToolOutput | TicketService.create_ticket |

约束:

- 每个 Tool 都有明确的 name / description / input schema;
- input schema 不包含 `refund_amount` / `amount`——MCP 无法携带资金字段;
- Tool handler 只允许 `MCP Tool → Service → Repository → DB`,不直接操作 SQLAlchemy Session 业务逻辑;
- 跨用户订单访问返回 `UNAUTHORIZED_ORDER_ACCESS`(与内部工具同一授权边界);
- `user_id` 由 Agent 侧 adapter 从可信执行上下文注入,模型 / 客户端不能覆盖。

## 5. 为什么 MCP 不暴露 refund / cancel

退款已经有 Risk + Human Approval(Phase 5)。

如果直接把退款做成简单 MCP Tool,会让架构表达混乱:任何接入 MCP 的客户端都可能绕过「Risk Engine → 人工审批」的既有约束。因此:

- 本阶段**不创建** MCP `create_refund` / `cancel_order` / `check_refund_eligibility` 工具;
- 退款 / 取消继续走现有内部 Tool Executor + Risk Gate(取消需用户确认、退款需人工审批,approve 后 Resume 原 ToolRequest 快照执行);
- 测试明确断言:MCP list_tools 只返回三个工具,`create_refund` 经 adapter 不会触达 MCP client,且退款最终仍创建 PENDING 审批等待人工。

这是故意的架构设计:展示「Risk Gate 没有被 MCP 绕过」。

## 6. MCP Client 错误映射

MCP Client 把四类错误统一归一化为内部 `ToolResult`,不把 MCP SDK exception 直接暴露给 Agent:

| 场景 | 内部 status | 内部错误码 |
| --- | --- | --- |
| tool not found | FAILED | MCP_TOOL_NOT_FOUND |
| invalid arguments | VALIDATION_ERROR | MCP_INVALID_ARGUMENTS |
| server / connection error | FAILED | MCP_SERVER_ERROR |
| malformed result | FAILED | MCP_MALFORMED_RESULT |

Tool handler 不抛出裸异常:业务失败以 JSON envelope(`{"ok": false, "status": …, "code": …, "message": …}`)返回,服务端异常由兜底边界归一化,不泄漏内部细节。

## 7. 组件与职责

- `backend/app/mcp/tools.py`:三个工具 + Service-bound 调用函数(输入归一化、授权检查),声明 `MCP_TOOL_NAMES`;
- `backend/app/mcp/server.py`:用官方 MCP SDK 的 `MCPServer` 注册三个工具,`if __name__ == "__main__"` 走 stdio(`run_stdio_async`);
- `backend/app/mcp/client.py`:最小 stdio MCP Client(一次调用一条短生命周期连接),list_tools → `MCPToolInfo`,call_tool → `map_result_to_tool_result`;
- `backend/app/mcp/adapter.py`:`MCPToolAdapter`——MCP 工具走 MCP,其余工具委托内部 executor;`user_id` 在此注入可信身份;
- `backend/app/mcp/__init__.py`:包导出 adapter / client / tools,但**故意不 import server.py**,保证 `python -m app.mcp.server` 是干净的子进程入口。

## 8. 边界与限制(MVP 范围)

- 传输:仅本地 stdio;无 HTTP / remote MCP、无 MCP Gateway、无多 MCP Server、无认证 / OAuth;
- 不引入 Redis / Kafka / Service Mesh / 复杂 observability;
- 不重写 Agent、Risk Engine 或 Tool Executor;
- Agent 层不依赖 MCP SDK(依赖注入 `MCPToolAdapter`,可替换);
- 一次 MCP call 对应一条短生命周期 stdio 连接(便于测试与隔离;未来如需长连接可在 client 内复用 session,不改变 adapter 接口);
- 依赖:仅新增官方 `mcp==2.1.1`(Python 3.13.14 兼容,MCP 2.x 使用 `MCPServer`,非旧版 FastMCP 名称)。

## 9. 验证

- 新增 MCP 测试 24 例:`tests/test_mcp_server.py`(6,注册与 schema,不暴露 refund/cancel/amount)、`tests/test_mcp_client.py`(10,真实 stdio 子进程:list / call / 错误映射 / 越权 / malformed)、`tests/test_mcp_adapter.py`(8,Agent 路径经 MCP + REFUND 不 bypass Risk);
- 全套 `pytest tests` = 337 passed(313 旧测试 + 24 新测试);
- `python -m compileall -q backend` 通过;
- 依赖最小化:仅新增 `mcp==2.1.1`。
