import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import {
  getCustomer,
  updateCustomer,
  listCustomerOrders,
  listCustomerConversations,
  type CustomerDetail,
  type CustomerOrderRow,
  type CustomerConversation,
  type OrderStatus,
} from "../api/portal";
import "./PortalClienteDetalhe.css";

const STATUS_LABELS: Record<OrderStatus, string> = {
  pending: "Pendente", confirmed: "Confirmado", processing: "Em preparo",
  shipped: "Enviado",  delivered: "Entregue",   cancelled: "Cancelado",
};

const fmtMoney = (n: number) =>
  n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const fmtDateTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";
const fmtDate = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString("pt-BR") : "—";

type Tab = "dados" | "pedidos" | "conversas";

export function PortalClienteDetalhe() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [tab, setTab] = useState<Tab>("dados");
  const [customer, setCustomer] = useState<CustomerDetail | null>(null);
  const [orders, setOrders] = useState<CustomerOrderRow[] | null>(null);
  const [conversations, setConversations] = useState<CustomerConversation[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // dados form state
  const [form, setForm] = useState<Partial<CustomerDetail> & { _addr?: any }>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    getCustomer(id)
      .then((c) => { setCustomer(c); setForm(c); })
      .catch(() => setError("Cliente não encontrado."))
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    if (tab !== "pedidos" || !id || orders) return;
    listCustomerOrders(id).then(setOrders).catch(() => setOrders([]));
  }, [tab, id, orders]);

  useEffect(() => {
    if (tab !== "conversas" || !id || conversations) return;
    listCustomerConversations(id).then(setConversations).catch(() => setConversations([]));
  }, [tab, id, conversations]);

  const stats = useMemo(() => {
    if (!orders) return null;
    const open = orders.filter((o) => !["delivered", "cancelled"].includes(o.status)).length;
    const cancelled = orders.filter((o) => o.status === "cancelled").length;
    const totalSpent = orders.filter((o) => o.status !== "cancelled")
      .reduce((acc, o) => acc + o.total, 0);
    const lastDate = orders[0]?.created_at;
    const avgTicket = orders.length ? totalSpent / Math.max(1, orders.length - cancelled) : 0;
    return { open, cancelled, totalSpent, lastDate, avgTicket, count: orders.length };
  }, [orders]);

  async function saveDados(e: React.FormEvent) {
    e.preventDefault();
    if (!id) return;
    setSaving(true);
    try {
      const payload: any = {
        name: form.name, email: form.email, doc: form.doc, notes: form.notes,
        cep: form.address?.cep, street: form.address?.street,
        street_number: form.address?.street_number, complement: form.address?.complement,
        neighborhood: form.address?.neighborhood,
        city: form.address?.city, state: form.address?.state,
      };
      const updated = await updateCustomer(id, payload);
      setCustomer(updated);
      setForm(updated);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao salvar.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <PortalLayout active="clientes"><div className="portal-loading"><Spinner size={32} /></div></PortalLayout>;
  }
  if (error || !customer) {
    return (
      <PortalLayout active="clientes">
        <div className="portal-page-header">
          <button className="btn btn-secondary" onClick={() => navigate("/portal/clientes")}>← Voltar</button>
        </div>
        <div className="form-error">{error || "Cliente não encontrado."}</div>
      </PortalLayout>
    );
  }

  return (
    <PortalLayout active="clientes">
      <div className="portal-page-header cliente-header">
        <div>
          <button className="cliente-back" onClick={() => navigate("/portal/clientes")}>← Clientes</button>
          <h1 className="portal-page-title">{customer.name || "Cliente sem nome"}</h1>
          <p className="portal-page-subtitle">{customer.phone}</p>
        </div>
        <div className="cliente-aggregates">
          <Aggregate label="Pedidos" value={String(customer.total_orders)} />
          <Aggregate label="Gasto total" value={fmtMoney(customer.total_spent)} />
          <Aggregate label="Último contato" value={fmtDate(customer.last_contact_at)} />
          <Aggregate label="Cliente desde" value={fmtDate(customer.created_at)} />
        </div>
      </div>

      <nav className="cliente-tabs">
        <TabBtn active={tab === "dados"}    onClick={() => setTab("dados")}>Dados</TabBtn>
        <TabBtn active={tab === "pedidos"}  onClick={() => setTab("pedidos")}>Pedidos {customer.total_orders > 0 && <span className="cliente-tab-count">{customer.total_orders}</span>}</TabBtn>
        <TabBtn active={tab === "conversas"} onClick={() => setTab("conversas")}>Conversas</TabBtn>
      </nav>

      {tab === "dados" && (
        <form className="cliente-card" onSubmit={saveDados}>
          <div className="cliente-grid">
            <Field label="Nome">
              <input className="form-input" value={form.name || ""} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="Telefone">
              <input className="form-input" value={customer.phone} disabled />
            </Field>
            <Field label="E-mail">
              <input className="form-input" type="email" value={form.email || ""} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </Field>
            <Field label="CPF/CNPJ">
              <input className="form-input" value={form.doc || ""} onChange={(e) => setForm({ ...form, doc: e.target.value })} />
            </Field>
            <Field label="CEP">
              <input className="form-input" value={form.address?.cep || ""} onChange={(e) => setForm({ ...form, address: { ...(form.address as any), cep: e.target.value } })} />
            </Field>
            <Field label="Rua">
              <input className="form-input" value={form.address?.street || ""} onChange={(e) => setForm({ ...form, address: { ...(form.address as any), street: e.target.value } })} />
            </Field>
            <Field label="Número">
              <input className="form-input" value={form.address?.street_number || ""} onChange={(e) => setForm({ ...form, address: { ...(form.address as any), street_number: e.target.value } })} />
            </Field>
            <Field label="Complemento">
              <input className="form-input" value={form.address?.complement || ""} onChange={(e) => setForm({ ...form, address: { ...(form.address as any), complement: e.target.value } })} />
            </Field>
            <Field label="Bairro">
              <input className="form-input" value={form.address?.neighborhood || ""} onChange={(e) => setForm({ ...form, address: { ...(form.address as any), neighborhood: e.target.value } })} />
            </Field>
            <Field label="Cidade">
              <input className="form-input" value={form.address?.city || ""} onChange={(e) => setForm({ ...form, address: { ...(form.address as any), city: e.target.value } })} />
            </Field>
            <Field label="UF">
              <input className="form-input" value={form.address?.state || ""} maxLength={2} onChange={(e) => setForm({ ...form, address: { ...(form.address as any), state: e.target.value.toUpperCase() } })} />
            </Field>
          </div>
          <Field label="Observações">
            <textarea className="form-textarea" rows={3} value={form.notes || ""} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </Field>

          <div className="cliente-form-actions">
            <button className="btn btn-primary" disabled={saving}>{saving ? <Spinner size={14} /> : "Salvar"}</button>
          </div>
        </form>
      )}

      {tab === "pedidos" && (
        <>
          {!orders ? (
            <div className="portal-loading"><Spinner size={28} /></div>
          ) : orders.length === 0 ? (
            <div className="cliente-empty">Esse cliente ainda não tem pedidos registrados.</div>
          ) : (
            <>
              {stats && (
                <div className="cliente-stats">
                  <Aggregate label="Total de pedidos" value={String(stats.count)} />
                  <Aggregate label="Em aberto"        value={String(stats.open)}  />
                  <Aggregate label="Cancelados"       value={String(stats.cancelled)} />
                  <Aggregate label="Receita"          value={fmtMoney(stats.totalSpent)} />
                  <Aggregate label="Ticket médio"     value={fmtMoney(stats.avgTicket)} />
                  <Aggregate label="Último pedido"    value={fmtDate(stats.lastDate)} />
                </div>
              )}
              <div className="cliente-orders">
                {orders.map((o) => (
                  <article key={o.id} className="cliente-order">
                    <header>
                      <div>
                        <span className="cliente-order-id">#{o.id.slice(0, 8)}</span>
                        <small> · {fmtDateTime(o.created_at)}</small>
                      </div>
                      <span className={`pedidos-status pedidos-status--${o.status}`}>
                        {STATUS_LABELS[o.status]}
                      </span>
                    </header>
                    {(() => {
                      const validItems = (o.items || []).filter(
                        (it) => (it.name || it.sku) && (it.qty || 0) > 0,
                      );
                      if (validItems.length === 0) {
                        return (
                          <p className="cliente-order-noitems">
                            Itens não disponíveis para este pedido.
                          </p>
                        );
                      }
                      return (
                        <ul className="cliente-order-items">
                          {validItems.map((it, idx) => (
                            <li key={idx}>
                              <span>{it.qty}× {it.name || it.sku}</span>
                              <span>{fmtMoney((it.price || 0) * (it.qty || 0))}</span>
                            </li>
                          ))}
                        </ul>
                      );
                    })()}
                    <footer>
                      {o.notes && <small>Obs: {o.notes}</small>}
                      <strong>{fmtMoney(o.total)}</strong>
                    </footer>
                    <button className="btn btn-secondary btn-sm" onClick={() => navigate(`/portal/pedidos?q=${o.id.slice(0,8)}`)}>
                      Abrir na tela de pedidos
                    </button>
                  </article>
                ))}
              </div>
            </>
          )}
        </>
      )}

      {tab === "conversas" && (
        <>
          {!conversations ? (
            <div className="portal-loading"><Spinner size={28} /></div>
          ) : conversations.length === 0 ? (
            <div className="cliente-empty">Sem mensagens registradas para este cliente.</div>
          ) : (
            <div className="cliente-chat">
              {[...conversations].reverse().map((m, idx) => (
                <div key={idx} className={`cliente-msg cliente-msg--${m.role}`}>
                  <div className="cliente-msg-meta">
                    <strong>{m.role === "human" ? "Cliente" : (m.skill_used || "Agente")}</strong>
                    <small>{fmtDateTime(m.created_at)}</small>
                  </div>
                  <p>{m.content}</p>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </PortalLayout>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      {children}
    </div>
  );
}

function Aggregate({ label, value }: { label: string; value: string }) {
  return (
    <div className="cliente-agg">
      <span className="cliente-agg__label">{label}</span>
      <span className="cliente-agg__value">{value}</span>
    </div>
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button className={`cliente-tab ${active ? "is-active" : ""}`} onClick={onClick}>{children}</button>
  );
}
