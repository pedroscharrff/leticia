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
  skip_rules: { path: string; equals: unknown; comment?: string }[];
  handoff_config: HandoffConfig;
  session_config: SessionConfig;
  handoff_pause_minutes: number;
  human_handoff_detection: HumanHandoffDetection;
  config_json: IntegrationConfig;
}

export interface HumanHandoffDetection {
  enabled?: boolean;
  outbound_match?: { path?: string; equals?: unknown };
  customer_phone_path?: string;
}

export interface SessionConfig {
  close_keywords?: string[];
  close_message?: string;
}

export interface IntegrationConfig {
  provider?: "clickmassa";
  base_url?: string;
  token?: string;
  external_key?: string;
  notify_order_status?: boolean;
  [key: string]: unknown;
}

export interface HandoffConfig {
  enabled?: boolean;
  provider?: "clickmassa";
  base_url?: string;
  token?: string;
  queue_id?: number | string;
  transfer_message?: string;
  trigger_keywords?: string[];
  post_handoff_order?: "summary_first" | "offers_first";
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
  skip_rules?: { path: string; equals: unknown; comment?: string }[];
  handoff_config?: HandoffConfig;
  session_config?: SessionConfig;
  handoff_pause_minutes?: number;
  human_handoff_detection?: HumanHandoffDetection;
}

export interface IntegrationInput {
  slug: string;
  name: string;
  direction?: "inbound" | "outbound" | "both";
  hmac_secret?: string;
  hmac_header?: string;
  hmac_algorithm?: string;
  enabled?: boolean;
  config_json?: IntegrationConfig;
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

// ── Handoff (transferência para atendente humano) ──
export interface HandoffTestResult {
  ok: boolean;
  status_code: number | null;
  response: unknown;
  error: string | null;
}

export const testHandoff = (
  integrationId: string,
  phone: string,
  message?: string,
) =>
  api.post<HandoffTestResult>(
    `/portal/broker/integrations/${integrationId}/handoff/test`,
    { phone, message },
  ).then((r) => r.data);

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

export interface DiscoveredFieldFromHistory {
  path: string;
  type: string;
  samples: unknown[];
  directions: string[];
  event_count: number;
}

export interface DiscoverFieldsFromHistoryResponse {
  paths: DiscoveredFieldFromHistory[];
  event_count: number;
  inbound_count: number;
  outbound_count: number;
}

export const discoverFieldsFromHistory = (integrationId: string, limit = 30) =>
  api.get<DiscoverFieldsFromHistoryResponse>(
    `/portal/broker/integrations/${integrationId}/discover-fields`,
    { params: { limit } },
  ).then((r) => r.data);

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
