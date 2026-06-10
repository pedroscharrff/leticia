import { useEffect, useState } from "react";
import { GlobalNav } from "../components/GlobalNav";
import { SubNav } from "../components/SubNav";
import { Spinner } from "../components/Spinner";
import { Modal } from "../components/Modal";
import {
  listTrainingDocs,
  uploadTrainingPdf,
  uploadTrainingText,
  deleteTrainingDoc,
  reindexTrainingDoc,
  searchTrainingKb,
  type TrainingDocument,
  type SearchHit,
} from "../api/training";

type Tab = "docs" | "search";

const STATUS_LABEL: Record<TrainingDocument["status"], string> = {
  pending:    "Aguardando",
  processing: "Processando",
  ready:      "Pronto",
  failed:     "Falhou",
};

export function AdminTraining() {
  const [tab, setTab] = useState<Tab>("docs");
  const [docs, setDocs] = useState<TrainingDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [showUpload, setShowUpload] = useState(false);
  const [filter, setFilter] = useState({ status: "", category: "", q: "" });

  const refresh = async () => {
    setLoading(true);
    try {
      const data = await listTrainingDocs({
        status:   filter.status   || undefined,
        category: filter.category || undefined,
        q:        filter.q        || undefined,
      });
      setDocs(data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); /* eslint-disable-next-line */ }, []);

  const handleDelete = async (id: string) => {
    if (!confirm("Remover este documento e todos os seus chunks?")) return;
    await deleteTrainingDoc(id);
    await refresh();
  };

  const handleReindex = async (id: string) => {
    await reindexTrainingDoc(id);
    await refresh();
  };

  return (
    <>
      <GlobalNav />
      <SubNav title="Treinamentos" />

      <main className="page-content">
        <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
          <button
            className={tab === "docs" ? "btn btn--primary" : "btn"}
            onClick={() => setTab("docs")}
          >
            Documentos
          </button>
          <button
            className={tab === "search" ? "btn btn--primary" : "btn"}
            onClick={() => setTab("search")}
          >
            Testar busca
          </button>
        </div>

        {tab === "docs" && (
          <section>
            <header style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 16 }}>
              <input
                placeholder="Buscar pelo título…"
                value={filter.q}
                onChange={(e) => setFilter((f) => ({ ...f, q: e.target.value }))}
                onKeyDown={(e) => { if (e.key === "Enter") void refresh(); }}
                style={{ flex: 1, padding: 8 }}
              />
              <select
                value={filter.status}
                onChange={(e) => { setFilter((f) => ({ ...f, status: e.target.value })); }}
              >
                <option value="">Todos status</option>
                <option value="pending">Aguardando</option>
                <option value="processing">Processando</option>
                <option value="ready">Pronto</option>
                <option value="failed">Falhou</option>
              </select>
              <button className="btn" onClick={() => void refresh()}>Filtrar</button>
              <button className="btn btn--primary" onClick={() => setShowUpload(true)}>
                + Adicionar documento
              </button>
            </header>

            {loading ? (
              <Spinner size={28} />
            ) : docs.length === 0 ? (
              <p style={{ color: "var(--color-ink-muted-48)" }}>
                Nenhum documento. Adicione PDFs (sítios de ligação, interações
                medicamentosas etc.) para o farmacêutico consultar.
              </p>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "1px solid var(--color-hairline)" }}>
                    <th>Título</th>
                    <th>Categoria</th>
                    <th>Tags</th>
                    <th>Status</th>
                    <th>Chunks</th>
                    <th>Atualizado</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {docs.map((d) => (
                    <tr key={d.id} style={{ borderBottom: "1px solid var(--color-divider-soft)" }}>
                      <td>
                        <div style={{ fontWeight: 500 }}>{d.title}</div>
                        {d.original_filename && (
                          <small style={{ color: "var(--color-ink-muted-48)" }}>
                            {d.original_filename}
                          </small>
                        )}
                      </td>
                      <td>{d.category ?? "—"}</td>
                      <td>{d.tags.join(", ") || "—"}</td>
                      <td>
                        <span style={{
                          padding: "2px 8px", borderRadius: 4, fontSize: 12,
                          background: d.status === "ready" ? "#e6f7e6"
                                     : d.status === "failed" ? "#fde2e2"
                                     : "#f1f1f1",
                        }}>
                          {STATUS_LABEL[d.status]}
                        </span>
                        {d.error && (
                          <div style={{ color: "#b00", fontSize: 11, marginTop: 4 }}>
                            {d.error}
                          </div>
                        )}
                      </td>
                      <td>{d.chunk_count}</td>
                      <td style={{ fontSize: 12 }}>
                        {new Date(d.updated_at).toLocaleString("pt-BR")}
                      </td>
                      <td style={{ display: "flex", gap: 8 }}>
                        <button className="btn" onClick={() => void handleReindex(d.id)}>
                          Reindexar
                        </button>
                        <button className="btn btn--danger" onClick={() => void handleDelete(d.id)}>
                          Excluir
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        )}

        {tab === "search" && <SearchPanel />}
      </main>

      {showUpload && (
        <UploadModal
          onClose={() => setShowUpload(false)}
          onUploaded={async () => { setShowUpload(false); await refresh(); }}
        />
      )}
    </>
  );
}

// ── Upload modal ───────────────────────────────────────────────────────────

function UploadModal({ onClose, onUploaded }: { onClose: () => void; onUploaded: () => void }) {
  const [mode, setMode] = useState<"pdf" | "text">("pdf");
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("");
  const [tagsCsv, setTagsCsv] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setErr(null);
    if (!title.trim()) { setErr("Informe o título."); return; }
    const tags = tagsCsv.split(",").map((t) => t.trim()).filter(Boolean);
    setBusy(true);
    try {
      if (mode === "pdf") {
        if (!file) { setErr("Selecione um PDF."); setBusy(false); return; }
        await uploadTrainingPdf({ file, title, category: category || undefined, tags });
      } else {
        if (!content.trim()) { setErr("Informe o texto."); setBusy(false); return; }
        await uploadTrainingText({ title, content, category: category || undefined, tags });
      }
      onUploaded();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Falha no upload.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open onClose={onClose} title="Novo documento">
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "flex", gap: 12 }}>
          <label><input type="radio" checked={mode === "pdf"} onChange={() => setMode("pdf")} /> PDF</label>
          <label><input type="radio" checked={mode === "text"} onChange={() => setMode("text")} /> Texto</label>
        </div>
        <input
          placeholder="Título (ex.: Sítios de Ligação — IECA)"
          value={title} onChange={(e) => setTitle(e.target.value)}
        />
        <input
          placeholder="Categoria (ex.: sitios_ligacao, interacoes, dosagem_pediatrica)"
          value={category} onChange={(e) => setCategory(e.target.value)}
        />
        <input
          placeholder="Tags (separadas por vírgula)"
          value={tagsCsv} onChange={(e) => setTagsCsv(e.target.value)}
        />
        {mode === "pdf" ? (
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        ) : (
          <textarea
            placeholder="Cole o texto…"
            value={content} onChange={(e) => setContent(e.target.value)}
            style={{ minHeight: 220 }}
          />
        )}
        {err && <div style={{ color: "#b00" }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn" onClick={onClose} disabled={busy}>Cancelar</button>
          <button className="btn btn--primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Enviando…" : "Enviar"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ── Search panel ───────────────────────────────────────────────────────────

function SearchPanel() {
  const [query, setQuery] = useState("");
  const [categoria, setCategoria] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    if (!query.trim()) return;
    setBusy(true);
    try {
      const data = await searchTrainingKb({
        query,
        categoria: categoria || undefined,
        k: 5,
      });
      setHits(data);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <p style={{ color: "var(--color-ink-muted-48)", marginBottom: 12 }}>
        Faça uma consulta como o farmacêutico faria. Use isto para validar se a
        base devolve o trecho certo antes de soltar pro agente.
      </p>
      <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
        <input
          placeholder="Pergunta (ex.: interação omeprazol e clopidogrel)"
          value={query} onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void run(); }}
          style={{ flex: 1, padding: 8 }}
        />
        <input
          placeholder="Categoria (opcional)"
          value={categoria} onChange={(e) => setCategoria(e.target.value)}
          style={{ width: 220, padding: 8 }}
        />
        <button className="btn btn--primary" onClick={() => void run()} disabled={busy}>
          Buscar
        </button>
      </div>
      {busy ? <Spinner size={24} /> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {hits.length === 0 && query && (
            <p style={{ color: "var(--color-ink-muted-48)" }}>Nenhum resultado.</p>
          )}
          {hits.map((h, i) => (
            <article
              key={`${h.document_id}-${h.chunk_index}-${i}`}
              style={{ border: "1px solid var(--color-hairline)", borderRadius: 4, padding: 12 }}
            >
              <header style={{ fontSize: 13, color: "var(--color-ink-muted-48)", marginBottom: 6 }}>
                <strong>{h.document_title}</strong>
                {h.category && <> · {h.category}</>}
                <> · trecho {h.chunk_index} · distância {h.distance.toFixed(4)}</>
              </header>
              <div style={{ whiteSpace: "pre-wrap" }}>{h.content}</div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
