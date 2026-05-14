import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import "./PortalLayout.css";

interface Props {
  children: React.ReactNode;
  active?: string;
}

const NAV_ITEMS = [
  { path: "/portal/dashboard",  label: "Dashboard",  icon: "⊞",  key: "dashboard" },
  { path: "/portal/skills",     label: "Agentes",    icon: "⚙",  key: "skills"    },
  { path: "/portal/persona",    label: "Persona",    icon: "✨", key: "persona"   },
  { path: "/portal/canais",     label: "Canais",     icon: "📡", key: "canais"    },
  { path: "/portal/estoque",    label: "Estoque",    icon: "📦", key: "estoque"   },
  { path: "/portal/clientes",   label: "Clientes",   icon: "👥", key: "clientes"  },
  { path: "/portal/logs",       label: "Conversas",  icon: "💬", key: "logs"      },
  { path: "/portal/integracao", label: "Integração", icon: "🔗", key: "integracao"},
  { path: "/portal/billing",    label: "Assinatura", icon: "💳", key: "billing"   },
  { path: "/portal/traces",    label: "Traces",     icon: "🔍", key: "traces"    },
  { path: "/portal/ia-config", label: "Config IA",  icon: "🤖", key: "ia-config" },
  { path: "/portal/vendas",    label: "Vendas",     icon: "🛒", key: "vendas"    },
  { path: "/portal/pedidos",   label: "Pedidos",    icon: "🧾", key: "pedidos"   },
];

export function PortalLayout({ children, active }: Props) {
  const { logout, userEmail } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <div className="portal-layout">
      <aside className="portal-sidebar">
        <div className="portal-sidebar__brand">
          <svg width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden="true">
            <rect width="28" height="28" rx="7" fill="var(--color-primary)"/>
            <rect x="12" y="6" width="4" height="16" rx="2" fill="white"/>
            <rect x="6" y="12" width="16" height="4" rx="2" fill="white"/>
          </svg>
          <span className="portal-sidebar__brand-name">FarmáciaSaaS</span>
        </div>

        <nav className="portal-sidebar__nav">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.path}
              className={`portal-nav-item ${(active ? active === item.key : location.pathname === item.path) ? "portal-nav-item--active" : ""}`}
              onClick={() => navigate(item.path)}
            >
              <span className="portal-nav-item__icon" aria-hidden="true">{item.icon}</span>
              <span>{item.label}</span>
            </button>
          ))}
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
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
              <polyline points="16 17 21 12 16 7"/>
              <line x1="21" y1="12" x2="9" y2="12"/>
            </svg>
          </button>
        </div>
      </aside>

      <main className="portal-main">
        {children}
      </main>
    </div>
  );
}
