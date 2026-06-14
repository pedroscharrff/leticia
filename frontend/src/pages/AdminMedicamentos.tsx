import { useEffect, useState } from "react";
import { GlobalNav } from "../components/GlobalNav";
import { SubNav } from "../components/SubNav";
import { Spinner } from "../components/Spinner";
import { Modal } from "../components/Modal";
import {
  listBulario,
  listReferencia,
  getReferencia,
  createReferencia,
  patchReferencia,
  deleteReferencia,
  patchSecao,
  type BularioItem,
  type ReferenciaListItem,
  type ReferenciaDetail,
  type SecaoStatus,
} from "../api/medicamentos";

type Tab = "bulario" | "referencia";

const SECAO_LABEL: Record<string, string> = {
  indicacoes: "Indicações",
  posologia: "Posologia",
  contraindicacoes: "Contraindicações",
  efeitos_adversos: "Efeitos adversos",
  interacoes: "Interações",
  precaucoes: "Precauções",
};

const STATUS_LABEL: Record<SecaoStatus, string> = {
  pending: "Pendente",
  active: "Ativa",
  disabled: "Desativada",
};

export function AdminMedicamentos() {
  const [tab, setTab] = useState<Tab>("referencia");

  return (
    <>
      <GlobalNav />
      <SubNav title="Medicamentos" />
      <main className="page-content">
        <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
          <button
            className={tab === "referencia" ? "btn btn--primary" : "btn"}
            onClick={() => setTab("referencia")}
          >
            Medicamentos de Referência
          </button>
          <button
            className={tab === "bulario" ? "btn btn--primary" : "btn"}
            onClick={() => setTab("bulario")}
          >
            Bulário ANVISA
          </button>
        </div>

        {tab === "referencia" ? <ReferenciaPanel /> : <BularioPanel />}
      </main>
    </>
  );
}

// ── Bulário ANVISA (read-only) ──────────────────────────────────────────────

function BularioPanel() {
  const [items, setItems] = useState<BularioItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");

  const refresh = async () => {
    setLoading(true);
    try {
      setItems(await listBulario({ q: q || undefined, limit: 100 }));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); /* eslint-disable-next-line */ }, []);

  return (
    <section>
      <p style={{ color: "var(--color-ink-muted-48)", marginBottom: 12 }}>
        Catálogo da ANVISA cacheado localmente (somente leitura). É populado sob
        demanda conforme os agentes consultam o bulário.
      </p>
      <header style={{ display: "flex", gap: 12, marginBottom: 16 }}>
        <input
          placeholder="Buscar por nome ou princípio ativo…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void refresh(); }}
          style={{ flex: 1, padding: 8 }}
        />
        <button className="btn" onClick={() => void refresh()}>Filtrar</button>
      </header>

      {loading ? <Spinner size={28} /> : items.length === 0 ? (
        <p style={{ color: "var(--color-ink-muted-48)" }}>Nenhum medicamento.</p>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid var(--color-hairline)" }}>
              <th>Produto</th>
              <th>Princípio ativo</th>
              <th>Fabricante</th>
              <th>Classe</th>
              <th>Bula</th>
            </tr>
          </thead>
          <tbody>
            {items.map((m) => (
              <tr key={m.num_processo} style={{ borderBottom: "1px solid var(--color-divider-soft)" }}>
                <td style={{ fontWeight: 500 }}>{m.nome_produto}</td>
                <td>{m.principio_ativo ?? "—"}</td>
                <td>{m.razao_social ?? "—"}</td>
                <td>{m.classes_terapeuticas.slice(0, 2).join(", ") || "—"}</td>
                <td>{m.has_detail ? "✓" : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

// ── Medicamentos de referência ──────────────────────────────────────────────

function ReferenciaPanel() {
  const [items, setItems] = useState<ReferenciaListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [pendentes, setPendentes] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      setItems(await listReferencia({ q: q || undefined, pendentes, limit: 200 }));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); /* eslint-disable-next-line */ }, [pendentes]);

  const handleDelete = async (id: number) => {
    if (!confirm("Remover este medicamento de referência e suas seções?")) return;
    await deleteReferencia(id);
    await refresh();
  };

  return (
    <section>
      <p style={{ color: "var(--color-ink-muted-48)", marginBottom: 12 }}>
        Guia curado (marca original ↔ princípio ativo). As seções clínicas só
        ficam visíveis ao agente quando você as marca como <strong>Ativa</strong>
        — a fonte é antiga, revise antes de ativar.
      </p>
      <header style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 16 }}>
        <input
          placeholder="Buscar por princípio ativo ou marca…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void refresh(); }}
          style={{ flex: 1, padding: 8 }}
        />
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 14 }}>
          <input
            type="checkbox"
            checked={pendentes}
            onChange={(e) => setPendentes(e.target.checked)}
          />
          Só com seção pendente
        </label>
        <button className="btn" onClick={() => void refresh()}>Filtrar</button>
        <button className="btn btn--primary" onClick={() => setCreating(true)}>
          + Adicionar
        </button>
      </header>

      {loading ? <Spinner size={28} /> : items.length === 0 ? (
        <p style={{ color: "var(--color-ink-muted-48)" }}>
          Nenhum medicamento de referência. Rode a ingestão do guia ou adicione manualmente.
        </p>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid var(--color-hairline)" }}>
              <th>Princípio ativo</th>
              <th>Referência (original)</th>
              <th>Forma</th>
              <th>Categoria</th>
              <th>Seções</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((m) => (
              <tr key={m.id} style={{ borderBottom: "1px solid var(--color-divider-soft)" }}>
                <td style={{ fontWeight: 500 }}>{m.principio_ativo}</td>
                <td>{m.nome_referencia ?? "—"}</td>
                <td style={{ fontSize: 13 }}>{m.forma_farmaceutica ?? "—"}</td>
                <td style={{ fontSize: 13 }}>{m.categoria ?? "—"}</td>
                <td>
                  <span style={{
                    padding: "2px 8px", borderRadius: 4, fontSize: 12,
                    background: m.secoes_active > 0 ? "#e6f7e6" : "#f1f1f1",
                  }}>
                    {m.secoes_active}/{m.secoes_total} ativas
                  </span>
                </td>
                <td style={{ display: "flex", gap: 8 }}>
                  <button className="btn" onClick={() => setEditId(m.id)}>Editar</button>
                  <button className="btn btn--danger" onClick={() => void handleDelete(m.id)}>
                    Excluir
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editId !== null && (
        <EditModal
          id={editId}
          onClose={() => setEditId(null)}
          onSaved={() => { void refresh(); }}
        />
      )}
      {creating && (
        <CreateModal
          onClose={() => setCreating(false)}
          onCreated={async (id) => { setCreating(false); await refresh(); setEditId(id); }}
        />
      )}
    </section>
  );
}

// ── Modal de edição + curadoria ─────────────────────────────────────────────

function EditModal({
  id, onClose, onSaved,
}: { id: number; onClose: () => void; onSaved: () => void }) {
  const [detail, setDetail] = useState<ReferenciaDetail | null>(null);
  const [savingParent, setSavingParent] = useState(false);

  const load = async () => setDetail(await getReferencia(id));
  useEffect(() => { void load(); /* eslint-disable-next-line */ }, [id]);

  const saveParent = async () => {
    if (!detail) return;
    setSavingParent(true);
    try {
      await patchReferencia(id, {
        principio_ativo: detail.principio_ativo,
        nome_referencia: detail.nome_referencia,
        forma_farmaceutica: detail.forma_farmaceutica,
        categoria: detail.categoria,
      });
      onSaved();
    } finally {
      setSavingParent(false);
    }
  };

  return (
    <Modal open onClose={onClose} title={detail?.principio_ativo ?? "Carregando…"} width={720}>
      {!detail ? <Spinner size={24} /> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 520 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <Field label="Princípio ativo">
              <input value={detail.principio_ativo}
                onChange={(e) => setDetail({ ...detail, principio_ativo: e.target.value })} />
            </Field>
            <Field label="Referência (marca original)">
              <input value={detail.nome_referencia ?? ""}
                onChange={(e) => setDetail({ ...detail, nome_referencia: e.target.value || null })} />
            </Field>
            <Field label="Forma farmacêutica">
              <input value={detail.forma_farmaceutica ?? ""}
                onChange={(e) => setDetail({ ...detail, forma_farmaceutica: e.target.value || null })} />
            </Field>
            <Field label="Categoria">
              <input value={detail.categoria ?? ""}
                onChange={(e) => setDetail({ ...detail, categoria: e.target.value || null })} />
            </Field>
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button className="btn btn--primary" onClick={() => void saveParent()} disabled={savingParent}>
              {savingParent ? "Salvando…" : "Salvar dados"}
            </button>
          </div>

          <hr style={{ border: 0, borderTop: "1px solid var(--color-hairline)", margin: "4px 0" }} />
          <h4 style={{ margin: 0 }}>Seções clínicas (curadoria)</h4>
          <p style={{ fontSize: 12, color: "var(--color-ink-muted-48)", margin: 0 }}>
            Edite o texto e marque <strong>Ativa</strong> só após revisar. Apenas
            seções ativas chegam ao agente.
          </p>

          {detail.secoes.length === 0 ? (
            <p style={{ color: "var(--color-ink-muted-48)" }}>Sem seções clínicas importadas.</p>
          ) : (
            detail.secoes.map((s) => (
              <SecaoEditor
                key={s.secao}
                id={id}
                secao={s.secao}
                initialConteudo={s.conteudo}
                initialStatus={s.status}
                reviewedBy={s.reviewed_by}
                reviewedAt={s.reviewed_at}
                onSaved={() => { void load(); onSaved(); }}
              />
            ))
          )}
        </div>
      )}
    </Modal>
  );
}

function SecaoEditor({
  id, secao, initialConteudo, initialStatus, reviewedBy, reviewedAt, onSaved,
}: {
  id: number;
  secao: string;
  initialConteudo: string;
  initialStatus: SecaoStatus;
  reviewedBy: string | null;
  reviewedAt: string | null;
  onSaved: () => void;
}) {
  const [conteudo, setConteudo] = useState(initialConteudo);
  const [status, setStatus] = useState<SecaoStatus>(initialStatus);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      await patchSecao(id, secao, { conteudo, status });
      onSaved();
    } finally {
      setBusy(false);
    }
  };

  const badgeBg = status === "active" ? "#e6f7e6" : status === "disabled" ? "#fde2e2" : "#f1f1f1";

  return (
    <div style={{ border: "1px solid var(--color-hairline)", borderRadius: 6, padding: 10 }}>
      <header style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
        <strong>{SECAO_LABEL[secao] ?? secao}</strong>
        <span style={{ padding: "2px 8px", borderRadius: 4, fontSize: 12, background: badgeBg }}>
          {STATUS_LABEL[status]}
        </span>
        {reviewedBy && (
          <small style={{ color: "var(--color-ink-muted-48)" }}>
            revisado por {reviewedBy}
            {reviewedAt ? ` em ${new Date(reviewedAt).toLocaleDateString("pt-BR")}` : ""}
          </small>
        )}
      </header>
      <textarea
        value={conteudo}
        onChange={(e) => setConteudo(e.target.value)}
        style={{ width: "100%", minHeight: 90, fontSize: 13 }}
      />
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
        <select value={status} onChange={(e) => setStatus(e.target.value as SecaoStatus)}>
          <option value="pending">Pendente</option>
          <option value="active">Ativa</option>
          <option value="disabled">Desativada</option>
        </select>
        <button className="btn btn--primary" onClick={() => void save()} disabled={busy}>
          {busy ? "Salvando…" : "Salvar seção"}
        </button>
      </div>
    </div>
  );
}

function CreateModal({
  onClose, onCreated,
}: { onClose: () => void; onCreated: (id: number) => void }) {
  const [pa, setPa] = useState("");
  const [ref, setRef] = useState("");
  const [forma, setForma] = useState("");
  const [cat, setCat] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setErr(null);
    if (!pa.trim()) { setErr("Informe o princípio ativo."); return; }
    setBusy(true);
    try {
      const created = await createReferencia({
        principio_ativo: pa.trim(),
        nome_referencia: ref.trim() || null,
        forma_farmaceutica: forma.trim() || null,
        categoria: cat.trim() || null,
      });
      onCreated(created.id);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Falha ao criar.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open onClose={onClose} title="Novo medicamento de referência">
      <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 420 }}>
        <Field label="Princípio ativo">
          <input value={pa} onChange={(e) => setPa(e.target.value)} placeholder="Ex.: BUSPIRONA (CLORIDRATO)" />
        </Field>
        <Field label="Referência (marca original)">
          <input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="Ex.: BUSPAR" />
        </Field>
        <Field label="Forma farmacêutica">
          <input value={forma} onChange={(e) => setForma(e.target.value)} />
        </Field>
        <Field label="Categoria">
          <input value={cat} onChange={(e) => setCat(e.target.value)} />
        </Field>
        {err && <div style={{ color: "#b00" }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn" onClick={onClose} disabled={busy}>Cancelar</button>
          <button className="btn btn--primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Criando…" : "Criar"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
      <span style={{ color: "var(--color-ink-muted-48)" }}>{label}</span>
      {children}
    </label>
  );
}
