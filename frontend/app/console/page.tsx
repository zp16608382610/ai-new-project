import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Console",
};

export default function ConsolePage() {
  return (
    <main className="page">
      <h1>Console</h1>
      <p className="placeholder">运营控制台占位 —— 后续将在此接入 Risk Control 与 Human-in-the-loop。</p>
    </main>
  );
}