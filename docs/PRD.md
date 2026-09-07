# PRD — Enterprise AI Customer Service Agent

> 状态:草案(Draft)| 更新:2026-09-06
> 范围约束:只描述已确定的业务场景,不添加尚未确定的业务功能。

## 1. Product Overview

企业级 AI 电商售后客服 Agent:在电商售后场景中,自动理解用户诉求、检索知识库与订单/物流等业务数据、自主决策并执行低风险操作,对高风险或敏感操作先做风控评估并转人工审批,全程可观测、可评测。

核心处理链路:Understand → Retrieve → Decide → Act → Verify → Escalate。

## 2. Target Users

- 消费者(买家):通过对话自助完成售后咨询与操作。
- 客服运营人员:处理转人工任务、审核高风险操作、维护知识库与规则。
- 质量/评测人员:评测回答质量与 Agent 行为,复盘异常会话。

## 3. Core Scenarios

| # | 场景 | 说明 | 主要数据来源 | 风险等级 |
| --- | --- | --- | --- | --- |
| S1 | FAQ | 退换货政策、售后流程、运费等常见问题解答 | 静态知识(RAG) | LOW |
| S2 | Order Lookup | 查询订单状态、商品、金额等信息 | 动态业务数据(Tools) | LOW |
| S3 | Logistics | 查询物流轨迹与配送进度 | 动态业务数据(Tools) | LOW |
| S4 | Refund Eligibility | 判断订单是否符合退款/退货条件 | 规则 + 动态业务数据 | MEDIUM |
| S5 | Refund Execution | 执行退款/退货操作 | 动态业务数据(Tools) | CRITICAL |
| S6 | Cancel Order | 执行订单取消 | 动态业务数据(Tools) | HIGH |
| S7 | Complaint | 投诉受理、记录与跟进 | 动态业务数据(Tools) | MEDIUM |
| S8 | Human Handoff | 转人工客服,交接上下文 | 会话 + 业务上下文 | 视风险而定 |

## 4. User Stories

- 作为消费者,我想询问退换货政策,以便在购买前了解售后规则(S1)。
- 作为消费者,我想查询我的订单状态和物流进度,以便了解货物到哪里了(S2/S3)。
- 作为消费者,我想知道我的订单能否退款以及原因,以便决定是否发起退款(S4)。
- 作为消费者,我想发起退款并完成操作,前提是我的订单符合条件且操作通过风控/审批(S5)。
- 作为消费者,我想取消未发货的订单,系统应确认风险后执行(S6)。
- 作为消费者,我想提交投诉并得到受理结果(S7)。
- 作为消费者,当自动服务无法解决时,我想转人工并保留完整上下文(S8)。
- 作为客服运营人员,我想审核高风险/异常操作,以便在授权后放行(S5/S6/S8)。
- 作为评测人员,我想查看检索、生成与工具调用质量,以便持续改进。

## 5. Functional Requirements

系统级:

- FR-API:统一 API 前缀 `/api/v1`,统一错误响应结构。
- FR-CHAT:多轮对话会话管理(当前未实现,Phase 4)。
- FR-RAG:静态知识问答:Query → Rewrite → Vector Search + BM25 → Fusion → Reranker → Context Assembly → LLM → Grounding Validation(Phase 3)。
- FR-TOOLS:通过工具访问动态业务数据:`get_order` / `get_logistics` / `check_refund_eligibility` / `create_refund` / `cancel_order` / `create_ticket`(Phase 2B 提供 mock,Phase 4B 已接入 Agent 执行)。
- FR-RISK:按风险等级(LOW/MEDIUM/HIGH/CRITICAL)分级处置,高风险不直接执行(Phase 5)。
- FR-HITL:Interrupt → Approval → Resume 的人工介入流程(Phase 5)。
- FR-EVAL:检索/生成/Agent/工具/产品五层评测(Phase 7C)。
- FR-OBS:端到端可观测:request → model → state → retrieval → tool → result → answer(Phase 7C)。

场景级(占位,细节在各 Phase 细化):

- S1 FAQ:基于知识库给出有出处、可溯源的回答;检索不到时转人工(S8)。
- S2/S3 Order Lookup / Logistics:读取订单与物流数据并如实回答;涉及个人信息需脱敏与鉴权。
- S4 Refund Eligibility:按退款规则判定资格并说明原因;结果仅作判定,不执行资金操作。
- S5 Refund Execution:必须通过风控评估;CRITICAL 默认转人工审批后执行;执行后返回结果并验证。
- S6 Cancel Order:HIGH,需二次确认/风控,必要时人工审批。
- S7 Complaint:受理、登记工单(create_ticket)、给出处理预期。
- S8 Human Handoff:完整交接对话与业务上下文,记录转交原因。

## 6. Non-functional Requirements

- 可靠性:关键工具调用有重试与失败回退;人工审批不丢失。
- 性能:对话首包延迟可接受;检索/Tool 单步有超时上限(具体阈值后续阶段确定)。
- 安全与隐私:订单/物流等个人数据最小化暴露,输出前鉴权与脱敏。
- 可观测:全链路 trace 与结构化日志,支持异常会话复盘。
- 可评测:关键场景有评测集与通过标准,改动需回归评测。
- 合规:退款/取消等资金与履约操作遵循业务风控与审计要求(人工审批留痕)。

## 7. Risk Levels

- LOW:只读、低影响(如 FAQ、查询),可由 Agent 直接处理。
- MEDIUM:涉及判定或部分敏感信息(如资格判定、投诉受理),通常可直接处理,但需留痕。
- HIGH:影响履约/账户状态且难以无成本回退(如取消订单),需要二次确认或风控评估后执行。
- CRITICAL:涉及资金或严重不可逆影响(如执行退款),默认必须人工审批后方可执行。

## 8. Human-in-the-loop

流程:Interrupt → Approval → Resume。

- 触发:CRITICAL 操作、HIGH 操作风控命中、低置信/异常会话、用户主动要求转人工。
- 审批人:客服运营人员;审批动作 approve / reject,并保留理由与审计记录。
- 恢复:审批通过后从断点恢复工作流继续执行。

## 9. Evaluation

五层评测(Phase 7C 细化):

- Retrieval:召回率、MRR/NDCG 等检索质量。
- Generation:回答准确性、忠实于检索结果(grounding)、可读性。
- Agent:路径合理性、状态正确性、打断/恢复正确性。
- Tool:工具选择正确率、参数正确率、执行成功率。
- Product:场景完成率、转人工率、用户满意度等业务指标。

## 10. Observability

全链路可观测(Phase 7C 细化),一次请求应可追踪:

request → model → state → retrieval → tool → result → answer

记录:模型输入输出、Agent 状态流转、检索命中的引用、工具调用与结果、风控与人工审批事件。

## 11. MVP Scope

首个可交付闭环(以 Phase 2B–Phase 5 落地为前提,最终以开发排期为准):

- S1 FAQ(纯知识问答 + 出处)
- S2 / S3 Order Lookup / Logistics(只读查询)
- S4 Refund Eligibility(只读判定,不执行资金操作)
- S8 Human Handoff(转人工与上下文交接)

S5 Refund Execution / S6 Cancel Order / S7 Complaint 在 MVP 后续版本进入;进入前需通过风控与 HITL 设计评审。
说明:Phase 2B 提供的 mock 工具接口(含 create_refund / cancel_order)仅用于开发与联调,不代表上述执行能力在产品中上线。

## 12. Out of Scope

- 非中文多语言支持(当前不规划)。
- 支付、库存、真实资金清算等上游系统改造。
- 多平台/多店铺/多渠道接入。
- 复杂 RBAC/组织权限体系(仅保留最小客服/运营/评测角色)。
- 情感分析、主动营销等非售后客服功能。
- 未经 PRD 评审新增的任何业务场景。