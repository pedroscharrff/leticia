import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import { listCustomers, type Customer } from "../api/portal";
import "./PortalClientes.css";

export function PortalClientes() {
  const navigate = useNavigate();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");

  async function load(q?: string) {
    setLoading(true);
    try {
      const data = await listCustomers({ q });
      setCustomers(data);
    } catch { setError("Erro ao carregar clientes"); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  return (
    <PortalLayout active="clientes">
      <div className="clientes-page">
        <div className="clientes-header">
          <h1 className="page-title">Clientes</h1>
          <input
            className="search-input"
            placeholder="Buscar por nome ou telefone…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); load(e.target.value); }}
          />
        </div>

        {error && <div className="error-banner">{error}</div>}

        {loading ? <Spinner /> : (
          <table className="customers-table">
            <thead>
              <tr><th>Telefone</th><th>Nome</th><th>Tags</th><th>Pedidos</th><th>Gasto total</th><th>Último contato</th></tr>
            </thead>
            <tbody>
              {customers.map((c) => (
                <tr
                  key={c.id}
                  className="customers-row"
                  onClick={() => navigate(`/portal/clientes/${c.id}`)}
                  style={{ cursor: "pointer" }}
                >
                  <td>{c.phone}</td>
                  <td>{c.name ?? "—"}</td>
                  <td>
                    <div className="tag-list">
                      {c.tags.map((t) => <span key={t} className="tag">{t}</span>)}
                    </div>
                  </td>
                  <td>{c.total_orders}</td>
                  <td>{c.total_spent > 0 ? `R$ ${c.total_spent.toFixed(2)}` : "—"}</td>
                  <td>{c.last_contact_at ? new Date(c.last_contact_at).toLocaleDateString("pt-BR") : "—"}</td>
                </tr>
              ))}
              {customers.length === 0 && (
                <tr><td colSpan={6} className="empty-row">Nenhum cliente encontrado</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </PortalLayout>
  );
}
