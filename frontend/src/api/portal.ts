import { api } from "./client";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface PortalMe {
  tenant_id: string;
  tenant_name: string;
  plan: string;
  api_key: string;
  schema_name: string;
  active: boolean;
  callback_url: string;
  user_email: string;
}

export interface UsageMetric {
  month: string;
  conversations: number;
  tokens_total: number;
  cost_usd: number;
}

export interface SkillConfig {
  skill_name: string;
  ativo: boolean;
  llm_model: string | null;
  llm_provider: string | null;
  prompt_version: string;
  config_json: Record<string, unknown>;
}

export interface ConversationLog {
  id: string;
  session_key: string;
  role: string;
  content: string;
  skill_used: string | null;
  llm_model: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  latency_ms: number | null;
  created_at: string;
}

export interface TenantUser {
  id: string;
  tenant_id: string;
  email: string;
  name: string | null;
  active: boolean;
}

export interface CreateTenantUserPayload {
  email: string;
  password: string;
  name?: string;
}

export async function getMe(): Promise<PortalMe> {
  const res = await api.get<PortalMe>("/portal/me");
  return res.data;
}

export async function getUsage(): Promise<UsageMetric[]> {
  const res = await api.get<UsageMetric[]>("/portal/usage");
  return res.data;
}

export async function getSkills(): Promise<SkillConfig[]> {
  const res = await api.get<SkillConfig[]>("/portal/skills");
  return res.data;
}

export async function updateSkill(skillName: string, data: Partial<SkillConfig>): Promise<SkillConfig> {
  const res = await api.patch<SkillConfig>(`/portal/skills/${skillName}`, data);
  return res.data;
}

export async function getLogs(limit = 50, offset = 0): Promise<ConversationLog[]> {
  const res = await api.get<ConversationLog[]>(`/portal/logs?limit=${limit}&offset=${offset}`);
  return res.data;
}

// Admin: criar usuário para uma farmácia
export async function createTenantUser(tenantId: string, payload: CreateTenantUserPayload): Promise<TenantUser> {
  const res = await api.post<TenantUser>(`/portal/auth/tenants/${tenantId}/users`, payload);
  return res.data;
}

export async function listTenantUsers(tenantId: string): Promise<TenantUser[]> {
  const res = await api.get<TenantUser[]>(`/portal/auth/tenants/${tenantId}/users`);
  return res.data;
}

// ── Channels ──────────────────────────────────────────────────────────────────

export interface Channel {
  id: string;
  channel_type: string;
  display_name: string | null;
  active: boolean;
  config_json: Record<string, unknown>;
  webhook_url: string;
}

export async function listChannels(): Promise<Channel[]> {
  return (await api.get<Channel[]>("/portal/channels")).data;
}

export async function createChannel(data: {
  channel_type: string;
  display_name?: string;
  credentials: Record<string, string>;
  config_json?: Record<string, unknown>;
}): Promise<Channel> {
  return (await api.post<Channel>("/portal/channels", data)).data;
}

export async function updateChannel(id: string, data: Partial<Channel> & { credentials?: Record<string, string> }): Promise<Channel> {
  return (await api.patch<Channel>(`/portal/channels/${id}`, data)).data;
}

export async function deleteChannel(id: string): Promise<void> {
  await api.delete(`/portal/channels/${id}`);
}

// ── Inventory / Products ──────────────────────────────────────────────────────

export interface Product {
  id: string;
  sku: string | null;
  name: string;
  brand: string | null;
  category: string | null;
  price: number | null;
  stock_qty: number;
  unit: string;
  source: string;
  active: boolean;
  tags: string[];
  updated_at: string;
}

export async function listProducts(params?: { q?: string; category?: string; limit?: number; offset?: number }): Promise<Product[]> {
  const qs = new URLSearchParams();
  if (params?.q) qs.set("q", params.q);
  if (params?.category) qs.set("category", params.category);
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset) qs.set("offset", String(params.offset));
  return (await api.get<Product[]>(`/portal/inventory/products?${qs}`)).data;
}

export async function createProduct(data: Omit<Product, "id" | "source" | "active" | "updated_at">): Promise<Product> {
  return (await api.post<Product>("/portal/inventory/products", data)).data;
}

export async function updateProduct(id: string, data: Partial<Product>): Promise<Product> {
  return (await api.patch<Product>(`/portal/inventory/products/${id}`, data)).data;
}

export async function deleteProduct(id: string): Promise<void> {
  await api.delete(`/portal/inventory/products/${id}`);
}

export async function importProductsCsv(file: File, mapping?: Record<string, string>): Promise<{ records_in: number; records_upd: number; errors: string[] }> {
  const fd = new FormData();
  fd.append("file", file);
  const qs = mapping ? `?mapping=${encodeURIComponent(JSON.stringify(mapping))}` : "";
  return (await api.post(`/portal/inventory/products/import-csv${qs}`, fd, {
    headers: { "Content-Type": "multipart/form-data" },
  })).data;
}

export async function triggerSync(connectorType: string): Promise<{ status: string; records_in: number; records_upd: number; errors: string[] }> {
  return (await api.post(`/portal/inventory/connectors/${connectorType}/sync`)).data;
}

// ── Customers ─────────────────────────────────────────────────────────────────

export interface Customer {
  id: string;
  phone: string;
  name: string | null;
  email: string | null;
  tags: string[];
  last_contact_at: string | null;
  total_orders: number;
  total_spent: number;
  lgpd_consent_at: string | null;
  created_at: string;
}

export async function listCustomers(params?: { q?: string; tag?: string; limit?: number; offset?: number }): Promise<Customer[]> {
  const qs = new URLSearchParams();
  if (params?.q) qs.set("q", params.q);
  if (params?.tag) qs.set("tag", params.tag);
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset) qs.set("offset", String(params.offset));
  return (await api.get<Customer[]>(`/portal/customers?${qs}`)).data;
}

// ── Billing ───────────────────────────────────────────────────────────────────

export interface Subscription {
  tenant_id: string;
  plan_name: string;
  provider: string;
  status: string;
  trial_ends_at: string | null;
  current_period_end: string | null;
}

export interface Invoice {
  id: string;
  status: string;
  amount_brl: number;
  due_date: string | null;
  paid_at: string | null;
  invoice_url: string | null;
  created_at: string;
}

export interface BillingUsage {
  msgs_this_month: number;
  limit_msgs: number | null;
  plan: string;
  subscription_status: string;
}

export async function getSubscription(): Promise<Subscription> {
  return (await api.get<Subscription>("/portal/billing/subscription")).data;
}

export async function getBillingUsage(): Promise<BillingUsage> {
  return (await api.get<BillingUsage>("/portal/billing/usage")).data;
}

export async function listInvoices(): Promise<Invoice[]> {
  return (await api.get<Invoice[]>("/portal/billing/invoices")).data;
}

export async function subscribeToPlan(data: {
  plan_name: string;
  provider: string;
  customer_name: string;
  customer_email: string;
  customer_doc?: string;
}): Promise<Record<string, unknown>> {
  return (await api.post("/portal/billing/subscribe", data)).data;
}

export async function cancelSubscription(): Promise<{ status: string }> {
  return (await api.post("/portal/billing/cancel")).data;
}

// ── LLM Config (BYOK vs Créditos) ────────────────────────────────────────────

export interface LLMConfig {
  mode: "byok" | "credits";
  provider: string | null;
  has_api_key: boolean;
  orchestrator_model: string | null;
  analyst_model: string | null;
  skill_model: string | null;
  ollama_base_url: string | null;
}

export interface LLMConfigUpdate {
  mode: "byok" | "credits";
  provider?: string;
  api_key?: string;
  orchestrator_model?: string;
  analyst_model?: string;
  skill_model?: string;
  ollama_base_url?: string;
}

export async function getLLMConfig(): Promise<LLMConfig> {
  return (await api.get<LLMConfig>("/portal/llm-config")).data;
}

export async function updateLLMConfig(data: LLMConfigUpdate): Promise<LLMConfig> {
  return (await api.put<LLMConfig>("/portal/llm-config", data)).data;
}

export async function removeLLMKey(): Promise<void> {
  await api.delete("/portal/llm-config/key");
}

// ── Agent Traces ──────────────────────────────────────────────────────────────

export interface AgentTrace {
  id: string;
  session_key: string;
  phone: string | null;
  message_in: string | null;
  final_response: string | null;
  skill_used: string | null;
  intent: string | null;
  confidence: number | null;
  latency_ms: number | null;
  error: string | null;
  created_at: string;
}

export interface AgentTraceDetail extends AgentTrace {
  steps: Array<{
    node: string;
    ts_ms: number;
    [key: string]: unknown;
  }>;
}

export async function listTraces(params?: {
  limit?: number;
  offset?: number;
  skill?: string;
  phone?: string;
}): Promise<AgentTrace[]> {
  const qs = new URLSearchParams();
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset) qs.set("offset", String(params.offset));
  if (params?.skill) qs.set("skill", params.skill);
  if (params?.phone) qs.set("phone", params.phone);
  return (await api.get<AgentTrace[]>(`/portal/traces?${qs}`)).data;
}

export async function getTrace(id: string): Promise<AgentTraceDetail> {
  return (await api.get<AgentTraceDetail>(`/portal/traces/${id}`)).data;
}

// ── Persona ───────────────────────────────────────────────────────────────────

export interface Persona {
  agent_name: string;
  agent_gender: "feminino" | "masculino" | "neutro";
  pharmacy_name: string | null;
  pharmacy_tagline: string | null;
  tone: "formal" | "amigavel" | "informal" | "profissional" | "divertido";
  formality: "tu" | "voce" | "senhor";
  emoji_usage: "none" | "light" | "moderate" | "heavy";
  response_length: "short" | "medium" | "long";
  language: string;
  persona_bio: string | null;
  greeting_template: string | null;
  signature: string | null;
  custom_instructions: string | null;
  forbidden_topics: string | null;
  catchphrases: string[];
  business_hours: string | null;
  location: string | null;
  delivery_info: string | null;
  payment_methods: string | null;
  website: string | null;
  instagram: string | null;
}

export async function getPersona(): Promise<Persona> {
  return (await api.get<Persona>("/portal/persona")).data;
}

export async function updatePersona(data: Partial<Persona>): Promise<Persona> {
  return (await api.put<Persona>("/portal/persona", data)).data;
}

// ── Agent prompts (per-skill overrides) ──────────────────────────────────────

export interface AgentPrompt {
  skill_name: string;
  display_name: string;
  catalog_default_prompt: string | null;
  code_default_prompt: string | null;
  system_prompt: string | null;
  extra_instructions: string | null;
  has_override: boolean;
}

export async function listAgentPrompts(): Promise<AgentPrompt[]> {
  return (await api.get<AgentPrompt[]>("/portal/agent-prompts")).data;
}

export async function updateAgentPrompt(
  skill: string,
  data: { system_prompt: string | null; extra_instructions: string | null },
): Promise<void> {
  await api.put(`/portal/agent-prompts/${skill}`, data);
}

export async function clearAgentPrompt(skill: string): Promise<void> {
  await api.delete(`/portal/agent-prompts/${skill}`);
}

// ── Admin variants (super admin can edit any tenant) ─────────────────────────

export async function adminGetPersona(tenantId: string): Promise<Persona> {
  return (await api.get<Persona>(`/admin/tenants/${tenantId}/persona`)).data;
}

export async function adminUpdatePersona(tenantId: string, data: Partial<Persona>): Promise<Persona> {
  return (await api.put<Persona>(`/admin/tenants/${tenantId}/persona`, data)).data;
}

export async function adminListAgentPrompts(tenantId: string): Promise<AgentPrompt[]> {
  return (await api.get<AgentPrompt[]>(`/admin/tenants/${tenantId}/agent-prompts`)).data;
}

export async function adminUpdateAgentPrompt(
  tenantId: string,
  skill: string,
  data: { system_prompt: string | null; extra_instructions: string | null },
): Promise<void> {
  await api.put(`/admin/tenants/${tenantId}/agent-prompts/${skill}`, data);
}

export async function adminClearAgentPrompt(tenantId: string, skill: string): Promise<void> {
  await api.delete(`/admin/tenants/${tenantId}/agent-prompts/${skill}`);
}

// ── Sales config (vendedor required fields + retry policy) ───────────────────

export interface SalesFieldOption {
  key: string;
  label: string;
}

export interface SalesConfig {
  required_fields: string[];
  max_attempts: number;
  fallback_message: string;
  available_fields: SalesFieldOption[];
}

export type SalesConfigUpdate = Partial<{
  required_fields: string[];
  max_attempts: number;
  fallback_message: string;
}>;

export async function getSalesConfig(): Promise<SalesConfig> {
  return (await api.get<SalesConfig>("/portal/sales-config")).data;
}

export async function updateSalesConfig(data: SalesConfigUpdate): Promise<SalesConfig> {
  return (await api.put<SalesConfig>("/portal/sales-config", data)).data;
}

export async function adminGetSalesConfig(tenantId: string): Promise<SalesConfig> {
  return (await api.get<SalesConfig>(`/admin/tenants/${tenantId}/sales-config`)).data;
}

export async function adminUpdateSalesConfig(
  tenantId: string,
  data: SalesConfigUpdate,
): Promise<SalesConfig> {
  return (await api.put<SalesConfig>(`/admin/tenants/${tenantId}/sales-config`, data)).data;
}

// ── Orders ────────────────────────────────────────────────────────────────────

export type OrderStatus =
  | "pending" | "confirmed" | "processing" | "shipped" | "delivered" | "cancelled";

export interface OrderListItem {
  id: string;
  status: OrderStatus;
  items_count: number;
  total: number;
  customer_name: string | null;
  customer_phone: string | null;
  created_at: string;
}

export interface OrderItem {
  produto_id: string | null;
  sku: string | null;
  name: string | null;
  qty: number;
  price: number;
  prescription_required: boolean;
}

export interface OrderCustomer {
  id: string | null;
  name: string | null;
  phone: string | null;
  email: string | null;
  doc: string | null;
  address: {
    cep: string | null;
    street: string | null;
    street_number: string | null;
    complement: string | null;
    neighborhood: string | null;
    city: string | null;
    state: string | null;
  } | null;
}

export interface OrderDetail {
  id: string;
  status: OrderStatus;
  session_key: string | null;
  items: OrderItem[];
  subtotal: number;
  discount: number;
  total: number;
  notes: string | null;
  requires_prescription: boolean;
  customer: OrderCustomer;
  created_at: string;
  updated_at: string;
}

export interface OrderMetrics {
  by_status: Record<OrderStatus, number>;
  open_count: number;
  closed_count: number;
  total_orders: number;
  revenue_today: number;
  revenue_week: number;
  revenue_month: number;
  avg_ticket_month: number;
  top_products_month: { name: string; sku: string | null; qty: number; revenue: number }[];
}

export interface OrderListFilters {
  status?: string;
  q?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}

export async function getOrderMetrics(): Promise<OrderMetrics> {
  return (await api.get<OrderMetrics>("/portal/orders/metrics")).data;
}

export async function listOrders(filters: OrderListFilters = {}): Promise<OrderListItem[]> {
  const qs = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== "" && v !== null) qs.set(k, String(v));
  });
  return (await api.get<OrderListItem[]>(`/portal/orders?${qs}`)).data;
}

export async function getOrder(id: string): Promise<OrderDetail> {
  return (await api.get<OrderDetail>(`/portal/orders/${id}`)).data;
}

export async function updateOrder(
  id: string,
  data: { status?: OrderStatus; notes?: string },
): Promise<OrderDetail> {
  return (await api.patch<OrderDetail>(`/portal/orders/${id}`, data)).data;
}

// ── Order status notification templates ──────────────────────────────────────

export interface OrderStatusMessage {
  status: OrderStatus;
  enabled: boolean;
  template: string;
}

export async function listOrderStatusMessages(): Promise<OrderStatusMessage[]> {
  return (await api.get<OrderStatusMessage[]>("/portal/order-status-messages")).data;
}

export async function updateOrderStatusMessage(
  status: OrderStatus,
  data: { enabled: boolean; template: string },
): Promise<OrderStatusMessage> {
  return (await api.put<OrderStatusMessage>(`/portal/order-status-messages/${status}`, data)).data;
}

export async function previewOrderStatusMessage(
  status: OrderStatus,
  data: { template?: string; customer_name?: string; pharmacy_name?: string | null },
): Promise<{ rendered: string }> {
  return (await api.post<{ rendered: string }>(`/portal/order-status-messages/${status}/preview`, data)).data;
}

// ── Customer detail (full + orders) ──────────────────────────────────────────

export interface CustomerOrderRow {
  id: string;
  status: OrderStatus;
  items: OrderItem[];
  subtotal: number;
  discount: number;
  total: number;
  notes: string | null;
  created_at: string;
}

export async function listCustomerOrders(
  customerId: string,
  limit: number = 100,
): Promise<CustomerOrderRow[]> {
  return (await api.get<CustomerOrderRow[]>(`/portal/customers/${customerId}/orders?limit=${limit}`)).data;
}

export interface CustomerAddress {
  cep: string | null;
  street: string | null;
  street_number: string | null;
  complement: string | null;
  neighborhood: string | null;
  city: string | null;
  state: string | null;
}

export interface CustomerDetail {
  id: string;
  phone: string;
  name: string | null;
  email: string | null;
  doc: string | null;
  birth_date: string | null;
  address: CustomerAddress;
  tags: string[];
  notes: string | null;
  last_contact_at: string | null;
  total_orders: number;
  total_spent: number;
  auto_created: boolean;
  lgpd_consent_at: string | null;
  created_at: string;
}

export interface CustomerConversation {
  session_key: string;
  role: string;
  content: string;
  skill_used: string | null;
  created_at: string;
}

export async function getCustomer(id: string): Promise<CustomerDetail> {
  return (await api.get<CustomerDetail>(`/portal/customers/${id}`)).data;
}

export async function updateCustomer(
  id: string,
  data: Partial<Omit<CustomerDetail, "id" | "phone" | "address" | "tags" | "total_orders" | "total_spent" | "auto_created" | "created_at" | "last_contact_at" | "lgpd_consent_at">> & {
    tags?: string[];
    cep?: string | null;
    street?: string | null;
    street_number?: string | null;
    complement?: string | null;
    neighborhood?: string | null;
    city?: string | null;
    state?: string | null;
  },
): Promise<CustomerDetail> {
  return (await api.patch<CustomerDetail>(`/portal/customers/${id}`, data)).data;
}

export async function listCustomerConversations(
  customerId: string,
  limit: number = 200,
): Promise<CustomerConversation[]> {
  return (await api.get<CustomerConversation[]>(`/portal/customers/${customerId}/conversations?limit=${limit}`)).data;
}
