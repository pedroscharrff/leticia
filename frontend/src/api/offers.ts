import { api } from "./client";

export interface Offer {
  id:          string;
  title:       string;
  description: string;
  valid_from:  string | null;
  valid_until: string | null;
  priority:    number;
  active:      boolean;
  created_at:  string;
  updated_at:  string;
}

export interface OfferIn {
  title:        string;
  description?: string;
  valid_from?:  string | null;
  valid_until?: string | null;
  priority?:    number;
  active?:      boolean;
}

export async function listOffers(): Promise<Offer[]> {
  const res = await api.get<Offer[]>("/portal/offers");
  return res.data;
}

export async function createOffer(payload: OfferIn): Promise<Offer> {
  const res = await api.post<Offer>("/portal/offers", payload);
  return res.data;
}

export async function updateOffer(
  id: string, patch: Partial<OfferIn>,
): Promise<Offer> {
  const res = await api.patch<Offer>(`/portal/offers/${id}`, patch);
  return res.data;
}

export async function deleteOffer(id: string): Promise<void> {
  await api.delete(`/portal/offers/${id}`);
}
