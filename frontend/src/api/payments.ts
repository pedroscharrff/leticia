import { api } from "./client";

export interface PaymentRow {
  id:          string;
  order_id:    string | null;
  phone:       string | null;
  amount:      number;
  status:      string;
  created_at:  string;
  paid_at:     string | null;
  expires_at:  string | null;
}

export interface PaymentsStatus {
  asaas_connected:  boolean;
  pending_count:    number;
  paid_last_30d:    number;
  revenue_last_30d: number;
  recent_charges:   PaymentRow[];
}

export async function getPaymentsStatus(): Promise<PaymentsStatus> {
  const res = await api.get<PaymentsStatus>("/portal/payments/status");
  return res.data;
}

export async function setAsaasKey(apiKey: string): Promise<void> {
  await api.put("/portal/payments/asaas-key", { api_key: apiKey });
}

export async function deleteAsaasKey(): Promise<void> {
  await api.delete("/portal/payments/asaas-key");
}

export interface RecoveryStats {
  carts_pending_recovery:  number;
  carts_recovered_last_7d: number;
  refill_clients_total:    number;
  refills_nudged_last_30d: number;
}

export async function getRecoveryStats(): Promise<RecoveryStats> {
  const res = await api.get<RecoveryStats>("/portal/recovery/stats");
  return res.data;
}

export type CartStatus = "pending" | "in_progress" | "recovered";

export interface CartRow {
  session_key:        string;
  phone:              string | null;
  customer_name:      string | null;
  items_count:        number;
  subtotal:           number;
  updated_at:         string;
  sent_recovery_at:   string | null;
  recovery_attempts:  number;
  status:             CartStatus;
}

export async function listCarts(): Promise<CartRow[]> {
  const res = await api.get<CartRow[]>("/portal/recovery/carts");
  return res.data;
}

export type BatchStatus =
  | "queued" | "running" | "completed"
  | "cancelled" | "undone" | "failed";

export interface RecoveryBatch {
  id:               string;
  status:           BatchStatus;
  total:            number;
  sent:             number;
  failed:           number;
  skipped:          number;
  actor_email:      string | null;
  created_at:       string;
  started_at:       string | null;
  finished_at:      string | null;
  cancel_requested: boolean;
  error:            string | null;
}

export interface TriggerResult {
  batch_id: string;
  total:    number;
}

/** Enfileira batch. `session_keys` vazio = todos os carrinhos com itens. */
export async function triggerRecovery(session_keys?: string[]): Promise<TriggerResult> {
  const res = await api.post<TriggerResult>("/portal/recovery/trigger",
    session_keys && session_keys.length ? { session_keys } : {});
  return res.data;
}

export async function listBatches(): Promise<RecoveryBatch[]> {
  const res = await api.get<RecoveryBatch[]>("/portal/recovery/batches");
  return res.data;
}

export async function getBatch(id: string): Promise<RecoveryBatch> {
  const res = await api.get<RecoveryBatch>(`/portal/recovery/batches/${id}`);
  return res.data;
}

export async function cancelBatch(id: string): Promise<RecoveryBatch> {
  const res = await api.post<RecoveryBatch>(`/portal/recovery/batches/${id}/cancel`);
  return res.data;
}

export async function undoBatch(id: string): Promise<RecoveryBatch> {
  const res = await api.post<RecoveryBatch>(`/portal/recovery/batches/${id}/undo`);
  return res.data;
}
