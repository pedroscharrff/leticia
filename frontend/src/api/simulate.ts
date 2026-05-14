import { api } from "./client";

export interface SimulateRequest {
  tenant_id: string;
  phone?: string;
  message: string;
  session_id?: string;
}

export interface SimulateResponse {
  final_response: string;
  selected_skill: string | null;
  intent: string | null;
  confidence: number | null;
  customer_profile: string | null;
  latency_ms: number;
  trace_steps: Record<string, unknown>[];
}

export const simulate = (body: SimulateRequest) =>
  api
    .post<SimulateResponse>("/admin/test/simulate", body, { timeout: 60_000 })
    .then((r) => r.data);
