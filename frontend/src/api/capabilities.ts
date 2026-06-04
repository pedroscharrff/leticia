import { api } from "./client";

export type CapabilityCategory =
  | "atendimento"
  | "vendas"
  | "pagamentos_entrega"
  | "analise"
  | "inteligencia";

export interface CapabilityBlocker {
  type: "plan" | "secret" | "dependency";
  message: string;
  min_plan?: string;
  secrets?: string[];
  depends_on?: string[];
}

export interface ConfigSchemaProperty {
  type: "boolean" | "integer" | "number" | "string";
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  enum?: string[];
  format?: "textarea" | string;
}

export interface ConfigSchema {
  type?: "object";
  properties?: Record<string, ConfigSchemaProperty>;
}

export interface Capability {
  key: string;
  name: string;
  category: CapabilityCategory;
  short_desc: string;
  long_desc: string;
  impact_label: string;
  min_plan: "basic" | "pro" | "enterprise";
  depends_on: string[];
  requires_secret: string[];
  config_schema: ConfigSchema;
  default_config: Record<string, unknown>;
  config: Record<string, unknown>;
  default_enabled: boolean;
  enabled: boolean;
  status: "ga" | "beta" | "experimental";
  icon: string;
  sort_order: number;
  blockers: CapabilityBlocker[];
  available: boolean;
  updated_at: string | null;
  updated_by: string | null;
}

export async function listCapabilities(): Promise<Capability[]> {
  const res = await api.get<{ items: Capability[] }>("/portal/capabilities");
  return res.data.items;
}

export async function updateCapability(
  key: string,
  payload: { enabled?: boolean; config?: Record<string, unknown> },
): Promise<Capability> {
  const res = await api.patch<Capability>(`/portal/capabilities/${key}`, payload);
  return res.data;
}
