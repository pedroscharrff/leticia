import { useCallback, useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import {
  type DiscoveredPath,
  type Integration,
  type RawEvent,
  createIntegration,
  deleteIntegration,
  discoverPaths,
  getRawEvent,
  listIntegrations,
  listRawEvents,
  replayEvent,
  saveFlow,
} from "../api/broker";
import "./PortalBroker.css";

type Tab = "connect" | "flow" | "events";

export function PortalBroker() {
  const [tab, setTab] = useState<Tab>("connect");
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [selected, setSelected] = useState<Integration | null>(null);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);

  const refresh = useCallback(async () => {
    const list = await listIntegrations();
    setIntegrations(list);
    if (!selected && list.length) setSelected(list[0]);
    if (selected) {
      const updated = list.find((i) => i.id === selected.id);
      if (updated) setSelected(updated);
    }
  }, [selected]);

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
                {(["connect", "flow", "events"] as Tab[]).map((t) => (
                  <button
                    key={t}
                    className={`broker-tab ${tab === t ? "is-active" : ""}`}
                    onClick={() => setTab(t)}
                  >
                    {t === "connect" && "1. Conectar"}
                    {t === "flow" && "2. Configurar fluxo"}
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
                      placeholder={f.hint || "$.caminho"}
                    />
                  )}
                </div>
              </div>
            );
          })}
        </div>

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
                Útil para autenticação (ex: <code>Authorization: Bearer ...</code>) ou
                tokens específicos do gateway (Z-API, Twilio, etc.).
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
              </div>
            </details>
          </div>
        )}

        {replyMode === "response" && (
          <label className="broker-field" style={{ maxWidth: 200 }}>
            <span>Status HTTP</span>
            <input type="number" value={replyStatusCode}
                   onChange={(e) => setReplyStatusCode(parseInt(e.target.value) || 200)} />
          </label>
        )}

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

      {/* Step 4: Bundling (debounce) — só faz sentido no modo forward */}
      <div className="broker-card">
        <h3 className="broker-card-title">4. Agrupar mensagens picadas (debounce)</h3>
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


// ── Events log ───────────────────────────────────────────────────────────────

function EventsTab() {
  const [events, setEvents] = useState<RawEvent[]>([]);
  const [filter, setFilter] = useState<string>("");

  const reload = useCallback(async () => {
    setEvents(await listRawEvents(filter || undefined));
  }, [filter]);
  useEffect(() => { reload(); }, [reload]);

  return (
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
      <table className="broker-table">
        <thead>
          <tr><th>Quando</th><th>Origem</th><th>Status</th><th>Evento</th><th>Erro</th><th></th></tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td>{new Date(e.created_at).toLocaleString("pt-BR")}</td>
              <td><code>{e.integration_slug}</code></td>
              <td><span className={`broker-status broker-status--${e.status}`}>{e.status}</span></td>
              <td>{e.canonical_event ?? "—"}</td>
              <td className="broker-truncate">{e.error ?? ""}</td>
              <td>
                {(e.status === "skipped" || e.status === "failed") && (
                  <button className="broker-link" onClick={async () => {
                    await replayEvent(e.id); reload();
                  }}>Reprocessar</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
