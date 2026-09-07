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
  ResolveResponse,
} from "./types";

const API_BASE = "/api/v1";

export class ApiRequestError extends Error {
  readonly code?: string;
  readonly status: number;

  constructor(status: number, detail: string, code?: string) {
    super(detail);
    this.status = status;
    this.code = code;
  }
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
