import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import {
  getOrderMetrics,
  listOrders,
  getOrder,
  updateOrder,
  type OrderMetrics,
  type OrderListItem,
  type OrderDetail,
  type OrderStatus,
} from "../api/portal";
import "./PortalPedidos.css";

const STATUS_LABELS: Record<OrderStatus, string> = {
  pending:    "Pendente",
  confirmed:  "Confirmado",
  processing: "Em preparo",
  shipped:    "Enviado",
  delivered:  "Entregue",
  cancelled:  "Cancelado",
};

const ALL_STATUSES: OrderStatus[] = [
  "pending", "confirmed", "processing", "shipped", "delivered", "cancelled",
];

const STATUS_FILTERS: { key: string; label: string }[] = [
  { key: "",          label: "Todos"      },
  { key: "open",      label: "Em aberto"  },
  { key: "closed",    label: "Concluídos" },
  { key: "pending",   label: "Pendentes"  },
  { key: "confirmed", label: "Confirmados"},
  { key: "processing",label: "Em preparo" },
  { key: "shipped",   label: "Enviados"   },
  { key: "delivered", label: "Entregues"  },
  { key: "cancelled", label: "Cancelados" },
];

const fmtMoney = (n: number) =>
  n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

const fmtDateTime = (iso: string) =>
  new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });

export function PortalPedidos() {
  const [metrics, setMetrics] = useState<OrderMetrics | null>(null);
  const [orders, setOrders]   = useState<OrderListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [reloading, setReloading] = useState(false);

  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const [selected, setSelected] = useState<OrderDetail | null>(null);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [updating, setUpdating] = useState(false);
  const navigate = useNavigate();

  async function reload() {
    setReloading(true);
    try {
      const [m, list] = await Promise.all([
        getOrderMetrics(),
        listOrders({
          status: statusFilter || undefined,
          q: search || undefined,
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
        }),
      ]);
      setMetrics(m);
      setOrders(list);
    } finally {
      setReloading(false);
      setLoading(false);
    }
  }

  // Refetch when any filter changes; debounce search by 350ms.
  useEffect(() => {
    const t = setTimeout(() => { reload(); }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line
  }, [statusFilter, dateFrom, dateTo, search]);

  async function openDetail(id: string) {
    setDrawerLoading(true);
    setSelected({ id } as OrderDetail);
    try {
      const d = await getOrder(id);
      setSelected(d);
    } finally {
      setDrawerLoading(false);
    }
  }

  async function changeStatus(newStatus: OrderStatus) {
    if (!selected) return;
    setUpdating(true);
    try {
      const updated = await updateOrder(selected.id, { status: newStatus });
      setSelected(updated);
      reload();
    } finally {
      setUpdating(false);
    }
  }

  if (loading) {
    return (
      <PortalLayout active="pedidos">
        <div className="portal-loading"><Spinner size={32} /></div>
      </PortalLayout>
    );
  }

  return (
    <PortalLayout active="pedidos">
      <div className="portal-page-header pedidos-page-header">
        <div>
          <h1 className="portal-page-title">Pedidos</h1>
          <p className="portal-page-subtitle">
            Acompanhe pedidos em aberto, métricas de receita e o detalhe de cada compra.
          </p>
        </div>
        <button
          className="btn btn-secondary"
          onClick={() => navigate("/portal/pedidos/mensagens")}
        >
          ✉ Mensagens automáticas
        </button>
      </div>

      {/* ── Metric cards ───────────────────────────────────────────────── */}
      <div className="pedidos-metrics">
        <MetricCard label="Em aberto"     value={metrics?.open_count ?? 0}    accent="warning" />
        <MetricCard label="Concluídos"    value={metrics?.closed_count ?? 0}  accent="success" />
        <MetricCard label="Total"         value={metrics?.total_orders ?? 0}                />
        <MetricCard label="Receita hoje"  value={fmtMoney(metrics?.revenue_today ?? 0)} />
        <MetricCard label="Receita 7d"    value={fmtMoney(metrics?.revenue_week ?? 0)}  />
        <MetricCard label="Receita mês"   value={fmtMoney(metrics?.revenue_month ?? 0)} accent="primary" />
        <MetricCard label="Ticket médio"  value={fmtMoney(metrics?.avg_ticket_month ?? 0)} />
      </div>

      {/* ── Status breakdown ──────────────────────────────────────────── */}
      {metrics && (
        <div className="pedidos-status-breakdown">
          {ALL_STATUSES.map((s) => (
            <button
              key={s}
              className={`pedidos-status-chip pedidos-status-chip--${s} ${statusFilter === s ? "is-active" : ""}`}
              onClick={() => setStatusFilter(statusFilter === s ? "" : s)}
            >
              <span className="pedidos-status-chip__label">{STATUS_LABELS[s]}</span>
              <span className="pedidos-status-chip__count">{metrics.by_status[s]}</span>
            </button>
          ))}
        </div>
      )}

      {/* ── Filters ──────────────────────────────────────────────────── */}
      <div className="pedidos-filters">
        <select
          className="form-select"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          {STATUS_FILTERS.map((f) => (
            <option key={f.key} value={f.key}>{f.label}</option>
          ))}
        </select>
        <input
          type="search"
          className="form-input"
          placeholder="Buscar por nome, telefone ou ID…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") reload(); }}
        />
        <input
          type="date"
          className="form-input"
          value={dateFrom}
          onChange={(e) => setDateFrom(e.target.value)}
        />
        <input
          type="date"
          className="form-input"
          value={dateTo}
          onChange={(e) => setDateTo(e.target.value)}
        />
        <button className="btn btn-secondary" onClick={reload} disabled={reloading}>
          {reloading ? <Spinner size={14} /> : "Atualizar"}
        </button>
      </div>

      {/* ── Orders list ──────────────────────────────────────────────── */}
      <div className="pedidos-table">
        <div className="pedidos-table__head">
          <span>Pedido</span>
          <span>Cliente</span>
          <span>Itens</span>
          <span>Total</span>
          <span>Status</span>
          <span>Quando</span>
        </div>
        {orders.length === 0 && (
          <div className="pedidos-empty">Nenhum pedido encontrado com esses filtros.</div>
        )}
        {orders.map((o) => (
          <button key={o.id} className="pedidos-table__row" onClick={() => openDetail(o.id)}>
            <span className="pedidos-id">#{o.id.slice(0, 8)}</span>
            <span>
              <strong>{o.customer_name || "—"}</strong>
              <small>{o.customer_phone || ""}</small>
            </span>
            <span>{o.items_count}</span>
            <span><strong>{fmtMoney(o.total)}</strong></span>
            <span>
              <span className={`pedidos-status pedidos-status--${o.status}`}>
                {STATUS_LABELS[o.status]}
              </span>
            </span>
            <span>{fmtDateTime(o.created_at)}</span>
          </button>
        ))}
      </div>

      {/* ── Detail drawer ────────────────────────────────────────────── */}
      {selected && (
        <div className="pedidos-drawer__backdrop" onClick={() => setSelected(null)}>
          <aside className="pedidos-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="pedidos-drawer__header">
              <div>
                <h2>Pedido #{selected.id.slice(0, 8)}</h2>
                {selected.created_at && (
                  <small>{fmtDateTime(selected.created_at)}</small>
                )}
              </div>
              <button className="pedidos-drawer__close" onClick={() => setSelected(null)}>×</button>
            </div>

            {drawerLoading || !selected.items ? (
              <div className="portal-loading"><Spinner size={28} /></div>
            ) : (
              <>
                <section className="pedidos-drawer__section">
                  <h3>Status</h3>
                  <div className="pedidos-status-buttons">
                    {ALL_STATUSES.map((s) => (
                      <button
                        key={s}
                        disabled={updating || selected.status === s}
                        className={`pedidos-status-btn pedidos-status-btn--${s} ${selected.status === s ? "is-current" : ""}`}
                        onClick={() => changeStatus(s)}
                      >
                        {STATUS_LABELS[s]}
                      </button>
                    ))}
                  </div>
                </section>

                <section className="pedidos-drawer__section">
                  <h3>Cliente</h3>
                  <dl className="pedidos-dl">
                    <dt>Nome</dt><dd>{selected.customer.name || "—"}</dd>
                    <dt>Telefone</dt><dd>{selected.customer.phone || "—"}</dd>
                    <dt>E-mail</dt><dd>{selected.customer.email || "—"}</dd>
                    <dt>CPF/CNPJ</dt><dd>{selected.customer.doc || "—"}</dd>
                  </dl>
                  {selected.customer.address && (
                    <p className="pedidos-address">
                      {[
                        selected.customer.address.street,
                        selected.customer.address.street_number,
                        selected.customer.address.complement,
                      ].filter(Boolean).join(", ")}
                      {selected.customer.address.neighborhood && (
                        <> — {selected.customer.address.neighborhood}</>
                      )}
                      {selected.customer.address.city && (
                        <><br />{selected.customer.address.city}/{selected.customer.address.state}</>
                      )}
                      {selected.customer.address.cep && (
                        <> · CEP {selected.customer.address.cep}</>
                      )}
                    </p>
                  )}
                </section>

                <section className="pedidos-drawer__section">
                  <h3>Itens ({selected.items.length})</h3>
                  <table className="pedidos-items">
                    <thead>
                      <tr>
                        <th>Produto</th>
                        <th>Qtd</th>
                        <th>Preço</th>
                        <th>Subtotal</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selected.items.map((it, idx) => (
                        <tr key={idx}>
                          <td>
                            {it.name || it.sku || "—"}
                            {it.prescription_required && <span className="pedidos-rx">Rx</span>}
                          </td>
                          <td>{it.qty}</td>
                          <td>{fmtMoney(it.price)}</td>
                          <td>{fmtMoney(it.price * it.qty)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>

                <section className="pedidos-drawer__section pedidos-totals">
                  <div><span>Subtotal</span><strong>{fmtMoney(selected.subtotal)}</strong></div>
                  {selected.discount > 0 && (
                    <div><span>Desconto</span><strong>-{fmtMoney(selected.discount)}</strong></div>
                  )}
                  <div className="pedidos-totals__final">
                    <span>Total</span><strong>{fmtMoney(selected.total)}</strong>
                  </div>
                </section>

                {selected.requires_prescription && (
                  <div className="pedidos-warn">⚠ Este pedido contém itens com receita obrigatória.</div>
                )}

                {selected.notes && (
                  <section className="pedidos-drawer__section">
                    <h3>Observações</h3>
                    <p>{selected.notes}</p>
                  </section>
                )}
              </>
            )}
          </aside>
        </div>
      )}
    </PortalLayout>
  );
}

function MetricCard({ label, value, accent }: { label: string; value: string | number; accent?: string }) {
  return (
    <div className={`pedidos-metric ${accent ? `pedidos-metric--${accent}` : ""}`}>
      <span className="pedidos-metric__label">{label}</span>
      <span className="pedidos-metric__value">{value}</span>
    </div>
  );
}
