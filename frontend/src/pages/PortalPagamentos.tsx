/**
 * PortalPagamentos — conecta a conta Asaas (PIX) e mostra cobranças recentes.
 * Funciona em conjunto com a capability `payments.pix_asaas`.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import {
  getPaymentsStatus,
  setAsaasKey,
  deleteAsaasKey,
  type PaymentsStatus,
} from "../api/payments";

const fmtMoney = (n: number) =>
  n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const fmtDateTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";

const STATUS_LABEL: Record<string, { label: string; color: string }> = {
  pending:   { label: "Aguardando", color: "#92400e" },
  paid:      { label: "Pago",       color: "#047857" },
  expired:   { label: "Expirado",   color: "#6b7280" },
  cancelled: { label: "Cancelado",  color: "#6b7280" },
  refunded:  { label: "Reembolsado",color: "#7c3aed" },
};

export function PortalPagamentos() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<PaymentsStatus | null>(null);
  const [draftKey, setDraftKey] = useState("");
  const [showKeyField, setShowKeyField] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo]   = useState("");

  async function refresh() {
    try { setStatus(await getPaymentsStatus()); }
    catch (e: any) { setError(e?.response?.data?.detail || "Não foi possível carregar."); }
  }
  useEffect(() => { void refresh(); }, []);

  async function saveKey() {
    setBusy(true); setError(""); setInfo("");
    try {
      await setAsaasKey(draftKey.trim());
      setDraftKey(""); setShowKeyField(false);
      setInfo("Chave Asaas salva. PIX no chat já pode ser ativado em Recursos.");
      await refresh();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao salvar.");
    } finally { setBusy(false); }
  }

  async function removeKey() {
    if (!confirm("Remover a chave Asaas? Isso desativa o PIX no chat.")) return;
    setBusy(true); setError("");
    try { await deleteAsaasKey(); await refresh(); }
    catch (e: any) { setError(e?.response?.data?.detail || "Erro ao remover."); }
    finally { setBusy(false); }
  }

  return (
    <PortalLayout active="pagamentos">
      <header className="portal-page-header">
        <h1 className="portal-page-title">Pagamentos (PIX)</h1>
        <p className="portal-page-subtitle">
          Conecte sua conta Asaas para o robô gerar links PIX direto no
          WhatsApp e confirmar pagamentos automaticamente.
          <br />
          💡 Depois de conectar, ative o recurso <strong>"PIX no Chat"</strong>
          {" "}em <em>Vendas › Recursos do seu Robô</em>.
        </p>
      </header>

      {!status ? (
        <div className="portal-loading"><Spinner size={28} /></div>
      ) : (
        <>
          {/* Status da conexão */}
          <section className="cliente-card" style={{ marginBottom: 24 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <h3 style={{ margin: 0 }}>Conexão Asaas</h3>
                <p style={{ margin: "4px 0 0 0", fontSize: 13, color: "#6b7280" }}>
                  {status.asaas_connected
                    ? "✅ Conectado. As cobranças PIX serão geradas com sua conta."
                    : "🔌 Desconectado. Cadastre sua API key do Asaas para começar."}
                </p>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                {status.asaas_connected ? (
                  <>
                    <button className="btn btn-secondary" onClick={() => setShowKeyField(true)}>
                      Substituir chave
                    </button>
                    <button className="btn btn-secondary" style={{ color: "#dc2626" }} onClick={removeKey} disabled={busy}>
                      Desconectar
                    </button>
                  </>
                ) : (
                  <button className="btn btn-primary" onClick={() => setShowKeyField(true)}>
                    Conectar Asaas
                  </button>
                )}
              </div>
            </div>

            {showKeyField && (
              <div style={{ marginTop: 16 }}>
                <label className="form-label">API key do Asaas</label>
                <div style={{ display: "flex", gap: 8 }}>
                  <input
                    className="form-input"
                    type="password"
                    placeholder="$aact_..."
                    value={draftKey}
                    onChange={(e) => setDraftKey(e.target.value)}
                    style={{ flex: 1 }}
                  />
                  <button className="btn btn-primary" disabled={busy || !draftKey} onClick={saveKey}>
                    {busy ? <Spinner size={14} /> : "Salvar"}
                  </button>
                  <button className="btn btn-secondary" onClick={() => { setShowKeyField(false); setDraftKey(""); }}>
                    Cancelar
                  </button>
                </div>
                <small style={{ color: "#6b7280", display: "block", marginTop: 6 }}>
                  Sua chave é criptografada no banco com Fernet. Você pode pegar
                  a chave em <a href="https://www.asaas.com" target="_blank" rel="noreferrer">Asaas → Configurações → Integrações</a>.
                </small>
              </div>
            )}

            {error && <div className="form-error" style={{ marginTop: 12 }}>{error}</div>}
            {info  && <div className="cliente-help" style={{ marginTop: 12 }}>{info}</div>}
          </section>

          {/* Stats */}
          <div className="cliente-stats" style={{ marginBottom: 24 }}>
            <Agg label="Aguardando pagamento" value={String(status.pending_count)} />
            <Agg label="Pagos (30d)"          value={String(status.paid_last_30d)} />
            <Agg label="Receita PIX (30d)"    value={fmtMoney(status.revenue_last_30d)} />
            <Agg label="Capability"           value={status.asaas_connected ? "Pronto p/ ativar" : "Conecte primeiro"} />
          </div>

          {/* Cobranças recentes */}
          <section className="cliente-card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <h3 style={{ margin: 0 }}>Cobranças recentes</h3>
              <button className="btn btn-secondary btn-sm" onClick={() => navigate("/portal/recursos")}>
                Ir para Recursos →
              </button>
            </div>
            {status.recent_charges.length === 0 ? (
              <div className="cliente-empty">Nenhuma cobrança PIX gerada ainda.</div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "1px solid #e5e7eb" }}>
                    <th style={{ padding: "8px 6px" }}>Criada</th>
                    <th style={{ padding: "8px 6px" }}>Cliente</th>
                    <th style={{ padding: "8px 6px" }}>Pedido</th>
                    <th style={{ padding: "8px 6px" }}>Valor</th>
                    <th style={{ padding: "8px 6px" }}>Status</th>
                    <th style={{ padding: "8px 6px" }}>Pago em</th>
                  </tr>
                </thead>
                <tbody>
                  {status.recent_charges.map((c) => (
                    <tr key={c.id} style={{ borderBottom: "1px solid #f3f4f6" }}>
                      <td style={{ padding: "10px 6px" }}>{fmtDateTime(c.created_at)}</td>
                      <td style={{ padding: "10px 6px" }}>{c.phone || "—"}</td>
                      <td style={{ padding: "10px 6px" }}>{c.order_id ? `#${c.order_id.slice(0, 8)}` : "—"}</td>
                      <td style={{ padding: "10px 6px" }}>{fmtMoney(c.amount)}</td>
                      <td style={{ padding: "10px 6px" }}>
                        <span style={{
                          color: STATUS_LABEL[c.status]?.color || "#6b7280",
                          fontWeight: 600, fontSize: 12,
                        }}>
                          {STATUS_LABEL[c.status]?.label || c.status}
                        </span>
                      </td>
                      <td style={{ padding: "10px 6px" }}>{fmtDateTime(c.paid_at)}</td>
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

function Agg({ label, value }: { label: string; value: string }) {
  return (
    <div className="cliente-agg">
      <span className="cliente-agg__label">{label}</span>
      <span className="cliente-agg__value">{value}</span>
    </div>
  );
}
