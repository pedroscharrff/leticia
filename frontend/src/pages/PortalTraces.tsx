import { useState, useEffect, useCallback } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { listTraces, getTrace, AgentTrace, AgentTraceDetail } from "../api/portal";
import "./PortalTraces.css";

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

function formatDate(iso: string) {
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "medium" });
}

function TraceRow({ trace, onSelect }: { trace: AgentTrace; onSelect: () => void }) {
  const confidence = trace.confidence != null ? `${Math.round(trace.confidence * 100)}%` : "—";
  const latency = trace.latency_ms != null ? `${trace.latency_ms}ms` : "—";
  const skillColor = trace.skill_used ? nodeColor(trace.skill_used) : "#6b7280";

  return (
    <tr className="trace-row" onClick={onSelect}>
      <td className="trace-td trace-time">{formatDate(trace.created_at)}</td>
      <td className="trace-td trace-phone">{trace.phone ?? "—"}</td>
      <td className="trace-td trace-message" title={trace.message_in ?? ""}>
        {trace.message_in ? (trace.message_in.length > 60 ? trace.message_in.slice(0, 60) + "…" : trace.message_in) : "—"}
      </td>
      <td className="trace-td">
        {trace.skill_used && (
          <span className="skill-badge" style={{ background: skillColor }}>
            {trace.skill_used}
          </span>
        )}
      </td>
      <td className="trace-td trace-intent" title={trace.intent ?? ""}>
        {trace.intent ? (trace.intent.length > 40 ? trace.intent.slice(0, 40) + "…" : trace.intent) : "—"}
      </td>
      <td className="trace-td trace-confidence">{confidence}</td>
      <td className="trace-td trace-latency">{latency}</td>
      <td className="trace-td">
        {trace.error
          ? <span className="status-badge error">Erro</span>
          : <span className="status-badge ok">OK</span>}
      </td>
    </tr>
  );
}

function StepCard({ step, index }: { step: Record<string, unknown>; index: number }) {
  const [open, setOpen] = useState(false);
  const node = String(step.node ?? "?");
  const ts = typeof step.ts_ms === "number" ? step.ts_ms : null;
  const rest = Object.fromEntries(Object.entries(step).filter(([k]) => k !== "node" && k !== "ts_ms"));

  return (
    <div className="step-card">
      <button className="step-header" onClick={() => setOpen(o => !o)}>
        <span className="step-index">#{index + 1}</span>
        <span className="step-node-badge" style={{ background: nodeColor(node) }}>{node}</span>
        {ts && <span className="step-ts">{new Date(ts).toLocaleTimeString("pt-BR", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3 })}</span>}
        <span className="step-toggle">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="step-body">
          {Object.entries(rest).map(([k, v]) => (
            <div key={k} className="step-field">
              <span className="step-key">{k}:</span>
              <span className="step-val">
                {typeof v === "object" ? JSON.stringify(v, null, 2) : String(v ?? "—")}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TraceModal({ traceId, onClose }: { traceId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<AgentTraceDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getTrace(traceId)
      .then(setDetail)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [traceId]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Detalhes do Trace</h2>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        {loading && <div className="modal-loading">Carregando…</div>}
        {detail && (
          <div className="modal-content">
            <div className="modal-meta-grid">
              <div><span className="meta-label">Telefone</span><span>{detail.phone ?? "—"}</span></div>
              <div><span className="meta-label">Skill</span><span>{detail.skill_used ?? "—"}</span></div>
              <div><span className="meta-label">Confiança</span><span>{detail.confidence != null ? `${Math.round(detail.confidence * 100)}%` : "—"}</span></div>
              <div><span className="meta-label">Latência</span><span>{detail.latency_ms != null ? `${detail.latency_ms}ms` : "—"}</span></div>
            </div>

            <div className="modal-section">
              <h3>Mensagem do cliente</h3>
              <p className="modal-bubble customer">{detail.message_in ?? "—"}</p>
            </div>

            <div className="modal-section">
              <h3>Resposta final</h3>
              <p className="modal-bubble agent">{detail.final_response ?? "—"}</p>
            </div>

            {detail.intent && (
              <div className="modal-section">
                <h3>Intenção detectada</h3>
                <p className="intent-text">{detail.intent}</p>
              </div>
            )}

            {detail.error && (
              <div className="modal-section error-section">
                <h3>Erro</h3>
                <pre className="error-pre">{detail.error}</pre>
              </div>
            )}

            <div className="modal-section">
              <h3>Passos do agente ({detail.steps.length})</h3>
              <div className="steps-list">
                {detail.steps.length === 0
                  ? <p className="empty-steps">Nenhum passo registrado.</p>
                  : detail.steps.map((s, i) => <StepCard key={i} step={s as Record<string, unknown>} index={i} />)
                }
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function PortalTraces() {
  const [traces, setTraces] = useState<AgentTrace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filterSkill, setFilterSkill] = useState("");
  const [filterPhone, setFilterPhone] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 50;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listTraces({
        limit,
        offset,
        skill: filterSkill || undefined,
        phone: filterPhone || undefined,
      });
      setTraces(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Erro ao carregar traces");
    } finally {
      setLoading(false);
    }
  }, [filterSkill, filterPhone, offset]);

  useEffect(() => { load(); }, [load]);

  return (
    <PortalLayout active="traces">
      <div className="traces-page">
        <div className="page-header">
          <h1>Traces de Execução</h1>
          <p className="page-subtitle">Visualize o que o squad de agentes está pensando e executando em cada conversa.</p>
        </div>

        <div className="filters-row">
          <input
            className="filter-input"
            placeholder="Filtrar por telefone…"
            value={filterPhone}
            onChange={e => { setFilterPhone(e.target.value); setOffset(0); }}
          />
          <select
            className="filter-select"
            value={filterSkill}
            onChange={e => { setFilterSkill(e.target.value); setOffset(0); }}
          >
            <option value="">Todas as skills</option>
            {Object.keys(SKILL_COLORS).map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <button className="btn-refresh" onClick={load}>↺ Atualizar</button>
        </div>

        {error && <div className="error-banner">{error}</div>}

        {loading ? (
          <div className="loading-state">Carregando traces…</div>
        ) : traces.length === 0 ? (
          <div className="empty-state">
            <p>Nenhum trace encontrado.</p>
            <p className="empty-hint">Os traces aparecem aqui após o processamento de mensagens pelo agente.</p>
          </div>
        ) : (
          <div className="table-wrapper">
            <table className="traces-table">
              <thead>
                <tr>
                  <th>Horário</th>
                  <th>Telefone</th>
                  <th>Mensagem</th>
                  <th>Skill</th>
                  <th>Intenção</th>
                  <th>Confiança</th>
                  <th>Latência</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {traces.map(t => (
                  <TraceRow key={t.id} trace={t} onSelect={() => setSelectedId(t.id)} />
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="pagination">
          <button
            className="btn-page"
            disabled={offset === 0}
            onClick={() => setOffset(o => Math.max(0, o - limit))}
          >
            ← Anterior
          </button>
          <span className="page-info">Mostrando {offset + 1}–{offset + traces.length}</span>
          <button
            className="btn-page"
            disabled={traces.length < limit}
            onClick={() => setOffset(o => o + limit)}
          >
            Próxima →
          </button>
        </div>
      </div>

      {selectedId && (
        <TraceModal traceId={selectedId} onClose={() => setSelectedId(null)} />
      )}
    </PortalLayout>
  );
}
