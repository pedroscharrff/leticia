import { useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { MetricCard } from "../components/MetricCard";
import { Badge } from "../components/Badge";
import { Spinner } from "../components/Spinner";
import {
  getSubscription, getBillingUsage, listInvoices,
  subscribeToPlan, cancelSubscription,
  type Subscription, type Invoice, type BillingUsage,
} from "../api/portal";
import "./PortalBilling.css";

const PLANS = [
  { name: "basic",      label: "Básico",      price: "R$ 97/mês",   skills: 1, msgs: "500 msg/mês" },
  { name: "pro",        label: "Pro",          price: "R$ 297/mês",  skills: 4, msgs: "2.000 msg/mês" },
  { name: "enterprise", label: "Enterprise",   price: "R$ 697/mês",  skills: "Ilimitado", msgs: "Ilimitado" },
];

const STATUS_BADGE: Record<string, "green" | "yellow" | "red" | "gray"> = {
  active: "green", trialing: "yellow", past_due: "red",
  canceled: "gray", paused: "gray",
};

export function PortalBilling() {
  const [sub, setSub] = useState<Subscription | null>(null);
  const [usage, setUsage] = useState<BillingUsage | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [upgradeLoading, setUpgradeLoading] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([getSubscription(), getBillingUsage(), listInvoices()])
      .then(([s, u, i]) => { setSub(s); setUsage(u); setInvoices(i); })
      .catch(() => setError("Erro ao carregar informações de cobrança"))
      .finally(() => setLoading(false));
  }, []);

  async function handleUpgrade(plan: string) {
    setUpgradeLoading(plan);
    try {
      const me = JSON.parse(localStorage.getItem("portal_user") || "{}");
      await subscribeToPlan({
        plan_name: plan,
        provider: "stripe",
        customer_name: me.name || "Proprietário",
        customer_email: me.email || "",
      });
      const [s, u] = await Promise.all([getSubscription(), getBillingUsage()]);
      setSub(s); setUsage(u);
    } catch {
      setError("Erro ao atualizar plano. Tente novamente.");
    } finally {
      setUpgradeLoading("");
    }
  }

  async function handleCancel() {
    if (!window.confirm("Tem certeza que deseja cancelar sua assinatura?")) return;
    await cancelSubscription();
    const [s] = await Promise.all([getSubscription()]);
    setSub(s);
  }

  if (loading) return <PortalLayout active="billing"><Spinner /></PortalLayout>;

  const usagePct = usage?.limit_msgs ? Math.min(100, Math.round((usage.msgs_this_month / usage.limit_msgs) * 100)) : 0;

  return (
    <PortalLayout active="billing">
      <div className="billing-page">
        <h1 className="page-title">Assinatura & Pagamentos</h1>

        {error && <div className="error-banner">{error}</div>}

        {/* Current plan + usage */}
        <div className="billing-cards">
          <MetricCard
            title="Plano Atual"
            value={sub?.plan_name?.toUpperCase() ?? "—"}
            sub={<Badge variant={STATUS_BADGE[sub?.status ?? "gray"] ?? "gray"}>{sub?.status}</Badge>}
          />
          <MetricCard
            title="Mensagens este mês"
            value={String(usage?.msgs_this_month ?? 0)}
            sub={usage?.limit_msgs ? `de ${usage.limit_msgs}` : "ilimitado"}
          />
          {usage?.limit_msgs && (
            <div className="usage-bar-card">
              <span className="usage-bar-label">{usagePct}% utilizado</span>
              <div className="usage-bar-track">
                <div
                  className="usage-bar-fill"
                  style={{ width: `${usagePct}%`, background: usagePct > 80 ? "var(--color-red)" : "var(--color-blue)" }}
                />
              </div>
            </div>
          )}
        </div>

        {/* Plan selector */}
        <h2 className="section-title">Planos disponíveis</h2>
        <div className="plan-grid">
          {PLANS.map((p) => (
            <div key={p.name} className={`plan-card ${sub?.plan_name === p.name ? "plan-card--active" : ""}`}>
              <div className="plan-card__header">
                <span className="plan-card__label">{p.label}</span>
                <span className="plan-card__price">{p.price}</span>
              </div>
              <ul className="plan-card__features">
                <li>{p.msgs}</li>
                <li>{typeof p.skills === "number" ? `${p.skills} skill(s) ativa(s)` : "Skills ilimitadas"}</li>
              </ul>
              {sub?.plan_name !== p.name ? (
                <button
                  className="btn btn--primary btn--sm"
                  onClick={() => handleUpgrade(p.name)}
                  disabled={upgradeLoading === p.name}
                >
                  {upgradeLoading === p.name ? "Processando…" : "Selecionar"}
                </button>
              ) : (
                <span className="plan-card__current">Plano atual</span>
              )}
            </div>
          ))}
        </div>

        {/* Invoices */}
        {invoices.length > 0 && (
          <>
            <h2 className="section-title">Histórico de faturas</h2>
            <table className="invoices-table">
              <thead>
                <tr>
                  <th>Data</th><th>Valor</th><th>Status</th><th>Link</th>
                </tr>
              </thead>
              <tbody>
                {invoices.map((inv) => (
                  <tr key={inv.id}>
                    <td>{new Date(inv.created_at).toLocaleDateString("pt-BR")}</td>
                    <td>R$ {inv.amount_brl.toFixed(2)}</td>
                    <td><Badge variant={inv.status === "paid" ? "green" : "red"}>{inv.status}</Badge></td>
                    <td>
                      {inv.invoice_url
                        ? <a href={inv.invoice_url} target="_blank" rel="noreferrer">Ver fatura</a>
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        {sub?.status === "active" && (
          <div className="cancel-zone">
            <button className="btn btn--danger btn--sm" onClick={handleCancel}>
              Cancelar assinatura
            </button>
          </div>
        )}
      </div>
    </PortalLayout>
  );
}
