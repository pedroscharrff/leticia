import { useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import {
  getLLMConfig, updateLLMConfig, removeLLMKey,
  type LLMConfig, type LLMConfigUpdate,
} from "../api/portal";
import "./PortalLLMConfig.css";

const PROVIDERS = [
  { value: "anthropic", label: "Anthropic (Claude)" },
  { value: "openai",    label: "OpenAI (GPT)"       },
  { value: "google",    label: "Google (Gemini)"    },
  { value: "ollama",    label: "Ollama (self-hosted)" },
];

const MODEL_OPTIONS: Record<string, { value: string; label: string }[]> = {
  anthropic: [
    { value: "claude-haiku-4-5-20251001", label: "Claude Haiku (rápido/barato)" },
    { value: "claude-sonnet-4-6",         label: "Claude Sonnet (balanceado)"  },
  ],
  openai: [
    { value: "gpt-4o-mini", label: "GPT-4o Mini (rápido/barato)" },
    { value: "gpt-4o",      label: "GPT-4o (avançado)"           },
  ],
  google: [
    { value: "gemini-2.0-flash", label: "Gemini Flash (rápido)" },
  ],
  ollama: [
    { value: "llama3.2", label: "Llama 3.2" },
    { value: "mistral",  label: "Mistral"   },
    { value: "phi3",     label: "Phi-3"     },
  ],
};

export function PortalLLMConfig() {
  const [config, setConfig]   = useState<LLMConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving]   = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError]     = useState("");
  const [success, setSuccess] = useState("");

  // Form state
  const [mode, setMode]               = useState<"credits" | "byok">("credits");
  const [provider, setProvider]       = useState("openai");
  const [apiKey, setApiKey]           = useState("");
  const [ollamaUrl, setOllamaUrl]     = useState("http://localhost:11434");
  const [orchModel, setOrchModel]     = useState("");
  const [analystModel, setAnalystModel] = useState("");
  const [skillModel, setSkillModel]   = useState("");

  useEffect(() => {
    getLLMConfig()
      .then((cfg) => {
        setConfig(cfg);
        setMode(cfg.mode);
        if (cfg.provider) setProvider(cfg.provider);
        if (cfg.ollama_base_url) setOllamaUrl(cfg.ollama_base_url);
        if (cfg.orchestrator_model) setOrchModel(cfg.orchestrator_model);
        if (cfg.analyst_model) setAnalystModel(cfg.analyst_model);
        if (cfg.skill_model) setSkillModel(cfg.skill_model);
      })
      .finally(() => setLoading(false));
  }, []);

  const modelOptions = MODEL_OPTIONS[provider] ?? [];

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSuccess("");
    setSaving(true);
    try {
      const payload: LLMConfigUpdate = { mode };
      if (mode === "byok") {
        payload.provider = provider;
        if (apiKey) payload.api_key = apiKey;
        if (provider === "ollama") payload.ollama_base_url = ollamaUrl;
        if (orchModel) payload.orchestrator_model = orchModel;
        if (analystModel) payload.analyst_model = analystModel;
        if (skillModel) payload.skill_model = skillModel;
      }
      const updated = await updateLLMConfig(payload);
      setConfig(updated);
      setApiKey(""); // clear after save
      setSuccess(mode === "credits"
        ? "Configurado para usar os créditos da plataforma."
        : "Sua API Key foi salva com sucesso.");
    } catch {
      setError("Erro ao salvar configuração. Verifique os dados e tente novamente.");
    } finally {
      setSaving(false);
    }
  }

  async function handleRemoveKey() {
    if (!confirm("Remover sua API Key e voltar para créditos da plataforma?")) return;
    setRemoving(true);
    try {
      await removeLLMKey();
      const updated = await getLLMConfig();
      setConfig(updated);
      setMode("credits");
      setSuccess("API Key removida. Usando créditos da plataforma.");
    } catch {
      setError("Erro ao remover chave.");
    } finally {
      setRemoving(false);
    }
  }

  if (loading) {
    return <PortalLayout><div className="portal-loading"><Spinner size={32} /></div></PortalLayout>;
  }

  return (
    <PortalLayout>
      <div className="portal-page-header">
        <h1 className="portal-page-title">Configuração de IA</h1>
        <p className="portal-page-subtitle">
          Use seus próprios créditos de API ou os créditos incluídos no seu plano.
        </p>
      </div>

      {/* Status atual */}
      {config && (
        <div className={`llm-status-card llm-status-card--${config.mode}`}>
          <div className="llm-status-card__icon">
            {config.mode === "credits" ? "🏦" : "🔑"}
          </div>
          <div className="llm-status-card__info">
            <strong>{config.mode === "credits" ? "Usando créditos da plataforma" : "Usando sua própria API Key"}</strong>
            {config.mode === "byok" && (
              <span>
                {config.provider?.toUpperCase()} · {config.has_api_key ? "Chave configurada ✓" : "Sem chave"}
              </span>
            )}
          </div>
          {config.mode === "byok" && config.has_api_key && (
            <button
              className="llm-remove-btn"
              onClick={handleRemoveKey}
              disabled={removing}
            >
              {removing ? <Spinner size={14} /> : "Remover chave"}
            </button>
          )}
        </div>
      )}

      <form className="llm-config-form" onSubmit={handleSave}>

        {/* Modo */}
        <div className="llm-mode-selector">
          <button
            type="button"
            className={`llm-mode-btn ${mode === "credits" ? "llm-mode-btn--active" : ""}`}
            onClick={() => setMode("credits")}
          >
            <span className="llm-mode-btn__icon">🏦</span>
            <span className="llm-mode-btn__title">Créditos da Plataforma</span>
            <span className="llm-mode-btn__desc">Use os créditos incluídos no seu plano. Sem configuração extra.</span>
          </button>
          <button
            type="button"
            className={`llm-mode-btn ${mode === "byok" ? "llm-mode-btn--active" : ""}`}
            onClick={() => setMode("byok")}
          >
            <span className="llm-mode-btn__icon">🔑</span>
            <span className="llm-mode-btn__title">Minha API Key (BYOK)</span>
            <span className="llm-mode-btn__desc">Use sua própria chave de API. Sem limite de mensagens da plataforma.</span>
          </button>
        </div>

        {/* Configuração BYOK */}
        {mode === "byok" && (
          <div className="llm-byok-fields">
            <div className="form-group">
              <label className="form-label">Provedor</label>
              <select
                className="form-select"
                value={provider}
                onChange={(e) => { setProvider(e.target.value); setOrchModel(""); setAnalystModel(""); setSkillModel(""); }}
              >
                {PROVIDERS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>

            {provider !== "ollama" && (
              <div className="form-group">
                <label className="form-label">
                  API Key {config?.has_api_key && <span className="llm-key-saved">(já configurada — deixe em branco para manter)</span>}
                </label>
                <input
                  type="password"
                  className="form-input"
                  placeholder={provider === "anthropic" ? "sk-ant-..." : provider === "openai" ? "sk-proj-..." : "AIza..."}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  autoComplete="new-password"
                />
              </div>
            )}

            {provider === "ollama" && (
              <div className="form-group">
                <label className="form-label">URL do Ollama</label>
                <input
                  type="url"
                  className="form-input"
                  placeholder="http://localhost:11434"
                  value={ollamaUrl}
                  onChange={(e) => setOllamaUrl(e.target.value)}
                />
                <p className="form-hint">Endereço do servidor Ollama rodando localmente ou em VPS.</p>
              </div>
            )}

            {/* Modelos por nó */}
            <div className="llm-models-grid">
              <div className="form-group">
                <label className="form-label">Modelo — Agentes (Skills)</label>
                <select className="form-select" value={skillModel} onChange={(e) => setSkillModel(e.target.value)}>
                  <option value="">Padrão do provedor</option>
                  {modelOptions.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Modelo — Orquestrador</label>
                <select className="form-select" value={orchModel} onChange={(e) => setOrchModel(e.target.value)}>
                  <option value="">Padrão do provedor</option>
                  {modelOptions.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Modelo — Analista de Qualidade</label>
                <select className="form-select" value={analystModel} onChange={(e) => setAnalystModel(e.target.value)}>
                  <option value="">Padrão do provedor</option>
                  {modelOptions.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
              </div>
            </div>
          </div>
        )}

        {error   && <p className="llm-feedback llm-feedback--error">{error}</p>}
        {success && <p className="llm-feedback llm-feedback--success">{success}</p>}

        <div className="llm-form-actions">
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? <Spinner size={16} /> : "Salvar configuração"}
          </button>
        </div>
      </form>
    </PortalLayout>
  );
}
