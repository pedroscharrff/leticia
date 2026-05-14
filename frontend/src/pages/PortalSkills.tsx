import { useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Toggle } from "../components/Toggle";
import { Spinner } from "../components/Spinner";
import { getSkills, updateSkill, type SkillConfig } from "../api/portal";
import "./PortalSkills.css";

const SKILL_LABELS: Record<string, { label: string; description: string }> = {
  farmaceutico:   { label: "Farmacêutico",    description: "Tira dúvidas sobre medicamentos, dosagens e contraindicações." },
  principio_ativo:{ label: "Princípio Ativo", description: "Identifica o princípio ativo de medicamentos e compara fórmulas." },
  genericos:      { label: "Genéricos",        description: "Sugere equivalentes genéricos e compara preços." },
  vendedor:       { label: "Vendedor",         description: "Apresenta produtos, promoções e auxilia na decisão de compra." },
  recuperador:    { label: "Recuperador",      description: "Reativa clientes inativos com ofertas personalizadas." },
};

const LLM_OPTIONS = [
  { value: "claude-haiku-4-5-20251001", label: "Claude Haiku (rápido)",       group: "Anthropic", provider: "anthropic" },
  { value: "claude-sonnet-4-6",         label: "Claude Sonnet (balanceado)",  group: "Anthropic", provider: "anthropic" },
  { value: "gpt-4o-mini",               label: "GPT-4o Mini (rápido)",        group: "OpenAI",    provider: "openai"    },
  { value: "gpt-4o",                    label: "GPT-4o (avançado)",            group: "OpenAI",    provider: "openai"    },
  { value: "gemini-2.0-flash",          label: "Gemini Flash (econômico)",    group: "Google",    provider: "google"    },
  { value: "llama3.2",                  label: "Llama 3.2 (local/Ollama)",    group: "Ollama",    provider: "ollama"    },
  { value: "mistral",                   label: "Mistral (local/Ollama)",      group: "Ollama",    provider: "ollama"    },
];

export function PortalSkills() {
  const [skills, setSkills] = useState<SkillConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    getSkills().then(setSkills).finally(() => setLoading(false));
  }, []);

  async function handleToggle(skill: SkillConfig) {
    setSaving(skill.skill_name);
    try {
      const updated = await updateSkill(skill.skill_name, { ativo: !skill.ativo });
      setSkills((prev) => prev.map((s) => s.skill_name === skill.skill_name ? updated : s));
    } finally {
      setSaving(null);
    }
  }

  async function handleModelChange(skill: SkillConfig, llm_model: string) {
    setSaving(skill.skill_name);
    const opt = LLM_OPTIONS.find((o) => o.value === llm_model);
    try {
      const updated = await updateSkill(skill.skill_name, {
        llm_model,
        llm_provider: opt?.provider ?? skill.llm_provider ?? "openai",
      });
      setSkills((prev) => prev.map((s) => s.skill_name === skill.skill_name ? updated : s));
    } finally {
      setSaving(null);
    }
  }

  if (loading) {
    return (
      <PortalLayout>
        <div className="portal-loading"><Spinner size={32} /></div>
      </PortalLayout>
    );
  }

  if (skills.length === 0) {
    return (
      <PortalLayout>
        <div className="portal-page-header">
          <h1 className="portal-page-title">Agentes de IA</h1>
        </div>
        <div className="portal-empty">
          <p>Nenhum agente configurado ainda.</p>
          <p className="portal-empty__hint">
            Solicite ao administrador que ative os agentes do seu plano.
          </p>
        </div>
      </PortalLayout>
    );
  }

  return (
    <PortalLayout>
      <div className="portal-page-header">
        <h1 className="portal-page-title">Agentes de IA</h1>
        <p className="portal-page-subtitle">
          Ative ou desative cada agente e escolha o modelo de linguagem.
        </p>
      </div>

      <div className="portal-skills-grid">
        {skills.map((skill) => {
          const meta = SKILL_LABELS[skill.skill_name] ?? {
            label: skill.skill_name,
            description: "",
          };
          const isSaving = saving === skill.skill_name;

          return (
            <div
              key={skill.skill_name}
              className={`portal-skill-card ${skill.ativo ? "portal-skill-card--active" : ""}`}
            >
              <div className="portal-skill-card__header">
                <div>
                  <h3 className="portal-skill-card__name">{meta.label}</h3>
                  <p className="portal-skill-card__desc">{meta.description}</p>
                </div>
                <div className="portal-skill-card__toggle">
                  {isSaving ? <Spinner size={16} /> : (
                    <Toggle
                      checked={skill.ativo}
                      onChange={() => handleToggle(skill)}
                    />
                  )}
                </div>
              </div>

              {skill.ativo && (
                <div className="portal-skill-card__model">
                  <label className="portal-skill-card__model-label" htmlFor={`model-${skill.skill_name}`}>
                    Modelo de IA
                  </label>
                  <select
                    id={`model-${skill.skill_name}`}
                    className="form-select"
                    value={skill.llm_model ?? ""}
                    onChange={(e) => handleModelChange(skill, e.target.value)}
                    disabled={isSaving}
                  >
                    {["Anthropic", "OpenAI", "Google", "Ollama"].map((group) => (
                      <optgroup key={group} label={group}>
                        {LLM_OPTIONS.filter((o) => o.group === group).map((opt) => (
                          <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </PortalLayout>
  );
}
