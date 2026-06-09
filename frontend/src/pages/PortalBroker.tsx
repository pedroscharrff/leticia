import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import {
  type DiscoveredFieldFromHistory,
  type DiscoveredPath,
  type HandoffConfig,
  type Integration,
  type RawEvent,
  createIntegration,
  deleteIntegration,
  discoverFieldsFromHistory,
  discoverPaths,
  getRawEvent,
  listIntegrations,
  listRawEvents,
  replayEvent,
  saveFlow,
  testHandoff,
  updateIntegration,
} from "../api/broker";
import "./PortalBroker.css";

type Tab = "connect" | "flow" | "handoff" | "notify" | "events";

export function PortalBroker() {
  const [params] = useSearchParams();
  const preSelectedId = params.get("selected");
  const [tab, setTab] = useState<Tab>("connect");
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [selected, setSelected] = useState<Integration | null>(null);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);

  const refresh = useCallback(async () => {
    const list = await listIntegrations();
    setIntegrations(list);
    // Se veio ?selected=<id> via query (vindo do hub Canais & Integrações),
    // pré-seleciona aquela integração. Senão, mantém o comportamento padrão.
    if (preSelectedId && (!selected || selected.id !== preSelectedId)) {
      const target = list.find((i) => i.id === preSelectedId);
      if (target) { setSelected(target); return; }
    }
    if (!selected && list.length) setSelected(list[0]);
    if (selected) {
      const updated = list.find((i) => i.id === selected.id);
      if (updated) setSelected(updated);
    }
  }, [selected, preSelectedId]);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <PortalLayout active="broker">
        <div className="portal-loading"><Spinner size={32} /></div>
      </PortalLayout>
    );
  }

  return (
    <PortalLayout active="broker">
      <div className="portal-page-header">
        <h1 className="portal-page-title">Webhooks & Integrações</h1>
        <p className="portal-page-subtitle">
          Conecte qualquer sistema externo em 3 passos. Nós recebemos, traduzimos e entregamos.
        </p>
      </div>

      <div className="broker-shell">
        <aside className="broker-side">
          <button className="broker-new" onClick={() => setShowNew(true)}>
            + Nova integração
          </button>
          {integrations.length === 0 && (
            <div className="broker-empty">
              Nenhuma integração ainda. Crie uma para começar.
            </div>
          )}
          {integrations.map((i) => (
            <button
              key={i.id}
              className={`broker-side-item ${selected?.id === i.id ? "is-active" : ""}`}
              onClick={() => setSelected(i)}
            >
              <span className={`broker-dot ${i.enabled ? "is-on" : ""}`} />
              <div>
                <div className="broker-side-name">{i.name}</div>
                <div className="broker-side-slug">/{i.slug}</div>
              </div>
            </button>
          ))}
        </aside>

        <section className="broker-main">
          {!selected && !showNew && (
            <div className="broker-blank">
              Selecione uma integração à esquerda ou crie uma nova.
            </div>
          )}

          {showNew && (
            <NewIntegrationForm
              onCancel={() => setShowNew(false)}
              onCreated={async (created) => {
                setShowNew(false);
                await refresh();
                setSelected(created);
                setTab("connect");
              }}
            />
          )}

          {selected && !showNew && (
            <>
              <div className="broker-tabs">
                {(["connect", "flow", "handoff", "notify", "events"] as Tab[]).map((t) => (
                  <button
                    key={t}
                    className={`broker-tab ${tab === t ? "is-active" : ""}`}
                    onClick={() => setTab(t)}
                  >
                    {t === "connect" && "1. Conectar"}
                    {t === "flow" && "2. Configurar fluxo"}
                    {t === "handoff" && "3. Transferir p/ atendente"}
                    {t === "notify" && "4. Notificar status de pedido"}
                    {t === "events" && "Eventos recebidos"}
                  </button>
                ))}
                <div style={{ flex: 1 }} />
                <button
                  className="broker-danger"
                  onClick={async () => {
                    if (!confirm(`Excluir integração "${selected.name}"?`)) return;
                    await deleteIntegration(selected.id);
                    setSelected(null);
                    refresh();
                  }}
                >
                  Excluir
                </button>
              </div>

              {tab === "connect" && <ConnectTab integration={selected} />}
              {tab === "flow" && <FlowTab integration={selected} onSaved={refresh} />}
              {tab === "handoff" && <HandoffTab integration={selected} onSaved={refresh} />}
              {tab === "notify" && <NotifyTab integration={selected} onSaved={refresh} />}
              {tab === "events" && <EventsTab />}
            </>
          )}
        </section>
      </div>
    </PortalLayout>
  );
}

// ── New integration form ─────────────────────────────────────────────────────

function NewIntegrationForm({
  onCancel, onCreated,
}: { onCancel: () => void; onCreated: (i: Integration) => void }) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const autoSlug = (n: string) =>
    n.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
     .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);

  async function save() {
    setSaving(true); setErr(null);
    try {
      const created = await createIntegration({ name, slug: slug || autoSlug(name) });
      onCreated(created);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Erro ao criar");
    } finally { setSaving(false); }
  }

  return (
    <div className="broker-card">
      <h2 className="broker-card-title">Nova integração</h2>
      <p className="broker-card-sub">
        Dê um nome amigável (ex: "Shopify Loja Principal"). O slug é usado na URL pública.
      </p>
      <label className="broker-field">
        <span>Nome</span>
        <input
          value={name}
          onChange={(e) => { setName(e.target.value); setSlug(autoSlug(e.target.value)); }}
          placeholder="Ex: Shopify Loja Principal"
        />
      </label>
      <label className="broker-field">
        <span>Slug (url)</span>
        <input value={slug} onChange={(e) => setSlug(autoSlug(e.target.value))} />
      </label>
      {err && <div className="broker-error">{err}</div>}
      <div className="broker-actions">
        <button className="broker-secondary" onClick={onCancel}>Cancelar</button>
        <button className="broker-primary" onClick={save} disabled={!name || saving}>
          {saving ? "Criando..." : "Criar"}
        </button>
      </div>
    </div>
  );
}

// ── Tab 1: Connect ───────────────────────────────────────────────────────────

function ConnectTab({ integration }: { integration: Integration }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(integration.inbound_url);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  }
  return (
    <div className="broker-card">
      <h2 className="broker-card-title">URL do seu webhook</h2>
      <p className="broker-card-sub">
        Cole essa URL no sistema externo. Aceita qualquer payload JSON ou form-urlencoded.
      </p>
      <div className="broker-url-box">
        <code>{integration.inbound_url}</code>
        <button className="broker-primary" onClick={copy}>{copied ? "✓ Copiado" : "Copiar"}</button>
      </div>
      <div className="broker-hint">
        <strong>Próximo:</strong> Envie um evento de teste do sistema externo, vá até{" "}
        <em>Eventos recebidos</em> e use o payload capturado para montar o mapeamento.
      </div>
    </div>
  );
}

// ── Tab 2: Flow (unified entrada + resposta) ───────────────────────────────

// Campos canônicos que o agente entende. Em uma evolução futura, isto vem
// do backend (super-admin define quais campos extras estão disponíveis).
type AgentField = {
  key: string;
  icon: string;
  label: string;
  desc: string;
  required?: boolean;
  hint?: string;  // dica do path típico
};

const AGENT_INPUT_FIELDS: AgentField[] = [
  { key: "phone",       icon: "📱", label: "Telefone do cliente",
    desc: "Número de WhatsApp pra responder", required: true, hint: "$.from" },
  { key: "message",     icon: "💬", label: "Texto da mensagem",
    desc: "O que o cliente escreveu", required: true, hint: "$.text.body" },
  { key: "name",        icon: "👤", label: "Nome do cliente",
    desc: "Para personalizar a saudação", hint: "$.profile.name" },
  { key: "session_id",  icon: "🔑", label: "ID da sessão",
    desc: "Identificador único da conversa (auto se vazio)" },
  // Campos extras pra futuras integrações (já aparecem como "avançado"):
  { key: "ticket_id",   icon: "🎫", label: "Número do ticket",
    desc: "Se o sistema externo tem um sistema de tickets" },
  { key: "cart_id",     icon: "🛒", label: "ID do carrinho",
    desc: "Para integrações com e-commerce" },
  { key: "customer_id", icon: "🆔", label: "ID interno do cliente",
    desc: "Código do cliente no seu sistema" },
];

const REPLY_VARS = [
  { path: "$.reply",      label: "Resposta do agente" },
  { path: "$.phone",      label: "Telefone (do input)" },
  { path: "$.message",    label: "Mensagem original" },
  { path: "$.name",       label: "Nome" },
  { path: "$.session_id", label: "ID da sessão" },
  { path: "$.event_id",   label: "ID do evento" },
];

function FlowTab({ integration, onSaved }: { integration: Integration; onSaved: () => void }) {
  // Listening / discovery
  const [listening, setListening] = useState(false);
  const [waitMsg, setWaitMsg] = useState<string>("");
  const [paths, setPaths] = useState<DiscoveredPath[]>([]);

  // Inbound config: map of canonical_key → source expression
  const [inputMap, setInputMap] = useState<Record<string, string>>(
    () => ({ ...(integration.inbound_field_map || {}) })
  );
  // Whether to show optional/advanced fields
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Reply config
  const [replyMode, setReplyMode] = useState<"response" | "forward">(integration.reply_mode);
  const [replyUrl, setReplyUrl] = useState(integration.reply_url ?? "");
  const [replyMethod, setReplyMethod] = useState(integration.reply_method || "POST");
  const [replyStatusCode, setReplyStatusCode] = useState(integration.reply_status_code || 200);
  const [replyHeaders, setReplyHeaders] = useState<{ key: string; value: string }[]>(() => {
    const existing = integration.reply_headers || {};
    const entries = Object.entries(existing);
    return entries.length > 0
      ? entries.map(([key, value]) => ({ key, value: String(value) }))
      : [];
  });
  // Bundling (debounce) config
  const [bundleEnabled, setBundleEnabled] = useState(integration.bundle_enabled || false);
  const [bundleWindow, setBundleWindow] = useState(integration.bundle_window_seconds || 10);
  // Skip rules (filtros para ignorar mensagens — evita loop bot↔gateway)
  const [skipRules, setSkipRules] = useState<{ path: string; equals: string; comment?: string }[]>(
    () => (integration.skip_rules || []).map(r => ({
      path: r.path, equals: String(r.equals), comment: r.comment,
    }))
  );
  const [replyFields, setReplyFields] = useState<{ key: string; expr: string }[]>(() => {
    const existing = integration.reply_body_template || {};
    const entries = Object.entries(existing);
    if (entries.length > 0) return entries.map(([key, expr]) => ({ key, expr: String(expr) }));
    return [
      { key: "to",      expr: "$.phone" },
      { key: "message", expr: "$.reply" },
    ];
  });

  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string>("");

  // Re-sync when switching integrations
  useEffect(() => {
    setInputMap({ ...(integration.inbound_field_map || {}) });
    setReplyMode(integration.reply_mode);
    setReplyUrl(integration.reply_url ?? "");
    setReplyMethod(integration.reply_method || "POST");
    setReplyStatusCode(integration.reply_status_code || 200);
    const headerEntries = Object.entries(integration.reply_headers || {});
    setReplyHeaders(headerEntries.map(([key, value]) => ({ key, value: String(value) })));
    setBundleEnabled(integration.bundle_enabled || false);
    setBundleWindow(integration.bundle_window_seconds || 10);
    setSkipRules((integration.skip_rules || []).map(r => ({
      path: r.path, equals: String(r.equals), comment: r.comment,
    })));
    const rt = Object.entries(integration.reply_body_template || {});
    setReplyFields(rt.length > 0
      ? rt.map(([key, expr]) => ({ key, expr: String(expr) }))
      : [{ key: "to", expr: "$.phone" }, { key: "message", expr: "$.reply" }]);
    setPaths([]);
    setWaitMsg("");
  }, [integration.id]);

  async function startListening() {
    const startedAt = new Date();
    setListening(true);
    setWaitMsg("Aguardando... envie um POST para a URL na aba 'Conectar'. (até 5 min)");
    setPaths([]);

    let found = false;
    for (let attempt = 0; attempt < 150 && !found; attempt++) {
      try {
        const events = await listRawEvents(undefined, 10);
        const fresh = events.find(e =>
          e.direction === "inbound"
          && e.integration_slug === integration.slug
          && new Date(e.created_at) > startedAt
        );
        if (fresh) {
          const full = await getRawEvent(fresh.id);
          if (full.payload) {
            const discovered = await discoverPaths(full.payload);
            setPaths(discovered);
            setWaitMsg(`✓ Webhook recebido! ${discovered.length} campo(s) detectado(s). Clique pra usar.`);
            found = true;
          }
        }
      } catch (e) { console.error("[broker] Poll error:", e); }
      if (!found) await new Promise(r => setTimeout(r, 2000));
    }
    if (!found) setWaitMsg("⏱ Timeout. Nenhum webhook recebido em 5 minutos.");
    setListening(false);
  }

  function setInputFor(canonicalKey: string, expr: string) {
    setInputMap(prev => {
      const next = { ...prev };
      if (expr) next[canonicalKey] = expr;
      else delete next[canonicalKey];
      return next;
    });
  }
  function setReplyField(idx: number, patch: Partial<{ key: string; expr: string }>) {
    const c = [...replyFields]; c[idx] = { ...c[idx], ...patch }; setReplyFields(c);
  }

  // Click on a payload chip → fills the first unmapped REQUIRED canonical field
  function applyPath(path: string) {
    const target = AGENT_INPUT_FIELDS.find(f => f.required && !inputMap[f.key])
                || AGENT_INPUT_FIELDS.find(f => !inputMap[f.key]);
    if (target) setInputFor(target.key, path);
  }
  function applyReplyVar(vpath: string) {
    const idx = replyFields.findIndex(f => !f.expr);
    if (idx >= 0) setReplyField(idx, { expr: vpath });
    else setReplyFields([...replyFields, { key: "", expr: vpath }]);
  }

  async function save() {
    setSaving(true); setSaveMsg("");
    const inbound_field_map: Record<string, string> = { ...inputMap };
    const reply_body_template: Record<string, string> = {};
    replyFields.forEach(f => { if (f.key && f.expr) reply_body_template[f.key] = f.expr; });
    const reply_headers: Record<string, string> = {};
    replyHeaders.forEach(h => { if (h.key && h.value) reply_headers[h.key] = h.value; });

    try {
      // Converte equals string → tipo correto (true/false/number ou string)
      const coerce = (v: string): unknown => {
        if (v === "true") return true;
        if (v === "false") return false;
        if (/^-?\d+(\.\d+)?$/.test(v)) return Number(v);
        return v;
      };
      const skip_rules = skipRules
        .filter(r => r.path && r.equals !== "")
        .map(r => ({ path: r.path, equals: coerce(r.equals), comment: r.comment }));

      await saveFlow(integration.id, {
        inbound_field_map,
        reply_mode: replyMode,
        reply_url: replyMode === "forward" ? replyUrl : null,
        reply_method: replyMethod,
        reply_headers,
        reply_body_template,
        reply_status_code: replyStatusCode,
        bundle_enabled: bundleEnabled,
        bundle_window_seconds: bundleWindow,
        skip_rules,
        // Preserva config de handoff (editada na aba "Transferir p/ atendente")
        handoff_config: integration.handoff_config || {},
        session_config: integration.session_config || {},
        handoff_pause_minutes: integration.handoff_pause_minutes ?? 240,
        human_handoff_detection: integration.human_handoff_detection || {},
        ticket_lifecycle_detection: integration.ticket_lifecycle_detection || {},
      });
      setSaveMsg("✓ Configuração salva!");
      onSaved();
    } catch (e: any) {
      setSaveMsg("✗ Erro: " + (e?.response?.data?.detail ?? e.message));
    } finally { setSaving(false); }
  }

  // Configuração mínima: os campos obrigatórios estão mapeados
  const missingRequired = AGENT_INPUT_FIELDS
    .filter(f => f.required && !inputMap[f.key])
    .map(f => f.label);
  const hasConfig = missingRequired.length === 0;

  return (
    <div className="broker-mapper">
      {/* Step 1: Listening */}
      <div className="broker-card">
        <h3 className="broker-card-title">1. Capture um exemplo de requisição</h3>
        <p className="broker-card-sub">
          Ative a escuta abaixo e envie uma requisição de teste do sistema externo (ou do Postman).
          Os campos do payload aparecerão como botões que você pode clicar para usar no mapeamento.
        </p>
        <button
          className={`broker-primary ${listening ? "is-listening" : ""}`}
          onClick={startListening}
          disabled={listening}
          style={{ width: "100%" }}
        >
          {listening ? "🔴 Aguardando webhook..." : "▶ Ativar escuta (5 min)"}
        </button>
        {waitMsg && (
          <div style={{
            marginTop: 12, padding: "10px 12px",
            background: waitMsg.includes("✓") ? "#e8f5e9" : "#fff8e1",
            border: `1px solid ${waitMsg.includes("✓") ? "#81c784" : "#ffb800"}`,
            borderRadius: 6, fontSize: 13,
            color: waitMsg.includes("✓") ? "#2e7d32" : "#f57c00",
          }}>{waitMsg}</div>
        )}
        {paths.length > 0 && (
          <div className="broker-paths">
            <div className="broker-paths-title">Campos do payload (clique para usar como entrada)</div>
            {paths.map((p) => (
              <button key={p.path} className="broker-path-chip"
                      onClick={() => applyPath(p.path)} title={p.sample}>
                <code>{p.path}</code>
                <span className="broker-path-type">{p.type}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Step 2: Inbound mapping (entrada do agente) */}
      <div className="broker-card">
        <h3 className="broker-card-title">2. O que o agente recebe</h3>
        <p className="broker-card-sub">
          Para cada campo abaixo, escolha de qual lugar do payload ele vem.
          Você pode clicar em um chip acima OU usar o seletor.
        </p>

        <div className="agent-fields">
          {AGENT_INPUT_FIELDS.filter(f => f.required || showAdvanced || inputMap[f.key]).map((f) => {
            const mapped = inputMap[f.key] || "";
            const knownPath = paths.find(p => p.path === mapped);
            const useCustom = mapped && !knownPath && mapped !== "__manual__";
            return (
              <div key={f.key} className={`agent-field ${mapped ? "is-mapped" : ""}`}>
                <div className="agent-field-icon">{f.icon}</div>
                <div className="agent-field-info">
                  <div className="agent-field-label">
                    {f.label}
                    {f.required && <span className="agent-field-required">obrigatório</span>}
                  </div>
                  <div className="agent-field-desc">{f.desc}</div>
                </div>
                <div className="agent-field-mapping">
                  {paths.length > 0 ? (
                    <select
                      value={useCustom ? "__manual__" : mapped}
                      onChange={(e) => {
                        if (e.target.value === "__manual__") {
                          setInputFor(f.key, mapped || "$.");
                        } else {
                          setInputFor(f.key, e.target.value);
                        }
                      }}
                    >
                      <option value="">— não usar —</option>
                      {paths.map(p => (
                        <option key={p.path} value={p.path}>
                          {p.path}  ·  {p.sample ? `"${p.sample.slice(0, 24)}"` : p.type}
                        </option>
                      ))}
                      <option value="__manual__">✏️ Digitar manualmente...</option>
                    </select>
                  ) : (
                    <input
                      type="text"
                      value={mapped}
                      onChange={(e) => setInputFor(f.key, e.target.value)}
                      placeholder={f.hint || "$.caminho.no.payload"}
                    />
                  )}
                  {useCustom && paths.length > 0 && (
                    <input
                      type="text"
                      className="agent-field-manual"
                      value={mapped}
                      onChange={(e) => setInputFor(f.key, e.target.value)}
                      placeholder={f.hint || "$.caminho | digits"}
                    />
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <details style={{ marginTop: 12 }}>
          <summary style={{
            cursor: "pointer", fontSize: 13, fontWeight: 600,
            color: "var(--color-text-muted, #86868b)",
          }}>
            🔧 Transformações disponíveis (regex, digits, lower, etc.)
          </summary>
          <div style={{
            marginTop: 8, padding: 12,
            background: "#f5f5f7", borderRadius: 8, fontSize: 12,
            color: "#1d1d1f", lineHeight: 1.7,
          }}>
            <p style={{ margin: "0 0 8px" }}>
              Use <code>|</code> (pipe com espaço dos dois lados) para encadear
              transformações no valor extraído:
            </p>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <tbody>
                <tr>
                  <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>
                    <code>$.from | digits</code>
                  </td>
                  <td style={{ padding: "4px 8px" }}>
                    Mantém só os dígitos.<br/>
                    <span style={{ color: "#86868b" }}>
                      Ex: <code>5511999@s.whatsapp.net</code> → <code>5511999</code>
                    </span>
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>
                    <code>$.from | regex:(\d+)</code>
                  </td>
                  <td style={{ padding: "4px 8px" }}>
                    Extrai a primeira ocorrência do padrão (ou grupo 1).<br/>
                    <span style={{ color: "#86868b" }}>
                      Ex: <code>tel: 11 99999-1234</code> com <code>regex:(\d{`{4}`}-\d{`{4}`})</code> → <code>9999-1234</code>
                    </span>
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>
                    <code>$.text | trim</code>
                  </td>
                  <td style={{ padding: "4px 8px" }}>Remove espaços em branco das pontas.</td>
                </tr>
                <tr>
                  <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>
                    <code>$.text | lower</code> / <code>upper</code>
                  </td>
                  <td style={{ padding: "4px 8px" }}>Converte para minúsculas / MAIÚSCULAS.</td>
                </tr>
                <tr>
                  <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>
                    <code>$.x | slice:0:10</code>
                  </td>
                  <td style={{ padding: "4px 8px" }}>
                    Pega só os caracteres da posição N até M.<br/>
                    <span style={{ color: "#86868b" }}>Ex: <code>"abcdefghij"</code> → <code>"abcde"</code> com <code>slice:0:5</code></span>
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>
                    <code>$.name | default:Cliente</code>
                  </td>
                  <td style={{ padding: "4px 8px" }}>
                    Valor padrão se o campo estiver vazio/nulo.
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "4px 8px", fontFamily: "monospace", verticalAlign: "top" }}>
                    <code>$.from | regex:(\d+) | digits</code>
                  </td>
                  <td style={{ padding: "4px 8px" }}>
                    Encadeia várias transformações da esquerda pra direita.
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 8 }}>
          <button className="broker-link" onClick={() => setShowAdvanced(!showAdvanced)}>
            {showAdvanced ? "− Ocultar campos opcionais" : "+ Mostrar campos opcionais (ticket, carrinho, etc.)"}
          </button>
          {missingRequired.length > 0 && (
            <div style={{ fontSize: 12, color: "#c62828" }}>
              Faltam: {missingRequired.join(", ")}
            </div>
          )}
        </div>
      </div>

      {/* Step 3: Reply mode */}
      <div className="broker-card">
        <h3 className="broker-card-title">3. Como devolver a resposta do agente</h3>

        <div className="broker-radio-group">
          <label className={`broker-radio ${replyMode === "response" ? "is-active" : ""}`}>
            <input type="radio" name="mode" checked={replyMode === "response"}
                   onChange={() => setReplyMode("response")} />
            <div>
              <strong>📥 Responder na mesma URL (síncrono)</strong>
              <div className="broker-radio-desc">
                Aguarda o agente processar e devolve a resposta no mesmo HTTP request.
                Use quando o sistema externo espera a resposta de volta na mesma chamada
                (ex: chatbot widget, integração HTTP simples). Pode demorar 2-8s.
              </div>
            </div>
          </label>
          <label className={`broker-radio ${replyMode === "forward" ? "is-active" : ""}`}>
            <input type="radio" name="mode" checked={replyMode === "forward"}
                   onChange={() => setReplyMode("forward")} />
            <div>
              <strong>📤 Enviar para outra URL (assíncrono)</strong>
              <div className="broker-radio-desc">
                Devolve <code>202</code> imediato e o agente processa em background.
                Quando termina, faz POST com a resposta na URL configurada.
                Ideal pra gateways tipo Z-API, WAHA, Twilio (que mandam mensagem de volta).
              </div>
            </div>
          </label>
        </div>

        {replyMode === "forward" && (
          <div className="broker-forward-config">
            <div style={{ display: "grid", gridTemplateColumns: "100px 1fr", gap: 8 }}>
              <label className="broker-field">
                <span>Método</span>
                <select value={replyMethod} onChange={(e) => setReplyMethod(e.target.value)}>
                  <option>POST</option><option>PUT</option><option>PATCH</option>
                </select>
              </label>
              <label className="broker-field">
                <span>URL destino</span>
                <input value={replyUrl} onChange={(e) => setReplyUrl(e.target.value)}
                       placeholder="https://api.z-api.io/instances/.../send-text" />
              </label>
            </div>
          </div>
        )}

        {replyMode === "response" && (
          <label className="broker-field" style={{ maxWidth: 200 }}>
            <span>Status HTTP</span>
            <input type="number" value={replyStatusCode}
                   onChange={(e) => setReplyStatusCode(parseInt(e.target.value) || 200)} />
          </label>
        )}

        {/* Headers — funciona pros dois modos (response = response HTTP, forward = POST destino) */}
        <details style={{ marginTop: 12 }}>
          <summary style={{
            cursor: "pointer", fontSize: 13, fontWeight: 600,
            color: "var(--color-text-muted, #86868b)", marginBottom: 8,
          }}>
            Headers personalizados (opcional)
            {replyHeaders.length > 0 && (
              <span style={{
                marginLeft: 8, fontSize: 11, padding: "1px 8px",
                background: "#e3f2fd", color: "#1565c0", borderRadius: 10,
              }}>{replyHeaders.length}</span>
            )}
          </summary>
          <p className="broker-card-sub" style={{ marginTop: 8 }}>
            {replyMode === "forward" ? (
              <>Adicionados ao POST para a URL destino. Útil para autenticação
              (ex: <code>Authorization: Bearer ...</code>) ou tokens de gateway
              (Z-API, Twilio, etc.).</>
            ) : (
              <>Adicionados ao HTTP response devolvido ao chamador. Útil para
              definir <code>Content-Type</code> customizado ou headers que o
              sistema externo valida na resposta.</>
            )}
          </p>
          {replyHeaders.map((h, idx) => (
            <div key={idx} className="broker-row">
              <input className="broker-row-key" placeholder="Authorization"
                     value={h.key} onChange={(e) => {
                       const c = [...replyHeaders];
                       c[idx] = { ...c[idx], key: e.target.value };
                       setReplyHeaders(c);
                     }} />
              <span className="broker-arrow">:</span>
              <input className="broker-row-expr" placeholder="Bearer abc123..."
                     value={h.value} onChange={(e) => {
                       const c = [...replyHeaders];
                       c[idx] = { ...c[idx], value: e.target.value };
                       setReplyHeaders(c);
                     }} />
              <button className="broker-row-del" onClick={() =>
                setReplyHeaders(replyHeaders.filter((_, i) => i !== idx))
              }>×</button>
            </div>
          ))}
          <button className="broker-link" onClick={() =>
            setReplyHeaders([...replyHeaders, { key: "", value: "" }])
          }>
            + Adicionar header
          </button>

          <div style={{ marginTop: 8, fontSize: 12, color: "#86868b" }}>
            <strong>Exemplos comuns:</strong>{" "}
            <button className="broker-link" onClick={() =>
              setReplyHeaders([...replyHeaders, { key: "Authorization", value: "Bearer " }])
            }>Authorization</button>
            <button className="broker-link" onClick={() =>
              setReplyHeaders([...replyHeaders, { key: "Client-Token", value: "" }])
            }>Client-Token (Z-API)</button>
            <button className="broker-link" onClick={() =>
              setReplyHeaders([...replyHeaders, { key: "X-API-Key", value: "" }])
            }>X-API-Key</button>
            <button className="broker-link" onClick={() =>
              setReplyHeaders([...replyHeaders, { key: "Content-Type", value: "application/json" }])
            }>Content-Type</button>
          </div>
        </details>

        <div className="broker-paths" style={{ marginTop: 14 }}>
          <div className="broker-paths-title">Variáveis disponíveis (clique para usar no corpo)</div>
          {REPLY_VARS.map((v) => (
            <button key={v.path} className="broker-path-chip" onClick={() => applyReplyVar(v.path)}>
              <code>{v.path}</code>
              <span className="broker-path-type">{v.label}</span>
            </button>
          ))}
        </div>

        <div className="broker-rows-title">Corpo da resposta</div>
        {replyFields.map((f, idx) => (
          <div key={idx} className="broker-row">
            <input className="broker-row-key" placeholder="campo_destino"
                   value={f.key} onChange={(e) => setReplyField(idx, { key: e.target.value })} />
            <span className="broker-arrow">←</span>
            <input className="broker-row-expr" placeholder="$.reply"
                   value={f.expr} onChange={(e) => setReplyField(idx, { expr: e.target.value })} />
            <button className="broker-row-del"
                    onClick={() => setReplyFields(replyFields.filter((_, i) => i !== idx))}>×</button>
          </div>
        ))}
        <button className="broker-link"
                onClick={() => setReplyFields([...replyFields, { key: "", expr: "" }])}>
          + Adicionar campo
        </button>
      </div>

      {/* Step 4: Filtros — IMPORTANTÍSSIMO pra evitar loop bot ↔ gateway */}
      <div className="broker-card" style={{ borderLeft: "4px solid #ff9800" }}>
        <h3 className="broker-card-title">
          🛡 Filtros — ignorar mensagens que não devem ser respondidas
        </h3>
        <p className="broker-card-sub">
          <strong>Crítico pra evitar loops!</strong> Gateways como Z-API e WAHA reenviam
          como webhook as mensagens que <em>nós mesmos</em> enviamos — sem filtro, o agente
          responde à própria resposta, gerando loop infinito.
        </p>

        {paths.length === 0 && (
          <div style={{
            padding: 10, background: "#e3f2fd", borderRadius: 6, fontSize: 13,
            color: "#1565c0", marginBottom: 12,
          }}>
            💡 Dica: capture um exemplo de webhook no passo 1 (botão "Ativar escuta")
            pra ver os campos disponíveis nos dropdowns abaixo.
          </div>
        )}

        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
          <span style={{ fontSize: 12, color: "#86868b", marginRight: 4, alignSelf: "center" }}>
            Presets:
          </span>
          <button className="broker-tmpl" onClick={() => setSkipRules([
            ...skipRules,
            { path: "$.fromMe", equals: "true", comment: "Z-API: ignorar mensagens enviadas pelo bot" }
          ])}>Z-API: fromMe</button>
          <button className="broker-tmpl" onClick={() => setSkipRules([
            ...skipRules,
            { path: "$.isStatusReply", equals: "true", comment: "Z-API: ignorar status replies" }
          ])}>Z-API: status</button>
          <button className="broker-tmpl" onClick={() => setSkipRules([
            ...skipRules,
            { path: "$.event", equals: "message.ack", comment: "WAHA: ignorar acks" }
          ])}>WAHA: ack</button>
          <button className="broker-tmpl" onClick={() => setSkipRules([
            ...skipRules,
            { path: "$.fromApi", equals: "true", comment: "Ignorar mensagens originadas da API" }
          ])}>fromApi</button>
        </div>

        {skipRules.length === 0 && (
          <div style={{
            padding: 12, background: "#fff3e0", borderRadius: 6, fontSize: 13,
            color: "#e65100", marginBottom: 10,
          }}>
            ⚠️ Nenhum filtro configurado. Se você usa Z-API/WAHA/Twilio,
            configure pelo menos um pra impedir loops.
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {skipRules.map((r, idx) => {
            const knownPath = paths.find(p => p.path === r.path);
            const useCustomPath = r.path && !knownPath;
            // Detecta se a regra está mal configurada: caminho não existe no payload capturado
            const pathMissing = r.path && paths.length > 0 && !knownPath;

            function updateRule(patch: Partial<typeof r>) {
              const c = [...skipRules]; c[idx] = { ...c[idx], ...patch }; setSkipRules(c);
            }

            return (
              <div key={idx} style={{
                border: pathMissing
                  ? "2px solid #c62828"
                  : "1px solid var(--color-border, #e5e7eb)",
                borderRadius: 8, padding: 10,
                background: pathMissing ? "#ffebee" : "#fafafa",
              }}>
                <div style={{
                  fontSize: 12, fontWeight: 600, color: "#c62828",
                  marginBottom: 8, textTransform: "uppercase", letterSpacing: 0.3,
                }}>
                  🚫 Ignorar mensagem quando:
                </div>
                {pathMissing && (
                  <div style={{
                    padding: "8px 10px", marginBottom: 8,
                    background: "white", border: "1px solid #c62828",
                    borderRadius: 6, fontSize: 12, color: "#c62828",
                  }}>
                    ⚠️ <strong>Esse caminho não existe no último webhook recebido.</strong>
                    {" "}A regra nunca vai matchar nada. Escolha um dos campos do dropdown
                    pra ter certeza que existe.
                  </div>
                )}
                <div style={{
                  display: "grid",
                  gridTemplateColumns: "1fr auto 1fr 32px",
                  gap: 6, alignItems: "center",
                }}>
                  {/* Path: select se temos paths, input manual senão */}
                  {paths.length > 0 ? (
                    <select
                      value={useCustomPath ? "__manual__" : r.path}
                      onChange={(e) => {
                        if (e.target.value === "__manual__") {
                          updateRule({ path: r.path || "$." });
                        } else {
                          // Quando seleciona um path, auto-preenche equals com o valor atual
                          const selected = paths.find(p => p.path === e.target.value);
                          const suggestedValue = selected?.sample ?? "";
                          // Limpa aspas se vier em string JSON
                          const cleanValue = suggestedValue.replace(/^"|"$/g, "");
                          updateRule({
                            path: e.target.value,
                            equals: r.equals || cleanValue,
                          });
                        }
                      }}
                      style={{
                        padding: "8px 10px",
                        border: "1px solid var(--color-border, #e5e7eb)",
                        borderRadius: 6, fontSize: 13,
                        fontFamily: r.path ? "monospace" : "inherit",
                        background: "white",
                      }}
                    >
                      <option value="">— selecione o campo —</option>
                      {paths.map(p => (
                        <option key={p.path} value={p.path}>
                          {p.path}  ·  {p.sample ? `"${p.sample.slice(0, 24)}"` : p.type}
                        </option>
                      ))}
                      <option value="__manual__">✏️ Digitar manualmente...</option>
                    </select>
                  ) : (
                    <input className="broker-row-key" placeholder="$.fromMe"
                           value={r.path} onChange={(e) => updateRule({ path: e.target.value })} />
                  )}

                  <span className="broker-arrow">==</span>

                  {/* Equals: dropdown sugerindo valores comuns se for tipo bool */}
                  {knownPath && knownPath.type === "bool" ? (
                    <select
                      value={r.equals}
                      onChange={(e) => updateRule({ equals: e.target.value })}
                      style={{
                        padding: "8px 10px",
                        border: "1px solid var(--color-border, #e5e7eb)",
                        borderRadius: 6, fontSize: 13, background: "white",
                      }}
                    >
                      <option value="">—</option>
                      <option value="true">true</option>
                      <option value="false">false</option>
                    </select>
                  ) : (
                    <input
                      className="broker-row-expr"
                      placeholder={knownPath?.sample
                        ? `valor recebido: ${knownPath.sample.slice(0, 20)}`
                        : "true"}
                      value={r.equals}
                      onChange={(e) => updateRule({ equals: e.target.value })}
                    />
                  )}

                  <button className="broker-row-del" onClick={() =>
                    setSkipRules(skipRules.filter((_, i) => i !== idx))
                  }>×</button>
                </div>

                {/* Frase em PT-BR explicando o que a regra faz */}
                {r.path && r.equals && (
                  <div style={{
                    marginTop: 8, padding: "8px 10px",
                    background: "#fff8e1", borderRadius: 6,
                    fontSize: 12, color: "#5d4037",
                  }}>
                    📋 <strong>O que essa regra faz:</strong> quando o webhook recebido
                    tiver <code>{r.path}</code> igual a <strong>{r.equals}</strong>,
                    a mensagem será <strong>ignorada</strong> (não processa, não responde).
                    {knownPath && String(knownPath.sample).replace(/^"|"$/g, "") === r.equals && (
                      <div style={{ marginTop: 4, color: "#c62828", fontWeight: 600 }}>
                        ⚠️ Atenção: o último webhook recebido tem esse mesmo valor —
                        confirme se isso é uma mensagem que você QUER ignorar.
                      </div>
                    )}
                  </div>
                )}

                {/* Comentário e dica do valor atual */}
                <div style={{ marginTop: 6, display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    placeholder="Comentário (opcional, aparece no log)"
                    value={r.comment ?? ""}
                    onChange={(e) => updateRule({ comment: e.target.value })}
                    style={{
                      flex: 1,
                      padding: "6px 10px",
                      border: "1px solid var(--color-border, #e5e7eb)",
                      borderRadius: 6, fontSize: 12, color: "#86868b",
                    }}
                  />
                  {knownPath && (
                    <span style={{
                      fontSize: 11, color: "#86868b",
                      padding: "3px 8px", background: "white",
                      border: "1px solid var(--color-border, #e5e7eb)",
                      borderRadius: 6,
                    }} title={`Valor real no último webhook: ${knownPath.sample}`}>
                      atual: <code>{knownPath.sample?.slice(0, 30) ?? "—"}</code>
                    </span>
                  )}
                </div>

                {/* Custom path input quando user escolheu "Digitar manualmente" */}
                {useCustomPath && paths.length > 0 && (
                  <input
                    type="text"
                    value={r.path}
                    onChange={(e) => updateRule({ path: e.target.value })}
                    placeholder="$.caminho.no.payload"
                    style={{
                      marginTop: 6, width: "100%",
                      padding: "8px 10px",
                      border: "1px solid var(--color-border, #e5e7eb)",
                      borderRadius: 6, fontSize: 13, fontFamily: "monospace",
                      background: "#fffde7",
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>

        <button className="broker-link" onClick={() =>
          setSkipRules([...skipRules, { path: "", equals: "", comment: "" }])
        } style={{ marginTop: 10 }}>
          + Adicionar regra
        </button>

        <details style={{ marginTop: 12, fontSize: 12, color: "#86868b" }}>
          <summary style={{ cursor: "pointer" }}>Como descobrir o campo certo?</summary>
          <div style={{ marginTop: 6 }}>
            Após ativar a escuta no passo 1 e receber um webhook que veio
            <strong> depois do bot responder</strong> (= é a própria resposta sendo
            ecoada), os campos vão aparecer nos dropdowns acima. Procure por:
            <ul style={{ marginTop: 6 }}>
              <li><code>fromMe: true</code> (Z-API, Baileys)</li>
              <li><code>isFromMe: true</code> (variantes)</li>
              <li><code>event: "message.ack"</code> ou <code>"message.delivered"</code> (WAHA)</li>
              <li>Campo <code>statuses</code> presente em vez de <code>messages</code> (WhatsApp Cloud)</li>
            </ul>
            Quando você escolhe o campo no dropdown, o valor real recebido aparece
            como sugestão no campo "== valor".
          </div>
        </details>
      </div>

      {/* Step 5: Bundling (debounce) — só faz sentido no modo forward */}
      <div className="broker-card">
        <h3 className="broker-card-title">5. Agrupar mensagens picadas (debounce)</h3>
        <p className="broker-card-sub">
          Clientes no WhatsApp mandam mensagens em sequência ("Olá", "Bom dia", "Tudo bem?").
          Em vez de chamar o agente 3 vezes, aguardamos alguns segundos de silêncio,
          concatenamos tudo e processamos como uma só mensagem.
        </p>

        <label className="agent-field" style={{ cursor: "pointer", marginBottom: 12 }}>
          <div className="agent-field-icon">⏱</div>
          <div className="agent-field-info">
            <div className="agent-field-label">
              Ativar agrupamento
              {replyMode !== "forward" && (
                <span className="agent-field-required" style={{
                  background: "#fff3e0", color: "#e65100",
                }}>
                  só funciona no modo "Enviar para outra URL"
                </span>
              )}
            </div>
            <div className="agent-field-desc">
              Acumula mensagens recebidas no mesmo telefone e processa todas juntas
              após o tempo de silêncio configurado.
            </div>
          </div>
          <div className="agent-field-mapping">
            <input
              type="checkbox"
              checked={bundleEnabled}
              onChange={(e) => setBundleEnabled(e.target.checked)}
              style={{ width: 22, height: 22, cursor: "pointer" }}
            />
          </div>
        </label>

        {bundleEnabled && (
          <div className="broker-row" style={{ gridTemplateColumns: "1fr 120px auto" }}>
            <div style={{ fontSize: 14, color: "#1d1d1f", fontWeight: 500 }}>
              ⏱ Tempo de espera após cada mensagem
            </div>
            <input
              type="number"
              min={2}
              max={120}
              value={bundleWindow}
              onChange={(e) => setBundleWindow(Math.max(2, Math.min(120, parseInt(e.target.value) || 10)))}
              style={{ fontFamily: "inherit", textAlign: "center" }}
            />
            <span style={{ color: "#86868b", fontSize: 13 }}>segundos (2–120)</span>
          </div>
        )}

        {bundleEnabled && replyMode === "response" && (
          <div className="broker-error" style={{ marginTop: 10 }}>
            ⚠️ Agrupamento só funciona no modo "Enviar para outra URL".
            No modo síncrono ("Responder na mesma URL"), cada requisição precisa
            responder imediatamente — não dá pra esperar mais mensagens.
          </div>
        )}
      </div>

      {/* Save */}
      <div className="broker-card">
        <div className="broker-actions" style={{ justifyContent: "space-between" }}>
          <div style={{
            fontSize: 13,
            color: saveMsg.includes("✓") ? "#2e7d32" : saveMsg.includes("✗") ? "#c62828" : "#86868b",
          }}>
            {saveMsg || (hasConfig ? "Pronto pra salvar." : "Preencha pelo menos um campo de entrada.")}
          </div>
          <button className="broker-primary" onClick={save} disabled={!hasConfig || saving}>
            {saving ? "Salvando..." : "Salvar configuração"}
          </button>
        </div>
      </div>
    </div>
  );
}


// ── Tab 3: Handoff (transferência para atendente humano / balcão) ──────────

const DEFAULT_TRIGGER_KEYWORDS = [
  "atendente", "humano", "pessoa", "balcão", "balcao",
  "falar com alguém", "falar com alguem", "atendimento humano",
];

const DEFAULT_CLOSE_KEYWORDS = ["encerrar", "encerrar atendimento", "tchau", "fim"];
const DEFAULT_CLOSE_MESSAGE =
  "Atendimento encerrado. Quando precisar de algo, é só me chamar!";

function HandoffTab({ integration, onSaved }: { integration: Integration; onSaved: () => void }) {
  const cfg = integration.handoff_config || {};
  const [enabled, setEnabled] = useState<boolean>(!!cfg.enabled);
  const [baseUrl, setBaseUrl] = useState<string>(cfg.base_url ?? "");
  const [token, setToken] = useState<string>(cfg.token ?? "");
  const [queueId, setQueueId] = useState<string>(
    cfg.queue_id != null ? String(cfg.queue_id) : ""
  );
  const [transferMessage, setTransferMessage] = useState<string>(
    cfg.transfer_message ?? "Vou te transferir para um de nossos atendentes agora. Um momento, por favor."
  );
  const [keywordsText, setKeywordsText] = useState<string>(
    (cfg.trigger_keywords && cfg.trigger_keywords.length
      ? cfg.trigger_keywords
      : DEFAULT_TRIGGER_KEYWORDS
    ).join(", ")
  );
  const [postHandoffOrder, setPostHandoffOrder] = useState<"summary_first" | "offers_first">(
    cfg.post_handoff_order === "offers_first" ? "offers_first" : "summary_first"
  );

  // ── Pausa da IA após handoff / resposta humana ────────────────────────────
  const [pauseMin, setPauseMin] = useState<number>(integration.handoff_pause_minutes ?? 240);

  // ── Detecção de resposta humana (pausa a IA quando o atendente responde) ──
  const hhd = integration.human_handoff_detection || {};
  const [hhdEnabled, setHhdEnabled] = useState<boolean>(!!hhd.enabled);
  const [hhdOutPath, setHhdOutPath] = useState<string>(hhd.outbound_match?.path ?? "$.fromMe");
  const [hhdOutEquals, setHhdOutEquals] = useState<string>(
    hhd.outbound_match?.equals != null ? String(hhd.outbound_match.equals) : "true"
  );
  const [hhdPhonePath, setHhdPhonePath] = useState<string>(hhd.customer_phone_path ?? "$.to");

  // ── Detecção de fechamento de ticket externo (event-driven resume) ────────
  const tld = integration.ticket_lifecycle_detection || {};
  const [tldEnabled, setTldEnabled] = useState<boolean>(!!tld.enabled);
  const [tldClosePath, setTldClosePath] = useState<string>(tld.close_match?.path ?? "$.event");
  const [tldCloseEquals, setTldCloseEquals] = useState<string>(
    tld.close_match?.equals != null ? String(tld.close_match.equals) : "ticket.closed"
  );
  const [tldOpenPath, setTldOpenPath] = useState<string>(tld.open_match?.path ?? "");
  const [tldOpenEquals, setTldOpenEquals] = useState<string>(
    tld.open_match?.equals != null ? String(tld.open_match.equals) : ""
  );
  const [tldPhonePath, setTldPhonePath] = useState<string>(tld.customer_phone_path ?? "$.contact.phone");
  const [tldFallbackMin, setTldFallbackMin] = useState<number>(
    tld.fallback_minutes != null ? Number(tld.fallback_minutes) : 480
  );

  // ── Escuta dedicada para eventos de ticket (lifecycle) ───────────────────
  // Polla broker_raw_events à procura do PRÓXIMO evento que chegar no /hooks
  // (qualquer direction), extrai os paths via discoverPaths e pré-preenche os
  // campos com palpites razoáveis. O tenant clica em "Ativar" e dispara um
  // ticket de teste na plataforma de atendimento (abrir/fechar) — a UI captura.
  const [tldListening, setTldListening] = useState(false);
  const [tldListenMsg, setTldListenMsg] = useState<string>("");
  const [tldDetectedPaths, setTldDetectedPaths] = useState<DiscoveredPath[]>([]);

  async function startTicketListening() {
    const startedAt = new Date();
    setTldListening(true);
    setTldListenMsg(
      "Aguardando... abra ou feche um ticket de teste na sua plataforma agora. (até 5 min)"
    );
    setTldDetectedPaths([]);

    let found = false;
    for (let attempt = 0; attempt < 150 && !found; attempt++) {
      try {
        const events = await listRawEvents(undefined, 20);
        const fresh = events.find(
          (e) =>
            e.integration_slug === integration.slug &&
            new Date(e.created_at) > startedAt
        );
        if (fresh) {
          const full = await getRawEvent(fresh.id);
          if (full.payload) {
            const paths = await discoverPaths(full.payload);
            setTldDetectedPaths(paths);
            // Heurística: procurar um path cujo valor de exemplo contenha
            // "ticket" / "close" / "open" — sugere de imediato.
            const pickByKeyword = (kw: string) =>
              paths.find(
                (p) =>
                  /string/i.test(p.type) &&
                  String(p.sample ?? "").toLowerCase().includes(kw)
              );
            const eventTypePath = paths.find((p) =>
              /(\.event(Type)?|\.type|\.kind|\.action)$/i.test(p.path)
            );
            if (eventTypePath) {
              setTldClosePath(eventTypePath.path);
              const sample = String(eventTypePath.sample ?? "").toLowerCase();
              if (sample.includes("clos") || sample.includes("end")
                  || sample.includes("fech")) {
                setTldCloseEquals(String(eventTypePath.sample));
              } else if (sample.includes("open") || sample.includes("abert")
                         || sample.includes("start") || sample.includes("creat")) {
                setTldOpenPath(eventTypePath.path);
                setTldOpenEquals(String(eventTypePath.sample));
              }
            } else {
              const closeHint = pickByKeyword("clos") || pickByKeyword("fech");
              if (closeHint) {
                setTldClosePath(closeHint.path);
                setTldCloseEquals(String(closeHint.sample));
              }
            }
            // Telefone do cliente: primeiro path que parece telefone.
            const phoneHint = paths.find(
              (p) =>
                /(phone|telefone|whatsapp|contact|number|msisdn)/i.test(p.path)
            );
            if (phoneHint) setTldPhonePath(phoneHint.path);

            setTldListenMsg(
              `✓ Evento capturado! ${paths.length} campo(s) detectado(s). ` +
              `Confira os palpites abaixo e ajuste se necessário.`
            );
            found = true;
          }
        }
      } catch (e) {
        console.error("[broker] Ticket lifecycle poll error:", e);
      }
      if (!found) await new Promise((r) => setTimeout(r, 2000));
    }
    if (!found) {
      setTldListenMsg(
        "⏱ Timeout. Nenhum evento recebido em 5 min. Verifique se a plataforma " +
        "está apontando para a URL do /hooks deste integrador."
      );
    }
    setTldListening(false);
  }

  // Sugestões de paths a partir dos últimos eventos reais (raw_events).
  // Evita o tenant ter que decorar "$.fromMe"/"$.to" — ele clica e escolhe.
  const [discovered, setDiscovered] = useState<DiscoveredFieldFromHistory[]>([]);
  const [discMeta, setDiscMeta] = useState<{
    event_count: number; inbound_count: number; outbound_count: number;
  } | null>(null);
  const [discBusy, setDiscBusy] = useState(false);
  const [discErr, setDiscErr] = useState<string>("");

  async function loadSuggestions() {
    setDiscBusy(true); setDiscErr("");
    try {
      const r = await discoverFieldsFromHistory(integration.id, 30);
      setDiscovered(r.paths);
      setDiscMeta({
        event_count: r.event_count,
        inbound_count: r.inbound_count,
        outbound_count: r.outbound_count,
      });
    } catch (e: any) {
      setDiscErr(e?.response?.data?.detail ?? e.message ?? "Falha ao buscar eventos.");
    } finally {
      setDiscBusy(false);
    }
  }

  // Heurística: pra "valor que indica saída", as opções vêm dos samples do path
  // selecionado em `hhdOutPath`. Pra "telefone do cliente", priorizamos paths
  // do tipo string/number que aparecem em eventos OUTBOUND.
  const selectedOutPathInfo = discovered.find((p) => p.path === hhdOutPath);
  const outboundOnlyPaths = discovered.filter((p) => p.directions.includes("outbound"));
  const phonePathCandidates = (outboundOnlyPaths.length ? outboundOnlyPaths : discovered)
    .filter((p) => p.type === "string" || p.type === "int");

  // ── Encerrar atendimento (session_config) ─────────────────────────────────
  const sessCfg = integration.session_config || {};
  const [closeKeywordsText, setCloseKeywordsText] = useState<string>(
    (sessCfg.close_keywords && sessCfg.close_keywords.length
      ? sessCfg.close_keywords
      : DEFAULT_CLOSE_KEYWORDS
    ).join(", ")
  );
  const [closeMessage, setCloseMessage] = useState<string>(
    sessCfg.close_message ?? DEFAULT_CLOSE_MESSAGE
  );

  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string>("");

  // Teste de transferência
  const [testPhone, setTestPhone] = useState<string>("");
  const [testMsg, setTestMsg] = useState<string>("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);

  useEffect(() => {
    const c = integration.handoff_config || {};
    setEnabled(!!c.enabled);
    setBaseUrl(c.base_url ?? "");
    setToken(c.token ?? "");
    setQueueId(c.queue_id != null ? String(c.queue_id) : "");
    setTransferMessage(c.transfer_message ?? "Vou te transferir para um de nossos atendentes agora. Um momento, por favor.");
    setKeywordsText((c.trigger_keywords && c.trigger_keywords.length
      ? c.trigger_keywords
      : DEFAULT_TRIGGER_KEYWORDS
    ).join(", "));
    setPostHandoffOrder(c.post_handoff_order === "offers_first" ? "offers_first" : "summary_first");
    setPauseMin(integration.handoff_pause_minutes ?? 240);
    const h = integration.human_handoff_detection || {};
    setHhdEnabled(!!h.enabled);
    setHhdOutPath(h.outbound_match?.path ?? "$.fromMe");
    setHhdOutEquals(h.outbound_match?.equals != null ? String(h.outbound_match.equals) : "true");
    setHhdPhonePath(h.customer_phone_path ?? "$.to");
    const t = integration.ticket_lifecycle_detection || {};
    setTldEnabled(!!t.enabled);
    setTldClosePath(t.close_match?.path ?? "$.event");
    setTldCloseEquals(t.close_match?.equals != null ? String(t.close_match.equals) : "ticket.closed");
    setTldOpenPath(t.open_match?.path ?? "");
    setTldOpenEquals(t.open_match?.equals != null ? String(t.open_match.equals) : "");
    setTldPhonePath(t.customer_phone_path ?? "$.contact.phone");
    setTldFallbackMin(t.fallback_minutes != null ? Number(t.fallback_minutes) : 480);
    const s = integration.session_config || {};
    setCloseKeywordsText((s.close_keywords && s.close_keywords.length
      ? s.close_keywords
      : DEFAULT_CLOSE_KEYWORDS
    ).join(", "));
    setCloseMessage(s.close_message ?? DEFAULT_CLOSE_MESSAGE);
    setTestResult(null);
    setSaveMsg("");
  }, [integration.id]);

  function parseKeywords(): string[] {
    return keywordsText.split(",").map(s => s.trim()).filter(Boolean);
  }

  function parseCloseKeywords(): string[] {
    return closeKeywordsText.split(",").map(s => s.trim()).filter(Boolean);
  }

  async function save() {
    setSaving(true); setSaveMsg("");
    const handoff_config: HandoffConfig = {
      enabled,
      provider: "clickmassa",
      base_url: baseUrl.trim(),
      token: token.trim(),
      queue_id: queueId.trim() ? Number(queueId.trim()) : undefined,
      transfer_message: transferMessage.trim(),
      trigger_keywords: parseKeywords(),
      post_handoff_order: postHandoffOrder,
    };
    // Coerção do valor esperado no match de saída: "true"/"false"/número/string
    const equalsRaw = hhdOutEquals.trim();
    let equalsCoerced: unknown = equalsRaw;
    if (equalsRaw === "true") equalsCoerced = true;
    else if (equalsRaw === "false") equalsCoerced = false;
    else if (equalsRaw !== "" && !isNaN(Number(equalsRaw))) equalsCoerced = Number(equalsRaw);
    const human_handoff_detection = {
      enabled: hhdEnabled,
      outbound_match: { path: hhdOutPath.trim(), equals: equalsCoerced },
      customer_phone_path: hhdPhonePath.trim(),
    };
    // Mesma coerção para os matches do ciclo de vida do ticket
    const coerceMatchValue = (raw: string): unknown => {
      const v = raw.trim();
      if (v === "true") return true;
      if (v === "false") return false;
      if (v !== "" && !isNaN(Number(v))) return Number(v);
      return v;
    };
    const ticket_lifecycle_detection: Record<string, unknown> = {
      enabled: tldEnabled,
      close_match: {
        path: tldClosePath.trim(),
        equals: coerceMatchValue(tldCloseEquals),
      },
      customer_phone_path: tldPhonePath.trim(),
      fallback_minutes: Number.isFinite(tldFallbackMin) ? tldFallbackMin : 0,
    };
    if (tldOpenPath.trim() && tldOpenEquals.trim()) {
      ticket_lifecycle_detection.open_match = {
        path: tldOpenPath.trim(),
        equals: coerceMatchValue(tldOpenEquals),
      };
    }
    try {
      // Reaproveita o saveFlow — passa os demais campos da integração sem alteração
      await saveFlow(integration.id, {
        inbound_field_map: integration.inbound_field_map || {},
        reply_mode: integration.reply_mode,
        reply_url: integration.reply_url,
        reply_method: integration.reply_method,
        reply_headers: integration.reply_headers || {},
        reply_body_template: integration.reply_body_template || {},
        reply_status_code: integration.reply_status_code,
        bundle_enabled: integration.bundle_enabled,
        bundle_window_seconds: integration.bundle_window_seconds,
        skip_rules: integration.skip_rules || [],
        handoff_config,
        session_config: {
          close_keywords: parseCloseKeywords(),
          close_message: closeMessage.trim() || DEFAULT_CLOSE_MESSAGE,
        },
        handoff_pause_minutes: pauseMin,
        human_handoff_detection,
        ticket_lifecycle_detection,
      });
      setSaveMsg("✓ Configuração de transferência salva!");
      onSaved();
    } catch (e: any) {
      setSaveMsg("✗ Erro: " + (e?.response?.data?.detail ?? e.message));
    } finally {
      setSaving(false);
    }
  }

  async function runTest() {
    setTesting(true); setTestResult(null);
    try {
      const result = await testHandoff(integration.id, testPhone, testMsg || undefined);
      setTestResult(result);
    } catch (e: any) {
      setTestResult({
        ok: false,
        error: e?.response?.data?.detail ?? e.message,
        status_code: null,
        response: null,
      });
    } finally {
      setTesting(false);
    }
  }

  const exampleUrl = baseUrl && token
    ? `${baseUrl.replace(/\/+$/, "")}/?token=${token.slice(0, 16)}...`
    : "https://chatapi.talkfarma.pro/v1/api/external/<UUID>/?token=<JWT>";

  return (
    <div className="broker-mapper">
      {/* Bloco 1: ativar + provider */}
      <div className="broker-card">
        <h3 className="broker-card-title">Transferir conversa para atendente humano</h3>
        <p className="broker-card-sub">
          Quando o agente decidir escalar (emergência, fora de escopo) ou o cliente
          pedir explicitamente ("quero falar com atendente"), nós paramos de responder
          e disparamos a transferência para o seu sistema de atendimento (PDV/CRM).
          Hoje suportamos a integração com <strong>ClickMassa / TalkFarma</strong>.
        </p>

        <label className="agent-field" style={{ cursor: "pointer" }}>
          <div className="agent-field-icon">🤝</div>
          <div className="agent-field-info">
            <div className="agent-field-label">Ativar transferência para o balcão</div>
            <div className="agent-field-desc">
              Sem isso, o agente continua respondendo mesmo em casos de escalonamento.
            </div>
          </div>
          <div className="agent-field-mapping">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              style={{ width: 22, height: 22, cursor: "pointer" }}
            />
          </div>
        </label>
      </div>

      {/* Bloco 2: credenciais */}
      <div className="broker-card">
        <h3 className="broker-card-title">Credenciais do ClickMassa / TalkFarma</h3>
        <p className="broker-card-sub">
          Cole abaixo a URL externa <strong>sem</strong> o token, e o token JWT no campo separado.
          Nós montamos a URL final: <code style={{ fontSize: 11 }}>{exampleUrl}</code>
        </p>

        <label className="broker-field">
          <span>URL base (sem token)</span>
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://chatapi.talkfarma.pro/v1/api/external/828c8f53-4a31-461d-bac7-124411779618"
          />
        </label>

        <label className="broker-field">
          <span>Token JWT</span>
          <input
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
            style={{ fontFamily: "monospace", fontSize: 12 }}
          />
        </label>

        <label className="broker-field">
          <span>Número do departamento (queueId)</span>
          <input
            type="number"
            value={queueId}
            onChange={(e) => setQueueId(e.target.value)}
            placeholder="Ex: 4"
            style={{ maxWidth: 200 }}
          />
          <small style={{ color: "#86868b", fontSize: 12 }}>
            ID da fila/setor no ClickMassa pra onde o ticket será encaminhado.
          </small>
        </label>
      </div>

      {/* Bloco 3: mensagem + keywords */}
      <div className="broker-card">
        <h3 className="broker-card-title">Comportamento da transferência</h3>

        <label className="broker-field">
          <span>Mensagem mostrada ao cliente no momento da transferência</span>
          <textarea
            value={transferMessage}
            onChange={(e) => setTransferMessage(e.target.value)}
            rows={3}
            placeholder="Vou te transferir para um de nossos atendentes agora. Um momento, por favor."
            style={{
              width: "100%", padding: "8px 10px",
              border: "1px solid var(--color-border, #e5e7eb)",
              borderRadius: 6, fontSize: 14, fontFamily: "inherit",
              resize: "vertical",
            }}
          />
          <small style={{ color: "#86868b", fontSize: 12 }}>
            Esta mensagem é enviada como <code>body</code> no POST para o ClickMassa
            e também aparece na conversa do WhatsApp.
          </small>
        </label>

        <label className="broker-field">
          <span>Palavras-chave que disparam a transferência automaticamente</span>
          <input
            value={keywordsText}
            onChange={(e) => setKeywordsText(e.target.value)}
            placeholder="atendente, humano, balcão"
          />
          <small style={{ color: "#86868b", fontSize: 12 }}>
            Lista separada por vírgula. Se a mensagem do cliente contiver qualquer
            uma destas palavras (case-insensitive), a transferência é disparada antes
            mesmo do agente responder. Deixe vazio para depender apenas do escalate
            do agente.
          </small>
        </label>

        <label className="broker-field">
          <span>Ordem das mensagens enviadas após a transferência</span>
          <select
            value={postHandoffOrder}
            onChange={(e) => setPostHandoffOrder(e.target.value as "summary_first" | "offers_first")}
          >
            <option value="summary_first">Resumo do pedido primeiro, depois as ofertas</option>
            <option value="offers_first">Ofertas primeiro, depois o resumo do pedido</option>
          </select>
          <small style={{ color: "#86868b", fontSize: 12 }}>
            Após a transferência ao balcão, o cliente recebe o resumo do pedido e as
            ofertas vigentes em mensagens separadas. Escolha qual vem primeiro. Cada
            bloco só é enviado se a respectiva funcionalidade estiver ativa.
          </small>
        </label>

        <label className="broker-field">
          <span>Tempo que a IA fica pausada após a transferência / resposta humana (minutos)</span>
          <input
            type="number"
            min={0}
            max={10080}
            value={pauseMin}
            onChange={(e) => setPauseMin(Math.max(0, Math.min(10080, parseInt(e.target.value || "0", 10) || 0)))}
          />
          <small style={{ color: "#86868b", fontSize: 12 }}>
            Depois de transferir ao atendente (ou quando um humano responde, se a
            detecção abaixo estiver ligada), o robô fica em silêncio por este tempo
            (padrão 240 = 4h). Quando o cliente volta a falar após a janela, a IA
            reassume. Use <strong>0</strong> para a IA não pausar.
          </small>
        </label>
      </div>

      {/* Bloco 3c: detecção de resposta humana → pausa automática da IA */}
      <div className="broker-card">
        <h3 className="broker-card-title">Pausar a IA quando o atendente responder</h3>
        <p className="broker-card-sub">
          Em gateways que reenviam as mensagens de <strong>saída</strong> (TalkFarma /
          ClickMassa / WAHA / Evolution), conseguimos detectar quando um atendente
          humano responde pelo WhatsApp e pausar a IA automaticamente (janela rolante:
          cada resposta do humano renova o tempo de pausa configurado acima). As
          respostas do próprio robô são reconhecidas e <strong>não</strong> pausam a IA.
        </p>

        <label className="broker-field" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="checkbox"
            checked={hhdEnabled}
            onChange={(e) => setHhdEnabled(e.target.checked)}
          />
          <span>Ativar detecção de resposta humana neste gateway</span>
        </label>

        {hhdEnabled && (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "8px 0 12px" }}>
              <button
                type="button"
                onClick={loadSuggestions}
                disabled={discBusy}
                style={{
                  padding: "6px 12px", borderRadius: 6, border: "1px solid #d1d5db",
                  background: discBusy ? "#f3f4f6" : "white", cursor: discBusy ? "wait" : "pointer",
                  fontSize: 13,
                }}
                title="Lê os últimos eventos REAIS recebidos no /hooks e sugere os campos disponíveis"
              >
                {discBusy ? "Buscando..." : "🔎 Sugerir a partir de eventos reais"}
              </button>
              {discMeta && (
                <small style={{ color: "#86868b", fontSize: 12 }}>
                  Analisados {discMeta.event_count} evento(s)
                  {" "}({discMeta.inbound_count} entrada, {discMeta.outbound_count} saída).
                  {discMeta.outbound_count === 0 && (
                    <> Nenhum evento de saída ainda — envie 1 msg pelo WhatsApp e tente de novo.</>
                  )}
                </small>
              )}
              {discErr && <small style={{ color: "#dc2626" }}>{discErr}</small>}
            </div>

            <datalist id={`hhd-out-path-${integration.id}`}>
              {discovered.map((p) => (
                <option
                  key={p.path}
                  value={p.path}
                  label={`${p.type} · ex.: ${(p.samples[0] ?? "").toString().slice(0, 40)}`}
                />
              ))}
            </datalist>
            <datalist id={`hhd-out-equals-${integration.id}`}>
              {(selectedOutPathInfo?.samples ?? []).map((s, i) => (
                <option key={i} value={String(s)} />
              ))}
            </datalist>
            <datalist id={`hhd-phone-path-${integration.id}`}>
              {phonePathCandidates.map((p) => (
                <option
                  key={p.path}
                  value={p.path}
                  label={`${p.type} · ex.: ${(p.samples[0] ?? "").toString().slice(0, 40)}`}
                />
              ))}
            </datalist>

            <label className="broker-field">
              <span>Campo que marca mensagem de saída (path)</span>
              <input
                list={`hhd-out-path-${integration.id}`}
                value={hhdOutPath}
                onChange={(e) => setHhdOutPath(e.target.value)}
                placeholder="$.fromMe"
              />
              {discovered.length > 0 && (
                <small style={{ color: "#86868b", fontSize: 12 }}>
                  Clique no campo para ver os caminhos detectados nos eventos reais.
                </small>
              )}
            </label>
            <label className="broker-field">
              <span>Valor que indica saída</span>
              <input
                list={`hhd-out-equals-${integration.id}`}
                value={hhdOutEquals}
                onChange={(e) => setHhdOutEquals(e.target.value)}
                placeholder="true"
              />
              <small style={{ color: "#86868b", fontSize: 12 }}>
                Ex.: <code>$.fromMe</code> = <code>true</code>, ou
                {" "}<code>$.message.direction</code> = <code>out</code>.
                {selectedOutPathInfo && selectedOutPathInfo.samples.length > 0 && (
                  <> Valores vistos para <code>{hhdOutPath}</code>:{" "}
                    {selectedOutPathInfo.samples.map((s) => String(s)).join(", ")}.</>
                )}
              </small>
            </label>
            <label className="broker-field">
              <span>Caminho do telefone do CLIENTE na mensagem de saída (path)</span>
              <input
                list={`hhd-phone-path-${integration.id}`}
                value={hhdPhonePath}
                onChange={(e) => setHhdPhonePath(e.target.value)}
                placeholder="$.to"
              />
              <small style={{ color: "#86868b", fontSize: 12 }}>
                Na saída, o telefone do cliente é o <strong>destinatário</strong> (não o
                número do bot). Ex.: <code>$.to</code>, <code>$.message.to</code>.
              </small>
            </label>
          </>
        )}
      </div>

      {/* Bloco 3d: detecção de fechamento de ticket externo (event-driven) */}
      <div className="broker-card">
        <h3 className="broker-card-title">Pausar a IA até o ticket fechar na plataforma</h3>
        <p className="broker-card-sub">
          Quando o seu sistema de multiatendimento envia webhooks de
          {" "}<strong>ticket aberto</strong> e <strong>ticket fechado</strong>{" "}
          no mesmo endpoint <code>/hooks</code>, podemos usar o evento de fechamento
          como gatilho para a IA voltar — em vez de um timer. Isso evita que a IA
          retome o atendimento no meio de uma conversa humana, mesmo que demore horas.
        </p>

        <label className="broker-field" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="checkbox"
            checked={tldEnabled}
            onChange={(e) => setTldEnabled(e.target.checked)}
          />
          <span>Ativar — IA só volta após o evento de fechamento</span>
        </label>

        {tldEnabled && (
          <>
            <small style={{ color: "#92400e", fontSize: 12, display: "block", margin: "8px 0" }}>
              ⚠️ Ao ligar isso, o tempo de pausa em minutos acima vira apenas{" "}
              <strong>safety net</strong> (caso o sistema externo esqueça de mandar o
              evento). A fonte da verdade passa a ser o ticket na sua plataforma.
            </small>

            {/* Escuta de eventos de ticket — descobre paths automaticamente */}
            <div style={{
              background: "#f8fafc", border: "1px solid #e5e7eb",
              borderRadius: 8, padding: 12, margin: "8px 0 16px",
            }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
                Não sabe quais são os campos do payload?
              </div>
              <p style={{ fontSize: 12, color: "#475569", marginTop: 0, marginBottom: 8 }}>
                Ative a escuta abaixo e, na sua plataforma de atendimento,{" "}
                <strong>abra ou feche 1 ticket de teste</strong>. Vamos capturar o
                webhook, sugerir os caminhos certos e pré-preencher os campos.
              </p>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <button
                  type="button"
                  className={`broker-primary ${tldListening ? "is-listening" : ""}`}
                  onClick={startTicketListening}
                  disabled={tldListening}
                  style={{ minWidth: 220 }}
                >
                  {tldListening
                    ? "🔴 Aguardando evento de ticket..."
                    : "▶ Ativar escuta (5 min)"}
                </button>
                <button
                  type="button"
                  onClick={loadSuggestions}
                  disabled={discBusy}
                  style={{
                    padding: "6px 12px", borderRadius: 6, border: "1px solid #d1d5db",
                    background: discBusy ? "#f3f4f6" : "white",
                    cursor: discBusy ? "wait" : "pointer", fontSize: 13,
                  }}
                  title="Lê os ÚLTIMOS eventos já recebidos no /hooks (sem precisar mandar de novo)"
                >
                  {discBusy ? "Buscando..." : "🔎 Usar eventos já recebidos"}
                </button>
              </div>
              {tldListenMsg && (
                <small style={{
                  display: "block", marginTop: 8, fontSize: 12,
                  color: tldListenMsg.startsWith("✓") ? "#2e7d32"
                       : tldListenMsg.startsWith("⏱") ? "#c62828" : "#475569",
                }}>
                  {tldListenMsg}
                </small>
              )}
              {!tldListenMsg && discMeta && (
                <small style={{ display: "block", marginTop: 8, fontSize: 12, color: "#475569" }}>
                  Analisados {discMeta.event_count} evento(s) do histórico
                  ({discMeta.inbound_count} entrada, {discMeta.outbound_count} saída).
                </small>
              )}
              {tldDetectedPaths.length > 0 && (
                <details style={{ marginTop: 8 }}>
                  <summary style={{ cursor: "pointer", fontSize: 12, color: "#475569" }}>
                    Ver todos os {tldDetectedPaths.length} campos detectados no último evento
                  </summary>
                  <div style={{
                    display: "flex", flexWrap: "wrap", gap: 4, marginTop: 6,
                    maxHeight: 160, overflowY: "auto",
                  }}>
                    {tldDetectedPaths.map((p) => (
                      <code key={p.path} style={{
                        background: "white", border: "1px solid #e5e7eb",
                        borderRadius: 4, padding: "2px 6px", fontSize: 11,
                      }} title={`Tipo: ${p.type} · Exemplo: ${p.sample}`}>
                        {p.path}
                      </code>
                    ))}
                  </div>
                </details>
              )}
            </div>

            {/* Datalists alimentados pelas duas fontes (escuta dedicada + histórico) */}
            <datalist id={`tld-path-${integration.id}`}>
              {tldDetectedPaths.map((p) => (
                <option
                  key={`d-${p.path}`}
                  value={p.path}
                  label={`${p.type} · ex.: ${String(p.sample ?? "").slice(0, 40)}`}
                />
              ))}
              {discovered.map((p) => (
                <option
                  key={`h-${p.path}`}
                  value={p.path}
                  label={`${p.type} · ex.: ${(p.samples[0] ?? "").toString().slice(0, 40)}`}
                />
              ))}
            </datalist>
            <datalist id={`tld-close-equals-${integration.id}`}>
              {tldDetectedPaths
                .filter((p) => p.path === tldClosePath)
                .map((p, i) => (
                  <option key={i} value={String(p.sample)} />
                ))}
              {(discovered.find((p) => p.path === tldClosePath)?.samples ?? []).map((s, i) => (
                <option key={`h-${i}`} value={String(s)} />
              ))}
            </datalist>
            <datalist id={`tld-open-equals-${integration.id}`}>
              {tldDetectedPaths
                .filter((p) => p.path === tldOpenPath)
                .map((p, i) => (
                  <option key={i} value={String(p.sample)} />
                ))}
              {(discovered.find((p) => p.path === tldOpenPath)?.samples ?? []).map((s, i) => (
                <option key={`h-${i}`} value={String(s)} />
              ))}
            </datalist>

            <label className="broker-field">
              <span>Caminho do tipo do evento (path)</span>
              <input
                list={`tld-path-${integration.id}`}
                value={tldClosePath}
                onChange={(e) => setTldClosePath(e.target.value)}
                placeholder="$.event"
              />
              <small style={{ color: "#86868b", fontSize: 12 }}>
                Onde, no payload do webhook, está o nome do evento. Clique no campo
                para ver as sugestões detectadas. Ex.: <code>$.event</code>,{" "}
                <code>$.type</code>, <code>$.eventType</code>.
              </small>
            </label>
            <label className="broker-field">
              <span>Valor que indica TICKET FECHADO</span>
              <input
                list={`tld-close-equals-${integration.id}`}
                value={tldCloseEquals}
                onChange={(e) => setTldCloseEquals(e.target.value)}
                placeholder="ticket.closed"
              />
              <small style={{ color: "#86868b", fontSize: 12 }}>
                Quando o campo acima for igual a este valor, a IA é liberada e o
                histórico zerado para o próximo atendimento começar do zero.
              </small>
            </label>
            <label className="broker-field">
              <span>Caminho do telefone do cliente no payload do evento</span>
              <input
                list={`tld-path-${integration.id}`}
                value={tldPhonePath}
                onChange={(e) => setTldPhonePath(e.target.value)}
                placeholder="$.contact.phone"
              />
              <small style={{ color: "#86868b", fontSize: 12 }}>
                Geralmente está em <code>$.contact.phone</code>, <code>$.customer.phone</code>{" "}
                ou <code>$.ticket.contact.number</code>.
              </small>
            </label>

            <details style={{ marginTop: 8 }}>
              <summary style={{ cursor: "pointer", fontSize: 13 }}>
                Opcional: também pausar a IA quando o ticket é aberto na plataforma
              </summary>
              <div style={{ marginTop: 8, paddingLeft: 8, borderLeft: "2px solid #e5e7eb" }}>
                <p style={{ fontSize: 12, color: "#86868b", marginTop: 0 }}>
                  Útil quando o atendente abre o ticket antes mesmo de o bot mandar
                  para o handoff (ex.: cliente cai direto na fila). Deixe em branco
                  para ignorar.
                </p>
                <label className="broker-field">
                  <span>Caminho do tipo do evento (path)</span>
                  <input
                    list={`tld-path-${integration.id}`}
                    value={tldOpenPath}
                    onChange={(e) => setTldOpenPath(e.target.value)}
                    placeholder="$.event"
                  />
                </label>
                <label className="broker-field">
                  <span>Valor que indica TICKET ABERTO</span>
                  <input
                    list={`tld-open-equals-${integration.id}`}
                    value={tldOpenEquals}
                    onChange={(e) => setTldOpenEquals(e.target.value)}
                    placeholder="ticket.opened"
                  />
                </label>
              </div>
            </details>

            <label className="broker-field">
              <span>Safety net — pausa máxima em minutos (0 = nunca expira)</span>
              <input
                type="number"
                min={0}
                max={43200}
                value={tldFallbackMin}
                onChange={(e) => setTldFallbackMin(Number(e.target.value) || 0)}
              />
              <small style={{ color: "#86868b", fontSize: 12 }}>
                Se o evento de fechamento não chegar nesse tempo, a IA reassume
                sozinha (útil para não deixar cliente esperando se a plataforma
                falhar). <strong>0</strong> = pausa indefinida até o evento chegar.
              </small>
            </label>
          </>
        )}
      </div>

      {/* Bloco 3b: encerrar atendimento via palavra-chave do cliente */}
      <div className="broker-card">
        <h3 className="broker-card-title">Encerrar atendimento</h3>
        <p className="broker-card-sub">
          Quando o cliente enviar uma das palavras-chave abaixo, a sessão é
          encerrada e o histórico é zerado. O próximo contato dele começa um
          atendimento novo, do zero. <strong>Também acontece automaticamente
          depois de uma transferência:</strong> quando o cliente voltar a
          falar (após a janela de pausa do balcão), o agente abre uma nova
          conversa em vez de continuar a anterior.
        </p>

        <label className="broker-field">
          <span>Palavras-chave que encerram o atendimento</span>
          <input
            value={closeKeywordsText}
            onChange={(e) => setCloseKeywordsText(e.target.value)}
            placeholder="encerrar, tchau, fim"
          />
          <small style={{ color: "#86868b", fontSize: 12 }}>
            Lista separada por vírgula. Comparação exata (case-insensitive,
            sem acentos) — evita acionar dentro de frases longas. Ex: "fim"
            casa só com a mensagem "fim", não com "perfil".
          </small>
        </label>

        <label className="broker-field">
          <span>Mensagem de confirmação enviada ao cliente</span>
          <textarea
            value={closeMessage}
            onChange={(e) => setCloseMessage(e.target.value)}
            rows={2}
            placeholder={DEFAULT_CLOSE_MESSAGE}
            style={{
              width: "100%", padding: "8px 10px",
              border: "1px solid var(--color-border, #e5e7eb)",
              borderRadius: 6, fontSize: 14, fontFamily: "inherit",
              resize: "vertical",
            }}
          />
        </label>
      </div>

      {/* Save */}
      <div className="broker-card">
        <div className="broker-actions" style={{ justifyContent: "space-between" }}>
          <div style={{
            fontSize: 13,
            color: saveMsg.includes("✓") ? "#2e7d32" : saveMsg.includes("✗") ? "#c62828" : "#86868b",
          }}>
            {saveMsg || (enabled
              ? "Confira as credenciais antes de salvar."
              : "Transferência desativada — o agente sempre responderá.")}
          </div>
          <button className="broker-primary" onClick={save} disabled={saving}>
            {saving ? "Salvando..." : "Salvar configuração"}
          </button>
        </div>
      </div>

      {/* Bloco 4: testar */}
      {enabled && (
        <div className="broker-card" style={{ borderLeft: "4px solid #0a84ff" }}>
          <h3 className="broker-card-title">🧪 Testar transferência</h3>
          <p className="broker-card-sub">
            Salva a configuração e dispara um POST de teste para o ClickMassa
            usando os dados acima. Útil para validar token, queueId e URL antes
            de receber mensagens reais.
          </p>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr auto", gap: 8, alignItems: "end" }}>
            <label className="broker-field" style={{ marginBottom: 0 }}>
              <span>Telefone de teste</span>
              <input
                value={testPhone}
                onChange={(e) => setTestPhone(e.target.value)}
                placeholder="5511999998888"
              />
            </label>
            <label className="broker-field" style={{ marginBottom: 0 }}>
              <span>Mensagem (opcional — usa a configurada se vazio)</span>
              <input
                value={testMsg}
                onChange={(e) => setTestMsg(e.target.value)}
                placeholder="Mensagem customizada de teste"
              />
            </label>
            <button
              className="broker-primary"
              onClick={runTest}
              disabled={!testPhone || testing}
              style={{ height: 38 }}
            >
              {testing ? "Enviando..." : "Disparar teste"}
            </button>
          </div>

          {testResult && (
            <div style={{
              marginTop: 14, padding: 12, borderRadius: 8,
              background: testResult.ok ? "#e8f5e9" : "#ffebee",
              border: `1px solid ${testResult.ok ? "#81c784" : "#ef9a9a"}`,
              fontSize: 13,
            }}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>
                {testResult.ok ? "✓ Transferência aceita pelo ClickMassa" : "✗ Falha na transferência"}
              </div>
              {testResult.status_code != null && (
                <div>Status HTTP: <code>{testResult.status_code}</code></div>
              )}
              {testResult.error && (
                <div style={{ color: "#c62828" }}>Erro: {testResult.error}</div>
              )}
              {testResult.response && (
                <pre style={{
                  marginTop: 8, background: "#0d0d0e", color: "#a5d6a7",
                  padding: 10, borderRadius: 6, fontSize: 11,
                  maxHeight: 200, overflow: "auto",
                }}>{JSON.stringify(testResult.response, null, 2)}</pre>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// ── Notify (order-status) ────────────────────────────────────────────────────

function NotifyTab({ integration, onSaved }: { integration: Integration; onSaved: () => void }) {
  const cfg = integration.config_json || {};
  const handoff = integration.handoff_config || {};

  const [enabled, setEnabled]   = useState<boolean>(Boolean(cfg.notify_order_status));
  const [baseUrl, setBaseUrl]   = useState<string>(String(cfg.base_url || ""));
  const [token, setToken]       = useState<string>(String(cfg.token || ""));
  const [externalKey, setKey]   = useState<string>(String(cfg.external_key || "123456"));
  const [saving, setSaving]     = useState(false);
  const [msg, setMsg]           = useState<string>("");

  useEffect(() => {
    const c = integration.config_json || {};
    setEnabled(Boolean(c.notify_order_status));
    setBaseUrl(String(c.base_url || ""));
    setToken(String(c.token || ""));
    setKey(String(c.external_key || "123456"));
    setMsg("");
  }, [integration.id]);

  const effectiveBaseUrl = baseUrl.trim() || (handoff.base_url || "");
  const effectiveToken   = token.trim()   || (handoff.token   || "");
  const ready = enabled && Boolean(effectiveBaseUrl) && Boolean(effectiveToken);

  async function save() {
    setSaving(true); setMsg("");
    try {
      await updateIntegration(integration.id, {
        config_json: {
          ...(integration.config_json || {}),
          provider: "clickmassa",
          notify_order_status: enabled,
          base_url: baseUrl.trim() || undefined,
          token: token.trim() || undefined,
          external_key: externalKey.trim() || "123456",
        },
      });
      setMsg("✓ Configuração salva.");
      onSaved();
    } catch (e: any) {
      setMsg("✗ Erro: " + (e?.response?.data?.detail ?? e.message));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="broker-mapper">
      <div className="broker-card">
        <h3 className="broker-card-title">Notificação automática de status de pedido</h3>
        <p className="broker-card-sub">
          Quando o operador alterar o status de um pedido no painel
          (<em>confirmado</em>, <em>em preparo</em>, <em>enviado</em>, etc.) nós
          enviamos a mensagem definida em{" "}
          <strong>Configuração → Notificações de Status</strong> pelo endpoint
          desta integração, no mesmo padrão da ClickMassa:
          <br />
          <code>POST {`{base_url}/?token={token}`}</code> com body{" "}
          <code>{`{ number, externalKey, body }`}</code>.
        </p>

        <label className="agent-field" style={{ cursor: "pointer" }}>
          <div className="agent-field-icon">🔔</div>
          <div className="agent-field-info">
            <div className="agent-field-label">Ativar notificação de status</div>
            <div className="agent-field-desc">
              Sem isso, mudanças de status ficam só no painel — o cliente não é
              avisado por WhatsApp.
            </div>
          </div>
          <div className="agent-field-mapping">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              style={{ width: 22, height: 22, cursor: "pointer" }}
            />
          </div>
        </label>
      </div>

      <div className="broker-card">
        <h3 className="broker-card-title">Endpoint ClickMassa</h3>
        <p className="broker-card-sub">
          Se você já preencheu a URL e o token na aba <em>Transferir p/ atendente</em>,
          pode deixar estes campos vazios — vamos reutilizar.{" "}
          {handoff.base_url ? (
            <span style={{ color: "#16a34a" }}>
              Atualmente usando da aba de transferência: <code>{handoff.base_url}</code>
            </span>
          ) : (
            <span style={{ color: "#b45309" }}>
              ⚠ Nenhum endpoint configurado na aba de transferência. Preencha aqui.
            </span>
          )}
        </p>

        <div className="broker-field">
          <label className="broker-field-label">Base URL</label>
          <input
            className="broker-field-input"
            placeholder={handoff.base_url || "https://chatapi.talkfarma.pro/v1/api/external/<UUID>"}
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </div>

        <div className="broker-field">
          <label className="broker-field-label">Token (JWT)</label>
          <input
            className="broker-field-input"
            placeholder={handoff.token ? "•••••• (herdado da aba de transferência)" : "eyJhbGciOi..."}
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        </div>

        <div className="broker-field">
          <label className="broker-field-label">externalKey</label>
          <input
            className="broker-field-input"
            value={externalKey}
            onChange={(e) => setKey(e.target.value)}
          />
          <small style={{ color: "#6b7280" }}>
            Valor fixo enviado no body. Mantenha <code>123456</code> se não tem motivo pra mudar.
          </small>
        </div>
      </div>

      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <button className="broker-primary" disabled={saving} onClick={save}>
          {saving ? <Spinner size={14} /> : "Salvar configuração"}
        </button>
        {ready ? (
          <span style={{ color: "#16a34a", fontSize: 13 }}>● Pronto pra enviar</span>
        ) : (
          <span style={{ color: "#6b7280", fontSize: 13 }}>
            {enabled ? "Faltam URL/token (aqui ou na aba de transferência)" : "Desativado"}
          </span>
        )}
        {msg && <span style={{ marginLeft: "auto", fontSize: 13 }}>{msg}</span>}
      </div>
    </div>
  );
}


// ── Events log ───────────────────────────────────────────────────────────────

function EventsTab() {
  const [events, setEvents] = useState<RawEvent[]>([]);
  const [filter, setFilter] = useState<string>("");
  const [detail, setDetail] = useState<any>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const reload = useCallback(async () => {
    setEvents(await listRawEvents(filter || undefined));
  }, [filter]);
  useEffect(() => { reload(); }, [reload]);

  async function openDetail(id: string) {
    setLoadingDetail(true);
    setDetail({ loading: true });
    try {
      const full = await getRawEvent(id);
      setDetail(full);
    } catch (e: any) {
      setDetail({ error: e?.response?.data?.detail ?? e.message });
    } finally {
      setLoadingDetail(false);
    }
  }

  // Agrupa eventos por idempotency_key pra evidenciar colisões
  const idemCounts = events.reduce<Record<string, number>>((acc, e) => {
    if (e.idempotency_key) acc[e.idempotency_key] = (acc[e.idempotency_key] || 0) + 1;
    return acc;
  }, {});

  return (
    <>
      <div className="broker-card">
        <div className="broker-events-header">
          <h3 className="broker-card-title">Eventos recebidos</h3>
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">Todos</option>
            <option value="processed">Processados</option>
            <option value="skipped">Sem mapeamento</option>
            <option value="failed">Falhas</option>
            <option value="pending">Pendentes</option>
          </select>
          <button className="broker-secondary" onClick={reload}>Atualizar</button>
        </div>
        <p className="broker-card-sub" style={{ marginBottom: 12 }}>
          Clique numa linha pra ver o payload completo. Use a coluna <strong>Idem. Key</strong> pra
          identificar mensagens deduplicadas (mesma chave = mesma mensagem dentro de 60s).
        </p>
        <table className="broker-table">
          <thead>
            <tr>
              <th>Quando</th>
              <th>Origem</th>
              <th>Status</th>
              <th>Evento</th>
              <th>Payload</th>
              <th title="Status HTTP retornado pelo gateway externo (modo forward)">Forward</th>
              <th title="Chave de idempotência (60s bucket + hash do payload)">Idem. Key</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => {
              const idemDup = e.idempotency_key && idemCounts[e.idempotency_key] > 1;
              return (
                <tr key={e.id} style={{ cursor: "pointer" }} onClick={() => openDetail(e.id)}>
                  <td>{new Date(e.created_at).toLocaleString("pt-BR")}</td>
                  <td><code>{e.integration_slug}</code></td>
                  <td><span className={`broker-status broker-status--${e.status}`}>{e.status}</span></td>
                  <td>{e.canonical_event ?? "—"}</td>
                  <td className="broker-truncate" style={{ maxWidth: 280, fontFamily: "monospace", fontSize: 11 }}>
                    {e.payload_preview ?? "—"}
                  </td>
                  <td style={{ fontFamily: "monospace", fontSize: 12 }}>
                    {e.forward_status_code != null ? (
                      <span style={{
                        padding: "2px 8px", borderRadius: 10, fontWeight: 600,
                        background: e.forward_status_code >= 200 && e.forward_status_code < 300
                          ? "#e8f5e9" : "#ffebee",
                        color: e.forward_status_code >= 200 && e.forward_status_code < 300
                          ? "#2e7d32" : "#c62828",
                      }}>
                        {e.forward_status_code >= 200 && e.forward_status_code < 300 ? "✓ " : "✗ "}
                        {e.forward_status_code}
                      </span>
                    ) : <span style={{ color: "#86868b" }}>—</span>}
                  </td>
                  <td style={{ fontFamily: "monospace", fontSize: 11 }}>
                    {e.idempotency_key
                      ? <span style={{
                          color: idemDup ? "#c62828" : "#86868b",
                          fontWeight: idemDup ? 600 : 400,
                        }} title={e.idempotency_key}>
                          {e.idempotency_key.slice(0, 24)}{e.idempotency_key.length > 24 ? "…" : ""}
                          {idemDup && ` (${idemCounts[e.idempotency_key]}×)`}
                        </span>
                      : "—"}
                  </td>
                  <td onClick={(ev) => ev.stopPropagation()}>
                    {(e.status === "skipped" || e.status === "failed") && (
                      <button className="broker-link" onClick={async () => {
                        await replayEvent(e.id); reload();
                      }}>Reprocessar</button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {detail && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
          zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center",
        }} onClick={() => setDetail(null)}>
          <div style={{
            background: "white", borderRadius: 12, padding: 24,
            maxWidth: 900, width: "92%", maxHeight: "85vh", overflow: "auto",
          }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <h3 style={{ margin: 0 }}>Detalhes do evento</h3>
              <button onClick={() => setDetail(null)} style={{
                border: 0, background: "transparent", fontSize: 28, cursor: "pointer", lineHeight: 1,
              }}>×</button>
            </div>
            {loadingDetail || detail.loading ? (
              <div style={{ padding: 40, textAlign: "center" }}>Carregando...</div>
            ) : detail.error ? (
              <div className="broker-error">{detail.error}</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                <DetailRow label="ID" value={detail.id} mono />
                <DetailRow label="Status" value={detail.status} />
                <DetailRow label="Origem" value={detail.integration_slug} />
                <DetailRow label="Recebido em" value={new Date(detail.created_at).toLocaleString("pt-BR")} />
                {detail.processed_at && (
                  <DetailRow label="Processado em" value={new Date(detail.processed_at).toLocaleString("pt-BR")} />
                )}
                <DetailRow label="Idempotency Key" value={detail.idempotency_key ?? "—"} mono />
                {detail.error && <DetailRow label="Erro" value={detail.error} />}

                <div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: "#86868b", marginBottom: 4 }}>
                    Headers recebidos
                  </div>
                  <pre style={{
                    background: "#0d0d0e", color: "#a5d6a7", padding: 12, borderRadius: 8,
                    fontSize: 11, maxHeight: 150, overflow: "auto",
                  }}>{JSON.stringify(detail.headers ?? {}, null, 2)}</pre>
                </div>

                <div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: "#86868b", marginBottom: 4 }}>
                    Payload bruto recebido
                  </div>
                  <pre style={{
                    background: "#0d0d0e", color: "#fff59d", padding: 12, borderRadius: 8,
                    fontSize: 11, maxHeight: 300, overflow: "auto",
                  }}>{JSON.stringify(detail.payload ?? {}, null, 2)}</pre>
                </div>

                {detail.canonical_payload && (
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: "#86868b", marginBottom: 4 }}>
                      Resultado processado (canonical + reply)
                    </div>
                    <pre style={{
                      background: "#0d0d0e", color: "#90caf9", padding: 12, borderRadius: 8,
                      fontSize: 11, maxHeight: 300, overflow: "auto",
                    }}>{JSON.stringify(detail.canonical_payload, null, 2)}</pre>
                  </div>
                )}

                {/* Resposta do gateway externo (modo forward) */}
                {(detail.forward_url || detail.forward_status_code) && (
                  <div style={{
                    border: "1px solid var(--color-border, #e5e7eb)",
                    borderRadius: 8, padding: 12,
                    background: detail.forward_status_code && detail.forward_status_code >= 400
                      ? "#fff5f5" : "#f0f9f4",
                  }}>
                    <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
                      📡 Resposta da API externa (forward)
                    </div>
                    <DetailRow label="URL chamada" value={detail.forward_url ?? "—"} mono />
                    <div style={{ marginTop: 6 }}>
                      <DetailRow
                        label="Status HTTP"
                        value={detail.forward_status_code != null
                          ? `${detail.forward_status_code} ${
                              detail.forward_status_code >= 200 && detail.forward_status_code < 300
                                ? "✓ Sucesso"
                                : detail.forward_status_code >= 400 && detail.forward_status_code < 500
                                ? "❌ Erro do cliente (auth, formato, etc.)"
                                : detail.forward_status_code >= 500
                                ? "💥 Erro do servidor destino"
                                : ""
                            }`
                          : "— (falhou antes de receber resposta)"}
                      />
                    </div>
                    {detail.forward_response && (
                      <div style={{ marginTop: 10 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: "#86868b", marginBottom: 4 }}>
                          Body retornado pelo gateway
                        </div>
                        <pre style={{
                          background: "#0d0d0e",
                          color: detail.forward_status_code && detail.forward_status_code >= 400
                            ? "#ff8a80" : "#a5d6a7",
                          padding: 12, borderRadius: 8,
                          fontSize: 11, maxHeight: 240, overflow: "auto",
                        }}>{JSON.stringify(detail.forward_response, null, 2)}</pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

function DetailRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: 12, alignItems: "baseline" }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "#86868b" }}>{label}</div>
      <div style={{ fontSize: 13, fontFamily: mono ? "monospace" : "inherit", wordBreak: "break-all" }}>
        {value}
      </div>
    </div>
  );
}
