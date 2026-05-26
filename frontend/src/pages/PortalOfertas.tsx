/**
 * PortalOfertas — CRUD de ofertas exibidas antes do handoff,
 * com suporte a mídia (imagem OU áudio) e preview WhatsApp ao vivo.
 *
 * Capability: `sales.pre_handoff_offers`.
 */
import { useEffect, useRef, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import { Toggle } from "../components/Toggle";
import { WhatsappPreview } from "../components/WhatsappPreview";
import {
  listOffers,
  createOffer,
  updateOffer,
  deleteOffer,
  uploadOfferMedia,
  deleteOfferMedia,
  getChannelCapabilities,
  type Offer,
  type OfferIn,
  type ChannelCapabilities,
} from "../api/offers";

const EMPTY_DRAFT: OfferIn = {
  title: "",
  description: "",
  valid_from: null,
  valid_until: null,
  priority: 0,
  active: true,
};

function isoToLocal(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function localToIso(local: string): string | null {
  if (!local) return null;
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export function PortalOfertas() {
  const [offers, setOffers]     = useState<Offer[] | null>(null);
  const [error, setError]       = useState("");
  const [draft, setDraft]       = useState<OfferIn>(EMPTY_DRAFT);
  const [current, setCurrent]   = useState<Offer | null>(null); // oferta persistida em edição
  const [busy, setBusy]         = useState(false);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [caps, setCaps]         = useState<ChannelCapabilities | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function refresh() {
    try {
      setOffers(await listOffers());
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Não foi possível carregar ofertas.");
    }
  }

  useEffect(() => {
    void refresh();
    getChannelCapabilities().then(setCaps).catch(() => setCaps(null));
  }, []);

  function startEdit(o: Offer) {
    setCurrent(o);
    setDraft({
      title: o.title,
      description: o.description,
      valid_from: o.valid_from,
      valid_until: o.valid_until,
      priority: o.priority,
      active: o.active,
    });
    setError("");
  }

  function cancelEdit() {
    setCurrent(null);
    setDraft(EMPTY_DRAFT);
    setError("");
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      const saved = current
        ? await updateOffer(current.id, draft)
        : await createOffer(draft);
      setCurrent(saved);   // permite anexar mídia imediatamente em cima do recém-criado
      await refresh();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao salvar.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    if (!confirm("Remover esta oferta?")) return;
    setBusy(true);
    try {
      await deleteOffer(id);
      if (current?.id === id) cancelEdit();
      await refresh();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao remover.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(o: Offer) {
    try {
      await updateOffer(o.id, { active: !o.active });
      await refresh();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao atualizar.");
    }
  }

  async function onFilePicked(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !current) return;
    setUploadBusy(true);
    setError("");
    try {
      const updated = await uploadOfferMedia(current.id, file);
      setCurrent(updated);
      await refresh();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Falha no upload.");
    } finally {
      setUploadBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function removeMedia() {
    if (!current) return;
    if (!confirm("Remover mídia desta oferta?")) return;
    setUploadBusy(true);
    try {
      const updated = await deleteOfferMedia(current.id);
      setCurrent(updated);
      await refresh();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao remover mídia.");
    } finally {
      setUploadBusy(false);
    }
  }

  // Preview lê: draft (texto editado ao vivo) + current (mídia já persistida)
  const previewMedia = current
    ? { url: current.media_url, type: current.media_type }
    : { url: null as string | null, type: null as "image" | "audio" | null };

  return (
    <PortalLayout active="ofertas">
      <header className="portal-page-header">
        <h1 className="portal-page-title">Ofertas</h1>
        <p className="portal-page-subtitle">
          Cadastre ofertas/promoções que o robô envia como última tentativa de
          retenção <strong>antes de transferir para um atendente humano</strong>.
          Cada oferta pode ter imagem OU áudio anexo — o cliente recebe uma
          mensagem separada por oferta com mídia.
          <br />
          💡 Para ligar o envio, ative a capability <strong>"Ofertas antes da
          Transferência"</strong> em <em>Vendas › Recursos do seu Robô</em>.
        </p>
        {caps && (
          <div style={{ fontSize: 12, color: caps.has_active_channel ? "#3a6f3a" : "#a06632", marginTop: 8 }}>
            {caps.has_active_channel
              ? <>Canal ativo: <strong>{caps.provider}</strong> — imagem {caps.supports_image ? "✓" : "✗"} · áudio {caps.supports_audio ? "✓" : "✗"}. Quando o canal não suporta, o caption é enviado como texto.</>
              : <>Nenhum canal de saída configurado ainda. Configure em <em>Configuração › Canais</em>.</>}
          </div>
        )}
      </header>

      {!offers ? (
        <div className="portal-loading"><Spinner size={28} /></div>
      ) : (
        <>
          {/* Form + Preview lado a lado */}
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 360px", gap: 24, marginBottom: 24 }}>
            <section className="cliente-card">
              <h3 style={{ marginTop: 0 }}>
                {current ? "Editar oferta" : "Nova oferta"}
              </h3>

              <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 12 }}>
                <div className="form-group">
                  <label className="form-label">Título</label>
                  <input
                    className="form-input"
                    placeholder="ex.: Kit Gripe 12% OFF"
                    maxLength={200}
                    value={draft.title}
                    onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Prioridade</label>
                  <input
                    className="form-input"
                    type="number"
                    min="0"
                    max="1000"
                    value={draft.priority ?? 0}
                    onChange={(e) => setDraft({ ...draft, priority: parseInt(e.target.value, 10) || 0 })}
                  />
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">Descrição</label>
                <textarea
                  className="form-input"
                  rows={2}
                  maxLength={1000}
                  placeholder="ex.: Paracetamol + xarope + soro por R$ 28,90 até domingo."
                  value={draft.description ?? ""}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div className="form-group">
                  <label className="form-label">Início (opcional)</label>
                  <input
                    className="form-input"
                    type="datetime-local"
                    value={isoToLocal(draft.valid_from)}
                    onChange={(e) => setDraft({ ...draft, valid_from: localToIso(e.target.value) })}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Fim (opcional)</label>
                  <input
                    className="form-input"
                    type="datetime-local"
                    value={isoToLocal(draft.valid_until)}
                    onChange={(e) => setDraft({ ...draft, valid_until: localToIso(e.target.value) })}
                  />
                </div>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
                <Toggle
                  checked={!!draft.active}
                  onChange={(v) => setDraft({ ...draft, active: v })}
                />
                <span style={{ fontSize: 13 }}>Oferta ativa</span>
              </div>

              {/* Bloco de mídia — só aparece após a oferta existir no DB */}
              <div style={{ marginTop: 18, padding: 12, background: "#f9fafb", borderRadius: 8 }}>
                <div style={{ fontWeight: 500, fontSize: 14, marginBottom: 8 }}>Mídia (opcional)</div>
                {!current ? (
                  <div style={{ fontSize: 12, color: "#6b7280" }}>
                    Salve a oferta primeiro para anexar uma imagem ou áudio.
                  </div>
                ) : current.media_url ? (
                  <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <span style={{ fontSize: 13 }}>
                      {current.media_type === "image" ? "🖼️ Imagem anexada" : "🎵 Áudio anexado"}
                    </span>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      style={{ color: "#dc2626" }}
                      disabled={uploadBusy}
                      onClick={removeMedia}
                    >
                      Remover mídia
                    </button>
                    <label className="btn btn-secondary btn-sm" style={{ cursor: "pointer" }}>
                      Trocar
                      <input
                        ref={fileRef}
                        type="file"
                        accept="image/jpeg,image/png,image/webp,audio/mpeg,audio/ogg,audio/mp4,audio/aac,audio/webm"
                        style={{ display: "none" }}
                        onChange={onFilePicked}
                        disabled={uploadBusy}
                      />
                    </label>
                    {uploadBusy && <Spinner size={14} />}
                  </div>
                ) : (
                  <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <label className="btn btn-secondary btn-sm" style={{ cursor: "pointer" }}>
                      Escolher arquivo…
                      <input
                        ref={fileRef}
                        type="file"
                        accept="image/jpeg,image/png,image/webp,audio/mpeg,audio/ogg,audio/mp4,audio/aac,audio/webm"
                        style={{ display: "none" }}
                        onChange={onFilePicked}
                        disabled={uploadBusy}
                      />
                    </label>
                    <span style={{ fontSize: 12, color: "#6b7280" }}>
                      Imagem até 5MB ou áudio até 16MB.
                    </span>
                    {uploadBusy && <Spinner size={14} />}
                  </div>
                )}
              </div>

              {error && <div className="form-error" style={{ marginTop: 12 }}>{error}</div>}

              <div className="cliente-form-actions" style={{ marginTop: 16 }}>
                {current && (
                  <button type="button" className="btn btn-secondary" onClick={cancelEdit}>
                    Cancelar
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy || !draft.title.trim()}
                  onClick={save}
                >
                  {busy ? <Spinner size={14} /> : current ? "Salvar alterações" : "Criar oferta"}
                </button>
              </div>
            </section>

            {/* Preview WhatsApp */}
            <div>
              <div style={{ fontSize: 12, color: "#6b7280", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 8 }}>
                Pré-visualização
              </div>
              <WhatsappPreview
                title={draft.title}
                description={draft.description ?? ""}
                mediaUrl={previewMedia.url}
                mediaType={previewMedia.type}
                contextHeader="Antes de transferir, veja nossas ofertas:"
              />
            </div>
          </div>

          {/* Tabela */}
          <section className="cliente-card">
            <h3 style={{ marginTop: 0 }}>Ofertas cadastradas ({offers.length})</h3>
            {offers.length === 0 ? (
              <div className="cliente-empty">
                Nenhuma oferta cadastrada ainda. Adicione a primeira acima.
              </div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "1px solid #e5e7eb" }}>
                    <th style={{ padding: "8px 6px" }}>Título</th>
                    <th style={{ padding: "8px 6px" }}>Mídia</th>
                    <th style={{ padding: "8px 6px" }}>Descrição</th>
                    <th style={{ padding: "8px 6px" }}>Vigência</th>
                    <th style={{ padding: "8px 6px" }}>Prioridade</th>
                    <th style={{ padding: "8px 6px" }}>Ativa</th>
                    <th style={{ padding: "8px 6px" }}></th>
                  </tr>
                </thead>
                <tbody>
                  {offers.map((o) => (
                    <tr key={o.id} style={{ borderBottom: "1px solid #f3f4f6" }}>
                      <td style={{ padding: "10px 6px", fontWeight: 500 }}>{o.title}</td>
                      <td style={{ padding: "10px 6px" }}>
                        {o.media_type === "image" ? "🖼️" : o.media_type === "audio" ? "🎵" : "—"}
                      </td>
                      <td style={{ padding: "10px 6px", color: "#4b5563", maxWidth: 280 }}>
                        {o.description || "—"}
                      </td>
                      <td style={{ padding: "10px 6px", fontSize: 12, color: "#6b7280" }}>
                        {fmtDate(o.valid_from)} → {fmtDate(o.valid_until)}
                      </td>
                      <td style={{ padding: "10px 6px" }}>{o.priority}</td>
                      <td style={{ padding: "10px 6px" }}>
                        <Toggle checked={o.active} onChange={() => toggleActive(o)} />
                      </td>
                      <td style={{ padding: "10px 6px", textAlign: "right" }}>
                        <button className="btn btn-secondary btn-sm" onClick={() => startEdit(o)}>
                          Editar
                        </button>
                        <button
                          className="btn btn-secondary btn-sm"
                          style={{ marginLeft: 6, color: "#dc2626" }}
                          onClick={() => remove(o.id)}
                        >
                          Remover
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </PortalLayout>
  );
}
