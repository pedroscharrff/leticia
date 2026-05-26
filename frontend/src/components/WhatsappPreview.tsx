/**
 * WhatsappPreview — mockup de bolha de chat WhatsApp.
 * Renderiza UMA mensagem do "bot" (lado direito, fundo verde claro) com:
 *   - imagem ou áudio opcional no topo,
 *   - texto/caption embaixo.
 *
 * Channel-agnóstico no design: o look é WhatsApp porque é o canal atual,
 * mas o componente apenas mostra "como o cliente vai ver" — a entrega real
 * é decidida no backend pelo canal ativo.
 */

interface WhatsappPreviewProps {
  title?:      string;
  description?: string;
  mediaUrl?:   string | null;
  mediaType?:  "image" | "audio" | null;
  /** Opcional: mostra um header de contexto acima da bolha (ex.: "Antes de transferir, veja nossas ofertas:") */
  contextHeader?: string;
}

export function WhatsappPreview({
  title, description, mediaUrl, mediaType, contextHeader,
}: WhatsappPreviewProps) {
  const caption = [title?.trim(), description?.trim()]
    .filter(Boolean)
    .join(": ");
  const hasAny = !!(caption || mediaUrl);

  return (
    <div
      style={{
        background: "#e5ddd5",
        backgroundImage:
          "radial-gradient(rgba(255,255,255,0.5) 1px, transparent 1px)",
        backgroundSize: "10px 10px",
        borderRadius: 12,
        padding: 16,
        minHeight: 240,
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      {contextHeader && (
        <div
          style={{
            alignSelf: "center",
            background: "rgba(255, 250, 220, 0.95)",
            color: "#52401f",
            fontSize: 11,
            padding: "4px 10px",
            borderRadius: 6,
            boxShadow: "0 1px 0 rgba(0,0,0,0.05)",
          }}
        >
          {contextHeader}
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <div
          style={{
            background: "#dcf8c6",
            borderRadius: "12px 12px 2px 12px",
            padding: 6,
            maxWidth: "85%",
            boxShadow: "0 1px 1px rgba(0,0,0,0.13)",
            position: "relative",
          }}
        >
          {!hasAny && (
            <div style={{ color: "#9ca3af", padding: "10px 12px", fontStyle: "italic", fontSize: 13 }}>
              Pré-visualização — preencha título, descrição ou anexe uma mídia.
            </div>
          )}

          {mediaUrl && mediaType === "image" && (
            <img
              src={mediaUrl}
              alt=""
              style={{
                width: "100%",
                maxWidth: 280,
                borderRadius: 8,
                display: "block",
              }}
            />
          )}

          {mediaUrl && mediaType === "audio" && (
            <audio
              controls
              src={mediaUrl}
              style={{ width: 260, display: "block", marginTop: 2 }}
            />
          )}

          {caption && (
            <div
              style={{
                padding: mediaUrl ? "6px 10px 4px" : "8px 10px 4px",
                fontSize: 14,
                color: "#111",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {caption}
            </div>
          )}

          <div
            style={{
              fontSize: 10,
              color: "#667781",
              textAlign: "right",
              padding: "0 10px 4px",
            }}
          >
            agora ✓✓
          </div>
        </div>
      </div>
    </div>
  );
}
