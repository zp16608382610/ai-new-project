import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "首页",
};

export default function HomePage() {
  return (
    <main className="page">
      <h1>Enterprise AI Customer Service Agent</h1>
      <p className="placeholder">企业级 AI 电商售后客服 Agent</p>
      <p className="placeholder">
        当前页面仅为占位。后续将在 Chat 接入 Agent / RAG / Tool Calling,在 Console 接入风控与人工介入,在 Evaluation 接入评测。
      </p>
    </main>
  );
}