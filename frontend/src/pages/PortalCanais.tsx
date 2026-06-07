/**
 * PortalCanais — hub unificado de conexões.
 *
 * Mostra em uma única página:
 *   • Canais nativos (WhatsApp Cloud, Z-API, Telegram, Instagram, Web Widget)
 *   • Integrações via Webhook (sistemas externos, PDV, CRM)
 *   • Chave de API do tenant + exemplos rápidos
 *
 * Para cada canal nativo: drawer com 3 abas — Conexão | Transferência | Eventos.
 * Para integrações webhook: linka para /portal/broker?selected=<id>.
 *
 * Substitui as antigas páginas /portal/canais, /portal/broker (entry) e
 * /portal/integracao (esta última some — virou bloco interno aqui).
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import {
  listChannels,
  createChannel,
  updateChannel,
  deleteChannel,
  testChannelHandoff,
  getMe,
  type Channel,
  type HandoffConfig,
  type PortalMe,
} from "../api/portal";
import { listIntegrations, type Integration } from "../api/broker";
import "./PortalCanais.css";

// ─────────────────────────────────────────────────────────────────────────────
// Constantes de canais nativos
// ─────────────────────────────────────────────────────────────────────────────

const CHANNEL_LABELS: Record<string, string> = {
  whatsapp_cloud: "WhatsApp Cloud API",
  whatsapp_zapi:  "WhatsApp Z-API",
  telegram:        "Telegram",
  instagram:       "Instagram DM",
  web_widget:      "Widget Web",
};

const CHANNEL_ICONS: Record<string, string> = {
  whatsapp_cloud: "💚",
  whatsapp_zapi:  "💚",
  telegram:        "✈️",
  instagram:       "📷",
  web_widget:      "🌐",
};

const CHANNEL_DESCRIPTIONS: Record<string, string> = {
  whatsapp_cloud: "Conexão oficial com Meta Business — recomendada para volumes maiores.",
  whatsapp_zapi:  "Via Z-API — conecta um celular físico (não-oficial Meta).",
  telegram:        "Bot do Telegram para clientes mais técnicos.",
  instagram:       "Mensagens diretas do Instagram via Meta.",
  web_widget:      "Widget de chat embedável no site da farmácia.",
};

const CHANNEL_CREDENTIAL_FIELDS: Record<string, { key: string; label: string; type?: string }[]> = {
  whatsapp_cloud: [
    { key: "wa_cloud_token",      label: "Access Token (permanente)", type: "password" },
    { key: "wa_cloud_phone_id",   label: "Phone Number ID" },
    { key: "wa_webhook_secret",   label: "Webhook Verify Token", type: "password" },
  ],
  whatsapp_zapi: [
    { key: "zapi_instance_id",    label: "Instance ID" },
    { key: "zapi_token",          label: "Token", type: "password" },
    { key: "zapi_client_token",   label: "Client Token (opcional)", type: "password" },
  ],
  telegram: [
    { key: "telegram_bot_token",  label: "Bot Token", type: "password" },
  ],
  instagram: [
    { key: "instagram_token",     label: "Page Access Token", type: "password" },
    { key: "instagram_page_id",   label: "Page ID" },
  ],
  web_widget: [],
};

// ─────────────────────────────────────────────────────────────────────────────
// Página principal
// ─────────────────────────────────────────────────────────────────────────────

export function PortalCanais() {
  const navigate = useNavigate();
  const [channels, setChannels]           = useState<Channel[]>([]);
  const [integrations, setIntegrations]   = useState<Integration[]>([]);
  const [me, setMe]                       = useState<PortalMe | null>(null);
  const [loading, setLoading]             = useState(true);
  const [error, setError]                 = useState("");

  // Drawers / modais
  const [showNewChannel, setShowNewChannel] = useState(false);
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null);
  const [showApiBlock, setShowApiBlock] = useState(false);

  async function refresh() {
    try {
      const [chs, ints, meData] = await Promise.all([
        listChannels(),
        listIntegrations().catch(() => [] as Integration[]),
        getMe().catch(() => null),
      ]);
      setChannels(chs);
      setIntegrations(ints);
      setMe(meData);
    } catch {
      setError("Não foi possível carregar suas conexões.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  return (
    <PortalLayout active="canais">
      <header className="portal-page-header">
        <h1 className="portal-page-title">Canais & Integrações</h1>
        <p className="portal-page-subtitle">
          Configure por onde seu robô conversa com clientes — WhatsApp, Telegram,
          ou qualquer sistema externo via webhook. Cada conexão tem sua própria
          configuração de transferência para o balcão.
        </p>
      </header>

      {error && <div className="form-error" style={{ marginBottom: 16 }}>{error}</div>}

      {loading ? (
        <div className="portal-loading"><Spinner size={32} /></div>
      ) : (
        <>
          {/* ── Canais Nativos ─────────────────────────────────────────── */}
          <section className="canais-section">
            <div className="canais-section__header">
              <div>
                <h2 className="canais-section__title">📲 Canais Nativos</h2>
                <p className="canais-section__hint">
                  Conexões diretas com plataformas de mensageria — o robô recebe e responde direto.
                </p>
              </div>
              <button className="btn btn-primary" onClick={() => setShowNewChannel(true)}>
                + Conectar canal
              </button>
            </div>

            {channels.length === 0 ? (
              <div className="canais-empty">
                Nenhum canal conectado ainda. Comece pelo <strong>WhatsApp Cloud</strong> — é o mais comum.
              </div>
            ) : (
              <div className="canais-grid">
                {channels.map((ch) => (
                  <button key={ch.id} className="canais-card" onClick={() => setSelectedChannel(ch)}>
                    <div className="canais-card__icon">{CHANNEL_ICONS[ch.channel_type] || "📨"}</div>
                    <div className="canais-card__body">
                      <div className="canais-card__title">
                        {ch.display_name || CHANNEL_LABELS[ch.channel_type]}
                      </div>
                      <div className="canais-card__subtitle">
                        {CHANNEL_LABELS[ch.channel_type]}
                      </div>
                      <div className="canais-card__badges">
                        <span className={`canais-badge ${ch.active ? "canais-badge--on" : "canais-badge--off"}`}>
                          {ch.active ? "● Ativo" : "○ Inativo"}
                        </span>
                        {ch.handoff_config?.enabled && (
                          <span className="canais-badge canais-badge--handoff" title="Transferência ao balcão configurada">
                            🏪 Balcão
                          </span>
                        )}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </section>

          {/* ── Integrações via Webhook ───────────────────────────────── */}
          <section className="canais-section">
            <div className="canais-section__header">
              <div>
                <h2 className="canais-section__title">🔌 Integrações via Webhook</h2>
                <p className="canais-section__hint">
                  Receba mensagens de qualquer sistema externo (PDV, CRM, ClickMassa,
                  TalkFarma…) — payload entra, é traduzido pelo broker e o robô responde.
                </p>
              </div>
              <button className="btn btn-primary" onClick={() => navigate("/portal/broker")}>
                + Nova integração
              </button>
            </div>

            {integrations.length === 0 ? (
              <div className="canais-empty">
                Nenhuma integração webhook configurada. Use isso se sua plataforma de
                atendimento (ClickMassa, TalkFarma, etc.) precisa mandar eventos para nós.
              </div>
            ) : (
              <div className="canais-grid">
                {integrations.map((it) => (
                  <button
                    key={it.id}
                    className="canais-card canais-card--webhook"
                    onClick={() => navigate(`/portal/broker?selected=${it.id}`)}
                  >
                    <div className="canais-card__icon">🔌</div>
                    <div className="canais-card__body">
                      <div className="canais-card__title">{it.name}</div>
                      <div className="canais-card__subtitle">/{it.slug}</div>
                      <div className="canais-card__badges">
                        <span className={`canais-badge ${it.enabled ? "canais-badge--on" : "canais-badge--off"}`}>
                          {it.enabled ? "● Ativa" : "○ Inativa"}
                        </span>
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </section>

          {/* ── Chave de API (genérica) ──────────────────────────────── */}
          <section className="canais-section canais-api">
            <div
              className="canais-api__toggle"
              onClick={() => setShowApiBlock((v) => !v)}
              role="button"
            >
              <h3 className="canais-section__title" style={{ marginBottom: 0 }}>
                🔑 Chave de API & Endpoint genérico
              </h3>
              <span className="canais-api__chevron">{showApiBlock ? "▾" : "▸"}</span>
            </div>
            <p className="canais-section__hint">
              Para integrações simples direto via curl/HTTP — sem precisar criar uma integração webhook formal.
            </p>

            {showApiBlock && me && (
              <div className="canais-api__body">
                <CodeBlock label="Sua API key (mantenha em segredo)" code={me.api_key || "—"} />
                <CodeBlock
                  label="Enviar mensagem (curl)"
                  code={`curl -X POST ${window.location.origin.replace(":5173", ":8000")}/webhook/${me.api_key} \\
  -H "Content-Type: application/json" \\
  -d '{"phone": "5511999999999", "message": "Olá"}'`}
                />
                <small style={{ color: "#6b7280" }}>
                  Para configurar callback de saída (resposta do robô), edite a URL em <em>Plano & Cobrança</em> ou via API admin.
                </small>
              </div>
            )}
          </section>
        </>
      )}

      {/* ── Modal de novo canal ────────────────────────────────────── */}
      {showNewChannel && (
        <NewChannelModal
          onClose={() => setShowNewChannel(false)}
          onCreated={async (ch) => { setShowNewChannel(false); setSelectedChannel(ch); await refresh(); }}
        />
      )}

      {/* ── Drawer de edição de canal ──────────────────────────────── */}
      {selectedChannel && (
        <ChannelDrawer
          channel={selectedChannel}
          onClose={() => setSelectedChannel(null)}
          onChanged={async (updated) => { setSelectedChannel(updated); await refresh(); }}
          onDeleted={async () => { setSelectedChannel(null); await refresh(); }}
        />
      )}
    </PortalLayout>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-componentes
// ─────────────────────────────────────────────────────────────────────────────

function CodeBlock({ label, code }: { label: string; code: string }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }
  return (
    <div className="canais-code">
      <div className="canais-code__head">
        <span>{label}</span>
        <button className="btn btn-sm btn-secondary" onClick={copy}>
          {copied ? "✓ Copiado" : "Copiar"}
        </button>
      </div>
      <pre><code>{code}</code></pre>
    </div>
  );
}

// ─── Modal de criação de canal ─────────────────────────────────────────────

function NewChannelModal({ onClose, onCreated }: { onClose: () => void; onCreated: (ch: Channel) => void }) {
  const [type, setType] = useState("whatsapp_cloud");
  const [displayName, setDisplayName] = useState("");
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const fields = CHANNEL_CREDENTIAL_FIELDS[type] || [];

  async function save() {
    setBusy(true); setErr("");
    try {
      const ch = await createChannel({
        channel_type: type,
        display_name: displayName.trim() || undefined,
        credentials: creds,
      });
      onCreated(ch);
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Erro ao criar canal.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Conectar novo canal</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <label className="form-label">Tipo de canal</label>
        <div className="canais-type-picker">
          {Object.entries(CHANNEL_LABELS).map(([k, label]) => (
            <button
              key={k}
              className={`canais-type-btn ${type === k ? "is-selected" : ""}`}
              onClick={() => { setType(k); setCreds({}); }}
            >
              <div style={{ fontSize: 20 }}>{CHANNEL_ICONS[k]}</div>
              <strong>{label}</strong>
              <small>{CHANNEL_DESCRIPTIONS[k]}</small>
            </button>
          ))}
        </div>

        <label className="form-label" style={{ marginTop: 16 }}>
          Nome de exibição <small style={{ color: "#6b7280" }}>(ex: "Loja Centro")</small>
        </label>
        <input
          className="form-input"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="Loja Centro"
        />

        {fields.length > 0 && (
          <>
            <h4 style={{ marginTop: 20, marginBottom: 8 }}>Credenciais</h4>
            {fields.map((f) => (
              <div key={f.key} style={{ marginBottom: 10 }}>
                <label className="form-label">{f.label}</label>
                <input
                  className="form-input"
                  type={f.type || "text"}
                  value={creds[f.key] || ""}
                  onChange={(e) => setCreds({ ...creds, [f.key]: e.target.value })}
                />
              </div>
            ))}
          </>
        )}

        {err && <div className="form-error" style={{ marginTop: 12 }}>{err}</div>}

        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={onClose}>Cancelar</button>
          <button className="btn btn-primary" disabled={busy} onClick={save}>
            {busy ? <Spinner size={14} /> : "Conectar"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Drawer de edição de canal (3 abas) ────────────────────────────────────

type DrawerTab = "conexao" | "transferencia" | "ajuda";

function ChannelDrawer({
  channel,
  onClose,
  onChanged,
  onDeleted,
}: {
  channel: Channel;
  onClose: () => void;
  onChanged: (ch: Channel) => void;
  onDeleted: () => void;
}) {
  const [tab, setTab] = useState<DrawerTab>("conexao");
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer__head">
          <div>
            <h2>{channel.display_name || CHANNEL_LABELS[channel.channel_type]}</h2>
            <small>{CHANNEL_LABELS[channel.channel_type]}</small>
          </div>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="drawer__tabs">
          <button className={tab === "conexao" ? "is-active" : ""} onClick={() => setTab("conexao")}>
            Conexão
          </button>
          <button className={tab === "transferencia" ? "is-active" : ""} onClick={() => setTab("transferencia")}>
            Transferência ao Balcão {channel.handoff_config?.enabled && <span className="dot-on">●</span>}
          </button>
          <button className={tab === "ajuda" ? "is-active" : ""} onClick={() => setTab("ajuda")}>
            Como usar
          </button>
        </div>

        {tab === "conexao"      && <TabConexao channel={channel} onChanged={onChanged} onDeleted={onDeleted} />}
        {tab === "transferencia"&& <TabTransferencia channel={channel} onChanged={onChanged} />}
        {tab === "ajuda"        && <TabAjuda channel={channel} />}
      </aside>
    </div>
  );
}

// ─── Aba: Conexão ──────────────────────────────────────────────────────────

function TabConexao({
  channel, onChanged, onDeleted,
}: { channel: Channel; onChanged: (ch: Channel) => void; onDeleted: () => void }) {
  const [displayName, setDisplayName] = useState(channel.display_name || "");
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [showCreds, setShowCreds] = useState(false);
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState("");
  const fields = CHANNEL_CREDENTIAL_FIELDS[channel.channel_type] || [];


  async function saveName() {
    setBusy(true);
    try {
      const updated = await updateChannel(channel.id, { display_name: displayName });
      onChanged(updated);
      setInfo("Nome atualizado.");
      setTimeout(() => setInfo(""), 2000);
    } finally { setBusy(false); }
  }

  async function saveCreds() {
    setBusy(true);
    try {
      const updated = await updateChannel(channel.id, { credentials: creds });
      onChanged(updated);
      setCreds({}); setShowCreds(false);
      setInfo("Credenciais atualizadas.");
      setTimeout(() => setInfo(""), 2000);
    } finally { setBusy(false); }
  }

  async function toggle() {
    const updated = await updateChannel(channel.id, { active: !channel.active });
    onChanged(updated);
  }

  async function destroy() {
    if (!confirm(`Remover ${channel.display_name || channel.channel_type}? Essa ação não pode ser desfeita.`)) return;
    await deleteChannel(channel.id);
    onDeleted();
  }

  return (
    <div className="drawer__body">
      <div className="drawer-row">
        <label className="form-label">Status</label>
        <button className={`btn ${channel.active ? "btn-secondary" : "btn-primary"}`} onClick={toggle}>
          {channel.active ? "Desativar canal" : "Ativar canal"}
        </button>
      </div>


      <div className="drawer-row">
        <label className="form-label">Nome de exibição</label>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            className="form-input"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            style={{ flex: 1 }}
          />
          <button className="btn btn-secondary" disabled={busy} onClick={saveName}>Salvar</button>
        </div>
      </div>

      <div className="drawer-row">
        <label className="form-label">Webhook URL deste canal</label>
        <CodeBlock label="Configure essa URL na plataforma de origem" code={window.location.origin.replace(":5173", ":8000") + channel.webhook_url} />
      </div>

      {fields.length > 0 && (
        <div className="drawer-row">
          <label className="form-label">Credenciais</label>
          {!showCreds ? (
            <button className="btn btn-secondary" onClick={() => setShowCreds(true)}>
              Atualizar credenciais
            </button>
          ) : (
            <>
              {fields.map((f) => (
                <div key={f.key} style={{ marginBottom: 8 }}>
                  <label className="form-label" style={{ fontSize: 12 }}>{f.label}</label>
                  <input
                    className="form-input"
                    type={f.type || "text"}
                    value={creds[f.key] || ""}
                    onChange={(e) => setCreds({ ...creds, [f.key]: e.target.value })}
                  />
                </div>
              ))}
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn btn-secondary" onClick={() => { setShowCreds(false); setCreds({}); }}>Cancelar</button>
                <button className="btn btn-primary" disabled={busy || Object.values(creds).every(v => !v)} onClick={saveCreds}>
                  Salvar credenciais
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {info && <div className="cliente-help" style={{ marginTop: 8 }}>{info}</div>}

      <div className="drawer-row" style={{ marginTop: 32, borderTop: "1px solid #e5e7eb", paddingTop: 16 }}>
        <button className="btn btn-danger" onClick={destroy}>🗑️ Remover canal</button>
      </div>
    </div>
  );
}

// ─── Aba: Transferência ao Balcão ──────────────────────────────────────────

function TabTransferencia({ channel, onChanged }: { channel: Channel; onChanged: (ch: Channel) => void }) {
  const [cfg, setCfg] = useState<HandoffConfig>(channel.handoff_config || {});
  const [pauseMin, setPauseMin] = useState<number>(channel.handoff_pause_minutes ?? 240);
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState("");
  const [err, setErr] = useState("");
  const [testPhone, setTestPhone] = useState("");

  function set<K extends keyof HandoffConfig>(k: K, v: HandoffConfig[K]) {
    setCfg((p) => ({ ...p, [k]: v }));
  }

  async function save() {
    setBusy(true); setErr(""); setInfo("");
    try {
      const updated = await updateChannel(channel.id, { handoff_config: cfg, handoff_pause_minutes: pauseMin });
      onChanged(updated);
      setInfo("Configuração salva.");
      setTimeout(() => setInfo(""), 2000);
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Erro ao salvar.");
    } finally { setBusy(false); }
  }

  async function test() {
    if (!testPhone) { setErr("Informe um número para teste."); return; }
    setBusy(true); setErr(""); setInfo("");
    try {
      const r = await testChannelHandoff(channel.id, { phone: testPhone });
      if (r.ok) setInfo("✅ Transferência de teste enviada com sucesso!");
      else setErr(`Falha (HTTP ${r.status_code}): ${r.error || "Verifique token / queue_id."}`);
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Erro ao testar.");
    } finally { setBusy(false); }
  }

  return (
    <div className="drawer__body">
      <p className="canais-section__hint" style={{ marginTop: 0 }}>
        Quando o robô precisar passar para um atendente humano (cliente pediu,
        emergência clínica, ou o agente sinalizou que não consegue resolver),
        nós disparamos um POST para esta plataforma criando um ticket na fila escolhida.
      </p>

      <div className="drawer-row">
        <label className="form-label" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="checkbox"
            checked={!!cfg.enabled}
            onChange={(e) => set("enabled", e.target.checked)}
          />
          Ativar transferência ao balcão neste canal
        </label>
      </div>

      <div className="drawer-row">
        <label className="form-label">Provider</label>
        <select
          className="form-select"
          value={cfg.provider || "clickmassa"}
          onChange={(e) => set("provider", e.target.value as any)}
        >
          <option value="clickmassa">ClickMassa / TalkFarma</option>
        </select>
      </div>

      <div className="drawer-row">
        <label className="form-label">Base URL</label>
        <input
          className="form-input"
          placeholder="https://chatapi.talkfarma.pro/v1/api/external/<uuid>"
          value={cfg.base_url || ""}
          onChange={(e) => set("base_url", e.target.value)}
        />
      </div>

      <div className="drawer-row">
        <label className="form-label">Token (JWT)</label>
        <input
          className="form-input"
          type="password"
          value={cfg.token || ""}
          onChange={(e) => set("token", e.target.value)}
        />
      </div>

      <div className="drawer-row">
        <label className="form-label">Queue ID (fila do atendente)</label>
        <input
          className="form-input"
          type="number"
          value={cfg.queue_id ?? ""}
          onChange={(e) => set("queue_id", e.target.value)}
        />
      </div>

      <div className="drawer-row">
        <label className="form-label">Mensagem mostrada ao cliente na transferência</label>
        <textarea
          className="form-input"
          rows={2}
          placeholder="Vou te transferir para um atendente humano agora..."
          value={cfg.transfer_message || ""}
          onChange={(e) => set("transfer_message", e.target.value)}
        />
      </div>

      <div className="drawer-row">
        <label className="form-label">Palavras-gatilho (separadas por vírgula)</label>
        <input
          className="form-input"
          placeholder="atendente, humano, balcão"
          value={(cfg.trigger_keywords || []).join(", ")}
          onChange={(e) => set("trigger_keywords",
            e.target.value.split(",").map(s => s.trim()).filter(Boolean))}
        />
        <small style={{ color: "#6b7280" }}>
          Se vazio, usamos a lista padrão (atendente, humano, balcão, falar com alguém...).
        </small>
      </div>

      <div className="drawer-row">
        <label className="form-label">Ordem das mensagens após transferência</label>
        <select
          className="form-select"
          value={cfg.post_handoff_order || "summary_first"}
          onChange={(e) => set("post_handoff_order", e.target.value as "summary_first" | "offers_first")}
        >
          <option value="summary_first">Resumo do pedido → Ofertas</option>
          <option value="offers_first">Ofertas → Resumo do pedido</option>
        </select>
        <small style={{ color: "#6b7280" }}>
          Define qual mensagem o cliente recebe primeiro após a transferência ao balcão.
        </small>
      </div>

      <div className="drawer-row">
        <label className="form-label">Tempo que a IA fica pausada após a transferência (minutos)</label>
        <input
          className="form-input"
          type="number"
          min={0}
          max={10080}
          value={pauseMin}
          onChange={(e) => setPauseMin(Math.max(0, Math.min(10080, parseInt(e.target.value || "0", 10) || 0)))}
        />
        <small style={{ color: "#6b7280" }}>
          Depois de transferir ao atendente, o robô fica em silêncio por este tempo
          (padrão 240 = 4h). Quando o cliente volta a falar após a janela, a IA reassume.
          Use <strong>0</strong> para a IA não pausar.
        </small>
      </div>

      {info && <div className="cliente-help">{info}</div>}
      {err && <div className="form-error">{err}</div>}

      <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
        <button className="btn btn-primary" disabled={busy} onClick={save}>
          {busy ? <Spinner size={14} /> : "Salvar"}
        </button>
      </div>

      {cfg.enabled && (
        <div className="drawer-row" style={{ marginTop: 24, borderTop: "1px solid #e5e7eb", paddingTop: 16 }}>
          <label className="form-label">Testar agora — envia uma transferência real para o número informado</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              className="form-input"
              placeholder="5511999999999"
              value={testPhone}
              onChange={(e) => setTestPhone(e.target.value)}
              style={{ flex: 1 }}
            />
            <button className="btn btn-secondary" disabled={busy || !testPhone} onClick={test}>
              Disparar teste
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Aba: Ajuda ────────────────────────────────────────────────────────────

function TabAjuda({ channel }: { channel: Channel }) {
  const HELP: Record<string, { steps: string[]; docs?: string }> = {
    whatsapp_cloud: {
      steps: [
        "1. Acesse business.facebook.com e crie um aplicativo Meta para Empresas.",
        "2. Em WhatsApp > Início, gere um Access Token permanente.",
        "3. Copie o Phone Number ID e o Webhook Verify Token.",
        "4. Na Meta, configure a URL de webhook com o link mostrado na aba Conexão.",
      ],
      docs: "https://developers.facebook.com/docs/whatsapp/cloud-api",
    },
    whatsapp_zapi: {
      steps: [
        "1. Crie uma conta em z-api.io e gere uma nova instância.",
        "2. Escaneie o QR code pelo celular para conectar.",
        "3. Copie Instance ID e Token da página da instância.",
        "4. Cole o Webhook URL na seção de webhooks da Z-API.",
      ],
      docs: "https://developer.z-api.io/",
    },
    telegram: {
      steps: [
        "1. No Telegram, fale com @BotFather e use /newbot.",
        "2. Copie o token que ele retornar.",
        "3. Cole o Webhook URL via /setwebhook (BotFather).",
      ],
      docs: "https://core.telegram.org/bots",
    },
    instagram: {
      steps: [
        "1. No Meta Business, conecte a página Instagram.",
        "2. Gere o Page Access Token com permissões de DM.",
        "3. Cole o Webhook URL nas configurações do app Meta.",
      ],
    },
    web_widget: {
      steps: [
        "1. Cole o snippet HTML no site da farmácia.",
        "2. O widget já se conecta automaticamente.",
      ],
    },
  };

  const help = HELP[channel.channel_type];

  return (
    <div className="drawer__body">
      <h4>Como configurar</h4>
      <ol className="canais-steps">
        {help?.steps.map((s, i) => <li key={i}>{s}</li>)}
      </ol>
      {help?.docs && (
        <p>
          <a href={help.docs} target="_blank" rel="noreferrer" className="canais-link">
            📚 Documentação oficial →
          </a>
        </p>
      )}
    </div>
  );
}
