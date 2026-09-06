import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Chat",
};

export default function ChatPage() {
  return (
    <main className="page">
      <h1>Chat</h1>
      <p className="placeholder">会话页面占位 —— 后续将在此接入 Agent、RAG 与 Tool Calling。</p>
    </main>
  );
}