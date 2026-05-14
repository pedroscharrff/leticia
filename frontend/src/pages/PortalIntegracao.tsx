import { useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import { getMe, type PortalMe } from "../api/portal";
import "./PortalIntegracao.css";

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  return (
    <div className="code-block">
      <pre><code>{code}</code></pre>
      <button className="code-block__copy" onClick={copy}>
        {copied ? "✓ Copiado" : "Copiar"}
      </button>
    </div>
  );
}

export function PortalIntegracao() {
  const [me, setMe] = useState<PortalMe | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getMe().then(setMe).finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <PortalLayout><div className="portal-loading"><Spinner size={32} /></div></PortalLayout>;
  }

  const apiKey = me?.api_key ?? "SUA_API_KEY";
  const baseUrl = window.location.origin.replace(":5173", ":8000");

  const curlExample = `curl -X POST ${baseUrl}/webhook/${apiKey} \\
  -H "Content-Type: application/json" \\
  -d '{
    "phone": "5511999999999",
    "message": "Olá, tem dipirona?"
  }'`;

  const callbackExample = `POST ${me?.callback_url ?? "https://seu-sistema.com/callback"}
Content-Type: application/json

{
  "phone": "5511999999999",
  "message": "Olá! Sim, temos Dipirona Sódica 500mg...",
  "session_key": "5511999999999_abc123"
}`;

  return (
    <PortalLayout>
      <div className="portal-page-header">
        <h1 className="portal-page-title">Integração</h1>
        <p className="portal-page-subtitle">
          Como conectar seu sistema de WhatsApp ao atendente inteligente.
        </p>
      </div>

      <div className="integracao-steps">

        <div className="integracao-step">
          <div className="integracao-step__num">1</div>
          <div className="integracao-step__content">
            <h2 className="integracao-step__title">Sua API Key</h2>
            <p className="integracao-step__desc">
              Essa chave identifica sua farmácia. Use em todas as requisições ao webhook.
            </p>
            <CodeBlock code={apiKey} />
          </div>
        </div>

        <div className="integracao-step">
          <div className="integracao-step__num">2</div>
          <div className="integracao-step__content">
            <h2 className="integracao-step__title">Enviar mensagem para o agente</h2>
            <p className="integracao-step__desc">
              Quando seu cliente enviar uma mensagem no WhatsApp, repasse para o webhook:
            </p>
            <CodeBlock code={curlExample} />
          </div>
        </div>

        <div className="integracao-step">
          <div className="integracao-step__num">3</div>
          <div className="integracao-step__content">
            <h2 className="integracao-step__title">Receber a resposta</h2>
            <p className="integracao-step__desc">
              O agente processa e chama seu <strong>Callback URL</strong> com a resposta:
            </p>
            <CodeBlock code={callbackExample} />
            <p className="integracao-step__desc" style={{ marginTop: 12 }}>
              Seu sistema deve responder com <code>HTTP 200</code> para confirmar o recebimento.
            </p>
          </div>
        </div>

        <div className="integracao-step">
          <div className="integracao-step__num">4</div>
          <div className="integracao-step__content">
            <h2 className="integracao-step__title">Integração com Evolution API</h2>
            <p className="integracao-step__desc">
              Configure o webhook da sua instância Evolution API para apontar para:
            </p>
            <CodeBlock code={`${baseUrl}/webhook/${apiKey}`} />
            <p className="integracao-step__desc" style={{ marginTop: 12 }}>
              Configure o campo <code>webhook.url</code> no painel da Evolution API com a URL acima.
              O sistema responderá automaticamente usando seu Callback URL configurado.
            </p>
          </div>
        </div>

      </div>
    </PortalLayout>
  );
}
