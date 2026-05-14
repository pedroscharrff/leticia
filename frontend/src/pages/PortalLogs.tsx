import { useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import { getLogs, type ConversationLog } from "../api/portal";
import "./PortalLogs.css";

function roleBadge(role: string) {
  if (role === "user") return <span className="role-badge role-badge--user">Cliente</span>;
  if (role === "assistant") return <span className="role-badge role-badge--bot">Agente</span>;
  return <span className="role-badge">{role}</span>;
}

export function PortalLogs() {
  const [logs, setLogs] = useState<ConversationLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const PAGE = 50;

  async function load(newOffset: number, append = false) {
    setLoading(true);
    try {
      const data = await getLogs(PAGE, newOffset);
      setLogs((prev) => append ? [...prev, ...data] : data);
      setHasMore(data.length === PAGE);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(0); }, []);

  function loadMore() {
    const newOffset = offset + PAGE;
    setOffset(newOffset);
    load(newOffset, true);
  }

  return (
    <PortalLayout>
      <div className="portal-page-header">
        <h1 className="portal-page-title">Conversas</h1>
        <p className="portal-page-subtitle">
          Histórico completo de interações com seus clientes.
        </p>
      </div>

      {loading && logs.length === 0 ? (
        <div className="portal-loading"><Spinner size={32} /></div>
      ) : logs.length === 0 ? (
        <div className="portal-empty">
          <p>Nenhuma conversa registrada ainda.</p>
          <p className="portal-empty__hint">
            As conversas aparecerão aqui assim que clientes interagirem com seu atendente.
          </p>
        </div>
      ) : (
        <>
          <div className="portal-logs-table-wrap">
            <table className="portal-logs-table">
              <thead>
                <tr>
                  <th>Data/Hora</th>
                  <th>Sessão</th>
                  <th>Tipo</th>
                  <th>Agente</th>
                  <th>Mensagem</th>
                  <th>Tokens</th>
                  <th>Latência</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id}>
                    <td className="logs-cell--date">
                      {new Date(log.created_at).toLocaleString("pt-BR", {
                        day: "2-digit", month: "2-digit",
                        hour: "2-digit", minute: "2-digit",
                      })}
                    </td>
                    <td className="logs-cell--session" title={log.session_key}>
                      {log.session_key.slice(-8)}
                    </td>
                    <td>{roleBadge(log.role)}</td>
                    <td className="logs-cell--skill">
                      {log.skill_used ?? "—"}
                    </td>
                    <td className="logs-cell--content" title={log.content}>
                      {log.content.length > 80
                        ? log.content.slice(0, 80) + "…"
                        : log.content}
                    </td>
                    <td className="logs-cell--num">
                      {log.tokens_in != null
                        ? `${log.tokens_in}↑ ${log.tokens_out ?? 0}↓`
                        : "—"}
                    </td>
                    <td className="logs-cell--num">
                      {log.latency_ms != null ? `${log.latency_ms}ms` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {hasMore && (
            <div className="portal-load-more">
              <button
                className="btn-secondary"
                onClick={loadMore}
                disabled={loading}
              >
                {loading ? <Spinner size={16} /> : "Carregar mais"}
              </button>
            </div>
          )}
        </>
      )}
    </PortalLayout>
  );
}
