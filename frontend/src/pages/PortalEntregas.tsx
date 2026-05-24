/**
 * PortalEntregas — CRUD de regras de frete por CEP.
 * Funciona com a capability `delivery.shipping_by_cep`.
 */
import { useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import { Toggle } from "../components/Toggle";
import {
  listShippingRules,
  createShippingRule,
  updateShippingRule,
  deleteShippingRule,
  type ShippingRule,
  type ShippingRuleIn,
} from "../api/shipping_rules";

const fmtMoney = (n: number) =>
  n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

const EMPTY_DRAFT: ShippingRuleIn = {
  label: "",
  cep_start: "",
  cep_end: "",
  valor: 0,
  prazo_dias: 2,
  gratis_acima: null,
  active: true,
  sort_order: 100,
};

function maskCep(raw: string): string {
  const digits = raw.replace(/\D/g, "").slice(0, 8);
  if (digits.length <= 5) return digits;
  return `${digits.slice(0, 5)}-${digits.slice(5)}`;
}

export function PortalEntregas() {
  const [rules, setRules]     = useState<ShippingRule[] | null>(null);
  const [error, setError]     = useState("");
  const [draft, setDraft]     = useState<ShippingRuleIn>(EMPTY_DRAFT);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy]       = useState(false);

  async function refresh() {
    try {
      setRules(await listShippingRules());
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Não foi possível carregar regras.");
    }
  }

  useEffect(() => { void refresh(); }, []);

  function startEdit(r: ShippingRule) {
    setEditing(r.id);
    setDraft({
      label: r.label, cep_start: r.cep_start, cep_end: r.cep_end,
      valor: r.valor, prazo_dias: r.prazo_dias,
      gratis_acima: r.gratis_acima, active: r.active, sort_order: r.sort_order,
    });
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
        await updateShippingRule(editing, draft);
      } else {
        await createShippingRule(draft);
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
    if (!confirm("Remover esta regra de frete?")) return;
    setBusy(true);
    try {
      await deleteShippingRule(id);
      await refresh();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao remover.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(r: ShippingRule) {
    try {
      await updateShippingRule(r.id, { active: !r.active });
      await refresh();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao atualizar.");
    }
  }

  return (
    <PortalLayout active="entregas">
      <header className="portal-page-header">
        <h1 className="portal-page-title">Frete & Entrega</h1>
        <p className="portal-page-subtitle">
          Defina o valor e o prazo do frete por faixa de CEP. O robô consulta
          essas regras automaticamente quando o cliente informa o CEP — antes de
          fechar o pedido.
          <br />
          💡 Para ativar a tool no bot, ligue a capability <strong>"Cálculo de
          Frete por CEP"</strong> em <em>Vendas › Recursos do seu Robô</em>.
        </p>
      </header>

      {!rules ? (
        <div className="portal-loading"><Spinner size={28} /></div>
      ) : (
        <>
          {/* Formulário */}
          <section className="cliente-card" style={{ marginBottom: 24 }}>
            <h3 style={{ marginTop: 0 }}>
              {editing ? "Editar regra" : "Nova regra de frete"}
            </h3>

            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 12 }}>
              <div className="form-group">
                <label className="form-label">Descrição</label>
                <input
                  className="form-input"
                  placeholder="ex.: Capital — Zona Sul"
                  value={draft.label}
                  onChange={(e) => setDraft({ ...draft, label: e.target.value })}
                />
              </div>
              <div className="form-group">
                <label className="form-label">CEP inicial</label>
                <input
                  className="form-input"
                  placeholder="01000-000"
                  value={draft.cep_start}
                  onChange={(e) => setDraft({ ...draft, cep_start: maskCep(e.target.value) })}
                />
              </div>
              <div className="form-group">
                <label className="form-label">CEP final</label>
                <input
                  className="form-input"
                  placeholder="04999-999"
                  value={draft.cep_end}
                  onChange={(e) => setDraft({ ...draft, cep_end: maskCep(e.target.value) })}
                />
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 12 }}>
              <div className="form-group">
                <label className="form-label">Valor (R$)</label>
                <input
                  className="form-input"
                  type="number"
                  step="0.01"
                  min="0"
                  value={draft.valor}
                  onChange={(e) => setDraft({ ...draft, valor: parseFloat(e.target.value) || 0 })}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Prazo (dias úteis)</label>
                <input
                  className="form-input"
                  type="number"
                  min="0"
                  max="60"
                  value={draft.prazo_dias}
                  onChange={(e) => setDraft({ ...draft, prazo_dias: parseInt(e.target.value, 10) || 0 })}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Frete grátis acima de (opcional)</label>
                <input
                  className="form-input"
                  type="number"
                  step="0.01"
                  min="0"
                  placeholder="ex.: 150"
                  value={draft.gratis_acima ?? ""}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      gratis_acima: e.target.value === "" ? null : parseFloat(e.target.value),
                    })
                  }
                />
              </div>
              <div className="form-group">
                <label className="form-label">Ordem (menor = topo)</label>
                <input
                  className="form-input"
                  type="number"
                  value={draft.sort_order ?? 100}
                  onChange={(e) => setDraft({ ...draft, sort_order: parseInt(e.target.value, 10) || 100 })}
                />
              </div>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
              <Toggle
                checked={!!draft.active}
                onChange={(v) => setDraft({ ...draft, active: v })}
              />
              <span style={{ fontSize: 13 }}>Regra ativa</span>
            </div>

            {error && <div className="form-error" style={{ marginTop: 12 }}>{error}</div>}

            <div className="cliente-form-actions" style={{ marginTop: 16 }}>
              {editing && (
                <button type="button" className="btn btn-secondary" onClick={cancelEdit}>
                  Cancelar
                </button>
              )}
              <button type="button" className="btn btn-primary" disabled={busy} onClick={save}>
                {busy ? <Spinner size={14} /> : editing ? "Salvar alterações" : "Adicionar regra"}
              </button>
            </div>
          </section>

          {/* Tabela */}
          <section className="cliente-card">
            <h3 style={{ marginTop: 0 }}>Regras cadastradas ({rules.length})</h3>
            {rules.length === 0 ? (
              <div className="cliente-empty">
                Nenhuma regra de frete cadastrada ainda. Adicione a primeira acima.
              </div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "1px solid #e5e7eb" }}>
                    <th style={{ padding: "8px 6px" }}>Descrição</th>
                    <th style={{ padding: "8px 6px" }}>Faixa CEP</th>
                    <th style={{ padding: "8px 6px" }}>Valor</th>
                    <th style={{ padding: "8px 6px" }}>Prazo</th>
                    <th style={{ padding: "8px 6px" }}>Grátis a partir de</th>
                    <th style={{ padding: "8px 6px" }}>Status</th>
                    <th style={{ padding: "8px 6px" }}></th>
                  </tr>
                </thead>
                <tbody>
                  {rules.map((r) => (
                    <tr key={r.id} style={{ borderBottom: "1px solid #f3f4f6" }}>
                      <td style={{ padding: "10px 6px" }}>{r.label}</td>
                      <td style={{ padding: "10px 6px" }}>{r.cep_start} → {r.cep_end}</td>
                      <td style={{ padding: "10px 6px" }}>{fmtMoney(r.valor)}</td>
                      <td style={{ padding: "10px 6px" }}>{r.prazo_dias} dias</td>
                      <td style={{ padding: "10px 6px" }}>
                        {r.gratis_acima != null ? fmtMoney(r.gratis_acima) : "—"}
                      </td>
                      <td style={{ padding: "10px 6px" }}>
                        <Toggle checked={r.active} onChange={() => toggleActive(r)} />
                      </td>
                      <td style={{ padding: "10px 6px", textAlign: "right" }}>
                        <button className="btn btn-secondary btn-sm" onClick={() => startEdit(r)}>
                          Editar
                        </button>
                        <button
                          className="btn btn-secondary btn-sm"
                          style={{ marginLeft: 6, color: "#dc2626" }}
                          onClick={() => remove(r.id)}
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
