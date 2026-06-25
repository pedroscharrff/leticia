/**
 * PortalEntregas — Frete & Entrega.
 * Funciona com a capability `delivery.shipping_by_cep`.
 *
 * Dois modos (seletor no topo):
 *   • Por faixa de CEP  → tabela de faixas de CEP → valor + prazo.
 *   • Por distância     → CEP da farmácia (origem) + faixas de raio (km).
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
  getShippingOrigin,
  putShippingOrigin,
  listShippingTiers,
  createShippingTier,
  updateShippingTier,
  deleteShippingTier,
  type ShippingRule,
  type ShippingRuleIn,
  type ShippingMode,
  type DistanceSource,
  type ShippingOrigin,
  type ShippingTier,
  type ShippingTierIn,
} from "../api/shipping_rules";

const fmtMoney = (n: number) =>
  n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

function maskCep(raw: string): string {
  const digits = raw.replace(/\D/g, "").slice(0, 8);
  if (digits.length <= 5) return digits;
  return `${digits.slice(0, 5)}-${digits.slice(5)}`;
}

const EMPTY_RULE: ShippingRuleIn = {
  label: "", cep_start: "", cep_end: "", valor: 0, prazo_dias: 2,
  gratis_acima: null, active: true, sort_order: 100,
};

const EMPTY_TIER: ShippingTierIn = {
  label: "", max_distance_km: 5, valor: 0, prazo_dias: 2,
  gratis_acima: null, active: true, sort_order: 100,
};

export function PortalEntregas() {
  const [origin, setOrigin] = useState<ShippingOrigin | null>(null);
  const [rules, setRules]   = useState<ShippingRule[] | null>(null);
  const [tiers, setTiers]   = useState<ShippingTier[] | null>(null);
  const [error, setError]   = useState("");
  const [busy, setBusy]     = useState(false);

  async function refresh() {
    try {
      const [o, r, t] = await Promise.all([
        getShippingOrigin(), listShippingRules(), listShippingTiers(),
      ]);
      setOrigin(o); setRules(r); setTiers(t);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Não foi possível carregar.");
    }
  }
  useEffect(() => { void refresh(); }, []);

  const mode: ShippingMode = origin?.mode ?? "cep_table";

  async function saveOrigin(next: {
    mode?: ShippingMode; distance_source?: DistanceSource; cep?: string | null;
  }) {
    setBusy(true); setError("");
    try {
      const o = await putShippingOrigin({
        mode: next.mode ?? mode,
        distance_source: next.distance_source ?? origin?.distance_source ?? "haversine",
        cep: next.cep !== undefined ? next.cep : origin?.cep ?? null,
      });
      setOrigin(o);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao salvar origem.");
    } finally {
      setBusy(false);
    }
  }

  if (!origin || !rules || !tiers) {
    return (
      <PortalLayout active="entregas">
        <div className="portal-loading"><Spinner size={28} /></div>
      </PortalLayout>
    );
  }

  return (
    <PortalLayout active="entregas">
      <header className="portal-page-header">
        <h1 className="portal-page-title">Frete & Entrega</h1>
        <p className="portal-page-subtitle">
          Defina como o robô calcula o frete quando o cliente informa o CEP —
          antes de fechar o pedido.
          <br />
          💡 Para ativar no bot, ligue a capability <strong>"Cálculo de Frete por
          CEP"</strong> em <em>Vendas › Recursos do seu Robô</em>.
        </p>
      </header>

      {error && <div className="form-error" style={{ marginBottom: 16 }}>{error}</div>}

      {/* Seletor de modo */}
      <section className="cliente-card" style={{ marginBottom: 24 }}>
        <h3 style={{ marginTop: 0 }}>Como você quer cobrar o frete?</h3>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <ModeCard
            title="Por faixa de CEP"
            desc="Cadastre faixas de CEP → valor + prazo."
            selected={mode === "cep_table"}
            disabled={busy}
            onClick={() => saveOrigin({ mode: "cep_table" })}
          />
          <ModeCard
            title="Por distância (raio)"
            desc="Cadastre o CEP da farmácia e faixas de km → valor + prazo."
            selected={mode === "distance"}
            disabled={busy}
            onClick={() => saveOrigin({ mode: "distance" })}
          />
        </div>
      </section>

      {mode === "distance"
        ? <DistanceSection origin={origin} tiers={tiers}
            onSaveOrigin={saveOrigin} onChanged={refresh}
            setError={setError} busy={busy} />
        : <CepSection rules={rules} onChanged={refresh} setError={setError} />}
    </PortalLayout>
  );
}

function ModeCard({ title, desc, selected, disabled, onClick }: {
  title: string; desc: string; selected: boolean; disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        flex: "1 1 240px", textAlign: "left", cursor: "pointer",
        border: selected ? "2px solid #2563eb" : "1px solid #e5e7eb",
        background: selected ? "#eff6ff" : "#fff",
        borderRadius: 10, padding: "14px 16px",
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 4 }}>
        {selected ? "✓ " : ""}{title}
      </div>
      <div style={{ fontSize: 13, color: "#6b7280" }}>{desc}</div>
    </button>
  );
}

// ── Modo distância ───────────────────────────────────────────────────────────

function DistanceSection({ origin, tiers, onSaveOrigin, onChanged, setError, busy: pageBusy }: {
  origin: ShippingOrigin;
  tiers: ShippingTier[];
  onSaveOrigin: (n: { distance_source?: DistanceSource; cep?: string | null }) => Promise<void>;
  onChanged: () => Promise<void>;
  setError: (s: string) => void;
  busy: boolean;
}) {
  const [cep, setCep] = useState(origin.cep ?? "");
  const [savingCep, setSavingCep] = useState(false);
  const [draft, setDraft] = useState<ShippingTierIn>(EMPTY_TIER);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function saveCep() {
    setSavingCep(true);
    try { await onSaveOrigin({ cep: cep || null }); }
    finally { setSavingCep(false); }
  }

  function startEdit(t: ShippingTier) {
    setEditing(t.id);
    setDraft({
      label: t.label, max_distance_km: t.max_distance_km, valor: t.valor,
      prazo_dias: t.prazo_dias, gratis_acima: t.gratis_acima,
      active: t.active, sort_order: t.sort_order,
    });
  }
  function cancelEdit() { setEditing(null); setDraft(EMPTY_TIER); }

  async function saveTier() {
    setBusy(true); setError("");
    try {
      if (editing) await updateShippingTier(editing, draft);
      else await createShippingTier(draft);
      await onChanged();
      cancelEdit();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao salvar faixa.");
    } finally { setBusy(false); }
  }

  async function removeTier(id: string) {
    if (!confirm("Remover esta faixa de distância?")) return;
    setBusy(true);
    try { await deleteShippingTier(id); await onChanged(); }
    catch (e: any) { setError(e?.response?.data?.detail || "Erro ao remover."); }
    finally { setBusy(false); }
  }

  return (
    <>
      {/* Origem */}
      <section className="cliente-card" style={{ marginBottom: 24 }}>
        <h3 style={{ marginTop: 0 }}>CEP da farmácia (origem)</h3>
        <p className="portal-page-subtitle" style={{ marginTop: 0 }}>
          A distância até o cliente é medida a partir deste endereço.
        </p>
        <div style={{ display: "flex", gap: 12, alignItems: "flex-end" }}>
          <div className="form-group" style={{ maxWidth: 200 }}>
            <label className="form-label">CEP</label>
            <input className="form-input" placeholder="01310-100"
              value={cep} onChange={(e) => setCep(maskCep(e.target.value))} />
          </div>
          <button className="btn btn-primary" disabled={savingCep} onClick={saveCep}>
            {savingCep ? <Spinner size={14} /> : "Salvar e localizar"}
          </button>
        </div>
        <div style={{ marginTop: 10, fontSize: 13 }}>
          {origin.geocoded
            ? <span style={{ color: "#16a34a" }}>
                ✓ Localizado: {origin.resolved_address || "coordenadas obtidas"}
              </span>
            : <span style={{ color: "#dc2626" }}>
                ⚠ Sem coordenadas. Salve um CEP válido para usar o modo por distância.
              </span>}
        </div>

        {/* Fonte da distância */}
        <div style={{ marginTop: 16, borderTop: "1px solid #f3f4f6", paddingTop: 14 }}>
          <label className="form-label">Como medir a distância</label>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 6 }}>
            <ModeCard
              title="Linha reta (grátis)"
              desc="Distância geográfica direta. Sem custo."
              selected={origin.distance_source === "haversine"}
              disabled={pageBusy}
              onClick={() => onSaveOrigin({ distance_source: "haversine" })}
            />
            <ModeCard
              title="Rota real (Google)"
              desc={origin.google_available
                ? "Distância de carro pelas ruas (Google Maps). Mais precisa."
                : "Requer chave do Google Maps na plataforma (indisponível)."}
              selected={origin.distance_source === "google"}
              disabled={pageBusy || !origin.google_available}
              onClick={() => onSaveOrigin({ distance_source: "google" })}
            />
          </div>
          {origin.distance_source === "google" && !origin.google_available && (
            <div className="form-error" style={{ marginTop: 8 }}>
              A chave do Google Maps não está configurada — o robô usa linha reta
              como reserva até a chave ser adicionada.
            </div>
          )}
        </div>
      </section>

      {/* Form de faixa */}
      <section className="cliente-card" style={{ marginBottom: 24 }}>
        <h3 style={{ marginTop: 0 }}>{editing ? "Editar faixa" : "Nova faixa de distância"}</h3>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 12 }}>
          <div className="form-group">
            <label className="form-label">Descrição</label>
            <input className="form-input" placeholder="ex.: Até 5 km"
              value={draft.label}
              onChange={(e) => setDraft({ ...draft, label: e.target.value })} />
          </div>
          <div className="form-group">
            <label className="form-label">Até quantos km</label>
            <input className="form-input" type="number" step="0.5" min="0.5"
              value={draft.max_distance_km}
              onChange={(e) => setDraft({ ...draft, max_distance_km: parseFloat(e.target.value) || 0 })} />
          </div>
          <div className="form-group">
            <label className="form-label">Valor (R$)</label>
            <input className="form-input" type="number" step="0.01" min="0"
              value={draft.valor}
              onChange={(e) => setDraft({ ...draft, valor: parseFloat(e.target.value) || 0 })} />
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
          <div className="form-group">
            <label className="form-label">Prazo (dias úteis)</label>
            <input className="form-input" type="number" min="0" max="60"
              value={draft.prazo_dias}
              onChange={(e) => setDraft({ ...draft, prazo_dias: parseInt(e.target.value, 10) || 0 })} />
          </div>
          <div className="form-group">
            <label className="form-label">Frete grátis acima de (opcional)</label>
            <input className="form-input" type="number" step="0.01" min="0" placeholder="ex.: 150"
              value={draft.gratis_acima ?? ""}
              onChange={(e) => setDraft({
                ...draft,
                gratis_acima: e.target.value === "" ? null : parseFloat(e.target.value),
              })} />
          </div>
          <div className="form-group" style={{ display: "flex", alignItems: "flex-end", gap: 8 }}>
            <Toggle checked={!!draft.active}
              onChange={(v) => setDraft({ ...draft, active: v })} />
            <span style={{ fontSize: 13 }}>Faixa ativa</span>
          </div>
        </div>
        <div className="cliente-form-actions" style={{ marginTop: 12 }}>
          {editing && <button className="btn btn-secondary" onClick={cancelEdit}>Cancelar</button>}
          <button className="btn btn-primary" disabled={busy} onClick={saveTier}>
            {busy ? <Spinner size={14} /> : editing ? "Salvar" : "Adicionar faixa"}
          </button>
        </div>
      </section>

      {/* Tabela de faixas */}
      <section className="cliente-card">
        <h3 style={{ marginTop: 0 }}>Faixas cadastradas ({tiers.length})</h3>
        {tiers.length === 0 ? (
          <div className="cliente-empty">
            Nenhuma faixa cadastrada. Adicione, por exemplo, "Até 3 km", "Até 5 km", "Até 10 km".
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid #e5e7eb" }}>
                <th style={{ padding: "8px 6px" }}>Descrição</th>
                <th style={{ padding: "8px 6px" }}>Até (km)</th>
                <th style={{ padding: "8px 6px" }}>Valor</th>
                <th style={{ padding: "8px 6px" }}>Prazo</th>
                <th style={{ padding: "8px 6px" }}>Grátis a partir de</th>
                <th style={{ padding: "8px 6px" }}>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {tiers.map((t) => (
                <tr key={t.id} style={{ borderBottom: "1px solid #f3f4f6" }}>
                  <td style={{ padding: "10px 6px" }}>{t.label}</td>
                  <td style={{ padding: "10px 6px" }}>{t.max_distance_km} km</td>
                  <td style={{ padding: "10px 6px" }}>{fmtMoney(t.valor)}</td>
                  <td style={{ padding: "10px 6px" }}>{t.prazo_dias} dias</td>
                  <td style={{ padding: "10px 6px" }}>
                    {t.gratis_acima != null ? fmtMoney(t.gratis_acima) : "—"}
                  </td>
                  <td style={{ padding: "10px 6px" }}>
                    <Toggle checked={t.active}
                      onChange={async () => {
                        await updateShippingTier(t.id, { active: !t.active });
                        await onChanged();
                      }} />
                  </td>
                  <td style={{ padding: "10px 6px", textAlign: "right" }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => startEdit(t)}>Editar</button>
                    <button className="btn btn-secondary btn-sm"
                      style={{ marginLeft: 6, color: "#dc2626" }}
                      onClick={() => removeTier(t.id)}>Remover</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="portal-page-subtitle" style={{ marginTop: 12 }}>
          O robô aplica a <strong>menor</strong> faixa que cobre a distância do
          cliente. Acima da maior faixa, ele avisa que o endereço está fora da
          área de entrega (não inventa valor).
        </p>
      </section>
    </>
  );
}

// ── Modo faixa de CEP (legado) ───────────────────────────────────────────────

function CepSection({ rules, onChanged, setError }: {
  rules: ShippingRule[];
  onChanged: () => Promise<void>;
  setError: (s: string) => void;
}) {
  const [draft, setDraft] = useState<ShippingRuleIn>(EMPTY_RULE);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function startEdit(r: ShippingRule) {
    setEditing(r.id);
    setDraft({
      label: r.label, cep_start: r.cep_start, cep_end: r.cep_end,
      valor: r.valor, prazo_dias: r.prazo_dias,
      gratis_acima: r.gratis_acima, active: r.active, sort_order: r.sort_order,
    });
  }
  function cancelEdit() { setEditing(null); setDraft(EMPTY_RULE); }

  async function save() {
    setBusy(true); setError("");
    try {
      if (editing) await updateShippingRule(editing, draft);
      else await createShippingRule(draft);
      await onChanged();
      cancelEdit();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao salvar.");
    } finally { setBusy(false); }
  }

  async function remove(id: string) {
    if (!confirm("Remover esta regra de frete?")) return;
    setBusy(true);
    try { await deleteShippingRule(id); await onChanged(); }
    catch (e: any) { setError(e?.response?.data?.detail || "Erro ao remover."); }
    finally { setBusy(false); }
  }

  return (
    <>
      <section className="cliente-card" style={{ marginBottom: 24 }}>
        <h3 style={{ marginTop: 0 }}>{editing ? "Editar regra" : "Nova regra de frete"}</h3>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 12 }}>
          <div className="form-group">
            <label className="form-label">Descrição</label>
            <input className="form-input" placeholder="ex.: Capital — Zona Sul"
              value={draft.label}
              onChange={(e) => setDraft({ ...draft, label: e.target.value })} />
          </div>
          <div className="form-group">
            <label className="form-label">CEP inicial</label>
            <input className="form-input" placeholder="01000-000"
              value={draft.cep_start}
              onChange={(e) => setDraft({ ...draft, cep_start: maskCep(e.target.value) })} />
          </div>
          <div className="form-group">
            <label className="form-label">CEP final</label>
            <input className="form-input" placeholder="04999-999"
              value={draft.cep_end}
              onChange={(e) => setDraft({ ...draft, cep_end: maskCep(e.target.value) })} />
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 12 }}>
          <div className="form-group">
            <label className="form-label">Valor (R$)</label>
            <input className="form-input" type="number" step="0.01" min="0"
              value={draft.valor}
              onChange={(e) => setDraft({ ...draft, valor: parseFloat(e.target.value) || 0 })} />
          </div>
          <div className="form-group">
            <label className="form-label">Prazo (dias úteis)</label>
            <input className="form-input" type="number" min="0" max="60"
              value={draft.prazo_dias}
              onChange={(e) => setDraft({ ...draft, prazo_dias: parseInt(e.target.value, 10) || 0 })} />
          </div>
          <div className="form-group">
            <label className="form-label">Frete grátis acima de (opcional)</label>
            <input className="form-input" type="number" step="0.01" min="0" placeholder="ex.: 150"
              value={draft.gratis_acima ?? ""}
              onChange={(e) => setDraft({
                ...draft,
                gratis_acima: e.target.value === "" ? null : parseFloat(e.target.value),
              })} />
          </div>
          <div className="form-group">
            <label className="form-label">Ordem (menor = topo)</label>
            <input className="form-input" type="number"
              value={draft.sort_order ?? 100}
              onChange={(e) => setDraft({ ...draft, sort_order: parseInt(e.target.value, 10) || 100 })} />
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
          <Toggle checked={!!draft.active}
            onChange={(v) => setDraft({ ...draft, active: v })} />
          <span style={{ fontSize: 13 }}>Regra ativa</span>
        </div>
        <div className="cliente-form-actions" style={{ marginTop: 16 }}>
          {editing && <button className="btn btn-secondary" onClick={cancelEdit}>Cancelar</button>}
          <button className="btn btn-primary" disabled={busy} onClick={save}>
            {busy ? <Spinner size={14} /> : editing ? "Salvar alterações" : "Adicionar regra"}
          </button>
        </div>
      </section>

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
                <th />
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
                    <Toggle checked={r.active}
                      onChange={async () => {
                        await updateShippingRule(r.id, { active: !r.active });
                        await onChanged();
                      }} />
                  </td>
                  <td style={{ padding: "10px 6px", textAlign: "right" }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => startEdit(r)}>Editar</button>
                    <button className="btn btn-secondary btn-sm"
                      style={{ marginLeft: 6, color: "#dc2626" }}
                      onClick={() => remove(r.id)}>Remover</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
