"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiRequestError,
  sendChat,
  fetchSessionRuns,
} from "@/lib/api";
import type { DemoRun, RetrievedSource } from "@/lib/types";

const CURRENT_USER = {
  id: 1,
  name: "Alice Zhang",
  label: "买家 Alice",
};

const QUICK_ACTIONS: { label: string; text: string }[] = [
  { label: "查询订单", text: "帮我查一下订单 ORD-1001" },
  { label: "查询物流", text: "订单 ORD-1001 到哪里了？" },
  { label: "咨询退款规则", text: "退款需要满足什么条件？" },
  { label: "申请退款", text: "帮我把订单 ORD-1003 退款" },
  { label: "取消订单", text: "帮我取消订单 ORD-1002" },
  { label: "创建售后工单", text: "收到货有破损，帮我创建一个售后工单（订单 ORD-1001）" },
];

/** Friendly route label used in the trace header. */
const ROUTE_LABEL: Record<string, string> = {
  RAG: "静态知识 → RAG",
  ORDER_TOOL: "订单数据 → Tool",
  LOGISTICS_TOOL: "物流数据 → Tool",
  REFUND_TOOL: "退款流程 → Tool",
  CANCEL_TOOL: "取消订单 → Tool",
  TICKET_TOOL: "售后工单 → Tool",
};

function shortTime(value?: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function routeLabel(route: string | null | undefined): string {
  if (!route) {
    return "—";
  }
  return `${route}${ROUTE_LABEL[route] ? ` · ${ROUTE_LABEL[route]}` : ""}`;
}

function riskPillClass(level?: string | null): string {
  const key = (level ?? "").toUpperCase();
  if (key === "LOW" || key === "MEDIUM" || key === "HIGH" || key === "CRITICAL") {
    return `risk-pill risk-${key}`;
  }
  return "risk-pill";
}

export default function ChatClient() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [runs, setRuns] = useState<DemoRun[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const latestRun = runs.length > 0 ? runs[runs.length - 1] : null;

  const refreshHistory = useCallback(
    async (sid: string) => {
      const history = await fetchSessionRuns(sid);
      setRuns(history);
    },
    [],
  );

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [runs, sending]);

  const send = useCallback(
    async (message: string, confirmed?: boolean) => {
      if (!message.trim() || sending) {
        return;
      }
      const text = message.trim();
      setInput("");
      setError(null);
      setSending(true);
      try {
        const run = await sendChat({
          message: text,
          user_id: CURRENT_USER.id,
          session_id: sessionId ?? undefined,
          user_confirmed: confirmed,
        });
        const sid = run.session_id || sessionId;
        if (sid) {
          setSessionId(sid);
          await refreshHistory(sid);
        } else {
          setRuns((previous) => [...previous, run]);
        }
      } catch (err) {
        if (err instanceof ApiRequestError) {
          setError(err.message);
        } else {
          setError("无法连接后端服务，请确认 FastAPI 已在 http://127.0.0.1:8000 启动。");
        }
      } finally {
        setSending(false);
      }
    },
    [sending, sessionId, refreshHistory],
  );

  const confirmOn = (message: string) => {
    void send(message, true);
  };

  const dismissOn = (message: string) => {
    void send(message, false);
  };

  const latestWaitingConfirmation =
    latestRun &&
    latestRun.action?.type === "user_confirmation" &&
    latestRun.run_status === "WAITING_USER_CONFIRMATION"
      ? latestRun
      : null;

  return (
    <main className="workbench">
      <div className="workbench-grid">
        <section className="pane chat-pane" aria-label="会话">
          <div className="pane-header">
            <h2>Chat · AI 售后客服</h2>
            <div className="meta-line">
              当前用户：<strong>{CURRENT_USER.label}</strong>
              <span className="mono">（ID {CURRENT_USER.id}）</span>
              {" · "}Session：<span className="mono">{sessionId ?? "等待首次消息自动创建"}</span>
            </div>
          </div>

          <div className="chat-scroll" ref={scrollRef}>
            {runs.length === 0 && !sending ? (
              <div className="empty-state">
                你好,我是电商售后 AI 客服(演示环境;后端开启 LLM_ENABLED 并配置 DEEPSEEK 后,由真实 DeepSeek 理解与生成回复,未配置时使用确定性流程)。
                <br />
                你可以直接输入问题，或点击下方快捷场景开始演示：
                <br />
                知识问答 → RAG；订单/物流 → Tool / MCP；取消订单 → 用户确认；退款 → 人工审批。
              </div>
            ) : null}

            {runs.map((run) => (
              <div key={run.request_id} className="message-stack">
                <div className="message-row user">
                  <div className="bubble">{run.user_message}</div>
                  <div className="meta-line">{shortTime(run.created_at)}</div>
                </div>
                <div className="message-row assistant">
                  <div className="bubble">{run.text}</div>
                  <div className="meta-line">
                    {run.route ? routeLabel(run.route) : ""}
                    {run.agent_status ? ` · ${run.agent_status}` : ""}
                  </div>
                </div>
                {run.run_status === "WAITING_USER_CONFIRMATION" &&
                run.action?.type === "user_confirmation" ? (
                  <div className="message-row assistant">
                    <div className="confirm-card">
                      <div>该操作会改变订单状态，需要你本人确认：</div>
                      <div className="card-actions">
                        <button
                          type="button"
                          className="btn btn-primary"
                          disabled={sending}
                          onClick={() => confirmOn(run.user_message ?? "")}
                        >
                          确认，继续执行
                        </button>
                        <button
                          type="button"
                          className="btn"
                          disabled={sending}
                          onClick={() => dismissOn(run.user_message ?? "")}
                        >
                          取消操作
                        </button>
                      </div>
                    </div>
                  </div>
                ) : null}
                {run.run_status === "WAITING_HUMAN_APPROVAL" &&
                run.action?.type === "human_approval" ? (
                  <div className="message-row assistant">
                    <div className="notice-card">
                      <strong>退款申请已进入人工审批。</strong>
                      <br />
                      请打开 <Link href="/console">Console</Link> 查看审批队列并进行
                      Approve / Reject。
                    </div>
                  </div>
                ) : null}
              </div>
            ))}

            {sending ? (
              <div className="message-row assistant">
                <div className="loading-row">
                  <span className="spinner" aria-hidden="true" />
                  Agent 正在处理（理解 → 检索 → 风控 → 执行）…
                </div>
              </div>
            ) : null}

            {error ? (
              <div className="message-row assistant">
                <div className="error-banner">{error}</div>
              </div>
            ) : null}
          </div>

          <div className="quick-actions">
            {QUICK_ACTIONS.map((action) => (
              <button
                key={action.label}
                type="button"
                className="chip"
                disabled={sending}
                onClick={() => void send(action.text)}
              >
                {action.label}
              </button>
            ))}
          </div>

          <form
            className="composer"
            onSubmit={(event) => {
              event.preventDefault();
              void send(input);
            }}
          >
            <textarea
              value={input}
              disabled={sending}
              placeholder="输入你的问题，例如：退款需要满足什么条件？"
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void send(input);
                }
              }}
              rows={2}
            />
            <button type="submit" className="btn btn-primary" disabled={sending || !input.trim()}>
              发送
            </button>
          </form>
        </section>

        <section className="pane trace-pane" aria-label="Agent Trace">
          <div className="pane-header">
            <h2>Agent Trace · 决策过程</h2>
          </div>
          <TracePanel run={latestRun} confirmationRun={latestWaitingConfirmation} />
        </section>
      </div>
    </main>
  );
}

function TracePanel({
  run,
  confirmationRun,
}: {
  run: DemoRun | null;
  confirmationRun: DemoRun | null;
}) {
  if (!run) {
    return (
      <div className="trace-scroll">
        <div className="trace-empty">
          发送一条消息后，这里会展示这条请求的完整 Agent 决策链路：
          <br />
          Intent → Route → Retrieval / Tool（Provider）→ Risk Gate → Approval →
          Execute → Verify → Final Response。
        </div>
      </div>
    );
  }

  const steps = (run.steps ?? []).filter((step) => step.label !== "Finalize");
  const sources = run.sources ?? [];

  return (
    <div className="trace-scroll">
      <div className="trace-section">
        <div className="trace-section">
          <h3>LLM Provider</h3>
          <div className="intent-row">
            {run.llm?.enabled ? (
              <span className="provider-badge">
                LLM Provider: {run.llm.provider ?? "DeepSeek"}
              </span>
            ) : (
              <span className="kv-tag">未启用 · 确定性流程</span>
            )}
          </div>
        </div>
        
        <h3>Understand · Intent</h3>
        <div className="intent-row">
          <span className="kv-tag">{run.intent ?? "—"}</span>
        </div>
      </div>

      <div className="trace-section">
        <h3>Route</h3>
        <div className="intent-row">
          <span className="kv-tag">{routeLabel(run.route)}</span>
        </div>
      </div>

      {run.entities && Object.keys(run.entities).length > 0 ? (
        <div className="trace-section">
          <h3>Entities</h3>
          <div className="intent-row">
            {Object.entries(run.entities).map(([key, value]) => (
              <span key={key} className="kv-tag">
                {key}: {String(value)}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {steps.length > 0 ? (
        <div className="trace-section">
          <h3>Agent Steps</h3>
          <div className="step-list">
            {steps.map((step, index) => (
              <div key={`${step.label}-${index}`} className="step-item">
                <span className={`step-dot ${step.state}`} aria-hidden="true" />
                <div className="step-main">
                  <div className="step-title">
                    {step.label}
                    {step.provider ? (
                      <span className="provider-badge">Provider: {step.provider}</span>
                    ) : null}
                  </div>
                  {step.detail ? <div className="step-detail">{step.detail}</div> : null}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {sources.length > 0 ? (
        <div className="trace-section">
          <h3>Retrieved Context（RAG）</h3>
          <SourceList sources={sources} />
        </div>
      ) : null}

      {run.risk ? (
        <div className="trace-section">
          <h3>Risk Decision</h3>
          <div>
            <span className={riskPillClass(run.risk.level)}>
              {run.risk.level_label ?? run.risk.level ?? "—"}
            </span>
            <span className="muted" style={{ marginLeft: "0.5rem" }}>
              {run.risk.action ?? ""}
            </span>
          </div>
          {run.risk.reason ? <p className="step-detail" style={{ margin: "0.3rem 0 0" }}>{run.risk.reason}</p> : null}
        </div>
      ) : null}

      {run.approval ? (
        <div className="trace-section">
          <h3>Human Approval</h3>
          <dl className="kv-list">
            <dt>审批单</dt>
            <dd className="mono">#{run.approval.id}</dd>
            <dt>风险等级</dt>
            <dd>
              <span className={riskPillClass(run.approval.risk_level)}>
                {run.approval.risk_label ?? run.approval.risk_level}
              </span>
            </dd>
            {run.approval.order_ref ? (
              <>
                <dt>订单</dt>
                <dd>{run.approval.order_ref}</dd>
              </>
            ) : null}
            {run.approval.expected_amount ? (
              <>
                <dt>退款金额（权威）</dt>
                <dd>¥{run.approval.expected_amount}</dd>
              </>
            ) : null}
            {run.approval.reason ? (
              <>
                <dt>原因</dt>
                <dd>{run.approval.reason}</dd>
              </>
            ) : null}
          </dl>
          <div className="muted" style={{ fontSize: "0.8rem", marginTop: "0.4rem" }}>
            状态：等待客服审批 → 请到 Console 处理
          </div>
        </div>
      ) : null}

      {confirmationRun ? (
        <div className="trace-section">
          <h3>User Confirmation</h3>
          <div className="confirm-card" style={{ marginTop: 0 }}>
            {confirmationRun.action?.message ?? "该操作需要您确认。"}
            <div className="card-actions">
              <button
                type="button"
                className="btn btn-primary"
                disabled
              >
                等待用户在左侧确认
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {run.approval_resolution ? (
        <div className="trace-section">
          <h3>Approval Result</h3>
          <dl className="kv-list">
            <dt>决定</dt>
            <dd>
              <span
                className={`status-pill ${
                  run.approval_resolution.approved ? "ok" : "bad"
                }`}
              >
                {run.approval_resolution.status}
              </span>
            </dd>
            {run.approval_resolution.resolved_by ? (
              <>
                <dt>处理人</dt>
                <dd>{run.approval_resolution.resolved_by}</dd>
              </>
            ) : null}
            {run.approval_resolution.final_status ? (
              <>
                <dt>执行结果</dt>
                <dd>{run.approval_resolution.final_status}</dd>
              </>
            ) : null}
            {run.approval_resolution.execute_detail ? (
              <>
                <dt>校验摘要</dt>
                <dd>{run.approval_resolution.execute_detail}</dd>
              </>
            ) : null}
          </dl>
        </div>
      ) : null}

      <div className="trace-section">
        <h3>Verification</h3>
        {steps
          .filter((step) => step.label === "Verify" || step.label === "Execute")
          .map((step) => (
            <div key={step.label} className="step-item">
              <span className={`step-dot ${step.state}`} aria-hidden="true" />
              <div className="step-main">
                <div className="step-title">{step.label}</div>
                {step.detail ? <div className="step-detail">{step.detail}</div> : null}
              </div>
            </div>
          ))}
        {run.run_status === "WAITING_HUMAN_APPROVAL" ||
        run.run_status === "WAITING_USER_CONFIRMATION" ? (
          <div className="muted" style={{ fontSize: "0.82rem" }}>
            执行与校验尚未开始，等待审批 / 确认后自动继续。
          </div>
        ) : null}
      </div>

      <div className="trace-section">
        <h3>Final Response</h3>
        <div className="bubble" style={{ background: "#eef2ff", color: "#1e1b4b" }}>
          {run.text}
        </div>
      </div>
    </div>
  );
}

function SourceList({ sources }: { sources: RetrievedSource[] }) {
  return (
    <div className="source-list">
      {sources.map((source, index) => (
        <div key={`${source.citation ?? source.title}-${index}`} className="source-item">
          <div className="source-title">
            《{source.title}》
            {source.version ? ` v${source.version}` : ""}
            {source.section ? ` · ${source.section}` : ""}
          </div>
          {source.content ? <div>{source.content}</div> : null}
          <div className="source-meta">
            {source.citation ? `引用 ${source.citation}` : ""}
            {source.relevance_score != null
              ? ` · 相关度 ${source.relevance_score.toFixed(4)}`
              : ""}
          </div>
        </div>
      ))}
    </div>
  );
}
