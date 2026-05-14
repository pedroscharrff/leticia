import { useEffect, useMemo, useRef, useState } from "react";
import { GlobalNav } from "../components/GlobalNav";
import { listTenants, Tenant } from "../api/tenants";
import { simulate, SimulateResponse } from "../api/simulate";
import "./ChatTest.css";

const SKILL_COLORS: Record<string, string> = {
  vendedor: "#3b82f6",
  farmaceutico: "#10b981",
  principio_ativo: "#8b5cf6",
  genericos: "#f59e0b",
  recuperador: "#ec4899",
  guardrails: "#ef4444",
  orchestrator: "#6366f1",
  analyst: "#14b8a6",
  load_state: "#94a3b8",
  save_state: "#94a3b8",
};

function nodeColor(node: string) {
  return SKILL_COLORS[node] ?? "#6b7280";
}

interface ChatMessage {
  id: string;
  role: "user" | "agent" | "error";
  text: string;
  trace?: SimulateResponse;
}

function newSessionId(tenantId: string, phone: string) {
  return `chat-test:${tenantId}:${phone}:${Date.now()}`;
}

function TraceStep({ step, index }: { step: Record<string, unknown>; index: number }) {
  const [open, setOpen] = useState(false);
  const node = String(step.node ?? "?");
  const ts = typeof step.ts_ms === "number" ? step.ts_ms : null;
  const rest = Object.fromEntries(
    Object.entries(step).filter(([k]) => k !== "node" && k !== "ts_ms")
  );

  return (
    <div className="trace-step">
      <button className="trace-step-head" onClick={() => setOpen((o) => !o)}>
        <span className="trace-step-idx">#{index + 1}</span>
        <span className="trace-step-node" style={{ background: nodeColor(node) }}>
          {node}
        </span>
        {ts && (
          <span className="trace-step-ts">
            {new Date(ts).toLocaleTimeString("pt-BR", {
              hour12: false,
              minute: "2-digit",
              second: "2-digit",
              fractionalSecondDigits: 3,
            })}
          </span>
        )}
      </button>
      {open && (
        <div className="trace-step-body">
          {Object.keys(rest).length === 0
            ? "(sem dados)"
            : JSON.stringify(rest, null, 2)}
        </div>
      )}
    </div>
  );
}

function TracePanel({ trace }: { trace: SimulateResponse | null }) {
  if (!trace) {
    return (
      <div className="trace-pane-empty">
        Clique em uma resposta do agente para ver o trace.
      </div>
    );
  }

  const conf = trace.confidence != null ? `${Math.round(trace.confidence * 100)}%` : "—";
  return (
    <>
      <div className="trace-summary">
        <div>
          <span>Skill</span>
          <span>{trace.selected_skill ?? "—"}</span>
        </div>
        <div>
          <span>Confiança</span>
          <span>{conf}</span>
        </div>
        <div>
          <span>Latência</span>
          <span>{trace.latency_ms}ms</span>
        </div>
        <div>
          <span>Perfil</span>
          <span>{trace.customer_profile ?? "—"}</span>
        </div>
        <div style={{ gridColumn: "1 / -1" }}>
          <span>Intenção</span>
          <span>{trace.intent ?? "—"}</span>
        </div>
      </div>
      <div>
        <div style={{ fontSize: 12, color: "#6b7280", marginBottom: 6 }}>
          Passos ({trace.trace_steps.length})
        </div>
        {trace.trace_steps.length === 0 ? (
          <div className="trace-pane-empty">Nenhum passo registrado.</div>
        ) : (
          trace.trace_steps.map((s, i) => (
            <TraceStep key={i} step={s as Record<string, unknown>} index={i} />
          ))
        )}
      </div>
    </>
  );
}

export function ChatTest() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState<string>("");
  const [phone, setPhone] = useState<string>("5511999990000");
  const [sessionId, setSessionId] = useState<string>("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listTenants()
      .then((ts) => {
        const active = ts.filter((t) => t.active);
        setTenants(active);
        if (active.length > 0) {
          setTenantId(active[0].id);
        }
      })
      .catch(console.error);
  }, []);

  useEffect(() => {
    if (tenantId) {
      setSessionId(newSessionId(tenantId, phone));
      setMessages([]);
      setSelectedTraceId(null);
    }
  }, [tenantId, phone]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  const selectedTrace = useMemo(() => {
    if (!selectedTraceId) {
      // default to last agent message trace
      const lastAgent = [...messages].reverse().find((m) => m.role === "agent" && m.trace);
      return lastAgent?.trace ?? null;
    }
    return messages.find((m) => m.id === selectedTraceId)?.trace ?? null;
  }, [selectedTraceId, messages]);

  const send = async () => {
    if (!input.trim() || !tenantId || sending) return;
    const text = input.trim();
    setInput("");

    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      role: "user",
      text,
    };
    setMessages((prev) => [...prev, userMsg]);
    setSending(true);

    try {
      const resp = await simulate({
        tenant_id: tenantId,
        phone,
        message: text,
        session_id: sessionId,
      });
      const agentMsg: ChatMessage = {
        id: `a-${Date.now()}`,
        role: "agent",
        text: resp.final_response || "(resposta vazia)",
        trace: resp,
      };
      setMessages((prev) => [...prev, agentMsg]);
      setSelectedTraceId(agentMsg.id);
    } catch (e: unknown) {
      const errText =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (e instanceof Error ? e.message : "Erro desconhecido");
      setMessages((prev) => [
        ...prev,
        { id: `e-${Date.now()}`, role: "error", text: `Erro: ${errText}` },
      ]);
    } finally {
      setSending(false);
    }
  };

  const resetSession = () => {
    setSessionId(newSessionId(tenantId, phone));
    setMessages([]);
    setSelectedTraceId(null);
  };

  return (
    <>
      <GlobalNav />
      <div className="chat-test-page">
        <div className="chat-test-header">
          <h1>Chat de Teste</h1>
          <p>
            Converse com os agentes da farmácia em tempo real. Cada mensagem mostra a skill
            selecionada, intenção e o trace completo da execução.
          </p>
        </div>

        <div className="chat-controls">
          <label>
            Tenant
            <select value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
              {tenants.length === 0 && <option value="">— nenhum tenant ativo —</option>}
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>

          <label>
            Telefone (simulado)
            <input
              type="text"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="55119..."
            />
          </label>

          <span className="session-info">
            Sessão: <code>{sessionId.slice(-16) || "—"}</code>
          </span>

          <button onClick={resetSession} disabled={!tenantId}>
            ↻ Nova sessão
          </button>
        </div>

        <div className="chat-body">
          <div className="chat-pane">
            <div className="chat-messages">
              {messages.length === 0 && !sending && (
                <div className="chat-empty">
                  Envie uma mensagem para iniciar a conversa com os agentes.
                </div>
              )}
              {messages.map((m) => {
                const skill = m.trace?.selected_skill;
                const conf = m.trace?.confidence;
                return (
                  <div
                    key={m.id}
                    className={`chat-bubble ${m.role}${
                      m.id === selectedTraceId ? " selected" : ""
                    }`}
                    onClick={() => m.role === "agent" && setSelectedTraceId(m.id)}
                  >
                    <div>{m.text}</div>
                    {m.role === "agent" && m.trace && (
                      <div className="chat-meta">
                        {skill && (
                          <span className="skill-pill" style={{ background: nodeColor(skill) }}>
                            {skill}
                          </span>
                        )}
                        {conf != null && <span>conf {Math.round(conf * 100)}%</span>}
                        <span>{m.trace.latency_ms}ms</span>
                        <span>{m.trace.trace_steps.length} passos</span>
                      </div>
                    )}
                  </div>
                );
              })}
              {sending && (
                <div className="chat-bubble agent">
                  <div className="typing-dots">
                    <span /><span /><span />
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>

            <div className="chat-input-row">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                placeholder={
                  tenantId ? "Digite uma mensagem…" : "Selecione um tenant para começar"
                }
                disabled={!tenantId || sending}
              />
              <button onClick={send} disabled={!tenantId || sending || !input.trim()}>
                Enviar
              </button>
            </div>
          </div>

          <div className="trace-pane">
            <div className="trace-pane-header">
              <h3>Trace da execução</h3>
            </div>
            <div className="trace-pane-content">
              <TracePanel trace={selectedTrace} />
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
