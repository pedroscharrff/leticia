import { useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import { Badge } from "../components/Badge";
import { getCatalogHealth, type CatalogHealth } from "../api/portal";
import "./PortalEstoque.css";

const WINDOWS = [
  { label: "Últimos 7 dias",  value: 7 },
  { label: "Últimos 30 dias", value: 30 },
  { label: "Últimos 90 dias", value: 90 },
];

function formatDate(iso: string): string {
  try { return new Date(iso).toLocaleString("pt-BR"); }
  catch { return iso; }
}

function formatDay(iso: string): string {
  try { return new Date(iso).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }); }
  catch { return iso; }
}

export function PortalCatalogoSaude() {
  const [data, setData] = useState<CatalogHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [days, setDays] = useState(30);

  useEffect(() => {
    setLoading(true);
    setError("");
    getCatalogHealth(days)
      .then(setData)
      .catch((e) => setError(e?.response?.data?.detail ?? e.message))
      .finally(() => setLoading(false));
  }, [days]);

  const maxOrders = data
    ? Math.max(1, ...data.orders_daily.map((d) => d.total))
    : 1;

  return (
    <PortalLayout active="catalogo-saude">
      <div className="estoque-page">
        <div className="estoque-header">
          <h1 className="page-title">Saúde do Catálogo</h1>
          <div className="estoque-actions">
            <select
              className="form-input"
              value={days}
              onChange={(e) => setDays(parseInt(e.target.value, 10))}
            >
              {WINDOWS.map((w) => (
                <option key={w.value} value={w.value}>{w.label}</option>
              ))}
            </select>
          </div>
        </div>

        {error && <div className="error-banner">{error}</div>}

        {loading && <Spinner />}

        {data && !loading && (
          <>
            {/* KPIs */}
            <div className="kpi-row">
              <div className="kpi-card">
                <div className="kpi-label">Produtos ativos</div>
                <div className="kpi-value">{data.products_active}</div>
                <div className="kpi-sub">{data.sources_count} fonte(s)</div>
              </div>
              <div className="kpi-card">
                <div className="kpi-label">Última sincronização</div>
                <div className="kpi-value">
                  {data.last_sync ? (
                    <Badge variant={data.last_sync.status === "ok" ? "success" : "neutral"}>
                      {data.last_sync.status}
                    </Badge>
                  ) : "—"}
                </div>
                <div className="kpi-sub">
                  {data.last_sync
                    ? `${data.last_sync.connector} • ${formatDate(data.last_sync.created_at)}`
                    : "Nenhuma sync ainda"}
                </div>
              </div>
              <div className="kpi-card">
                <div className="kpi-label">Buscas com hit</div>
                <div className="kpi-value">
                  {data.top_searched.reduce((s, p) => s + p.hits, 0)}
                </div>
                <div className="kpi-sub">
                  vs {data.top_searched.reduce((s, p) => s + p.misses, 0)} sem resposta
                </div>
              </div>
              <div className="kpi-card">
                <div className="kpi-label">Itens efetivamente pedidos</div>
                <div className="kpi-value">
                  {data.top_requested.reduce((s, p) => s + p.quantidade, 0)}
                </div>
                <div className="kpi-sub">soma de quantidades</div>
              </div>
            </div>

            {/* Pauta de catálogo: top NÃO encontrados */}
            <section className="catalog-section">
              <h2 className="section-title">🎯 Pauta de catálogo — produtos pedidos e NÃO encontrados</h2>
              <p className="muted">
                Estes produtos foram pedidos por clientes mas não estão na sua lista.
                Adicione na planilha para parar de perder venda.
              </p>
              {data.top_missing.length === 0 ? (
                <div className="empty-row">Nenhum produto sem catálogo — você está atendendo todas as buscas! 🎉</div>
              ) : (
                <table className="products-table">
                  <thead><tr><th>Produto pedido</th><th>Vezes sem resposta</th></tr></thead>
                  <tbody>
                    {data.top_missing.map((p) => (
                      <tr key={p.produto}>
                        <td className="product-name">{p.produto}</td>
                        <td><Badge variant="neutral">{p.misses}</Badge></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>

            {/* Top buscados em geral */}
            <section className="catalog-section">
              <h2 className="section-title">🔎 Produtos mais buscados</h2>
              {data.top_searched.length === 0 ? (
                <div className="empty-row">Sem buscas no período</div>
              ) : (
                <table className="products-table">
                  <thead><tr><th>Produto</th><th>Buscas</th><th>Encontradas</th><th>Sem resposta</th></tr></thead>
                  <tbody>
                    {data.top_searched.map((p) => (
                      <tr key={p.produto}>
                        <td className="product-name">{p.produto}</td>
                        <td>{p.total}</td>
                        <td><Badge variant="success">{p.hits}</Badge></td>
                        <td>{p.misses > 0 ? <Badge variant="neutral">{p.misses}</Badge> : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>

            {/* Top efetivamente pedidos */}
            <section className="catalog-section">
              <h2 className="section-title">🛒 Produtos efetivamente pedidos</h2>
              <p className="muted">Inclui carrinho do vendedor + pedidos anotados no pré-atendimento.</p>
              {data.top_requested.length === 0 ? (
                <div className="empty-row">Nenhum pedido no período</div>
              ) : (
                <table className="products-table">
                  <thead><tr><th>Produto</th><th>Quantidade total</th></tr></thead>
                  <tbody>
                    {data.top_requested.map((p) => (
                      <tr key={p.produto}>
                        <td className="product-name">{p.produto}</td>
                        <td><Badge variant="success">{p.quantidade}</Badge></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>

            {/* Pedidos por dia (mini barras) */}
            <section className="catalog-section">
              <h2 className="section-title">📈 Pedidos por dia</h2>
              {data.orders_daily.length === 0 ? (
                <div className="empty-row">Sem pedidos no período</div>
              ) : (
                <div className="bars-row">
                  {data.orders_daily.slice().reverse().map((d) => (
                    <div className="bar-col" key={d.dia}>
                      <div className="bar-wrap" title={`${d.total} pedidos (${d.com_preco} c/ preço, ${d.sem_preco} balcão)`}>
                        <div className="bar bar--green" style={{ height: `${(d.com_preco / maxOrders) * 100}%` }} />
                        <div className="bar bar--gray"  style={{ height: `${(d.sem_preco / maxOrders) * 100}%` }} />
                      </div>
                      <div className="bar-label">{formatDay(d.dia)}</div>
                    </div>
                  ))}
                </div>
              )}
              <div className="bars-legend">
                <span><span className="legend-swatch legend-swatch--green" /> Com preço (vendedor)</span>
                <span><span className="legend-swatch legend-swatch--gray" /> Sem preço (balcão)</span>
              </div>
            </section>
          </>
        )}
      </div>
    </PortalLayout>
  );
}
