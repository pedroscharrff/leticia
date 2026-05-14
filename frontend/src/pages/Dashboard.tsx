import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { GlobalNav } from "../components/GlobalNav";
import { SubNav } from "../components/SubNav";
import { MetricCard } from "../components/MetricCard";
import { Spinner } from "../components/Spinner";
import { getOverview, listTenants, getUsage } from "../api/tenants";
import type { SystemOverview, Tenant, UsageMetric } from "../api/tenants";
import "./Dashboard.css";

function IconPharmacy() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
      <path d="M19 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zM12 8v8M8 12h8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
    </svg>
  );
}
function IconActive() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
      <path d="M9 12l2 2 4-4M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}
function IconMsg() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}

const PLAN_LABEL: Record<string, string> = { basic: "Basic", pro: "Pro", enterprise: "Enterprise" };

export function Dashboard() {
  const [overview, setOverview] = useState<SystemOverview | null>(null);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [usage, setUsage] = useState<UsageMetric[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getOverview(), listTenants()])
      .then(async ([ov, ts]) => {
        setOverview(ov);
        setTenants(ts);
        // Aggregate usage from the first active tenant as a demo chart
        const active = ts.find((t) => t.active);
        if (active) {
          const u = await getUsage(active.id).catch(() => []);
          setUsage(u);
        }
      })
      .finally(() => setLoading(false));
  }, []);

  const totalConversations = usage.reduce((s, m) => s + m.conversations, 0);

  return (
    <>
      <GlobalNav />
      <SubNav title="Dashboard" />

      <main className="page-content dashboard">
        {loading ? (
          <div className="dashboard__loading">
            <Spinner size={32} />
          </div>
        ) : (
          <>
            {/* ── Metric tiles ─────────────────────────────────────────── */}
            <section className="dashboard__metrics">
              <MetricCard
                label="Total de Farmácias"
                value={overview?.total_tenants ?? 0}
                sub="tenants cadastrados"
                icon={<IconPharmacy />}
              />
              <MetricCard
                label="Farmácias Ativas"
                value={overview?.active_tenants ?? 0}
                sub="em produção agora"
                icon={<IconActive />}
                accent
              />
              <MetricCard
                label="Conversas (histórico)"
                value={totalConversations.toLocaleString("pt-BR")}
                sub="do tenant selecionado"
                icon={<IconMsg />}
              />
            </section>

            {/* ── Chart section ─────────────────────────────────────────── */}
            {usage.length > 0 && (
              <section className="dashboard__section">
                <h2 className="dashboard__section-title">Conversas por Mês</h2>
                <div className="dashboard__chart">
                  <ResponsiveContainer width="100%" height={240}>
                    <BarChart data={[...usage].reverse()} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--color-divider-soft)" vertical={false} />
                      <XAxis
                        dataKey="month"
                        tickFormatter={(v: string) => v.slice(0, 7)}
                        tick={{ fontSize: 12, fill: "var(--color-ink-muted-48)", fontFamily: "var(--font-text)" }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <YAxis
                        tick={{ fontSize: 12, fill: "var(--color-ink-muted-48)", fontFamily: "var(--font-text)" }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <Tooltip
                        contentStyle={{
                          background: "var(--color-canvas)",
                          border: "1px solid var(--color-hairline)",
                          borderRadius: "var(--radius-sm)",
                          fontFamily: "var(--font-text)",
                          fontSize: 13,
                          boxShadow: "none",
                        }}
                        cursor={{ fill: "var(--color-divider-soft)" }}
                      />
                      <Bar dataKey="conversations" fill="var(--color-primary)" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </section>
            )}

            {/* ── Tenant list preview ──────────────────────────────────── */}
            <section className="dashboard__section">
              <h2 className="dashboard__section-title">Farmácias Recentes</h2>
              <div className="dashboard__tenant-grid">
                {tenants.slice(0, 6).map((t) => (
                  <div key={t.id} className={`tenant-preview-card ${!t.active ? "tenant-preview-card--inactive" : ""}`}>
                    <div className="tenant-preview-card__header">
                      <span className="tenant-preview-card__name">{t.name}</span>
                      <span className={`tenant-preview-card__dot ${t.active ? "dot--on" : "dot--off"}`} />
                    </div>
                    <span className="tenant-preview-card__plan">{PLAN_LABEL[t.plan]}</span>
                    <span className="tenant-preview-card__date">
                      Desde {new Date(t.created_at).toLocaleDateString("pt-BR")}
                    </span>
                  </div>
                ))}
              </div>
            </section>
          </>
        )}
      </main>
    </>
  );
}
