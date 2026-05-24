/**
 * PortalRecursos — Hub de capacidades plugáveis (feature flags).
 *
 * Cada capability é um cartão com toggle. Clique no cartão abre um drawer com
 * descrição completa, formulário de configuração (gerado a partir do
 * config_schema do backend) e histórico.
 *
 * Lógica de bloqueio: capabilities com `blockers` (plano, secret, dependência)
 * mostram badge e o toggle fica desabilitado, com explicação no tooltip.
 */
import { useEffect, useMemo, useState } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Toggle } from "../components/Toggle";
import { Modal } from "../components/Modal";
import { Spinner } from "../components/Spinner";
import {
  listCapabilities,
  updateCapability,
  type Capability,
  type CapabilityCategory,
  type ConfigSchemaProperty,
} from "../api/capabilities";
import "./PortalRecursos.css";

const CATEGORIES: { key: CapabilityCategory | "all"; label: string }[] = [
  { key: "all",                label: "Todas" },
  { key: "atendimento",        label: "Atendimento" },
  { key: "vendas",             label: "Vendas" },
  { key: "pagamentos_entrega", label: "Pagamentos & Entrega" },
  { key: "analise",            label: "Análise" },
  { key: "inteligencia",       label: "Inteligência" },
];

const CATEGORY_LABEL: Record<CapabilityCategory, string> = {
  atendimento:        "Atendimento",
  vendas:             "Vendas",
  pagamentos_entrega: "Pagamentos & Entrega",
  analise:            "Análise",
  inteligencia:       "Inteligência",
};

// Ícone simples renderizado como emoji para evitar dependência extra.
const ICON_MAP: Record<string, string> = {
  brain:               "🧠",
  "mouse-pointer-click": "🖱️",
  "shopping-cart":     "🛒",
  package:             "📦",
  "shopping-bag":      "🛍️",
  pill:                "💊",
  truck:               "🚚",
  "qr-code":           "📱",
  "bar-chart-3":       "📊",
  crown:               "👑",
  "shield-alert":      "🛡️",
  sparkles:            "✨",
};

const STATUS_LABEL: Record<Capability["status"], string> = {
  ga:           "",
  beta:         "BETA",
  experimental: "EXP",
};


// ── Helpers de markdown leve (sem dependência externa) ──────────────────────

function renderMarkdown(md: string): { __html: string } {
  // Suporta: **bold**, *italic*, # / ## headings, > blockquote, listas e quebra de linha.
  const lines = md.split(/\n/);
  const out: string[] = [];
  let inList = false;
  for (const raw of lines) {
    let line = raw;
    line = line.replace(/`([^`]+)`/g, "<code>$1</code>");
    line = line.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    line = line.replace(/\*([^*]+)\*/g, "<em>$1</em>");

    if (/^\s*$/.test(line)) {
      if (inList) { out.push("</ul>"); inList = false; }
      out.push("<br/>");
      continue;
    }
    if (/^### /.test(line))      { if (inList) { out.push("</ul>"); inList = false; } out.push(`<h4>${line.slice(4)}</h4>`); continue; }
    if (/^## /.test(line))       { if (inList) { out.push("</ul>"); inList = false; } out.push(`<h3>${line.slice(3)}</h3>`); continue; }
    if (/^# /.test(line))        { if (inList) { out.push("</ul>"); inList = false; } out.push(`<h2>${line.slice(2)}</h2>`); continue; }
    if (/^>\s+/.test(line))      { if (inList) { out.push("</ul>"); inList = false; } out.push(`<blockquote>${line.replace(/^>\s+/, "")}</blockquote>`); continue; }
    if (/^[-*]\s+/.test(line))   { if (!inList) { out.push("<ul>"); inList = true; } out.push(`<li>${line.replace(/^[-*]\s+/, "")}</li>`); continue; }
    out.push(`<p>${line}</p>`);
  }
  if (inList) out.push("</ul>");
  return { __html: out.join("\n") };
}


// ── Form gerado a partir do JSON Schema ─────────────────────────────────────

interface SchemaFormProps {
  schema: Record<string, ConfigSchemaProperty>;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  disabled?: boolean;
}

function SchemaForm({ schema, value, onChange, disabled }: SchemaFormProps) {
  function set(key: string, v: unknown) {
    onChange({ ...value, [key]: v });
  }

  return (
    <div className="recursos-form">
      {Object.entries(schema).map(([key, prop]) => {
        const id = `cfg-${key}`;
        const current = value[key] ?? prop.default ?? "";

        if (prop.type === "boolean") {
          return (
            <div className="recursos-form__row" key={key}>
              <label htmlFor={id} className="recursos-form__label">
                {prop.title ?? key}
              </label>
              <Toggle
                checked={!!current}
                onChange={(v) => set(key, v)}
                disabled={disabled}
              />
            </div>
          );
        }

        if (prop.enum && prop.enum.length) {
          return (
            <div className="recursos-form__row recursos-form__row--col" key={key}>
              <label htmlFor={id} className="recursos-form__label">
                {prop.title ?? key}
              </label>
              <select
                id={id}
                className="recursos-form__input"
                value={String(current)}
                onChange={(e) => set(key, e.target.value)}
                disabled={disabled}
              >
                {prop.enum.map((opt) => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
              </select>
            </div>
          );
        }

        if (prop.type === "integer" || prop.type === "number") {
          return (
            <div className="recursos-form__row recursos-form__row--col" key={key}>
              <label htmlFor={id} className="recursos-form__label">
                {prop.title ?? key}
                {prop.minimum !== undefined && prop.maximum !== undefined && (
                  <span className="recursos-form__hint">
                    {" "}({prop.minimum}–{prop.maximum})
                  </span>
                )}
              </label>
              <input
                id={id}
                type="number"
                className="recursos-form__input"
                value={String(current)}
                min={prop.minimum}
                max={prop.maximum}
                step={prop.type === "integer" ? 1 : "any"}
                onChange={(e) => {
                  const n = prop.type === "integer"
                    ? parseInt(e.target.value, 10)
                    : parseFloat(e.target.value);
                  set(key, isNaN(n) ? "" : n);
                }}
                disabled={disabled}
              />
            </div>
          );
        }

        return (
          <div className="recursos-form__row recursos-form__row--col" key={key}>
            <label htmlFor={id} className="recursos-form__label">
              {prop.title ?? key}
            </label>
            <input
              id={id}
              type="text"
              className="recursos-form__input"
              value={String(current)}
              onChange={(e) => set(key, e.target.value)}
              disabled={disabled}
            />
          </div>
        );
      })}
    </div>
  );
}


// ── Página ──────────────────────────────────────────────────────────────────

export function PortalRecursos() {
  const [items, setItems]       = useState<Capability[]>([]);
  const [loading, setLoading]   = useState(true);
  const [filter, setFilter]     = useState<CapabilityCategory | "all">("all");
  const [search, setSearch]     = useState("");
  const [onlyAvailable, setOnlyAvailable] = useState(false);
  const [saving, setSaving]     = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [draftConfig, setDraftConfig] = useState<Record<string, unknown>>({});
  const [error, setError]       = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    try {
      const data = await listCapabilities();
      setItems(data);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((it) => {
      if (filter !== "all" && it.category !== filter) return false;
      if (onlyAvailable && !it.available) return false;
      if (q && !it.name.toLowerCase().includes(q) && !it.short_desc.toLowerCase().includes(q))
        return false;
      return true;
    });
  }, [items, filter, search, onlyAvailable]);

  const stats = useMemo(() => {
    const total      = items.length;
    const active     = items.filter((it) => it.enabled).length;
    const blocked    = items.filter((it) => !it.available).length;
    return { total, active, blocked };
  }, [items]);

  const selected = items.find((it) => it.key === selectedKey) || null;

  function openDrawer(it: Capability) {
    setSelectedKey(it.key);
    setDraftConfig({ ...it.default_config, ...it.config });
    setError(null);
  }

  async function handleToggle(it: Capability, next: boolean) {
    if (next && !it.available) return;
    setSaving(it.key);
    setError(null);
    try {
      const updated = await updateCapability(it.key, { enabled: next });
      setItems((prev) => prev.map((p) => (p.key === it.key ? updated : p)));
    } catch (e: unknown) {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        "Não foi possível atualizar.";
      setError(msg);
    } finally {
      setSaving(null);
    }
  }

  async function handleSaveConfig() {
    if (!selected) return;
    setSaving(selected.key);
    setError(null);
    try {
      const updated = await updateCapability(selected.key, {
        config: draftConfig,
      });
      setItems((prev) => prev.map((p) => (p.key === selected.key ? updated : p)));
      setSelectedKey(null);
    } catch (e: unknown) {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        "Não foi possível salvar.";
      setError(msg);
    } finally {
      setSaving(null);
    }
  }

  if (loading) {
    return (
      <PortalLayout active="recursos">
        <div className="portal-loading"><Spinner size={32} /></div>
      </PortalLayout>
    );
  }

  return (
    <PortalLayout active="recursos">
      <header className="portal-page-header">
        <h1 className="portal-page-title">Recursos do seu Robô</h1>
        <p className="portal-page-subtitle">
          Plugue e desplugue capacidades de forma independente. Cada recurso é
          opcional — ative só o que faz sentido para o seu negócio.
        </p>
      </header>

      {/* Stats topo */}
      <div className="recursos-stats">
        <div className="recursos-stats__card">
          <span className="recursos-stats__num">{stats.active}</span>
          <span className="recursos-stats__lbl">Ativos</span>
        </div>
        <div className="recursos-stats__card">
          <span className="recursos-stats__num">{stats.total - stats.active - stats.blocked}</span>
          <span className="recursos-stats__lbl">Disponíveis para ativar</span>
        </div>
        <div className="recursos-stats__card">
          <span className="recursos-stats__num">{stats.blocked}</span>
          <span className="recursos-stats__lbl">Bloqueados</span>
        </div>
        <div className="recursos-stats__card">
          <span className="recursos-stats__num">{stats.total}</span>
          <span className="recursos-stats__lbl">Total no catálogo</span>
        </div>
      </div>

      {/* Filtros */}
      <div className="recursos-filters">
        <div className="recursos-filters__chips">
          {CATEGORIES.map((c) => (
            <button
              key={c.key}
              className={`recursos-chip${filter === c.key ? " recursos-chip--active" : ""}`}
              onClick={() => setFilter(c.key)}
              type="button"
            >
              {c.label}
            </button>
          ))}
        </div>
        <div className="recursos-filters__right">
          <input
            type="search"
            className="recursos-search"
            placeholder="Buscar recurso…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <label className="recursos-checkbox">
            <input
              type="checkbox"
              checked={onlyAvailable}
              onChange={(e) => setOnlyAvailable(e.target.checked)}
            />
            <span>Disponível no meu plano</span>
          </label>
        </div>
      </div>

      {error && !selected && <div className="recursos-error">{error}</div>}

      {/* Grid */}
      {filtered.length === 0 ? (
        <div className="portal-empty">
          <p>Nenhum recurso encontrado com esses filtros.</p>
        </div>
      ) : (
        <div className="recursos-grid">
          {filtered.map((it) => {
            const icon = ICON_MAP[it.icon] ?? "✨";
            const isSaving = saving === it.key;
            const statusBadge = STATUS_LABEL[it.status];
            return (
              <article
                key={it.key}
                className={`recurso-card${it.enabled ? " recurso-card--on" : ""}${!it.available ? " recurso-card--blocked" : ""}`}
              >
                <header className="recurso-card__top">
                  <div className="recurso-card__icon">{icon}</div>
                  <div className="recurso-card__heading">
                    <h3 className="recurso-card__title">
                      {it.name}
                      {statusBadge && (
                        <span className="recurso-card__status">{statusBadge}</span>
                      )}
                    </h3>
                    <span className="recurso-card__category">
                      {CATEGORY_LABEL[it.category]}
                    </span>
                  </div>
                  <div className="recurso-card__toggle">
                    <Toggle
                      checked={it.enabled}
                      onChange={(v) => handleToggle(it, v)}
                      disabled={isSaving || (!it.enabled && !it.available)}
                    />
                  </div>
                </header>

                <p className="recurso-card__desc">{it.short_desc}</p>

                {it.impact_label && (
                  <div className="recurso-card__impact">
                    📈 {it.impact_label}
                  </div>
                )}

                {/* Bloqueios */}
                {it.blockers.length > 0 && (
                  <div className="recurso-card__blockers">
                    {it.blockers.map((b, i) => (
                      <span
                        key={i}
                        className={`recurso-blocker recurso-blocker--${b.type}`}
                        title={b.message}
                      >
                        {b.type === "plan"       && "🔒 "}
                        {b.type === "secret"     && "🔌 "}
                        {b.type === "dependency" && "🔗 "}
                        {b.message}
                      </span>
                    ))}
                  </div>
                )}

                <footer className="recurso-card__footer">
                  <button
                    type="button"
                    className="recurso-card__more"
                    onClick={() => openDrawer(it)}
                  >
                    Saber mais e configurar →
                  </button>
                  {it.updated_at && (
                    <span className="recurso-card__updated">
                      Atualizado {new Date(it.updated_at).toLocaleDateString("pt-BR")}
                    </span>
                  )}
                </footer>
              </article>
            );
          })}
        </div>
      )}

      {/* Drawer / Modal de detalhes */}
      {selected && (
        <Modal
          open={!!selected}
          title={selected.name}
          onClose={() => setSelectedKey(null)}
          width={680}
        >
          <div className="recursos-drawer">
            <div className="recursos-drawer__meta">
              <span className={`recursos-drawer__plan recursos-drawer__plan--${selected.min_plan}`}>
                Plano: {selected.min_plan}
              </span>
              {selected.impact_label && (
                <span className="recursos-drawer__impact">📈 {selected.impact_label}</span>
              )}
              <span className={`recursos-drawer__state${selected.enabled ? " recursos-drawer__state--on" : ""}`}>
                {selected.enabled ? "● Ativo" : "○ Inativo"}
              </span>
            </div>

            {selected.blockers.length > 0 && (
              <div className="recursos-drawer__blockers">
                <strong>Pré-requisitos pendentes:</strong>
                <ul>
                  {selected.blockers.map((b, i) => (
                    <li key={i}>{b.message}</li>
                  ))}
                </ul>
              </div>
            )}

            <div
              className="recursos-drawer__md"
              dangerouslySetInnerHTML={renderMarkdown(selected.long_desc)}
            />

            {selected.config_schema?.properties && Object.keys(selected.config_schema.properties).length > 0 && (
              <section className="recursos-drawer__config">
                <h4>Configuração</h4>
                <SchemaForm
                  schema={selected.config_schema.properties}
                  value={draftConfig}
                  onChange={setDraftConfig}
                  disabled={saving === selected.key}
                />
              </section>
            )}

            {error && <div className="recursos-error">{error}</div>}

            <footer className="recursos-drawer__actions">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setSelectedKey(null)}
              >
                Cancelar
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={handleSaveConfig}
                disabled={saving === selected.key}
              >
                {saving === selected.key ? "Salvando…" : "Salvar configuração"}
              </button>
            </footer>
          </div>
        </Modal>
      )}
    </PortalLayout>
  );
}
