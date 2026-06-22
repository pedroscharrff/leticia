/**
 * PortalResumoPedido — edita o template do resumo do pedido enviado ao
 * cliente logo após a transferência para o atendente humano.
 *
 * Mora na capability `sales.order_summary_after_handoff` (mig 044), cujo
 * envio é orquestrado pelo worker em send_order_summary (celery_app.py).
 *
 * 5 campos editáveis (header / item / show_total / total_label / footer)
 * + preview ao vivo com 2 modos: com preço (modo ERP) e sem preço
 * (pré-atendimento). Não toca no toggle da capability — só no texto.
 * Para ativar/desativar, o operador vai em "Recursos do seu Robô".
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import {
  getOrderSummaryConfig, updateOrderSummaryConfig, previewOrderSummary,
  type OrderSummaryConfig, type OrderSummaryConfigPatch,
} from "../api/payments";

type Draft = {
  header_text:   string;
  item_template: string;
  show_total:    boolean;
  total_label:   string;
  show_payment:  boolean;
  payment_label: string;
  show_address:  boolean;
  address_label: string;
  footer_text:   string;
};

export function PortalResumoPedido() {
  const navigate = useNavigate();
  const [cfg, setCfg]           = useState<OrderSummaryConfig | null>(null);
  const [draft, setDraft]       = useState<Draft | null>(null);
  const [preview, setPreview]   = useState("");
  const [noPrices, setNoPrices] = useState(true); // foca no caso pré-atendimento
  const [loading, setLoading]   = useState(false);
  const [saving, setSaving]     = useState(false);
  const [error, setError]       = useState("");
  const [savedOk, setSavedOk]   = useState(false);

  async function load() {
    try {
      const c = await getOrderSummaryConfig();
      setCfg(c);
      setDraft(toDraft(c));
      await refreshPreview(toDraft(c), noPrices);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Falha ao carregar template.");
    }
  }

  function toDraft(c: OrderSummaryConfig): Draft {
    return {
      header_text:   c.header_text,
      item_template: c.item_template,
      show_total:    c.show_total,
      total_label:   c.total_label,
      show_payment:  c.show_payment,
      payment_label: c.payment_label,
      show_address:  c.show_address,
      address_label: c.address_label,
      footer_text:   c.footer_text,
    };
  }

  async function refreshPreview(d: Draft, np: boolean) {
    setLoading(true); setError("");
    try {
      const p = await previewOrderSummary({ ...d, no_prices: np });
      setPreview(p.rendered);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Falha no preview.");
    } finally { setLoading(false); }
  }

  useEffect(() => { load(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, []);

  // Re-renderiza preview quando alterna modo (com/sem preço) e existe draft
  useEffect(() => {
    if (draft) refreshPreview(draft, noPrices);
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [noPrices]);

  function patch<K extends keyof Draft>(key: K, value: Draft[K]) {
    if (!draft) return;
    setDraft({ ...draft, [key]: value });
  }

  async function save() {
    if (!draft || !cfg) return;
    setSaving(true); setError(""); setSavedOk(false);
    try {
      const payload: OrderSummaryConfigPatch = { ...draft };
      const updated = await updateOrderSummaryConfig(payload);
      setCfg(updated);
      setDraft(toDraft(updated));
      setSavedOk(true);
      setTimeout(() => setSavedOk(false), 2500);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Falha ao salvar.");
    } finally { setSaving(false); }
  }

  async function restoreDefault() {
    if (!cfg) return;
    if (!window.confirm("Restaurar o texto padrão? Sua personalização será perdida.")) return;
    setSaving(true); setError("");
    try {
      // null em todos os campos → backend remove os overrides e herda do catálogo
      const updated = await updateOrderSummaryConfig({
        header_text: null, item_template: null, show_total: null,
        total_label: null, show_payment: null, payment_label: null,
        show_address: null, address_label: null, footer_text: null,
      });
      setCfg(updated);
      setDraft(toDraft(updated));
      await refreshPreview(toDraft(updated), noPrices);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Falha ao restaurar.");
    } finally { setSaving(false); }
  }

  const dirty = !!(cfg && draft) && (
    draft.header_text   !== cfg.header_text   ||
    draft.item_template !== cfg.item_template ||
    draft.show_total    !== cfg.show_total    ||
    draft.total_label   !== cfg.total_label   ||
    draft.show_payment  !== cfg.show_payment  ||
    draft.payment_label !== cfg.payment_label ||
    draft.show_address  !== cfg.show_address  ||
    draft.address_label !== cfg.address_label ||
    draft.footer_text   !== cfg.footer_text
  );

  return (
    <PortalLayout active="resumo-pedido">
      <header className="portal-page-header">
        <h1 className="portal-page-title">Resumo do pedido na transferência</h1>
        <p className="portal-page-subtitle">
          Quando o robô transfere para o atendente humano, ele envia uma
          mensagem com o resumo do que o cliente pediu. Esse é o template
          dessa mensagem — você pode mexer no cabeçalho, no formato de cada
          item, no rodapé e se mostra o total ou não.
          <br />
          💡 Para ligar ou desligar essa função, vá em <em>Vendas › Recursos
          do seu Robô</em>.
        </p>
      </header>

      {error && (
        <div className="form-error" style={{ marginBottom: 16 }}>
          {error}
          <button onClick={() => setError("")} style={{ float: "right",
            background: "none", border: "none", color: "inherit", cursor: "pointer" }}>×</button>
        </div>
      )}

      {!cfg || !draft ? (
        <div className="portal-loading"><Spinner size={28} /></div>
      ) : (
        <>
          {!cfg.enabled && (
            <section className="cliente-card" style={{
                marginBottom: 16,
                border: "1px solid rgba(245,158,11,0.4)",
                background: "rgba(245,158,11,0.06)" }}>
              <div style={{ display: "flex", justifyContent: "space-between",
                            alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                <div style={{ fontSize: 13 }}>
                  ⚠️ A função está <strong>desativada</strong>. O template é salvo
                  normalmente, mas nenhuma mensagem de resumo é enviada enquanto
                  estiver assim.
                </div>
                <button className="btn btn-sm"
                        onClick={() => navigate("/portal/recursos")}>
                  Ativar em Recursos →
                </button>
              </div>
            </section>
          )}

          <section className="cliente-card" style={{ marginBottom: 24 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr",
                          gap: 16, alignItems: "start" }}>
              {/* Coluna 1 — editor */}
              <div>
                <Field label="Cabeçalho da mensagem"
                       hint="Primeira linha da mensagem. Pode ficar vazio.">
                  <input type="text" value={draft.header_text}
                         onChange={(e) => patch("header_text", e.target.value)}
                         style={inputStyle} />
                </Field>

                <Field label="Modelo de cada item"
                       hint="Como cada item do pedido vai aparecer. Use placeholders {nome}, {quantidade}, {preco_total}, {preco_unit}.">
                  <input type="text" value={draft.item_template}
                         onChange={(e) => patch("item_template", e.target.value)}
                         style={inputStyle} />
                  <PlaceholderRow
                    placeholders={cfg.placeholders}
                    onPick={(k) => patch("item_template",
                      draft.item_template + "{" + k + "}")} />
                </Field>

                <div style={{ display: "grid",
                              gridTemplateColumns: "auto 1fr", gap: 12,
                              alignItems: "center", marginTop: 12 }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 8,
                                  fontSize: 13, color: "#e5e5e5" }}>
                    <input type="checkbox" checked={draft.show_total}
                           onChange={(e) => patch("show_total", e.target.checked)} />
                    Mostrar total
                  </label>
                  <input type="text" value={draft.total_label}
                         onChange={(e) => patch("total_label", e.target.value)}
                         disabled={!draft.show_total}
                         placeholder="Rótulo do total (ex: *Total*)"
                         style={{ ...inputStyle,
                                  opacity: draft.show_total ? 1 : 0.5 }} />
                </div>
                <div style={{ fontSize: 11, color: "#6b7280", marginTop: 4 }}>
                  Em modo pré-atendimento (sem catálogo) o total é
                  automaticamente omitido — mesmo com esta opção ligada — porque
                  ainda não temos preço.
                </div>

                <div style={{ display: "grid",
                              gridTemplateColumns: "auto 1fr", gap: 12,
                              alignItems: "center", marginTop: 12 }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 8,
                                  fontSize: 13, color: "#e5e5e5" }}>
                    <input type="checkbox" checked={draft.show_payment}
                           onChange={(e) => patch("show_payment", e.target.checked)} />
                    Mostrar pagamento
                  </label>
                  <input type="text" value={draft.payment_label}
                         onChange={(e) => patch("payment_label", e.target.value)}
                         disabled={!draft.show_payment}
                         placeholder="Rótulo do pagamento (ex: *Pagamento*)"
                         style={{ ...inputStyle,
                                  opacity: draft.show_payment ? 1 : 0.5 }} />
                </div>

                <div style={{ display: "grid",
                              gridTemplateColumns: "auto 1fr", gap: 12,
                              alignItems: "center", marginTop: 12 }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 8,
                                  fontSize: 13, color: "#e5e5e5" }}>
                    <input type="checkbox" checked={draft.show_address}
                           onChange={(e) => patch("show_address", e.target.checked)} />
                    Mostrar endereço
                  </label>
                  <input type="text" value={draft.address_label}
                         onChange={(e) => patch("address_label", e.target.value)}
                         disabled={!draft.show_address}
                         placeholder="Rótulo do endereço (ex: *Entrega*)"
                         style={{ ...inputStyle,
                                  opacity: draft.show_address ? 1 : 0.5 }} />
                </div>
                <div style={{ fontSize: 11, color: "#6b7280", marginTop: 4 }}>
                  Pagamento e endereço aparecem <strong>só quando houver o dado</strong>:
                  a forma de pagamento sai do fechamento (some no pré-atendimento) e
                  o endereço sai do cadastro do cliente (some em pedidos de retirada).
                  Ambos são preenchidos automaticamente — sem o robô interpretar nada.
                </div>

                <Field label="Rodapé (opcional)"
                       hint="Última linha. Pode ficar vazio.">
                  <textarea value={draft.footer_text}
                            onChange={(e) => patch("footer_text", e.target.value)}
                            rows={2}
                            style={{ ...inputStyle, fontFamily: "inherit",
                                     resize: "vertical" }} />
                </Field>

                <div style={{ display: "flex", gap: 8, marginTop: 16,
                              flexWrap: "wrap" }}>
                  <button className="btn" onClick={() => draft && refreshPreview(draft, noPrices)}
                          disabled={loading}>
                    {loading ? "Renderizando…" : "Atualizar preview"}
                  </button>
                  <button className="btn btn-primary"
                          onClick={save} disabled={saving || !dirty}>
                    {saving ? "Salvando…" : "Salvar"}
                  </button>
                  <button className="btn btn-sm"
                          onClick={restoreDefault}
                          disabled={saving || cfg.is_default}
                          title="Volta ao texto padrão e remove sua personalização.">
                    Restaurar padrão
                  </button>
                  {savedOk && <span style={{ color: "#22c55e", fontSize: 12,
                                              alignSelf: "center" }}>✓ Salvo</span>}
                </div>
              </div>

              {/* Coluna 2 — preview */}
              <div>
                <div style={{ display: "flex", gap: 8, marginBottom: 8,
                              alignItems: "center" }}>
                  <span style={{ fontSize: 12, color: "#9ca3af" }}>Preview:</span>
                  <button
                    className={"btn btn-sm" + (!noPrices ? " btn-primary" : "")}
                    onClick={() => setNoPrices(false)}>
                    Com preço (modo ERP)
                  </button>
                  <button
                    className={"btn btn-sm" + (noPrices ? " btn-primary" : "")}
                    onClick={() => setNoPrices(true)}>
                    Sem preço (pré-atendimento)
                  </button>
                </div>
                <div style={{ padding: 14, background: "#0b1620",
                              border: "1px solid #1e3a5f", borderRadius: 8,
                              whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.5,
                              minHeight: 200, color: "#e5e5e5" }}>
                  {preview || <span style={{ color: "#6b7280" }}>
                    Renderizando…
                  </span>}
                </div>
                <p style={{ fontSize: 11, color: "#6b7280", marginTop: 8,
                            lineHeight: 1.5 }}>
                  Dados de exemplo: 2x Dipirona 500mg + 1x{" "}
                  {noPrices ? "Soro fisiológico" : "Tylenol"}.
                  <br />
                  Formatação WhatsApp: <code>*texto*</code> = negrito,{" "}
                  <code>_texto_</code> = itálico,{" "}
                  <code>~texto~</code> = riscado.
                </p>
              </div>
            </div>
          </section>

          <section className="cliente-card">
            <h3 style={{ marginTop: 0 }}>Quando essa mensagem é enviada</h3>
            <ul style={{ margin: 0, paddingLeft: 20, lineHeight: 1.7,
                         fontSize: 13 }}>
              <li>Logo após a transferência para o atendente humano, em qualquer modo (ERP ou pré-atendimento).</li>
              <li>Em pré-atendimento (sem catálogo), o resumo só lista nome + quantidade — os preços e o total ficam para o atendente confirmar no balcão.</li>
              <li>A <strong>forma de pagamento</strong> aparece quando o pedido foi fechado com pagamento definido (some no pré-atendimento). O <strong>endereço de entrega</strong> aparece quando o cliente tem endereço no cadastro (some em pedidos de retirada). Os dois são preenchidos automaticamente, sem depender da interpretação do robô.</li>
              <li>Carrinho vazio → nenhuma mensagem é enviada (sem ruído).</li>
              <li>Se você desligar a função em "Recursos do seu Robô", nenhuma mensagem é enviada — independente do que está salvo aqui.</li>
            </ul>
          </section>
        </>
      )}
    </PortalLayout>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%", padding: "8px 10px", fontSize: 13,
  background: "#0f0f0f", border: "1px solid #2a2a2a",
  color: "#e5e5e5", borderRadius: 6,
};

function Field({ label, hint, children }:
    { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4,
                  marginTop: 12 }}>
      <label style={{ fontSize: 12, color: "#9ca3af" }}>{label}</label>
      {children}
      {hint && <span style={{ fontSize: 11, color: "#6b7280" }}>{hint}</span>}
    </div>
  );
}

function PlaceholderRow({ placeholders, onPick }:
    { placeholders: { key: string; desc: string }[];
      onPick: (k: string) => void }) {
  return (
    <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
      {placeholders.map(p => (
        <button key={p.key} onClick={() => onPick(p.key)} title={p.desc}
                style={{ padding: "2px 8px", fontSize: 11,
                         background: "#1f2937", border: "1px solid #374151",
                         borderRadius: 10, color: "#9ca3af",
                         cursor: "pointer", fontFamily: "monospace" }}>
          {"{" + p.key + "}"}
        </button>
      ))}
    </div>
  );
}
