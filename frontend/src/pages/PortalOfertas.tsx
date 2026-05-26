/**
 * PortalOfertas — CRUD de ofertas exibidas antes do handoff.
 * Funciona com a capability `sales.pre_handoff_offers`.
 */
import { useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import { Toggle } from "../components/Toggle";
import {
  listOffers,
  createOffer,
  updateOffer,
  deleteOffer,
  type Offer,
  type OfferIn,
} from "../api/offers";

const EMPTY_DRAFT: OfferIn = {
  title: "",
  description: "",
  valid_from: null,
  valid_until: null,
  priority: 0,
  active: true,
};

// ISO 8601 ↔ valor do <input type="datetime-local">
function isoToLocal(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function localToIso(local: string): string | null {
  if (!local) return null;
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export function PortalOfertas() {
  const [offers, setOffers]   = useState<Offer[] | null>(null);
  const [error, setError]     = useState("");
  const [draft, setDraft]     = useState<OfferIn>(EMPTY_DRAFT);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy]       = useState(false);

  async function refresh() {
    try {
      setOffers(await listOffers());
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Não foi possível carregar ofertas.");
    }
  }

  useEffect(() => { void refresh(); }, []);

  function startEdit(o: Offer) {
    setEditing(o.id);
    setDraft({
      title: o.title,
      description: o.description,
      valid_from: o.valid_from,
      valid_until: o.valid_until,
      priority: o.priority,
      active: o.active,
    });
    setError("");
  }

  function cancelEdit() {
    setEditing(null);
    setDraft(EMPTY_DRAFT);
    setError("");
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      if (editing) {
        await updateOffer(editing, draft);
      } else {
        await createOffer(draft);
      }
      await refresh();
      cancelEdit();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao salvar.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    if (!confirm("Remover esta oferta?")) return;
    setBusy(true);
    try {
      await deleteOffer(id);
      await refresh();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao remover.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(o: Offer) {
    try {
      await updateOffer(o.id, { active: !o.active });
      await refresh();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao atualizar.");
    }
  }

  return (
    <PortalLayout active="ofertas">
      <header className="portal-page-header">
        <h1 className="portal-page-title">Ofertas</h1>
        <p className="portal-page-subtitle">
          Cadastre ofertas/promoções que o robô vai mostrar como última
          tentativa de retenção <strong>antes de transferir para um atendente
          humano</strong>. Só ofertas <em>ativas</em> e dentro da janela de
          validade aparecem.
          <br />
          💡 Para ligar o envio, ative a capability <strong>"Ofertas antes da
          Transferência"</strong> em <em>Vendas › Recursos do seu Robô</em>.
        </p>
      </header>

      {!offers ? (
        <div className="portal-loading"><Spinner size={28} /></div>
      ) : (
        <>
          {/* Formulário */}
          <section className="cliente-card" style={{ marginBottom: 24 }}>
            <h3 style={{ marginTop: 0 }}>
              {editing ? "Editar oferta" : "Nova oferta"}
            </h3>

            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 12 }}>
              <div className="form-group">
                <label className="form-label">Título</label>
                <input
                  className="form-input"
                  placeholder="ex.: Kit Gripe 12% OFF"
                  maxLength={200}
                  value={draft.title}
                  onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Prioridade (maior = topo)</label>
                <input
                  className="form-input"
                  type="number"
                  min="0"
                  max="1000"
                  value={draft.priority ?? 0}
                  onChange={(e) => setDraft({ ...draft, priority: parseInt(e.target.value, 10) || 0 })}
                />
              </div>
            </div>

            <div className="form-group">
              <label className="form-label">Descrição (curta, vai inteira para o cliente)</label>
              <textarea
                className="form-input"
                rows={2}
                maxLength={1000}
                placeholder="ex.: Paracetamol + xarope + soro por R$ 28,90 até domingo."
                value={draft.description ?? ""}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div className="form-group">
                <label className="form-label">Início (opcional)</label>
                <input
                  className="form-input"
                  type="datetime-local"
                  value={isoToLocal(draft.valid_from)}
                  onChange={(e) => setDraft({ ...draft, valid_from: localToIso(e.target.value) })}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Fim (opcional)</label>
                <input
                  className="form-input"
                  type="datetime-local"
                  value={isoToLocal(draft.valid_until)}
                  onChange={(e) => setDraft({ ...draft, valid_until: localToIso(e.target.value) })}
                />
              </div>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
              <Toggle
                checked={!!draft.active}
                onChange={(v) => setDraft({ ...draft, active: v })}
              />
              <span style={{ fontSize: 13 }}>Oferta ativa</span>
            </div>

            {error && <div className="form-error" style={{ marginTop: 12 }}>{error}</div>}

            <div className="cliente-form-actions" style={{ marginTop: 16 }}>
              {editing && (
                <button type="button" className="btn btn-secondary" onClick={cancelEdit}>
                  Cancelar
                </button>
              )}
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy || !draft.title.trim()}
                onClick={save}
              >
                {busy ? <Spinner size={14} /> : editing ? "Salvar alterações" : "Adicionar oferta"}
              </button>
            </div>
          </section>

          {/* Tabela */}
          <section className="cliente-card">
            <h3 style={{ marginTop: 0 }}>Ofertas cadastradas ({offers.length})</h3>
            {offers.length === 0 ? (
              <div className="cliente-empty">
                Nenhuma oferta cadastrada ainda. Adicione a primeira acima.
              </div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "1px solid #e5e7eb" }}>
                    <th style={{ padding: "8px 6px" }}>Título</th>
                    <th style={{ padding: "8px 6px" }}>Descrição</th>
                    <th style={{ padding: "8px 6px" }}>Vigência</th>
                    <th style={{ padding: "8px 6px" }}>Prioridade</th>
                    <th style={{ padding: "8px 6px" }}>Ativa</th>
                    <th style={{ padding: "8px 6px" }}></th>
                  </tr>
                </thead>
                <tbody>
                  {offers.map((o) => (
                    <tr key={o.id} style={{ borderBottom: "1px solid #f3f4f6" }}>
                      <td style={{ padding: "10px 6px", fontWeight: 500 }}>{o.title}</td>
                      <td style={{ padding: "10px 6px", color: "#4b5563", maxWidth: 320 }}>
                        {o.description || "—"}
                      </td>
                      <td style={{ padding: "10px 6px", fontSize: 12, color: "#6b7280" }}>
                        {fmtDate(o.valid_from)} → {fmtDate(o.valid_until)}
                      </td>
                      <td style={{ padding: "10px 6px" }}>{o.priority}</td>
                      <td style={{ padding: "10px 6px" }}>
                        <Toggle checked={o.active} onChange={() => toggleActive(o)} />
                      </td>
                      <td style={{ padding: "10px 6px", textAlign: "right" }}>
                        <button className="btn btn-secondary btn-sm" onClick={() => startEdit(o)}>
                          Editar
                        </button>
                        <button
                          className="btn btn-secondary btn-sm"
                          style={{ marginLeft: 6, color: "#dc2626" }}
                          onClick={() => remove(o.id)}
                        >
                          Remover
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </PortalLayout>
  );
}
