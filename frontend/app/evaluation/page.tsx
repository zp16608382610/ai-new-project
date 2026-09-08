"use client";

import { useState } from "react";

import {
  ApiRequestError,
  runEvaluation,
} from "@/lib/api";
import type {
  EvaluationCaseRow,
  EvaluationExpected,
  EvaluationReport,
} from "@/lib/types";

const METRIC_ORDER = [
  "INTENT",
  "ENTITY",
  "ROUTE",
  "RISK",
  "APPROVAL",
  "EXECUTION",
  "VERIFICATION",
];

function formatRate(rate: number | null | undefined): string {
  return rate == null ? "N/A" : `${rate}%`;
}

function formatBool(value: boolean | null | undefined): string {
  if (value == null) {
    return "N/A";
  }
  return value ? "Yes" : "No";
}

function outcomeLabel(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  return value.replace(/_/g, " ");
}

export default function EvaluationPage() {
  const [report, setReport] = useState<EvaluationReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [useLlm, setUseLlm] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const run = async () => {
    setLoading(true);
    setError(null);
    setReport(null);
    setExpanded(new Set());
    try {
      const result = await runEvaluation({ use_llm: useLlm });
      setReport(result);
    } catch (err) {
      if (err instanceof ApiRequestError) {
        setError(err.message);
      } else {
        setError("无法运行评测：请确认后端服务已启动（http://127.0.0.1:8000）。");
      }
    } finally {
      setLoading(false);
    }
  };

  const toggleCase = (caseId: string) => {
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(caseId)) {
        next.delete(caseId);
      } else {
        next.add(caseId);
      }
      return next;
    });
  };

  const metrics = report?.metrics ?? {};
  const total = report?.total_cases ?? 0;

  return (
    <main className="page" style={{ maxWidth: 1200 }}>
      <h1>Evaluation · Agent 系统性评测</h1>
      <p className="placeholder" style={{ marginBottom: "0.4rem" }}>
        固定数据集 + 真实 Agent 流程（同一套 /demo/chat 工作流）。每次评测使用一个隔离的临时 SQLite
        数据库执行，因此退款/取消的写操作不会污染演示数据，也不会伪造分数。
      </p>

      <div className="eval-run-bar">
        <label className="eval-check">
          <input
            type="checkbox"
            checked={useLlm}
            onChange={(event) => setUseLlm(event.target.checked)}
            disabled={loading}
          />
          使用 DeepSeek LLM（实时，较慢）
        </label>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void run()}
          disabled={loading}
        >
          {loading ? "正在评测（约 20–60 秒）…" : "运行评测"}
        </button>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      {report ? (
        <>
          <section className="eval-summary-card">
            <div className="eval-summary-row">
              <span>
                Total Cases: <strong>{total}</strong>
              </span>
              <span className="eval-ok">
                Passed: <strong>{report.passed}</strong>
              </span>
              <span className="eval-bad">
                Failed: <strong>{report.failed}</strong>
              </span>
              <span className="muted">
                N/A: {report.na} · 模式:{" "}
                {report.mode === "live" ? "Live (LLM)" : "Deterministic (offline)"}
              </span>
              <span className="muted">{report.ran_at}</span>
            </div>
          </section>

          <section className="metric-grid">
            {METRIC_ORDER.map((key) => {
              const metric = metrics[key];
              if (!metric) {
                return null;
              }
              return (
                <div key={key} className="metric-tile">
                  <div className="metric-label">{metric.label}</div>
                  <div className="metric-rate">{formatRate(metric.rate)}</div>
                  <div className="metric-detail muted">
                    {metric.passed}/{metric.applicable} applicable
                  </div>
                </div>
              );
            })}
          </section>

          <section className="case-table-section">
            <h2>Case Table</h2>
            <div className="case-table-wrap">
              <table className="case-table">
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>Input</th>
                    <th>Expected Outcome</th>
                    <th>Actual Outcome</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {report.cases.map((row) => (
                    <CaseRow
                      key={row.case_id}
                      row={row}
                      expanded={expanded.has(row.case_id)}
                      onToggle={() => toggleCase(row.case_id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : null}

      {!report && !loading ? (
        <p className="placeholder">
          点击“运行评测”后，这里会展示 9 类共 {total || "11"} 个场景的
          Intent / Entity / Route / Risk / Approval / Execution / Verification
          指标与逐条对比。指标仅针对明确预期的维度计分，不适用的维度标记为 N/A。
        </p>
      ) : null}
    </main>
  );
}

function CaseRow({
  row,
  expanded,
  onToggle,
}: {
  row: EvaluationCaseRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  const statusClass =
    row.status === "PASS" ? "ok" : row.status === "FAIL" ? "bad" : "";
  return (
    <>
      <tr className="case-row" onClick={onToggle}>
        <td>
          <span className="case-id">{row.case_id}</span>
          <span className="muted"> · {row.category}</span>
        </td>
        <td className="case-input">{row.user_message}</td>
        <td>{outcomeLabel(row.expected.outcome)}</td>
        <td>{outcomeLabel(row.actual.outcome)}</td>
        <td>
          <span className={`status-pill ${statusClass}`}>{row.status}</span>
        </td>
      </tr>
      {expanded ? (
        <tr className="case-detail-row">
          <td colSpan={5}>
            <div className="case-detail">
              <p className="step-detail">{row.scenario}</p>
              {row.failure_reasons.length > 0 ? (
                <ul className="failure-list">
                  {row.failure_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              ) : (
                <p className="muted">全部预期维度通过。</p>
              )}
              <ExpectedActual expected={row.expected} actual={row.actual} />
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function ExpectedActual({
  expected,
  actual,
}: {
  expected: EvaluationExpected;
  actual: EvaluationCaseRow["actual"];
}) {
  const rows: { label: string; expected: string; actual: string }[] = [
    {
      label: "Intent",
      expected: expected.intent ?? "N/A",
      actual: actual.intent ?? "N/A",
    },
    {
      label: "Route",
      expected: expected.route ?? "N/A",
      actual: actual.route ?? "N/A",
    },
    {
      label: "Order",
      expected: expected.order_id ?? "N/A",
      actual: actual.order_id ?? "N/A",
    },
    {
      label: "Risk",
      expected: expected.risk_level ?? "N/A",
      actual: actual.risk_level ?? "N/A",
    },
    {
      label: "Risk Action",
      expected: expected.risk_action ?? "N/A",
      actual: actual.risk_action ?? "N/A",
    },
    {
      label: "Approval",
      expected: formatBool(expected.requires_approval),
      actual: formatBool(actual.requires_approval),
    },
    {
      label: "Execution",
      expected: formatBool(expected.execution_success),
      actual: formatBool(actual.execution_success),
    },
    {
      label: "Verify",
      expected: formatBool(expected.verification_success),
      actual: formatBool(actual.verification_success),
    },
  ];
  return (
    <table className="expected-actual">
      <thead>
        <tr>
          <th>Dimension</th>
          <th>Expected</th>
          <th>Actual</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((item) => (
          <tr key={item.label}>
            <td>{item.label}</td>
            <td>{item.expected}</td>
            <td>{item.actual}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
