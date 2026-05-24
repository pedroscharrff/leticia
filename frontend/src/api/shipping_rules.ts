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
