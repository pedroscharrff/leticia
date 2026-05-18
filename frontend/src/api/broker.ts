import { api } from "./client";

export interface Integration {
  id: string;
  slug: string;
  name: string;
  direction: "inbound" | "outbound" | "both";
  hmac_header: string | null;
  hmac_algorithm: string | null;
  has_secret: boolean;
  enabled: boolean;
  inbound_url: string;
  inbound_field_map: Record<string, string>;
  reply_mode: "response" | "forward";
  reply_url: string | null;
  reply_method: string;
  reply_headers: Record<string, string>;
  reply_body_template: Record<string, string>;
  reply_status_code: number;
  bundle_enabled: boolean;
  bundle_window_seconds: number;
}

export interface FlowConfig {
  inbound_field_map: Record<string, string>;
  reply_mode: "response" | "forward";
  reply_url?: string | null;
  reply_method?: string;
  reply_headers?: Record<string, string>;
  reply_body_template: Record<string, string>;
  reply_status_code?: number;
  bundle_enabled?: boolean;
  bundle_window_seconds?: number;
}

export interface IntegrationInput {
  slug: string;
  name: string;
  direction?: "inbound" | "outbound" | "both";
  hmac_secret?: string;
  hmac_header?: string;
  hmac_algorithm?: string;
  enabled?: boolean;
}

export interface Mapping {
  id: string;
  integration_id: string;
  canonical_event: string;
  match_rules: Record<string, unknown>;
  field_map: Record<string, unknown>;
  direction: "inbound" | "outbound";
  enabled: boolean;
  version: number;
}

export interface MappingInput {
  canonical_event: string;
  match_rules?: Record<string, unknown>;
  field_map?: Record<string, unknown>;
  direction?: "inbound" | "outbound";
  enabled?: boolean;
}

export interface OutboundTarget {
  id: string;
  canonical_event: string;
  url: string;
  method: string;
  headers: Record<string, string>;
  field_map: Record<string, unknown>;
  enabled: boolean;
}

export interface OutboundInput {
  canonical_event: string;
  url: string;
  method?: string;
  headers?: Record<string, string>;
  field_map?: Record<string, unknown>;
  enabled?: boolean;
}

export interface DiscoveredPath {
  path: string;
  type: string;
  sample: string;
}

export interface RawEvent {
  id: string;
  integration_slug: string;
  direction: string;
  status: string;
  canonical_event: string | null;
  error: string | null;
  attempts: number;
  created_at: string;
  idempotency_key?: string | null;
  payload_preview?: string | null;
  forward_status_code?: number | null;
}

// ── Integrations ──
export const listIntegrations = () =>
  api.get<Integration[]>("/portal/broker/integrations").then((r) => r.data);

export const createIntegration = (body: IntegrationInput) =>
  api.post<Integration>("/portal/broker/integrations", body).then((r) => r.data);

export const updateIntegration = (id: string, body: Partial<IntegrationInput>) =>
  api.patch<Integration>(`/portal/broker/integrations/${id}`, body).then((r) => r.data);

export const deleteIntegration = (id: string) =>
  api.delete(`/portal/broker/integrations/${id}`);

export const saveFlow = (id: string, body: FlowConfig) =>
  api.put<Integration>(`/portal/broker/integrations/${id}/flow`, body).then((r) => r.data);

// ── Mappings ──
export const listMappings = (integrationId: string) =>
  api.get<Mapping[]>(`/portal/broker/integrations/${integrationId}/mappings`).then((r) => r.data);

export const createMapping = (integrationId: string, body: MappingInput) =>
  api.post<Mapping>(`/portal/broker/integrations/${integrationId}/mappings`, body).then((r) => r.data);

export const updateMapping = (mappingId: string, body: Partial<MappingInput>) =>
  api.patch<Mapping>(`/portal/broker/mappings/${mappingId}`, body).then((r) => r.data);

export const deleteMapping = (mappingId: string) =>
  api.delete(`/portal/broker/mappings/${mappingId}`);

// ── Outbound ──
export const listOutbound = (integrationId: string) =>
  api.get<OutboundTarget[]>(`/portal/broker/integrations/${integrationId}/outbound`).then((r) => r.data);

export const createOutbound = (integrationId: string, body: OutboundInput) =>
  api.post<OutboundTarget>(`/portal/broker/integrations/${integrationId}/outbound`, body).then((r) => r.data);

export const updateOutbound = (id: string, body: Partial<OutboundInput>) =>
  api.patch<OutboundTarget>(`/portal/broker/outbound/${id}`, body).then((r) => r.data);

export const deleteOutbound = (id: string) =>
  api.delete(`/portal/broker/outbound/${id}`);

// ── Tools ──
export const discoverPaths = (payload: unknown) =>
  api.post<{ paths: DiscoveredPath[] }>("/portal/broker/discover", { payload })
     .then((r) => r.data.paths);

export const previewMapping = (
  payload: unknown,
  match_rules: Record<string, unknown>,
  field_map: Record<string, unknown>,
) =>
  api.post<{ matched: boolean; result: Record<string, unknown> }>(
    "/portal/broker/preview",
    { payload, match_rules, field_map },
  ).then((r) => r.data);

// ── Events ──
export const listRawEvents = (status?: string, limit = 50) =>
  api.get<RawEvent[]>("/portal/broker/raw-events", {
    params: { limit, status_filter: status },
  }).then((r) => r.data);

export const getRawEvent = (id: string) =>
  api.get(`/portal/broker/raw-events/${id}`).then((r) => r.data);

export const replayEvent = (id: string) =>
  api.post(`/portal/broker/raw-events/${id}/replay`).then((r) => r.data);
