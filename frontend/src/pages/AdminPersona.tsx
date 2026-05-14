import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { GlobalNav } from "../components/GlobalNav";
import { Spinner } from "../components/Spinner";
import {
  adminGetPersona,
  adminUpdatePersona,
  adminListAgentPrompts,
  adminUpdateAgentPrompt,
  adminClearAgentPrompt,
  type Persona,
  type AgentPrompt,
} from "../api/portal";
import { getTenant, type Tenant } from "../api/tenants";
import "./PortalPersona.css";

export function AdminPersona() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [persona, setPersona] = useState<Persona | null>(null);
  const [prompts, setPrompts] = useState<AgentPrompt[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [openSkill, setOpenSkill] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ system_prompt: string; extras: string }>({ system_prompt: "", extras: "" });

  useEffect(() => {
    if (!id) return;
    Promise.all([getTenant(id), adminGetPersona(id), adminListAgentPrompts(id)])
      .then(([t, p, l]) => {
        setTenant(t);
        setPersona(p);
        setPrompts(l);
      })
      .finally(() => setLoading(false));
  }, [id]);

  async function savePersona() {
    if (!persona) return;
    setSaving(true);
    try {
      const updated = await adminUpdatePersona(id, persona);
      setPersona(updated);
    } finally {
      setSaving(false);
    }
  }

  function openPrompt(p: AgentPrompt) {
    setOpenSkill(p.skill_name);
    setDraft({ system_prompt: p.system_prompt ?? "", extras: p.extra_instructions ?? "" });
  }

  async function savePrompt(skill: string) {
    setSaving(true);
    try {
      await adminUpdateAgentPrompt(id, skill, {
        system_prompt: draft.system_prompt.trim() || null,
        extra_instructions: draft.extras.trim() || null,
      });
      setPrompts(await adminListAgentPrompts(id));
      setOpenSkill(null);
    } finally {
      setSaving(false);
    }
  }

  async function resetPrompt(skill: string) {
    if (!confirm(`Restaurar prompt padrão de "${skill}"?`)) return;
    setSaving(true);
    try {
      await adminClearAgentPrompt(id, skill);
      setPrompts(await adminListAgentPrompts(id));
      setOpenSkill(null);
    } finally {
      setSaving(false);
    }
  }

  if (loading || !persona) {
    return (
      <>
        <GlobalNav />
        <div className="portal-loading"><Spinner size={32} /></div>
      </>
    );
  }

  return (
    <>
      <GlobalNav />
      <div style={{ maxWidth: 980, margin: "0 auto", padding: "32px 24px" }}>
        <button className="btn btn--ghost" onClick={() => navigate(`/tenants/${id}`)}>
          ← Voltar
        </button>
        <div className="portal-page-header">
          <h1 className="portal-page-title">Persona &amp; Prompts</h1>
          <p className="portal-page-subtitle">
            Editando como <strong>super admin</strong> — Tenant: {tenant?.name ?? id}
          </p>
        </div>

        <h2 style={{ fontSize: 16, marginTop: 24 }}>Persona do agente</h2>
        <div className="persona-form">
          <section className="persona-section">
            <div className="persona-grid">
              <label>
                Nome
                <input className="form-input" value={persona.agent_name}
                  onChange={(e) => setPersona({ ...persona, agent_name: e.target.value })} />
              </label>
              <label>
                Farmácia
                <input className="form-input" value={persona.pharmacy_name ?? ""}
                  onChange={(e) => setPersona({ ...persona, pharmacy_name: e.target.value })} />
              </label>
              <label>
                Tom
                <input className="form-input" value={persona.tone}
                  onChange={(e) => setPersona({ ...persona, tone: e.target.value as Persona["tone"] })} />
              </label>
              <label>
                Tratamento
                <input className="form-input" value={persona.formality}
                  onChange={(e) => setPersona({ ...persona, formality: e.target.value as Persona["formality"] })} />
              </label>
            </div>
            <label className="persona-full">
              Bio
              <textarea className="form-input" rows={3} value={persona.persona_bio ?? ""}
                onChange={(e) => setPersona({ ...persona, persona_bio: e.target.value })} />
            </label>
            <label className="persona-full">
              Instruções extras
              <textarea className="form-input" rows={4} value={persona.custom_instructions ?? ""}
                onChange={(e) => setPersona({ ...persona, custom_instructions: e.target.value })} />
            </label>
            <label className="persona-full">
              Tópicos proibidos
              <textarea className="form-input" rows={3} value={persona.forbidden_topics ?? ""}
                onChange={(e) => setPersona({ ...persona, forbidden_topics: e.target.value })} />
            </label>
          </section>
          <div className="persona-actions">
            <button className="btn btn--primary" disabled={saving} onClick={savePersona}>
              {saving ? "Salvando…" : "Salvar persona"}
            </button>
          </div>
        </div>

        <h2 style={{ fontSize: 16, marginTop: 32 }}>Prompts dos agentes</h2>
        <div className="prompts-list">
          {prompts.map((p) => {
            const isOpen = openSkill === p.skill_name;
            const baseDefault = p.catalog_default_prompt ?? p.code_default_prompt ?? "";
            return (
              <div key={p.skill_name} className={`prompt-card ${p.has_override ? "prompt-card--override" : ""}`}>
                <div className="prompt-card__head">
                  <div>
                    <h3>{p.display_name}</h3>
                    <span className="prompt-card__badge">{p.has_override ? "Customizado" : "Padrão"}</span>
                  </div>
                  <button className="btn btn--secondary"
                    onClick={() => isOpen ? setOpenSkill(null) : openPrompt(p)}>
                    {isOpen ? "Fechar" : "Editar"}
                  </button>
                </div>
                {isOpen && (
                  <div className="prompt-card__body">
                    <details>
                      <summary>Prompt padrão</summary>
                      <pre className="prompt-default">{baseDefault || "(vazio)"}</pre>
                    </details>
                    <label>
                      Instruções extras
                      <textarea className="form-input" rows={4} value={draft.extras}
                        onChange={(e) => setDraft({ ...draft, extras: e.target.value })} />
                    </label>
                    <label>
                      Substituir prompt completo
                      <textarea className="form-input prompt-textarea" rows={12}
                        value={draft.system_prompt}
                        onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value })} />
                    </label>
                    <div className="prompt-card__actions">
                      {p.has_override && (
                        <button className="btn btn--ghost" onClick={() => resetPrompt(p.skill_name)}>
                          Restaurar padrão
                        </button>
                      )}
                      <button className="btn btn--primary" disabled={saving}
                        onClick={() => savePrompt(p.skill_name)}>
                        {saving ? "Salvando…" : "Salvar"}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
