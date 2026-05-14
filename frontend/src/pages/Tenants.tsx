import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { GlobalNav } from "../components/GlobalNav";
import { SubNav } from "../components/SubNav";
import { Badge } from "../components/Badge";
import { Modal } from "../components/Modal";
import { Spinner } from "../components/Spinner";
import {
  listTenants, createTenant, toggleTenant, deleteTenant,
  type Tenant, type TenantCreate,
} from "../api/tenants";
import "./Tenants.css";

const PLAN_OPTIONS = [
  { value: "basic",      label: "Basic",      desc: "500 conversas/mês · Farmacêutico" },
  { value: "pro",        label: "Pro",         desc: "2.000 conversas/mês · 4 skills" },
  { value: "enterprise", label: "Enterprise",  desc: "Ilimitado · Todas as skills" },
];

function PlanBadge({ plan }: { plan: string }) {
  const variant = plan === "enterprise" ? "success" : plan === "pro" ? "primary" : "neutral";
  return <Badge variant={variant}>{plan.toUpperCase()}</Badge>;
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  return (
    <button className="copy-btn" onClick={copy} title="Copiar">
      {copied ? (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2 7l3 3 7-7" stroke="var(--color-success)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><rect x="4.5" y="1.5" width="8" height="8" rx="1.5" stroke="currentColor" strokeWidth="1.5"/><path d="M1.5 5.5h1v6a1 1 0 0 0 1 1h6v1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
      )}
    </button>
  );
}

export function Tenants() {
  const navigate = useNavigate();
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  // Create modal state
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<TenantCreate>({ name: "", callback_url: "", plan: "basic" });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [newTenant, setNewTenant] = useState<Tenant | null>(null);

  // Delete confirm
  const [confirmDelete, setConfirmDelete] = useState<Tenant | null>(null);

  async function load() {
    setLoading(true);
    const data = await listTenants().catch(() => []);
    setTenants(data);
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  const filtered = tenants.filter((t) =>
    t.name.toLowerCase().includes(search.toLowerCase()) ||
    t.plan.includes(search.toLowerCase())
  );

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreateError("");
    setCreating(true);
    try {
      const tenant = await createTenant(form);
      setNewTenant(tenant);
      setForm({ name: "", callback_url: "", plan: "basic" });
      setTenants((prev) => [tenant, ...prev]);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setCreateError(msg ?? "Erro ao criar farmácia. Tente novamente.");
    } finally {
      setCreating(false);
    }
  }

  function closeCreate() {
    setShowCreate(false);
    setNewTenant(null);
    setCreateError("");
    setForm({ name: "", callback_url: "", plan: "basic" });
  }

  async function handleToggle(tenant: Tenant) {
    const updated = await toggleTenant(tenant.id).catch(() => null);
    if (updated) {
      setTenants((prev) => prev.map((t) => (t.id === tenant.id ? updated : t)));
    }
  }

  async function handleDelete(tenant: Tenant) {
    await deleteTenant(tenant.id).catch(() => null);
    setTenants((prev) => prev.filter((t) => t.id !== tenant.id));
    setConfirmDelete(null);
  }

  return (
    <>
      <GlobalNav />
      <SubNav
        title="Farmácias"
        action={{ label: "+ Nova Farmácia", onClick: () => setShowCreate(true) }}
      />

      <main className="page-content tenants">

        {/* Search bar */}
        <div className="tenants__search-row">
          <div className="search-input-wrap">
            <svg className="search-icon" width="16" height="16" viewBox="0 0 16 16" fill="none">
              <circle cx="6.5" cy="6.5" r="5" stroke="var(--color-ink-muted-48)" strokeWidth="1.5"/>
              <path d="M10.5 10.5l3.5 3.5" stroke="var(--color-ink-muted-48)" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
            <input
              type="search"
              className="search-input"
              placeholder="Buscar por nome ou plano…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <p className="tenants__count">
            {filtered.length} farmácia{filtered.length !== 1 ? "s" : ""}
          </p>
        </div>

        {/* Tenant cards grid */}
        {loading ? (
          <div style={{ display: "flex", justifyContent: "center", padding: 64 }}>
            <Spinner size={32} />
          </div>
        ) : filtered.length === 0 ? (
          <div className="tenants__empty">
            <p>Nenhuma farmácia encontrada.</p>
            <button className="btn-primary" onClick={() => setShowCreate(true)}>
              Criar primeira farmácia
            </button>
          </div>
        ) : (
          <div className="tenants__grid">
            {filtered.map((t) => (
              <article
                key={t.id}
                className={`tenant-card ${!t.active ? "tenant-card--inactive" : ""}`}
              >
                <div className="tenant-card__top">
                  <div>
                    <h3 className="tenant-card__name">{t.name}</h3>
                    <p className="tenant-card__schema">{t.schema_name}</p>
                  </div>
                  <div className="tenant-card__badges">
                    <PlanBadge plan={t.plan} />
                    <Badge variant={t.active ? "success" : "neutral"}>
                      {t.active ? "Ativo" : "Inativo"}
                    </Badge>
                  </div>
                </div>

                <div className="tenant-card__key-row">
                  <span className="tenant-card__key-label">API Key</span>
                  <code className="tenant-card__key">{t.api_key.slice(0, 24)}…</code>
                  <CopyButton value={t.api_key} />
                </div>

                <div className="tenant-card__key-row">
                  <span className="tenant-card__key-label">Callback</span>
                  <span className="tenant-card__callback" title={t.callback_url}>
                    {t.callback_url.length > 40 ? t.callback_url.slice(0, 40) + "…" : t.callback_url}
                  </span>
                </div>

                <p className="tenant-card__date">
                  Criado em {new Date(t.created_at).toLocaleDateString("pt-BR")}
                </p>

                <div className="tenant-card__actions">
                  <button
                    className="btn-primary"
                    style={{ fontSize: 13, padding: "7px 16px" }}
                    onClick={() => navigate(`/tenants/${t.id}`)}
                  >
                    Gerenciar
                  </button>
                  <button
                    className="btn-secondary"
                    style={{ fontSize: 13, padding: "7px 16px" }}
                    onClick={() => handleToggle(t)}
                  >
                    {t.active ? "Pausar" : "Reativar"}
                  </button>
                  <button
                    className="btn-danger"
                    style={{ fontSize: 13 }}
                    onClick={() => setConfirmDelete(t)}
                  >
                    Excluir
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </main>

      {/* ── Create modal ─────────────────────────────────────────────── */}
      <Modal open={showCreate} title="Nova Farmácia" onClose={closeCreate} width={520}>
        {newTenant ? (
          <div className="create-success">
            <div className="create-success__icon">
              <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
                <circle cx="16" cy="16" r="16" fill="var(--color-success-bg)"/>
                <path d="M10 16l4 4 8-8" stroke="var(--color-success)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
            <h3 className="create-success__title">Farmácia criada!</h3>
            <p className="create-success__name">{newTenant.name}</p>

            <div className="create-success__field">
              <span className="create-success__field-label">API Key</span>
              <div className="create-success__key-row">
                <code className="create-success__key">{newTenant.api_key}</code>
                <CopyButton value={newTenant.api_key} />
              </div>
              <p className="create-success__warning">
                Salve a API Key agora — ela não será exibida novamente.
              </p>
            </div>

            <div className="form-actions">
              <button className="btn-primary" onClick={() => { closeCreate(); navigate(`/tenants/${newTenant.id}`); }}>
                Configurar skills
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleCreate}>
            <div className="form-group">
              <label htmlFor="c-name">Nome da farmácia</label>
              <input
                id="c-name"
                type="text"
                className="form-input"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Farmácia São João"
                required
                minLength={2}
              />
            </div>

            <div className="form-group">
              <label htmlFor="c-callback">URL de Callback (WhatsApp gateway)</label>
              <input
                id="c-callback"
                type="url"
                className="form-input"
                value={form.callback_url}
                onChange={(e) => setForm({ ...form, callback_url: e.target.value })}
                placeholder="https://waha.example.com/api/sendText"
                required
              />
            </div>

            <div className="form-group">
              <label>Plano</label>
              <div className="plan-selector">
                {PLAN_OPTIONS.map((opt) => (
                  <label key={opt.value} className={`plan-option ${form.plan === opt.value ? "plan-option--selected" : ""}`}>
                    <input
                      type="radio"
                      name="plan"
                      className="sr-only"
                      value={opt.value}
                      checked={form.plan === opt.value}
                      onChange={() => setForm({ ...form, plan: opt.value as TenantCreate["plan"] })}
                    />
                    <span className="plan-option__label">{opt.label}</span>
                    <span className="plan-option__desc">{opt.desc}</span>
                  </label>
                ))}
              </div>
            </div>

            {createError && <p className="form-error">{createError}</p>}

            <div className="form-actions">
              <button type="button" className="btn-secondary" onClick={closeCreate}>Cancelar</button>
              <button type="submit" className="btn-primary" disabled={creating || !form.name || !form.callback_url}>
                {creating ? <Spinner size={16} /> : null}
                {creating ? "Criando…" : "Criar Farmácia"}
              </button>
            </div>
          </form>
        )}
      </Modal>

      {/* ── Delete confirm ───────────────────────────────────────────── */}
      <Modal open={!!confirmDelete} title="Excluir Farmácia" onClose={() => setConfirmDelete(null)} width={400}>
        <p style={{ color: "var(--color-ink-muted-80)", marginBottom: "var(--space-lg)" }}>
          Tem certeza que deseja desativar <strong>{confirmDelete?.name}</strong>?
          Esta ação pode ser revertida posteriormente.
        </p>
        <div className="form-actions">
          <button className="btn-secondary" onClick={() => setConfirmDelete(null)}>Cancelar</button>
          <button className="btn-danger" onClick={() => confirmDelete && handleDelete(confirmDelete)}>
            Confirmar Exclusão
          </button>
        </div>
      </Modal>
    </>
  );
}
