import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Evaluation",
};

export default function EvaluationPage() {
  return (
    <main className="page" style={{ maxWidth: 1100 }}>
      <h1>Evaluation · 评测</h1>
      <p className="placeholder">
        当前为 Phase 7A 基础展示页。后端评测体系（Phase 8）尚未实现，本页不展示任何伪造的
        accuracy / precision / recall 指标。
      </p>

      <div className="evaluation-grid">
        <section className="eval-card">
          <h3>Reliability · 可靠性</h3>
          <p className="placeholder" style={{ marginTop: 0 }}>
            仓库现有后端自动化测试（tests/）覆盖以下可靠性维度；真实测试可直接在仓库运行：
          </p>
          <ul>
            <li>Business scenario tests —— FAQ / 订单 / 物流 / 退款 / 取消 / 工单场景</li>
            <li>Tool execution tests —— Tool 定义、校验、执行器与数据库一致</li>
            <li>Risk control tests —— 风险分级与执行门禁（LOW→CRITICAL）</li>
            <li>Human-in-the-loop tests —— 审批 API 与审批流（approve / reject / resume）</li>
            <li>MCP integration tests —— MCP server / client / adapter 真实链路</li>
            <li>Retrieval tests —— 混合检索、重排、Context 组装</li>
          </ul>
        </section>

        <section className="eval-card">
          <h3>AI Quality · 生成质量</h3>
          <p className="placeholder" style={{ marginTop: 0 }}>
            当前 Agent 为确定性演示工作流（无 LLM 生成），因此本页对“生成质量”不做数字宣称：
          </p>
          <ul>
            <li>
              Evaluation framework: <strong>Planned / Lightweight MVP</strong>
            </li>
            <li>Retrieval 质量：可基于真实评测集计算 Recall@K / NDCG（Phase 8）</li>
            <li>Agent / Tool 质量：场景级通过率与工具调用准确率（Phase 8）</li>
            <li>产品指标：退款成功率、一次解决率、人工介入率（Phase 9）</li>
          </ul>
        </section>

        <section className="eval-card">
          <h3>Observability · 可观测性</h3>
          <p className="placeholder" style={{ marginTop: 0 }}>
            前端 Trace 面板（/chat 右侧）已展示单条请求的 Agent 决策链路：
          </p>
          <ul>
            <li>Understand → Intent / Route</li>
            <li>Retrieve → RAG 来源与相关度</li>
            <li>Tool → Provider（MCP / Internal）与结果</li>
            <li>Risk Gate → Human Approval → Execute → Verify</li>
          </ul>
          <p className="placeholder">
            全链路 Trace（request → model → state → retrieval → tool → answer）将在 Phase 8 补齐。
          </p>
        </section>
      </div>
    </main>
  );
}
