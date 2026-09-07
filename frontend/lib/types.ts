/**
 * Shared API contracts for the Phase 7A demo UI.
 *
 * These types mirror the JSON the backend demo layer returns. The frontend is
 * only a display layer: it never classifies intents, computes risk, decides on
 * approvals, or fabricates results.
 */

export type StepState = "success" | "failed" | "waiting" | "pending";

export interface AgentStep {
  label: string;
  state: StepState;
  detail?: string | null;
  /** Present on tool steps; "MCP" or "Internal" (provider that executed). */
  provider?: string | null;
  result_data?: Record<string, unknown> | null;
}

export interface RetrievedSource {
  title: string;
  version?: string | null;
  section?: string | null;
  category?: string | null;
  citation?: string | null;
  content?: string | null;
  relevance_score?: number | null;
}

export interface RiskInfo {
  tool?: string | null;
  level?: string | null;
  level_label?: string | null;
  action?: string | null;
  reason?: string | null;
}

export interface ApprovalInfo {
  id: number;
  request_id?: string | null;
  tool_name?: string | null;
  order_ref?: string | null;
  risk_level?: string | null;
  risk_label?: string | null;
  reason?: string | null;
  status?: string | null;
  created_at?: string | null;
  expected_amount?: string | null;
}

export interface ActionInfo {
  type: "user_confirmation" | "human_approval";
  message?: string | null;
  request_id?: string | null;
  approval_id?: number | null;
}

export interface ApprovalResolution {
  approval_id: number;
  id: number;
  request_id?: string | null;
  session_id?: string | null;
  user_id?: number | null;
  user_message?: string | null;
  tool_name?: string | null;
  risk_level?: string | null;
  reason?: string | null;
  approved: boolean;
  status: "APPROVED" | "REJECTED";
  resolved_by?: string | null;
  resolved_at?: string | null;
  final_status?: string | null;
  final_agent_status?: string | null;
  execute_detail?: string | null;
  summary?: string | null;
}

export interface LlmInfo {
  enabled?: boolean | null;
  provider?: string | null;
}

export interface DemoRun {
  request_id: string;
  session_id: string;
  user_id: number;
  user_message?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  agent_status?: string | null;
  run_status: string;
  text?: string | null;
  intent?: string | null;
  route?: string | null;
  entities?: Record<string, unknown> | null;
  sources?: RetrievedSource[];
  steps?: AgentStep[];
  risk?: RiskInfo | null;
  approval?: ApprovalInfo | null;
  expected_refund?: { amount?: string | null; order_ref?: string | null } | null;
  action?: ActionInfo | null;
  approval_resolution?: ApprovalResolution | null;
  approval_id?: number | null;
  llm?: LlmInfo | null;
}

export interface OrderContext {
  order_ref: string;
  status: string;
  status_label: string;
  total_amount: string;
  currency: string;
  items_count: number;
}

export interface ApprovalItem {
  id: number;
  request_id?: string | null;
  user_id?: number | null;
  tool_name?: string | null;
  action?: string | null;
  order_ref?: string | null;
  order?: OrderContext | null;
  expected_amount?: string | null;
  risk_level?: string | null;
  reason?: string | null;
  status: string;
  created_at?: string | null;
  resolved_at?: string | null;
  resolved_by?: string | null;
  /** Present on the single-approval detail endpoint. */
  resolution?: ApprovalResolution | null;
  run?: DemoRun | null;
}

export interface ApprovalsResponse {
  pending: ApprovalItem[];
  resolved: ApprovalResolution[];
}

export interface ResolveResponse {
  approval: ApprovalItem;
  run: DemoRun;
}
