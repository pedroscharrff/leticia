import { useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import {
  listOrderStatusMessages,
  updateOrderStatusMessage,
  previewOrderStatusMessage,
  type OrderStatus,
  type OrderStatusMessage,
} from "../api/portal";
import "./PortalMensagensPedido.css";

const STATUS_LABELS: Record<OrderStatus, string> = {
  pending:    "Pendente",
  confirmed:  "Confirmado",
  processing: "Em preparo",
  shipped:    "Enviado",
  delivered:  "Entregue",
  cancelled:  "Cancelado",
};

const STATUS_HINTS: Record<OrderStatus, string> = {
  pending:    "Enviada quando o agente cria o pedido (geralmente desativada — o agente já avisa).",
  confirmed:  "Quando alguém da farmácia confirma o pedido manualmente no painel.",
  processing: "Quando o pedido entra em separação/preparação.",
  shipped:    "Quando o pedido sai para entrega.",
  delivered:  "Quando o pedido foi entregue ao cliente.",
  cancelled:  "Quando o pedido é cancelado.",
};

const PLACEHOLDERS = [
  { key: "{nome}", desc: "Nome do cliente" },
  { key: "{numero_pedido}", desc: "Número curto do pedido" },
  { key: "{total}", desc: 'Total formatado ("R$ 47,50")' },
  { key: "{itens}", desc: "Lista de itens em bullets" },
  { key: "{farmacia}", desc: "Nome da farmácia (da Persona)" },
];

export function PortalMensagensPedido() {
  const [messages, setMessages] = useState<OrderStatusMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [savingStatus, setSavingStatus] = useState<OrderStatus | null>(null);
  const [previewByStatus, setPreviewByStatus] = useState<Partial<Record<OrderStatus, string>>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    listOrderStatusMessages()
      .then(setMessages)
      .catch(() => setError("Erro ao carregar mensagens."))
      .finally(() => setLoading(false));
  }, []);

  function setField(status: OrderStatus, patch: Partial<OrderStatusMessage>) {
    setMessages((prev) =>
      prev.map((m) => (m.status === status ? { ...m, ...patch } : m)),
    );
  }

  async function save(status: OrderStatus) {
    const m = messages.find((x) => x.status === status);
    if (!m) return;
    setSavingStatus(status);
    try {
      const updated = await updateOrderStatusMessage(status, {
        enabled: m.enabled, template: m.template,
      });
      setField(status, updated);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao salvar.");
    } finally {
      setSavingStatus(null);
    }
  }

  async function preview(status: OrderStatus) {
    const m = messages.find((x) => x.status === status);
    if (!m) return;
    const p = await previewOrderStatusMessage(status, { template: m.template });
    setPreviewByStatus((prev) => ({ ...prev, [status]: p.rendered }));
  }

  if (loading) {
    return (
      <PortalLayout active="mensagens-pedido">
        <div className="portal-loading"><Spinner size={32} /></div>
      </PortalLayout>
    );
  }

  return (
    <PortalLayout active="mensagens-pedido">
      <div className="portal-page-header">
        <h1 className="portal-page-title">Mensagens automáticas de pedido</h1>
        <p className="portal-page-subtitle">
          Quando o status de um pedido mudar no painel, o cliente recebe uma
          mensagem no WhatsApp com o texto que você definir aqui.
        </p>
      </div>

      <div className="msg-placeholders">
        <strong>Variáveis disponíveis:</strong>
        {PLACEHOLDERS.map((p) => (
          <span key={p.key} className="msg-placeholder" title={p.desc}>
            <code>{p.key}</code> <small>{p.desc}</small>
          </span>
        ))}
      </div>

      {error && <div className="form-error">{error}</div>}

      <div className="msg-list">
        {messages.map((m) => (
          <article key={m.status} className="msg-card">
            <header className="msg-card__head">
              <div>
                <span className={`pedidos-status pedidos-status--${m.status}`}>
                  {STATUS_LABELS[m.status]}
                </span>
                <p className="msg-hint">{STATUS_HINTS[m.status]}</p>
              </div>
              <label className="msg-toggle">
                <input
                  type="checkbox"
                  checked={m.enabled}
                  onChange={(e) => setField(m.status, { enabled: e.target.checked })}
                />
                <span>Notificar</span>
              </label>
            </header>

            <textarea
              className="form-textarea"
              rows={3}
              value={m.template}
              onChange={(e) => setField(m.status, { template: e.target.value })}
              disabled={!m.enabled}
            />

            <div className="msg-card__actions">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => preview(m.status)}
              >
                Pré-visualizar
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => save(m.status)}
                disabled={savingStatus === m.status}
              >
                {savingStatus === m.status ? <Spinner size={14} /> : "Salvar"}
              </button>
            </div>

            {previewByStatus[m.status] && (
              <div className="msg-preview">
                <strong>Pré-visualização:</strong>
                <pre>{previewByStatus[m.status]}</pre>
              </div>
            )}
          </article>
        ))}
      </div>
    </PortalLayout>
  );
}
