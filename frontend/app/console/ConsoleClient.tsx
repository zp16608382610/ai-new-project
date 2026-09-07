"use client";

import { useCallback, useEffect, useState } from "react";

import {
  ApiRequestError,
  approveApproval,
  fetchApprovals,
  rejectApproval,
} from "@/lib/api";
import type {
  ApprovalItem,
  ApprovalResolution,
  DemoRun,
} from "@/lib/types";

const OPERATOR = "demo-operator";

const ACTION_LABEL: Record<string, string> = {
  create_refund: "创建退款单",
  cancel_order: "取消订单",
  create_ticket: "创建售后工单",
};

function shortDateTime(value?: string | null): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function actionName(toolName?: string | null): string {
  if (!toolName) {
    return "—";
  }
  return `${ACTION_LABEL[toolName] ?? toolName}（${toolName}）`;
}

function riskPillClass(level?: string | null): string {
  const key = (level ?? "").toUpperCase();
  if (key === "LOW" || key === "MEDIUM" || key === "HIGH" || key === "CRITICAL") {
    return `risk-pill risk-${key}`;
  }
  return "risk-pill";
}

function riskLevelOf(item: ApprovalItem): string | undefined {
  return item.risk_level ?? item.resolution?.risk_level ?? undefined;
}

export default function ConsoleClient() {
  const [pending, setPending] = useState<ApprovalItem[]>([]);
  const [resolved, setResolved] = useState<ApprovalResolution[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [lastDecision, setLastDecision] = useState<{
    approval: ApprovalItem;
    run: DemoRun;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const data = await fetchApprovals();
      setPending(data.pending);
      setResolved(data.resolved);
    } catch (err) {
      if (err instanceof ApiRequestError) {
        setError(err.message);
      } else {
        setError("无法连接后端服务，请确认 FastAPI 已在 http://127.0.0.1:8000 启动。");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selected =
    pending.find((item) => item.id === selectedId) ??
    (lastDecision && lastDecision.approval.id === selectedId
      ? lastDecision.approval
      : null) ??
    null;

  const decide = async (approvalId: number, approved: boolean) => {
    if (busyId !== null) {
      return;
    }
    setBusyId(approvalId);
    setError(null);
    setNotice(null);
    try {
      const response = approved
        ? await approveApproval(approvalId, OPERATOR)
        : await rejectApproval(approvalId, OPERATOR);
      setLastDecision(response);
      setSelectedId(approvalId);
      setNotice(
        approved
          ? `审批单 #${approvalId} 已批准，Agent 已恢复执行并完成校验。`
          : `审批单 #${approvalId} 已拒绝，未执行任何写操作。`,
      );
      await refresh();
    } catch (err) {
      if (err instanceof ApiRequestError) {
        setError(err.message);
      } else {
        setError("审批请求失败，请稍后重试。");
      }
    } finally {
      setBusyId(null);
    }
  };

  return (
    <main className="workbench">
      <div className="console-stack">
        <div className="console-stats">
          <div className="stat-card">
            <strong>{pending.length}</strong>待审批（High-risk）
          </div>
          <div className="stat-card">
            <strong>{resolved.length}</strong>已处理（本次运行）
          </div>
          <button type="button" className="btn" onClick={() => void refresh()} disabled={loading}>
            刷新队列
          </button>
        </div>

        {error ? <div className="error-banner">{error}</div> : null}
        {notice ? (
          <div className="notice-card" style={{ borderColor: "#bbf7d0", background: "#f0fdf4", color: "#14532d" }}>
            {notice}
          </div>
        ) : null}

        <div className="console-grid">
          <section className="pane" aria-label="审批队列">
            <div className="pane-header">
              <h2>Approval Queue · 人工审批队列</h2>
            </div>
            <div className="pane-body">
              {loading ? (
                <div className="loading-row">
                  <span className="spinner" aria-hidden="true" />
                  正在加载审批队列…
                </div>
              ) : pending.length === 0 ? (
                <div className="empty-state">
                  当前没有待审批的高风险操作。
                  <br />
                  提示：在 Chat 中发起“申请退款（ORD-1003）”后，这里会出现审批单。
                </div>
              ) : (
                <div className="approval-list">
                  {pending.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className={`approval-card ${item.id === selectedId ? "selected" : ""}`}
                      onClick={() => {
                        setSelectedId(item.id);
                        setLastDecision(null);
                        setNotice(null);
                      }}
                    >
                      <div className="card-top">
                        <span className="card-title">
                          #{item.id} · {actionName(item.tool_name)}
                        </span>
                        <span className={riskPillClass(riskLevelOf(item))}>
                          {riskLevelOf(item) ?? "—"}
                        </span>
                      </div>
                      <div className="card-line">
                        {item.order_ref ? `订单 ${item.order_ref}` : "无关联订单"}
                        {item.expected_amount ? ` · 退款金额 ¥${item.expected_amount}` : ""}
                      </div>
                      <div className="card-line muted">
                        {shortDateTime(item.created_at)} · 来自用户 ID {item.user_id ?? "—"}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </section>

          <section className="pane" aria-label="审批详情">
            <div className="pane-header">
              <h2>Approval Detail · 审批详情</h2>
            </div>
            <div className="pane-body">
              {selected ? (
                <ApprovalDetail
                  item={selected}
                  run={
                    lastDecision && lastDecision.approval.id === selected.id
                      ? lastDecision.run
                      : selected.run
                  }
                  busy={busyId === selected.id}
                  onApprove={() => void decide(selected.id, true)}
                  onReject={() => void decide(selected.id, false)}
                />
              ) : (
                <div className="empty-state">
                  从左侧选择一个待审批项，查看 Agent 的风险判断、计划动作与订单权威状态，然后做出 Approve / Reject 决定。
                </div>
              )}
            </div>
          </section>
        </div>

        <section className="pane" aria-label="处理记录">
          <div className="pane-header">
            <h2>处理记录（Resolved · 本次运行）</h2>
          </div>
          <div className="pane-body">
            {resolved.length === 0 ? (
              <div className="empty-state">暂无已处理记录。审批通过后会在此显示 Resume → Execute → Verify 的结果。</div>
            ) : (
              <div className="approval-list">
                {resolved.map((item) => (
                  <div key={item.approval_id} className="approval-card" style={{ cursor: "default" }}>
                    <div className="card-top">
                      <span className="card-title">
                        #{item.approval_id} · {actionName(item.tool_name)}
                      </span>
                      <span className={`status-pill ${item.approved ? "ok" : "bad"}`}>
                        {item.status}
                      </span>
                    </div>
                    <div className="card-line">
                      {item.approved ? "审批通过 → 已执行 → 已校验" : "审批拒绝，未执行"}
                      {item.final_status ? `（${item.final_status}）` : ""}
                    </div>
                    {item.execute_detail ? (
                      <div className="card-line muted">{item.execute_detail}</div>
                    ) : null}
                    <div className="card-line muted">
                      {shortDateTime(item.resolved_at)} · {item.resolved_by ?? "—"}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}

function ApprovalDetail({
  item,
  run,
  busy,
  onApprove,
  onReject,
}: {
  item: ApprovalItem;
  run?: DemoRun | null;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const isPending = item.status === "PENDING";
  const resolution = item.resolution ?? run?.approval_resolution ?? null;
  const runSteps = (run?.steps ?? []).filter((step) => step.label !== "Finalize");
  const resolved = resolution !== null;

  return (
    <div>
      <div className="card-top" style={{ marginBottom: "0.6rem" }}>
        <span className="card-title" style={{ fontSize: "1rem" }}>
          审批单 #{item.id}
        </span>
        {isPending ? (
          <span className="status-pill warn">PENDING</span>
        ) : (
          <span className={`status-pill ${item.status === "APPROVED" ? "ok" : "bad"}`}>
            {item.status}
          </span>
        )}
      </div>

      <dl className="kv-list">
        <dt>风险等级</dt>
        <dd>
          <span className={riskPillClass(riskLevelOf(item))}>{riskLevelOf(item) ?? "—"}</span>
        </dd>
        <dt>Agent Action</dt>
        <dd>{actionName(item.tool_name)}</dd>
        <dt>风险原因</dt>
        <dd>{item.reason ?? "—"}</dd>
        <dt>关联订单</dt>
        <dd>{item.order_ref ?? "—"}</dd>
        {item.order ? (
          <>
            <dt>订单当前状态</dt>
            <dd>
              {item.order.status_label}（{item.order.status}）
            </dd>
            <dt>订单金额</dt>
            <dd>
              ¥{item.order.total_amount} {item.order.currency} · {item.order.items_count} 件商品
            </dd>
          </>
        ) : null}
        {item.expected_amount ? (
          <>
            <dt>退款金额（权威）</dt>
            <dd>¥{item.expected_amount}</dd>
          </>
        ) : null}
        <dt>提交时间</dt>
        <dd>{shortDateTime(item.created_at)}</dd>
      </dl>

      {isPending ? (
        <div className="divider" />
      ) : null}

      {isPending ? (
        <div>
          <div className="notice-card" style={{ marginBottom: "0.7rem" }}>
            高风险操作不能直接执行：审批通过后，Agent 会恢复执行 create_refund 并使用订单权威金额，再与数据库状态核对。
          </div>
          <div className="card-actions">
            <button type="button" className="btn btn-success" disabled={busy} onClick={onApprove}>
              {busy ? "处理中…" : "Approve · 批准并执行"}
            </button>
            <button type="button" className="btn btn-danger" disabled={busy} onClick={onReject}>
              {busy ? "处理中…" : "Reject · 拒绝"}
            </button>
          </div>
        </div>
      ) : null}

      {resolved ? (
        <div className="divider" />
      ) : null}

      {resolved ? (
        <div className="flow-steps">
          <div className="step-item">
            <span className={`step-dot ${resolution.approved ? "success" : "failed"}`} aria-hidden="true" />
            <div className="step-main">
              <div className="step-title">
                {resolution.approved ? "Approved → Resume" : "Rejected"}
              </div>
              <div className="step-detail">
                {resolution.approved
                  ? "已批准，Agent 恢复执行原始 ToolRequest 快照。"
                  : "已拒绝，未执行任何写操作。"}
              </div>
            </div>
          </div>

          {resolution.approved ? (
            <>
              {runSteps.map((step, index) => (
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
              {run?.text ? (
                <div className="step-item">
                  <span className="step-dot success" aria-hidden="true" />
                  <div className="step-main">
                    <div className="step-title">Final Response</div>
                    <div className="step-detail">{run.text}</div>
                  </div>
                </div>
              ) : null}
            </>
          ) : null}

          <div className="card-top">
            <span>
              {resolution.approved ? (
                <span className="status-pill ok">Resolved · Verified</span>
              ) : (
                <span className="status-pill bad">Resolved · Not Executed</span>
              )}
            </span>
            <span className="muted" style={{ fontSize: "0.8rem" }}>
              {shortDateTime(resolution.resolved_at)} · {resolution.resolved_by ?? "—"}
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
