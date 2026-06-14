import { api } from "./client";

// ── Bulário ANVISA (read-only) ──────────────────────────────────────────────

export interface BularioItem {
  num_processo: string;
  nome_produto: string;
  principio_ativo: string | null;
  razao_social: string | null;
  classes_terapeuticas: string[];
  has_detail: boolean;
}

export async function listBulario(params: {
  q?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<BularioItem[]> {
  const search = new URLSearchParams();
  if (params.q) search.set("q", params.q);
  if (params.limit) search.set("limit", String(params.limit));
  if (params.offset) search.set("offset", String(params.offset));
  const qs = search.toString();
  const { data } = await api.get<BularioItem[]>(
    `/admin/medicamentos/bulario${qs ? `?${qs}` : ""}`,
  );
  return data;
}

// ── Medicamentos de referência ──────────────────────────────────────────────

export type SecaoStatus = "pending" | "active" | "disabled";

export interface ReferenciaSecao {
  secao: string;
  conteudo: string;
  status: SecaoStatus;
  reviewed_at: string | null;
  reviewed_by: string | null;
}

export interface ReferenciaListItem {
  id: number;
  principio_ativo: string;
  nome_referencia: string | null;
  forma_farmaceutica: string | null;
  categoria: string | null;
  secoes_active: number;
  secoes_total: number;
}

export interface ReferenciaDetail {
  id: number;
  principio_ativo: string;
  nome_referencia: string | null;
  forma_farmaceutica: string | null;
  categoria: string | null;
  source: string | null;
  page_ref: number | null;
  secoes: ReferenciaSecao[];
}

export async function listReferencia(params: {
  q?: string;
  pendentes?: boolean;
  limit?: number;
  offset?: number;
} = {}): Promise<ReferenciaListItem[]> {
  const search = new URLSearchParams();
  if (params.q) search.set("q", params.q);
  if (params.pendentes) search.set("pendentes", "true");
  if (params.limit) search.set("limit", String(params.limit));
  if (params.offset) search.set("offset", String(params.offset));
  const qs = search.toString();
  const { data } = await api.get<ReferenciaListItem[]>(
    `/admin/medicamentos/referencia${qs ? `?${qs}` : ""}`,
  );
  return data;
}

export async function getReferencia(id: number): Promise<ReferenciaDetail> {
  const { data } = await api.get<ReferenciaDetail>(
    `/admin/medicamentos/referencia/${id}`,
  );
  return data;
}

export async function createReferencia(input: {
  principio_ativo: string;
  nome_referencia?: string | null;
  forma_farmaceutica?: string | null;
  categoria?: string | null;
}): Promise<ReferenciaDetail> {
  const { data } = await api.post<ReferenciaDetail>(
    "/admin/medicamentos/referencia",
    input,
  );
  return data;
}

export async function patchReferencia(
  id: number,
  patch: Partial<{
    principio_ativo: string;
    nome_referencia: string | null;
    forma_farmaceutica: string | null;
    categoria: string | null;
  }>,
): Promise<ReferenciaDetail> {
  const { data } = await api.patch<ReferenciaDetail>(
    `/admin/medicamentos/referencia/${id}`,
    patch,
  );
  return data;
}

export async function deleteReferencia(id: number): Promise<void> {
  await api.delete(`/admin/medicamentos/referencia/${id}`);
}

export async function patchSecao(
  id: number,
  secao: string,
  patch: { conteudo?: string; status?: SecaoStatus },
): Promise<ReferenciaSecao> {
  const { data } = await api.patch<ReferenciaSecao>(
    `/admin/medicamentos/referencia/${id}/secoes/${secao}`,
    patch,
  );
  return data;
}
