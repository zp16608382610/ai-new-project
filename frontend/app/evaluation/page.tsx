import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Evaluation",
};

export default function EvaluationPage() {
  return (
    <main className="page">
      <h1>Evaluation</h1>
      <p className="placeholder">评测页面占位 —— 后续将在此接入 Evaluation 与 Observability 看板。</p>
    </main>
  );
}