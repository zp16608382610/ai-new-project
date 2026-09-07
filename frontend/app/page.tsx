import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "首页",
};

export default function HomePage() {
  return (
    <main className="page" style={{ maxWidth: 1000 }}>
      <h1>Enterprise AI Customer Service Agent</h1>
      <p className="placeholder">企业级 AI 电商售后客服 Agent · Phase 7A Frontend Demo</p>

      <div className="hero-links">
        <Link className="hero-link" href="/chat">
          /chat · AI 客服会话
        </Link>
        <Link className="hero-link" href="/console">
          /console · 风控审批控制台
        </Link>
        <Link className="hero-link" href="/evaluation">
          /evaluation · 评测
        </Link>
      </div>

      <h2 style={{ marginTop: "2rem" }}>这是一个什么 Demo？</h2>
      <p>
        前端是纯展示层，所有结果来自真实后端 API：AgentWorkflow 理解意图、Routing、RAG / Tool / MCP
        检索与执行、RiskEngine 风险分级、Human-in-the-loop 审批、执行后校验。前端不做任何业务决策，也不伪造数据。
      </p>
      <ul>
        <li><strong>知识问答</strong> → RAG（混合检索 + 重排）</li>
        <li><strong>订单查询</strong> → Tool（get_order）</li>
        <li><strong>物流查询</strong> → MCP（get_logistics，Trace 显示 Provider: MCP）</li>
        <li><strong>取消订单</strong> → 风险门禁 + 用户确认</li>
        <li><strong>退款执行</strong> → 高风险门禁 + 人工审批 → Resume → Verify</li>
      </ul>
      <p className="placeholder">
        后端启动方式与演示步骤见仓库根目录 README.md 的 Demo 章节。
      </p>
    </main>
  );
}
