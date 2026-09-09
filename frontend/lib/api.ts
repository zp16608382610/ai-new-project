/**
 * Central API client for the Phase 7A demo.
 *
 * Every request goes to the same-origin /api/v1 path; the Next dev server
 * proxies it to the FastAPI backend (see next.config.mjs). The UI never
 * bypasses the API to reach the database and never fakes a response.
 */

import type {
  ApprovalsResponse,
  DemoRun,
  EvaluationCasesResponse,
  EvaluationReport,
  ResolveResponse,
} from "./types";

const API_BASE = "/api/v1";

export class ApiRequestError extends Error {
  readonly code?: string;
  readonly status: number;
  /** True when the error response contained a parseable JSON body. */
  readonly hasJsonBody: boolean;

  constructor(status: number, detail: string, code?: string, hasJsonBody = false) {
    super(detail);
    this.status = status;
    this.code = code;
    this.hasJsonBody = hasJsonBody;
  }
}

/**
 * Liveness probe for the Render-free backend.
 *
 * Render free instances sleep after ~15 minutes without inbound requests and
 * take 30-60+ seconds to cold-start. A single request can therefore time out
 * or hit a gateway 5xx before the app answers, so this function is designed to
 * be polled by the caller:
 *
 * - every call is bounded by `timeoutMs` (AbortController), so one probe can
 *   never hang forever;
 * - it never throws; it returns a classified `BackendHealthResult` instead
 *   ("starting" = Render is still booting, "timeout"/"network" = transient,
 *   "unavailable" = the backend answered but is not usable).
 *
 * The caller owns the overall deadline and the pacing between probes.
 */
export type BackendHealthKind =
  | "starting"
  | "timeout"
  | "network"
  | "unavailable";

export type BackendHealthResult =
  | { ok: true; kind: "ok" }
  | { ok: false; kind: BackendHealthKind; httpStatus?: number };

export async function fetchBackendHealth(
  timeoutMs = 20_000,
): Promise<BackendHealthResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    await requestJson("/health", { signal: controller.signal });
    return { ok: true, kind: "ok" };
  } catch (err) {
    return classifyHealthError(err);
  } finally {
    clearTimeout(timer);
  }
}

function classifyHealthError(err: unknown): BackendHealthResult {
  if (err instanceof ApiRequestError) {
    // Render / Next rewrite answers 5xx without a JSON body while the sleeping
    // instance is still booting (the app was not reached yet). Keep polling.
    if (
      err.status === 500 ||
      err.status === 502 ||
      err.status === 503 ||
      err.status === 504
    ) {
      return { ok: false, kind: "starting", httpStatus: err.status };
    }
    // The backend answered but is not usable (e.g. route/authorization error).
    return { ok: false, kind: "unavailable", httpStatus: err.status };
  }
  if (err instanceof DOMException && err.name === "AbortError") {
    return { ok: false, kind: "timeout" };
  }
  if (err instanceof TypeError) {
    return { ok: false, kind: "network" };
  }
  return { ok: false, kind: "network" };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    const errorBody = (body ?? {}) as {
      detail?: unknown;
      code?: unknown;
    };
    const detail =
      typeof errorBody.detail === "string"
        ? errorBody.detail
        : `请求失败 (HTTP ${response.status})`;
    throw new ApiRequestError(
      response.status,
      detail,
      typeof errorBody.code === "string" ? errorBody.code : undefined,
      body !== null,
    );
  }

  return body as T;
}

export interface ChatPayload {
  message: string;
  user_id: number;
  session_id?: string;
  user_confirmed?: boolean;
}

/** Run one chat message through the real Agent workflow. */
export function sendChat(payload: ChatPayload): Promise<DemoRun> {
  return requestJson<DemoRun>("/demo/chat", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** Console queue: pending approvals + decisions resolved in this process. */
export function fetchApprovals(): Promise<ApprovalsResponse> {
  return requestJson<ApprovalsResponse>("/demo/approvals", {
    method: "GET",
  });
}

/** Full chat history of one session (assistant + user turns). */
export function fetchSessionRuns(sessionId: string): Promise<DemoRun[]> {
  return requestJson<DemoRun[]>(
    `/demo/sessions/${encodeURIComponent(sessionId)}/runs`,
    { method: "GET" },
  );
}

/** Approve a pending high-risk operation; the Agent resumes and executes. */
export function approveApproval(
  approvalId: number,
  resolvedBy: string,
): Promise<ResolveResponse> {
  return requestJson<ResolveResponse>(`/demo/approvals/${approvalId}/approve`, {
    method: "POST",
    body: JSON.stringify({ resolved_by: resolvedBy }),
  });
}

/** Reject a pending operation; nothing is executed. */
export function rejectApproval(
  approvalId: number,
  resolvedBy: string,
): Promise<ResolveResponse> {
  return requestJson<ResolveResponse>(`/demo/approvals/${approvalId}/reject`, {
    method: "POST",
    body: JSON.stringify({ resolved_by: resolvedBy }),
  });
}

/** List the fixed Phase 7C evaluation dataset. */
export function fetchEvaluationCases(): Promise<EvaluationCasesResponse> {
  return requestJson<EvaluationCasesResponse>("/demo/evaluation/cases", {
    method: "GET",
  });
}

export interface RunEvaluationPayload {
  use_llm: boolean;
  case_ids?: string[];
}

/** Run the Phase 7C evaluation dataset against the real agent. */
export function runEvaluation(
  payload: RunEvaluationPayload,
): Promise<EvaluationReport> {
  return requestJson<EvaluationReport>("/demo/evaluation/run", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
