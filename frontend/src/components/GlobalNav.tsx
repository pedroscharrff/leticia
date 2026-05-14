import { useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import "./GlobalNav.css";

export function GlobalNav() {
  const { logout } = useAuth();
  const navigate = useNavigate();

  return (
    <nav className="global-nav" role="navigation" aria-label="Global">
      <div className="global-nav__inner">
        <button className="global-nav__logo" onClick={() => navigate("/dashboard")} aria-label="Ir para dashboard">
          <svg width="18" height="22" viewBox="0 0 18 22" fill="none" aria-hidden="true">
            <path d="M14.9 11.6c0-3 2.4-4.4 2.5-4.5-1.4-2-3.5-2.3-4.2-2.3-1.8-.2-3.5 1-4.4 1-.9 0-2.3-1-3.8-.9-2 0-3.8 1.1-4.8 2.9C-1.8 11.5-.3 17 1.8 20c1 1.5 2.2 3.1 3.8 3s2.1-1 4-1 2.4 1 4 .9c1.6 0 2.6-1.5 3.6-3 1.1-1.7 1.6-3.4 1.6-3.4-.1-.1-3-.8-3-4z" fill="white"/>
            <path d="M12.5 2.9c.8-1 1.4-2.4 1.2-3.9-1.2.1-2.7.8-3.5 1.8-.8.9-1.5 2.3-1.3 3.7 1.3.1 2.7-.7 3.6-1.6z" fill="white"/>
          </svg>
        </button>

        <ul className="global-nav__links">
          <li><button onClick={() => navigate("/dashboard")}>Dashboard</button></li>
          <li><button onClick={() => navigate("/tenants")}>Farmácias</button></li>
          <li><button onClick={() => navigate("/chat-test")}>Chat de Teste</button></li>
          <li><button onClick={() => navigate("/settings")}>Configurações</button></li>
        </ul>

        <button className="global-nav__logout" onClick={logout}>
          Sair
        </button>
      </div>
    </nav>
  );
}
