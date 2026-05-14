import { useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { MetricCard } from "../components/MetricCard";
import { Spinner } from "../components/Spinner";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { getMe, getUsage, type PortalMe, type UsageMetric } from "../api/portal";
import "./PortalDashboard.css";

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  return (
    <button className="copy-btn" onClick={copy} title="Copiar">
      {copied ? "✓ Copiado" : "Copiar"}
    </button>
  );
}

const PLAN_LABEL: Record<string, string> = {
  basic: "Basic",
  pro: "Pro",
  enterprise: "Enterprise",
};

export function PortalDashboard() {
  const [me, setMe] = useState<PortalMe | null>(null);
  const [usage, setUsage] = useState<UsageMetric[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getMe(), getUsage()])
      .then(([meData, usageData]) => {
        setMe(meData);
        setUsage(usageData);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <PortalLayout>
        <div className="portal-loading"><Spinner size={32} /></div>
      </PortalLayout>
    );
  }

  const totalConversations = usage.reduce((s, r) => s + r.conversations, 0);
  const totalTokens = usage.reduce((s, r) => s + r.tokens_total, 0);
  const thisMonth = usage[0];

  return (
    <PortalLayout>
      <div className="portal-page-header">
        <h1 className="portal-page-title">{me?.tenant_name ?? "Minha Farmácia"}</h1>
        <p className="portal-page-subtitle">Visão geral do seu atendimento inteligente</p>
      </div>

      {/* Info tile */}
      <div className="portal-info-tile">
        <div className="portal-info-tile__left">
          <span className="portal-plan-badge portal-plan-badge--{me?.plan}">
            {PLAN_LABEL[me?.plan ?? "basic"]}
          </span>
          <p className="portal-info-tile__label">API Key</p>
          <div className="portal-api-key-row">
            <code className="portal-api-key">{me?.api_key}</code>
            <CopyButton value={me?.api_key ?? ""} />
          </div>
          <p className="portal-info-tile__hint">
            Use essa chave para integrar o WhatsApp ao sistema de atendimento.
          </p>
        </div>
        <div className="portal-info-tile__right">
          <p className="portal-info-tile__label">Webhook URL</p>
          <div className="portal-api-key-row">
            <code className="portal-api-key">/webhook/{me?.api_key?.slice(0, 16)}…</code>
            <CopyButton value={`/webhook/${me?.api_key}`} />
          </div>
          <p className="portal-info-tile__label" style={{ marginTop: 16 }}>Callback URL</p>
          <code className="portal-api-key">{me?.callback_url}</code>
        </div>
      </div>

      {/* Métricas */}
      <div className="portal-metrics-grid">
        <MetricCard label="Conversas este mês"  value={thisMonth?.conversations ?? 0} />
        <MetricCard label="Total de conversas"  value={totalConversations} />
        <MetricCard label="Tokens consumidos"   value={totalTokens.toLocaleString("pt-BR")} />
        <MetricCard label="Custo estimado"      value={`$${usage.reduce((s, r) => s + r.cost_usd, 0).toFixed(2)}`} />
      </div>

      {/* Gráfico */}
      {usage.length > 0 && (
        <div className="portal-chart-section">
          <h2 className="portal-section-title">Conversas por mês</h2>
          <div className="portal-chart-card">
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={[...usage].reverse()} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <XAxis dataKey="month" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} />
                <Tooltip />
                <Bar dataKey="conversations" fill="var(--color-primary)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </PortalLayout>
  );
}
