import { useEffect, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import {
  getPersona,
  updatePersona,
  listAgentPrompts,
  updateAgentPrompt,
  clearAgentPrompt,
  type Persona,
  type AgentPrompt,
} from "../api/portal";
import "./PortalPersona.css";

const TONE_OPTIONS = [
  { value: "amigavel",     label: "Amigável" },
  { value: "formal",       label: "Formal" },
  { value: "informal",     label: "Informal" },
  { value: "profissional", label: "Profissional" },
  { value: "divertido",    label: "Divertido" },
];
const FORMALITY_OPTIONS = [
  { value: "voce",   label: "Você" },
  { value: "tu",     label: "Tu" },
  { value: "senhor", label: "Senhor(a)" },
];
const EMOJI_OPTIONS = [
  { value: "none",     label: "Sem emojis" },
  { value: "light",    label: "Leve (1 por msg)" },
  { value: "moderate", label: "Moderado (até 2)" },
  { value: "heavy",    label: "Bastante" },
];
const LENGTH_OPTIONS = [
  { value: "short",  label: "Curtas" },
  { value: "medium", label: "Médias" },
  { value: "long",   label: "Detalhadas" },
];
const GENDER_OPTIONS = [
  { value: "feminino",  label: "Feminino" },
  { value: "masculino", label: "Masculino" },
  { value: "neutro",    label: "Neutro" },
];

export function PortalPersona() {
  const [persona, setPersona] = useState<Persona | null>(null);
  const [prompts, setPrompts] = useState<AgentPrompt[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState<"persona" | "prompts">("persona");
  const [openSkill, setOpenSkill] = useState<string | null>(null);
  const [draftPrompt, setDraftPrompt] = useState<{ system_prompt: string; extras: string }>({
    system_prompt: "",
    extras: "",
  });

  useEffect(() => {
    Promise.all([getPersona(), listAgentPrompts()])
      .then(([p, l]) => {
        setPersona(p);
        setPrompts(l);
      })
      .finally(() => setLoading(false));
  }, []);

  async function handleSavePersona() {
    if (!persona) return;
    setSaving(true);
    try {
      const updated = await updatePersona(persona);
      setPersona(updated);
    } finally {
      setSaving(false);
    }
  }

  function startEditingPrompt(p: AgentPrompt) {
    setOpenSkill(p.skill_name);
    setDraftPrompt({
      system_prompt: p.system_prompt ?? "",
      extras: p.extra_instructions ?? "",
    });
  }

  async function savePrompt(skill: string) {
    setSaving(true);
    try {
      await updateAgentPrompt(skill, {
        system_prompt: draftPrompt.system_prompt.trim() || null,
        extra_instructions: draftPrompt.extras.trim() || null,
      });
      setPrompts(await listAgentPrompts());
      setOpenSkill(null);
    } finally {
      setSaving(false);
    }
  }

  async function resetPrompt(skill: string) {
    if (!confirm(`Restaurar o prompt padrão de "${skill}"?`)) return;
    setSaving(true);
    try {
      await clearAgentPrompt(skill);
      setPrompts(await listAgentPrompts());
      setOpenSkill(null);
    } finally {
      setSaving(false);
    }
  }

  if (loading || !persona) {
    return (
      <PortalLayout>
        <div className="portal-loading"><Spinner size={32} /></div>
      </PortalLayout>
    );
  }

  return (
    <PortalLayout>
      <div className="portal-page-header">
        <h1 className="portal-page-title">Personalização dos Agentes</h1>
        <p className="portal-page-subtitle">
          Dê uma identidade aos atendentes virtuais e ajuste como eles falam com seus clientes.
        </p>
      </div>

      <div className="persona-tabs">
        <button
          className={`persona-tab ${activeTab === "persona" ? "persona-tab--active" : ""}`}
          onClick={() => setActiveTab("persona")}
        >Persona</button>
        <button
          className={`persona-tab ${activeTab === "prompts" ? "persona-tab--active" : ""}`}
          onClick={() => setActiveTab("prompts")}
        >Prompts dos Agentes</button>
      </div>

      {activeTab === "persona" && (
        <div className="persona-form">
          <section className="persona-section">
            <h2>Identidade</h2>
            <div className="persona-grid">
              <label>
                Nome do(a) atendente
                <input
                  type="text"
                  className="form-input"
                  value={persona.agent_name}
                  placeholder="Ex.: Letícia"
                  onChange={(e) => setPersona({ ...persona, agent_name: e.target.value })}
                />
              </label>
              <label>
                Gênero
                <select
                  className="form-select"
                  value={persona.agent_gender}
                  onChange={(e) => setPersona({ ...persona, agent_gender: e.target.value as Persona["agent_gender"] })}
                >
                  {GENDER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label>
                Nome da farmácia
                <input
                  type="text"
                  className="form-input"
                  value={persona.pharmacy_name ?? ""}
                  onChange={(e) => setPersona({ ...persona, pharmacy_name: e.target.value })}
                />
              </label>
              <label>
                Slogan (opcional)
                <input
                  type="text"
                  className="form-input"
                  value={persona.pharmacy_tagline ?? ""}
                  onChange={(e) => setPersona({ ...persona, pharmacy_tagline: e.target.value })}
                />
              </label>
            </div>
            <label className="persona-full">
              Bio do agente (1–3 frases sobre a personalidade)
              <textarea
                className="form-input"
                rows={3}
                value={persona.persona_bio ?? ""}
                placeholder="Ex.: Letícia é carinhosa, paciente e adora dar dicas práticas de saúde."
                onChange={(e) => setPersona({ ...persona, persona_bio: e.target.value })}
              />
            </label>
          </section>

          <section className="persona-section">
            <h2>Tom e estilo</h2>
            <div className="persona-grid">
              <label>
                Tom
                <select
                  className="form-select"
                  value={persona.tone}
                  onChange={(e) => setPersona({ ...persona, tone: e.target.value as Persona["tone"] })}
                >
                  {TONE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label>
                Tratamento
                <select
                  className="form-select"
                  value={persona.formality}
                  onChange={(e) => setPersona({ ...persona, formality: e.target.value as Persona["formality"] })}
                >
                  {FORMALITY_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label>
                Uso de emojis
                <select
                  className="form-select"
                  value={persona.emoji_usage}
                  onChange={(e) => setPersona({ ...persona, emoji_usage: e.target.value as Persona["emoji_usage"] })}
                >
                  {EMOJI_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label>
                Tamanho das respostas
                <select
                  className="form-select"
                  value={persona.response_length}
                  onChange={(e) => setPersona({ ...persona, response_length: e.target.value as Persona["response_length"] })}
                >
                  {LENGTH_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
            </div>
            <label className="persona-full">
              Saudação preferida
              <input
                type="text"
                className="form-input"
                value={persona.greeting_template ?? ""}
                placeholder="Ex.: Oi! Aqui é a Letícia da Drogaria São Paulo 💊 Como posso te ajudar?"
                onChange={(e) => setPersona({ ...persona, greeting_template: e.target.value })}
              />
            </label>
            <label className="persona-full">
              Assinatura
              <input
                type="text"
                className="form-input"
                value={persona.signature ?? ""}
                placeholder="Ex.: — Letícia | Drogaria São Paulo"
                onChange={(e) => setPersona({ ...persona, signature: e.target.value })}
              />
            </label>
            <label className="persona-full">
              Bordões da marca (separe por vírgula)
              <input
                type="text"
                className="form-input"
                value={(persona.catchphrases ?? []).join(", ")}
                placeholder="Ex.: Cuidar de você é o nosso remédio favorito"
                onChange={(e) => setPersona({
                  ...persona,
                  catchphrases: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                })}
              />
            </label>
          </section>

          <section className="persona-section">
            <h2>Regras de conduta</h2>
            <label className="persona-full">
              Instruções extras (sempre aplicadas)
              <textarea
                className="form-input"
                rows={4}
                value={persona.custom_instructions ?? ""}
                placeholder="Ex.: Sempre confirmar nome e CPF antes de finalizar pedido. Lembrar política de troca em 7 dias."
                onChange={(e) => setPersona({ ...persona, custom_instructions: e.target.value })}
              />
            </label>
            <label className="persona-full">
              Tópicos proibidos
              <textarea
                className="form-input"
                rows={3}
                value={persona.forbidden_topics ?? ""}
                placeholder="Ex.: Não falar sobre política. Nunca comparar preço com farmácias concorrentes."
                onChange={(e) => setPersona({ ...persona, forbidden_topics: e.target.value })}
              />
            </label>
          </section>

          <section className="persona-section">
            <h2>Contexto da farmácia</h2>
            <div className="persona-grid">
              <label>
                Horário de funcionamento
                <input
                  type="text"
                  className="form-input"
                  value={persona.business_hours ?? ""}
                  placeholder="Seg-Sex 8h-22h | Sáb 8h-18h"
                  onChange={(e) => setPersona({ ...persona, business_hours: e.target.value })}
                />
              </label>
              <label>
                Localização
                <input
                  type="text"
                  className="form-input"
                  value={persona.location ?? ""}
                  onChange={(e) => setPersona({ ...persona, location: e.target.value })}
                />
              </label>
              <label>
                Site
                <input
                  type="text"
                  className="form-input"
                  value={persona.website ?? ""}
                  onChange={(e) => setPersona({ ...persona, website: e.target.value })}
                />
              </label>
              <label>
                Instagram
                <input
                  type="text"
                  className="form-input"
                  value={persona.instagram ?? ""}
                  onChange={(e) => setPersona({ ...persona, instagram: e.target.value })}
                />
              </label>
            </div>
            <label className="persona-full">
              Política de entregas
              <textarea
                className="form-input"
                rows={2}
                value={persona.delivery_info ?? ""}
                onChange={(e) => setPersona({ ...persona, delivery_info: e.target.value })}
              />
            </label>
            <label className="persona-full">
              Formas de pagamento
              <textarea
                className="form-input"
                rows={2}
                value={persona.payment_methods ?? ""}
                onChange={(e) => setPersona({ ...persona, payment_methods: e.target.value })}
              />
            </label>
          </section>

          <div className="persona-actions">
            <button className="btn btn--primary" disabled={saving} onClick={handleSavePersona}>
              {saving ? "Salvando…" : "Salvar persona"}
            </button>
          </div>
        </div>
      )}

      {activeTab === "prompts" && (
        <div className="prompts-list">
          <p className="persona-help">
            Cada agente tem um prompt padrão. Você pode adicionar instruções extras
            (recomendado) ou substituir totalmente o prompt (avançado).
          </p>
          {prompts.map((p) => {
            const isOpen = openSkill === p.skill_name;
            const baseDefault = p.catalog_default_prompt ?? p.code_default_prompt ?? "";
            return (
              <div key={p.skill_name} className={`prompt-card ${p.has_override ? "prompt-card--override" : ""}`}>
                <div className="prompt-card__head">
                  <div>
                    <h3>{p.display_name}</h3>
                    <span className="prompt-card__badge">
                      {p.has_override ? "Customizado" : "Padrão"}
                    </span>
                  </div>
                  <button className="btn btn--secondary" onClick={() => isOpen ? setOpenSkill(null) : startEditingPrompt(p)}>
                    {isOpen ? "Fechar" : "Editar"}
                  </button>
                </div>

                {isOpen && (
                  <div className="prompt-card__body">
                    <details>
                      <summary>Ver prompt padrão</summary>
                      <pre className="prompt-default">{baseDefault || "(sem prompt cadastrado)"}</pre>
                    </details>

                    <label>
                      Instruções extras (recomendado)
                      <textarea
                        className="form-input"
                        rows={4}
                        value={draftPrompt.extras}
                        placeholder="Ex.: Sempre mencionar nosso programa de fidelidade ao final."
                        onChange={(e) => setDraftPrompt({ ...draftPrompt, extras: e.target.value })}
                      />
                    </label>

                    <label>
                      Substituir prompt completo (avançado, deixe vazio para usar o padrão)
                      <textarea
                        className="form-input prompt-textarea"
                        rows={10}
                        value={draftPrompt.system_prompt}
                        onChange={(e) => setDraftPrompt({ ...draftPrompt, system_prompt: e.target.value })}
                      />
                    </label>

                    <div className="prompt-card__actions">
                      {p.has_override && (
                        <button className="btn btn--ghost" onClick={() => resetPrompt(p.skill_name)}>
                          Restaurar padrão
                        </button>
                      )}
                      <button className="btn btn--primary" disabled={saving} onClick={() => savePrompt(p.skill_name)}>
                        {saving ? "Salvando…" : "Salvar"}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </PortalLayout>
  );
}
