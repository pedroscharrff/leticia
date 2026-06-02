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

export type CartStatus = "pending" | "in_progress" | "recovered" | "expired";

export interface CartItemPreview {
  nome:       string;
  quantidade: number;
  preco:      number;
}

export interface CartRow {
  session_key:        string;
  phone:              string | null;
  customer_name:      string | null;
  items_count:        number;
  items_preview:      CartItemPreview[];
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

/** Força encerramento de batch travado em queued/running (worker crashado). */
export async function dismissBatch(id: string): Promise<RecoveryBatch> {
  const res = await api.post<RecoveryBatch>(`/portal/recovery/batches/${id}/dismiss`);
  return res.data;
}

// ── Template editável da mensagem ───────────────────────────────────────────

export interface TemplatePlaceholder { key: string; desc: string; }

export interface RecoveryTemplate {
  template:     string;
  is_default:   boolean;
  default:      string;
  placeholders: TemplatePlaceholder[];
}

export async function getTemplate(): Promise<RecoveryTemplate> {
  const res = await api.get<RecoveryTemplate>("/portal/recovery/template");
  return res.data;
}

export async function updateTemplate(template: string): Promise<RecoveryTemplate> {
  const res = await api.put<RecoveryTemplate>("/portal/recovery/template", { template });
  return res.data;
}

export async function previewTemplate(
  template?: string,
  session_key?: string,
): Promise<{ rendered: string; used_sample: boolean }> {
  const res = await api.post<{ rendered: string; used_sample: boolean }>(
    "/portal/recovery/template/preview",
    { template, session_key },
  );
  return res.data;
}

export async function undoBatch(id: string): Promise<RecoveryBatch> {
  const res = await api.post<RecoveryBatch>(`/portal/recovery/batches/${id}/undo`);
  return res.data;
}

// ── Régua da recuperação (delay + tentativas + horário silencioso) ─────────

export interface RecoveryConfig {
  delay_minutes:   number;   // 1..1440
  max_attempts:    number;   // 1..5
  quiet_start:     string;   // "HH:MM"
  quiet_end:       string;   // "HH:MM"
  default_minutes: number;
}

export async function getRecoveryConfig(): Promise<RecoveryConfig> {
  const res = await api.get<RecoveryConfig>("/portal/recovery/config");
  return res.data;
}

export async function updateRecoveryConfig(cfg: {
  delay_minutes: number;
  max_attempts:  number;
  quiet_start:   string;
  quiet_end:     string;
}): Promise<RecoveryConfig> {
  const res = await api.put<RecoveryConfig>("/portal/recovery/config", cfg);
  return res.data;
}

// ── Expiração automática do carrinho após mensagem de recuperação ──────────

export interface ExpireConfig {
  expire_minutes:  number;   // 0 = desativado
  default_minutes: number;
  min_minutes:     number;
  max_minutes:     number;
}

export async function getExpireConfig(): Promise<ExpireConfig> {
  const res = await api.get<ExpireConfig>("/portal/recovery/expire-config");
  return res.data;
}

export async function updateExpireConfig(expire_minutes: number): Promise<ExpireConfig> {
  const res = await api.put<ExpireConfig>("/portal/recovery/expire-config",
    { expire_minutes });
  return res.data;
}

export async function getExpireTemplate(): Promise<RecoveryTemplate> {
  const res = await api.get<RecoveryTemplate>("/portal/recovery/expire-template");
  return res.data;
}

export async function updateExpireTemplate(template: string): Promise<RecoveryTemplate> {
  const res = await api.put<RecoveryTemplate>("/portal/recovery/expire-template",
    { template });
  return res.data;
}

export async function previewExpireTemplate(
  template?: string,
  session_key?: string,
): Promise<{ rendered: string; used_sample: boolean }> {
  const res = await api.post<{ rendered: string; used_sample: boolean }>(
    "/portal/recovery/expire-template/preview",
    { template, session_key },
  );
  return res.data;
}

// ── Resumo de pedido enviado após handoff (sales.order_summary_after_handoff)
// Página dedicada PortalResumoPedido. UI edita os 5 campos juntos (header/
// item/show_total/total_label/footer), por isso um único endpoint /config.

export interface OrderSummaryPlaceholder { key: string; desc: string; }

export interface OrderSummaryConfig {
  header_text:   string;
  item_template: string;
  show_total:    boolean;
  total_label:   string;
  footer_text:   string;
  is_default:    boolean;
  defaults:      Omit<OrderSummaryConfig, "is_default" | "defaults" | "placeholders" | "enabled">;
  placeholders:  OrderSummaryPlaceholder[];
  enabled:       boolean;
}

export interface OrderSummaryConfigPatch {
  header_text?:   string | null;
  item_template?: string | null;
  show_total?:    boolean | null;
  total_label?:   string | null;
  footer_text?:   string | null;
}

export interface OrderSummaryPreview {
  rendered:    string;
  used_sample: boolean;
}

export async function getOrderSummaryConfig(): Promise<OrderSummaryConfig> {
  const res = await api.get<OrderSummaryConfig>("/portal/order-summary/config");
  return res.data;
}

export async function updateOrderSummaryConfig(
  patch: OrderSummaryConfigPatch,
): Promise<OrderSummaryConfig> {
  const res = await api.put<OrderSummaryConfig>("/portal/order-summary/config", patch);
  return res.data;
}

export async function previewOrderSummary(
  patch: OrderSummaryConfigPatch & { session_key?: string; no_prices?: boolean },
): Promise<OrderSummaryPreview> {
  const res = await api.post<OrderSummaryPreview>(
    "/portal/order-summary/preview", patch,
  );
  return res.data;
}
