/**
 * PortalLogs — Inbox de conversas estilo WhatsApp.
 *
 * Layout 3 colunas:
 *   • Esquerda: lista de conversas (filtro por estado + busca)
 *   • Centro:   histórico de mensagens da conversa selecionada
 *   • Direita:  painel de ações (pausar / retomar / encerrar / info do cliente)
 */
import { useEffect, useLayoutEffect, useState, useRef } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import {
  listInbox,
  getConversationMessages,
  pauseConversation,
  resumeConversation,
  closeConversation,
  type InboxItem,
  type MessageItem,
} from "../api/portal";
import "./PortalLogs.css";

type FilterState = "all" | "active" | "paused" | "closed";

const FILTER_LABELS: Record<FilterState, string> = {
  all:    "Todas",
  active: "🟢 Ativas",
  paused: "⏸ Pausadas",
  closed: "🔒 Encerradas",
};

function formatTime(iso: string | null): string {
  if (!iso) return "";
  const dt = new Date(iso);
  const now = new Date();
  const sameDay = dt.toDateString() === now.toDateString();
  if (sameDay) {
    return dt.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  }
  const diffDays = Math.floor((now.getTime() - dt.getTime()) / 86400000);
  if (diffDays < 7) {
    return dt.toLocaleDateString("pt-BR", { weekday: "short" });
  }
  return dt.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
}

function formatUntil(iso: string | null): string {
  if (!iso) return "indefinido";
  const dt = new Date(iso);
  const diffMs = dt.getTime() - Date.now();
  if (diffMs <= 0) return "expirado";
  const mins = Math.round(diffMs / 60000);
  if (mins < 60) return `${mins}min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m > 0 ? `${h}h ${m}min` : `${h}h`;
}

export function PortalLogs() {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [filter, setFilter] = useState<FilterState>("all");
  const [search, setSearch] = useState("");
  const [loadingInbox, setLoadingInbox] = useState(true);

  const [selectedPhone, setSelectedPhone] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const chatBodyRef = useRef<HTMLDivElement | null>(null);

  // useLayoutEffect roda ANTES do paint, então a classe `has-inbox` já está
  // no body quando o CSS é aplicado pela primeira vez — evita o "flash" de
  // layout quebrado durante o primeiro render.
  useLayoutEffect(() => {
    document.body.classList.add("has-inbox");
    return () => { document.body.classList.remove("has-inbox"); };
  }, []);

  // ── Loads ───────────────────────────────────────────────────────────────
  async function loadInbox() {
    setLoadingInbox(true);
    try {
      const data = await listInbox({
        search: search.trim() || undefined,
        filter_state: filter,
        limit: 200,
      });
      setItems(data);
      // Auto-seleciona a primeira se nada estiver selecionado
      if (!selectedPhone && data.length > 0) {
        setSelectedPhone(data[0].phone);
      }
    } finally {
      setLoadingInbox(false);
    }
  }

  async function loadMessages(phone: string) {
    setLoadingMessages(true);
    try {
      const data = await getConversationMessages(phone);
      setMessages(data);
    } finally {
      setLoadingMessages(false);
    }
  }

  useEffect(() => { void loadInbox(); }, [filter]);

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => { void loadInbox(); }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line
  }, [search]);

  useEffect(() => {
    if (selectedPhone) void loadMessages(selectedPhone);
    else setMessages([]);
  }, [selectedPhone]);

  // Auto-scroll para o fim ao carregar novas mensagens.
  // Usa scrollTop direto no elemento — evita que scrollIntoView role um
  // scroll-container ancestor (e quebre o layout da página inteira).
  useEffect(() => {
    const el = chatBodyRef.current;
    if (!el) return;
    // Pequeno timeout para o DOM atualizar antes de calcular scrollHeight
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
  }, [messages.length, selectedPhone]);

  // ── Ações ───────────────────────────────────────────────────────────────
  const selectedItem = items.find((i) => i.phone === selectedPhone);

  async function doPause(minutes: number | null) {
    if (!selectedPhone) return;
    setActionBusy(true);
    try {
      await pauseConversation(selectedPhone, {
        until_minutes: minutes ?? undefined,
        reason: minutes ? `pausado_${minutes}min` : "pausado_manual",
      });
      await loadInbox();
    } finally {
      setActionBusy(false);
    }
  }

  async function doResume() {
    if (!selectedPhone) return;
    setActionBusy(true);
    try {
      await resumeConversation(selectedPhone);
      await loadInbox();
    } finally {
      setActionBusy(false);
    }
  }

  async function doClose() {
    if (!selectedPhone) return;
    if (!confirm(`Encerrar atendimento de ${selectedPhone}? A IA não vai mais responder.`)) return;
    setActionBusy(true);
    try {
      await closeConversation(selectedPhone, { keep_history: true });
      await loadInbox();
    } finally {
      setActionBusy(false);
    }
  }

  return (
    <PortalLayout active="logs">
      <div className="inbox-page">
        {/* ── Coluna 1: lista de conversas ───────────────────────────── */}
        <aside className="inbox-list">
          <div className="inbox-list__head">
            <h2>Conversas</h2>
            <input
              className="inbox-search"
              type="search"
              placeholder="🔍 Buscar telefone ou nome..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <div className="inbox-filters">
              {(["all", "active", "paused", "closed"] as FilterState[]).map((f) => (
                <button
                  key={f}
                  className={`inbox-filter ${filter === f ? "is-active" : ""}`}
                  onClick={() => setFilter(f)}
                >
                  {FILTER_LABELS[f]}
                </button>
              ))}
            </div>
          </div>

          <div className="inbox-list__body">
            {loadingInbox && items.length === 0 ? (
              <div className="inbox-loading"><Spinner size={24} /></div>
            ) : items.length === 0 ? (
              <div className="inbox-empty-list">
                Nenhuma conversa para o filtro selecionado.
              </div>
            ) : (
              items.map((item) => {
                const isSelected = item.phone === selectedPhone;
                const isPaused = item.ai_paused;
                const isClosed = !!item.closed_at;
                return (
                  <button
                    key={item.phone}
                    className={`inbox-item ${isSelected ? "is-selected" : ""} ${isPaused ? "is-paused" : ""}`}
                    onClick={() => setSelectedPhone(item.phone)}
                  >
                    <div className="inbox-item__head">
                      <span className="inbox-item__name">
                        {item.customer_name || item.phone}
                      </span>
                      <span className="inbox-item__time">{formatTime(item.last_at)}</span>
                    </div>
                    <div className="inbox-item__sub">
                      {item.customer_name && (
                        <span className="inbox-item__phone">{item.phone}</span>
                      )}
                    </div>
                    <div className="inbox-item__preview">
                      {item.last_role === "assistant" && <span style={{ color: "#6b7280" }}>🤖 </span>}
                      {item.last_message || "—"}
                    </div>
                    <div className="inbox-item__badges">
                      {isClosed && <span className="inbox-badge inbox-badge--closed">🔒 Encerrado</span>}
                      {isPaused && !isClosed && (
                        <span className="inbox-badge inbox-badge--paused">
                          ⏸ Pausado · {formatUntil(item.paused_until)}
                        </span>
                      )}
                      {!isPaused && !isClosed && (
                        <span className="inbox-badge inbox-badge--active">🤖 IA ativa</span>
                      )}
                      <span className="inbox-badge inbox-badge--count">{item.message_count} msg</span>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </aside>

        {/* ── Coluna 2: histórico de mensagens ───────────────────────── */}
        <section className="inbox-chat">
          {!selectedPhone ? (
            <div className="inbox-empty">
              <div style={{ fontSize: 48, marginBottom: 12 }}>💬</div>
              <p>Selecione uma conversa à esquerda para ver o histórico.</p>
            </div>
          ) : (
            <>
              <header className="inbox-chat__head">
                <div>
                  <div className="inbox-chat__title">
                    {selectedItem?.customer_name || selectedPhone}
                  </div>
                  <div className="inbox-chat__sub">
                    {selectedItem?.customer_name && <>{selectedPhone} · </>}
                    {messages.length} mensagens
                  </div>
                </div>
                {selectedItem?.closed_at ? (
                  <span className="inbox-badge inbox-badge--closed">🔒 Encerrado</span>
                ) : selectedItem?.ai_paused ? (
                  <span className="inbox-badge inbox-badge--paused">
                    ⏸ IA pausada · volta em {formatUntil(selectedItem.paused_until)}
                  </span>
                ) : (
                  <span className="inbox-badge inbox-badge--active">🤖 IA respondendo</span>
                )}
              </header>

              <div className="inbox-chat__body" ref={chatBodyRef}>
                {loadingMessages ? (
                  <div className="inbox-loading"><Spinner size={28} /></div>
                ) : messages.length === 0 ? (
                  <div className="inbox-empty">Nenhuma mensagem nesta conversa.</div>
                ) : (
                  messages.map((m) => (
                    <div
                      key={m.id}
                      className={`bubble bubble--${m.role === "assistant" ? "bot" : "user"}`}
                    >
                      <div className="bubble__content">{m.content}</div>
                      <div className="bubble__meta">
                        {m.skill_used && <span>{m.skill_used} · </span>}
                        {new Date(m.created_at).toLocaleString("pt-BR", {
                          day: "2-digit", month: "2-digit",
                          hour: "2-digit", minute: "2-digit",
                        })}
                        {m.latency_ms && <span> · {m.latency_ms}ms</span>}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </>
          )}
        </section>

        {/* ── Coluna 3: painel de ações ──────────────────────────────── */}
        <aside className="inbox-actions">
          {!selectedItem ? (
            <div className="inbox-empty" style={{ padding: 24, color: "#9ca3af" }}>
              Selecione uma conversa para ver as ações.
            </div>
          ) : (
            <>
              <div className="inbox-actions__section">
                <h3>Cliente</h3>
                <dl className="inbox-dl">
                  <dt>Telefone</dt>
                  <dd>{selectedItem.phone}</dd>
                  {selectedItem.customer_name && (
                    <>
                      <dt>Nome</dt>
                      <dd>{selectedItem.customer_name}</dd>
                    </>
                  )}
                  <dt>Mensagens</dt>
                  <dd>{selectedItem.message_count}</dd>
                  <dt>Última</dt>
                  <dd>{formatTime(selectedItem.last_at)}</dd>
                </dl>
              </div>

              <div className="inbox-actions__section">
                <h3>Estado da IA</h3>
                {selectedItem.closed_at ? (
                  <div className="inbox-state-box inbox-state-box--closed">
                    <strong>🔒 Atendimento encerrado</strong>
                    <small>A IA não responde. Encerrado em {new Date(selectedItem.closed_at).toLocaleString("pt-BR")}</small>
                  </div>
                ) : selectedItem.ai_paused ? (
                  <div className="inbox-state-box inbox-state-box--paused">
                    <strong>⏸ IA pausada</strong>
                    <small>
                      {selectedItem.paused_until
                        ? `Volta em ${formatUntil(selectedItem.paused_until)}`
                        : "Pausa indefinida"}
                    </small>
                    {selectedItem.paused_reason && (
                      <small>Motivo: {selectedItem.paused_reason}</small>
                    )}
                  </div>
                ) : (
                  <div className="inbox-state-box inbox-state-box--active">
                    <strong>🤖 IA ativa</strong>
                    <small>Respondendo automaticamente.</small>
                  </div>
                )}
              </div>

              <div className="inbox-actions__section">
                <h3>Ações</h3>
                {selectedItem.ai_paused || selectedItem.closed_at ? (
                  <button
                    className="btn btn-primary"
                    disabled={actionBusy}
                    onClick={doResume}
                    style={{ width: "100%" }}
                  >
                    ▶ Retomar IA
                  </button>
                ) : (
                  <>
                    <div className="inbox-pause-grid">
                      <button className="btn btn-secondary" disabled={actionBusy} onClick={() => doPause(60)}>⏸ 1h</button>
                      <button className="btn btn-secondary" disabled={actionBusy} onClick={() => doPause(240)}>⏸ 4h</button>
                      <button className="btn btn-secondary" disabled={actionBusy} onClick={() => doPause(1440)}>⏸ 24h</button>
                      <button className="btn btn-secondary" disabled={actionBusy} onClick={() => doPause(null)}>⏸ Indefinido</button>
                    </div>
                    <button
                      className="btn btn-danger"
                      disabled={actionBusy}
                      onClick={doClose}
                      style={{ width: "100%", marginTop: 8 }}
                    >
                      🔒 Encerrar atendimento
                    </button>
                  </>
                )}
              </div>
            </>
          )}
        </aside>
      </div>
    </PortalLayout>
  );
}
