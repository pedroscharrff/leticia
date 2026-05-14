import { useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import {
  getSalesConfig,
  updateSalesConfig,
  type SalesConfig,
} from "../api/portal";
import "./PortalVendas.css";

export function PortalVendas() {
  const [config, setConfig] = useState<SalesConfig | null>(null);
  const [required, setRequired] = useState<Set<string>>(new Set());
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [fallback, setFallback] = useState("");

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    getSalesConfig()
      .then((cfg) => {
        setConfig(cfg);
        setRequired(new Set(cfg.required_fields));
        setMaxAttempts(cfg.max_attempts);
        setFallback(cfg.fallback_message);
      })
      .catch(() => setError("Erro ao carregar configuração."))
      .finally(() => setLoading(false));
  }, []);

  function toggle(key: string) {
    const next = new Set(required);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setRequired(next);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSuccess("");
    setSaving(true);
    try {
      const updated = await updateSalesConfig({
        required_fields: Array.from(required),
        max_attempts: maxAttempts,
        fallback_message: fallback,
      });
      setConfig(updated);
      setSuccess("Configuração salva com sucesso.");
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Erro ao salvar.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <PortalLayout active="vendas">
        <div className="portal-loading"><Spinner size={32} /></div>
      </PortalLayout>
    );
  }

  return (
    <PortalLayout active="vendas">
      <div className="portal-page-header">
        <h1 className="portal-page-title">Configuração de Vendas</h1>
        <p className="portal-page-subtitle">
          Defina quais dados o agente vendedor precisa coletar antes de fechar
          um pedido, quantas vezes ele deve insistir e o que dizer caso o
          cliente não queira fornecer.
        </p>
      </div>

      <form className="vendas-form" onSubmit={handleSave}>
        <section className="vendas-card">
          <h2 className="vendas-section-title">Campos obrigatórios</h2>
          <p className="vendas-section-desc">
            Marque os campos que o agente DEVE ter no cadastro do cliente
            antes de chamar <code>criar_pedido</code>. Se algum estiver
            faltando, ele vai pedir ao cliente.
          </p>
          <div className="vendas-fields-grid">
            {config?.available_fields.map((f) => (
              <label
                key={f.key}
                className={`vendas-field ${required.has(f.key) ? "vendas-field--checked" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={required.has(f.key)}
                  onChange={() => toggle(f.key)}
                />
                <span>{f.label}</span>
              </label>
            ))}
          </div>
        </section>

        <section className="vendas-card">
          <h2 className="vendas-section-title">Política de tentativas</h2>
          <div className="form-group">
            <label className="form-label" htmlFor="max-attempts">
              Número máximo de tentativas
            </label>
            <input
              id="max-attempts"
              type="number"
              min={1}
              max={10}
              className="form-input vendas-input-narrow"
              value={maxAttempts}
              onChange={(e) => setMaxAttempts(Number(e.target.value))}
            />
            <p className="form-hint">
              Total de tentativas (somando todos os campos faltantes) antes
              de o agente desistir e enviar a mensagem de fallback.
            </p>
          </div>
        </section>

        <section className="vendas-card">
          <h2 className="vendas-section-title">Mensagem de fallback</h2>
          <p className="vendas-section-desc">
            Quando as tentativas se esgotarem, o agente envia esta mensagem
            literal e para de insistir naquela conversa.
          </p>
          <textarea
            className="form-textarea"
            rows={4}
            value={fallback}
            onChange={(e) => setFallback(e.target.value)}
            placeholder="Ex.: Para finalizar o pedido eu preciso desses dados…"
          />
        </section>

        {error && <div className="form-error">{error}</div>}
        {success && <div className="form-success">{success}</div>}

        <div className="vendas-actions">
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? <Spinner size={14} /> : "Salvar"}
          </button>
        </div>
      </form>
    </PortalLayout>
  );
}
