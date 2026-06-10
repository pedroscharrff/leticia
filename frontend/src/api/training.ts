import { api } from "./client";

export interface TrainingDocument {
  id: string;
  title: string;
  category: string | null;
  tags: string[];
  source_type: "pdf" | "text" | "url";
  storage_url: string | null;
  original_filename: string | null;
  uploaded_by: string | null;
  status: "pending" | "processing" | "ready" | "failed";
  chunk_count: number;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface TrainingDocumentDetail extends TrainingDocument {
  chunks_preview: Array<{ chunk_index: number; tokens: number; preview: string }>;
}

export interface SearchHit {
  document_id: string;
  document_title: string;
  category: string | null;
  chunk_index: number;
  content: string;
  distance: number;
}

export async function listTrainingDocs(params: {
  status?: string;
  category?: string;
  q?: string;
} = {}): Promise<TrainingDocument[]> {
  const search = new URLSearchParams();
  if (params.status) search.set("status_filter", params.status);
  if (params.category) search.set("category", params.category);
  if (params.q) search.set("q", params.q);
  const qs = search.toString();
  const { data } = await api.get<TrainingDocument[]>(
    `/admin/training/documents${qs ? `?${qs}` : ""}`,
  );
  return data;
}

export async function getTrainingDoc(id: string): Promise<TrainingDocumentDetail> {
  const { data } = await api.get<TrainingDocumentDetail>(
    `/admin/training/documents/${id}`,
  );
  return data;
}

export async function uploadTrainingPdf(input: {
  file: File;
  title: string;
  category?: string;
  tags?: string[];
}): Promise<TrainingDocument> {
  const fd = new FormData();
  fd.append("file", input.file);
  fd.append("title", input.title);
  if (input.category) fd.append("category", input.category);
  if (input.tags?.length) fd.append("tags", input.tags.join(","));
  const { data } = await api.post<TrainingDocument>(
    "/admin/training/documents",
    fd,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return data;
}

export async function uploadTrainingText(input: {
  title: string;
  content: string;
  category?: string;
  tags?: string[];
}): Promise<TrainingDocument> {
  const { data } = await api.post<TrainingDocument>(
    "/admin/training/documents/text",
    {
      title: input.title,
      content: input.content,
      category: input.category ?? null,
      tags: input.tags ?? [],
    },
  );
  return data;
}

export async function patchTrainingDoc(
  id: string,
  patch: { title?: string; category?: string; tags?: string[] },
): Promise<TrainingDocument> {
  const { data } = await api.patch<TrainingDocument>(
    `/admin/training/documents/${id}`,
    patch,
  );
  return data;
}

export async function deleteTrainingDoc(id: string): Promise<void> {
  await api.delete(`/admin/training/documents/${id}`);
}

export async function reindexTrainingDoc(id: string): Promise<TrainingDocument> {
  const { data } = await api.post<TrainingDocument>(
    `/admin/training/documents/${id}/reindex`,
  );
  return data;
}

export async function searchTrainingKb(input: {
  query: string;
  categoria?: string;
  tags?: string[];
  k?: number;
}): Promise<SearchHit[]> {
  const { data } = await api.post<SearchHit[]>("/admin/training/search", {
    query: input.query,
    categoria: input.categoria ?? null,
    tags: input.tags ?? [],
    k: input.k ?? 5,
  });
  return data;
}
