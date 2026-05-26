import { api } from "./client";

export interface Offer {
  id:          string;
  title:       string;
  description: string;
  valid_from:  string | null;
  valid_until: string | null;
  priority:    number;
  active:      boolean;
  media_type:  "image" | "audio" | null;
  media_url:   string | null;
  media_mime:  string | null;
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

export async function uploadOfferMedia(id: string, file: File): Promise<Offer> {
  const form = new FormData();
  form.append("file", file);
  const res = await api.post<Offer>(`/portal/offers/${id}/media`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return res.data;
}

export async function deleteOfferMedia(id: string): Promise<Offer> {
  const res = await api.delete<Offer>(`/portal/offers/${id}/media`);
  return res.data;
}

export interface ChannelCapabilities {
  has_active_channel: boolean;
  provider:           string | null;
  supports_image:     boolean;
  supports_audio:     boolean;
}

export async function getChannelCapabilities(): Promise<ChannelCapabilities> {
  const res = await api.get<ChannelCapabilities>("/portal/channels/capabilities");
  return res.data;
}
