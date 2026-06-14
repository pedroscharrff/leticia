/**
 * Admin · Medicamentos (superadmin, painel GLOBAL — vale p/ todos os tenants).
 *
 * Duas abas:
 *  • Bulário ANVISA  — somente leitura (cache populado sob demanda pelos agentes).
 *  • Medicamentos de Referência — guia curado (marca ↔ princípio ativo). As seções
 *    clínicas nascem `pending` e SÓ chegam ao agente quando marcadas `active`.
 *
 * Curadoria por seção: `patchSecao(id, secao, {conteudo,status})`.
 * Curadoria em massa (este painel):
 *  • Por medicamento (no modal): `bulkSetMedSecoes(id, {status})` — "Ativar todas".
 *  • Global (na toolbar):        `bulkSetAllSecoes({status})`    — "Ativar tudo".
 *  Toda ação destrutiva/ampla pede confirm(); ativar expõe conteúdo de 2001 ao agente.
 *
 * Stats do header: `getReferenciaStats()`. Estilos em AdminMedicamentos.css.
 * Backend: api/routers/medicamentos.py. Gate determinístico real: api/services/referencia_repo.py.
 */
import { useEffect, useState } from "react";
import { GlobalNav } from "../components/GlobalNav";
import { SubNav } from "../components/SubNav";
import { Spinner } from "../components/Spinner";
import { Modal } from "../components/Modal";
import {
  listBulario,
  listReferencia,
  getReferencia,
  getReferenciaStats,
  createReferencia,
  patchReferencia,
  deleteReferencia,
  patchSecao,
  bulkSetMedSecoes,
  bulkSetAllSecoes,
  type BularioItem,
  type ReferenciaListItem,
  type ReferenciaDetail,
  type ReferenciaStats,
  type SecaoStatus,
} from "../api/medicamentos";
import "./AdminMedicamentos.css";

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

function StatusChip({ status }: { status: SecaoStatus }) {
  return <span className={`meds-chip meds-chip--${status}`}>{STATUS_LABEL[status]}</span>;
}

export function AdminMedicamentos() {
  const [tab, setTab] = useState<Tab>("referencia");

  return (
    <>
      <GlobalNav />
      <SubNav title="Medicamentos" />
      <main className="page-content">
        <div className="meds">
          <div className="meds__tabs">
            <button
              className={`meds__tab ${tab === "referencia" ? "meds__tab--on" : ""}`}
              onClick={() => setTab("referencia")}
            >
              Medicamentos de Referência
            </button>
            <button
              className={`meds__tab ${tab === "bulario" ? "meds__tab--on" : ""}`}
              onClick={() => setTab("bulario")}
            >
              Bulário ANVISA
            </button>
          </div>

          {tab === "referencia" ? <ReferenciaPanel /> : <BularioPanel />}
        </div>
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
      <p className="meds__intro">
        Catálogo da ANVISA cacheado localmente (somente leitura). É populado sob
        demanda conforme os agentes consultam o bulário.
      </p>
      <div className="meds__toolbar">
        <SearchBox value={q} onChange={setQ} onSubmit={() => void refresh()}
          placeholder="Buscar por nome ou princípio ativo…" />
        <button className="meds-btn" onClick={() => void refresh()}>Filtrar</button>
      </div>

      {loading ? <Spinner size={28} /> : items.length === 0 ? (
        <div className="meds__empty">Nenhum medicamento no cache.</div>
      ) : (
        <div className="meds__table-wrap">
          <table className="meds-table">
            <thead>
              <tr>
                <th>Produto</th>
                <th>Princípio ativo</th>
                <th>Fabricante</th>
                <th>Classe</th>
                <th>Bula</th>
              </tr>
            </thead>
            <tbody>
              {items.map((m) => (
                <tr key={m.num_processo}>
                  <td className="meds-table__pa">{m.nome_produto}</td>
                  <td>{m.principio_ativo ?? "—"}</td>
                  <td className="meds-table__muted">{m.razao_social ?? "—"}</td>
                  <td className="meds-table__muted">{m.classes_terapeuticas.slice(0, 2).join(", ") || "—"}</td>
                  <td>{m.has_detail ? "✓" : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ── Medicamentos de referência ──────────────────────────────────────────────

function ReferenciaPanel() {
  const [items, setItems] = useState<ReferenciaListItem[]>([]);
  const [stats, setStats] = useState<ReferenciaStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [pendentes, setPendentes] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      const [list, st] = await Promise.all([
        listReferencia({ q: q || undefined, pendentes, limit: 500 }),
        getReferenciaStats(),
      ]);
      setItems(list);
      setStats(st);
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

  const handleBulkAll = async (status: SecaoStatus) => {
    const verbo = status === "active" ? "ATIVAR" : status === "pending" ? "redefinir para PENDENTE" : "DESATIVAR";
    const n = stats?.secoes_total ?? 0;
    if (!confirm(
      `Isso vai ${verbo} TODAS as ${n} seções clínicas de TODOS os medicamentos.\n\n` +
      (status === "active"
        ? "Atenção: conteúdo clínico é de 2001 e passa a ser exposto ao agente. Confirma?"
        : "Confirma?")
    )) return;
    setBulkBusy(true);
    try {
      const { updated } = await bulkSetAllSecoes({ status });
      await refresh();
      alert(`${updated} seções atualizadas.`);
    } finally {
      setBulkBusy(false);
    }
  };

  const pct = stats && stats.secoes_total > 0
    ? Math.round((stats.secoes_active / stats.secoes_total) * 100) : 0;
  const pctDisabled = stats && stats.secoes_total > 0
    ? Math.round((stats.secoes_disabled / stats.secoes_total) * 100) : 0;

  return (
    <section>
      <p className="meds__intro">
        Guia curado (marca original ↔ princípio ativo). As seções clínicas só
        ficam visíveis ao agente quando marcadas como <strong>Ativa</strong> — a
        fonte é antiga, revise antes de ativar.
      </p>

      {/* Stats */}
      <div className="meds__stats">
        <Stat label="Medicamentos" value={stats?.medicamentos ?? "—"} />
        <Stat label="Seções ativas" value={stats?.secoes_active ?? "—"} variant="active" />
        <Stat label="Pendentes" value={stats?.secoes_pending ?? "—"} variant="pending" />
        <Stat label="Desativadas" value={stats?.secoes_disabled ?? "—"} variant="disabled" />
      </div>

      {/* Curation progress */}
      {stats && stats.secoes_total > 0 && (
        <div className="meds__progress">
          <div className="meds__progress-head">
            <strong>Curadoria: {pct}% ativas</strong>
            <span>{stats.secoes_active} de {stats.secoes_total} seções liberadas ao agente</span>
          </div>
          <div className="meds__bar">
            <div className="meds__bar-fill meds__bar-fill--active" style={{ width: `${pct}%` }} />
            <div className="meds__bar-fill meds__bar-fill--disabled" style={{ width: `${pctDisabled}%` }} />
          </div>
        </div>
      )}

      {/* Toolbar */}
      <div className="meds__toolbar">
        <SearchBox value={q} onChange={setQ} onSubmit={() => void refresh()}
          placeholder="Buscar por princípio ativo ou marca…" />
        <label className="meds__filter">
          <input type="checkbox" checked={pendentes} onChange={(e) => setPendentes(e.target.checked)} />
          Só com seção pendente
        </label>
        <button className="meds-btn" onClick={() => void refresh()}>Filtrar</button>
        <span className="meds__spacer" />
        <button className="meds-btn meds-btn--success" disabled={bulkBusy}
          onClick={() => void handleBulkAll("active")} title="Ativar todas as seções de todos os medicamentos">
          {bulkBusy ? "Processando…" : "✓ Ativar tudo"}
        </button>
        <button className="meds-btn" disabled={bulkBusy}
          onClick={() => void handleBulkAll("pending")} title="Redefinir todas as seções para pendente">
          Redefinir p/ pendente
        </button>
        <button className="meds-btn meds-btn--primary" onClick={() => setCreating(true)}>
          + Adicionar
        </button>
      </div>

      {loading ? <Spinner size={28} /> : items.length === 0 ? (
        <div className="meds__empty">
          Nenhum medicamento de referência. Rode a ingestão do guia ou adicione manualmente.
        </div>
      ) : (
        <div className="meds__table-wrap">
          <table className="meds-table">
            <thead>
              <tr>
                <th>Princípio ativo</th>
                <th>Referência (original)</th>
                <th>Categoria</th>
                <th>Curadoria</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((m) => {
                const p = m.secoes_total > 0 ? Math.round((m.secoes_active / m.secoes_total) * 100) : 0;
                return (
                  <tr key={m.id} onDoubleClick={() => setEditId(m.id)}>
                    <td className="meds-table__pa">
                      {m.principio_ativo}
                      {m.forma_farmaceutica && (
                        <div className="meds-table__muted">{m.forma_farmaceutica}</div>
                      )}
                    </td>
                    <td className="meds-table__ref">{m.nome_referencia ?? "—"}</td>
                    <td className="meds-table__muted">{m.categoria ?? "—"}</td>
                    <td>
                      <div className="meds-prog">
                        <div className="meds-prog__bar">
                          <div className="meds-prog__fill" style={{ width: `${p}%` }} />
                        </div>
                        <span className="meds-prog__txt">
                          <strong>{m.secoes_active}</strong>/{m.secoes_total} ativas
                        </span>
                      </div>
                    </td>
                    <td>
                      <div className="meds-table__actions">
                        <button className="meds-btn meds-btn--sm" onClick={() => setEditId(m.id)}>Editar</button>
                        <button className="meds-btn meds-btn--sm meds-btn--ghost-danger"
                          onClick={() => void handleDelete(m.id)}>Excluir</button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {editId !== null && (
        <EditModal id={editId} onClose={() => setEditId(null)} onSaved={() => { void refresh(); }} />
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

function Stat({ label, value, variant }: {
  label: string; value: number | string; variant?: "active" | "pending" | "disabled";
}) {
  return (
    <div className="meds-stat">
      <p className="meds-stat__label">{label}</p>
      <div className={`meds-stat__value ${variant ? `meds-stat__value--${variant}` : ""}`}>{value}</div>
    </div>
  );
}

function SearchBox({ value, onChange, onSubmit, placeholder }: {
  value: string; onChange: (v: string) => void; onSubmit: () => void; placeholder: string;
}) {
  return (
    <div className="meds__search">
      <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
        <circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.6" />
        <path d="M11 11l3.5 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      <input
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") onSubmit(); }}
      />
    </div>
  );
}

// ── Modal de edição + curadoria ─────────────────────────────────────────────

function EditModal({
  id, onClose, onSaved,
}: { id: number; onClose: () => void; onSaved: () => void }) {
  const [detail, setDetail] = useState<ReferenciaDetail | null>(null);
  const [savingParent, setSavingParent] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);

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

  const bulkSections = async (status: SecaoStatus) => {
    if (status === "active" &&
      !confirm("Ativar TODAS as seções deste medicamento? O conteúdo clínico (de 2001) passa a ser exposto ao agente.")) return;
    setBulkBusy(true);
    try {
      const updated = await bulkSetMedSecoes(id, { status });
      setDetail(updated);
      onSaved();
    } finally {
      setBulkBusy(false);
    }
  };

  const activeCount = detail?.secoes.filter((s) => s.status === "active").length ?? 0;

  return (
    <Modal open onClose={onClose} title={detail?.principio_ativo ?? "Carregando…"} width={760}>
      {!detail ? <Spinner size={24} /> : (
        <div className="meds-edit">
          <div className="meds-edit__grid">
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
          <div className="meds-edit__footer">
            <button className="meds-btn meds-btn--primary" onClick={() => void saveParent()} disabled={savingParent}>
              {savingParent ? "Salvando…" : "Salvar dados"}
            </button>
          </div>

          <hr className="meds-edit__divider" />

          <div className="meds-edit__section-head">
            <h4>Seções clínicas <span className="meds-table__muted">({activeCount}/{detail.secoes.length} ativas)</span></h4>
            <div className="meds-edit__bulk">
              <button className="meds-btn meds-btn--sm meds-btn--success" disabled={bulkBusy}
                onClick={() => void bulkSections("active")}>✓ Ativar todas</button>
              <button className="meds-btn meds-btn--sm" disabled={bulkBusy}
                onClick={() => void bulkSections("pending")}>Pendentes</button>
              <button className="meds-btn meds-btn--sm" disabled={bulkBusy}
                onClick={() => void bulkSections("disabled")}>Desativar todas</button>
            </div>
          </div>
          <p className="meds-edit__hint">
            Edite o texto e marque <strong>Ativa</strong> só após revisar. Apenas
            seções ativas chegam ao agente. Use os botões acima para agir em todas de uma vez.
          </p>

          {detail.secoes.length === 0 ? (
            <p className="meds-edit__hint">Sem seções clínicas importadas.</p>
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

  // Sincroniza quando o pai recarrega (ex.: após ação em massa).
  useEffect(() => { setConteudo(initialConteudo); }, [initialConteudo]);
  useEffect(() => { setStatus(initialStatus); }, [initialStatus]);

  const save = async () => {
    setBusy(true);
    try {
      await patchSecao(id, secao, { conteudo, status });
      onSaved();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`meds-sec ${status === "active" ? "meds-sec--active" : ""}`}>
      <div className="meds-sec__head">
        <strong>{SECAO_LABEL[secao] ?? secao}</strong>
        <StatusChip status={status} />
        {reviewedBy && (
          <span className="meds-sec__rev">
            revisado por {reviewedBy}
            {reviewedAt ? ` em ${new Date(reviewedAt).toLocaleDateString("pt-BR")}` : ""}
          </span>
        )}
      </div>
      <textarea value={conteudo} onChange={(e) => setConteudo(e.target.value)} />
      <div className="meds-sec__foot">
        <select value={status} onChange={(e) => setStatus(e.target.value as SecaoStatus)}>
          <option value="pending">Pendente</option>
          <option value="active">Ativa</option>
          <option value="disabled">Desativada</option>
        </select>
        <button className="meds-btn meds-btn--sm meds-btn--primary" onClick={() => void save()} disabled={busy}>
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
    <Modal open onClose={onClose} title="Novo medicamento de referência" width={520}>
      <div className="meds-edit" style={{ minWidth: 380 }}>
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
        {err && <div className="meds-err">{err}</div>}
        <div className="meds-edit__footer">
          <button className="meds-btn" onClick={onClose} disabled={busy}>Cancelar</button>
          <button className="meds-btn meds-btn--primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Criando…" : "Criar"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="meds-field">
      <span>{label}</span>
      {children}
    </label>
  );
}
