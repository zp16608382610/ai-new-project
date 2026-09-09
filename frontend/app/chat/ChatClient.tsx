"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiRequestError,
  fetchBackendHealth,
  sendChat,
  fetchSessionRuns,
} from "@/lib/api";
import type { BackendHealthResult, ChatPayload } from "@/lib/api";
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

const WAKE_POLL_INTERVAL_MS = 3_000;
const HEALTH_PROBE_TIMEOUT_MS = 20_000;
const WAKE_TOTAL_TIMEOUT_MS = 120_000;
const WAKE_TOTAL_SECONDS = WAKE_TOTAL_TIMEOUT_MS / 1000;
const STARTUP_MESSAGE = "AI 服务正在启动，首次加载可能需要 30–60 秒，请稍候…";

const sleep = (ms: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, ms));

type ChatFailureKind = "cold_start" | "ambiguous" | "app_error";

/**
 * Classify a failed chat POST:
 * - "cold_start": the gateway answered 5xx without a JSON body, so the request
 *   never reached the Agent; waking once and retrying once is safe.
 * - "ambiguous": a network/timeout failure where we cannot prove the backend
 *   did NOT process the request; never auto-resubmit here.
 * - "app_error": the backend itself answered (JSON body or non-gateway status).
 */
function classifyChatFailure(err: unknown): ChatFailureKind {
  if (err instanceof ApiRequestError) {
    const isGatewayStatus =
      err.status === 500 ||
      err.status === 502 ||
      err.status === 503 ||
      err.status === 504;
    if (isGatewayStatus && !err.hasJsonBody) {
      return "cold_start";
    }
    return "app_error";
  }
  return "ambiguous";
}

function chatErrorMessage(err: unknown): string {
  if (err instanceof ApiRequestError) {
    if (err.hasJsonBody && err.message) {
      return `请求未完成：${err.message}`;
    }
    return `请求未完成（HTTP ${err.status}），请稍后重试。`;
  }
  return "网络暂时失败，无法连接后端服务。";
}

/** Final message shown only after the whole wake window has been exhausted. */
function wakeTimeoutMessage(lastProbe: BackendHealthResult): string {
  if (!lastProbe.ok && lastProbe.kind === "unavailable") {
    return `后端已响应但状态异常（HTTP ${lastProbe.httpStatus ?? "-"}），已自动等待约 ${WAKE_TOTAL_SECONDS} 秒仍不可用。请确认后端健康检查正常后重试。`;
  }
  return `已自动等待约 ${WAKE_TOTAL_SECONDS} 秒，后端仍未完成冷启动（Render 免费实例冷启动较慢或暂时不可达）。请点击下方“重试连接”，无需手动访问后端。`;
}

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
  const [backendReady, setBackendReady] = useState<boolean | null>(null);
  const [waking, setWaking] = useState(true);
  const [wakeFailure, setWakeFailure] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const warmupStartedRef = useRef(false);
  const probeInFlightRef = useRef<Promise<BackendHealthResult> | null>(null);
  const wakeInFlightRef = useRef<Promise<boolean> | null>(null);

  const latestRun = runs.length > 0 ? runs[runs.length - 1] : null;
  const busy = sending || waking;

  const refreshHistory = useCallback(
    async (sid: string) => {
      const history = await fetchSessionRuns(sid);
      setRuns(history);
    },
    [],
  );

  const storeRun = useCallback(
    async (run: DemoRun) => {
      const sid = run.session_id || sessionId;
      if (sid) {
        setSessionId(sid);
        await refreshHistory(sid);
      } else {
        setRuns((previous) => [...previous, run]);
      }
    },
    [sessionId, refreshHistory],
  );

  // Single-flight health probe: concurrent callers share one in-flight request
  // instead of firing overlapping /api/v1/health polls.
  const probeBackend = useCallback(
    (timeoutMs: number): Promise<BackendHealthResult> => {
      if (probeInFlightRef.current) {
        return probeInFlightRef.current;
      }
      const probe = fetchBackendHealth(timeoutMs).finally(() => {
        if (probeInFlightRef.current === probe) {
          probeInFlightRef.current = null;
        }
      });
      probeInFlightRef.current = probe;
      return probe;
    },
    [],
  );

  // Single-flight wake: page-open auto-wake, manual retry and the one safe chat
  // retry all share the same in-flight wake, so no two poll loops can overlap
  // (StrictMode double effects included). Total wait is capped at
  // WAKE_TOTAL_TIMEOUT_MS; the last probe is shrunk to the remaining budget.
  const startWake = useCallback((): Promise<boolean> => {
    if (wakeInFlightRef.current) {
      return wakeInFlightRef.current;
    }
    setWaking(true);
    const wake = (async (): Promise<boolean> => {
      let lastProbe: BackendHealthResult = { ok: false, kind: "timeout" };
      const deadline = Date.now() + WAKE_TOTAL_TIMEOUT_MS;
      try {
        while (mountedRef.current) {
          const remaining = deadline - Date.now();
          if (remaining <= 0) {
            break;
          }
          lastProbe = await probeBackend(
            Math.min(HEALTH_PROBE_TIMEOUT_MS, remaining),
          );
          if (!mountedRef.current) {
            return false;
          }
          if (lastProbe.ok) {
            setBackendReady(true);
            setWakeFailure(null);
            return true;
          }
          // Backend answered but is unusable: polling more cannot help.
          if (lastProbe.kind === "unavailable") {
            break;
          }
          const pause = Math.min(
            WAKE_POLL_INTERVAL_MS,
            Math.max(0, deadline - Date.now()),
          );
          if (pause <= 0) {
            break;
          }
          await sleep(pause);
        }
        if (mountedRef.current) {
          setBackendReady(false);
          setWakeFailure(wakeTimeoutMessage(lastProbe));
        }
        return false;
      } finally {
        if (mountedRef.current) {
          setWaking(false);
        }
      }
    })();
    const run = wake.finally(() => {
      if (wakeInFlightRef.current === wake) {
        wakeInFlightRef.current = null;
      }
    });
    wakeInFlightRef.current = run;
    return run;
  }, [probeBackend]);

  const retryWake = useCallback(() => {
    setWakeFailure(null);
    void startWake();
  }, [startWake]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [runs, sending]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Render 免费实例约 15 分钟无请求会休眠。页面一打开即自动唤醒:发出同源
  // /api/v1/health(经 Next rewrite 转发到后端),health 未就绪时在总体时限内
  // 串行轮询。整个过程不阻塞页面渲染,只显示启动状态并禁用发送;只在超过
  // 最大等待时间后才展示失败信息。warmupStartedRef + 单飞 startWake 共同保证
  // React StrictMode / 重复挂载不会启动第二个轮询。
  useEffect(() => {
    if (warmupStartedRef.current) {
      return;
    }
    warmupStartedRef.current = true;
    void startWake();
  }, [startWake]);

  const attemptChat = useCallback(
    async (payload: ChatPayload): Promise<"ok" | "cold_start" | "failed"> => {
      let run: DemoRun;
      try {
        run = await sendChat(payload);
      } catch (err) {
        const kind = classifyChatFailure(err);
        if (kind === "cold_start") {
          // Gateway 5xx without a JSON body: the request never reached the
          // Agent, so waking the backend and retrying once is safe.
          return "cold_start";
        }
        setError(
          kind === "ambiguous"
            ? "网络中断或请求超时，无法确认后端是否已处理该消息。为避免重复操作，请先查看会话历史，不要直接重发。"
            : chatErrorMessage(err),
        );
        return "failed";
      }
      try {
        await storeRun(run);
      } catch {
        // The business request already succeeded; only the history refresh
        // failed. Never re-submit a business request in this case.
        setError("消息已成功处理，但会话记录刷新失败。请勿重复发送，刷新页面即可看到最新会话。");
        return "failed";
      }
      return "ok";
    },
    [storeRun],
  );

  const sendInner = useCallback(
    async (text: string, confirmed?: boolean) => {
      const payload: ChatPayload = {
        message: text,
        user_id: CURRENT_USER.id,
        session_id: sessionId ?? undefined,
        user_confirmed: confirmed,
      };
      const first = await attemptChat(payload);
      if (first === "ok") {
        return;
      }
      if (first !== "cold_start") {
        return; // attemptChat 已显示具体错误,不自动重发。
      }
      // 后端在两次请求之间再次休眠(距上次请求约 15 分钟):唤醒后最多安全重发
      // 一次。只有“确认请求未到达后端”的冷启动失败才会走到这里。
      const ready = await startWake();
      if (!ready) {
        if (mountedRef.current) {
          setError("后端未能在等待时间内就绪，本次消息未发送。后端恢复后请重新发送。");
        }
        return;
      }
      const retried = await attemptChat(payload);
      if (retried === "cold_start") {
        if (mountedRef.current) {
          setError("后端刚恢复又暂时不可用，本次消息未发送。请稍后重试。");
        }
      }
    },
    [attemptChat, startWake, sessionId],
  );

  const send = useCallback(
    async (message: string, confirmed?: boolean) => {
      if (!message.trim() || busy) {
        return;
      }
      const text = message.trim();
      setInput("");
      setError(null);
      setSending(true);
      try {
        // Inputs stay disabled until backendReady === true, so this path only
        // runs when health already succeeded; sendInner handles a re-sleep.
        await sendInner(text, confirmed);
      } finally {
        setSending(false);
      }
    },
    [busy, sendInner],
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

          {backendReady !== true ? (
            <div
              className={`startup-banner${waking ? "" : " startup-banner-error"}`}
              role={waking ? "status" : "alert"}
            >
              {waking ? (
                <>
                  <span className="spinner" aria-hidden="true" />
                  <span>
                    {STARTUP_MESSAGE}
                    <br />
                    <span className="muted">
                      无需手动访问后端，页面会自动等待并重试。
                    </span>
                  </span>
                </>
              ) : (
                <>
                  <span>{wakeFailure ?? "后端尚未就绪。"}</span>
                  <button type="button" className="btn" onClick={retryWake}>
                    重试连接
                  </button>
                </>
              )}
            </div>
          ) : null}

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
                          disabled={busy || backendReady !== true}
                          onClick={() => confirmOn(run.user_message ?? "")}
                        >
                          确认，继续执行
                        </button>
                        <button
                          type="button"
                          className="btn"
                          disabled={busy || backendReady !== true}
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

            {sending && !waking ? (
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
                disabled={busy || backendReady !== true}
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
              disabled={busy || backendReady !== true}
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
            <button type="submit" className="btn btn-primary" disabled={busy || backendReady !== true || !input.trim()}>
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
