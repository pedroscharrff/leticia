import { api } from "./client";

export interface Tenant {
  id: string;
  name: string;
  callback_url: string;
  plan: "basic" | "pro" | "enterprise";
  schema_name: string;
  active: boolean;
  api_key: string;
  created_at: string;
}

export interface TenantCreate {
  name: string;
  callback_url: string;
  plan: "basic" | "pro" | "enterprise";
}

export interface SkillConfig {
  skill_name: string;
  ativo: boolean;
  llm_model: string | null;
  llm_provider: string | null;
  prompt_version: string;
  config_json: Record<string, unknown>;
}

export interface UsageMetric {
  month: string;
  conversations: number;
  tokens_total: number;
  cost_usd: number;
}

export interface SystemOverview {
  total_tenants: number;
  active_tenants: number;
  total_conversations_this_month: number;
}

// ── Tenants ──────────────────────────────────────────────────────────────────

export const listTenants = () =>
  api.get<Tenant[]>("/admin/tenants").then((r) => r.data);

export const getTenant = (id: string) =>
  api.get<Tenant>(`/admin/tenants/${id}`).then((r) => r.data);

export const createTenant = (body: TenantCreate) =>
  api.post<Tenant>("/admin/tenants", body).then((r) => r.data);

export const updateTenant = (id: string, body: TenantCreate) =>
  api.patch<Tenant>(`/admin/tenants/${id}`, body).then((r) => r.data);

export const toggleTenant = (id: string) =>
  api.patch<Tenant>(`/admin/tenants/${id}/toggle`).then((r) => r.data);

export const deleteTenant = (id: string) =>
  api.delete(`/admin/tenants/${id}`);

// ── Skills ────────────────────────────────────────────────────────────────────

export const listSkills = (tenantId: string) =>
  api.get<SkillConfig[]>(`/admin/tenants/${tenantId}/skills`).then((r) => r.data);

export const updateSkill = (
  tenantId: string,
  skillName: string,
  body: Partial<SkillConfig>
) =>
  api
    .patch<SkillConfig>(`/admin/tenants/${tenantId}/skills/${skillName}`, body)
    .then((r) => r.data);

export const seedSkills = (tenantId: string) =>
  api.post(`/admin/tenants/${tenantId}/skills/seed`).then((r) => r.data);

// ── Metrics ───────────────────────────────────────────────────────────────────

export const getOverview = () =>
  api.get<SystemOverview>("/admin/overview").then((r) => r.data);

export const getUsage = (tenantId: string) =>
  api.get<UsageMetric[]>(`/admin/tenants/${tenantId}/usage`).then((r) => r.data);
