import { api } from "./client";

export interface ShippingRule {
  id:           string;
  label:        string;
  cep_start:    string;
  cep_end:      string;
  valor:        number;
  prazo_dias:   number;
  gratis_acima: number | null;
  active:       boolean;
  sort_order:   number;
}

export interface ShippingRuleIn {
  label:        string;
  cep_start:    string;
  cep_end:      string;
  valor:        number;
  prazo_dias:   number;
  gratis_acima?: number | null;
  active?:      boolean;
  sort_order?:  number;
}

export async function listShippingRules(): Promise<ShippingRule[]> {
  const res = await api.get<ShippingRule[]>("/portal/shipping-rules");
  return res.data;
}

export async function createShippingRule(payload: ShippingRuleIn): Promise<ShippingRule> {
  const res = await api.post<ShippingRule>("/portal/shipping-rules", payload);
  return res.data;
}

export async function updateShippingRule(
  id: string, patch: Partial<ShippingRuleIn>,
): Promise<ShippingRule> {
  const res = await api.patch<ShippingRule>(`/portal/shipping-rules/${id}`, patch);
  return res.data;
}

export async function deleteShippingRule(id: string): Promise<void> {
  await api.delete(`/portal/shipping-rules/${id}`);
}

// ── Origem da farmácia + modo de frete ──────────────────────────────────────

export type ShippingMode = "cep_table" | "distance";
export type DistanceSource = "haversine" | "google";

export interface ShippingOrigin {
  mode:             ShippingMode;
  distance_source:  DistanceSource;
  cep:              string | null;
  lat:              number | null;
  lng:              number | null;
  resolved_address: string | null;
  geocoded:         boolean;
  google_available: boolean;
}

export interface ShippingOriginIn {
  mode: ShippingMode;
  distance_source?: DistanceSource;
  cep?: string | null;
}

export async function getShippingOrigin(): Promise<ShippingOrigin> {
  const res = await api.get<ShippingOrigin>("/portal/shipping-origin");
  return res.data;
}

export async function putShippingOrigin(payload: ShippingOriginIn): Promise<ShippingOrigin> {
  const res = await api.put<ShippingOrigin>("/portal/shipping-origin", payload);
  return res.data;
}

// ── Faixas de raio (km) ─────────────────────────────────────────────────────

export interface ShippingTier {
  id:              string;
  label:           string;
  max_distance_km: number;
  valor:           number;
  prazo_dias:      number;
  gratis_acima:    number | null;
  active:          boolean;
  sort_order:      number;
}

export interface ShippingTierIn {
  label:           string;
  max_distance_km: number;
  valor:           number;
  prazo_dias:      number;
  gratis_acima?:   number | null;
  active?:         boolean;
  sort_order?:     number;
}

export async function listShippingTiers(): Promise<ShippingTier[]> {
  const res = await api.get<ShippingTier[]>("/portal/shipping-tiers");
  return res.data;
}

export async function createShippingTier(payload: ShippingTierIn): Promise<ShippingTier> {
  const res = await api.post<ShippingTier>("/portal/shipping-tiers", payload);
  return res.data;
}

export async function updateShippingTier(
  id: string, patch: Partial<ShippingTierIn>,
): Promise<ShippingTier> {
  const res = await api.patch<ShippingTier>(`/portal/shipping-tiers/${id}`, patch);
  return res.data;
}

export async function deleteShippingTier(id: string): Promise<void> {
  await api.delete(`/portal/shipping-tiers/${id}`);
}
