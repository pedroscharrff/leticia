import { useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import {
  getLogs,
  listConversationStates,
  pauseConversation,
  resumeConversation,
  closeConversation,
  type ConversationLog,
  type ConversationState,
} from "../api/portal";
import "./PortalLogs.css";

function roleBadge(role: string) {
  if (role === "user") return <span className="role-badge role-badge--user">Cliente</span>;
  if (role === "assistant") return <span className="role-badge role-badge--bot">Agente</span>;
  return <span className="role-badge">{role}</span>;
}

/**
 * Extrai o telefone do session_key (formato: "tenant_id:phone").
 * Se o formato não corresponder, devolve o session_key inteiro como fallback.
 */
function extractPhone(sessionKey: string): string {
  const parts = sessionKey.split(":");
  return parts.length >= 2 ? parts[parts.length - 1] : sessionKey;
}

function formatUntil(iso: string | null): string {
  if (!iso) return "indefinido";
  const dt = new Date(iso);
  const now = new Date();
  const diffMs = dt.getTime() - now.getTime();
  if (diffMs <= 0) return "expirado";
  const mins = Math.round(diffMs / 60000);
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m > 0 ? `${h}h ${m}min` : `${h}h`;
}

export function PortalLogs() {
  const [logs, setLogs] = useState<ConversationLog[]>([]);
  const [states, setStates] = useState<ConversationState[]>([]);
  const [loading, setLoading] = useState(true);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const PAGE = 50;

  async function loadLogs(newOffset: number, append = false) {
    setLoading(true);
    try {
      const data = await getLogs(PAGE, newOffset);
      setLogs((prev) => (append ? [...prev, ...data] : data));
      setHasMore(data.length === PAGE);
    } finally {
      setLoading(false);
    }
  }

  async function loadStates() {
    try {
      const data = await listConversationStates({ limit: 30 });
      setStates(data);
    } catch {
      setStates([]);
    }
  }

  useEffect(() => {
    void loadLogs(0);
    void loadStates();
  }, []);

  function loadMore() {
    const newOffset = offset + PAGE;
    setOffset(newOffset);
    void loadLogs(newOffset, true);
  }

  // ── Ações por telefone ──────────────────────────────────────────────────
  function findState(phone: string): ConversationState | undefined {
    return states.find((s) => s.phone === phone);
  }

  async function doPause(phone: string, minutes: number | null) {
    setActionBusy(phone);
    try {
      await pauseConversation(phone, {
        until_minutes: minutes ?? undefined,
        reason: minutes ? `pausado_${minutes}min` : "pausado_manual",
      });
      await loadStates();
    } finally {
      setActionBusy(null);
    }
  }

  async function doResume(phone: string) {
    setActionBusy(phone);
    try {
      await resumeConversation(phone);
      await loadStates();
    } finally {
      setActionBusy(null);
    }
  }

  async function doClose(phone: string) {
    if (!confirm(`Encerrar atendimento de ${phone}? A IA não vai mais responder.`)) return;
    setActionBusy(phone);
    try {
      await closeConversation(phone, { keep_history: true });
      await loadStates();
    } finally {
      setActionBusy(null);
    }
  }

  const pausedStates = states.filter((s) => s.ai_paused);

  return (
    <PortalLayout active="logs">
      <div className="portal-page-header">
        <h1 className="portal-page-title">Conversas</h1>
        <p className="portal-page-subtitle">
          Pause a IA, encerre atendimentos ou veja o histórico de cada interação.
        </p>
      </div>

      {/* ── Bloco: Conversas com estado especial (IA pausada ou encerrada) ── */}
      {pausedStates.length > 0 && (
        <section className="conv-state-block">
          <h2>⏸ IA pausada ou encerrada ({pausedStates.length})</h2>
          <p className="conv-state-block__hint">
            Estes clientes têm um atendente humano em ação ou foram explicitamente
            pausados. A IA não responde até retomar.
          </p>
          <div className="conv-state-grid">
            {pausedStates.map((s) => (
              <div key={s.phone} className="conv-state-card">
                <div className="conv-state-card__phone">{s.phone}</div>
                <div className="conv-state-card__meta">
                  {s.closed_at ? (
                    <span className="conv-state-card__badge conv-state-card__badge--closed">
                      🔒 Encerrado
                    </span>
                  ) : (
                    <span className="conv-state-card__badge conv-state-card__badge--paused">
                      ⏸ Pausado · volta em {formatUntil(s.paused_until)}
                    </span>
                  )}
                  <small>{s.paused_reason || "—"}</small>
                  {s.paused_by && <small>por {s.paused_by}</small>}
                </div>
                <div className="conv-state-card__actions">
                  <button
                    className="btn btn-sm btn-primary"
                    disabled={actionBusy === s.phone}
                    onClick={() => doResume(s.phone)}
                  >
                    ▶ Retomar IA
                  </button>
                  {!s.closed_at && (
                    <button
                      className="btn btn-sm btn-secondary"
                      disabled={actionBusy === s.phone}
                      onClick={() => doClose(s.phone)}
                    >
                      🔒 Encerrar
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── Tabela de logs ──────────────────────────────────────────────── */}
      {loading && logs.length === 0 ? (
        <div className="portal-loading">
          <Spinner size={32} />
        </div>
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
                  <th>Telefone</th>
                  <th>Tipo</th>
                  <th>Agente</th>
                  <th>Mensagem</th>
                  <th>Tokens</th>
                  <th>Latência</th>
                  <th>Ações</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => {
                  const phone = extractPhone(log.session_key);
                  const st = findState(phone);
                  const isPaused = st?.ai_paused ?? false;
                  return (
                    <tr key={log.id} className={isPaused ? "is-paused" : ""}>
                      <td className="logs-cell--date">
                        {new Date(log.created_at).toLocaleString("pt-BR", {
                          day: "2-digit", month: "2-digit",
                          hour: "2-digit", minute: "2-digit",
                        })}
                      </td>
                      <td className="logs-cell--phone" title={log.session_key}>
                        {phone}
                        {isPaused && (
                          <span className="logs-pause-dot" title="IA pausada">⏸</span>
                        )}
                      </td>
                      <td>{roleBadge(log.role)}</td>
                      <td className="logs-cell--skill">{log.skill_used ?? "—"}</td>
                      <td className="logs-cell--content" title={log.content}>
                        {log.content.length > 80 ? log.content.slice(0, 80) + "…" : log.content}
                      </td>
                      <td className="logs-cell--num">
                        {log.tokens_in != null
                          ? `${log.tokens_in}↑ ${log.tokens_out ?? 0}↓`
                          : "—"}
                      </td>
                      <td className="logs-cell--num">
                        {log.latency_ms != null ? `${log.latency_ms}ms` : "—"}
                      </td>
                      <td className="logs-cell--actions">
                        {isPaused ? (
                          <button
                            className="btn-link"
                            disabled={actionBusy === phone}
                            onClick={() => doResume(phone)}
                          >
                            ▶ Retomar
                          </button>
                        ) : (
                          <PauseMenu
                            phone={phone}
                            busy={actionBusy === phone}
                            onPause={(min) => doPause(phone, min)}
                            onClose={() => doClose(phone)}
                          />
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {hasMore && (
            <div className="portal-load-more">
              <button className="btn-secondary" onClick={loadMore} disabled={loading}>
                {loading ? <Spinner size={16} /> : "Carregar mais"}
              </button>
            </div>
          )}
        </>
      )}
    </PortalLayout>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// PauseMenu — micro-dropdown para escolher duração da pausa
// ─────────────────────────────────────────────────────────────────────────────

function PauseMenu({
  phone, busy, onPause, onClose,
}: {
  phone: string;
  busy: boolean;
  onPause: (minutes: number | null) => void;
  onClose: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="logs-actions-menu">
      <button
        className="btn-link"
        disabled={busy}
        onClick={() => setOpen((v) => !v)}
        title={`Ações para ${phone}`}
      >
        ⏸ Pausar IA ▾
      </button>
      {open && (
        <div className="logs-actions-dropdown" onMouseLeave={() => setOpen(false)}>
          <button onClick={() => { onPause(60); setOpen(false); }}>1 hora</button>
          <button onClick={() => { onPause(240); setOpen(false); }}>4 horas</button>
          <button onClick={() => { onPause(1440); setOpen(false); }}>24 horas</button>
          <button onClick={() => { onPause(null); setOpen(false); }}>Indefinido</button>
          <div className="logs-actions-divider"></div>
          <button className="danger" onClick={() => { onClose(); setOpen(false); }}>
            🔒 Encerrar atendimento
          </button>
        </div>
      )}
    </div>
  );
}
