import { api } from "./client";

// ── Bulário ANVISA (read-only) ──────────────────────────────────────────────

export interface BularioItem {
  num_processo: string;
  nome_produto: string;
  principio_ativo: string | null;
  razao_social: string | null;
  classes_terapeuticas: string[];
  has_detail: boolean;
  has_bula: boolean;
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

export interface BularioStats {
  total: number;
  com_detalhe: number;
  com_bula: number;
}

export async function getBularioStats(): Promise<BularioStats> {
  const { data } = await api.get<BularioStats>("/admin/medicamentos/bulario/stats");
  return data;
}

export interface BularioIngestResult {
  termo: string;
  encontrados: number;
  com_detalhe: number;
  com_bula: number;
  itens: BularioItem[];
  erro: string | null;
}

/** Consulta manual: busca na ANVISA e insere no cache local. */
export async function ingestBulario(input: {
  termo: string;
  top_n?: number;
}): Promise<BularioIngestResult> {
  const { data } = await api.post<BularioIngestResult>(
    "/admin/medicamentos/bulario/consultar",
    input,
  );
  return data;
}

export interface BularioBulkResult {
  total_termos: number;
  com_resultado: number;
  sem_resultado: number;
  com_erro: number;
  novos_no_cache: number;
  resultados: BularioIngestResult[];
}

/** Inserção em massa: vários termos numa tacada. */
export async function ingestBularioBulk(input: {
  termos: string[];
  top_n?: number;
}): Promise<BularioBulkResult> {
  const { data } = await api.post<BularioBulkResult>(
    "/admin/medicamentos/bulario/bulk",
    input,
  );
  return data;
}

export interface BularioSecao {
  secao: string;
  secao_titulo: string | null;
  conteudo: string;
  char_count: number;
}

export interface BularioDetail {
  num_processo: string;
  nome_produto: string;
  nome_comercial: string | null;
  principio_ativo: string | null;
  razao_social: string | null;
  classes_terapeuticas: string[];
  has_detail: boolean;
  fetched_at: string | null;
  detail_fetched_at: string | null;
  secoes: BularioSecao[];
}

export async function getBularioDetail(numProcesso: string): Promise<BularioDetail> {
  const { data } = await api.get<BularioDetail>(
    `/admin/medicamentos/bulario/${encodeURIComponent(numProcesso)}`,
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

// ── Curadoria em massa ──────────────────────────────────────────────────────

export interface ReferenciaStats {
  medicamentos: number;
  secoes_total: number;
  secoes_active: number;
  secoes_pending: number;
  secoes_disabled: number;
}

export async function getReferenciaStats(): Promise<ReferenciaStats> {
  const { data } = await api.get<ReferenciaStats>(
    "/admin/medicamentos/referencia/stats",
  );
  return data;
}

/** Muda o status de TODAS as seções de um medicamento de uma vez. */
export async function bulkSetMedSecoes(
  id: number,
  body: { status: SecaoStatus; only_pending?: boolean },
): Promise<ReferenciaDetail> {
  const { data } = await api.patch<ReferenciaDetail>(
    `/admin/medicamentos/referencia/${id}/secoes`,
    body,
  );
  return data;
}

/** Muda o status de seções em MASSA (todos os medicamentos). Retorna a contagem. */
export async function bulkSetAllSecoes(body: {
  status: SecaoStatus;
  secao?: string;
  only_pending?: boolean;
}): Promise<{ updated: number }> {
  const { data } = await api.post<{ updated: number }>(
    "/admin/medicamentos/referencia/bulk/status",
    body,
  );
  return data;
}

// ── Consultas (log de uso da base pelo agente) ──────────────────────────────

export interface ConsultaMedicamento {
  principio_ativo: string | null;
  nome_referencia: string | null;
}

export interface ConsultaItem {
  id: number;
  tenant_id: string | null;
  session_id: string | null;
  skill: string | null;
  termo: string;
  encontrado: boolean;
  num_resultados: number;
  medicamentos: ConsultaMedicamento[];
  secoes: string[];
  created_at: string;
}

export interface ConsultasStats {
  total: number;
  encontrados: number;
  nao_encontrados: number;
  sem_secao_ativa: number;
  por_secao: Record<string, number>;
}

export async function listConsultas(params: {
  q?: string;
  tenant_id?: string;
  skill?: string;
  encontrado?: boolean;
  secao?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<ConsultaItem[]> {
  const search = new URLSearchParams();
  if (params.q) search.set("q", params.q);
  if (params.tenant_id) search.set("tenant_id", params.tenant_id);
  if (params.skill) search.set("skill", params.skill);
  if (params.encontrado !== undefined) search.set("encontrado", String(params.encontrado));
  if (params.secao) search.set("secao", params.secao);
  if (params.limit) search.set("limit", String(params.limit));
  if (params.offset) search.set("offset", String(params.offset));
  const qs = search.toString();
  const { data } = await api.get<ConsultaItem[]>(
    `/admin/medicamentos/referencia/consultas${qs ? `?${qs}` : ""}`,
  );
  return data;
}

export async function getConsultasStats(
  params: { tenant_id?: string } = {},
): Promise<ConsultasStats> {
  const search = new URLSearchParams();
  if (params.tenant_id) search.set("tenant_id", params.tenant_id);
  const qs = search.toString();
  const { data } = await api.get<ConsultasStats>(
    `/admin/medicamentos/referencia/consultas/stats${qs ? `?${qs}` : ""}`,
  );
  return data;
}
