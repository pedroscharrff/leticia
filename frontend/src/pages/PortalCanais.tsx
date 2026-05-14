import { useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import { Badge } from "../components/Badge";
import { listChannels, createChannel, updateChannel, deleteChannel, type Channel } from "../api/portal";
import "./PortalCanais.css";

const CHANNEL_LABELS: Record<string, string> = {
  whatsapp_cloud: "WhatsApp Cloud API",
  whatsapp_zapi:  "WhatsApp Z-API",
  telegram:        "Telegram",
  instagram:       "Instagram DM",
  web_widget:      "Widget Web",
};

const CHANNEL_CREDENTIAL_FIELDS: Record<string, string[]> = {
  whatsapp_cloud: ["wa_cloud_token", "wa_cloud_phone_id", "wa_webhook_secret"],
  whatsapp_zapi:  ["zapi_instance_id", "zapi_token", "zapi_client_token"],
  telegram:        ["telegram_bot_token"],
  instagram:       ["instagram_token", "instagram_page_id"],
  web_widget:      [],
};

export function PortalCanais() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [selectedType, setSelectedType] = useState("whatsapp_cloud");
  const [displayName, setDisplayName] = useState("");
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    listChannels()
      .then(setChannels)
      .catch(() => setError("Erro ao carregar canais"))
      .finally(() => setLoading(false));
  }, []);

  async function handleCreate() {
    try {
      const ch = await createChannel({ channel_type: selectedType, display_name: displayName, credentials });
      setChannels((cs) => [...cs, ch]);
      setShowModal(false);
      setCredentials({});
      setDisplayName("");
    } catch { setError("Erro ao criar canal. Verifique seu plano."); }
  }

  async function handleToggle(ch: Channel) {
    const updated = await updateChannel(ch.id, { active: !ch.active });
    setChannels((cs) => cs.map((c) => (c.id === updated.id ? updated : c)));
  }

  async function handleDelete(id: string) {
    if (!window.confirm("Remover este canal?")) return;
    await deleteChannel(id);
    setChannels((cs) => cs.filter((c) => c.id !== id));
  }

  function copyWebhook(url: string) {
    navigator.clipboard.writeText(window.location.origin + url);
  }

  const credFields = CHANNEL_CREDENTIAL_FIELDS[selectedType] ?? [];

  return (
    <PortalLayout active="canais">
      <div className="canais-page">
        <div className="canais-header">
          <h1 className="page-title">Canais de Atendimento</h1>
          <button className="btn btn--primary btn--sm" onClick={() => setShowModal(true)}>+ Conectar canal</button>
        </div>

        {error && <div className="error-banner">{error}</div>}

        {loading ? <Spinner /> : (
          <div className="channels-grid">
            {channels.map((ch) => (
              <div key={ch.id} className="channel-card">
                <div className="channel-card__top">
                  <div>
                    <span className="channel-card__type">{CHANNEL_LABELS[ch.channel_type] ?? ch.channel_type}</span>
                    {ch.display_name && <span className="channel-card__name"> — {ch.display_name}</span>}
                  </div>
                  <Badge variant={ch.active ? "green" : "gray"}>{ch.active ? "Ativo" : "Inativo"}</Badge>
                </div>
                <div className="channel-card__webhook">
                  <code>{ch.webhook_url}</code>
                  <button className="btn-icon" onClick={() => copyWebhook(ch.webhook_url)} title="Copiar URL">📋</button>
                </div>
                <div className="channel-card__actions">
                  <button className="btn btn--secondary btn--sm" onClick={() => handleToggle(ch)}>
                    {ch.active ? "Desativar" : "Ativar"}
                  </button>
                  <button className="btn btn--sm" style={{ color: "#ef4444" }} onClick={() => handleDelete(ch.id)}>
                    Remover
                  </button>
                </div>
              </div>
            ))}
            {channels.length === 0 && (
              <p className="empty-state">Nenhum canal configurado. Clique em "+ Conectar canal" para começar.</p>
            )}
          </div>
        )}
      </div>

      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <h2>Conectar novo canal</h2>
            <label className="form-label">
              <span>Tipo de canal</span>
              <select
                className="form-input"
                value={selectedType}
                onChange={(e) => { setSelectedType(e.target.value); setCredentials({}); }}
              >
                {Object.entries(CHANNEL_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </label>
            <label className="form-label" style={{ marginTop: 12 }}>
              <span>Nome de exibição (opcional)</span>
              <input className="form-input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
            </label>
            {credFields.map((field) => (
              <label key={field} className="form-label" style={{ marginTop: 12 }}>
                <span>{field}</span>
                <input
                  className="form-input"
                  type="password"
                  placeholder="••••••••"
                  value={credentials[field] ?? ""}
                  onChange={(e) => setCredentials((c) => ({ ...c, [field]: e.target.value }))}
                />
              </label>
            ))}
            <div className="modal-footer">
              <button className="btn btn--secondary" onClick={() => setShowModal(false)}>Cancelar</button>
              <button className="btn btn--primary" onClick={handleCreate}>Conectar</button>
            </div>
          </div>
        </div>
      )}
    </PortalLayout>
  );
}
