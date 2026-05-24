import { api } from "./client";

export interface ContinuousMed {
  name: string;
  frequency_days: number;
  last_refill_at?: string | null;
  last_nudge_at?: string | null;
}

export interface CustomerMemory {
  allergies:        string[];
  continuous_meds:  ContinuousMed[];
  preferences:      Record<string, unknown>;
  segment:          string;
  ltv:              number;
  last_purchase_at: string | null;
}

export interface CustomerMemoryPatch {
  allergies?:       string[];
  continuous_meds?: ContinuousMed[];
  preferences?:     Record<string, unknown>;
  segment?:         string;
}

export async function getCustomerMemory(id: string): Promise<CustomerMemory> {
  const res = await api.get<CustomerMemory>(`/portal/customers/${id}/memory`);
  return res.data;
}

export async function updateCustomerMemory(
  id: string, patch: CustomerMemoryPatch,
): Promise<CustomerMemory> {
  const res = await api.put<CustomerMemory>(`/portal/customers/${id}/memory`, patch);
  return res.data;
}
