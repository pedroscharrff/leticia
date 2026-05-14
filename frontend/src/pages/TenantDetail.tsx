import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { GlobalNav } from "../components/GlobalNav";
import { SubNav } from "../components/SubNav";
import { Badge } from "../components/Badge";
import { Toggle } from "../components/Toggle";
import { Spinner } from "../components/Spinner";
import {
  getTenant, listSkills, updateSkill, seedSkills, getUsage,
  type Tenant, type SkillConfig, type UsageMetric,
} from "../api/tenants";
import {
  listTenantUsers, createTenantUser, type TenantUser,
} from "../api/portal";
import { Modal } from "../components/Modal";
import "./TenantDetail.css";

const SKILL_META: Record<string, { label: string; desc: string; plan: string }> = {
  farmaceutico:   { label: "Farmacêutico",        desc: "Bulas, posologia, contraindicações",            plan: "Todos" },
  principio_ativo:{ label: "Princípio Ativo",     desc: "Substituições e equivalências farmacológicas",  plan: "Pro+" },
  genericos:      { label: "Genéricos",            desc: "Equivalentes ANVISA, comparação de preço",     plan: "Pro+" },
  vendedor:       { label: "Vendedor",             desc: "Upsell, cross-sell, aumento de ticket médio",   plan: "Pro+" },
  recuperador:    { label: "Recuperador",          desc: "Carrinho abandonado, reativação de sessão",     plan: "Enterprise" },
};

const LLM_OPTIONS = [
  { value: "claude-sonnet-4-6",         label: "Claude Sonnet 4.6",      provider: "anthropic", group: "Anthropic" },
  { value: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5",       provider: "anthropic", group: "Anthropic" },
  { value: "gpt-4o",                    label: "GPT-4o",                  provider: "openai",    group: "OpenAI"    },
  { value: "gpt-4o-mini",              label: "GPT-4o Mini",             provider: "openai",    group: "OpenAI"    },
  { value: "gemini-2.0-flash",          label: "Gemini Flash 2.0",       provider: "google",    group: "Google"    },
  { value: "llama3.2",                  label: "Llama 3.2 (Ollama)",     provider: "ollama",    group: "Ollama"    },
  { value: "mistral",                   label: "Mistral (Ollama)",        provider: "ollama",    group: "Ollama"    },
];

function SkillCard({ skill, tenantId, onChange }: {
  skill: SkillConfig;
  tenantId: string;
  onChange: (updated: SkillConfig) => void;
}) {
  const meta = SKILL_META[skill.skill_name] ?? { label: skill.skill_name, desc: "", plan: "" };
  const [saving, setSaving] = useState(false);
  const [editModel, setEditModel] = useState(skill.llm_model ?? "");

  async function handleToggle(val: boolean) {
    setSaving(true);
    try {
      const updated = await updateSkill(tenantId, skill.skill_name, { ativo: val });
      onChange(updated);
    } finally {
      setSaving(false);
    }
  }

  async function handleModelChange(model: string) {
    setEditModel(model);
    const opt = LLM_OPTIONS.find((o) => o.value === model);
    setSaving(true);
    try {
      const updated = await updateSkill(tenantId, skill.skill_name, {
        llm_model: model,
        llm_provider: opt?.provider ?? skill.llm_provider ?? "anthropic",
      });
      onChange(updated);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={`skill-card ${skill.ativo ? "skill-card--active" : "skill-card--inactive"}`}>
      <div className="skill-card__top">
        <div className="skill-card__info">
          <h3 className="skill-card__name">{meta.label}</h3>
          <p className="skill-card__desc">{meta.desc}</p>
          <Badge variant="neutral">{meta.plan}</Badge>
        </div>
        <div className="skill-card__toggle">
          {saving && <Spinner size={16} />}
          <Toggle
            checked={skill.ativo}
            onChange={handleToggle}
            disabled={saving}
            label={skill.ativo ? "Ativa" : "Inativa"}
          />
        </div>
      </div>

      {skill.ativo && (
        <div className="skill-card__config">
          <label className="skill-card__config-label">Modelo LLM</label>
          <select
            className="form-select"
            value={editModel}
            onChange={(e) => handleModelChange(e.target.value)}
            disabled={saving}
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
}

export function TenantDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [skills, setSkills] = useState<SkillConfig[]>([]);
  const [usage, setUsage] = useState<UsageMetric[]>([]);
  const [loading, setLoading] = useState(true);
  const [seeding, setSeeding] = useState(false);
  const [users, setUsers] = useState<TenantUser[]>([]);
  const [showUserModal, setShowUserModal] = useState(false);
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [newUserName, setNewUserName] = useState("");
  const [savingUser, setSavingUser] = useState(false);
  const [userError, setUserError] = useState("");

  useEffect(() => {
    if (!id) return;
    Promise.all([
      getTenant(id),
      listSkills(id),
      getUsage(id),
      listTenantUsers(id).catch(() => [] as TenantUser[]),
    ])
      .then(([t, s, u, usr]) => { setTenant(t); setSkills(s); setUsage(u); setUsers(usr); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [id]);

  async function handleSeed() {
    if (!id) return;
    setSeeding(true);
    await seedSkills(id);
    const s = await listSkills(id);
    setSkills(s);
    setSeeding(false);
  }

  async function handleCreateUser() {
    if (!id) return;
    setUserError("");
    setSavingUser(true);
    try {
      const user = await createTenantUser(id, {
        email: newUserEmail,
        password: newUserPassword,
        name: newUserName || undefined,
      });
      setUsers((prev) => [...prev, user]);
      setShowUserModal(false);
      setNewUserEmail("");
      setNewUserPassword("");
      setNewUserName("");
    } catch {
      setUserError("Erro ao criar usuário. Verifique se o e-mail já está cadastrado.");
    } finally {
      setSavingUser(false);
    }
  }

  function updateSkillInList(updated: SkillConfig) {
    setSkills((prev) => prev.map((s) => s.skill_name === updated.skill_name ? updated : s));
  }

  if (loading) return (
    <>
      <GlobalNav /><SubNav title="Carregando…" />
      <main className="page-content" style={{ display: "flex", justifyContent: "center", paddingTop: 80 }}>
        <Spinner size={32} />
      </main>
    </>
  );

  if (!tenant) return (
    <>
      <GlobalNav /><SubNav title="Não encontrado" action={{ label: "Voltar", onClick: () => navigate("/tenants") }} />
      <main className="page-content"><p>Tenant não encontrado.</p></main>
    </>
  );

  const totalConversations = usage.reduce((s, m) => s + m.conversations, 0);
  const activeSkillsCount = skills.filter((s) => s.ativo).length;

  return (
    <>
      <GlobalNav />
      <SubNav
        title={tenant.name}
        action={{ label: "← Farmácias", onClick: () => navigate("/tenants") }}
      />

      <div style={{ display: "flex", justifyContent: "flex-end", padding: "12px 24px 0", gap: 8 }}>
        <button className="btn btn--secondary" onClick={() => navigate(`/tenants/${tenant.id}/persona`)}>
          Persona &amp; Prompts
        </button>
      </div>

      <main className="page-content tenant-detail">

        {/* ── Overview tile ────────────────────────────────────────── */}
        <section className="tenant-detail__overview tile-dark">
          <div className="tenant-detail__overview-grid">
            <div className="overview-stat">
              <span className="overview-stat__label">Plano</span>
              <span className="overview-stat__value">{tenant.plan.toUpperCase()}</span>
            </div>
            <div className="overview-stat">
              <span className="overview-stat__label">Status</span>
              <span className="overview-stat__value">{tenant.active ? "Ativo" : "Inativo"}</span>
            </div>
            <div className="overview-stat">
              <span className="overview-stat__label">Skills ativas</span>
              <span className="overview-stat__value">{activeSkillsCount}</span>
            </div>
            <div className="overview-stat">
              <span className="overview-stat__label">Conversas</span>
              <span className="overview-stat__value">{totalConversations.toLocaleString("pt-BR")}</span>
            </div>
          </div>
        </section>

        {/* ── Usage chart ─────────────────────────────────────────── */}
        {usage.length > 0 && (
          <section className="tenant-detail__section">
            <h2 className="tenant-detail__section-title">Conversas por Mês</h2>
            <div className="tenant-detail__chart">
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={[...usage].reverse()} margin={{ top: 0, right: 0, left: -24, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-divider-soft)" vertical={false} />
                  <XAxis
                    dataKey="month"
                    tickFormatter={(v: string) => v.slice(0, 7)}
                    tick={{ fontSize: 11, fill: "var(--color-ink-muted-48)", fontFamily: "var(--font-text)" }}
                    axisLine={false} tickLine={false}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: "var(--color-ink-muted-48)", fontFamily: "var(--font-text)" }}
                    axisLine={false} tickLine={false}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "var(--color-canvas)",
                      border: "1px solid var(--color-hairline)",
                      borderRadius: "var(--radius-sm)",
                      fontFamily: "var(--font-text)",
                      fontSize: 12,
                      boxShadow: "none",
                    }}
                    cursor={{ fill: "var(--color-divider-soft)" }}
                  />
                  <Bar dataKey="conversations" fill="var(--color-primary)" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </section>
        )}

        {/* ── Skills section ───────────────────────────────────────── */}
        <section className="tenant-detail__section">
          <div className="tenant-detail__section-header">
            <h2 className="tenant-detail__section-title">Skills de Atendimento</h2>
            <button
              className="btn-utility"
              onClick={handleSeed}
              disabled={seeding}
              title="Repopula skills com os padrões do plano"
            >
              {seeding ? <Spinner size={14} /> : null}
              {seeding ? "Aplicando…" : "Aplicar padrões do plano"}
            </button>
          </div>

          {skills.length === 0 ? (
            <div className="tenant-detail__empty">
              <p>Nenhuma skill configurada.</p>
              <button className="btn-primary" onClick={handleSeed} disabled={seeding}>
                {seeding ? <Spinner size={16} /> : null}
                Aplicar padrões do plano {tenant.plan}
              </button>
            </div>
          ) : (
            <div className="skills-grid">
              {skills.map((skill) => (
                <SkillCard
                  key={skill.skill_name}
                  skill={skill}
                  tenantId={tenant.id}
                  onChange={updateSkillInList}
                />
              ))}
            </div>
          )}
        </section>

        {/* ── Usuários do portal ───────────────────────────────────── */}
        <section className="tenant-detail__section">
          <div className="tenant-detail__section-header">
            <h2 className="tenant-detail__section-title">Usuários do Portal</h2>
            <button className="btn-primary" onClick={() => setShowUserModal(true)}>
              + Criar usuário
            </button>
          </div>

          {users.length === 0 ? (
            <div className="tenant-detail__empty">
              <p>Nenhum usuário criado.</p>
              <p style={{ fontSize: 13, color: "var(--color-ink-muted-48)", marginTop: 4 }}>
                Crie um usuário para que o proprietário acesse o portal em <strong>/portal/login</strong>.
              </p>
            </div>
          ) : (
            <div className="users-list">
              {users.map((u) => (
                <div key={u.id} className="user-row">
                  <div className="user-row__avatar">{u.email[0].toUpperCase()}</div>
                  <div className="user-row__info">
                    <span className="user-row__name">{u.name ?? u.email}</span>
                    <span className="user-row__email">{u.email}</span>
                  </div>
                  <span className={`user-row__status ${u.active ? "user-row__status--active" : ""}`}>
                    {u.active ? "Ativo" : "Inativo"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ── API info ─────────────────────────────────────────────── */}
        <section className="tenant-detail__section tile-parchment tenant-detail__api-info">
          <h2 className="tenant-detail__section-title">Integração</h2>
          <div className="api-info-grid">
            <div className="api-info-item">
              <span className="api-info-item__label">Endpoint Webhook</span>
              <code className="api-info-item__code">
                POST /webhook/{tenant.id}
              </code>
            </div>
            <div className="api-info-item">
              <span className="api-info-item__label">Header obrigatório</span>
              <code className="api-info-item__code">X-Api-Key: {tenant.api_key.slice(0, 20)}…</code>
            </div>
            <div className="api-info-item">
              <span className="api-info-item__label">Callback URL</span>
              <code className="api-info-item__code">{tenant.callback_url}</code>
            </div>
            <div className="api-info-item">
              <span className="api-info-item__label">Schema PostgreSQL</span>
              <code className="api-info-item__code">{tenant.schema_name}</code>
            </div>
          </div>
        </section>
      </main>

      {showUserModal && (
        <Modal open title="Criar usuário do portal" onClose={() => setShowUserModal(false)}>
          <div className="form-group">
            <label>Nome (opcional)</label>
            <input
              className="form-input"
              value={newUserName}
              onChange={(e) => setNewUserName(e.target.value)}
              placeholder="João Silva"
            />
          </div>
          <div className="form-group">
            <label>E-mail *</label>
            <input
              className="form-input"
              type="email"
              value={newUserEmail}
              onChange={(e) => setNewUserEmail(e.target.value)}
              placeholder="joao@farmacia.com"
              required
            />
          </div>
          <div className="form-group">
            <label>Senha *</label>
            <input
              className="form-input"
              type="password"
              value={newUserPassword}
              onChange={(e) => setNewUserPassword(e.target.value)}
              placeholder="••••••••"
              required
            />
          </div>
          {userError && <p className="form-error">{userError}</p>}
          <div className="form-actions">
            <button className="btn-secondary" onClick={() => setShowUserModal(false)}>
              Cancelar
            </button>
            <button
              className="btn-primary"
              onClick={handleCreateUser}
              disabled={savingUser || !newUserEmail || !newUserPassword}
            >
              {savingUser ? <Spinner size={16} /> : null}
              {savingUser ? "Criando…" : "Criar usuário"}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
