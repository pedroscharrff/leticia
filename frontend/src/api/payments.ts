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
