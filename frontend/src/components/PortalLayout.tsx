import { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import "./PortalLayout.css";

interface Props {
  children: React.ReactNode;
  active?: string;
}

interface NavItem {
  path: string;
  label: string;
  icon: string;
  key: string;
}

interface NavSection {
  id: string;
  title: string;
  icon: string;
  items: NavItem[];
}

/**
 * Navegação agrupada em 5 seções temáticas. Cada seção pode ser colapsada.
 * O grupo da página ativa abre automaticamente.
 */
const NAV_SECTIONS: NavSection[] = [
  {
    id: "inicio",
    title: "Início",
    icon: "🏠",
    items: [
      { path: "/portal/dashboard", label: "Dashboard", icon: "⊞", key: "dashboard" },
    ],
  },
  {
    id: "atendimento",
    title: "Atendimento",
    icon: "💬",
    items: [
      { path: "/portal/persona",  label: "Persona & Voz",       icon: "✨", key: "persona"  },
      { path: "/portal/skills",   label: "Agentes Ativos",      icon: "⚙",  key: "skills"   },
      { path: "/portal/clientes", label: "Memória de Clientes", icon: "🧠", key: "clientes" },
    ],
  },
  {
    id: "vendas",
    title: "Vendas",
    icon: "🛒",
    items: [
      { path: "/portal/estoque",      label: "Produtos & Estoque",   icon: "📦", key: "estoque"     },
      { path: "/portal/recursos",     label: "Recursos do seu Robô", icon: "🧩", key: "recursos"    },
      { path: "/portal/entregas",     label: "Frete & Entrega",      icon: "🚚", key: "entregas"    },
      { path: "/portal/pagamentos",   label: "Pagamentos (PIX)",     icon: "📱", key: "pagamentos"  },
      { path: "/portal/recuperacao",  label: "Recuperação Automática",icon: "🔁", key: "recuperacao"},
      { path: "/portal/pedidos",      label: "Pedidos",              icon: "🧾", key: "pedidos"     },
    ],
  },
  {
    id: "analise",
    title: "Análise",
    icon: "📊",
    items: [
      { path: "/portal/vendas", label: "Vendas & KPIs", icon: "📈", key: "vendas"  },
      { path: "/portal/logs",   label: "Conversas",     icon: "💬", key: "logs"    },
      { path: "/portal/traces", label: "Traces",        icon: "🔍", key: "traces"  },
    ],
  },
  {
    id: "config",
    title: "Configuração",
    icon: "⚙️",
    items: [
      { path: "/portal/canais",    label: "Canais & Integrações", icon: "📡", key: "canais"    },
      { path: "/portal/configuracoes/notificacoes", label: "Notificações de Status", icon: "🔔", key: "notificacoes" },
      { path: "/portal/ia-config", label: "Modelos de IA",         icon: "🤖", key: "ia-config" },
      { path: "/portal/billing",   label: "Plano & Cobrança",      icon: "💳", key: "billing"   },
    ],
  },
];

function findSectionForActive(active: string | undefined, pathname: string): string {
  for (const s of NAV_SECTIONS) {
    for (const i of s.items) {
      if (active ? active === i.key : pathname === i.path) {
        return s.id;
      }
    }
  }
  return NAV_SECTIONS[0].id;
}

export function PortalLayout({ children, active }: Props) {
  const { logout, userEmail } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const initialOpen = findSectionForActive(active, location.pathname);
  // Início + a seção da página corrente começam abertas.
  const [open, setOpen] = useState<Record<string, boolean>>(() => ({
    inicio:      true,
    atendimento: initialOpen === "atendimento",
    vendas:      initialOpen === "vendas",
    analise:     initialOpen === "analise",
    config:      initialOpen === "config",
  }));

  const toggle = (id: string) =>
    setOpen((prev) => ({ ...prev, [id]: !prev[id] }));

  const isActive = (item: NavItem) =>
    active ? active === item.key : location.pathname === item.path;

  return (
    <div className="portal-layout">
      <aside className="portal-sidebar">
        <div className="portal-sidebar__brand">
          <svg width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden="true">
            <rect width="28" height="28" rx="7" fill="var(--color-primary)" />
            <rect x="12" y="6" width="4" height="16" rx="2" fill="white" />
            <rect x="6" y="12" width="16" height="4" rx="2" fill="white" />
          </svg>
          <span className="portal-sidebar__brand-name">FarmáciaSaaS</span>
        </div>

        <nav className="portal-sidebar__nav">
          {NAV_SECTIONS.map((section) => {
            const isOpen = open[section.id];
            const hasActiveChild = section.items.some(isActive);
            return (
              <div key={section.id} className="portal-nav-section">
                <button
                  type="button"
                  className={`portal-nav-section__header${
                    hasActiveChild ? " portal-nav-section__header--active" : ""
                  }`}
                  onClick={() => toggle(section.id)}
                  aria-expanded={isOpen}
                >
                  <span className="portal-nav-section__icon" aria-hidden="true">
                    {section.icon}
                  </span>
                  <span className="portal-nav-section__title">{section.title}</span>
                  <span
                    className={`portal-nav-section__chevron${
                      isOpen ? " portal-nav-section__chevron--open" : ""
                    }`}
                    aria-hidden="true"
                  >
                    ▸
                  </span>
                </button>

                {isOpen && (
                  <div className="portal-nav-section__items">
                    {section.items.map((item) => (
                      <button
                        key={item.path}
                        className={`portal-nav-item ${
                          isActive(item) ? "portal-nav-item--active" : ""
                        }`}
                        onClick={() => navigate(item.path)}
                      >
                        <span className="portal-nav-item__icon" aria-hidden="true">
                          {item.icon}
                        </span>
                        <span>{item.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </nav>

        <div className="portal-sidebar__footer">
          <div className="portal-sidebar__user">
            <div className="portal-sidebar__avatar">
              {userEmail?.[0]?.toUpperCase() ?? "?"}
            </div>
            <div className="portal-sidebar__user-info">
              <span className="portal-sidebar__user-email">{userEmail}</span>
              <span className="portal-sidebar__user-role">Proprietário</span>
            </div>
          </div>
          <button className="portal-sidebar__logout" onClick={logout} title="Sair">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <polyline points="16 17 21 12 16 7" />
              <line x1="21" y1="12" x2="9" y2="12" />
            </svg>
          </button>
        </div>
      </aside>

      <main className="portal-main">{children}</main>
    </div>
  );
}
